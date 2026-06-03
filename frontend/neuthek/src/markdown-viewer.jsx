// Markdown viewer — renders GitHub-Flavored Markdown (headings, lists,
// tables, task lists, links, fenced code blocks, images) as themed HTML
// styled to read like GitHub's `.markdown-body`, with a one-click toggle
// to the raw source.
//
// Rendering pipeline (all client-side, sanitized):
//   react-markdown  → parses CommonMark to an MDAST/HAST tree
//   remark-gfm      → GFM extensions (tables, strikethrough, task lists,
//                     autolinks)
//   rehype-sanitize → strips dangerous nodes/attrs from the HAST before
//                     React renders it. We extend GitHub's default schema
//                     to also permit `className` on `code`/`span`/`pre`
//                     (so language-* class hints survive) and a few
//                     harmless table attributes. `javascript:` / `data:`
//                     hrefs and inline event handlers are dropped by the
//                     sanitizer, so a hostile .md can't run script in our
//                     origin or exfiltrate the session cookie.
//
// Syntax highlighting: fenced code blocks are highlighted by Prism, but
// NOT through the sanitized HAST tree. The `code` renderer pulls the raw
// (already-sanitized) text content of the fence and runs Prism inside our
// own <CodeBlock>, injecting the resulting token markup via
// dangerouslySetInnerHTML. That HTML is something WE produce from text we
// control — Prism only HTML-escapes the source and wraps it in
// `<span class="token …">` — so no document-authored HTML is ever
// injected; the sanitizer still governs everything else (links, images,
// element set). Inline code is left un-highlighted (just the chip style).
// We share the SAME Prism singleton as code-preview.jsx (importing
// `prismjs/components/prism-core` returns the one global instance), so any
// grammar either module has already registered is reused for free.
//
// Styling: the rendered output is wrapped in `.markdown-body` and styled
// by the CANONICAL GitHub stylesheet (`github-markdown-css`, imported just
// below) so it reads as genuine GitHub — real heading borders, GitHub-blue
// links, GitHub tables/blockquotes/task-lists, the inline-code chip, and
// the fenced-code panel. styles-markdown.css adds only two thin layers on
// top: (a) a theme bridge that redefines GitHub's light/dark palette
// variables under the app's `[data-theme]` (the package switches them only
// by OS `prefers-color-scheme`; this app's theme is a manual toggle), and
// (b) a map of Prism's `.token.*` classes onto GitHub's prettylights syntax
// variables, so highlighted fences use GitHub's own syntax colors. GitHub
// paints the code PANEL; Prism colors the tokens — they don't collide
// (GitHub styles `.pl-*`, never `.token.*`).
//
// Data load mirrors csv-viewer / ics-viewer exactly: pull the original
// bytes through `fetchMediaBlob(originalMediaUrl(fileId))` (cookie auth +
// legacy-Bearer fallback live in that helper) and decode as text.
//
// Props: { fileId, fileName } — same contract as the other full-display
// viewers so preview.jsx can mount it inside the shared `pdf-modal__body`.
import React, { useState, useEffect, useMemo } from "react";
import toast from "react-hot-toast";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
// Canonical GitHub Markdown stylesheet (sindresorhus/github-markdown-css, MIT).
// This is the *real* `.markdown-body` CSS GitHub ships — it owns all the
// structure (heading sizes + h1/h2 underlines, GitHub-blue links, tables
// with header shading + zebra, blockquotes, task lists, <hr>, the inline-
// code chip, the fenced-code panel) so the rendered output reads as
// genuine GitHub rather than a hand-rolled approximation.
//
// We import the COMBINED build (not the standalone light/dark files): every
// base rule is variable-driven (`color: var(--fgColor-default)` etc.), and
// the theme palettes live in `--fgColor-*` / `--bgColor-*` / `--borderColor-*`
// / `--color-prettylights-syntax-*` custom properties. The package only
// switches those vars via `@media (prefers-color-scheme)`, which would tie
// the look to the OS setting — but this app's theme is a MANUAL toggle
// (`<html data-theme>`, see app.jsx / main.tsx) independent of the OS. So
// styles-markdown.css redefines those same vars under un-media-gated
// `[data-theme="dark"|"light"] .markdown-body` blocks (the theme bridge),
// making the app toggle authoritative in both directions. Importing it here
// (a JS bare specifier Vite resolves from node_modules) guarantees the
// bundle pulls it in; styles-markdown.css then layers only the thin bridge
// + the Prism-token → GitHub-syntax-color mapping on top.
import "github-markdown-css/github-markdown.css";
// Same Prism singleton code-preview.jsx uses. Importing the core + these
// bases registers them on the one global `Prism.languages`; the grammars
// loaded lazily below (and any code-preview has already loaded) all share
// it, so a fence and the code-preview pane never fight over Prism state.
import Prism from "prismjs/components/prism-core";
import "prismjs/components/prism-markup";
import "prismjs/components/prism-clike";
import "prismjs/components/prism-javascript";
import "prismjs/components/prism-css";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { safeHref } from "./safe-href.js";

