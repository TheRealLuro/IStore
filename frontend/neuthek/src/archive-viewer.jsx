// Archive viewer — browse the contents of a zip / tar / gz / 7z / rar
// the user owns, and preview a single file from INSIDE it without
// downloading the whole archive.
//
// Two backend calls (see backend/api/archives.py):
//   GET /archives/{id}/list            → { tree: [{name,path,size,is_dir,children}], … }
//   GET /archives/{id}/file?path=…     → the inner file's bytes, content-type sniffed
//
// The left pane is a collapsible folder/file tree with sizes. Clicking
// a file fetches it through the extract endpoint and previews it inline
// in the right pane using the SAME per-type viewers the main gallery
// uses (routed by extension via file-types.js): images render as <img>,
// PDFs in an <iframe>, and code / text / markdown / JSON / YAML / XML /
// CSV / notebook files render in the rich syntax-highlighted CodeView
// (line gutter, find-in-file, copy, word-wrap) reused from
// code-preview.jsx. Anything else falls back to a download button.
// Mirrors the minimal dark aesthetic + authed-fetch pattern of
// csv-viewer.jsx / ics-viewer.jsx (which read via @/api/files); the
// archive endpoints aren't image-media URLs, so we do the same
// cookie+legacy-bearer fetch fetchMediaBlob uses, pointed at /archives.
import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { API_BASE_URL } from "@/api/client";
import { CodeView, mimeFromFilename } from "./code-preview.jsx";
import { LanguageIcon, languageIcon } from "./language-icons.jsx";
import { fileTypeInfo } from "./file-types.js";

