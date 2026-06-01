// Monochrome, single-color, LINE-style per-language glyphs.
//
// Deliberately NOT colorful brand logos — these match the minimalist
// outline set in icons.jsx (24x24 viewBox, fill:none, stroke:currentColor,
// strokeWidth ~1.3, round caps/joins). The user explicitly chose thin
// monochrome line marks over brand colors so code files in the gallery
// read as one cohesive icon family rather than a sticker sheet.
//
// Each glyph is an abstract-but-distinct line motif evoking the language
// (Python's two stacked loops, Rust's gear ring, shell's `>_` prompt,
// braces for the C family, a database cylinder for SQL, etc.) — visually
// separable at 24–42px while staying on-brand.
//
// Public API:
//   <LanguageIcon name="python" size={32} strokeWidth={1.3} .../>
//       renders the glyph by its catalog name (self-contained <svg>,
//       same props surface as icons.jsx's <Icon/>).
//   languageIcon(ext)  -> resolves a file extension (or language token)
//       to the right glyph name, with a sensible generic-code fallback
//       ("code"). Mirrors fileTypeInfo(ext) ergonomics: tolerant of a
//       leading dot, mixed case, or a bare token.
//
// Wiring is intentionally left to the spine files (gallery.jsx /
// preview.jsx) — see the returned snippet in the task report.
import React from "react";

