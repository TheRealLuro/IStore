// File-type catalog. Maps a normalized extension to:
//   icon  : an Icon name from icons.jsx (the glyph shown on the
//           gallery card / preview hero when there's no raster
//           thumbnail);
//   label : a 3-5 letter label drawn underneath the icon;
//   kind  : a coarse "what kind of viewer should open this" tag
//           used by preview.jsx to pick between video / audio /
//           csv / ics / vcf / pdf / code / image / generic.
//
// The catalog is intentionally explicit instead of inferred from
// MIME — extensions are stable across our upload pipeline and let
// us cover formats whose MIME the browser sometimes gets wrong
// (`.ics` → `text/calendar` vs `text/plain`, `.heic` →
// `application/octet-stream` on Chrome, etc.).
const T = (icon, label, kind) => ({ icon, label, kind });

const CATALOG = {
  // images — handled by the existing lightbox path, listed here so the
  // mini-preview can fall back to a clean glyph if the thumb fails.
  jpg:  T("image", "JPG",  "image"),
  jpeg: T("image", "JPG",  "image"),
  png:  T("image", "PNG",  "image"),
  gif:  T("image", "GIF",  "image"),
  webp: T("image", "WEBP", "image"),
  bmp:  T("image", "BMP",  "image"),
  tif:  T("image", "TIF",  "image"),
  tiff: T("image", "TIFF", "image"),
  heic: T("image", "HEIC", "image"),
  heif: T("image", "HEIF", "image"),
  svg:  T("image", "SVG",  "image"),
  avif: T("image", "AVIF", "image"),

  // video — handled by the new VideoPlayer.
  mp4:  T("video", "MP4",  "video"),
  mov:  T("video", "MOV",  "video"),
  webm: T("video", "WEBM", "video"),
  mkv:  T("video", "MKV",  "video"),
  avi:  T("video", "AVI",  "video"),
  m4v:  T("video", "M4V",  "video"),

  // audio — handled by the new AudioPlayer.
  mp3:  T("music", "MP3",  "audio"),
  wav:  T("music", "WAV",  "audio"),
  flac: T("music", "FLAC", "audio"),
  ogg:  T("music", "OGG",  "audio"),
  m4a:  T("music", "M4A",  "audio"),
  aac:  T("music", "AAC",  "audio"),
  opus: T("music", "OPUS", "audio"),

  // documents — pdf opens in PdfPageStack; office formats fall back
  // to the generic doc icon for now (preview is the icon + download).
  pdf:  T("document", "PDF", "pdf"),
  doc:  T("document", "DOC", "doc"),
  docx: T("document", "DOCX","doc"),
  odt:  T("document", "ODT", "doc"),
  rtf:  T("document", "RTF", "doc"),
  txt:  T("document", "TXT", "code"),
  md:   T("document", "MD",  "markdown"),
  markdown: T("document", "MARKDOWN", "markdown"),
  epub: T("book", "EPUB", "ebook"),

  // notebooks — Jupyter .ipynb opens in the NotebookViewer.
  ipynb: T("code", "IPYNB", "notebook"),

  // 3D models — opened by Model3dViewer (three.js WebGL preview).
  stl:  T("cube", "STL",  "model3d"),
  obj:  T("cube", "OBJ",  "model3d"),
  gltf: T("cube", "GLTF", "model3d"),
  glb:  T("cube", "GLB",  "model3d"),

  // fonts — opened by FontViewer (runtime FontFace specimen).
  ttf:  T("type", "TTF",   "font"),
  otf:  T("type", "OTF",   "font"),
  woff: T("type", "WOFF",  "font"),
  woff2:T("type", "WOFF2", "font"),

  // spreadsheets — csv opens in the new CsvViewer; xlsx is generic.
  csv:  T("spreadsheet", "CSV",  "csv"),
  tsv:  T("spreadsheet", "TSV",  "csv"),
  xls:  T("spreadsheet", "XLS",  "spreadsheet"),
  xlsx: T("spreadsheet", "XLSX", "spreadsheet"),
  ods:  T("spreadsheet", "ODS",  "spreadsheet"),

  // slides.
  ppt:  T("slides", "PPT",  "slides"),
  pptx: T("slides", "PPTX", "slides"),
  odp:  T("slides", "ODP",  "slides"),

  // calendar / contact — themed table-style viewers.
  ics:  T("calendar", "ICS", "ics"),
  vcf:  T("contact",  "VCF", "vcf"),

  // data / config — structured config/data opens in the DataTreeViewer
  // (collapsible JSON/YAML/XML tree); keep the `code` glyph so the card
  // still reads as a config file.
  json: T("code", "JSON", "datatree"),
  yaml: T("code", "YAML", "datatree"),
  yml:  T("code", "YML",  "datatree"),
  xml:  T("code", "XML",  "datatree"),
  toml: T("code", "TOML", "datatree"),
  // html/htm open in the HtmlViewer — a sandboxed, scripts-disabled
  // static preview (with a raw-source toggle). Kept as its own `html`
  // kind so preview.jsx routes them there instead of the raw CodePreview.
  html: T("code", "HTML", "html"),
  htm:  T("code", "HTM",  "html"),
  css:  T("code", "CSS",  "code"),
  scss: T("code", "SCSS", "code"),
  js:   T("code", "JS",   "code"),
  jsx:  T("code", "JSX",  "code"),
  ts:   T("code", "TS",   "code"),
  tsx:  T("code", "TSX",  "code"),
  py:   T("code", "PY",   "code"),
  rb:   T("code", "RB",   "code"),
  go:   T("code", "GO",   "code"),
  rs:   T("code", "RS",   "code"),
  java: T("code", "JAVA", "code"),
  c:    T("code", "C",    "code"),
  cpp:  T("code", "CPP",  "code"),
  h:    T("code", "H",    "code"),
  sh:   T("code", "SH",   "code"),
  sql:  T("code", "SQL",  "code"),

  // extended code/text languages — all open in CodePreview (Prism
  // grammar picked from the backend `text/x-<lang>` mime). `toml` is
  // deliberately NOT here — it's a datatree above.
  kt:   T("code", "KT",   "code"),
  kts:  T("code", "KTS",  "code"),
  swift:T("code", "SWIFT","code"),
  cs:   T("code", "CS",   "code"),
  php:  T("code", "PHP",  "code"),
  lua:  T("code", "LUA",  "code"),
  r:    T("code", "R",    "code"),
  pl:   T("code", "PL",   "code"),
  dart: T("code", "DART", "code"),
  scala:T("code", "SCALA","code"),
  clj:  T("code", "CLJ",  "code"),
  ex:   T("code", "EX",   "code"),
  exs:  T("code", "EXS",  "code"),
  erl:  T("code", "ERL",  "code"),
  hs:   T("code", "HS",   "code"),
  ml:   T("code", "ML",   "code"),
  elm:  T("code", "ELM",  "code"),
  nim:  T("code", "NIM",  "code"),
  zig:  T("code", "ZIG",  "code"),
  cr:   T("code", "CR",   "code"),
  jl:   T("code", "JL",   "code"),
  sol:  T("code", "SOL",  "code"),
  groovy:T("code","GROOVY","code"),
  gradle:T("code","GRADLE","code"),
  fnl:  T("code", "FNL",  "code"),
  fs:   T("code", "FS",   "code"),
  nix:  T("code", "NIX",  "code"),
  d:    T("code", "D",    "code"),
  vala: T("code", "VALA", "code"),
  hx:   T("code", "HX",   "code"),
  tf:   T("code", "TF",   "code"),
  hcl:  T("code", "HCL",  "code"),
  cc:   T("code", "CC",   "code"),
  cxx:  T("code", "CXX",  "code"),
  hpp:  T("code", "HPP",  "code"),
  cmake:T("code", "CMAKE","code"),
  ini:  T("code", "INI",  "code"),
  conf: T("code", "CONF", "code"),
  cfg:  T("code", "CFG",  "code"),
  env:  T("code", "ENV",  "code"),
  proto:T("code", "PROTO","code"),
  graphql:T("code","GRAPHQL","code"),
  gql:  T("code", "GQL",  "code"),
  diff: T("code", "DIFF", "code"),
  patch:T("code", "PATCH","code"),
  vue:  T("code", "VUE",  "code"),
  svelte:T("code","SVELTE","code"),
  ps1:  T("code", "PS1",  "code"),
  bash: T("code", "BASH", "code"),
  zsh:  T("code", "ZSH",  "code"),
  tex:  T("code", "TEX",  "code"),
  rst:  T("code", "RST",  "code"),
  bat:  T("code", "BAT",  "code"),
  cmd:  T("code", "CMD",  "code"),

  // archives.
  zip:  T("archive", "ZIP", "archive"),
  tar:  T("archive", "TAR", "archive"),
  gz:   T("archive", "GZ",  "archive"),
  "7z": T("archive", "7Z",  "archive"),
  rar:  T("archive", "RAR", "archive"),
};

