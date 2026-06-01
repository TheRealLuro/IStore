// Data-tree viewer — for structured config/data files (JSON, YAML, XML).
// Parses the source into a plain JS value and renders it as an
// interactive, collapsible tree: every object/array node expands and
// collapses, leaf values are typed and colored, vertical line-guides
// connect a node to its children, and any node's value can be copied to
// the clipboard. A header toggle flips to the raw source, plus
// expand-all / collapse-all controls.
//
// Parsing:
//   .json / json mime  → native JSON.parse
//   .yaml / .yml       → js-yaml `load` (safe by default in v4 — no
//                        custom-type construction, so a malicious YAML
//                        tag can't instantiate arbitrary objects)
//   .xml               → fast-xml-parser, attributes folded under an
//                        "@_" prefix and text under "#text", which we
//                        render as ordinary tree nodes
//
// The format is chosen from the file extension first (stable across our
// upload pipeline) and falls back to sniffing the first non-space byte
// ({/[ → JSON, < → XML, else YAML). If parsing fails for the detected
// format we cascade through the others, and if everything fails we show
// the raw text with an inline "couldn't parse" note instead of an error
// wall — the user still sees their file.
//
// Data load matches csv-viewer / ics-viewer: original bytes via
// `fetchMediaBlob(originalMediaUrl(fileId))`, decoded as text.
//
// Props: { fileId, fileName, ext } — `ext` is optional; when omitted the
// format is sniffed from the content. preview.jsx passes file.ext.
import React, { useState, useEffect, useMemo, useCallback } from "react";
import toast from "react-hot-toast";
import yaml from "js-yaml";
import { XMLParser } from "fast-xml-parser";
import { Icon } from "./icons.jsx";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { ViewerSkeleton, ViewerError, CopyButton } from "./viewer-states.jsx";

// ---- parsing ---------------------------------------------------------

const xmlParser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: "@_",
  textNodeName: "#text",
  // Keep declared order where the lib can, parse numbers/bools so leaf
  // badges read naturally rather than every scalar being a string.
  parseAttributeValue: true,
  parseTagValue: true,
  trimValues: true,
});

function normalizeExt(ext) {
  return (ext || "").toString().toLowerCase().replace(/^\./, "");
}

function sniffFormat(text) {
  const t = (text || "").replace(/^﻿/, "").trimStart();
  if (!t) return "yaml";
  const c = t[0];
  if (c === "{" || c === "[") return "json";
  if (c === "<") return "xml";
  return "yaml";
}

// Try the formats in a sensible order (preferred first), returning the
// first that parses. Each parser is wrapped so a throw just moves to the
// next candidate.
function parseStructured(text, ext) {
  const preferred =
    ["json"].includes(normalizeExt(ext)) ? "json" :
    ["yaml", "yml"].includes(normalizeExt(ext)) ? "yaml" :
    ["xml"].includes(normalizeExt(ext)) ? "xml" :
    sniffFormat(text);

  const order = [preferred, ...["json", "yaml", "xml"].filter((f) => f !== preferred)];
  const tryers = {
    json: (s) => JSON.parse(s),
    // load (not loadAll) — preview a single document. DEFAULT_SCHEMA in
    // js-yaml@4 is the safe schema (no !!js/function etc.).
    yaml: (s) => {
      const v = yaml.load(s);
      // js-yaml returns undefined for an all-comments/empty doc; treat
      // that as "not really YAML" so we fall through rather than show a
      // blank tree.
      if (v === undefined) throw new Error("empty");
      return v;
    },
    xml: (s) => xmlParser.parse(s),
  };
  for (const fmt of order) {
    try {
      return { value: tryers[fmt](text), format: fmt, ok: true };
    } catch { /* next */ }
  }
  return { value: null, format: preferred, ok: false };
}

// ---- value typing ----------------------------------------------------

function valueType(v) {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  const t = typeof v;
  if (t === "object") return "object";
  return t; // string | number | boolean | undefined | bigint | function
}

function isContainer(v) {
  return v !== null && typeof v === "object";
}

function entriesOf(v) {
  // Array → [index, item]; object → [key, value]. Stable insertion order.
  if (Array.isArray(v)) return v.map((item, i) => [i, item]);
  return Object.keys(v).map((k) => [k, v[k]]);
}

function previewScalar(v) {
  const t = valueType(v);
  if (t === "string") {
    const s = v.length > 120 ? v.slice(0, 120) + "…" : v;
    return `"${s}"`;
  }
  if (t === "null") return "null";
  if (t === "undefined") return "undefined";
  return String(v);
}

