// Code-file preview pane — syntax-highlighted source code with line numbers.
//
// Sits next to the PDF preview in the gallery's preview panel: when the
// selected file has a `text/*` or recognized code MIME, we fetch the raw
// bytes via /images/{id}/original (signed URL or raw blob endpoint),
// pick a Prism grammar from the MIME's `text/x-<lang>` tail, and render
// the highlighted HTML inside a scrollable, line-numbered table.
//
// This is a VS-Code / GitHub-flavored viewer: a header toolbar with
// Copy / word-wrap / find-in-file, a sticky non-selectable line-number
// gutter (clickable, anchors a `#Lnn` hash, lights the current line),
// and a tuned dark Prism token theme defined in styles.css.
//
// Grammars are lazily imported so the initial bundle doesn't pay for
// every language at once. Each entry maps a Prism component to its
// import path; an unknown MIME falls back to plain monospace text,
// which is the right behavior for log files and Procfile-shaped scripts.
import React, { useEffect, useState as useStateC, useMemo, useRef, useCallback, useLayoutEffect } from "react";
import Prism from "prismjs/components/prism-core";
// Markup / clike are the bases the others depend on — eagerly loaded
// so the lazy import below doesn't have to chain another await.
import "prismjs/components/prism-markup";
import "prismjs/components/prism-clike";
import "prismjs/components/prism-javascript";
import "prismjs/components/prism-css";
import { API_BASE_URL, tokens } from "@/api/client";
import { Icon } from "./icons.jsx";

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
  "text/x-nim": "nim",
  "text/x-crystal": "crystal",
  "text/x-groovy": "groovy",
  "text/x-julia": "julia",
  "text/x-solidity": "solidity",
  "text/x-hcl": "hcl",
  "text/x-nix": "nix",
  "text/x-d": "d",
  "text/x-pascal": "pascal",
  "text/x-fortran": "fortran",
  "text/x-cobol": "cobol",
  "text/x-asm": "nasm",
  "text/x-vala": "vala",
  "text/x-haxe": "haxe",
  "text/x-scheme": "scheme",
  "text/x-lisp": "lisp",
  "text/x-fennel": "lisp",       // Fennel is a Lisp; Lisp grammar is closest
  "text/x-coffeescript": "coffeescript",
  "text/x-purescript": "purescript",
  "text/x-reason": "reason",
  "text/x-rescript": "rescript",
  "text/x-fsharp": "fsharp",
  "text/x-objectivec": "objectivec",
  "text/x-applescript": "applescript",
  "text/x-autohotkey": "autohotkey",
  "text/x-autoit": "autoit",
  "text/x-arduino": "arduino",
  "text/x-processing": "processing",
  "text/x-idris": "idris",
  "text/x-agda": "agda",
  "text/x-wasm": "wasm",
  "text/x-odin": "odin",
  "text/x-batch": "batch",
  "text/x-tcl": "tcl",
  "text/x-vim": "vim",
  "text/x-awk": "awk",
  "text/x-pug": "pug",
  "text/x-haml": "haml",
  "text/x-twig": "twig",
  "text/x-handlebars": "handlebars",
  "text/x-liquid": "liquid",
  "text/x-erb": "erb",
  "text/x-ejs": "ejs",
  "text/x-cmake": "cmake",
  "text/x-jinja2": "django",     // Prism's django grammar covers jinja2/twig-ish
  "text/x-makefile": "makefile",
  "text/x-toml": "toml",
  "text/x-gradle": "groovy",
  "text/turtle": "turtle",
  "text/n3": "turtle",
  "text/x-objc": "objectivec",
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

