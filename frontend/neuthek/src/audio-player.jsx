// Themed <audio> player. Subset of the video player's controls
// (no fullscreen, no playback-speed menu by default, no volume
// slider on small screens — but everything else matches).
//
// Keyboard:
//   Space / K  : play / pause
//   ← / →      : skip 5 s
//   J / L      : skip 10 s
//   ↑ / ↓      : volume ±10 %
//   M          : mute / unmute
import React, { useState, useEffect, useRef } from "react";
import { Icon } from "./icons.jsx";
import { getStreamUrl } from "@/api/files";

const AUTOPLAY_KEY = "neuthek.audio.autoplay_sound";

function fmtTime(s) {
  if (!Number.isFinite(s) || s < 0) return "0:00";
  const total = Math.floor(s);
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export function AudioPlayer({ fileId, fileName, fileExt, onClose }) {
  const [streamUrl, setStreamUrl] = useState(null);
  useEffect(() => {
    let cancelled = false;
    setStreamUrl(null);
    getStreamUrl(fileId)
      .then((u) => { if (!cancelled) setStreamUrl(u); })
      .catch(() => { if (!cancelled) setStreamUrl(null); });
    return () => { cancelled = true; };
  }, [fileId]);
  const refreshStreamUrl = () => {
    getStreamUrl(fileId).then(setStreamUrl).catch(() => {});
  };
  const audioRef = useRef(null);

  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.localStorage.getItem(AUTOPLAY_KEY) !== "on";
  });
  const [volume, setVolume] = useState(1.0);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [scrubbing, setScrubbing] = useState(false);
  const [showConsent, setShowConsent] = useState(false);

  useEffect(() => {
    if (!streamUrl) return;
    const a = audioRef.current; if (!a) return;
    a.muted = muted;
    a.volume = volume;
    const consent = window.localStorage.getItem(AUTOPLAY_KEY) === "on";
    if (!consent) setShowConsent(true);
    a.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streamUrl]);

  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      const a = audioRef.current; if (!a) return;
      const skip = (s) => { a.currentTime = Math.max(0, Math.min(a.duration || 0, a.currentTime + s)); };
      if (e.code === "Space" || e.key.toLowerCase() === "k") { e.preventDefault(); a.paused ? a.play() : a.pause(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); skip(-5); }
      else if (e.key === "ArrowRight") { e.preventDefault(); skip(5); }
      else if (e.key.toLowerCase() === "j") { e.preventDefault(); skip(-10); }
      else if (e.key.toLowerCase() === "l") { e.preventDefault(); skip(10); }
      else if (e.key === "ArrowUp") { e.preventDefault(); a.volume = Math.min(1, a.volume + 0.1); setVolume(a.volume); if (a.muted) { a.muted = false; setMuted(false); } }
      else if (e.key === "ArrowDown") { e.preventDefault(); a.volume = Math.max(0, a.volume - 0.1); setVolume(a.volume); }
      else if (e.key.toLowerCase() === "m") { e.preventDefault(); a.muted = !a.muted; setMuted(a.muted); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const togglePlay = () => {
    const a = audioRef.current; if (!a) return;
    if (a.paused) a.play(); else a.pause();
  };

  const onTimeUpdate = () => {
    const a = audioRef.current; if (!a) return;
    if (!scrubbing) setCurrent(a.currentTime);
    if (a.buffered.length > 0) setBuffered(a.buffered.end(a.buffered.length - 1));
  };

  const onScrubChange = (e) => {
    const a = audioRef.current; if (!a) return;
    const p = Number(e.target.value);
    if (Number.isFinite(p) && a.duration) {
      a.currentTime = (p / 100) * a.duration;
      setCurrent(a.currentTime);
    }
  };

  const onVolumeChange = (e) => {
    const a = audioRef.current; if (!a) return;
    const next = Number(e.target.value);
    a.volume = next;
    setVolume(next);
    if (next > 0 && a.muted) { a.muted = false; setMuted(false); }
  };

  const acceptConsent = () => {
    window.localStorage.setItem(AUTOPLAY_KEY, "on");
    setShowConsent(false);
    const a = audioRef.current; if (a) { a.muted = false; setMuted(false); a.play(); }
  };
  const declineConsent = () => {
    window.localStorage.setItem(AUTOPLAY_KEY, "off");
    setShowConsent(false);
  };

  const pct = duration > 0 ? (current / duration) * 100 : 0;
  const bufferedPct = duration > 0 ? (buffered / duration) * 100 : 0;

  return (
    <div className="audio-player" onClick={(e) => e.stopPropagation()}>
      {onClose && (
        <button
          type="button"
          className="audio-player__close btn-icon"
          onClick={onClose}
          aria-label="Close"
          title="Close"
        >
          <Icon name="x" size={16} />
        </button>
      )}
      <audio
        ref={audioRef}
        src={streamUrl || undefined}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onTimeUpdate={onTimeUpdate}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onError={() => { refreshStreamUrl(); }}
        preload="metadata"
      />

      <div className="audio-player__art">
        <Icon name="music" size={56} strokeWidth={1.2} />
        <span className="mono">{fileExt?.toUpperCase()}</span>
      </div>

      <div className="audio-player__name" title={fileName}>{fileName}</div>

      {!streamUrl && (
        <div className="audio-player__loading">Loading…</div>
      )}

      {showConsent && (
        <div className="audio-player__consent">
          <Icon name="volume" size={13} />
          <span>Play future audio files with sound automatically?</span>
          <button type="button" className="btn btn--primary btn--sm" onClick={acceptConsent}>Yes, with sound</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={declineConsent}>No thanks</button>
        </div>
      )}

      <div className="audio-player__scrub">
        <div className="audio-player__scrub-track">
          <div className="audio-player__scrub-buffered" style={{ width: `${bufferedPct}%` }} />
          <div className="audio-player__scrub-played" style={{ width: `${pct}%` }} />
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
          aria-label="Seek"
        />
      </div>

      <div className="audio-player__time mono">
        <span>{fmtTime(current)}</span>
        <span>{fmtTime(duration)}</span>
      </div>

      <div className="audio-player__row">
        <button
          type="button"
          className="btn-icon"
          onClick={() => { const a = audioRef.current; if (a) a.currentTime -= 10; }}
          aria-label="Skip back 10 s"
          title="Skip back 10 s (J)"
        >
          <Icon name="rewind10" size={18} />
        </button>
        <button
          type="button"
          className="btn-icon audio-player__play"
          onClick={togglePlay}
          aria-label={playing ? "Pause" : "Play"}
          title={playing ? "Pause (Space)" : "Play (Space)"}
        >
          <Icon name={playing ? "pause" : "play"} size={22} />
        </button>
        <button
          type="button"
          className="btn-icon"
          onClick={() => { const a = audioRef.current; if (a) a.currentTime += 10; }}
          aria-label="Skip forward 10 s"
          title="Skip forward 10 s (L)"
        >
          <Icon name="forward10" size={18} />
        </button>

        <div className="audio-player__vol">
          <button
            type="button"
            className="btn-icon"
            onClick={() => {
              const a = audioRef.current; if (!a) return;
              a.muted = !a.muted;
              setMuted(a.muted);
            }}
            aria-label={muted ? "Unmute" : "Mute"}
            title={muted ? "Unmute (M)" : "Mute (M)"}
          >
            <Icon name={muted || volume === 0 ? "volume_off" : volume < 0.5 ? "volume_low" : "volume"} size={15} />
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            onChange={onVolumeChange}
            aria-label="Volume"
          />
        </div>
      </div>
    </div>
  );
}