// Extend GitHub's sanitize schema: keep the defaults (which already allow
// the GFM element set and strip scripts/handlers) but let class hints
// through on code-bearing elements so fenced blocks keep their
// `language-xxx` class, and allow `align` on table cells (GFM emits it).
//
// NOTE on highlighting + sanitize: Prism's token <span>s do NOT pass
// through this schema. We highlight in the <CodeBlock> renderer, AFTER
// sanitize, injecting the markup ourselves — so the schema only needs to
// preserve the `language-xxx` class hint on the fence's <code>, which the
// `className` allowance below covers. (Were Prism spans to flow through
// the sanitized tree, rehype-sanitize would strip their `class` and the
// colors would vanish; rendering them in our own component side-steps
// that entirely.)
export const MD_SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code || []), ["className"]],
    span: [...(defaultSchema.attributes?.span || []), ["className"]],
    pre: [...(defaultSchema.attributes?.pre || []), ["className"]],
    th: [...(defaultSchema.attributes?.th || []), ["align"]],
    td: [...(defaultSchema.attributes?.td || []), ["align"]],
    input: [...(defaultSchema.attributes?.input || []), ["type"], ["checked"], ["disabled"]],
  },
};

// ---- fenced-code language resolution -------------------------------------
// Fence info strings ("```py") map to a Prism grammar id. Aliases collapse
// the common short forms onto the grammar name Prism registers, mirroring
// the subset of code-preview's MIME_TO_LANG that fences actually use.
const LANG_ALIASES = {
  js: "javascript", jsx: "jsx", mjs: "javascript", cjs: "javascript", node: "javascript",
  ts: "typescript", tsx: "tsx",
  py: "python", py3: "python", python3: "python",
  rb: "ruby", sh: "bash", shell: "bash", zsh: "bash", console: "bash", shellsession: "bash",
  ps: "powershell", ps1: "powershell", pwsh: "powershell",
  yml: "yaml",
  md: "markdown", markdown: "markdown",
  html: "markup", xml: "markup", svg: "markup", vue: "markup", svelte: "markup",
  "c++": "cpp", "c#": "csharp", cs: "csharp", objc: "objectivec",
  golang: "go", rs: "rust", kt: "kotlin",
  jsonc: "json", json5: "json",
  dockerfile: "docker", tf: "hcl", tfvars: "hcl",
  plaintext: null, text: null, txt: null, plain: null, "": null,
};