// Self-contained styles. The component is a deliberate orphan (not yet
// wired), so rather than add dead rules to the shared styles.css spine,
// we ship the CSS with the component and inject it once on first mount.
// Uses the app's design tokens (--surface / --ink / --line / --radius-*)
// so it tracks the light/dark theme automatically.
const ARCHIVE_VIEWER_CSS = `
.archive-viewer { display:flex; flex-direction:column; height:100%; min-height:0;
  background:var(--surface); color:var(--ink); }

/* ---------- header ---------- */
.archive-viewer__head { display:flex; align-items:center; gap:9px;
  height:44px; padding:0 8px 0 14px; flex-shrink:0;
  border-bottom:1px solid var(--line); background:var(--surface-2); color:var(--ink-3); }
.archive-viewer__head > svg { color:var(--ink-3); flex-shrink:0; }
.archive-viewer__title { color:var(--ink); font-weight:600; font-size:12.5px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
.archive-viewer__meta { margin-left:auto; font-size:11px; color:var(--ink-3);
  white-space:nowrap; font-variant-numeric:tabular-nums; }
.archive-viewer__head .btn-icon { margin-left:4px; flex-shrink:0; }

/* ---------- two-pane split (resizable) ----------
   Column width is driven by a CSS var the JS resizer updates, clamped so
   neither pane can collapse. A thin draggable gutter sits between them. */
.archive-viewer__split { display:grid;
  grid-template-columns:
    clamp(180px, var(--arcv-tree-w, 264px), 60%)
    1px
    1fr;
  flex:1; min-height:0; }
.archive-viewer__tree { overflow:auto; padding:6px 0 8px;
  background:var(--surface-2); min-height:0;
  scrollbar-width:thin; scrollbar-color:var(--line-2) transparent; }
.archive-viewer__tree::-webkit-scrollbar { width:9px; }
.archive-viewer__tree::-webkit-scrollbar-thumb {
  background:var(--line-2); border-radius:5px; border:2px solid var(--surface-2); }
.archive-viewer__tree::-webkit-scrollbar-thumb:hover { background:var(--ink-4); }

/* Drag handle between the panes. The 1px column is visually the border;
   the handle widens its hit area with a transparent overlay via padding. */
.archive-viewer__gutter { position:relative; background:var(--line);
  cursor:col-resize; }
.archive-viewer__gutter::before { content:""; position:absolute; inset:0 -4px;
  z-index:2; }
.archive-viewer__gutter::after { content:""; position:absolute;
  top:50%; left:50%; transform:translate(-50%,-50%);
  width:3px; height:26px; border-radius:3px; background:var(--ink-4);
  opacity:0; transition:opacity .14s ease; }
.archive-viewer__gutter:hover::after,
.archive-viewer__gutter[data-dragging="true"]::after { opacity:.55; }
.archive-viewer__gutter[data-dragging="true"] { background:var(--ink-4); }

.archive-viewer__pane { overflow:hidden; display:flex; min-height:0; min-width:0; }

/* ---------- tree rows ---------- */
.archive-viewer__row { display:flex; align-items:center; gap:6px; width:100%;
  padding:5px 10px 5px 8px; background:none; border:0; color:var(--ink-2);
  font:inherit; font-size:12.5px; line-height:1.4; text-align:left; cursor:pointer;
  border-radius:7px; position:relative;
  transition:background .12s ease, color .12s ease; }
.archive-viewer__row:hover { background:var(--surface-3); color:var(--ink); }
.archive-viewer__row:focus-visible { outline:none; box-shadow:0 0 0 2px
  color-mix(in srgb, var(--ink-3) 32%, transparent) inset; }
.archive-viewer__row--dir { color:var(--ink); font-weight:500; }
/* Chevron rotates smoothly between collapsed / expanded. */
.archive-viewer__chev { flex-shrink:0; color:var(--ink-3);
  transition:transform .16s ease; }
.archive-viewer__chev[data-open="true"] { transform:rotate(90deg); }
.archive-viewer__ico { flex-shrink:0; color:var(--ink-3); display:block; }
.archive-viewer__row--dir .archive-viewer__ico { color:var(--ink-2); }
/* Selected file: tinted pill + a flush accent bar on the leading edge. */
.archive-viewer__row--file.is-selected { background:var(--surface-4); color:var(--ink); }
.archive-viewer__row--file.is-selected::before { content:""; position:absolute;
  left:0; top:3px; bottom:3px; width:2px; border-radius:2px; background:var(--ink); }
.archive-viewer__row--file.is-selected .archive-viewer__ico { color:var(--ink); }
.archive-viewer__name { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.archive-viewer__size { color:var(--ink-3); font-size:11px; flex-shrink:0;
  font-variant-numeric:tabular-nums; }
/* Tighten the row gutter so deep nesting still leaves room for names. */
.archive-viewer__node { padding:0 6px; }
.archive-viewer__children { position:relative; }
/* Faint guide line down each open folder's children. */
.archive-viewer__children::before { content:""; position:absolute;
  top:0; bottom:4px; width:1px; background:var(--line-2);
  left:calc(var(--arcv-depth, 0) * 14px + 15px); }

/* ---------- right pane: preview ---------- */
.archive-viewer__preview { display:flex; flex-direction:column; flex:1; min-height:0; min-width:0; }
.archive-viewer__preview-head { display:flex; align-items:center; gap:8px;
  height:42px; padding:0 8px 0 12px; flex-shrink:0;
  border-bottom:1px solid var(--line); background:var(--surface-2); color:var(--ink-3); }
.archive-viewer__preview-head > .archive-viewer__ico,
.archive-viewer__preview-head > svg { flex-shrink:0; }
.archive-viewer__preview-name { color:var(--ink); font-size:12.5px; font-weight:500;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
.archive-viewer__preview-size { margin-left:auto; font-size:11px; color:var(--ink-3);
  flex-shrink:0; font-variant-numeric:tabular-nums; }
.archive-viewer__preview-head .btn-icon { margin-left:2px; flex-shrink:0; }

/* Breadcrumb of the selected inner path, under the preview header. */
.archive-viewer__crumbs { display:flex; align-items:center; flex-wrap:wrap;
  gap:1px; padding:6px 12px; flex-shrink:0;
  border-bottom:1px solid var(--line); background:var(--surface);
  font-size:11px; color:var(--ink-3); }
.archive-viewer__crumb { display:inline-flex; align-items:center;
  max-width:24ch; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.archive-viewer__crumb--last { color:var(--ink-2); font-weight:500; }
.archive-viewer__crumb-sep { color:var(--ink-4); margin:0 4px; flex-shrink:0;
  display:inline-flex; }

.archive-viewer__preview-body { flex:1; min-height:0; overflow:auto; padding:14px;
  display:flex; background:var(--surface); }
/* When hosting the CodeView (or any height:100% inline viewer) the body
   must NOT add its own padding/scroll — the viewer fills the pane and
   manages its own scroll, line gutter, and toolbar. */
.archive-viewer__preview-body--code { padding:0; overflow:hidden; display:block; }
.archive-viewer__preview-body--code > .code-preview { height:100%; }
/* A faint checkerboard behind transparent images so PNGs with alpha read
   clearly; the image itself sits on a small floating card. */
.archive-viewer__preview-body--img {
  background:
    var(--surface),
    repeating-conic-gradient(var(--surface-3) 0% 25%, var(--surface-2) 0% 50%) 0 / 18px 18px; }
.archive-viewer__img { max-width:100%; max-height:100%; margin:auto; object-fit:contain;
  border-radius:var(--radius-1); box-shadow:var(--shadow-2); }
.archive-viewer__pdf { width:100%; height:100%; border:0; border-radius:var(--radius-1);
  background:#fff; box-shadow:var(--shadow-1); }

/* Centered states (binary / empty / error / loading). */
.archive-viewer__binary, .archive-viewer__empty { margin:auto;
  display:flex; flex-direction:column; align-items:center;
  gap:12px; color:var(--ink-3); text-align:center; padding:24px; }
.archive-viewer__state-icon { display:grid; place-items:center;
  width:54px; height:54px; border-radius:50%; color:var(--ink-3);
  background:var(--surface-3); border:1px solid var(--line); }
.archive-viewer__hint { color:var(--ink-3); font-size:12.5px; }
.archive-viewer__error { margin:auto; display:flex; flex-direction:column;
  align-items:center; gap:12px; padding:24px; text-align:center; }
.archive-viewer__error-icon { display:grid; place-items:center;
  width:54px; height:54px; border-radius:50%; color:var(--danger);
  background:color-mix(in srgb, var(--danger) 12%, var(--surface));
  border:1px solid color-mix(in srgb, var(--danger) 30%, var(--line)); }
.archive-viewer__error-text { color:var(--ink-2); font-size:12.5px; line-height:1.5;
  max-width:40ch; }

/* ---------- loading skeleton rows ---------- */
.archive-viewer__skel { padding:8px 12px; display:flex; flex-direction:column; gap:9px; }
.archive-viewer__skel-row { display:flex; align-items:center; gap:8px; }
.archive-viewer__skel-row > span { height:11px; border-radius:4px;
  background:linear-gradient(90deg, var(--surface-3) 0%, var(--surface-4) 50%, var(--surface-3) 100%);
  background-size:200% 100%; animation:arcv-shimmer 1.4s ease-in-out infinite; }
.archive-viewer__skel-dot { width:13px; height:13px; border-radius:4px; flex-shrink:0; }
.archive-viewer__skel-bar { flex:1; }
@keyframes arcv-shimmer { 0% { background-position:100% 0; } 100% { background-position:-100% 0; } }
html[data-theme="dark"] .archive-viewer__skel-row > span {
  background:linear-gradient(90deg, rgba(255,255,255,.05) 0%, rgba(255,255,255,.10) 50%, rgba(255,255,255,.05) 100%);
  background-size:200% 100%; }
@media (prefers-reduced-motion: reduce) {
  .archive-viewer__skel-row > span { animation:none; }
  .archive-viewer__chev { transition:none; }
  .archive-viewer__row { transition:none; }
}
`;

