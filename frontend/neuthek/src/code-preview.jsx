// Code-file preview pane — syntax-highlighted source code with line numbers.
//
// Sits next to the PDF preview in the gallery's preview panel: when the
// selected file has a `text/*` or recognized code MIME, we fetch the raw
// bytes via /images/{id}/original (signed URL or raw blob endpoint),
// pick a Prism grammar from the MIME's `text/x-<lang>` tail, and render
// the highlighted HTML inside a scrollable `<pre>`.
//
// Grammars are lazily imported so the initial bundle doesn't pay for
// every language at once. Each entry maps a Prism component to its
// import path; an unknown MIME falls back to plain monospace text,
// which is the right behavior for log files and Procfile-shaped scripts.
import React, { useEffect, useState as useStateC, useMemo } from "react";
import Prism from "prismjs/components/prism-core";
// Markup / clike are the bases the others depend on — eagerly loaded
// so the lazy import below doesn't have to chain another await.
import "prismjs/components/prism-markup";
import "prismjs/components/prism-clike";
import "prismjs/components/prism-javascript";
import "prismjs/components/prism-css";
import { API_BASE_URL, tokens } from "@/api/client";

// MIME → Prism grammar name. The string on the right matches Prism's
// component id (the file under prismjs/components/prism-<id>.js).
const MIME_TO_LANG = {
  "text/javascript": "javascript",
  "text/x-typescript": "typescript",
  "text/x-tsx": "tsx",
  "text/x-jsx": "jsx",
  "text/x-vue": "markup",  // Vue SFC = markup + js, markup is closest baseline
  "text/x-svelte": "markup",
  "text/x-python": "python",
  "text/x-ruby": "ruby",
  "text/x-php": "php",
  "text/x-java": "java",
  "text/x-kotlin": "kotlin",
  "text/x-scala": "scala",
  "text/x-swift": "swift",
  "text/x-go": "go",
  "text/x-rust": "rust",
  "text/x-c": "c",
  "text/x-c++": "cpp",
  "text/x-csharp": "csharp",
  "text/x-dart": "dart",
  "text/x-lua": "lua",
  "text/x-r": "r",
  "text/x-perl": "perl",
  "text/x-shellscript": "bash",
  "text/x-powershell": "powershell",
  "text/x-sql": "sql",
  "text/x-clojure": "clojure",
  "text/x-elixir": "elixir",
  "text/x-elm": "elm",
  "text/x-erlang": "erlang",
  "text/x-haskell": "haskell",
  "text/x-ocaml": "ocaml",
  "text/x-zig": "zig",
  "text/html": "markup",
  "text/css": "css",
  "text/x-scss": "scss",
  "text/x-sass": "sass",
  "text/x-less": "less",
  "image/svg+xml": "markup",
  "text/xml": "markup",
  "text/markdown": "markdown",
  "text/x-rst": "rest",
  "text/x-tex": "latex",
  "text/x-dotenv": "bash",  // close enough — comments + KEY=value
  "text/x-dockerfile": "docker",
  "text/x-makefile": "makefile",
  "text/x-properties": "ini",
  "text/x-graphql": "graphql",
  "text/x-protobuf": "protobuf",
  "text/x-diff": "diff",
  "application/json": "json",
  "application/x-ipynb+json": "json",
  "text/x-yaml": "yaml",
};