// Prism grammar id → lazy import. A subset of code-preview's LANG_LOADERS
// covering the languages people actually fence in Markdown. Eager bases
// (javascript / css / markup / clike) resolve immediately. Anything not
// listed simply renders as un-highlighted (escaped) text — the right
// fallback for an unknown fence label.
const LANG_LOADERS = {
  javascript: () => Promise.resolve(),
  css: () => Promise.resolve(),
  markup: () => Promise.resolve(),
  jsx: () => import("prismjs/components/prism-jsx"),
  typescript: () => import("prismjs/components/prism-typescript"),
  tsx: () => import("prismjs/components/prism-typescript").then(() => import("prismjs/components/prism-jsx")).then(() => import("prismjs/components/prism-tsx")),
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
  cpp: () => import("prismjs/components/prism-c").then(() => import("prismjs/components/prism-cpp")),
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
  erlang: () => import("prismjs/components/prism-erlang"),
  haskell: () => import("prismjs/components/prism-haskell"),
  ocaml: () => import("prismjs/components/prism-ocaml"),
  objectivec: () => import("prismjs/components/prism-objectivec"),
  groovy: () => import("prismjs/components/prism-groovy"),
  julia: () => import("prismjs/components/prism-julia"),
  solidity: () => import("prismjs/components/prism-solidity"),
  hcl: () => import("prismjs/components/prism-hcl"),
  nix: () => import("prismjs/components/prism-nix"),
  scss: () => import("prismjs/components/prism-scss"),
  sass: () => import("prismjs/components/prism-sass"),
  less: () => import("prismjs/components/prism-less"),
  markdown: () => import("prismjs/components/prism-markdown"),
  latex: () => import("prismjs/components/prism-latex"),
  docker: () => import("prismjs/components/prism-docker"),
  makefile: () => import("prismjs/components/prism-makefile"),
  ini: () => import("prismjs/components/prism-ini"),
  toml: () => import("prismjs/components/prism-toml"),
  graphql: () => import("prismjs/components/prism-graphql"),
  protobuf: () => import("prismjs/components/prism-protobuf"),
  diff: () => import("prismjs/components/prism-diff"),
  json: () => import("prismjs/components/prism-json"),
  yaml: () => import("prismjs/components/prism-yaml"),
};

// Own-property lookup of a grammar loader. Bracket access alone could
// surface inherited Object.prototype members (e.g. `constructor`) for an
// adversarial fence label, so gate every loader/highlight decision on this.
function getLoader(lang) {
  if (typeof lang !== "string") return null;
  return Object.prototype.hasOwnProperty.call(LANG_LOADERS, lang)
    ? LANG_LOADERS[lang]
    : null;
}

// Pull the grammar id out of react-markdown's `language-xxx` class hint
// (it lowercases the fence label into a class). Returns the resolved Prism
// id, or null when the label is unknown / explicitly plain-text.
function langFromClassName(className) {
  if (typeof className !== "string") return null;
  const m = /(?:^|\s)language-([^\s]+)/i.exec(className);
  if (!m) return null;
  const raw = m[1].toLowerCase();
  // hasOwnProperty (not `in`) so a fence labeled e.g. `constructor` /
  // `toString` doesn't resolve to an inherited Object.prototype member.
  const aliased = Object.prototype.hasOwnProperty.call(LANG_ALIASES, raw)
    ? LANG_ALIASES[raw]
    : raw;
  return typeof aliased === "string" ? aliased : null;
}

// HTML-escape for the un-highlighted code path (before a grammar loads, on
// a plain fence, or if Prism throws). Mirrors code-preview's escapeHtml.
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

// Recover the raw text of a fenced block. Prefer the authoritative HAST
// node value (set before React rendering, so it can't be perturbed by a
// custom child renderer), falling back to flattening React children.
function codeText(node, children) {
  const v = node?.children?.[0];
  if (v && v.type === "text" && typeof v.value === "string") return v.value;
  if (typeof children === "string") return children;
  if (Array.isArray(children)) {
    return children
      .map((c) => (typeof c === "string" ? c : c?.props?.children ? codeText(null, c.props.children) : ""))
      .join("");
  }
  return children == null ? "" : String(children);
}

