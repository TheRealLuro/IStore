// Themed <video> player with full custom controls. Renders inside
// the existing .lightbox surface so it inherits the same modal
// behavior (Esc to close, comments panel on the left, click outside
// to dismiss).
//
// Controls (all keyboard-driven too):
//   Space / K          : play / pause
//   ← / →              : skip 5 s
//   J / L              : skip 10 s
//   ↑ / ↓              : volume ±10 %
//   M                  : mute / unmute
//   F                  : fullscreen
//   0-9                : seek to 0-90 % of duration
//
// Autoplay-with-sound is gated by browser policy. We honor it: on
// first mount the player asks for an "audio consent" if it can't
// guarantee user gesture. The consent flips a localStorage flag
// (`neuthek.video.autoplay_sound = "on"`) so subsequent files play
// with sound the moment they open. Without consent, autoplay still
// works muted (the browser allows that universally); the user just
// has to click the volume icon to unmute.
import React, { useState, useEffect, useRef, useMemo } from "react";
import { Icon } from "./icons.jsx";
import { getStreamInfo, getStreamUrl } from "@/api/files";
import { API_BASE_URL, tokens } from "@/api/client";
import Hls from "hls.js";

const AUTOPLAY_KEY = "neuthek.video.autoplay_sound";

function fmtTime(s) {
  if (!Number.isFinite(s) || s < 0) return "0:00";
  const total = Math.floor(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// Props:
//   fileId, fileName    — what's playing.
//   onClose             — header X / Esc handler.
//   onPlayingChange(b)  — fires when playback state transitions
//                         play→pause or pause→play. Preview.jsx uses
//                         this to drive a "focus mode" (full-black
//                         lightbox + comment panel auto-collapses
//                         to its bubble) while playing.
export function VideoPlayer({ fileId, fileName, onClose, onPlayingChange }) {
  // Signed streaming URL (HTTP Range-supported on the backend) so the
  // browser can seek without re-downloading from byte 0 and metadata
  // loads in seconds even on a 4 GB file. URL expires per the
  // backend's `stream_url_ttl_seconds` (1 hour by default); we refresh
  // on the video element's `error` event so a long pause doesn't
  // strand the player on a 403.
  // Quality tiers populated from /stream-info on mount. The default
  // picks the highest available (per backend). On quality change we
  // swap `video.src` and re-seek to preserve currentTime + the
  // play/pause state so the user doesn't lose their place.
  const [streamUrl, setStreamUrl] = useState(null);
  const [qualities, setQualities] = useState([]); // [{label, url, expires_at}]
  const [quality, setQuality] = useState(null);   // current label
  const [showQualityMenu, setShowQualityMenu] = useState(false);

  // HLS state — set when the row was transcoded by the 2026-05+
  // pipeline. `hlsUrl` is the master.m3u8 URL; `hlsRenditions` is the
  // resolution ladder for the quality picker. When these are set the
  // player binds hls.js to <video> instead of using `streamUrl` as
  // an `src=`. The hls.js instance handles quality switching itself
  // (level === -1 = auto-adapt).
  const [hlsUrl, setHlsUrl] = useState(null);
  const [hlsRenditions, setHlsRenditions] = useState([]); // [{label,width,height,bandwidth_bps}]
  const [hlsLevel, setHlsLevel] = useState(-1);           // -1 = auto, else array index
  // `hlsActiveLevel` is the level hls.js is CURRENTLY playing
  // (whether the user picked it or the ABR algorithm did). Different
  // from `hlsLevel` which is the user's MANUAL pick — in auto mode
  // `hlsLevel === -1` but `hlsActiveLevel` follows whatever the
  // adapter chose. The quality button shows both: "Auto (720p)".
  const [hlsActiveLevel, setHlsActiveLevel] = useState(-1);
  // `hlsLevels` is the level array snapshot from hls.js. Lives in
  // React state so the menu re-renders when the manifest finishes
  // parsing (Hls.Events.MANIFEST_PARSED) — relying on `hlsRef.current.levels`
  // alone meant the menu was empty the first time the user opened
  // it because React didn't know to re-render.
  const [hlsLevels, setHlsLevels] = useState([]);
  // Live bandwidth estimate — hls.js exposes its measured throughput
  // via Hls.Events.FRAG_LOADED. We surface it as a small badge so the
  // user can see WHY auto mode picked the rendition it did.
  const [hlsBandwidthKbps, setHlsBandwidthKbps] = useState(0);
  // Buffering / quality-switch overlay state. True while hls.js is
  // mid-segment-fetch on a new level OR the video element fires
  // `waiting` (it ran out of buffered bytes).
  const [hlsBuffering, setHlsBuffering] = useState(false);
  const hlsRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setStreamUrl(null);
    setQualities([]);
    setQuality(null);
    setHlsUrl(null);
    setHlsRenditions([]);
    setHlsLevel(-1);
    getStreamInfo(fileId)
      .then((info) => {
        if (cancelled) return;
        // HLS pipeline first — when stream-info says mode=hls the
        // legacy mp4 picker is irrelevant.
        if (info?.mode === "hls" && info?.hls_url) {
          setHlsUrl(`${API_BASE_URL}${info.hls_url}`);
          setHlsRenditions(info.renditions || []);
          return;
        }
        const picks = info?.qualities || [];
        setQualities(picks);
        // Honor the user's per-device default-quality preference
        // (Settings → Playback → Default video quality). Falls back
        // to the server's `default_quality` (highest available) when
        // no preference is set OR the preferred tier doesn't exist
        // for this particular video — small libraries / 480p source
        // clips don't have a 1080p tier, so a "1080p" pref is silently
        // ignored on those rows.
        let preferred = "";
        try { preferred = localStorage.getItem("neuthek.video.default_quality") || ""; } catch {}
        let chosen = null;
        if (preferred) {
          chosen = picks.find((q) => q.label === preferred) || null;
        }
        if (!chosen) {
          chosen = picks.find((q) => q.label === info?.default_quality) || picks[0] || null;
        }
        setQuality(chosen?.label || null);
        if (chosen) setStreamUrl(chosen.url);
      })
      .catch(() => { if (!cancelled) setStreamUrl(null); });
    return () => { cancelled = true; };
  }, [fileId]);

  // HLS attach/detach. Runs only when we have an hls_url. Safari
  // (and iOS WebKit generally) plays HLS natively via <video src> —
  // we detect that and skip hls.js entirely so the OS player owns
  // the session. Everyone else uses hls.js with our Bearer token
  // injected on every segment fetch via `xhrSetup`.
  useEffect(() => {
    if (!hlsUrl) return;
    const video = videoRef.current;
    if (!video) return;

    const canNativeHls = video.canPlayType("application/vnd.apple.mpegurl");
    if (canNativeHls) {
      // Safari path. We still need auth on the segment fetches —
      // Safari sends cookies but our API uses Bearer tokens. Workaround
      // is to set the cookie too OR fall through to hls.js even on
      // Safari. For now, prefer hls.js everywhere we can — Safari can
      // run it, just without MSE optimisations. Cleaner story.
      // (Drop into the hls.js branch below.)
    }

    if (Hls.isSupported()) {
      const hls = new Hls({
        // Cookie auth: send the HttpOnly session cookie on every
        // manifest + segment fetch via XHR `withCredentials`.
        // Legacy localStorage Bearer is still set if the user is on
        // a pre-cookie session — we add it via xhrSetup so the same
        // session continues to play through transition.
        xhrSetup(xhr) {
          xhr.withCredentials = true;
          try {
            const legacy = localStorage.getItem("neuthek.jwt") || localStorage.getItem("istore.jwt");
            if (legacy) xhr.setRequestHeader("Authorization", `Bearer ${legacy}`);
          } catch { /* private browsing */ }
        },
        // Modest startup buffer — gets the spinner off the screen
        // faster while still letting the ABR logic settle.
        maxBufferLength: 30,
        maxMaxBufferLength: 60,
      });
      hls.loadSource(hlsUrl);
      hls.attachMedia(video);
      hlsRef.current = hls;

      // Live level / bandwidth surfacing for the UI badges. We
      // listen for:
      //   MANIFEST_PARSED → snapshot the level array into React
      //   LEVEL_SWITCHED  → update `hlsActiveLevel` so the
      //                      "Auto (720p)" readout stays accurate
      //   FRAG_LOADED     → update measured bandwidth in kbps
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        const levels = (hls.levels || []).map((lvl) => ({
          width: lvl.width,
          height: lvl.height,
          bitrate: lvl.bitrate,
        }));
        setHlsLevels(levels);
        setHlsActiveLevel(hls.currentLevel);

        // Honor the user's Streaming-quality preset (Settings →
        // Playback) on first play. "auto" = let hls.js adapt
        // (level === -1, the default). "best" locks to the highest
        // rendition. "saver" locks to the lowest. "custom" leaves
        // it at -1 too but the user is expected to flip per video.
        let preset = "auto";
        try {
          preset = localStorage.getItem("neuthek.video.quality_preset") || "auto";
        } catch {}
        if (preset === "best" && levels.length > 0) {
          const highest = levels.length - 1;
          hls.currentLevel = highest;
          setHlsLevel(highest);
        } else if (preset === "saver" && levels.length > 0) {
          hls.currentLevel = 0;
          setHlsLevel(0);
        }
      });
      hls.on(Hls.Events.LEVEL_SWITCHED, (_evt, data) => {
        setHlsActiveLevel(data.level);
        // Briefly show the buffering overlay so the user gets visual
        // confirmation that quality is shifting; auto-clear after the
        // first FRAG_LOADED at the new level.
        setHlsBuffering(true);
      });
      hls.on(Hls.Events.FRAG_LOADED, (_evt, data) => {
        setHlsBuffering(false);
        // hls.js exposes the rolling average via the bandwidth
        // estimator; falls back to instantaneous from the latest
        // fragment when unavailable.
        const avg = Math.round(
          (hls.bandwidthEstimate ?? data?.frag?.stats?.bwEstimate ?? 0) / 1000,
        );
        if (avg > 0) setHlsBandwidthKbps(avg);
      });
      hls.on(Hls.Events.ERROR, (_evt, data) => {
        // Non-fatal hiccups are common during quality switches —
        // hls.js recovers itself. Only log fatal errors so the dev
        // console isn't noisy.
        if (data?.fatal) {
          console.warn("hls fatal:", data.type, data.details);
          // Network errors are usually a stale auth token — try
          // to recover by re-loading the source.
          if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
            try { hls.startLoad(); } catch {}
          } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
            try { hls.recoverMediaError(); } catch {}
          }
        }
      });
    } else if (canNativeHls) {
      // Native HLS playback — Safari only. Set Authorization via a
      // pre-signed cookie isn't viable here; for now Safari users
      // need to be logged in via cookie session. Falls back to the
      // browser's own ABR. Rare path; ship and iterate.
      video.src = hlsUrl;
    }

    return () => {
      const hls = hlsRef.current;
      if (hls) {
        try { hls.destroy(); } catch {}
        hlsRef.current = null;
      }
    };
  }, [hlsUrl]);

  // Manual quality pick from the menu. -1 → auto (hls.js picks
  // based on measured throughput). Anything else maps to a level
  // index in the hls.js levels array.
  const setHlsQualityLevel = (level) => {
    const hls = hlsRef.current;
    if (!hls) return;
    hls.currentLevel = level;
    setHlsLevel(level);
  };

  // Used on the <video onError=> path AND when the user picks a
  // new quality. Refreshes the URL for whatever `quality` is
  // currently selected.
  const refreshStreamUrl = () => {
    const label = quality || "";
    getStreamUrl(fileId, label).then(setStreamUrl).catch(() => {});
  };

  const switchQuality = async (nextLabel) => {
    setShowQualityMenu(false);
    if (!nextLabel || nextLabel === quality) return;
    const v = videoRef.current;
    const t = v?.currentTime || 0;
    const wasPlaying = v && !v.paused;
    try {
      const url = await getStreamUrl(fileId, nextLabel);
      setQuality(nextLabel);
      setStreamUrl(url);
      // After React re-renders with the new src, the <video> will
      // emit `loadedmetadata`. We need to re-seek + resume there;
      // doing it inline before the re-render would target the OLD
      // source. A short rAF-then-setTimeout dance gives the
      // element a chance to receive its new src.
      requestAnimationFrame(() => {
        setTimeout(() => {
          const v2 = videoRef.current;
          if (!v2) return;
          v2.currentTime = t;
          if (wasPlaying) v2.play().catch(() => {});
        }, 30);
      });
    } catch {
      // URL refresh failed — keep the current quality + src.
    }
  };

  const videoRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(() => {
    // First load: respect prior consent. If the user has authorized
    // sound-on-autoplay before, start unmuted; else start muted (the
    // only autoplay path the browser allows without a user gesture).
    if (typeof window === "undefined") return true;
    return window.localStorage.getItem(AUTOPLAY_KEY) !== "on";
  });
  const [volume, setVolume] = useState(1.0);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  // Initial playback speed honours the per-device preference set
  // in Settings → Playback → Default playback speed. Falls back to
  // 1× when the localStorage key is missing or malformed.
  const [speed, setSpeed] = useState(() => {
    try {
      const raw = localStorage.getItem("neuthek.video.default_speed");
      const n = parseFloat(raw || "");
      return Number.isFinite(n) && n > 0 ? n : 1.0;
    } catch {
      return 1.0;
    }
  });
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [scrubbing, setScrubbing] = useState(false);
  const [showConsent, setShowConsent] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const hideTimerRef = useRef(null);

  // Auto-hide controls during playback after 2.5 s of mouse-quiet.
  const bumpControls = () => {
    setControlsVisible(true);
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => {
      // Only hide while actively playing — when paused, keep visible.
      if (videoRef.current && !videoRef.current.paused) {
        setControlsVisible(false);
      }
    }, 2500);
  };
  useEffect(() => () => hideTimerRef.current && clearTimeout(hideTimerRef.current), []);

  // Try to autoplay when the blob URL resolves. Browsers allow
  // muted autoplay unconditionally; unmuted autoplay needs prior
  // consent (which we store in localStorage). If the consent is
  // missing, we show the consent strip so the user can opt in.
  useEffect(() => {
    if (!streamUrl) return;
    const v = videoRef.current;
    if (!v) return;
    v.muted = muted;
    v.volume = volume;
    v.playbackRate = speed;
    const consent = window.localStorage.getItem(AUTOPLAY_KEY) === "on";
    if (!consent) setShowConsent(true);
    const tryPlay = async () => {
      try {
        await v.play();
        setPlaying(true);
      } catch {
        // Browser blocked autoplay — leave paused; user clicks to start.
        setPlaying(false);
      }
    };
    tryPlay();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamUrl]);

  // Keyboard handling. Esc is handled by the parent (preview.jsx) so
  // we don't double-trigger here.
  useEffect(() => {
    const onKey = (e) => {
      if (!videoRef.current) return;
      // Ignore when focus is in an input/textarea (comment panel).
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      const v = videoRef.current;
      const skip = (s) => { v.currentTime = Math.max(0, Math.min(v.duration || 0, v.currentTime + s)); };
      if (e.code === "Space" || e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (v.paused) v.play(); else v.pause();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        skip(-5);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        skip(5);
      } else if (e.key.toLowerCase() === "j") {
        e.preventDefault();
        skip(-10);
      } else if (e.key.toLowerCase() === "l") {
        e.preventDefault();
        skip(10);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        v.volume = Math.min(1, v.volume + 0.1);
        setVolume(v.volume);
        if (v.muted) { v.muted = false; setMuted(false); }
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        v.volume = Math.max(0, v.volume - 0.1);
        setVolume(v.volume);
      } else if (e.key.toLowerCase() === "m") {
        e.preventDefault();
        v.muted = !v.muted;
        setMuted(v.muted);
      } else if (e.key.toLowerCase() === "f") {
        e.preventDefault();
        toggleFullscreen();
      } else if (/^[0-9]$/.test(e.key) && v.duration) {
        e.preventDefault();
        v.currentTime = (Number(e.key) / 10) * v.duration;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggleFullscreen = () => {
    const root = videoRef.current?.parentElement;
    if (!root) return;
    if (document.fullscreenElement) {
      document.exitFullscreen?.();
    } else {
      root.requestFullscreen?.();
    }
  };

  const togglePlay = () => {
    const v = videoRef.current; if (!v) return;
    if (v.paused) { v.play(); } else { v.pause(); }
  };

  const onTimeUpdate = () => {
    const v = videoRef.current; if (!v) return;
    if (!scrubbing) setCurrent(v.currentTime);
    if (v.buffered.length > 0) {
      setBuffered(v.buffered.end(v.buffered.length - 1));
    }
  };

  const onScrubChange = (e) => {
    const v = videoRef.current; if (!v) return;
    const pct = Number(e.target.value);
    if (Number.isFinite(pct) && v.duration) {
      const t = (pct / 100) * v.duration;
      setCurrent(t);
      v.currentTime = t;
    }
  };

  const onVolumeChange = (e) => {
    const v = videoRef.current; if (!v) return;
    const next = Number(e.target.value);
    v.volume = next;
    setVolume(next);
    if (next > 0 && v.muted) { v.muted = false; setMuted(false); }
  };

  const acceptAudioConsent = () => {
    window.localStorage.setItem(AUTOPLAY_KEY, "on");
    setShowConsent(false);
    const v = videoRef.current;
    if (v) {
      v.muted = false;
      setMuted(false);
      v.play();
    }
  };

  const declineAudioConsent = () => {
    window.localStorage.setItem(AUTOPLAY_KEY, "off");
    setShowConsent(false);
  };

  const pct = duration > 0 ? (current / duration) * 100 : 0;
  const bufferedPct = duration > 0 ? (buffered / duration) * 100 : 0;

  return (
    <div
      className={`video-player ${controlsVisible ? "video-player--show-controls" : ""}`}
      onMouseMove={bumpControls}
      onClick={(e) => {
        // Click on the video area (not on the controls bar) toggles play.
        if (e.target === videoRef.current || e.currentTarget === e.target) {
          togglePlay();
        }
      }}
    >
      {!streamUrl && !hlsUrl && (
        <div className="video-player__loading">
          <div className="video-player__spinner" />
          <span>Loading video…</span>
        </div>
      )}
      {/* Buffering / quality-switch overlay. Shown ONLY when the
          video has already started (we have a duration) and is now
          paused waiting for bytes — the initial-load spinner above
          handles the first-paint case. Subtle so it doesn't dominate
          the frame; the spinner is centered, the message names what
          the player is doing. */}
      {hlsBuffering && duration > 0 && (
        <div className="video-player__buffering" aria-live="polite">
          <div className="video-player__spinner video-player__spinner--sm" />
          <span>
            {hlsActiveLevel !== hlsLevel && hlsLevel !== -1
              ? `Switching to ${hlsLevels[hlsLevel]?.height || ""}p…`
              : "Buffering…"}
          </span>
        </div>
      )}
      <video
        ref={videoRef}
        // In HLS mode `src` is owned by hls.js (attachMedia) — we
        // don't set it here, otherwise the browser tries to play the
        // manifest as a binary file. Legacy mp4 mode keeps the
        // signed `streamUrl` as src.
        src={hlsUrl ? undefined : (streamUrl || undefined)}
        className="video-player__media"
        onPlay={() => {
          setPlaying(true);
          bumpControls();
          onPlayingChange && onPlayingChange(true);
        }}
        onPause={() => {
          setPlaying(false);
          setControlsVisible(true);
          onPlayingChange && onPlayingChange(false);
        }}
        onTimeUpdate={onTimeUpdate}
        onLoadedMetadata={(e) => {
          setDuration(e.currentTarget.duration);
          // Apply the default-speed preference once the element has
          // a manifest / metadata. Doing it on mount races with
          // hls.js's own attachMedia path; doing it here is
          // load-order-independent.
          try { e.currentTarget.playbackRate = speed; } catch {}
        }}
        onWaiting={() => setHlsBuffering(true)}
        onPlaying={() => setHlsBuffering(false)}
        onCanPlay={() => setHlsBuffering(false)}
        onEnded={() => {
          setPlaying(false);
          setControlsVisible(true);
          onPlayingChange && onPlayingChange(false);
        }}
        onError={() => {
          // Most common cause: the signed URL TTL expired during a
          // long pause. Refresh and let HTML5 re-attach to the new
          // source. Other errors (codec / network) will fail again
          // after the refresh and the user sees the spinner-then-
          // nothing state — that's accurate and we don't want to
          // pretend otherwise.
          refreshStreamUrl();
        }}
        onClick={(e) => e.stopPropagation()}
        playsInline
      />

      {/* Header — file name + close. Always visible. */}
      <div className="video-player__head">
        <span className="video-player__name">{fileName}</span>
        <button
          type="button"
          className="btn-icon"
          onClick={onClose}
          aria-label="Close"
          title="Close (Esc)"
        >
          <Icon name="x" size={16} />
        </button>
      </div>

      {/* Audio-consent strip — first-time-only prompt to enable
          autoplay with sound on subsequent files. */}
      {showConsent && (
        <div className="video-player__consent">
          <Icon name="volume" size={14} />
          <span>Play future videos with sound automatically?</span>
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={acceptAudioConsent}
          >
            Yes, with sound
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={declineAudioConsent}
          >
            No thanks
          </button>
        </div>
      )}

      {/* Controls bar — pinned to bottom, fades on inactivity. */}
      <div className="video-player__controls" onClick={(e) => e.stopPropagation()}>
        {/* Scrub bar — full width across the top of the controls. The
            buffered range is rendered as a softer underlay behind the
            played-progress fill. */}
        <div className="video-player__scrub">
          <div className="video-player__scrub-track">
            <div
              className="video-player__scrub-buffered"
              style={{ width: `${bufferedPct}%` }}
            />
            <div
              className="video-player__scrub-played"
              style={{ width: `${pct}%` }}
            />
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={0.1}
            value={pct}
            onChange={onScrubChange}
            onMouseDown={() => setScrubbing(true)}
            onMouseUp={() => setScrubbing(false)}
            onTouchStart={() => setScrubbing(true)}
            onTouchEnd={() => setScrubbing(false)}
            className="video-player__scrub-input"
            aria-label="Seek"
          />
        </div>

        <div className="video-player__row">
          {/* Left group: skip-back, play/pause, skip-forward. */}
          <button
            type="button"
            className="btn-icon"
            onClick={() => { const v = videoRef.current; if (v) v.currentTime -= 10; }}
            aria-label="Skip back 10 seconds"
            title="Skip back 10 s (J)"
          >
            <Icon name="rewind10" size={18} />
          </button>
          <button
            type="button"
            className="btn-icon video-player__play"
            onClick={togglePlay}
            aria-label={playing ? "Pause" : "Play"}
            title={playing ? "Pause (Space)" : "Play (Space)"}
          >
            <Icon name={playing ? "pause" : "play"} size={22} />
          </button>
          <button
            type="button"
            className="btn-icon"
            onClick={() => { const v = videoRef.current; if (v) v.currentTime += 10; }}
            aria-label="Skip forward 10 seconds"
            title="Skip forward 10 s (L)"
          >
            <Icon name="forward10" size={18} />
          </button>

          {/* Time. */}
          <span className="video-player__time mono">
            {fmtTime(current)} / {fmtTime(duration)}
          </span>

          <span style={{ flex: 1 }} />

          {/* Right group: volume, speed, fullscreen. */}
          <div className="video-player__vol">
            <button
              type="button"
              className="btn-icon"
              onClick={() => {
                const v = videoRef.current; if (!v) return;
                v.muted = !v.muted;
                setMuted(v.muted);
              }}
              aria-label={muted ? "Unmute" : "Mute"}
              title={muted ? "Unmute (M)" : "Mute (M)"}
            >
              <Icon
                name={muted || volume === 0 ? "volume_off" : volume < 0.5 ? "volume_low" : "volume"}
                size={16}
              />
            </button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={muted ? 0 : volume}
              onChange={onVolumeChange}
              className="video-player__vol-slider"
              aria-label="Volume"
            />
          </div>

          {/* Quality menu. Two render paths:
              - HLS mode: pull renditions from `hlsLevels` (snapshot
                of hls.js's parsed manifest). "Auto" trigger shows
                BOTH the user's pick AND the current adapted level
                so it's clear what's playing — "Auto (720p)" beats
                a bare "Auto" that gives no signal. Each level
                lists its bitrate so the user can correlate
                quality with bandwidth cost.
              - Legacy mp4 mode: per-tier signed URLs from `qualities`.
              Hidden when there's only one option in either path. */}
          {hlsUrl && hlsLevels.length > 0 ? (
            <details
              className="video-player__speed video-player__quality"
              open={showQualityMenu}
              onToggle={(e) => setShowQualityMenu(e.currentTarget.open)}
            >
              <summary className="btn-icon" title="Quality">
                <span className="mono" style={{ fontSize: 12 }}>
                  {hlsLevel === -1 ? (
                    <>
                      Auto
                      {hlsActiveLevel >= 0 && hlsLevels[hlsActiveLevel] && (
                        <span style={{ opacity: 0.55, marginLeft: 3 }}>
                          ({hlsLevels[hlsActiveLevel].height}p)
                        </span>
                      )}
                    </>
                  ) : (
                    (hlsLevels[hlsLevel]?.height ?? "?") + "p"
                  )}
                </span>
              </summary>
              <div className="video-player__speed-menu video-player__quality-menu">
                <button
                  type="button"
                  data-active={hlsLevel === -1}
                  onClick={() => setHlsQualityLevel(-1)}
                  title="Auto-pick the best quality your network can sustain"
                >
                  <span className="video-player__quality-label">Auto</span>
                  <span className="video-player__quality-sub">
                    {hlsActiveLevel >= 0 && hlsLevels[hlsActiveLevel]
                      ? `Currently ${hlsLevels[hlsActiveLevel].height}p`
                      : "Adapts to bandwidth"}
                  </span>
                </button>
                {hlsLevels.map((lvl, idx) => (
                  <button
                    key={idx}
                    type="button"
                    data-active={idx === hlsLevel}
                    onClick={() => setHlsQualityLevel(idx)}
                  >
                    <span className="video-player__quality-label">
                      {lvl.height}p
                    </span>
                    <span className="video-player__quality-sub">
                      {lvl.bitrate ? `${Math.round(lvl.bitrate / 1000)} kbps` : ""}
                    </span>
                  </button>
                ))}
                {hlsBandwidthKbps > 0 && (
                  <div className="video-player__quality-foot">
                    <Icon name="cloud" size={11}/>
                    <span>Measured: {hlsBandwidthKbps >= 1000 ? `${(hlsBandwidthKbps/1000).toFixed(1)} Mbps` : `${hlsBandwidthKbps} kbps`}</span>
                  </div>
                )}
              </div>
            </details>
          ) : qualities.length > 1 ? (
            <details
              className="video-player__speed"
              open={showQualityMenu}
              onToggle={(e) => setShowQualityMenu(e.currentTarget.open)}
            >
              <summary className="btn-icon" title="Quality">
                <span className="mono" style={{ fontSize: 12 }}>{quality || "auto"}</span>
              </summary>
              <div className="video-player__speed-menu">
                {qualities.map((q) => (
                  <button
                    key={q.label}
                    type="button"
                    data-active={q.label === quality}
                    onClick={() => switchQuality(q.label)}
                  >
                    {q.label}
                  </button>
                ))}
              </div>
            </details>
          ) : null}

          <details
            className="video-player__speed"
            open={showSpeedMenu}
            onToggle={(e) => setShowSpeedMenu(e.currentTarget.open)}
          >
            <summary className="btn-icon" title="Playback speed">
              <span className="mono" style={{ fontSize: 12 }}>{speed}×</span>
            </summary>
            <div className="video-player__speed-menu">
              {[0.5, 0.75, 1, 1.25, 1.5, 2].map((s) => (
                <button
                  key={s}
                  type="button"
                  data-active={s === speed}
                  onClick={() => {
                    const v = videoRef.current;
                    if (v) v.playbackRate = s;
                    setSpeed(s);
                    setShowSpeedMenu(false);
                  }}
                >
                  {s}×
                </button>
              ))}
            </div>
          </details>

          <button
            type="button"
            className="btn-icon"
            onClick={toggleFullscreen}
            aria-label="Fullscreen"
            title="Fullscreen (F)"
          >
            <Icon name="maximize" size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
