// HTML viewer — renders an uploaded .html / .htm file as a STATIC,
// non-runnable preview inside a locked-down sandboxed iframe, with a
// Rendered / Source segmented toggle.
//
// Security model — "view, not run":
//   The document is rendered via `<iframe sandbox srcDoc={html}>` with a
//   sandbox attribute that DELIBERATELY omits `allow-scripts`. With no
//   `allow-scripts` token:
//     - <script> tags, inline event handlers, and javascript: URLs never
//       execute;
//     - the iframe is forced into a unique opaque origin (we also omit
//       `allow-same-origin`), so even if script could run it could not
//       touch our DOM, cookies, or localStorage;
//     - forms can't submit, popups can't open, and the framed page can't
//       navigate the top window (no `allow-forms` / `allow-popups` /
//       `allow-top-navigation`).
//   The backend additionally sanitizes uploaded SVG/HTML server-side; this
//   sandbox is the belt-and-suspenders client layer so a hostile document
//   is inert in the browser regardless. The sandbox string stays "" — do
//   NOT add tokens here.
//
// `srcDoc` (vs a blob: src) keeps the bytes in-document so there's no blob
// URL to leak/revoke, and the sandbox's opaque origin applies cleanly.
//
// Data load mirrors markdown-viewer / csv-viewer exactly: pull the original
// bytes through `fetchMediaBlob(originalMediaUrl(fileId))` (cookie auth +
// legacy-Bearer fallback live in that helper) and decode as text.
//
// Raw-source toggle reuses the shared <CodeView> from code-preview.jsx
// (HTML grammar via the `text/html` mime) so the source view gets the same
// line gutter / find-in-file / copy affordances as the code preview.
//
// Styling lives in styles-model3d.css (the `.htmlv__*` block), co-located
// with the 3D viewer since both are "render-with-a-source-toggle" surfaces
// and share the toolbar / segmented-control vocabulary.
//
// Props: { fileId, fileName } — same contract as the other full-display
// viewers so preview.jsx can mount it inside the shared `pdf-modal__body`.
import React, { useState, useEffect } from "react";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { CodeView } from "./code-preview.jsx";

export function HtmlViewer({ fileId, fileName }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);
  const [view, setView] = useState("rendered"); // rendered | source

  useEffect(() => {
    let cancelled = false;
    setText(null);
    setErr(null);
    setView("rendered");
    (async () => {
      try {
        const blob = await fetchMediaBlob(originalMediaUrl(fileId));
        const body = await blob.text();
        if (!cancelled) setText(body);
      } catch (e) {
        if (cancelled) return;
        setErr(e?.message || "Could not load file");
        toast.error("Couldn't load document");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId]);

  const loading = text == null && !err;

  return (
    <div className="htmlv" onClick={(e) => e.stopPropagation()}>
      {/* ---- header ---- */}
      <div className="htmlv__bar">
        <span className="htmlv__name">{fileName}</span>
        {/* "view, not runnable" affordance — makes the safety model legible
            so the user understands why an interactive page looks inert. */}
        <span
          className="htmlv__pill"
          title="Scripts, forms, and navigation are disabled in this preview."
        >
          <Icon name="shield" size={11} />
          Static preview — scripts disabled
        </span>

        <span className="htmlv__spacer" />

        {/* Rendered / Source segmented toggle — only meaningful once the
            bytes are in hand; disabled-looking (hidden) while loading. */}
        {!loading && !err && (
          <div className="htmlv__seg" role="tablist" aria-label="View mode">
            <button
              type="button"
              role="tab"
              aria-selected={view === "rendered"}
              className="htmlv__segbtn"
              data-active={view === "rendered" ? "true" : "false"}
              onClick={() => setView("rendered")}
            >
              <Icon name="eye" size={13} />
              <span>Rendered</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "source"}
              className="htmlv__segbtn"
              data-active={view === "source" ? "true" : "false"}
              onClick={() => setView("source")}
            >
              <Icon name="code" size={13} />
              <span>Source</span>
            </button>
          </div>
        )}
      </div>

      {/* ---- body ---- */}
      <div className="htmlv__body">
        {err ? (
          <div className="htmlv__state htmlv__state--error">
            <div className="htmlv__state-icon">
              <Icon name="alert" size={22} strokeWidth={1.5} />
            </div>
            <div className="htmlv__state-text">{err}</div>
          </div>
        ) : loading ? (
          // Skeleton page bars so the surface doesn't flash empty.
          <div className="htmlv__skel" aria-label="Loading document">
            <div className="htmlv__skel-bar" style={{ width: "45%" }} />
            <div className="htmlv__skel-bar" />
            <div className="htmlv__skel-bar" style={{ width: "92%" }} />
            <div className="htmlv__skel-bar" style={{ width: "78%" }} />
            <div className="htmlv__skel-bar" style={{ width: "88%" }} />
            <div className="htmlv__skel-bar" style={{ width: "40%" }} />
          </div>
        ) : view === "source" ? (
          // Raw source: reuse the shared code viewer (gutter / find / copy).
          // `text/html` selects Prism's markup grammar.
          <div className="htmlv__source">
            <CodeView text={text} filename={fileName} mime="text/html" />
          </div>
        ) : (
          // Rendered: a neutral "deck" with the page floated as a max-width
          // card (page shadow), so it reads like a document, not a raw
          // frame. The iframe is a LOCKED-DOWN sandbox: NO `allow-scripts`
          // and NO `allow-same-origin` — the document is inert and runs in
          // an opaque origin. `srcDoc` injects the bytes directly (no blob
          // URL to leak). `referrerpolicy=no-referrer` keeps the framed
          // page from leaking our URL when it loads remote assets.
          <div className="htmlv__deck">
            <div className="htmlv__page">
              <iframe
                className="htmlv__frame"
                title={fileName || "HTML preview"}
                srcDoc={text}
                sandbox=""
                referrerPolicy="no-referrer"
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