// A single fenced code block: lazily loads the grammar, highlights with
// Prism, and injects the token markup. Hooks are unconditional. The
// highlighted HTML never touches the sanitized tree — it's produced here
// from text we already hold, so the only markup we inject is Prism's own
// token spans over (HTML-escaped) source.
function CodeBlock({ lang, text }) {
  // Ready immediately when there's nothing to load (no/unknown grammar).
  const [grammarReady, setGrammarReady] = useState(() => !getLoader(lang));

  useEffect(() => {
    const loader = getLoader(lang);
    if (!loader) { setGrammarReady(true); return; }
    // Already registered (e.g. an eager base, or code-preview loaded it)?
    // Mark ready synchronously so we don't flash un-highlighted text.
    if (Prism.languages[lang]) { setGrammarReady(true); return; }
    let alive = true;
    setGrammarReady(false);
    loader()
      .then(() => { if (alive) setGrammarReady(true); })
      .catch(() => { if (alive) setGrammarReady(true); });
    return () => { alive = false; };
  }, [lang]);

  const innerHtml = useMemo(() => {
    // Only highlight grammars we actually have a loader for — keeps an
    // adversarial fence label off the bracket-access path into Prism.
    if (getLoader(lang) && grammarReady && Prism.languages[lang]) {
      try {
        return Prism.highlight(text, Prism.languages[lang], lang);
      } catch {
        /* fall through to escaped plain text */
      }
    }
    return escapeHtml(text);
  }, [text, lang, grammarReady]);

  return (
    <pre className="md-code">
      <code
        className={lang ? `language-${lang}` : undefined}
        dangerouslySetInnerHTML={{ __html: innerHtml }}
      />
    </pre>
  );
}

// Custom renderers. Links route through `safeHref` (same scheme allow-list
// the VCF/ICS viewers use) as a belt-and-suspenders layer on top of the
// sanitizer, opening external links in a new tab with noopener. Images cap
// to the container width. `pre` is a pass-through so <CodeBlock> is the
// sole <pre> element (no nested <pre><pre>); `code` splits inline (chip)
// from fenced (highlighted) rendering.
// Exported so OTHER hosts that already hold the markdown TEXT (e.g. the
// archive viewer previewing a `.md` from inside a zip) can render through
// the EXACT same pipeline — same fenced-code highlighting, same safe-href
// links, same image guard — instead of duplicating renderers or falling
// back to raw source. Pair it with MD_SANITIZE_SCHEMA + remarkGfm.
export const MD_COMPONENTS = {
  // react-markdown passes a `node` (the HAST node) into every custom
  // renderer; we strip it before spreading the rest onto the DOM element
  // so React doesn't warn about an unknown `node` attribute.
  a({ node, href, children, ...props }) {
    const safe = safeHref(href || "");
    if (!safe) {
      return <span title="Link uses an unsafe scheme and was disabled" {...props}>{children}</span>;
    }
    return (
      <a href={safe} target="_blank" rel="noreferrer noopener" {...props}>
        {children}
      </a>
    );
  },
  img({ node, src, alt, ...props }) {
    // Only allow http(s) / data:image sources; anything else is dropped
    // to a caption-less placeholder. (rehype-sanitize already filters
    // `src`, but data: URIs are permitted by its default img protocols,
    // so we keep them — they're inert as image data.)
    const ok = typeof src === "string" && /^(https?:|data:image\/)/i.test(src);
    return ok ? (
      <img src={src} alt={alt || ""} loading="lazy" {...props} />
    ) : (
      <span className="md-img-fallback">{alt || "image"}</span>
    );
  },
  // Pass the fenced-block wrapper through untouched so <CodeBlock> owns the
  // <pre>. react-markdown only emits <pre> around code, so this is safe.
  pre({ node, children }) {
    return <>{children}</>;
  },
  code({ node, className, children, ...props }) {
    const lang = langFromClassName(className);
    // Distinguish a fenced/indented BLOCK from INLINE code. react-markdown
    // v10 dropped the old `inline` prop, so we infer it: a block carries a
    // `language-xxx` hint (fence with an info string) OR its text spans
    // multiple lines (any fence/indented block keeps CommonMark's trailing
    // newline, and multi-line content can't be inline). Everything else is
    // inline → the chip style. We deliberately don't gate on the HAST
    // parent being <pre> (not exposed here) — these two signals cover every
    // real case and degrade safely.
    const hasLangHint = /(?:^|\s)language-/i.test(className || "");
    const isBlock = hasLangHint || /\n/.test(codeText(node, children));
    if (!isBlock) {
      return (
        <code className="md-inline-code" {...props}>
          {children}
        </code>
      );
    }
    // Strip the trailing newline CommonMark keeps on a fence's text so the
    // panel doesn't render a blank final line.
    const raw = codeText(node, children).replace(/\n$/, "");
    return <CodeBlock lang={lang} text={raw} />;
  },
};