// Short summary shown on a COLLAPSED container, e.g. `{ 3 keys }` or
// `[ 12 items ]`, so a folded node still tells you what's inside.
function containerSummary(v) {
  if (Array.isArray(v)) {
    return `[ ${v.length} ${v.length === 1 ? "item" : "items"} ]`;
  }
  const n = Object.keys(v).length;
  return `{ ${n} ${n === 1 ? "key" : "keys"} }`;
}

// ---- copy ------------------------------------------------------------

// Stringify any node value to clipboard text: scalars as-is, containers
// as pretty JSON. Used as the `text` for the shared CopyButton.
function copyText(v) {
  try {
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

// ---- search highlight ------------------------------------------------

// Split `text` around case-insensitive occurrences of `q`, wrapping the
// matched runs in <mark>. Returns the raw string when there's no query so
// non-searching renders stay allocation-free.
function highlight(text, q) {
  if (!q) return text;
  const s = String(text);
  const lower = s.toLowerCase();
  const needle = q.toLowerCase();
  const out = [];
  let from = 0;
  let idx = lower.indexOf(needle, from);
  if (idx === -1) return s;
  let k = 0;
  while (idx !== -1) {
    if (idx > from) out.push(s.slice(from, idx));
    out.push(<mark key={k++} className="dtv__mark">{s.slice(idx, idx + needle.length)}</mark>);
    from = idx + needle.length;
    idx = lower.indexOf(needle, from);
  }
  if (from < s.length) out.push(s.slice(from));
  return out;
}

function nodeMatches(nodeKey, value, q) {
  if (!q) return false;
  const needle = q.toLowerCase();
  if (nodeKey != null && String(nodeKey).toLowerCase().includes(needle)) return true;
  if (!isContainer(value) && previewScalar(value).toLowerCase().includes(needle)) return true;
  return false;
}

// ---- tree node -------------------------------------------------------

function TreeNode({ nodeKey, value, depth, openSignal, query }) {
  const container = isContainer(value);
  // openSignal: a [generation, openState] pair pushed from the header's
  // expand-all / collapse-all. We seed local open from the default and
  // re-sync whenever the generation changes.
  const [open, setOpen] = useState(depth < 2);
  const lastSig = React.useRef(openSignal?.[0]);
  if (openSignal && openSignal[0] !== lastSig.current) {
    lastSig.current = openSignal[0];
    if (open !== openSignal[1]) setOpen(openSignal[1]);
  }
  // While a search is active every container is forced open so matches
  // deep in the tree are actually visible.
  const effectiveOpen = query ? true : open;

  const type = valueType(value);
  const hit = nodeMatches(nodeKey, value, query);

  const keyLabel =
    nodeKey === null ? null : (
      <span className="dtv__key mono">
        {highlight(typeof nodeKey === "number" ? nodeKey : JSON.stringify(nodeKey).slice(1, -1), query)}
      </span>
    );

  if (!container) {
    return (
      <div className={`dtv__row dtv__row--leaf${hit ? " is-hit" : ""}`}>
        <span className="dtv__caret dtv__caret--empty" aria-hidden="true" />
        <span className={`dtv__dot dtv__dot--${type}`} aria-hidden="true" />
        {keyLabel}
        {keyLabel && <span className="dtv__colon">:</span>}
        <span className={`dtv__val dtv__val--${type} mono`}>{highlight(previewScalar(value), query)}</span>
        <span className="dtv__type mono">{type}</span>
        <CopyButton text={copyText(value)} variant="text" title="Copy value" className="dtv__copy" />
      </div>
    );
  }

  const entries = entriesOf(value);
  return (
    <div className="dtv__node">
      <div className={`dtv__row dtv__row--branch${hit ? " is-hit" : ""}`}>
        <button
          type="button"
          className={`dtv__caret${effectiveOpen ? " dtv__caret--open" : ""}`}
          aria-expanded={effectiveOpen}
          aria-label={effectiveOpen ? "Collapse" : "Expand"}
          onClick={() => setOpen((o) => !o)}
          disabled={!!query}
        >
          ▶
        </button>
        <span className={`dtv__dot dtv__dot--${type}`} aria-hidden="true" />
        {keyLabel}
        {keyLabel && <span className="dtv__colon">:</span>}
        <button type="button" className="dtv__summary mono" onClick={() => setOpen((o) => !o)} disabled={!!query}>
          {effectiveOpen ? (Array.isArray(value) ? "[" : "{") : containerSummary(value)}
        </button>
        <span className="dtv__type mono">
          {type}{Array.isArray(value) ? `(${value.length})` : ""}
        </span>
        <CopyButton text={copyText(value)} variant="text" title="Copy subtree as JSON" className="dtv__copy" />
      </div>
      {effectiveOpen && (
        <div className="dtv__children">
          {entries.length === 0 ? (
            <div className="dtv__empty mono">{Array.isArray(value) ? "empty array" : "empty object"}</div>
          ) : (
            entries.map(([k, v]) => (
              <TreeNode key={String(k)} nodeKey={k} value={v} depth={depth + 1} openSignal={openSignal} query={query} />
            ))
          )}
          <div className="dtv__row dtv__row--close mono">{Array.isArray(value) ? "]" : "}"}</div>
        </div>
      )}
    </div>
  );
}

// ---- top-level component --------------------------------------------

export function DataTreeViewer({ fileId, fileName, ext }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);
  const [raw, setRaw] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  // Bumped on expand-all / collapse-all; the boolean is the target state.
  const [openSignal, setOpenSignal] = useState([0, true]);

  useEffect(() => {
    let cancelled = false;
    setText(null);
    setErr(null);
    (async () => {
      try {
        const blob = await fetchMediaBlob(originalMediaUrl(fileId));
        const body = await blob.text();
        if (!cancelled) setText(body);
      } catch (e) {
        if (cancelled) return;
        setErr(e?.message || "Could not load file");
        toast.error("Couldn't load file");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId, attempt]);

  const parsed = useMemo(() => (text == null ? null : parseStructured(text, ext)), [text, ext]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  // If the parse failed, force the raw view (and remember the user can't
  // flip back to a tree that doesn't exist).
  const showRaw = raw || (parsed && !parsed.ok);
  const setAll = (state) => setOpenSignal(([g]) => [g + 1, state]);
  const q = query.trim();

  return (
    <div className="dtv" onClick={(e) => e.stopPropagation()}>
      <div className="dtv__head">
        <span className="vw-head__icon"><Icon name="layers" size={15} /></span>
        <span className="dtv__name">{fileName}</span>
        <span className="dtv__meta mono">
          {parsed ? (parsed.ok ? parsed.format.toUpperCase() : "unparsed") : ""}
        </span>
        {parsed && parsed.ok && !showRaw && (
          <label className="dtv__search" title="Filter keys and values">
            <Icon name="search" size={13} />
            <input
              type="text"
              className="dtv__search-input mono"
              placeholder="Find…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              spellCheck={false}
              aria-label="Search tree"
            />
            {q && (
              <button type="button" className="dtv__search-clear" onClick={() => setQuery("")} aria-label="Clear search" title="Clear">
                <Icon name="x" size={12} />
              </button>
            )}
          </label>
        )}
        {parsed && parsed.ok && !showRaw && !q && (
          <span className="dtv__expanders">
            <button type="button" className="dtv__expbtn mono" onClick={() => setAll(true)} title="Expand all">Expand</button>
            <button type="button" className="dtv__expbtn mono" onClick={() => setAll(false)} title="Collapse all">Collapse</button>
          </span>
        )}
        {parsed && parsed.ok && (
          <button
            type="button"
            className="dtv__toggle mono"
            onClick={() => setRaw((v) => !v)}
            aria-pressed={raw}
            title={raw ? "Show tree" : "Show raw source"}
          >
            {raw ? "Tree" : "Raw"}
          </button>
        )}
      </div>
      <div className="dtv__body">
        {err ? (
          <ViewerError
            title="Couldn't load file"
            message={err}
            onRetry={retry}
            downloadUrl={originalMediaUrl(fileId)}
            downloadName={fileName}
          />
        ) : text == null ? (
          <ViewerSkeleton lines={["30%", "52%", "44%", "60%", "38%", "48%", "34%"]} className="dtv-skel" />
        ) : showRaw ? (
          <>
            {parsed && !parsed.ok && (
              <div className="dtv__warn">
                <Icon name="alert" size={13} />
                Couldn't parse this as {parsed.format.toUpperCase()} — showing raw source.
              </div>
            )}
            <pre className="dtv__raw mono">{text}</pre>
          </>
        ) : (
          <div className="dtv__tree mono">
            <TreeNode nodeKey={null} value={parsed.value} depth={0} openSignal={openSignal} query={q} />
          </div>
        )}
      </div>
    </div>
  );
}
