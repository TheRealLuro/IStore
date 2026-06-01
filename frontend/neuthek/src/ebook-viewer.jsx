// EPUB reader — paginated reading view built on epubjs. Loads the
// served EPUB bytes through the shared authed `fetchMediaBlob` helper
// (same path as the CSV / ICS / VCF viewers), hands the ArrayBuffer to
// epubjs (`ePub(buffer)`), renders a single-page paginated flow into a
// centered readable column, registers a dark theme so the page matches
// neuthek's surface, and wires prev / next + a collapsible TOC drawn
// from the book's navigation (spine order is respected by epubjs's own
// next()/prev()).
//
// epubjs unpacks the EPUB zip in-memory (jszip) and renders each
// section inside a sandboxed iframe it manages; we never touch the
// DOM it owns. Cleanup is a single `book.destroy()` which tears down
// the rendition, its iframes, and the unpacked archive — without it
// the detached iframes + blob URLs epubjs created leak across opens.
import React, { useState, useEffect, useRef, useCallback } from "react";
import toast from "react-hot-toast";
import ePub from "epubjs";
import { Icon } from "./icons.jsx";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { ViewerError } from "./viewer-states.jsx";

// Reader font-size presets (percent applied to epubjs's base). The
// reader remembers the choice across page turns within a session.
const FONT_STEPS = [90, 100, 110, 125, 140];

// Dark theme injected into every rendered section's iframe. epubjs
// scopes these rules inside the section document, so we restate the
// palette literally (CSS custom properties from the host page don't
// cross the iframe boundary). Values mirror neuthek's dark surface /
// ink tokens.
const DARK_THEME = {
  body: {
    background: "#16181d",
    color: "#d6d9df",
    "font-family": "Georgia, 'Times New Roman', serif",
    "line-height": "1.7",
    padding: "0 4px",
  },
  p: { color: "#d6d9df" },
  a: { color: "#7aa2f7", "text-decoration": "none" },
  "h1, h2, h3, h4, h5, h6": { color: "#f2f4f8" },
  "img, image": { "max-width": "100%", height: "auto" },
  "::selection": { background: "rgba(122,162,247,0.35)" },
};

