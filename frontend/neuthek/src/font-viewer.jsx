// Font specimen viewer — TTF / OTF / WOFF / WOFF2. Loads the served
// font bytes through the shared authed `fetchMediaBlob` helper (same
// path as the CSV / ICS / VCF viewers), registers them as a FontFace
// at runtime (no dependency — the FontFace API is built into every
// modern browser), and renders a classic type specimen in that face:
// a big editable headline, a size ramp (12 / 18 / 24 / 36 / 48 / 72
// px), the full upper / lower alphabets, digits + punctuation, and the
// file details.
//
// A FontFace loaded from an ArrayBuffer embeds the bytes, so there's
// no second network round-trip and nothing to revoke. We still tidy
// up on unmount by removing the face from document.fonts so a long
// session doesn't accumulate dozens of one-off families.
//
// "Metadata" here is what the browser exposes for free off the
// FontFace plus the byte size: family name, the detected format, and
// the file weight. (Rich name-table parsing — designer, version,
// copyright — would need an OpenType parser; out of scope for a
// preview surface, so we surface what's cheaply available.)
import React, { useState, useEffect, useRef, useCallback } from "react";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { ViewerError } from "./viewer-states.jsx";

const UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const LOWER = "abcdefghijklmnopqrstuvwxyz";
const DIGITS = "0123456789";
const PUNCT = "& . , ; : ! ? ’ “ ” ( ) [ ] { } / \\ @ # $ % * + = — –";
const PANGRAM = "The quick brown fox jumps over the lazy dog";
const RAMP_SIZES = [12, 18, 24, 36, 48, 72];
// One glyph per cell — a compact specimen grid that shows letterforms,
// figures, and the most common punctuation at a glance.
const GLYPH_GRID = (UPPER + LOWER + DIGITS + "&.,;:!?@#$%*+=-/()[]{}").split("");

// Each mounted viewer gets a unique family so two open specimens (or a
// re-open of the same file) never collide in document.fonts.
let famSeq = 0;

function detectFormat(fileName, ext) {
  const fromName = (fileName || "").toLowerCase().match(/\.([a-z0-9]+)$/)?.[1];
  const e = (ext || fromName || "").toLowerCase().replace(/^\./, "");
  if (e === "ttf") return "TrueType";
  if (e === "otf") return "OpenType";
  if (e === "woff") return "WOFF";
  if (e === "woff2") return "WOFF2";
  return e ? e.toUpperCase() : "Font";
}