// Each value is the inner SVG markup for one 24x24 glyph. Keep every
// path inside the 2..22 safe area so nothing clips at small sizes, and
// rely on the shared <svg> wrapper below for stroke/fill so individual
// glyphs never re-declare color.
const GLYPHS = {
  // ---- Generic fallback: angle brackets (same motif as icons.jsx `code`).
  code: <><polyline points="8 6 2 12 8 18"/><polyline points="16 6 22 12 16 18"/></>,

  // ---- Python: two interlocked rounded loops (the classic two-snake
  // silhouette reduced to line form) with the two "eye" dots.
  python: <><path d="M12 3c-3 0-4 1.4-4 3.2V9h4.2"/><path d="M8 9H6.5C4.6 9 3.5 10.2 3.5 12.4 3.5 14.6 4.6 15 6 15h2"/><path d="M12 21c3 0 4-1.4 4-3.2V15h-4.2"/><path d="M16 15h1.5c1.9 0 3-1.2 3-3.4C20.5 9.4 19.4 9 18 9h-2"/><path d="M10 6.2h.01"/><path d="M14 17.8h.01"/></>,

  // ---- JavaScript: a square frame with "JS" implied by a hook + a
  // dropping stroke — abstract, reads as a script badge.
  javascript: <><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 9v5a1.5 1.5 0 0 1-3 0"/><path d="M17 10.5a1.6 1.6 0 0 0-3 0c0 2 3 1.4 3 3.4a1.7 1.7 0 0 1-3.2.6"/></>,

  // ---- TypeScript: same script frame, a bold "T" bar to separate it.
  typescript: <><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M6.5 10.5h5"/><path d="M9 10.5V17"/><path d="M18 10.8a1.6 1.6 0 0 0-3 0c0 2 3 1.4 3 3.4a1.7 1.7 0 0 1-3.2.6"/></>,

  // ---- JSX / TSX: angle-bracket "tag" wrapping an atom orbit (React-ish
  // without being the literal logo).
  jsx: <><path d="M6 8 2.5 12 6 16"/><path d="M18 8l3.5 4L18 16"/><ellipse cx="12" cy="12" rx="4.5" ry="2" transform="rotate(60 12 12)"/><circle cx="12" cy="12" r="1"/></>,
  tsx: <><path d="M6 8 2.5 12 6 16"/><path d="M18 8l3.5 4L18 16"/><ellipse cx="12" cy="12" rx="4.5" ry="2" transform="rotate(-60 12 12)"/><circle cx="12" cy="12" r="1"/></>,

  // ---- Go: a simple speed/gopher-ear motif — two round whiskers and an
  // eye, very minimal.
  go: <><circle cx="9" cy="11" r="6"/><circle cx="9" cy="10" r="1"/><path d="M15 9h6"/><path d="M15 13h5"/></>,

  // ---- Rust: a gear ring (cog) — Rust's emblem reduced to a thin gear.
  rust: <><circle cx="12" cy="12" r="4"/><path d="M12 3v2"/><path d="M12 19v2"/><path d="M3 12h2"/><path d="M19 12h2"/><path d="m5.6 5.6 1.4 1.4"/><path d="m17 17 1.4 1.4"/><path d="m18.4 5.6-1.4 1.4"/><path d="m7 17-1.4 1.4"/></>,

  // ---- Java: a steaming cup of coffee.
  java: <><path d="M5 10h12v4a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4z"/><path d="M17 11h1.5a2 2 0 0 1 0 4H17"/><path d="M9 3c-.8 1 .8 2 0 3"/><path d="M13 3c-.8 1 .8 2 0 3"/></>,

  // ---- Kotlin: two stacked triangles meeting at a corner (the K wedge).
  kotlin: <><path d="M4 4h16L4 12z"/><path d="M4 12 20 20H4z"/></>,

  // ---- Swift: a swift bird in flight — single swooping stroke.
  swift: <><path d="M4 7c4 1 7 3 9 6"/><path d="M20 17c-2-6-7-10-13-12 5 0 9 1.5 13 5.5"/><path d="M20 17c-2 1.5-5 1.5-8-.5"/></>,

  // ---- C: a single open ring (the letter C).
  c: <><path d="M17 7.5A7 7 0 1 0 17 16.5"/></>,

  // ---- C++: the C ring plus two plus signs.
  cpp: <><path d="M12 7.5A6 6 0 1 0 12 16.5"/><path d="M16 10v4"/><path d="M14 12h4"/><path d="M20.5 10v4"/><path d="M18.5 12h4"/></>,

  // ---- C#: the C ring plus a sharp (#).
  csharp: <><path d="M12.5 7.5A6 6 0 1 0 12.5 16.5"/><path d="M16 9l-1 6"/><path d="M19 9l-1 6"/><path d="M14.5 11h5.5"/><path d="M14 13.5h5.5"/></>,

  // ---- Ruby: a cut gem (diamond) with facet lines.
  ruby: <><path d="M7 4h10l4 5-9 11L3 9z"/><path d="m3 9 9 2 9-2"/><path d="m12 11-3-7"/><path d="m12 11 3-7"/></>,

  // ---- PHP: an oval (elephant body) with a small "p" tail.
  php: <><ellipse cx="12" cy="12" rx="9" ry="5.5"/><path d="M8 10v6"/><path d="M8 10h2a1.5 1.5 0 0 1 0 3H8"/><path d="M14.5 9v6"/><path d="M14.5 9h1.8a1.4 1.4 0 0 1 0 2.8h-1.8"/></>,

  // ---- HTML: angle bracket tag with a slash (markup).
  html: <><path d="M5 4 3 12l2 8"/><path d="M19 4l2 8-2 8"/><path d="m14 7-4 10"/></>,

  // ---- CSS: a paint-bucket-ish hash with a sweep (styling).
  css: <><path d="M5 4 6.2 18 12 20l5.8-2L19 4z"/><path d="M8 8h8l-.5 4.5L12 14l-3.5-1"/></>,

  // ---- SCSS / Sass: the CSS frame plus a small swirl.
  scss: <><path d="M5 4 6.2 18 12 20l5.8-2L19 4z"/><path d="M14 9c-2-1-4 0-4 1.6 0 2 4 1.4 4 3.4 0 1.4-2 2.2-4 1.2"/></>,
  sass: <><path d="M4 14c2-3 6-4 9-3"/><path d="M16 8c-3-1-6 .5-6 3 0 3 6 2 6 5 0 2-3 3-6 1.5"/></>,

  // ---- JSON: braces with a key/value dot pair.
  json: <><path d="M8 4C6 4 6 7 6 9s-2 2-2 3 2 1 2 3 0 5 2 5"/><path d="M16 4c2 0 2 3 2 5s2 2 2 3-2 1-2 3 0 5-2 5"/><circle cx="12" cy="12" r="0.6"/></>,

  // ---- YAML: a key + colon + indented value lines (list-ish).
  yaml: <><path d="M4 6h6"/><path d="M12 6h2"/><path d="M8 11h6"/><path d="M16 11h2"/><path d="M8 16h4"/><path d="M14 16h2"/><path d="M4 6v12"/></>,

  // ---- TOML: a wrench + key=value (config).
  toml: <><path d="M5 7h5"/><path d="M12 7h7"/><path d="M5 12h3"/><path d="M10 12h9"/><path d="M5 17h6"/><path d="M13 17h6"/></>,

  // ---- Markdown: the "M↓" arrow inside a rounded rect.
  markdown: <><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M6 15V9l3 3 3-3v6"/><path d="M16 9v4"/><path d="m14 11 2 2 2-2"/></>,

  // ---- SQL: a database cylinder (also reused as the generic db code icon).
  sql: <><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.6 3.1 3 7 3s7-1.4 7-3V6"/><path d="M5 12c0 1.6 3.1 3 7 3s7-1.4 7-3"/></>,

  // ---- Shell: a terminal prompt `>_` in a window.
  shell: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 10 3 2-3 2"/><path d="M12 14h5"/></>,

  // ---- PowerShell: prompt with a chevron + underscore, bolder angle.
  powershell: <><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m6 8 5 4-5 4"/><path d="M12 16h6"/></>,

  // ---- Lua: the moon crescent (Lua = moon).
  lua: <><path d="M16 4a8 8 0 1 0 4 12A6.5 6.5 0 0 1 16 4z"/><circle cx="17.5" cy="7.5" r="1.3"/></>,

  // ---- R: statistics — a bell curve over an axis.
  r: <><path d="M3 18h18"/><path d="M4 18c3 0 4-10 8-10s5 10 8 10"/></>,

  // ---- Perl: an onion/pearl drop with a shine.
  perl: <><path d="M12 4c-3 4-5 6-5 9a5 5 0 0 0 10 0c0-3-2-5-5-9z"/><path d="M10 14a2 2 0 0 0 2 2"/></>,

  // ---- Go-style brace fallback for generic C-family handled by `code`.

  // ---- Haskell: the lambda + arrows (>>=) abstractly: a lambda wedge.
  haskell: <><path d="M4 5l6 7-6 7"/><path d="M9 5l5 7-5 7"/><path d="M14 12h7"/><path d="M16 9h5"/></>,

  // ---- Elixir: a teardrop / droplet.
  elixir: <><path d="M13 3c-1 4-6 5-6 10a6 6 0 0 0 12 0c0-3-3-4-3-7"/><path d="M10 13a3 3 0 0 0 3 3"/></>,

  // ---- Erlang: telecom — concentric signal arcs from a node.
  erlang: <><circle cx="7" cy="12" r="1.5"/><path d="M11 8a6 6 0 0 1 0 8"/><path d="M14 5a10 10 0 0 1 0 14"/></>,

  // ---- Clojure / Lisp: parens with a center dot (the (λ)).
  clojure: <><path d="M9 5a9 9 0 0 0 0 14"/><path d="M15 5a9 9 0 0 1 0 14"/><circle cx="12" cy="12" r="1.4"/></>,
  lisp: <><path d="M8 6a8 8 0 0 0 0 12"/><path d="M16 6a8 8 0 0 1 0 12"/><circle cx="12" cy="12" r="1"/></>,
  scheme: <><path d="M8 6a8 8 0 0 0 0 12"/><path d="M16 6a8 8 0 0 1 0 12"/><path d="M10.5 12h3"/></>,

  // ---- OCaml / camel: a simple camel hump line.
  ocaml: <><path d="M3 17c0-3 2-3 2-6 1 1 2 1 3 0 1 3 2 2 3 4 1-2 2-1 3-4 1 1 2 1 3 0 0 3 2 3 2 6"/><path d="M3 17h18"/></>,

  // ---- F#: forward slash + sharp.
  fsharp: <><path d="M7 20 17 4"/><path d="M9 9h6"/><path d="M8 14h6"/></>,

  // ---- Scala: two stacked slashes (the Scala "stairs").
  scala: <><path d="M7 5l10-1v3L7 8z"/><path d="M7 10.5l10-1v3l-10 1z"/><path d="M7 16l10-1v3l-10 1z"/></>,

  // ---- Dart: a target dart (bullseye with arrow).
  dart: <><circle cx="11" cy="13" r="6"/><circle cx="11" cy="13" r="2"/><path d="m16 8 5-5"/><path d="M18 3h3v3"/></>,

  // ---- Nim: a crown (Nim's emblem) reduced to a 3-point line crown.
  nim: <><path d="M4 17h16"/><path d="M4 17 6 8l4 4 2-6 2 6 4-4 2 9"/></>,

  // ---- Zig: a lightning bolt / Z.
  zig: <><path d="M5 6h13l-9 5h9l-13 7 6-7H6z"/></>,

  // ---- Crystal: a faceted crystal shard.
  crystal: <><path d="M12 3 5 9l7 12 7-12z"/><path d="M5 9h14"/><path d="m12 3v18"/></>,

  // ---- Groovy: a sound/groove wave.
  groovy: <><path d="M3 12c2-6 3-6 5 0s3 6 5 0 3-6 5 0"/><path d="M19 12c.5 1.5 1 1.5 2 0"/></>,

  // ---- Julia: three dots (the three Julia circles) in a triangle.
  julia: <><circle cx="8" cy="15" r="2.4"/><circle cx="16" cy="15" r="2.4"/><circle cx="12" cy="8" r="2.4"/></>,

  // ---- Solidity: an angular ETH-style diamond (two stacked triangles).
  solidity: <><path d="M12 3 6 11l6 3 6-3z"/><path d="m6 13 6 8 6-8-6 3z"/></>,

  // ---- HCL / Terraform: stacked parallelogram blocks (infra).
  hcl: <><path d="m4 8 5-2v4l-5 2z"/><path d="m11 6 5-2v4l-5 2z"/><path d="M11 11l5-2v4l-5 2z"/><path d="M11 16l5-2v4l-5 2z"/></>,

  // ---- Nix: a snowflake / lambda-flake (six arms).
  nix: <><path d="M12 3v18"/><path d="m4.5 7.5 15 9"/><path d="m19.5 7.5-15 9"/><path d="M12 8 9 6.5"/><path d="m12 16 3 1.5"/></>,

  // ---- Assembly: chip with pins.
  asm: <><rect x="7" y="7" width="10" height="10" rx="1"/><path d="M10 7V4"/><path d="M14 7V4"/><path d="M10 20v-3"/><path d="M14 20v-3"/><path d="M7 10H4"/><path d="M7 14H4"/><path d="M20 10h-3"/><path d="M20 14h-3"/></>,

  // ---- WebAssembly: stacked "W" strokes in a rounded square.
  wasm: <><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m6 9 1.5 6L9 11l1.5 4L12 9"/><path d="m13 9 1.5 6L16 11l1.5 4L19 9"/></>,

  // ---- Vim: the green diamond reduced to a thin rhombus + cursor block.
  vim: <><path d="M12 3 5 10v4l7 7 7-7v-4z"/><path d="M9 11h3v3"/></>,

  // ---- Docker / container: a shipping container with a wave.
  docker: <><rect x="3" y="9" width="14" height="6" rx="1"/><path d="M6 9V7"/><path d="M9 9V7"/><path d="M12 9V7"/><path d="M17 12c2 .5 3-.5 4-1.5"/><path d="M3 17c3 1 12 1 14-2"/></>,

  // ---- Makefile / build: a hammer + brace.
  makefile: <><path d="m14 7 3 3-7 7-3-3z"/><path d="m17 4 3 3-2 2-3-3z"/><path d="m9 13-5 5 1 1 5-5"/></>,

  // ---- XML: angle brackets with a slash (markup variant, distinct from html).
  xml: <><path d="m7 8-4 4 4 4"/><path d="m17 8 4 4-4 4"/><path d="m13 6-2 12"/></>,

  // ---- Diff / patch: plus over minus.
  diff: <><path d="M5 8h6"/><path d="M8 5v6"/><path d="M5 17h14"/><path d="M14 8h5"/></>,

  // ---- GraphQL: a hexagon graph with nodes.
  graphql: <><path d="M12 3 20 7.5v9L12 21 4 16.5v-9z"/><circle cx="12" cy="4" r="1"/><circle cx="20" cy="16" r="1"/><circle cx="4" cy="16" r="1"/><path d="M12 4 4 16h16z"/></>,

  // ---- Protobuf: nested brackets / schema.
  protobuf: <><path d="M9 4H6a2 2 0 0 0-2 2v3"/><path d="M15 4h3a2 2 0 0 1 2 2v3"/><path d="M9 20H6a2 2 0 0 1-2-2v-3"/><path d="M15 20h3a2 2 0 0 0 2-2v-3"/><path d="M9 12h6"/></>,

  // ---- LaTeX / TeX: the lowered-E "TeX" baseline mark.
  latex: <><path d="M5 7h7"/><path d="M8.5 7v10"/><path d="M14 9l5 6"/><path d="M19 9l-5 6"/></>,

  // ---- Vue: the V chevron (no brand green, just the wedge).
  vue: <><path d="M3 5h4l5 9 5-9h4l-9 15z"/><path d="M8 5l4 7 4-7"/></>,

  // ---- Svelte: a curved swoosh "S".
  svelte: <><path d="M15 6c-2-1.5-5-1-6.5 1.2-1.4 2-0.6 3.8 1.5 5"/><path d="M9 18c2 1.5 5 1 6.5-1.2 1.4-2 .6-3.8-1.5-5"/></>,

  // ---- Objective-C / generic: parens + C ring kept under `c`.
  objectivec: <><path d="M14 7.5A6 6 0 1 0 14 16.5"/><path d="M16 9l1.5 6"/><path d="M20 9l-1.5 6"/></>,

  // ---- Haxe: an X formed by two strokes inside a rounded square.
  haxe: <><rect x="4" y="4" width="16" height="16" rx="2"/><path d="m9 9 6 6"/><path d="m15 9-6 6"/></>,

  // ---- Vala / generic-templated: a checkmark-in-gear (Vala = GNOME).
  vala: <><circle cx="12" cy="12" r="7"/><path d="m9 12 2 2 4-4"/></>,

  // ---- Fortran / scientific: a sigma summation.
  fortran: <><path d="M17 5H7l5 7-5 7h10"/></>,

  // ---- COBOL / legacy: stacked punch-card rows.
  cobol: <><rect x="4" y="6" width="16" height="12" rx="1"/><path d="M7 9h2"/><path d="M11 9h2"/><path d="M15 9h2"/><path d="M7 13h6"/></>,

  // ---- Pascal: a triangle (Pascal's triangle hint) of dots.
  pascal: <><path d="M12 4 5 18h14z"/><circle cx="12" cy="8" r="0.6"/><circle cx="10" cy="12" r="0.6"/><circle cx="14" cy="12" r="0.6"/><circle cx="8" cy="16" r="0.6"/><circle cx="16" cy="16" r="0.6"/></>,

  // ---- D: the letter D ring with a bar.
  d: <><path d="M7 5v14"/><path d="M7 5h4a7 7 0 0 1 0 14H7"/></>,

  // ---- Dotenv / config: a key + dots (secrets).
  dotenv: <><circle cx="8" cy="12" r="3"/><path d="M11 12h9"/><path d="M16 12v3"/><path d="M19 12v2"/><path d="M7 11.5h.01"/></>,

  // ---- Properties / ini: a slider/toggle row stack.
  ini: <><path d="M4 7h10"/><path d="M16 7h4"/><circle cx="14" cy="7" r="1.6"/><path d="M4 12h4"/><path d="M10 12h10"/><circle cx="8" cy="12" r="1.6"/><path d="M4 17h10"/><path d="M16 17h4"/><circle cx="14" cy="17" r="1.6"/></>,

  // ---- Jupyter notebook: a stacked cell + run dot.
  notebook: <><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8"/><path d="m8 12 2 2-2 2"/><path d="M13 16h3"/></>,

  // ---- reStructuredText / docs: a document with a tilde-rule heading.
  rst: <><path d="M14 3v4a1 1 0 0 0 1 1h4"/><path d="M6 21V5a2 2 0 0 1 2-2h6l5 5v13a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2z"/><path d="M9 12h6"/><path d="M9 15h4"/></>,
};