let _styleInjected = false;
function injectStyleOnce() {
  if (_styleInjected || typeof document === "undefined") return;
  _styleInjected = true;
  const el = document.createElement("style");
  el.setAttribute("data-archive-viewer", "");
  el.textContent = ARCHIVE_VIEWER_CSS;
  document.head.appendChild(el);
}

function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

// Authed fetch to a neuthek API path. Same posture as
// @/api/files#fetchMediaBlob: ship the session cookie via
// `credentials: "include"`, plus a transitional localStorage Bearer
// for users still on the pre-cookie build. The /archives endpoints
// take the JWT the same way every other authed GET does.
async function archiveFetch(path) {
  let legacyAuth;
  try {
    const legacy = localStorage.getItem("neuthek.jwt");
    if (legacy) legacyAuth = { Authorization: `Bearer ${legacy}` };
  } catch {
    /* private browsing */
  }
  const res = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: legacyAuth || {},
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      detail = j?.detail || detail;
    } catch {
      /* non-JSON body */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res;
}

// Lowercase extension of a leaf name (no leading dot). "" when none.
function extOf(name) {
  const base = (name || "").split(/[\\/]/).pop();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : "";
}

// Reuse the gallery's extension→viewer taxonomy (file-types.js) so a
// file INSIDE an archive routes to the same kind of preview it would
// get as a first-class upload: image / pdf / code / markdown / datatree
// / csv / notebook all become rich previews; everything else falls back
// to a download. Keeps this viewer in lockstep with the main gallery.
function leafKind(name) {
  return fileTypeInfo(extOf(name)).kind;
}