function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function FontViewer({ fileId, fileName, fileExt, byteSize }) {
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState(null);
  const [loadedBytes, setLoadedBytes] = useState(null);
  const [sample, setSample] = useState(PANGRAM);
  // Live size control for the hero line (the ramp below stays fixed).
  const [heroSize, setHeroSize] = useState(56);
  const [attempt, setAttempt] = useState(0);
  // Stable across renders for this mount; the family name is what the
  // specimen rows reference via `fontFamily`.
  const familyRef = useRef(`neuthek-font-${++famSeq}`);
  const family = familyRef.current;
  const format = detectFormat(fileName, fileExt);

  useEffect(() => {
    let cancelled = false;
    let face = null;
    setStatus("loading");
    setError(null);

    (async () => {
      try {
        const blob = await fetchMediaBlob(originalMediaUrl(fileId));
        const buf = await blob.arrayBuffer();
        if (cancelled) return;
        setLoadedBytes(buf.byteLength);
        // FontFace accepts the raw ArrayBuffer for ttf/otf/woff/woff2 —
        // the browser sniffs the actual sfnt/woff signature, so we
        // don't have to pass a format hint.
        face = new FontFace(family, buf);
        await face.load();
        if (cancelled) {
          // Loaded after unmount — make sure we don't leak it.
          try { document.fonts.delete(face); } catch {}
          return;
        }
        document.fonts.add(face);
        setStatus("ready");
      } catch (e) {
        if (cancelled) return;
        setStatus("error");
        setError(e?.message || "Could not load font");
        toast.error("Couldn't load font");
      }
    })();

    return () => {
      cancelled = true;
      // Drop the face from the document so document.fonts doesn't grow
      // unbounded over a long browsing session.
      if (face) { try { document.fonts.delete(face); } catch {} }
    };
  }, [fileId, family, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const ff = `"${family}", system-ui, sans-serif`;
  const sizeBytes = byteSize ?? loadedBytes;
  const line = (sample || "").trim() || PANGRAM;

  return (
    <div className="font-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="font-viewer__head">
        <span className="vw-head__icon"><Icon name="type" size={15} /></span>
        <span className="font-viewer__name mono">{fileName}</span>
        <span className="font-viewer__tags">
          <span className="font-viewer__fmt">{format}</span>
          {sizeBytes != null && <span className="font-viewer__size mono">{fmtBytes(sizeBytes)}</span>}
        </span>
      </div>

      <div className="font-viewer__body">
        {status === "loading" ? (
          <FontSkeleton />
        ) : status === "error" ? (
          <ViewerError
            title="Couldn't load font"
            message={error || "This font file could not be decoded."}
            onRetry={retry}
            downloadUrl={originalMediaUrl(fileId)}
            downloadName={fileName}
          />
        ) : (
          <div className="font-viewer__doc">
            {/* Editable headline — type to preview your own text. */}
            <div className="font-viewer__hero-wrap">
              <input
                className="font-viewer__hero"
                style={{ fontFamily: ff, fontSize: heroSize }}
                value={sample}
                onChange={(e) => setSample(e.target.value)}
                spellCheck={false}
                aria-label="Sample text"
                placeholder="Type to preview…"
              />
              <div className="font-viewer__sizer">
                <input
                  type="range"
                  min={16}
                  max={120}
                  step={1}
                  value={heroSize}
                  onChange={(e) => setHeroSize(Number(e.target.value))}
                  aria-label="Preview size"
                  className="font-viewer__slider"
                />
                <span className="font-viewer__sizeval mono">{heroSize}px</span>
              </div>
            </div>

            <FvSection label="Glyphs">
              <div className="font-viewer__grid" style={{ fontFamily: ff }} aria-label="Glyph grid">
                {GLYPH_GRID.map((g, i) => (
                  <span key={i} className="font-viewer__cell" title={g}>{g}</span>
                ))}
              </div>
            </FvSection>

            <FvSection label="Size ramp">
              <div className="font-viewer__ramp">
                {RAMP_SIZES.map((sz) => (
                  <div key={sz} className="font-viewer__ramp-row">
                    <span className="font-viewer__ramp-size mono">{sz}</span>
                    <span className="font-viewer__ramp-text" style={{ fontFamily: ff, fontSize: sz }}>
                      {line}
                    </span>
                  </div>
                ))}
              </div>
            </FvSection>

            <FvSection label="Uppercase">
              <div className="font-viewer__glyphs" style={{ fontFamily: ff }}>{UPPER}</div>
            </FvSection>

            <FvSection label="Lowercase">
              <div className="font-viewer__glyphs" style={{ fontFamily: ff }}>{LOWER}</div>
            </FvSection>

            <FvSection label="Numerals & punctuation">
              <div className="font-viewer__glyphs" style={{ fontFamily: ff }}>{DIGITS}</div>
              <div className="font-viewer__glyphs font-viewer__glyphs--punct" style={{ fontFamily: ff }}>{PUNCT}</div>
            </FvSection>

            <FvSection label="Details">
              <dl className="font-viewer__kv">
                <FvRow k="Format" v={format} />
                <FvRow k="File size" v={fmtBytes(sizeBytes)} />
                <FvRow k="Family (runtime)" v={family} />
              </dl>
            </FvSection>
          </div>
        )}
      </div>
    </div>
  );
}

function FvSection({ label, children }) {
  return (
    <section className="font-viewer__section">
      <div className="font-viewer__section-label">{label}</div>
      {children}
    </section>
  );
}

// Specimen-shaped shimmer: a big hero block, then a couple of ramp rows.
function FontSkeleton() {
  return (
    <div className="font-skel" aria-busy="true" aria-live="polite">
      <span className="vw-sr">Loading font…</span>
      <div className="vw-shimmer" style={{ width: "78%", height: 52, borderRadius: 10 }} />
      <div className="font-skel__ramp">
        {[26, 34, 46, 60].map((h, i) => (
          <div key={i} className="vw-shimmer" style={{ width: `${70 - i * 9}%`, height: h, borderRadius: 8 }} />
        ))}
      </div>
    </div>
  );
}

function FvRow({ k, v }) {
  return (
    <div className="font-viewer__kv-row">
      <dt>{k}</dt>
      <dd className="mono">{v}</dd>
    </div>
  );
}