export function EbookViewer({ fileId, fileName }) {
  const viewerRef = useRef(null);
  // epubjs Book + Rendition kept on a ref so the cleanup closure and
  // the prev/next handlers can reach the live instances.
  const bookRef = useRef(null);
  const renditionRef = useRef(null);

  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState(null);
  const [toc, setToc] = useState([]);
  const [showToc, setShowToc] = useState(false);
  const [label, setLabel] = useState(""); // current chapter / location label
  const [progress, setProgress] = useState(0); // 0..1 across the book
  const [fontPct, setFontPct] = useState(100); // reader font scale
  const [attempt, setAttempt] = useState(0); // bumped by Retry

  useEffect(() => {
    const host = viewerRef.current;
    if (!host) return;

    let destroyed = false;
    let book = null;
    let rendition = null;
    let ro = null;
    // Re-flow the paginated rendition to the host's live box. epubjs sizes
    // its iframe + column geometry to whatever the page element measured at
    // renderTo() time; in a modal that's still laying out (or a fixed-width
    // `--fill` body that the host fills only after first paint) that initial
    // read can be narrow/zero, pinning the reader to the left and stranding
    // the right half blank. Measuring the host and calling rendition.resize
    // on every container change keeps the page filling the full width.
    const resizeToHost = () => {
      if (destroyed || !rendition) return;
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (!w || !h) return; // not laid out yet — the observer fires again
      try { rendition.resize(w, h); } catch {}
    };

    (async () => {
      try {
        const blob = await fetchMediaBlob(originalMediaUrl(fileId));
        const buf = await blob.arrayBuffer();
        if (destroyed) return;

        // ePub() accepts the EPUB zip as an ArrayBuffer; epubjs unzips
        // it in memory. No URL / auth needed past this point.
        book = ePub(buf);
        bookRef.current = book;

        rendition = book.renderTo(host, {
          flow: "paginated",
          width: "100%",
          height: "100%",
          // One column per view — a clean single page rather than a
          // two-up spread, which reads better in a modal.
          spread: "none",
        });
        renditionRef.current = rendition;

        // Keep the rendition matched to the host box as the modal settles
        // and on any later resize (e.g. the 94vw modal when the window
        // changes). Observe the page host; correct the first measurement on
        // the next frame in case renderTo() read a pre-layout 0/narrow box.
        if (typeof ResizeObserver !== "undefined") {
          ro = new ResizeObserver(resizeToHost);
          ro.observe(host);
        }
        requestAnimationFrame(resizeToHost);

        rendition.themes.register("neuthek-dark", DARK_THEME);
        rendition.themes.select("neuthek-dark");

        await rendition.display();
        if (destroyed) return;

        // TOC from the book's navigation (spine-ordered). Flatten one
        // level of nesting so sub-chapters are still reachable.
        try {
          const nav = await book.loaded.navigation;
          if (!destroyed && nav?.toc) {
            const flat = [];
            const walk = (items, depth) => {
              for (const it of items) {
                flat.push({ href: it.href, label: (it.label || "").trim(), depth });
                if (it.subitems && it.subitems.length) walk(it.subitems, depth + 1);
              }
            };
            walk(nav.toc, 0);
            setToc(flat);
          }
        } catch { /* some EPUBs ship no nav doc — TOC stays empty */ }

        // Build the location index in the background so we can show a
        // reading-progress percentage. Cheap for typical books; safe
        // to skip if it throws on a malformed spine.
        try {
          await book.ready;
          if (!destroyed && book.locations) {
            await book.locations.generate(1600);
          }
        } catch { /* progress percentage just stays at chapter level */ }

        if (!destroyed) setStatus("ready");
      } catch (e) {
        if (destroyed) return;
        setStatus("error");
        setError(e?.message || "Could not load book");
        toast.error("Couldn't load EPUB");
      }
    })();

    return () => {
      destroyed = true;
      if (ro) { try { ro.disconnect(); } catch {} ro = null; }
      // Single call tears down the rendition, its iframes, and the
      // in-memory archive. Guarded — destroy() throws if the book
      // never finished opening.
      try { bookRef.current?.destroy(); } catch {}
      bookRef.current = null;
      renditionRef.current = null;
    };
  }, [fileId, attempt]);

  // Apply the reader font scale to the live rendition whenever it changes
  // (epubjs exposes a per-rendition fontSize override).
  useEffect(() => {
    if (status !== "ready") return;
    try { renditionRef.current?.themes?.fontSize(`${fontPct}%`); } catch {}
  }, [fontPct, status]);

  // Keep the chapter label + progress bar in sync as the reader pages
  // through. `relocated` fires on every page turn with the start CFI.
  useEffect(() => {
    const rendition = renditionRef.current;
    if (status !== "ready" || !rendition) return;
    const onRelocated = (location) => {
      const book = bookRef.current;
      const href = location?.start?.href;
      if (book?.navigation && href) {
        const item = book.navigation.get(href);
        if (item?.label) setLabel(item.label.trim());
      }
      // Percentage comes from the generated locations index when ready.
      const pct = location?.start?.percentage;
      if (typeof pct === "number") setProgress(pct);
    };
    rendition.on("relocated", onRelocated);
    return () => { try { rendition.off("relocated", onRelocated); } catch {} };
  }, [status]);

  // Keyboard paging — arrows page within the reader. Esc is owned by
  // preview.jsx's modal handler, so we don't touch it here.
  useEffect(() => {
    if (status !== "ready") return;
    const onKey = (e) => {
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if (e.key === "ArrowLeft") { e.preventDefault(); prev(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); next(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const prev = () => { try { renditionRef.current?.prev(); } catch {} };
  const next = () => { try { renditionRef.current?.next(); } catch {} };
  const goTo = (href) => {
    setShowToc(false);
    try { renditionRef.current?.display(href); } catch {}
  };
  const retry = useCallback(() => { setStatus("loading"); setError(null); setAttempt((n) => n + 1); }, []);
  const cycleFont = useCallback(() => {
    setFontPct((cur) => {
      const i = FONT_STEPS.indexOf(cur);
      return FONT_STEPS[(i + 1) % FONT_STEPS.length] ?? 100;
    });
  }, []);

  return (
    <div className="ebook-viewer" onClick={(e) => e.stopPropagation()}>
      {/* Header — TOC toggle, chapter / file name, font-size, progress. */}
      <div className="ebook-viewer__head">
        <button
          type="button"
          className="ebook-viewer__tocbtn"
          onClick={() => setShowToc((v) => !v)}
          aria-label="Table of contents"
          aria-pressed={showToc}
          title="Table of contents"
          disabled={toc.length === 0}
        >
          <Icon name="list" size={15} />
        </button>
        <span className="ebook-viewer__title mono">{label || fileName}</span>
        {status === "ready" && (
          <button
            type="button"
            className="ebook-viewer__fontbtn mono"
            onClick={cycleFont}
            title={`Reading size — ${fontPct}%`}
            aria-label={`Reading size ${fontPct}%, click to change`}
          >
            A<span className="ebook-viewer__fontbtn-sm">A</span>
          </button>
        )}
        {status === "ready" && progress > 0 && (
          <span className="ebook-viewer__pct mono">{Math.round(progress * 100)}%</span>
        )}
      </div>

      {/* Thin progress bar under the header. */}
      <div className="ebook-viewer__progress" aria-hidden="true">
        <div className="ebook-viewer__progress-fill" style={{ width: `${Math.round(progress * 100)}%` }} />
      </div>

      {/* Reader area — epubjs renders into `viewerRef`. The TOC drawer
          overlays from the left when open. */}
      <div className="ebook-viewer__stage">
        {showToc && toc.length > 0 && (
          <nav className="ebook-viewer__toc">
            {toc.map((item, i) => (
              <button
                key={`${item.href}-${i}`}
                type="button"
                className="ebook-viewer__toc-item"
                onClick={() => goTo(item.href)}
                style={{ paddingLeft: 16 + item.depth * 14 }}
              >
                {item.label || "(untitled)"}
              </button>
            ))}
          </nav>
        )}

        {/* Prev / next click zones flank the page. */}
        <button
          type="button"
          onClick={prev}
          aria-label="Previous page"
          title="Previous (←)"
          className="ebook-viewer__nav ebook-viewer__nav--prev"
        >
          <Icon name="chevronLeft" size={22} />
        </button>

        <div ref={viewerRef} className="ebook-viewer__page" />

        <button
          type="button"
          onClick={next}
          aria-label="Next page"
          title="Next (→)"
          className="ebook-viewer__nav ebook-viewer__nav--next"
        >
          <Icon name="chevronRight" size={22} />
        </button>

        {status === "loading" && (
          <div className="ebook-viewer__overlay" aria-busy="true">
            <div className="ebook-skel">
              <span className="vw-sr">Opening book…</span>
              {["72%", "94%", "88%", "96%", "60%", "90%", "84%", "50%"].map((w, i) => (
                <div key={i} className="vw-shimmer ebook-skel__line" style={{ width: w }} />
              ))}
            </div>
          </div>
        )}
        {status === "error" && (
          <div className="ebook-viewer__overlay ebook-viewer__overlay--err">
            <ViewerError
              title="Couldn't open book"
              message={error || "This EPUB could not be opened."}
              onRetry={retry}
              downloadUrl={originalMediaUrl(fileId)}
              downloadName={fileName}
            />
          </div>
        )}
      </div>
    </div>
  );
}