// The text-ish kinds we can render inline here. Markdown + structured
// data (json/yaml/xml) get the syntax-highlighted CodeView rather than
// their dedicated MarkdownViewer / DataTreeViewer — those two only
// accept a fileId (they fetch the original media URL themselves) and
// can't take pre-extracted bytes, so we give archive inner files the
// highlighted-source treatment instead, which still beats a raw <pre>.
const TEXT_KINDS = new Set(["code", "markdown", "datatree", "csv", "notebook"]);

// Render the tree/preview leaf icon. Code-shaped files get the matching
// monochrome per-language glyph from language-icons.jsx (so a `.py`
// shows the Python mark, `.rs` the Rust gear, etc., matching the
// gallery); non-code leaves keep a plain Icon glyph. Returns a ready
// element so callers render it directly.
function LeafIcon({ name, size = 14, className = "archive-viewer__ico" }) {
  const ext = extOf(name);
  const kind = fileTypeInfo(ext).kind;
  if (kind === "image") return <Icon name="image" size={size} className={className} />;
  if (kind === "pdf" || kind === "doc") return <Icon name="document" size={size} className={className} />;
  if (kind === "csv" || kind === "spreadsheet") return <Icon name="spreadsheet" size={size} className={className} />;
  if (kind === "video") return <Icon name="video" size={size} className={className} />;
  if (kind === "audio") return <Icon name="music" size={size} className={className} />;
  if (kind === "archive") return <Icon name="archive" size={size} className={className} />;
  // code / markdown / datatree / notebook / config → per-language glyph.
  if (TEXT_KINDS.has(kind)) return <LanguageIcon name={languageIcon(ext)} size={size} className={className} />;
  return <Icon name="file" size={size} className={className} />;
}

// 3 MB ceiling on inline text rendering inside the archive viewer (a hair
// under code-preview's 5 MB original cap — archive extraction is pricier,
// so we stay conservative). Larger text files render the first slice with
// a truncation banner via CodeView.
const INNER_TEXT_CAP = 3 * 1024 * 1024;

// One row of the tree. Directories toggle open/closed; files invoke
// onPick. Indentation scales with depth. Pure-presentational — the
// open-state map + selection live in the parent so Esc / re-render
// behave predictably.
function TreeNode({ node, depth, openSet, onToggle, onPick, selectedPath }) {
  const pad = 6 + depth * 14;
  if (node.is_dir) {
    const isOpen = openSet.has(node.path);
    const hasKids = Array.isArray(node.children) && node.children.length > 0;
    return (
      <div className="archive-viewer__node">
        <button
          type="button"
          className="archive-viewer__row archive-viewer__row--dir"
          style={{ paddingLeft: pad }}
          onClick={() => onToggle(node.path)}
          aria-expanded={isOpen}
        >
          <Icon
            name="chevronRight"
            size={12}
            className="archive-viewer__chev"
            data-open={isOpen ? "true" : "false"}
          />
          <Icon name="folder" size={14} className="archive-viewer__ico" />
          <span className="archive-viewer__name">{node.name}</span>
          <span className="archive-viewer__size mono">{fmtBytes(node.size)}</span>
        </button>
        {isOpen && hasKids && (
          <div className="archive-viewer__children" style={{ "--arcv-depth": depth }}>
            {node.children.map((c) => (
              <TreeNode
                key={c.path}
                node={c}
                depth={depth + 1}
                openSet={openSet}
                onToggle={onToggle}
                onPick={onPick}
                selectedPath={selectedPath}
              />
            ))}
          </div>
        )}
      </div>
    );
  }
  const isSel = selectedPath === node.path;
  return (
    <button
      type="button"
      className={"archive-viewer__row archive-viewer__row--file" + (isSel ? " is-selected" : "")}
      style={{ paddingLeft: pad + 18 }}
      onClick={() => onPick(node)}
      title={node.path}
    >
      <LeafIcon name={node.name} size={14} />
      <span className="archive-viewer__name">{node.name}</span>
      <span className="archive-viewer__size mono">{fmtBytes(node.size)}</span>
    </button>
  );
}

