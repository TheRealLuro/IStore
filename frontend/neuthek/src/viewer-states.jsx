// Shared presentational chrome for the type-specific file viewers
// (VCF / ICS / CSV / Font / EPUB / Data-tree / Notebook). These are the
// few pieces that MUST look identical across all seven so they read as
// one design family: the loading skeleton, the empty state, the error
// state (with Retry + Download affordances), and the copy-button that
// flashes "Copied ✓".
//
// Deliberately presentational only — no data loading, no parsing, no
// viewer-specific logic lives here. Each viewer keeps its own parser and
// pipeline self-contained; this module is just shared skin + the one
// copy interaction, styled entirely by styles-viewers.css.
import React, { useState, useRef, useEffect, useCallback } from "react";
import toast from "react-hot-toast";
import { Icon, initials as _initials } from "./icons.jsx";

// A calm shimmer skeleton. `lines` is an array of pixel widths (number)
// or "%" strings; each renders as one shimmering bar. Callers pass a
// shape that roughly mirrors their real content so the transition in
// feels seamless rather than a generic spinner.
export function ViewerSkeleton({ lines, className = "" }) {
  const bars = lines || [180, "70%", "55%", "62%", "40%"];
  return (
    <div className={`vw-skel ${className}`} aria-busy="true" aria-live="polite">
      <span className="vw-sr">Loading…</span>
      {bars.map((w, i) => (
        <div
          key={i}
          className="vw-shimmer"
          style={{
            width: typeof w === "number" ? `${w}px` : w,
            height: 14,
          }}
        />
      ))}
    </div>
  );
}

// Friendly error state: an icon, a title, the underlying message, and
// two affordances — Retry (re-runs the load) and Download (the original
// bytes, so the user is never stuck). `downloadUrl` / `downloadName`
// are optional; omit them to hide the download link. `onRetry` is
// optional; omit it to hide Retry.
export function ViewerError({ title = "Couldn't open this file", message, onRetry, downloadUrl, downloadName }) {
  return (
    <div className="vw-state vw-state--err" role="alert">
      <span className="vw-state__icon"><Icon name="alert" size={22} /></span>
      <div className="vw-state__title">{title}</div>
      {message && <div className="vw-state__msg">{message}</div>}
      <div className="vw-state__actions">
        {onRetry && (
          <button type="button" className="vw-btn" onClick={onRetry}>
            <Icon name="refresh" size={14} /> Retry
          </button>
        )}
        {downloadUrl && (
          <a className="vw-btn vw-btn--primary" href={downloadUrl} download={downloadName || true}>
            <Icon name="download" size={14} /> Download
          </a>
        )}
      </div>
    </div>
  );
}

// Empty state — same skeleton of icon + title + sub, but neutral.
export function ViewerEmpty({ icon = "file", title = "Nothing to show", message }) {
  return (
    <div className="vw-state" role="status">
      <span className="vw-state__icon"><Icon name={icon} size={22} /></span>
      <div className="vw-state__title">{title}</div>
      {message && <div className="vw-state__msg">{message}</div>}
    </div>
  );
}

// Copy-to-clipboard control with a transient "Copied ✓" confirmation.
// `variant` lets each viewer pick the chrome that fits its row
// ("icon" = a square ghost icon button, "text" = a tiny pill that reads
// "copy" → "copied"). The checkmark/label swap is the confirmation; we
// also fire a toast for parity with the rest of the app.
export function CopyButton({ text, variant = "icon", title = "Copy", size = 13, className = "" }) {
  const [done, setDone] = useState(false);
  const timer = useRef(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const onCopy = useCallback(async (e) => {
    e?.preventDefault?.();
    e?.stopPropagation?.();
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      toast.success("Copied");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setDone(false), 1400);
    } catch {
      toast.error("Couldn't copy");
    }
  }, [text]);

  if (variant === "text") {
    return (
      <button
        type="button"
        className={`vw-copytext mono${done ? " is-done" : ""} ${className}`}
        onClick={onCopy}
        title={done ? "Copied" : title}
        aria-label={title}
      >
        {done ? "copied ✓" : "copy"}
      </button>
    );
  }

  return (
    <button
      type="button"
      className={`vw-copy${done ? " is-done" : ""} ${className}`}
      onClick={onCopy}
      title={done ? "Copied" : title}
      aria-label={done ? "Copied" : title}
    >
      <Icon name={done ? "check" : "copy"} size={size} />
    </button>
  );
}

// Re-export so viewers can grab the initials helper from one place if
// they already import from here (keeps import lines tidy).
export const initials = _initials;
