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

  useEffect(() => {
    let cancelled = false;
    setStreamUrl(null);
    setQualities([]);
    setQuality(null);
    getStreamInfo(fileId)
      .then((info) => {
        if (cancelled) return;
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
  const [speed, setSpeed] = useState(1.0);
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
      {!streamUrl && (
        <div className="video-player__loading">
          <div className="video-player__spinner" />
          <span>Loading video…</span>
        </div>
      )}
      <video
        ref={videoRef}
        src={streamUrl || undefined}
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
        onLoadedMetadata={(e) => { setDuration(e.currentTarget.duration); }}
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

          {/* Quality menu — only renders when the backend reported
              more than one tier. Rows uploaded before multi-quality
              support land here with a single "auto" entry, so the
              menu gracefully self-hides for them. */}
          {qualities.length > 1 && (
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
          )}

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