// Map Prism component id → dynamic import. Keys are the grammar names
// from MIME_TO_LANG values. The eager-loaded ones (javascript, css,
// markup, clike) are listed as no-op resolves so the loader code below
// stays uniform.
const LANG_LOADERS = {
  javascript: () => Promise.resolve(),
  css: () => Promise.resolve(),
  markup: () => Promise.resolve(),
  typescript: () => import("prismjs/components/prism-typescript"),
  tsx: () => import("prismjs/components/prism-typescript").then(() => import("prismjs/components/prism-jsx")).then(() => import("prismjs/components/prism-tsx")),
  jsx: () => import("prismjs/components/prism-jsx"),
  python: () => import("prismjs/components/prism-python"),
  ruby: () => import("prismjs/components/prism-ruby"),
  php: () => import("prismjs/components/prism-php"),
  java: () => import("prismjs/components/prism-java"),
  kotlin: () => import("prismjs/components/prism-kotlin"),
  scala: () => import("prismjs/components/prism-scala"),
  swift: () => import("prismjs/components/prism-swift"),
  go: () => import("prismjs/components/prism-go"),
  rust: () => import("prismjs/components/prism-rust"),
  c: () => import("prismjs/components/prism-c"),
  cpp: () => import("prismjs/components/prism-cpp"),
  csharp: () => import("prismjs/components/prism-csharp"),
  dart: () => import("prismjs/components/prism-dart"),
  lua: () => import("prismjs/components/prism-lua"),
  r: () => import("prismjs/components/prism-r"),
  perl: () => import("prismjs/components/prism-perl"),
  bash: () => import("prismjs/components/prism-bash"),
  powershell: () => import("prismjs/components/prism-powershell"),
  sql: () => import("prismjs/components/prism-sql"),
  clojure: () => import("prismjs/components/prism-clojure"),
  elixir: () => import("prismjs/components/prism-elixir"),
  elm: () => import("prismjs/components/prism-elm"),
  erlang: () => import("prismjs/components/prism-erlang"),
  haskell: () => import("prismjs/components/prism-haskell"),
  ocaml: () => import("prismjs/components/prism-ocaml"),
  zig: () => import("prismjs/components/prism-zig"),
  scss: () => import("prismjs/components/prism-scss"),
  sass: () => import("prismjs/components/prism-sass"),
  less: () => import("prismjs/components/prism-less"),
  markdown: () => import("prismjs/components/prism-markdown"),
  rest: () => import("prismjs/components/prism-rest"),
  latex: () => import("prismjs/components/prism-latex"),
  docker: () => import("prismjs/components/prism-docker"),
  makefile: () => import("prismjs/components/prism-makefile"),
  ini: () => import("prismjs/components/prism-ini"),
  graphql: () => import("prismjs/components/prism-graphql"),
  protobuf: () => import("prismjs/components/prism-protobuf"),
  diff: () => import("prismjs/components/prism-diff"),
  json: () => import("prismjs/components/prism-json"),
  yaml: () => import("prismjs/components/prism-yaml"),
};

// MIMEs the FE accepts as "code-shaped" for the preview. If the file's
// mime starts with `text/`, JSON, or is the ipynb mime, we render it
// here. SVG is in code MIMEs too but the main preview pane shows it
// as an image — caller filters this list down.
export function isCodeMime(mime) {
  if (!mime) return false;
  if (mime in MIME_TO_LANG) return true;
  return mime.startsWith("text/");
}

function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// 5 MB ceiling so a 100MB JSON dump doesn't lock the browser. Larger
// files render the first 5 MB with a truncation banner.
const RENDER_BYTE_CAP = 5 * 1024 * 1024;

