// Jupyter notebook (.ipynb) viewer — parses the notebook JSON and
// renders its cells in document order:
//
//   • markdown cells  → rendered GFM (react-markdown + remark-gfm +
//                        rehype-sanitize), same sanitized pipeline as
//                        markdown-viewer.jsx
//   • code cells      → syntax-highlighted source with an execution-count
//                        gutter (`In [n]:`), mirroring code-preview.jsx's
//                        Prism setup; the kernel language from the
//                        notebook metadata picks the grammar
//   • outputs         → stream text (stdout/stderr), `text/plain` results,
//                        `image/png` (base64 → data URI), and sanitized
//                        `text/html`. Errors render their traceback as a
//                        monospace block with ANSI escapes stripped.
//
// A header toggle flips the whole surface to the raw .ipynb JSON.
//
// Data load matches the other viewers: original bytes through
// `fetchMediaBlob(originalMediaUrl(fileId))`, decoded as text, then
// JSON.parsed. A parse failure falls back to the raw source with an
// inline note rather than an error wall.
//
// Self-contained on purpose (matches the other orphan viewers): the
// markdown pipeline and the Prism grammar loader are duplicated here so
// this file has no cross-viewer import coupling.
//
// Props: { fileId, fileName }.
import React, { useState, useEffect, useMemo, useCallback } from "react";
import toast from "react-hot-toast";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import Prism from "prismjs/components/prism-core";
import "prismjs/components/prism-markup";
import "prismjs/components/prism-clike";
import "prismjs/components/prism-javascript";
import { Icon } from "./icons.jsx";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { safeHref } from "./safe-href.js";
import { ViewerSkeleton, ViewerError, ViewerEmpty, CopyButton } from "./viewer-states.jsx";

// ---- markdown pipeline (mirrors markdown-viewer.jsx) -----------------

const MD_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code || []), ["className"]],
    span: [...(defaultSchema.attributes?.span || []), ["className"]],
    pre: [...(defaultSchema.attributes?.pre || []), ["className"]],
    th: [...(defaultSchema.attributes?.th || []), ["align"]],
    td: [...(defaultSchema.attributes?.td || []), ["align"]],
  },
};

const MD_COMPONENTS = {
  // Strip the HAST `node` prop react-markdown injects before spreading
  // onto the DOM element (avoids the unknown-attribute warning).
  a({ node, href, children, ...props }) {
    const safe = safeHref(href || "");
    if (!safe) return <span {...props}>{children}</span>;
    return <a href={safe} target="_blank" rel="noreferrer noopener" {...props}>{children}</a>;
  },
  img({ node, src, alt, ...props }) {
    const ok = typeof src === "string" && /^(https?:|data:image\/)/i.test(src);
    return ok
      ? <img src={src} alt={alt || ""} loading="lazy" style={{ maxWidth: "100%", height: "auto" }} {...props} />
      : <span style={{ color: "var(--ink-3)", fontStyle: "italic" }}>{alt || "image"}</span>;
  },
};