// Right-pane preview of the currently-picked inner file. Fetches the
// bytes once per pick, then routes by the leaf's EXTENSION (more reliable
// than the extract endpoint's Content-Type, which is usually
// octet-stream) to the SAME per-type viewers the main gallery uses:
//   image      → <img src=blobURL>
//   pdf        → <iframe src=blobURL>
//   code/text/markdown/json/yaml/xml/csv/notebook → <CodeView> (the rich
//                syntax-highlighted viewer: gutter, find, copy, wrap)
//   anything else (or a file that decodes as binary) → download button
// Text is decoded from an arrayBuffer and capped at INNER_TEXT_CAP so a
// huge inner file can't freeze the browser (CodeView shows a banner).
// Blob URLs are revoked on unmount / re-pick to avoid leaks.
function InnerPreview({ fileId, node }) {
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    let madeUrl = null;
    setState({ status: "loading" });
    (async () => {
      try {
        const res = await archiveFetch(
          `/archives/${fileId}/file?path=${encodeURIComponent(node.path)}`,
        );
        const ctype = (res.headers.get("Content-Type") || "").toLowerCase();
        const kind = leafKind(node.name);
        // Treat as text if the extension says so, OR the server clearly
        // sniffed a text-ish type (covers unmapped extensions that are
        // still readable, e.g. a LICENSE or .conf the catalog doesn't list).
        const looksTextByCtype =
          ctype.startsWith("text/") ||
          ctype.includes("json") ||
          ctype.includes("xml") ||
          ctype.includes("javascript") ||
          ctype.includes("yaml");

        if (kind === "image" || ctype.startsWith("image/")) {
          const blob = await res.blob();
          if (cancelled) return;
          madeUrl = URL.createObjectURL(blob);
          setState({ status: "image", url: madeUrl });
        } else if (kind === "pdf" || ctype.startsWith("application/pdf")) {
          const blob = await res.blob();
          if (cancelled) return;
          madeUrl = URL.createObjectURL(blob);
          setState({ status: "pdf", url: madeUrl });
        } else if (TEXT_KINDS.has(kind) || looksTextByCtype) {
          // Decode to text, capping the rendered bytes like code-preview
          // does for its originals so a 50 MB log doesn't lock the tab.
          const buf = await res.arrayBuffer();
          if (cancelled) return;
          let bytes = new Uint8Array(buf);
          let truncated = false;
          if (bytes.byteLength > INNER_TEXT_CAP) {
            bytes = bytes.slice(0, INNER_TEXT_CAP);
            truncated = true;
          }
          const text = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
          if (cancelled) return;
          setState({ status: "text", text, truncated });
        } else {
          // Non-previewable — offer a download of the blob we already
          // hold so the user doesn't pay a second fetch.
          const blob = await res.blob();
          if (cancelled) return;
          madeUrl = URL.createObjectURL(blob);
          setState({ status: "binary", url: madeUrl });
        }
      } catch (e) {
        if (cancelled) return;
        setState({ status: "error", message: e?.message || "Could not load file" });
        toast.error(e?.message || "Couldn't open file");
      }
    })();
    return () => {
      cancelled = true;
      if (madeUrl) URL.revokeObjectURL(madeUrl);
    };
  }, [fileId, node.path]);

  const download = (url) => {
    const a = document.createElement("a");
    a.href = url;
    a.download = node.name || "download";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  // The CodeView path manages its own scroll + padding (it's height:100%),
  // so drop the body's padding/scroll/flex when it's hosting one. Images
  // get the alpha-checkerboard backdrop.
  const bodyClass =
    "archive-viewer__preview-body" +
    (state.status === "text" ? " archive-viewer__preview-body--code" : "") +
    (state.status === "image" ? " archive-viewer__preview-body--img" : "");

  // Split the inner path into breadcrumb segments (the last is the file
  // itself). Handles both / and \\ separators the backend might emit.
  const segments = (node.path || node.name).split(/[\\/]/).filter(Boolean);

  return (
    <div className="archive-viewer__preview">
      <div className="archive-viewer__preview-head">
        <LeafIcon name={node.name} size={13} />
        <span className="archive-viewer__preview-name">{node.name}</span>
        <span className="archive-viewer__preview-size mono">{fmtBytes(node.size)}</span>
        {(state.status === "image" || state.status === "pdf" || state.status === "binary") && state.url && (
          <button
            type="button"
            className="btn-icon"
            onClick={() => download(state.url)}
            aria-label="Download"
            title="Download"
          >
            <Icon name="download" size={13} />
          </button>
        )}
      </div>
      {segments.length > 1 && (
        <nav className="archive-viewer__crumbs" aria-label="Path inside archive">
          {segments.map((seg, i) => {
            const last = i === segments.length - 1;
            return (
              <React.Fragment key={i}>
                {i > 0 && (
                  <span className="archive-viewer__crumb-sep" aria-hidden="true">
                    <Icon name="chevronRight" size={10} />
                  </span>
                )}
                <span
                  className={"archive-viewer__crumb" + (last ? " archive-viewer__crumb--last" : "")}
                  title={seg}
                >
                  {seg}
                </span>
              </React.Fragment>
            );
          })}
        </nav>
      )}
      <div className={bodyClass}>
        {state.status === "loading" && (
          <div className="archive-viewer__binary">
            <div className="archive-viewer__state-icon">
              <LeafIcon name={node.name} size={24} className="" />
            </div>
            <div className="archive-viewer__hint">Extracting…</div>
          </div>
        )}
        {state.status === "error" && (
          <div className="archive-viewer__error">
            <div className="archive-viewer__error-icon">
              <Icon name="alert" size={22} strokeWidth={1.5} />
            </div>
            <div className="archive-viewer__error-text">{state.message}</div>
          </div>
        )}
        {state.status === "image" && (
          <img className="archive-viewer__img" src={state.url} alt={node.name} />
        )}
        {state.status === "pdf" && (
          <iframe className="archive-viewer__pdf" src={state.url} title={node.name} />
        )}
        {state.status === "text" && (
          <CodeView
            text={state.text}
            filename={node.name}
            mime={mimeFromFilename(node.name)}
            byteSize={node.size}
            truncated={state.truncated}
          />
        )}
        {state.status === "binary" && (
          <div className="archive-viewer__binary">
            <div className="archive-viewer__state-icon">
              <LeafIcon name={node.name} size={24} className="" />
            </div>
            <div className="archive-viewer__hint">
              This file can&rsquo;t be previewed here.
            </div>
            <button type="button" className="btn btn--secondary btn--sm" onClick={() => download(state.url)}>
              <Icon name="download" size={13} /> Download {node.name}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// Walk the tree once and collect the paths of directories to open by
// default. We open the top level only (keeps deep archives from
// dumping every folder open at once).
function topLevelDirs(tree) {
  const s = new Set();
  for (const n of tree || []) if (n.is_dir) s.add(n.path);
  return s;
}

// Loading skeleton for the tree pane: a stack of shimmering rows at
// staggered indents so the empty archive list reads as "reading…" and
// the layout doesn't jump when real rows arrive.
function TreeSkeleton() {
  const rows = [0, 1, 1, 0, 1, 2, 1, 0, 1, 1];
  return (
    <div className="archive-viewer__skel" aria-hidden="true">
      {rows.map((depth, i) => (
        <div className="archive-viewer__skel-row" key={i} style={{ paddingLeft: depth * 14 }}>
          <span className="archive-viewer__skel-dot" />
          <span
            className="archive-viewer__skel-bar"
            style={{ maxWidth: `${58 + ((i * 37) % 38)}%` }}
          />
        </div>
      ))}
    </div>
  );
}

export function ArchiveViewer({ fileId, fileName, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [openSet, setOpenSet] = useState(() => new Set());
  const [picked, setPicked] = useState(null);
  // Resizable split: width of the tree pane in px, persisted to the CSS
  // var on the split element. `dragging` drives the gutter's active style.
  const splitRef = useRef(null);
  const [treeW, setTreeW] = useState(264);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    injectStyleOnce();
    let cancelled = false;
    setData(null);
    setErr(null);
    setPicked(null);
    (async () => {
      try {
        const res = await archiveFetch(`/archives/${fileId}/list`);
        const json = await res.json();
        if (cancelled) return;
        setData(json);
        setOpenSet(topLevelDirs(json.tree));
      } catch (e) {
        if (cancelled) return;
        setErr(e?.message || "Could not read archive");
        toast.error(e?.message || "Couldn't read archive");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fileId]);

  const onToggle = useCallback((path) => {
    setOpenSet((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // Pointer-driven gutter drag. Measures against the split container so
  // the width tracks the cursor exactly; clamps to a sane band (the CSS
  // clamp() is the final guard). Uses pointer capture so the drag keeps
  // following even if the cursor outruns the 1px gutter.
  const onGutterDown = useCallback((e) => {
    e.preventDefault();
    const split = splitRef.current;
    if (!split) return;
    setDragging(true);
    const rect = split.getBoundingClientRect();
    try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch { /* unsupported */ }
    const move = (ev) => {
      const x = (ev.clientX ?? rect.left) - rect.left;
      const w = Math.max(180, Math.min(x, rect.width - 260));
      setTreeW(w);
    };
    const up = () => {
      setDragging(false);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, []);

  const fileCount = useMemo(() => {
    if (!data) return 0;
    let n = 0;
    const walk = (nodes) => {
      for (const node of nodes || []) {
        if (node.is_dir) walk(node.children);
        else n += 1;
      }
    };
    walk(data.tree);
    return n;
  }, [data]);

  return (
    <div className="archive-viewer" onClick={(e) => e.stopPropagation()}>
      <div className="archive-viewer__head">
        <Icon name="archive" size={14} />
        <span className="archive-viewer__title">{data?.filename || fileName}</span>
        <span className="archive-viewer__meta mono">
          {data
            ? `${fileCount.toLocaleString()} file${fileCount === 1 ? "" : "s"} · ${fmtBytes(
                data.total_uncompressed,
              )}${data.truncated ? ` · ${data.entry_count.toLocaleString()} entries, first ${data.tree.length ? "2000" : "0"} shown` : ""}`
            : ""}
        </span>
        {onClose && (
          <button type="button" className="btn-icon" onClick={onClose} aria-label="Close" title="Close">
            <Icon name="x" size={14} />
          </button>
        )}
      </div>
      <div
        className="archive-viewer__split"
        ref={splitRef}
        style={{ "--arcv-tree-w": `${treeW}px` }}
      >
        <div className="archive-viewer__tree">
          {err ? (
            <div className="archive-viewer__error">
              <div className="archive-viewer__error-icon">
                <Icon name="alert" size={22} strokeWidth={1.5} />
              </div>
              <div className="archive-viewer__error-text">{err}</div>
            </div>
          ) : !data ? (
            <TreeSkeleton />
          ) : !data.tree || data.tree.length === 0 ? (
            <div className="archive-viewer__empty">
              <div className="archive-viewer__state-icon">
                <Icon name="archive" size={24} strokeWidth={1.3} />
              </div>
              <div className="archive-viewer__hint">Archive is empty.</div>
            </div>
          ) : (
            data.tree.map((n) => (
              <TreeNode
                key={n.path}
                node={n}
                depth={0}
                openSet={openSet}
                onToggle={onToggle}
                onPick={setPicked}
                selectedPath={picked?.path}
              />
            ))
          )}
        </div>
        {/* Drag handle — the middle 1px grid column. */}
        <div
          className="archive-viewer__gutter"
          data-dragging={dragging ? "true" : "false"}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize file tree"
          onPointerDown={onGutterDown}
        />
        <div className="archive-viewer__pane">
          {picked ? (
            <InnerPreview fileId={fileId} node={picked} />
          ) : (
            <div className="archive-viewer__empty">
              <div className="archive-viewer__state-icon">
                <Icon name="archive" size={26} strokeWidth={1.2} />
              </div>
              <div className="archive-viewer__hint">Select a file to preview it.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
