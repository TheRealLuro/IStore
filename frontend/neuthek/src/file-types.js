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
  md:   T("document", "MD",  "code"),

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

  // data / config — opened by CodePreview (already wired).
  json: T("code", "JSON", "code"),
  yaml: T("code", "YAML", "code"),
  yml:  T("code", "YML",  "code"),
  xml:  T("code", "XML",  "code"),
  toml: T("code", "TOML", "code"),
  html: T("code", "HTML", "code"),
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