// Resolver: extension/token → glyph name. Mirrors fileTypeInfo's input
// tolerance (leading dot, mixed case). Returns the glyph name; unknown
// or non-code extensions resolve to the generic "code" mark so callers
// can render unconditionally without their own fallback.
const EXT_TO_GLYPH = {
  // Python
  py: "python", pyi: "python", pyw: "python", ipynb: "notebook",
  // JS / TS family
  js: "javascript", mjs: "javascript", cjs: "javascript",
  ts: "typescript", mts: "typescript", cts: "typescript",
  jsx: "jsx", tsx: "tsx",
  coffee: "javascript",
  // Web
  html: "html", htm: "html",
  css: "css", scss: "scss", sass: "sass", less: "scss",
  vue: "vue", svelte: "svelte", astro: "html",
  pug: "html", jade: "html", haml: "html", slim: "html",
  // Systems
  c: "c", h: "c",
  cpp: "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp", hxx: "cpp", "c++": "cpp",
  cs: "csharp",
  rs: "rust",
  go: "go",
  zig: "zig",
  nim: "nim", nims: "nim",
  d: "d",
  v: "code",
  odin: "code",
  asm: "asm", s: "asm",
  wat: "wasm", wasm: "wasm",
  // JVM
  java: "java", kt: "kotlin", kts: "kotlin", scala: "scala",
  groovy: "groovy", gradle: "groovy", clj: "clojure", cljs: "clojure",
  cljc: "clojure", edn: "clojure",
  // Apple / mobile
  swift: "swift", m: "objectivec", mm: "objectivec", dart: "dart",
  // Scripting
  rb: "ruby", php: "php", lua: "lua", perl: "perl", pl: "perl", pm: "perl",
  r: "r", tcl: "code", vim: "vim", awk: "code", sed: "code",
  // Shell
  sh: "shell", bash: "shell", zsh: "shell", fish: "shell", ksh: "shell",
  ps1: "powershell", psm1: "powershell", psd1: "powershell",
  bat: "shell", cmd: "shell",
  // Functional
  hs: "haskell", lhs: "haskell",
  ml: "ocaml", mli: "ocaml",
  ex: "elixir", exs: "elixir",
  erl: "erlang", hrl: "erlang",
  elm: "haskell",
  fs: "fsharp", fsi: "fsharp", fsx: "fsharp",
  lisp: "lisp", lsp: "lisp", el: "lisp", scm: "scheme", ss: "scheme",
  rkt: "scheme", fnl: "lisp",
  purs: "haskell", re: "ocaml", rei: "ocaml", gleam: "code",
  cr: "crystal", jl: "julia", vala: "vala", hx: "haxe",
  // Scientific / legacy
  f: "fortran", f90: "fortran", f95: "fortran", for: "fortran",
  cob: "cobol", cbl: "cobol",
  pas: "pascal", pp: "pascal",
  // Blockchain
  sol: "solidity",
  // Data / config
  json: "json", json5: "json", jsonc: "json",
  yaml: "yaml", yml: "yaml",
  toml: "toml",
  xml: "xml", xsd: "xml", xsl: "xml", xslt: "xml", plist: "xml",
  md: "markdown", markdown: "markdown", mdx: "markdown", rst: "rst",
  tex: "latex", latex: "latex",
  ini: "ini", cfg: "ini", conf: "ini", properties: "ini",
  env: "dotenv",
  sql: "sql", psql: "sql", plsql: "sql", cql: "sql",
  graphql: "graphql", gql: "graphql",
  proto: "protobuf",
  diff: "diff", patch: "diff",
  // Infra
  hcl: "hcl", tf: "hcl", tfvars: "hcl",
  nix: "nix",
  dockerfile: "docker",
  makefile: "makefile", mk: "makefile", cmake: "asm",
};