// Lookup with safe fallback. `ext` may be missing, uppercase,
// or include a leading dot — normalize all of those.
export function fileTypeInfo(ext) {
  const k = (ext || "").toString().toLowerCase().replace(/^\./, "");
  return CATALOG[k] || T("file", k.toUpperCase() || "FILE", "generic");
}

// Convenience helpers used by preview.jsx + gallery.jsx.
export function isVideoExt(ext) { return fileTypeInfo(ext).kind === "video"; }
export function isAudioExt(ext) { return fileTypeInfo(ext).kind === "audio"; }
export function isCsvExt(ext)   { return fileTypeInfo(ext).kind === "csv"; }
export function isIcsExt(ext)   { return fileTypeInfo(ext).kind === "ics"; }
export function isVcfExt(ext)   { return fileTypeInfo(ext).kind === "vcf"; }
export function isMarkdownExt(ext) { return fileTypeInfo(ext).kind === "markdown"; }
export function isDataTreeExt(ext) { return fileTypeInfo(ext).kind === "datatree"; }
export function isNotebookExt(ext) { return fileTypeInfo(ext).kind === "notebook"; }
export function isModel3dExt(ext)  { return fileTypeInfo(ext).kind === "model3d"; }
export function isFontExt(ext)     { return fileTypeInfo(ext).kind === "font"; }
export function isEbookExt(ext)    { return fileTypeInfo(ext).kind === "ebook"; }
export function isArchiveExt(ext)  { return fileTypeInfo(ext).kind === "archive"; }
export function isHtmlExt(ext)     { return fileTypeInfo(ext).kind === "html"; }