// Filename / extension → a MIME that MIME_TO_LANG understands. The
// gallery path always has the backend's `text/x-<lang>` mime to hand,
// but other callers (e.g. the archive viewer previewing a file from
// INSIDE a zip) only know the inner file's *name* — the extract
// endpoint's Content-Type is usually octet-stream. This map lets those
// callers derive the same grammar key from the extension alone.
//
// Keys are bare lowercase extensions; a handful of well-known
// extensionless basenames (Dockerfile / Makefile) are matched by name
// in mimeFromFilename below. Values intentionally reuse the exact MIME
// strings used as MIME_TO_LANG keys so the grammar lookup is shared.
const EXT_TO_MIME = {
  js: "text/javascript", mjs: "text/javascript", cjs: "text/javascript",
  ts: "text/x-typescript", mts: "text/x-typescript", cts: "text/x-typescript",
  jsx: "text/x-jsx", tsx: "text/x-tsx",
  vue: "text/x-vue", svelte: "text/x-svelte",
  py: "text/x-python", pyi: "text/x-python", pyw: "text/x-python",
  rb: "text/x-ruby", php: "text/x-php",
  java: "text/x-java", kt: "text/x-kotlin", kts: "text/x-kotlin",
  scala: "text/x-scala", swift: "text/x-swift",
  go: "text/x-go", rs: "text/x-rust",
  c: "text/x-c", h: "text/x-c",
  cpp: "text/x-c++", cc: "text/x-c++", cxx: "text/x-c++", hpp: "text/x-c++", hxx: "text/x-c++", "c++": "text/x-c++",
  cs: "text/x-csharp", dart: "text/x-dart", lua: "text/x-lua",
  r: "text/x-r", pl: "text/x-perl", pm: "text/x-perl", perl: "text/x-perl",
  sh: "text/x-shellscript", bash: "text/x-shellscript", zsh: "text/x-shellscript",
  fish: "text/x-shellscript", ksh: "text/x-shellscript",
  ps1: "text/x-powershell", psm1: "text/x-powershell", psd1: "text/x-powershell",
  sql: "text/x-sql", psql: "text/x-sql", plsql: "text/x-sql",
  clj: "text/x-clojure", cljs: "text/x-clojure", cljc: "text/x-clojure", edn: "text/x-clojure",
  ex: "text/x-elixir", exs: "text/x-elixir",
  elm: "text/x-elm", erl: "text/x-erlang", hrl: "text/x-erlang",
  hs: "text/x-haskell", lhs: "text/x-haskell",
  ml: "text/x-ocaml", mli: "text/x-ocaml",
  zig: "text/x-zig", nim: "text/x-nim", nims: "text/x-nim",
  cr: "text/x-crystal",
  groovy: "text/x-groovy", gradle: "text/x-gradle",
  jl: "text/x-julia", sol: "text/x-solidity",
  hcl: "text/x-hcl", tf: "text/x-hcl", tfvars: "text/x-hcl",
  nix: "text/x-nix", d: "text/x-d",
  pas: "text/x-pascal", pp: "text/x-pascal",
  f: "text/x-fortran", f90: "text/x-fortran", f95: "text/x-fortran", for: "text/x-fortran",
  cob: "text/x-cobol", cbl: "text/x-cobol",
  asm: "text/x-asm", s: "text/x-asm",
  vala: "text/x-vala", hx: "text/x-haxe",
  scm: "text/x-scheme", ss: "text/x-scheme", rkt: "text/x-scheme",
  lisp: "text/x-lisp", lsp: "text/x-lisp", el: "text/x-lisp", fnl: "text/x-fennel",
  coffee: "text/x-coffeescript",
  purs: "text/x-purescript", re: "text/x-reason", rei: "text/x-reason",
  res: "text/x-rescript",
  fs: "text/x-fsharp", fsi: "text/x-fsharp", fsx: "text/x-fsharp",
  m: "text/x-objectivec", mm: "text/x-objectivec",
  tcl: "text/x-tcl", vim: "text/x-vim", awk: "text/x-awk",
  bat: "text/x-batch", cmd: "text/x-batch",
  pug: "text/x-pug", jade: "text/x-pug", haml: "text/x-haml",
  cmake: "text/x-cmake",
  toml: "text/x-toml",
  scss: "text/x-scss", sass: "text/x-sass", less: "text/x-less",
  html: "text/html", htm: "text/html",
  css: "text/css", xml: "text/xml", xsl: "text/xml", xslt: "text/xml", xsd: "text/xml", plist: "text/xml",
  svg: "image/svg+xml",
  md: "text/markdown", markdown: "text/markdown", mdx: "text/markdown",
  rst: "text/x-rst", tex: "text/x-tex", latex: "text/x-tex",
  env: "text/x-dotenv",
  ini: "text/x-properties", cfg: "text/x-properties", conf: "text/x-properties", properties: "text/x-properties",
  graphql: "text/x-graphql", gql: "text/x-graphql",
  proto: "text/x-protobuf",
  diff: "text/x-diff", patch: "text/x-diff",
  json: "application/json", json5: "application/json", jsonc: "application/json", ndjson: "application/json",
  ipynb: "application/x-ipynb+json",
  yaml: "text/x-yaml", yml: "text/x-yaml",
  // Plain-text shapes — no grammar, render as monospace (still nicer
  // than a bare <pre>: gutter, copy, find, wrap).
  txt: "text/plain", log: "text/plain", text: "text/plain",
};

// Extensionless basenames worth special-casing (case-insensitive).
const NAME_TO_MIME = {
  dockerfile: "text/x-dockerfile",
  makefile: "text/x-makefile",
  "cmakelists.txt": "text/x-cmake",
  procfile: "text/plain",
  ".gitignore": "text/plain",
  ".gitattributes": "text/plain",
  ".dockerignore": "text/plain",
  ".npmignore": "text/plain",
  ".env": "text/x-dotenv",
};