function Markdown({ source }) {
  return (
    <div className="markdown-body nbv-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeSanitize, MD_SCHEMA]]}
        components={MD_COMPONENTS}
        skipHtml
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}

// ---- code highlighting (mirrors code-preview.jsx) --------------------

// Notebook kernel language → Prism grammar id. Notebooks are
// overwhelmingly Python, but R / Julia / Scala kernels exist.
const LANG_TO_GRAMMAR = {
  python: "python",
  python3: "python",
  ipython: "python",
  r: "r",
  julia: "julia",
  scala: "scala",
  javascript: "javascript",
  typescript: "typescript",
  ruby: "ruby",
  bash: "bash",
  sql: "sql",
};

const GRAMMAR_LOADERS = {
  javascript: () => Promise.resolve(),
  python: () => import("prismjs/components/prism-python"),
  r: () => import("prismjs/components/prism-r"),
  julia: () => import("prismjs/components/prism-julia"),
  scala: () => import("prismjs/components/prism-clike").then(() => import("prismjs/components/prism-scala")),
  typescript: () => import("prismjs/components/prism-typescript"),
  ruby: () => import("prismjs/components/prism-ruby"),
  bash: () => import("prismjs/components/prism-bash"),
  sql: () => import("prismjs/components/prism-sql"),
};

// ---- ipynb helpers ---------------------------------------------------

// Notebook source / stream text can be either a single string or an
// array of line-strings (nbformat allows both). Normalize to one string.
function joinSource(src) {
  if (src == null) return "";
  if (Array.isArray(src)) return src.join("");
  return String(src);
}

// Strip ANSI SGR escape sequences (colored tracebacks, colored stderr)
// so they don't render as garbage. The \x1b escape keeps a raw control
// byte out of the source; the pattern matches CSI SGR runs:
// ESC "[" params "m".
function stripAnsi(s) {
  // eslint-disable-next-line no-control-regex
  return s.replace(/\x1b\[[0-9;]*m/g, "");
}

function notebookLanguage(nb) {
  const md = nb?.metadata || {};
  const fromKernel = md.kernelspec?.language;
  const fromLangInfo = md.language_info?.name;
  const name = (fromKernel || fromLangInfo || "python").toString().toLowerCase();
  return LANG_TO_GRAMMAR[name] || (name === "python" ? "python" : null);
}

// ---- output rendering ------------------------------------------------

function OutputBlock({ output }) {
  const type = output?.output_type;

  if (type === "stream") {
    const isErr = output.name === "stderr";
    return (
      <pre className={`mono nbv-out nbv-out--stream${isErr ? " nbv-out--err" : ""}`}>
        {stripAnsi(joinSource(output.text))}
      </pre>
    );
  }

  if (type === "error") {
    const tb = Array.isArray(output.traceback) ? output.traceback.join("\n") : joinSource(output.traceback);
    return (
      <pre className="mono nbv-out nbv-out--err">
        {stripAnsi(tb || `${output.ename}: ${output.evalue}`)}
      </pre>
    );
  }

  // execute_result + display_data share a `data` mimebundle. Prefer the
  // richest representation we can safely render.
  if (type === "execute_result" || type === "display_data") {
    const data = output.data || {};
    // image/png (and image/jpeg) come base64-encoded (possibly as a
    // line-array). Render as a data URI.
    const png = data["image/png"];
    const jpeg = data["image/jpeg"];
    if (png || jpeg) {
      const b64 = (joinSource(png || jpeg) || "").replace(/\s+/g, "");
      const mime = png ? "image/png" : "image/jpeg";
      return (
        <div className="nbv-out nbv-out--img">
          <img src={`data:${mime};base64,${b64}`} alt="cell output" />
        </div>
      );
    }
    const html = data["text/html"];
    if (html) {
      return (
        <div className="nbv-out nbv-out--html">
          {/* Route the result HTML through the SAME sanitizer the
              markdown cells use. react-markdown with a raw-HTML string
              and rehype-sanitize strips <script>, event handlers, and
              javascript: URLs — so a notebook that embeds a hostile
              `text/html` repr can't run script in our origin. */}
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[[rehypeSanitize, MD_SCHEMA]]}
            components={MD_COMPONENTS}
          >
            {joinSource(html)}
          </ReactMarkdown>
        </div>
      );
    }
    const plain = data["text/plain"];
    if (plain != null) {
      return (
        <pre className="mono nbv-out nbv-out--plain">
          {stripAnsi(joinSource(plain))}
        </pre>
      );
    }
    return null;
  }

  return null;
}

// ---- cells -----------------------------------------------------------

function CodeCell({ cell, grammar, grammarReady }) {
  const source = joinSource(cell.source);
  const html = useMemo(() => {
    if (!grammar || !grammarReady || !Prism.languages[grammar]) return null;
    try {
      return Prism.highlight(source, Prism.languages[grammar], grammar);
    } catch {
      return null;
    }
  }, [source, grammar, grammarReady]);

  const execCount = cell.execution_count;
  const outputs = Array.isArray(cell.outputs) ? cell.outputs : [];

  return (
    <div className="nbv-cell nbv-cell--code">
      <div className="nbv-cell__src-wrap">
        <div className="nbv-cell__gutter mono" aria-hidden="true">In&nbsp;[{execCount ?? " "}]:</div>
        <pre className="mono nbv-cell__src">
          {html ? <code dangerouslySetInnerHTML={{ __html: html }} /> : <code>{source || " "}</code>}
        </pre>
        {source.trim() && <CopyButton text={source} title="Copy cell" className="nbv-cell__copy" />}
      </div>
      {outputs.length > 0 && (
        <div className="nbv-cell__outputs">
          {outputs.map((o, i) => <OutputBlock key={i} output={o} />)}
        </div>
      )}
    </div>
  );
}

function MarkdownCell({ cell }) {
  const source = joinSource(cell.source);
  return (
    <div className="nbv-cell nbv-cell--md">
      <Markdown source={source} />
    </div>
  );
}

function RawCell({ cell }) {
  return (
    <pre className="mono nbv-cell nbv-cell--raw">
      {joinSource(cell.source)}
    </pre>
  );
}

// ---- top-level component --------------------------------------------

export function NotebookViewer({ fileId, fileName }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);
  const [raw, setRaw] = useState(false);
  const [grammarReady, setGrammarReady] = useState(false);
  const [attempt, setAttempt] = useState(0);

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
        toast.error("Couldn't load notebook");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId, attempt]);

  const parsed = useMemo(() => {
    if (text == null) return null;
    try {
      return { nb: JSON.parse(text), ok: true };
    } catch {
      return { nb: null, ok: false };
    }
  }, [text]);

  const grammar = useMemo(() => (parsed?.ok ? notebookLanguage(parsed.nb) : null), [parsed]);

  useEffect(() => {
    if (!grammar) { setGrammarReady(true); return; }
    let alive = true;
    setGrammarReady(false);
    const loader = GRAMMAR_LOADERS[grammar];
    if (!loader) { setGrammarReady(true); return; }
    loader().then(() => { if (alive) setGrammarReady(true); }).catch(() => { if (alive) setGrammarReady(true); });
    return () => { alive = false; };
  }, [grammar]);

  const cells = parsed?.ok && Array.isArray(parsed.nb?.cells) ? parsed.nb.cells : [];
  const showRaw = raw || (parsed && !parsed.ok);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return (
    <div className="nbv" onClick={(e) => e.stopPropagation()}>
      <div className="nbv__head">
        <span className="vw-head__icon"><Icon name="code" size={15} /></span>
        <span className="nbv__name">{fileName}</span>
        <span className="nbv__meta mono">
          {parsed && parsed.ok ? `${cells.length} cell${cells.length === 1 ? "" : "s"}` : parsed ? "unparsed" : ""}
        </span>
        {parsed && parsed.ok && (
          <button
            type="button"
            className="nbv__toggle mono"
            onClick={() => setRaw((v) => !v)}
            aria-pressed={raw}
            title={raw ? "Show rendered notebook" : "Show raw .ipynb JSON"}
          >
            {raw ? "Rendered" : "Raw"}
          </button>
        )}
      </div>
      <div className="nbv__body">
        {err ? (
          <ViewerError
            title="Couldn't load notebook"
            message={err}
            onRetry={retry}
            downloadUrl={originalMediaUrl(fileId)}
            downloadName={fileName}
          />
        ) : text == null ? (
          <NbvSkeleton />
        ) : showRaw ? (
          <>
            {parsed && !parsed.ok && (
              <div className="nbv__warn">
                <Icon name="alert" size={13} />
                Couldn't parse this notebook — showing raw source.
              </div>
            )}
            <pre className="nbv__raw mono">{text}</pre>
          </>
        ) : cells.length === 0 ? (
          <ViewerEmpty icon="code" title="Empty notebook" message="This notebook doesn't contain any cells." />
        ) : (
          <div className="nbv__cells">
            {cells.map((cell, i) => {
              const t = cell.cell_type;
              if (t === "markdown") return <MarkdownCell key={i} cell={cell} />;
              if (t === "code") return <CodeCell key={i} cell={cell} grammar={grammar} grammarReady={grammarReady} />;
              return <RawCell key={i} cell={cell} />;
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// Notebook-shaped shimmer: a markdown block, then a couple of code-cell
// blocks (gutter + body), so the swap to live cells feels seamless.
function NbvSkeleton() {
  return (
    <div className="nbv-skel" aria-busy="true" aria-live="polite">
      <span className="vw-sr">Loading notebook…</span>
      <div className="nbv-skel__md">
        <div className="vw-shimmer" style={{ width: "40%", height: 18 }} />
        <div className="vw-shimmer" style={{ width: "92%", height: 12 }} />
        <div className="vw-shimmer" style={{ width: "78%", height: 12 }} />
      </div>
      {[0, 1].map((k) => (
        <div key={k} className="nbv-skel__code">
          <div className="vw-shimmer nbv-skel__gutter" style={{ width: 42, height: 12 }} />
          <div className="nbv-skel__lines">
            <div className="vw-shimmer" style={{ width: "70%", height: 12 }} />
            <div className="vw-shimmer" style={{ width: "55%", height: 12 }} />
            <div className="vw-shimmer" style={{ width: "62%", height: 12 }} />
          </div>
        </div>
      ))}
    </div>
  );
}