// Glyph names that exist in GLYPHS — used to guard the resolver so a
// stale mapping can never point at a missing glyph.
const HAS_GLYPH = (n) => Object.prototype.hasOwnProperty.call(GLYPHS, n);

/**
 * Resolve a file extension (or bare language token) to a language glyph
 * name. Tolerant of a leading dot and mixed case. Falls back to the
 * generic "code" angle-bracket mark for anything unmapped, so callers
 * can render unconditionally.
 *
 *   languageIcon("py")     -> "python"
 *   languageIcon(".RS")    -> "rust"
 *   languageIcon("widget") -> "code"
 */
export function languageIcon(ext) {
  const k = (ext || "").toString().toLowerCase().replace(/^\./, "").trim();
  if (!k) return "code";
  // Direct extension hit.
  const mapped = EXT_TO_GLYPH[k];
  if (mapped && HAS_GLYPH(mapped)) return mapped;
  // The token might already be a glyph name (e.g. a `text/x-<lang>` tail
  // passed straight through): accept it if we have that glyph.
  if (HAS_GLYPH(k)) return k;
  return "code";
}

/**
 * Render a language glyph by name. Self-contained <svg> with the same
 * prop surface as icons.jsx's <Icon/> (size, strokeWidth, ...rest), so
 * it drops into the gallery card / preview hero exactly where <Icon/>
 * used to sit. Unknown names render the generic "code" mark.
 */
export const LanguageIcon = ({ name, size = 24, strokeWidth = 1.3, ...props }) => {
  const glyph = (name && GLYPHS[name]) || GLYPHS.code;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {glyph}
    </svg>
  );
};

// Names of every glyph this module provides — handy for tests / a future
// icon gallery. Not load-bearing for rendering.
export const LANGUAGE_GLYPH_NAMES = Object.keys(GLYPHS);