// Resolve a filename (or bare extension) to a MIME string that
// MIME_TO_LANG / isCodeMime understand. Tolerant of a leading dot, mixed
// case, and a path-y name (`src/app.py`). Returns null when nothing
// matches, so callers can fall back to their own heuristics (e.g. the
// archive viewer's binary download path). Used by both the gallery's
// CodePreview-adjacent callers and the archive inner-file viewer.
export function mimeFromFilename(name) {
  if (!name) return null;
  const base = String(name).split(/[\\/]/).pop().toLowerCase();
  if (NAME_TO_MIME[base]) return NAME_TO_MIME[base];
  const dot = base.lastIndexOf(".");
  // No dot (or leading-dot dotfile like `.bashrc`) → try the whole base
  // as an extension token, else give up.
  const ext = dot > 0 ? base.slice(dot + 1) : base.replace(/^\./, "");
  return EXT_TO_MIME[ext] || null;
}

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
  nim: () => import("prismjs/components/prism-nim"),
  crystal: () => import("prismjs/components/prism-crystal"),
  groovy: () => import("prismjs/components/prism-groovy"),
  julia: () => import("prismjs/components/prism-julia"),
  solidity: () => import("prismjs/components/prism-solidity"),
  hcl: () => import("prismjs/components/prism-hcl"),
  nix: () => import("prismjs/components/prism-nix"),
  d: () => import("prismjs/components/prism-d"),
  pascal: () => import("prismjs/components/prism-pascal"),
  fortran: () => import("prismjs/components/prism-fortran"),
  cobol: () => import("prismjs/components/prism-cobol"),
  nasm: () => import("prismjs/components/prism-nasm"),
  vala: () => import("prismjs/components/prism-vala"),
  haxe: () => import("prismjs/components/prism-haxe"),
  scheme: () => import("prismjs/components/prism-scheme"),
  lisp: () => import("prismjs/components/prism-lisp"),
  coffeescript: () => import("prismjs/components/prism-coffeescript"),
  purescript: () => import("prismjs/components/prism-purescript"),
  reason: () => import("prismjs/components/prism-reason"),
  rescript: () => import("prismjs/components/prism-rescript"),
  fsharp: () => import("prismjs/components/prism-fsharp"),
  objectivec: () => import("prismjs/components/prism-objectivec"),
  applescript: () => import("prismjs/components/prism-applescript"),
  autohotkey: () => import("prismjs/components/prism-autohotkey"),
  autoit: () => import("prismjs/components/prism-autoit"),
  arduino: () => import("prismjs/components/prism-c").then(() => import("prismjs/components/prism-cpp")).then(() => import("prismjs/components/prism-arduino")),
  processing: () => import("prismjs/components/prism-processing"),
  idris: () => import("prismjs/components/prism-idris"),
  agda: () => import("prismjs/components/prism-agda"),
  wasm: () => import("prismjs/components/prism-wasm"),
  odin: () => import("prismjs/components/prism-odin"),
  batch: () => import("prismjs/components/prism-batch"),
  tcl: () => import("prismjs/components/prism-tcl"),
  vim: () => import("prismjs/components/prism-vim"),
  awk: () => import("prismjs/components/prism-awk"),
  pug: () => import("prismjs/components/prism-pug"),
  haml: () => import("prismjs/components/prism-haml"),
  twig: () => import("prismjs/components/prism-markup-templating").then(() => import("prismjs/components/prism-twig")),
  handlebars: () => import("prismjs/components/prism-markup-templating").then(() => import("prismjs/components/prism-handlebars")),
  liquid: () => import("prismjs/components/prism-markup-templating").then(() => import("prismjs/components/prism-liquid")),
  erb: () => import("prismjs/components/prism-markup-templating").then(() => import("prismjs/components/prism-ruby")).then(() => import("prismjs/components/prism-erb")),
  ejs: () => import("prismjs/components/prism-markup-templating").then(() => import("prismjs/components/prism-ejs")),
  cmake: () => import("prismjs/components/prism-cmake"),
  django: () => import("prismjs/components/prism-markup-templating").then(() => import("prismjs/components/prism-django")),
  toml: () => import("prismjs/components/prism-toml"),
  turtle: () => import("prismjs/components/prism-turtle"),
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

// Find-in-file safety valves: above this many matches we stop counting
// (the counter shows "500+") so a 1-char query on a huge file can't
// pin the main thread; and we never search a query shorter than this.
const MAX_MATCHES = 2000;
const MIN_QUERY_LEN = 1;

const WRAP_KEY = "neuthek.code.wrap";

// ---- HTML escaping for the plain-text (no-grammar) render path ----
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

// Escape a user query for safe use inside a RegExp.
function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// ============================================================
// Log mode — severity-aware rendering for `.log` (and log-shaped)
// files. Instead of a syntax grammar we run a line-classification
// pass: each line is tagged with a level (error / warn / info /
// debug) from the conventional token near its start, the whole row
// gets a subtle left accent + the level keyword is tinted, and
// timestamps are faintly dimmed so the eye lands on the message.
//
// This composes with find-in-file: the per-line HTML is built by
// escaping, then wrapping the level keyword / timestamp in tinted
// spans at known plain-text offsets, then (if there's a query)
// layering <mark> ranges over the result via markRangesInHtml —
// which already walks tags safely.
// ============================================================

// Severity token → level bucket. Whole-word, case-insensitive. Order
// in the alternation matters only for the regex; bucket is the value.
const LOG_LEVELS = {
  fatal: "error", critical: "error", panic: "error", emerg: "error",
  emergency: "error", alert: "error", error: "error", err: "error",
  fail: "error", failed: "error", failure: "error", severe: "error",
  warn: "warn", warning: "warn",
  notice: "info", info: "info", information: "info", log: "info",
  debug: "debug", trace: "debug", verbose: "debug", fine: "debug",
  finer: "debug", finest: "debug", dbg: "debug",
};

// Display order + singular/plural labels for the legend chips and the
// count summary. Keyed by bucket; only buckets with a non-zero count
// are shown.
const LOG_LEVEL_META = [
  { key: "error", label: "Error", one: "error", many: "errors" },
  { key: "warn", label: "Warn", one: "warning", many: "warnings" },
  { key: "info", label: "Info", one: "info", many: "info" },
  { key: "debug", label: "Debug", one: "debug", many: "debug" },
];

// One alternation of every level token, longest-first so e.g. `WARNING`
// is preferred over `WARN`. Anchored to a word boundary; we only look
// at the line PREFIX (first ~64 chars) so a stray "error" deep in a
// message body doesn't recolor an INFO line.
const LOG_LEVEL_WORDS = Object.keys(LOG_LEVELS).sort((a, b) => b.length - a.length);
const LOG_LEVEL_RE = new RegExp(`\\b(${LOG_LEVEL_WORDS.join("|")})\\b`, "i");
const LOG_PREFIX_LEN = 64;