// Render an in-memory Markdown STRING through the full pipeline (GFM,
// sanitize, fenced-code highlighting, safe links). Exported so other
// hosts that already hold the text — the archive inner-file preview and
// the encrypted Vault's decrypted `.md` — render identically to the
// gallery viewer without re-wiring the plugins or duplicating renderers.
// The caller wraps it in a `.markdown-body` scroll container.
export function MarkdownInline({ text }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeSanitize, MD_SANITIZE_SCHEMA]]}
      components={MD_COMPONENTS}
      skipHtml
    >
      {text || ""}
    </ReactMarkdown>
  );
}

export function MarkdownViewer({ fileId, fileName }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState(null);
  const [raw, setRaw] = useState(false);

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
        toast.error("Couldn't load document");
      }
    })();
    return () => { cancelled = true; };
  }, [fileId]);

  const lineCount = useMemo(() => {
    if (text == null) return 0;
    const trailing = text.endsWith("\n") ? 1 : 0;
    return text.split("\n").length - trailing;
  }, [text]);

  return (
    <div className="md-viewer" onClick={(e) => e.stopPropagation()}
         style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%", flex: "1 1 auto", minWidth: 0, minHeight: 0, background: "var(--surface)", color: "var(--ink)" }}>
      <div className="md-viewer__head"
           style={{
             display: "flex", alignItems: "center", gap: 10,
             padding: "10px 16px", borderBottom: "1px solid var(--line)",
             background: "var(--surface-2)", position: "sticky", top: 0, zIndex: 1,
           }}>
        <span className="md-viewer__name"
              style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {fileName}
        </span>
        <span className="md-viewer__meta mono" style={{ marginLeft: "auto", fontSize: 11.5, color: "var(--ink-3)" }}>
          {text != null ? `${lineCount} lines` : ""}
        </span>
        <button
          type="button"
          className="md-viewer__toggle mono"
          onClick={() => setRaw((v) => !v)}
          aria-pressed={raw}
          title={raw ? "Show rendered Markdown" : "Show raw source"}
          style={{
            fontSize: 11, padding: "3px 9px", cursor: "pointer",
            color: raw ? "var(--ink)" : "var(--ink-3)",
            background: raw ? "var(--surface)" : "transparent",
            border: "1px solid var(--line)", borderRadius: 6,
          }}
        >
          {raw ? "Rendered" : "Raw"}
        </button>
      </div>
      <div className="md-viewer__body"
           style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {err ? (
          <div style={{ padding: 28, fontSize: 13, color: "#b34141", textAlign: "center" }}>{err}</div>
        ) : text == null ? (
          <div style={{ padding: 28, fontSize: 13, color: "var(--ink-3)", textAlign: "center" }}>Loading…</div>
        ) : raw ? (
          <pre className="mono" style={{
            margin: 0, padding: "16px 18px", fontSize: 12.5, lineHeight: 1.55,
            whiteSpace: "pre-wrap", wordBreak: "break-word",
            color: "var(--ink-2)", fontFamily: "var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace)",
          }}>
            {text}
          </pre>
        ) : (
          <div className="md-viewer__rendered markdown-body">
            <MarkdownInline text={text} />
          </div>
        )}
      </div>
    </div>
  );
}