export function CodePreview({ fileId, mime, byteSize, filename }) {
  const [text, setText] = useStateC(null);
  const [error, setError] = useStateC(null);
  const [grammarReady, setGrammarReady] = useStateC(false);
  const [truncated, setTruncated] = useStateC(false);

  const lang = useMemo(() => {
    if (mime && MIME_TO_LANG[mime]) return MIME_TO_LANG[mime];
    if (mime === "text/plain" || !mime) return null;
    if (mime?.startsWith("text/")) return null;  // unknown text — plain render
    return null;
  }, [mime]);

  useEffect(() => {
    if (!lang) { setGrammarReady(true); return; }
    let alive = true;
    const loader = LANG_LOADERS[lang];
    if (!loader) { setGrammarReady(true); return; }
    loader().then(() => { if (alive) setGrammarReady(true); }).catch(() => { if (alive) setGrammarReady(true); });
    return () => { alive = false; };
  }, [lang]);

  useEffect(() => {
    if (!fileId) return;
    let alive = true;
    setText(null);
    setError(null);
    setTruncated(false);
    // Cookie auth: ship the session cookie via credentials. Legacy
    // Bearer kept for users mid-migration.
    let legacy = null;
    try { legacy = localStorage.getItem("neuthek.jwt") || localStorage.getItem("istore.jwt"); } catch {}
    fetch(`${API_BASE_URL}/images/${fileId}/original`, {
      credentials: "include",
      headers: legacy ? { Authorization: `Bearer ${legacy}` } : {},
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const buf = await r.arrayBuffer();
        // Cap the rendered size so a huge file doesn't freeze the
        // browser. Larger files show a truncation banner and the
        // first 5 MB; user can still download the original from the
        // preview's "Download" button.
        let bytes = new Uint8Array(buf);
        let wasTrunc = false;
        if (bytes.byteLength > RENDER_BYTE_CAP) {
          bytes = bytes.slice(0, RENDER_BYTE_CAP);
          wasTrunc = true;
        }
        const decoded = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
        if (alive) {
          setText(decoded);
          setTruncated(wasTrunc);
        }
      })
      .catch((e) => { if (alive) setError(e.message || "Failed to load"); });
    return () => { alive = false; };
  }, [fileId]);

  const html = useMemo(() => {
    if (text == null) return null;
    if (!lang || !grammarReady || !Prism.languages[lang]) {
      // Plain monospace — still readable, just uncolored.
      return null;
    }
    try {
      return Prism.highlight(text, Prism.languages[lang], lang);
    } catch {
      return null;
    }
  }, [text, lang, grammarReady]);

  const lineCount = useMemo(() => {
    if (text == null) return 0;
    // Don't count a final empty line from a trailing newline — matches
    // editor convention.
    const trailing = text.endsWith("\n") ? 1 : 0;
    return text.split("\n").length - trailing;
  }, [text]);

  if (error) {
    return (
      <div style={{ padding: 14, fontSize: 12, color: "var(--ink-3)" }}>
        Could not load file: {error}
      </div>
    );
  }
  if (text == null) {
    return (
      <div style={{ padding: 14, fontSize: 12, color: "var(--ink-3)" }}>
        Loading…
      </div>
    );
  }

  return (
    <div className="code-preview" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          padding: "6px 12px",
          fontSize: 11,
          color: "var(--ink-3)",
          borderBottom: "1px solid var(--line)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          background: "var(--surface-2)",
        }}
      >
        <span className="mono">{filename || "code"}</span>
        <span style={{ display: "inline-flex", gap: 10 }}>
          {lang && <span style={{ textTransform: "uppercase", fontWeight: 600 }}>{lang}</span>}
          <span>{lineCount} lines</span>
          {byteSize != null && <span>{fmtBytes(byteSize)}</span>}
        </span>
      </div>
      {truncated && (
        <div
          style={{
            padding: "6px 12px",
            fontSize: 11,
            color: "var(--warning)",
            background: "color-mix(in oklab, var(--warning) 12%, transparent)",
            borderBottom: "1px solid var(--line)",
          }}
        >
          Showing the first {fmtBytes(RENDER_BYTE_CAP)} of a larger file. Download the original to see the rest.
        </div>
      )}
      <div
        className="code-preview__body"
        style={{
          flex: 1,
          overflow: "auto",
          fontFamily: "var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace)",
          fontSize: 12.5,
          lineHeight: 1.55,
          background: "var(--surface)",
          color: "var(--ink)",
        }}
      >
        <table
          style={{
            borderCollapse: "collapse",
            width: "100%",
            tabSize: 4,
          }}
        >
          <tbody>
            {text.split("\n").slice(0, text.endsWith("\n") ? -1 : undefined).map((line, i) => (
              <tr key={i}>
                <td
                  className="mono"
                  style={{
                    userSelect: "none",
                    textAlign: "right",
                    padding: "0 10px 0 14px",
                    color: "var(--ink-3)",
                    borderRight: "1px solid var(--line)",
                    whiteSpace: "nowrap",
                    verticalAlign: "top",
                    width: "1%",
                  }}
                >
                  {i + 1}
                </td>
                <td style={{ padding: "0 14px", whiteSpace: "pre" }}>
                  {html
                    ? <span dangerouslySetInnerHTML={{ __html: highlightLine(html, i) }}/>
                    : line || " "}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Pull the i-th line out of the fully-highlighted HTML. Prism returns
// one big HTML string; rendering it line-by-line gives us correct
// per-line layout (sticky line numbers + horizontal scroll only on
// the code column) without re-running the highlighter for each row.
function highlightLine(html, idx) {
  // Cache the split per html string so we don't re-split on every line.
  if (highlightLine._cache?.html !== html) {
    highlightLine._cache = { html, lines: html.split("\n") };
  }
  const line = highlightLine._cache.lines[idx];
  return line && line.length ? line : "&nbsp;";
}