// Timestamp shapes to faintly dim, matched near the line start:
//   ISO-8601           2026-05-31T08:12:04.123Z  / 2026-05-31 08:12:04
//   bracketed/syslog   [2026-05-31 08:12:04]  [08:12:04]  Mar 31 08:12:04
//   bare clock         08:12:04(.123)
// We capture the FIRST such run in the prefix and tint just that span.
const LOG_TS_RE = new RegExp(
  "^\\s*\\[?(" +
    // ISO date(+time)
    "\\d{4}-\\d{2}-\\d{2}(?:[T ]\\d{2}:\\d{2}:\\d{2}(?:[.,]\\d{1,9})?(?:Z|[+-]\\d{2}:?\\d{2})?)?" +
    "|" +
    // syslog "Mon DD HH:MM:SS"
    "[A-Z][a-z]{2}\\s+\\d{1,2}\\s+\\d{2}:\\d{2}:\\d{2}" +
    "|" +
    // bare clock
    "\\d{2}:\\d{2}:\\d{2}(?:[.,]\\d{1,9})?" +
  ")\\]?"
);

// Classify a single line → level bucket ("error" | "warn" | "info" |
// "debug") or null, plus the [start,end) of the matched level keyword
// (in plain-text coords) so the renderer can tint just that word.
function classifyLogLine(line) {
  if (!line) return null;
  const head = line.length > LOG_PREFIX_LEN ? line.slice(0, LOG_PREFIX_LEN) : line;
  const m = LOG_LEVEL_RE.exec(head);
  if (!m) return null;
  const level = LOG_LEVELS[m[1].toLowerCase()];
  if (!level) return null;
  return { level, kw: [m.index, m.index + m[1].length] };
}

// Decide whether to treat this text as a log. True when the filename
// ends in `.log`, OR enough non-blank lines carry a level token that
// it's clearly log output (sampled, so a huge file stays cheap).
function detectLogMode(text, filename) {
  if (filename && /\.log$/i.test(String(filename).trim())) return true;
  if (text == null) return false;
  const lines = text.split("\n");
  let scanned = 0;
  let hits = 0;
  for (let i = 0; i < lines.length && scanned < 400; i++) {
    const ln = lines[i];
    if (!ln || !ln.trim()) continue;
    scanned++;
    if (classifyLogLine(ln)) hits++;
  }
  // Need a decent sample and a strong majority so prose / config files
  // with an occasional "error" word don't flip into log mode.
  return scanned >= 8 && hits / scanned >= 0.6;
}

// Build the HTML for one log line: escape it, then wrap the timestamp
// run and the level keyword in tinted spans (by plain-text offset).
// Returns plain escaped HTML when there's nothing to tint. `ranges`,
// when present, are find-in-file match ranges layered on last.
function renderLogLineHtml(line, info, ranges, activeMatch) {
  if (!line.length) return "&nbsp;";
  // Collect tint spans as [start, end, className] in plain-text coords,
  // non-overlapping, left-to-right.
  const spans = [];
  const ts = LOG_TS_RE.exec(line);
  if (ts && ts[0].length) {
    // Tint the matched timestamp run; trim leading whitespace offset.
    const lead = ts[0].length - ts[0].trimStart().length;
    const s = lead;
    const e = ts[0].length;
    if (e > s) spans.push([s, e, "cv-log-ts"]);
  }
  if (info && info.kw) {
    const [s, e] = info.kw;
    // Avoid double-wrapping if the keyword sits inside the timestamp
    // span (won't normally happen, but stay safe).
    if (!spans.length || s >= spans[spans.length - 1][1]) {
      spans.push([s, e, `cv-log-kw cv-log-kw--${info.level}`]);
    }
  }
  let baseHtml;
  if (!spans.length) {
    baseHtml = escapeHtml(line);
  } else {
    let out = "";
    let pos = 0;
    for (const [s, e, cls] of spans) {
      if (s > pos) out += escapeHtml(line.slice(pos, s));
      out += `<span class="${cls}">${escapeHtml(line.slice(s, e))}</span>`;
      pos = e;
    }
    if (pos < line.length) out += escapeHtml(line.slice(pos));
    baseHtml = out;
  }
  return ranges ? markRangesInHtml(baseHtml, ranges, 0, activeMatch) : baseHtml;
}

