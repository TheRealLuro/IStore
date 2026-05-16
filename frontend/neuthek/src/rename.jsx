// Rename modal — AI smart-name suggestions based on file content.
// Naming convention: enforces extension match + safe characters.
//
// §C1.2 — for `image`-category files, suggestions come from the real
// backend endpoint `GET /images/{id}/suggest-names` which reuses the
// already-computed Florence-2 / Qwen summary signals (topic, summary,
// scene, captured place, named people). For non-image types or when
// the API call fails (network blip, image still uploading), the modal
// falls back to the deterministic client-side suggestions.
import React, {
  useState as useStateRn,
  useEffect as useEffectRn,
  useMemo as useMemoRn,
} from "react";
import { Icon } from "./icons.jsx";
import {
  Modal as ModalRn,
  ModalClose as ModalCloseRn,
} from "./primitives.jsx";
import { suggestNames } from "@/api/files";

const SLUG_RX = /[^A-Za-z0-9._-]+/g;

function safeName(input, ext) {
  let base = (input || "").trim();
  // strip a trailing extension if user typed one (we re-append)
  base = base.replace(new RegExp("\\." + ext + "$", "i"), "");
  base = base.replace(SLUG_RX, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  if (base.length === 0) base = "untitled";
  return base + "." + ext.toLowerCase();
}

// Generate plausible smart suggestions from a file's metadata.
// In a real app this would call an LLM with the AI summary as context.
function generateSuggestions(file) {
  if (!file) return [];
  const ext = (file.ext || "").toLowerCase();
  const date = (file.when || "").includes("ago") ? "" : "";
  const today = new Date().toISOString().slice(0, 10);

  if (file.type === "image") {
    const place = file.gps?.place?.split(",")[0]?.trim().toLowerCase().replace(/\s+/g, "-") || "photo";
    const subj = (file.topic || "").toLowerCase()
      .replace(/[,.]/g, "")
      .split(" ").slice(0, 4).join("-");
    return [
      { name: safeName(`${place}-${subj}`, ext), why: "Place + main subject from AI summary." },
      { name: safeName(`${today}-${subj}`, ext), why: "Date-prefixed for chronological sorting." },
      { name: safeName(`${subj}-${file.id}`, ext), why: "Subject + ID for uniqueness." },
    ];
  }
  if (file.type === "video") {
    const subj = (file.topic || "video").toLowerCase().replace(/[,.]/g, "").split(" ").slice(0, 4).join("-");
    return [
      { name: safeName(`${subj}-clip`, ext), why: "Subject keyword from summary." },
      { name: safeName(`${today}-${subj}`, ext), why: "Date-prefixed for chronological sorting." },
    ];
  }
  if (file.type === "doc") {
    const subj = (file.topic || file.name).toLowerCase().replace(/[,.]/g, "").split(" ").slice(0, 5).join("-");
    return [
      { name: safeName(subj, ext), why: "Topic distilled from contents." },
      { name: safeName(`${today}-${subj}`, ext), why: "Filed by date." },
    ];
  }
  if (file.type === "contact") {
    const who = (file.topic || "contact").split("—")[0].trim().toLowerCase().replace(/\s+/g, "-").replace(/[()]/g, "");
    return [
      { name: safeName(who, ext), why: "Person's name from card body." },
      { name: safeName(`contacts-${who}`, ext), why: "Prefixed for contact directory." },
    ];
  }
  if (file.type === "password") {
    return [
      { name: safeName("vault-main", ext), why: "Generic vault label, no PII." },
      { name: safeName(`vault-${today}`, ext), why: "Dated backup." },
    ];
  }
  if (file.type === "gamesave") {
    const game = (file.aiContent || "").split(".")[0].toLowerCase().split(" ").slice(0, 2).join("-");
    return [
      { name: safeName(`${game}-${today}`, ext), why: "Game + save date." },
      { name: safeName(`${game}-checkpoint`, ext), why: "Game + checkpoint label." },
    ];
  }
  if (file.type === "iot") {
    const dev = (file.topic || "").split("—")[0].trim().toLowerCase().replace(/\s+/g, "-");
    return [
      { name: safeName(`${dev}-${today}`, ext), why: "Device + telemetry date." },
      { name: safeName(`telemetry-${dev}`, ext), why: "Prefixed by domain." },
    ];
  }
  return [];
}

export function RenameModal({ open, file, onClose, onSave }) {
  const [name, setName] = useStateRn("");
  const [error, setError] = useStateRn(null);
  const [pickedIdx, setPickedIdx] = useStateRn(-1);
  const [thinking, setThinking] = useStateRn(false);
  // §C1.2 — when the file is an image we fetch real AI suggestions
  // from the backend; for other types or when the API isn't reachable
  // we fall back to the deterministic client suggestions.
  const [aiSuggestions, setAiSuggestions] = useStateRn([]);
  const [pendingSummary, setPendingSummary] = useStateRn(false);
  // Held by the Regenerate button so the 500ms "thinking" fake-spinner
  // doesn't fire setState on an unmounted modal when the user closes
  // mid-animation.
  const regenTimerRef = React.useRef(null);

  const fetchAi = React.useCallback(async (f) => {
    // Only images currently have backend-computed signals to draw on.
    // Future: videos go through the same path once the keyframe →
    // summarize pipeline lands.
    if (!f || (f.type !== "image" && f.type !== "video")) {
      setAiSuggestions([]);
      return false;
    }
    try {
      setThinking(true);
      const resp = await suggestNames(f.id, 3);
      setAiSuggestions(resp?.suggestions || []);
      setPendingSummary(!!resp?.pending_summary);
      return true;
    } catch {
      // Network blip / 404 — fall back to client deterministic.
      setAiSuggestions([]);
      return false;
    } finally {
      setThinking(false);
    }
  }, []);

  useEffectRn(() => {
    if (!open || !file) return;
    setName(file.name);
    setError(null);
    setPickedIdx(-1);
    setPendingSummary(false);
    fetchAi(file);
  }, [open, file, fetchAi]);

  useEffectRn(() => () => {
    if (regenTimerRef.current) clearTimeout(regenTimerRef.current);
  }, []);

  const ext = file?.ext?.toLowerCase() || "";
  // Merge order: real AI proposals first (when present), then the
  // client deterministic fallbacks. `dedupName` keeps duplicates out
  // when both paths produce the same name.
  const suggestions = useMemoRn(() => {
    const seen = new Set();
    const out = [];
    for (const s of aiSuggestions) {
      const k = (s.name || "").toLowerCase();
      if (!k || seen.has(k)) continue;
      seen.add(k);
      out.push({ name: s.name, why: s.why });
    }
    for (const s of generateSuggestions(file)) {
      const k = (s.name || "").toLowerCase();
      if (!k || seen.has(k)) continue;
      seen.add(k);
      out.push(s);
    }
    return out.slice(0, 5);
  }, [aiSuggestions, file]);

  const validate = (v) => {
    if (!v || v.trim().length === 0) return "Filename can't be empty.";
    if (!v.toLowerCase().endsWith("." + ext)) return `Must end in .${ext} (file type).`;
    const base = v.slice(0, v.length - ext.length - 1);
    if (base.length === 0) return "Need at least one character before .${ext}.".replace("${ext}", ext);
    if (/[\\/:*?"<>|]/.test(v)) return "Can't contain  \\ / : * ? \" < > |";
    if (v.length > 120) return "Keep under 120 characters.";
    return null;
  };

  const handleSave = async () => {
    const err = validate(name);
    if (err) { setError(err); return; }
    try {
      // saveRename in the parent is async and throws on backend rejection
      // (reserved Windows names, control chars, extension mismatch, …).
      // Surface the server's detail inline so the user sees exactly what
      // failed instead of a generic toast.
      if (onSave) await onSave(name);
      onClose && onClose();
    } catch (e) {
      setError(e?.detail || "Could not rename. Try a different name.");
    }
  };

  if (!file) return null;

  return (
    <ModalRn open={open} onClose={onClose} labelledBy="rn-title">
      <div className="modal__head">
        <h2 id="rn-title">
          <span className="modal__head-icon"><Icon name="pencil" size={15}/></span>
          Rename file
        </h2>
        <p>Smart suggestions are generated from this file's content. Naming rules apply: extension must stay <span className="mono">.{ext}</span>.</p>
        <ModalCloseRn onClose={onClose}/>
      </div>
      <div className="modal__body">
        <div className="label-block" style={{ marginTop: 0 }}>
          <div className="label-block__label">New name</div>
          <input
            className="input input--lg mono"
            autoFocus
            value={name}
            onChange={(e) => { setName(e.target.value); setError(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") handleSave(); }}
          />
          {error && (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--danger)", display: "flex", alignItems: "center", gap: 6 }}>
              <Icon name="alert" size={12}/> {error}
            </div>
          )}
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--ink-3)" }}>
            Allowed: letters, numbers, dots, dashes, underscores. No <span className="mono">/ \ : * ? " &lt; &gt; |</span>.
          </div>
        </div>

        <div style={{ marginTop: 22, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="kicker"><Icon name="sparkles" size={10} style={{ verticalAlign: "-1px", marginRight: 4 }}/> AI suggestions</div>
          <button
            className="btn btn--ghost btn--sm"
            onClick={async () => {
              // §C1.2 — real refetch for image/video files; clean
              // setTimeout-based "thinking" spin for the deterministic
              // fallback path so the UI still flickers honestly.
              if (regenTimerRef.current) {
                clearTimeout(regenTimerRef.current);
                regenTimerRef.current = null;
              }
              const did = await fetchAi(file);
              if (!did) {
                setThinking(true);
                regenTimerRef.current = setTimeout(() => {
                  regenTimerRef.current = null;
                  setThinking(false);
                }, 400);
              }
            }}
            title="Re-run AI naming with the latest summary signals"
          >
            <Icon name="refresh" size={12}/> Regenerate
          </button>
        </div>

        {pendingSummary && !thinking && (
          <div
            style={{
              marginTop: 8,
              padding: "6px 10px",
              fontSize: 11.5,
              color: "var(--ink-3)",
              background: "var(--surface-2)",
              borderRadius: 6,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <Icon name="info" size={11}/>
            AI summary is still running — these suggestions will sharpen once it finishes. Press Regenerate then.
          </div>
        )}
        {thinking ? (
          <div className="rename-conv">
            <Icon name="sparkles" size={12}/> Reading content and proposing names…
          </div>
        ) : (
          <div className="rename-suggestions">
            {suggestions.map((s, i) => (
              <button
                key={i}
                className="rename-suggestion"
                data-active={pickedIdx === i}
                onClick={() => { setPickedIdx(i); setName(s.name); setError(null); }}
              >
                <div className="rename-suggestion__icon"><Icon name="sparkles" size={11}/></div>
                <div className="rename-suggestion__body">
                  <div className="rename-suggestion__name">{s.name}</div>
                  <div className="rename-suggestion__why">{s.why}</div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="modal__foot">
        <span className="modal__foot-left mono">{file.name}</span>
        <div className="modal__foot-actions">
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" onClick={handleSave}>Save name</button>
        </div>
      </div>
    </ModalRn>
  );
}

// Named export above; legacy `window.RenameModal` removed.