// Wrap plain-text match ranges with <mark> inside an *already-highlighted*
// HTML line, preserving Prism's tags. We walk the html char by char,
// tracking the plain-text offset (advanced only for visible text — tags
// and entities don't move the offset by their literal length). When the
// offset enters/leaves a match range we emit <mark …>/</mark>. The active
// match (matchBase + local index === activeIdx) gets the "is-current"
// class so it can be styled brighter and scrolled to.
//
// `ranges` is a sorted, non-overlapping list of [start, end) pairs in
// plain-text coordinates for this line. Returns an HTML string.
function markRangesInHtml(html, ranges, matchBase, activeIdx) {
  if (!ranges.length) return html;
  let out = "";
  let pos = 0;          // plain-text offset within the line
  let i = 0;            // cursor within the html string
  let ri = 0;           // index into ranges
  let open = false;     // are we currently inside a <mark>?
  const n = html.length;

  // Open a <mark> for the range at index `rangeNo`. The 3rd tuple slot
  // holds that match's flat index, so we can flag the active hit.
  const openMark = (rangeNo) => {
    const localIdx = ranges[rangeNo][2];
    const cur = matchBase + localIdx === activeIdx;
    out += cur ? '<mark class="cv-hit cv-hit--current">' : '<mark class="cv-hit">';
    open = true;
  };
  const closeMark = () => { out += "</mark>"; open = false; };

  while (i < n) {
    const ch = html[i];
    if (ch === "<") {
      // Copy the whole tag verbatim; tags don't advance plain-text pos.
      // If a mark is open we must close it before the tag and reopen
      // after, so <mark> never straddles a Prism span boundary illegally
      // (marks may nest fine, but keeping them tag-tight is simplest).
      const end = html.indexOf(">", i);
      const tag = end === -1 ? html.slice(i) : html.slice(i, end + 1);
      if (open) {
        closeMark();
        out += tag;
        // Reopen only if the current range still has visible chars left
        // (avoids emitting an empty <mark></mark> at a range's tail).
        if (pos < ranges[ri][1]) openMark(ri);
      } else {
        out += tag;
      }
      i += tag.length;
      continue;
    }
    // A visible char (or an HTML entity like &lt; / &amp; — which counts
    // as ONE plain-text char). Determine the token to copy.
    let token = ch;
    if (ch === "&") {
      const semi = html.indexOf(";", i);
      if (semi !== -1 && semi - i <= 10) token = html.slice(i, semi + 1);
    }
    // Boundary handling at this plain-text position `pos`.
    // Close finished range first.
    if (open && pos >= ranges[ri][1]) {
      closeMark();
      ri++;
    }
    // Open a new range that starts here.
    if (!open && ri < ranges.length && pos >= ranges[ri][0] && pos < ranges[ri][1]) {
      openMark(ri);
    }
    out += token;
    i += token.length;
    pos += 1;
  }
  if (open) closeMark();
  return out;
}

// Gallery entry point: fetches the original bytes for `fileId` (capping
// the rendered size), then hands the decoded text to the presentational
// CodeView below. Props are unchanged from before the CodeView split —
// preview.jsx depends on this exact signature.
export function CodePreview({ fileId, mime, byteSize, filename }) {
  const [text, setText] = useStateC(null);
  const [error, setError] = useStateC(null);
  const [truncated, setTruncated] = useStateC(false);

  useEffect(() => {
    if (!fileId) return;
    let alive = true;
    setText(null);
    setError(null);
    setTruncated(false);
    // Cookie auth: ship the session cookie via credentials. Legacy
    // Bearer kept for users mid-migration.
    let legacy = null;
    try { legacy = localStorage.getItem("neuthek.jwt"); } catch {}
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

  return (
    <CodeView
      text={text}
      error={error}
      filename={filename}
      mime={mime}
      byteSize={byteSize}
      truncated={truncated}
    />
  );
}

// Presentational syntax-highlighted code viewer. Takes RAW TEXT that's
// already been fetched + size-capped by the caller — it never fetches.
// Owns the full VS-Code-style UI: Prism highlighting, the toolbar
// (lang/lines/size, find-in-file, word-wrap, copy), the sticky line
// gutter with #Lnn anchoring, and all transient UI state.
//
// Props:
//   text      : the file's text, or null while the caller is still
//               loading (renders a "Loading…" placeholder)
//   error     : optional error string → renders the error placeholder
//   filename  : used for the (informational) lang badge fallback
//   mime      : MIME used to pick the Prism grammar (callers without a
//               server MIME can derive one via mimeFromFilename(name))
//   byteSize  : optional, shown in the toolbar
//   truncated : show the "first N MB" banner
//
// Reused by CodePreview (gallery) and the archive viewer's inner-file
// preview, which feeds it text extracted from inside an archive.
export function CodeView({ text, error = null, filename, mime, byteSize, truncated = false }) {
  const [grammarReady, setGrammarReady] = useStateC(false);

  // ---- viewer UI state ----
  const [wrap, setWrap] = useStateC(() => {
    try { return localStorage.getItem(WRAP_KEY) === "1"; } catch { return false; }
  });
  const [copied, setCopied] = useStateC(false);
  const [findOpen, setFindOpen] = useStateC(false);
  const [query, setQuery] = useStateC("");
  const [debouncedQuery, setDebouncedQuery] = useStateC("");
  const [activeMatch, setActiveMatch] = useStateC(0);   // index into the flat match list
  const [activeLine, setActiveLine] = useStateC(null);  // 1-based, for current-line highlight
  // Log mode: which level buckets are hidden via the legend chips.
  const [hiddenLevels, setHiddenLevels] = useStateC(() => new Set());

  const rootRef = useRef(null);
  const bodyRef = useRef(null);
  const findInputRef = useRef(null);
  const copyTimer = useRef(null);

  // Log mode is decided from the filename (.log) or log-shaped content.
  // When on, we skip Prism and run the severity classifier instead.
  const logMode = useMemo(() => detectLogMode(text, filename), [text, filename]);

  const lang = useMemo(() => {
    if (logMode) return null;  // log mode owns the render — no grammar
    // Prefer an explicit MIME; if the caller only knows the filename,
    // fall back to deriving a MIME from its extension.
    const m = (mime && MIME_TO_LANG[mime]) ? mime : (mime || mimeFromFilename(filename));
    if (m && MIME_TO_LANG[m]) return MIME_TO_LANG[m];
    return null;  // unknown / plain text — monospace render, no grammar
  }, [mime, filename, logMode]);

  useEffect(() => {
    setGrammarReady(false);
    if (!lang) { setGrammarReady(true); return; }
    let alive = true;
    const loader = LANG_LOADERS[lang];
    if (!loader) { setGrammarReady(true); return; }
    loader().then(() => { if (alive) setGrammarReady(true); }).catch(() => { if (alive) setGrammarReady(true); });
    return () => { alive = false; };
  }, [lang]);

  // Reset transient UI when the file (text identity) changes.
  useEffect(() => {
    setQuery("");
    setDebouncedQuery("");
    setFindOpen(false);
    setActiveMatch(0);
    setActiveLine(null);
  }, [text]);

  // Take focus once the file is ready so Ctrl/⌘-F and "/" work straight
  // away — but never yank focus out from under an active selection or
  // an already-focused control inside the viewer.
  useEffect(() => {
    if (text == null || !rootRef.current) return;
    const active = document.activeElement;
    if (active && rootRef.current.contains(active)) return;
    rootRef.current.focus({ preventScroll: true });
  }, [text]);

  // The full highlighted HTML (one big string) from Prism, or null when
  // we fall back to plain monospace.
  const html = useMemo(() => {
    if (text == null) return null;
    if (!lang || !grammarReady || !Prism.languages[lang]) return null;
    try {
      return Prism.highlight(text, Prism.languages[lang], lang);
    } catch {
      return null;
    }
  }, [text, lang, grammarReady]);

  // Split source + highlighted HTML into per-line arrays once. Rendering
  // line-by-line keeps the line-number gutter sticky and confines the
  // horizontal scroll to the code column.
  const lines = useMemo(() => {
    if (text == null) return [];
    const arr = text.split("\n");
    // Drop a single trailing empty line produced by a final newline —
    // matches editor convention (a file ending in "\n" has N lines).
    if (arr.length > 1 && arr[arr.length - 1] === "") arr.pop();
    return arr;
  }, [text]);

  const htmlLines = useMemo(() => (html == null ? null : html.split("\n")), [html]);
  const lineCount = lines.length;

  // Per-line severity classification + bucket counts (log mode only).
  // `logInfo[i]` is { level, kw:[s,e] } or null; `logCounts` drives the
  // legend chips and the "2 errors · 1 warning" summary.
  const { logInfo, logCounts } = useMemo(() => {
    if (!logMode) return { logInfo: null, logCounts: null };
    const info = new Array(lines.length);
    const counts = { error: 0, warn: 0, info: 0, debug: 0 };
    for (let i = 0; i < lines.length; i++) {
      const c = classifyLogLine(lines[i]);
      info[i] = c;
      if (c) counts[c.level]++;
    }
    return { logInfo: info, logCounts: counts };
  }, [logMode, lines]);

  // Toggle a level's visibility (legend chip). Stored as the set of
  // HIDDEN buckets so the default (empty set) shows everything.
  const toggleLevel = useCallback((level) => {
    setHiddenLevels((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level); else next.add(level);
      return next;
    });
  }, []);

  // Reset level filters whenever the file changes.
  useEffect(() => { setHiddenLevels(new Set()); }, [text]);

  // ---- find-in-file: debounce the query so typing on a big file is smooth ----
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 120);
    return () => clearTimeout(t);
  }, [query]);

  // Compute per-line match ranges + a flat match list. Each flat match is
  // { line, start }; per-line ranges are [[start,end], …]. Capped at
  // MAX_MATCHES so a pathological query can't stall the render.
  const search = useMemo(() => {
    const q = debouncedQuery;
    if (!q || q.length < MIN_QUERY_LEN || !lines.length) {
      return { byLine: null, flat: [], capped: false };
    }
    const re = new RegExp(escapeRegExp(q), "gi");
    const byLine = new Array(lines.length);
    const flat = [];
    let total = 0;
    let capped = false;
    for (let li = 0; li < lines.length; li++) {
      const line = lines[li];
      let ranges = null;
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(line)) !== null) {
        if (m.index === re.lastIndex) re.lastIndex++;  // zero-width guard
        if (!ranges) ranges = [];
        const localIdx = flat.length;
        // store local match index as 3rd tuple slot so markRangesInHtml
        // can label the active hit without recomputing.
        ranges.push([m.index, m.index + q.length, localIdx]);
        flat.push({ line: li, start: m.index });
        total++;
        if (total >= MAX_MATCHES) { capped = true; break; }
      }
      if (ranges) byLine[li] = ranges;
      if (capped) break;
    }
    return { byLine, flat, capped };
  }, [debouncedQuery, lines]);

  const matchCount = search.flat.length;

  // Keep the active-match index in range whenever the result set changes.
  useEffect(() => {
    if (matchCount === 0) { setActiveMatch(0); return; }
    setActiveMatch((i) => (i >= matchCount ? 0 : i < 0 ? matchCount - 1 : i));
  }, [matchCount]);

  // Which flat-match index is "current" — used to brighten one hit.
  const activeFlat = matchCount ? search.flat[Math.min(activeMatch, matchCount - 1)] : null;

  // Scroll the active match into view when it (or the result set) changes.
  // Depend on primitives, not the freshly-built activeFlat object, so this
  // only fires on a real change of which match is current.
  const activeFlatLine = activeFlat ? activeFlat.line : -1;
  const activeFlatStart = activeFlat ? activeFlat.start : -1;
  useEffect(() => {
    if (activeFlatLine < 0 || !bodyRef.current) return;
    const el = bodyRef.current.querySelector(".cv-hit--current");
    if (el) el.scrollIntoView({ block: "center", inline: "nearest" });
  }, [activeFlatLine, activeFlatStart]);

  const gotoMatch = useCallback((delta) => {
    setActiveMatch((i) => {
      if (matchCount === 0) return 0;
      return (i + delta + matchCount) % matchCount;
    });
  }, [matchCount]);

  const openFind = useCallback(() => {
    setFindOpen(true);
    // focus + select after paint
    requestAnimationFrame(() => {
      const el = findInputRef.current;
      if (el) { el.focus(); el.select(); }
    });
  }, []);

  const closeFind = useCallback(() => {
    setFindOpen(false);
    setQuery("");
    setDebouncedQuery("");
  }, []);

  // ---- copy whole file ----
  const onCopy = useCallback(async () => {
    if (text == null) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for non-secure contexts.
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard denied — silently no-op */
    }
  }, [text]);

  useEffect(() => () => clearTimeout(copyTimer.current), []);

  // ---- word-wrap toggle (persisted) ----
  const toggleWrap = useCallback(() => {
    setWrap((w) => {
      const next = !w;
      try { localStorage.setItem(WRAP_KEY, next ? "1" : "0"); } catch {}
      return next;
    });
  }, []);

  // ---- line anchoring: clicking a gutter number lights the line and
  // writes a #Lnn hash so the view can be linked. On mount, honor an
  // existing #Lnn hash by scrolling to + highlighting it.
  const anchorLine = useCallback((n) => {
    setActiveLine(n);
    try {
      const base = (window.location.hash || "").replace(/#?L\d+$/i, "");
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${base}#L${n}`);
    } catch {}
  }, []);

  useLayoutEffect(() => {
    if (text == null) return;
    const m = /#L(\d+)/i.exec(window.location.hash || "");
    if (!m) return;
    const n = parseInt(m[1], 10);
    if (!n || n > lineCount) return;
    setActiveLine(n);
    requestAnimationFrame(() => {
      const row = bodyRef.current?.querySelector(`tr[data-line="${n}"]`);
      if (row) row.scrollIntoView({ block: "center" });
    });
  }, [text, lineCount]);

  // Escape handling lives on a window CAPTURE listener, not the React
  // tree. The host modal (preview.jsx) closes the code viewer on a
  // window-capture Escape; because CodePreview mounts as a child its
  // effects register first, so this capture handler runs before the
  // modal's. When Find is open we consume the Escape (close Find, keep
  // the modal open) via stopImmediatePropagation; otherwise we let the
  // event through so Escape still closes the viewer.
  useEffect(() => {
    if (!findOpen) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopImmediatePropagation();
        e.preventDefault();
        closeFind();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [findOpen, closeFind]);

  // ---- keyboard: Ctrl/Cmd-F or "/" opens find ----
  const onKeyDown = useCallback((e) => {
    const meta = e.ctrlKey || e.metaKey;
    if (meta && (e.key === "f" || e.key === "F")) {
      e.preventDefault();
      openFind();
      return;
    }
    // "/" opens find, but only when not already typing into a field.
    if (e.key === "/" && !findOpen) {
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag !== "input" && tag !== "textarea") {
        e.preventDefault();
        openFind();
      }
    }
  }, [findOpen, openFind]);

  const onFindKeyDown = useCallback((e) => {
    // Enter / Shift+Enter cycle matches. Escape is handled by the
    // window-capture listener above so it can also block the modal close.
    if (e.key === "Enter") {
      e.preventDefault();
      gotoMatch(e.shiftKey ? -1 : 1);
    }
  }, [gotoMatch]);

  if (error) {
    return (
      <div className="code-preview code-preview--msg code-preview--err">
        Could not load file: {error}
      </div>
    );
  }
  if (text == null) {
    return (
      <div className="code-preview code-preview--msg">
        Loading…
      </div>
    );
  }
  if (text === "") {
    return (
      <div className="code-preview code-preview--msg code-preview--empty">
        This file is empty.
      </div>
    );
  }

  const wrapMode = wrap ? "wrap" : "nowrap";

  // Legend chips: only buckets that actually appear in this log, in
  // severity order. Each carries its count + a singular/plural label.
  const legend = logCounts
    ? LOG_LEVEL_META
        .filter((m) => logCounts[m.key] > 0)
        .map((m) => ({ ...m, count: logCounts[m.key] }))
    : [];

  return (
    <div
      className="code-preview"
      data-wrap={wrapMode}
      data-logmode={logMode ? "true" : undefined}
      tabIndex={-1}
      ref={rootRef}
      onKeyDown={onKeyDown}
    >
      {/* ---- header toolbar ---- */}
      <div className="code-preview__toolbar">
        <div className="code-preview__meta">
          {logMode
            ? <span className="code-preview__lang code-preview__lang--log">LOG</span>
            : (lang && <span className="code-preview__lang">{lang}</span>)}
          <span className="code-preview__metaitem">{lineCount.toLocaleString()} lines</span>
          {byteSize != null && <span className="code-preview__metaitem">{fmtBytes(byteSize)}</span>}
          {legend.length > 0 && (
            <span className="code-loglegend" role="group" aria-label="Filter log levels">
              {legend.map((m) => {
                const off = hiddenLevels.has(m.key);
                return (
                  <button
                    key={m.key}
                    type="button"
                    className="code-loglegend__chip"
                    data-level={m.key}
                    data-off={off ? "true" : undefined}
                    aria-pressed={!off}
                    title={off ? `Show ${m.many}` : `Hide ${m.many}`}
                    onClick={() => toggleLevel(m.key)}
                  >
                    <span className="code-loglegend__dot" aria-hidden="true" />
                    <span className="code-loglegend__label">{m.label}</span>
                    <span className="code-loglegend__count">{m.count.toLocaleString()}</span>
                  </button>
                );
              })}
            </span>
          )}
        </div>

        <div className="code-preview__actions">
          {findOpen && (
            <div className="code-find" role="search">
              <span className="code-find__icon" aria-hidden="true">
                <Icon name="search" size={13} />
              </span>
              <input
                ref={findInputRef}
                className="code-find__input"
                type="text"
                value={query}
                placeholder="Find"
                spellCheck={false}
                autoComplete="off"
                aria-label="Find in file"
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onFindKeyDown}
              />
              <span className="code-find__count" aria-live="polite">
                {debouncedQuery
                  ? (matchCount
                      ? `${Math.min(activeMatch + 1, matchCount)} of ${matchCount}${search.capped ? "+" : ""}`
                      : "0")
                  : ""}
              </span>
              <button
                type="button"
                className="code-find__nav"
                title="Previous match (Shift+Enter)"
                aria-label="Previous match"
                disabled={!matchCount}
                onClick={() => gotoMatch(-1)}
              >
                <Icon name="chevronUp" size={14} />
              </button>
              <button
                type="button"
                className="code-find__nav"
                title="Next match (Enter)"
                aria-label="Next match"
                disabled={!matchCount}
                onClick={() => gotoMatch(1)}
              >
                <Icon name="chevronDown" size={14} />
              </button>
              <button
                type="button"
                className="code-find__nav"
                title="Close (Esc)"
                aria-label="Close find"
                onClick={closeFind}
              >
                <Icon name="x" size={14} />
              </button>
            </div>
          )}

          {!findOpen && (
            <button
              type="button"
              className="code-tool"
              title="Find in file (Ctrl/⌘+F)"
              aria-label="Find in file"
              onClick={openFind}
            >
              <Icon name="search" size={14} />
            </button>
          )}

          <button
            type="button"
            className="code-tool"
            data-active={wrap ? "true" : "false"}
            title={wrap ? "Disable word wrap" : "Enable word wrap"}
            aria-label="Toggle word wrap"
            aria-pressed={wrap}
            onClick={toggleWrap}
          >
            <WrapGlyph />
          </button>

          <button
            type="button"
            className="code-tool"
            data-ok={copied ? "true" : "false"}
            title="Copy file"
            aria-label="Copy file contents"
            onClick={onCopy}
          >
            <Icon name={copied ? "check" : "copy"} size={14} />
            <span className="code-tool__label">{copied ? "Copied" : "Copy"}</span>
          </button>
        </div>
      </div>

      {truncated && (
        <div className="code-preview__banner">
          Showing the first {fmtBytes(RENDER_BYTE_CAP)} of a larger file. Download the original to see the rest.
        </div>
      )}

      <div className="code-preview__body" ref={bodyRef}>
        <table className="code-preview__table">
          <tbody>
            {lines.map((line, i) => {
              const n = i + 1;
              const ranges = search.byLine ? search.byLine[i] : null;
              const info = logInfo ? logInfo[i] : null;
              const level = info ? info.level : null;
              // A level chip can hide its lines. We render the row but
              // collapse it (CSS) so line numbers stay accurate.
              const hidden = level != null && hiddenLevels.has(level);
              let cellHtml;
              if (logMode) {
                cellHtml = renderLogLineHtml(line, info, ranges, activeMatch);
              } else if (htmlLines) {
                const base = htmlLines[i] != null && htmlLines[i].length
                  ? htmlLines[i]
                  : "&nbsp;";
                cellHtml = ranges
                  ? markRangesInHtml(base, ranges, 0, activeMatch)
                  : base;
              } else {
                // Plain (no grammar) path — escape then optionally mark.
                const esc = line.length ? escapeHtml(line) : "&nbsp;";
                cellHtml = ranges
                  ? markRangesInHtml(esc, ranges, 0, activeMatch)
                  : esc;
              }
              const cls = [
                activeLine === n ? "is-active" : null,
                hidden ? "is-filtered" : null,
              ].filter(Boolean).join(" ") || undefined;
              return (
                <tr
                  key={i}
                  data-line={n}
                  data-level={level || undefined}
                  className={cls}
                >
                  <td
                    className="code-preview__ln"
                    onClick={() => anchorLine(n)}
                    title={`Line ${n}`}
                  >
                    {n}
                  </td>
                  <td className="code-preview__code">
                    <span dangerouslySetInnerHTML={{ __html: cellHtml }} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Small "word wrap" glyph — an arrow that turns back on itself. Drawn
// inline (no matching entry in icons.jsx) but matched to the 1.6-weight
// outline house style.
function WrapGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 7h16" />
      <path d="M4 12h13a3 3 0 0 1 0 6h-4" />
      <path d="m12 15-2 3 2 3" transform="translate(0 -3)" />
      <path d="M4 17h5" />
    </svg>
  );
}
