from __future__ import annotations

import asyncio
import html
import logging
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Callable
from uuid import UUID, uuid4

from PIL import Image as PILImage

from backend.config import settings
from backend.storage import storage

logger = logging.getLogger(__name__)


class UploadValidationError(ValueError):
    def __init__(self, detail: str, status_code: int = 415) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


# Reserved Windows device names — case-insensitive, applied to the basename
# (the part before the final extension). Set as `name.upper()` so the lookup
# is `base.upper() in _WINDOWS_RESERVED_NAMES`.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# Characters that Windows forbids in filenames. `:` is also banned because of
# its role in alternate-data-stream syntax (`name.jpg:hidden.exe`).
_FORBIDDEN_FILENAME_CHARS = set('<>:"/\\|?*')

# Control chars (0x00–0x1F) — always banned.
_CONTROL_CHARS = set(chr(c) for c in range(0x20))


def validate_image_filename(
    new_name: str, original_filename: str | None
) -> str:
    """Sanitize a user-supplied filename for the rename endpoint.

    Rules:
    - Collapse whitespace; strip leading/trailing.
    - Reject empty result, path separators, control chars, and any of
      `<>:"/\\|?*`.
    - Reject reserved Windows device names (CON, PRN, AUX, NUL, COM1–9,
      LPT1–9) case-insensitive, checked against the basename.
    - If the original filename had an extension, the new name MUST have
      the same extension (case-insensitive). If the user dropped it, we
      add it back; if they tried to change it, we reject.
    - UTF-8 byte length ≤ 255 after sanitization. We trim from the
      basename, never from the extension.

    Returns the sanitized name. Raises `UploadValidationError(400)` on
    invalid input.
    """
    if not new_name or not isinstance(new_name, str):
        raise UploadValidationError("Filename is required.", status_code=400)

    # Strip + collapse whitespace.
    cleaned = re.sub(r"\s+", " ", new_name).strip()
    if not cleaned:
        raise UploadValidationError(
            "Filename can't be empty after trimming.", status_code=400
        )

    # Control chars / forbidden punctuation.
    if any(ch in _CONTROL_CHARS for ch in cleaned):
        raise UploadValidationError(
            "Filename contains control characters.", status_code=400
        )
    bad = [ch for ch in cleaned if ch in _FORBIDDEN_FILENAME_CHARS]
    if bad:
        raise UploadValidationError(
            f"Filename can't contain {' '.join(sorted(set(bad)))}.",
            status_code=400,
        )

    # Split base + ext on the LAST dot (so `archive.tar.gz` keeps `.gz`).
    if "." in cleaned and not cleaned.endswith("."):
        base, ext_new = cleaned.rsplit(".", 1)
        ext_new = "." + ext_new
    else:
        base, ext_new = cleaned, ""

    # Reserved Windows names — checked on the basename only.
    if base.upper() in _WINDOWS_RESERVED_NAMES:
        raise UploadValidationError(
            f"'{base}' is a reserved system name on Windows.",
            status_code=400,
        )

    # Preserve original extension. If the upload had `.jpg`, the rename
    # must keep `.jpg` (case-insensitive). Missing extension → we
    # re-append. Mismatched extension → reject (avoid silently breaking
    # the served MIME / decoder).
    orig_ext = _suffix(original_filename) or ""
    if orig_ext:
        if not ext_new:
            ext_new = orig_ext
        elif ext_new.lower() != orig_ext.lower():
            raise UploadValidationError(
                f"Extension must stay {orig_ext}; got {ext_new}.",
                status_code=400,
            )

    if not base:
        raise UploadValidationError(
            "Filename can't be just an extension.", status_code=400
        )

    full = base + ext_new

    # UTF-8 byte length cap. Trim from the basename so the extension survives.
    if len(full.encode("utf-8")) > 255:
        ext_bytes = len(ext_new.encode("utf-8"))
        max_base_bytes = 255 - ext_bytes
        if max_base_bytes <= 0:
            raise UploadValidationError(
                "Filename is too long.", status_code=400
            )
        # Truncate base char-by-char until it fits the byte budget.
        truncated = base
        while len(truncated.encode("utf-8")) > max_base_bytes:
            truncated = truncated[:-1]
        if not truncated:
            raise UploadValidationError(
                "Filename is too long.", status_code=400
            )
        full = truncated + ext_new

    return full


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str | None
    raw_bytes: bytes
    original_raw_bytes: bytes
    mime_type: str
    category: str
    # Pre-computed dimensions from the validate-time PIL decode so the
    # caller doesn't have to re-decode the file just to read width/height.
    # None for non-image categories (no decode happened).
    width: int | None = None
    height: int | None = None


_GENERIC_CLIENT_MIME = {
    "",
    "application/octet-stream",
    "binary/octet-stream",
}

_TEXT_EXTS = {".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml", ".toml", ".ini"}

# Source code + structured-text extensions. Treated as document uploads
# with a language-specific text/x-* mime so the FE can render them
# through the syntax-highlighted code preview instead of trying to
# decode them as images. The value is the mime returned by detect_magic;
# the FE inspects the language part to pick a Prism grammar.
_CODE_EXTS: dict[str, str] = {
    # Web
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".scss": "text/x-scss",
    ".sass": "text/x-sass",
    ".less": "text/x-less",
    ".svg": "image/svg+xml",
    # JS family
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".ts": "text/x-typescript",
    ".tsx": "text/x-tsx",
    ".jsx": "text/x-jsx",
    ".vue": "text/x-vue",
    ".svelte": "text/x-svelte",
    # Compiled-lang family
    ".py": "text/x-python",
    ".pyi": "text/x-python",
    ".rb": "text/x-ruby",
    ".php": "text/x-php",
    ".java": "text/x-java",
    ".kt": "text/x-kotlin",
    ".kts": "text/x-kotlin",
    ".scala": "text/x-scala",
    ".swift": "text/x-swift",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cpp": "text/x-c++",
    ".cc": "text/x-c++",
    ".cxx": "text/x-c++",
    ".hpp": "text/x-c++",
    ".cs": "text/x-csharp",
    ".dart": "text/x-dart",
    ".lua": "text/x-lua",
    ".r": "text/x-r",
    ".pl": "text/x-perl",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript",
    ".fish": "text/x-shellscript",
    ".ps1": "text/x-powershell",
    ".sql": "text/x-sql",
    ".clj": "text/x-clojure",
    ".ex": "text/x-elixir",
    ".exs": "text/x-elixir",
    ".elm": "text/x-elm",
    ".erl": "text/x-erlang",
    ".hs": "text/x-haskell",
    ".lhs": "text/x-haskell",
    ".ml": "text/x-ocaml",
    ".mli": "text/x-ocaml",
    ".fs": "text/x-fsharp",
    ".fsi": "text/x-fsharp",
    ".fsx": "text/x-fsharp",
    ".nim": "text/x-nim",
    ".nims": "text/x-nim",
    ".zig": "text/x-zig",
    ".v": "text/x-v",
    ".cr": "text/x-crystal",
    ".groovy": "text/x-groovy",
    ".gradle": "text/x-groovy",
    ".jl": "text/x-julia",
    ".sol": "text/x-solidity",
    ".fnl": "text/x-fennel",
    ".hcl": "text/x-hcl",
    ".tf": "text/x-hcl",
    ".tfvars": "text/x-hcl",
    ".nix": "text/x-nix",
    ".d": "text/x-d",
    ".pas": "text/x-pascal",
    ".pp": "text/x-pascal",
    ".f": "text/x-fortran",
    ".f90": "text/x-fortran",
    ".f95": "text/x-fortran",
    ".for": "text/x-fortran",
    ".cob": "text/x-cobol",
    ".cbl": "text/x-cobol",
    ".asm": "text/x-asm",
    ".s": "text/x-asm",
    ".vala": "text/x-vala",
    ".hx": "text/x-haxe",
    ".rkt": "text/x-scheme",
    ".scm": "text/x-scheme",
    ".ss": "text/x-scheme",
    ".lisp": "text/x-lisp",
    ".lsp": "text/x-lisp",
    ".el": "text/x-lisp",
    ".cljs": "text/x-clojure",
    ".cljc": "text/x-clojure",
    ".edn": "text/x-clojure",
    ".coffee": "text/x-coffeescript",
    ".purs": "text/x-purescript",
    ".re": "text/x-reason",
    ".rei": "text/x-reason",
    ".gleam": "text/x-gleam",
    ".odin": "text/x-odin",
    ".jsonc": "application/json",
    ".json5": "application/json",
    ".tsv": "text/tab-separated-values",
    ".csv": "text/csv",
    ".awk": "text/x-awk",
    ".sed": "text/x-sed",
    ".bat": "text/x-batch",
    ".cmd": "text/x-batch",
    ".tcl": "text/x-tcl",
    ".vim": "text/x-vim",
    ".pug": "text/x-pug",
    ".jade": "text/x-pug",
    ".haml": "text/x-haml",
    ".slim": "text/x-slim",
    ".astro": "text/x-astro",
    ".njk": "text/x-jinja2",
    ".jinja": "text/x-jinja2",
    ".jinja2": "text/x-jinja2",
    ".j2": "text/x-jinja2",
    ".twig": "text/x-twig",
    ".mustache": "text/x-handlebars",
    ".hbs": "text/x-handlebars",
    ".handlebars": "text/x-handlebars",
    ".liquid": "text/x-liquid",
    ".erb": "text/x-erb",
    ".ejs": "text/x-ejs",
    ".cmake": "text/x-cmake",
    ".mk": "text/x-makefile",
    ".bazel": "text/x-bazel",
    ".bzl": "text/x-bazel",
    ".n3": "text/n3",
    ".ttl": "text/turtle",
    ".m": "text/x-objectivec",
    ".mm": "text/x-objectivec",
    ".applescript": "text/x-applescript",
    ".ahk": "text/x-autohotkey",
    ".au3": "text/x-autoit",
    ".ino": "text/x-arduino",
    ".pde": "text/x-processing",
    ".sml": "text/x-sml",
    ".idr": "text/x-idris",
    ".agda": "text/x-agda",
    ".lean": "text/x-lean",
    ".wat": "text/x-wasm",
    ".prql": "text/x-prql",
    ".cql": "text/x-sql",
    ".psql": "text/x-sql",
    ".plsql": "text/x-sql",
    ".rs.in": "text/x-rust",
    ".kts.in": "text/x-kotlin",
    # Markup + docs
    ".rst": "text/x-rst",
    ".adoc": "text/x-asciidoc",
    ".asciidoc": "text/x-asciidoc",
    ".tex": "text/x-tex",
    ".latex": "text/x-tex",
    # Config / infra
    ".env": "text/x-dotenv",
    ".env.example": "text/x-dotenv",
    ".env.sample": "text/x-dotenv",
    ".dockerfile": "text/x-dockerfile",
    ".dockerignore": "text/plain",
    ".gitignore": "text/plain",
    ".editorconfig": "text/plain",
    ".cfg": "text/x-properties",
    ".conf": "text/x-properties",
    ".ini": "text/x-properties",
    ".properties": "text/x-properties",
    ".xml": "text/xml",
    ".xsd": "text/xml",
    ".xsl": "text/xml",
    ".xslt": "text/xml",
    ".svg.txt": "text/xml",
    ".plist": "text/xml",
    ".toml": "text/x-toml",
    ".sarif": "application/json",
    ".lock": "text/plain",
    ".graphql": "text/x-graphql",
    ".gql": "text/x-graphql",
    ".proto": "text/x-protobuf",
    ".diff": "text/x-diff",
    ".patch": "text/x-diff",
    # Jupyter
    ".ipynb": "application/x-ipynb+json",
}
# Files identified by basename (no extension) — Dockerfile / Makefile etc.
_CODE_BASENAMES: dict[str, str] = {
    "dockerfile": "text/x-dockerfile",
    "makefile": "text/x-makefile",
    "gnumakefile": "text/x-makefile",
    "rakefile": "text/x-ruby",
    "gemfile": "text/x-ruby",
    "podfile": "text/x-ruby",
    "vagrantfile": "text/x-ruby",
    "procfile": "text/plain",
}
# Extension → bare language token, used by `_lang_text_mime` to derive a
# `text/x-<lang>` MIME for a text-decoding file whose extension is NOT in
# the curated `_CODE_EXTS` allow-list above. This is the broadening that
# makes ANY UTF-8-clean code/text file viewable: rather than fall straight
# to a download-only `application/octet-stream`, an unrecognized-but-textual
# extension still earns a viewable `text/x-<token>` MIME so the FE renders
# it through the code preview (with a graceful plain-monospace fallback
# when the FE has no Prism grammar for the token). Curated `_CODE_EXTS`
# entries always win — this table only catches the long tail. Tokens are
# lowercased extensions stripped of the leading dot unless remapped here
# to a friendlier Prism-ish name.
_EXT_LANG_TOKEN: dict[str, str] = {
    # Friendlier tokens than the bare extension for a handful of cases the
    # FE's MIME→grammar map keys on. Anything not listed here derives its
    # token directly from the extension (e.g. `.foo` → `text/x-foo`).
    "rs": "rust",
    "py": "python",
    "rb": "ruby",
    "kt": "kotlin",
    "kts": "kotlin",
    "js": "javascript",
    "ts": "typescript",
    "yml": "yaml",
    "md": "markdown",
    "txt": "plain",
    "text": "plain",
    "rs.in": "rust",
}
_OOXML = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
# Camera RAW formats. All TIFF-shaped (except CR3 which is ISOBMFF and
# handled separately). The mime values are the IANA-registered
# `image/x-<vendor>-<format>` aliases — Pillow doesn't decode any of
# them properly, but rawpy does. See codecs.decode_raw_to_pil.
_RAW_EXTS = {
    ".nef": "image/x-nikon-nef",
    ".cr2": "image/x-canon-cr2",
    ".arw": "image/x-sony-arw",
    ".dng": "image/x-adobe-dng",
    ".raf": "image/x-fuji-raf",
    ".orf": "image/x-olympus-orf",
    ".rw2": "image/x-panasonic-rw2",
    ".pef": "image/x-pentax-pef",
}
RAW_MIMES: frozenset[str] = frozenset(_RAW_EXTS.values())

# Video / audio extensions paired with their canonical MIMEs. The
# magic-byte branches below check the file signature first (so a
# `.mp4` masquerading as a PDF still gets rejected); the extension
# map is consulted to disambiguate ISO BMFF brands (mp4 vs m4a vs
# mov vs m4v all share the `ftyp` box) and to pick the right MIME
# for matched RIFF / Matroska bytes.
_VIDEO_EXTS: dict[str, str] = {
    ".mp4":  "video/mp4",
    ".m4v":  "video/mp4",
    ".mov":  "video/quicktime",
    ".webm": "video/webm",
    ".mkv":  "video/x-matroska",
    ".avi":  "video/x-msvideo",
}
_AUDIO_EXTS: dict[str, str] = {
    ".mp3":  "audio/mpeg",
    ".m4a":  "audio/mp4",
    ".aac":  "audio/aac",
    ".wav":  "audio/wav",
    ".flac": "audio/flac",
    ".ogg":  "audio/ogg",
    ".opus": "audio/opus",
}
# ISO Base Media File Format brand → MIME. The first 8 bytes of an
# ISO BMFF file are `<size>ftyp`, then a 4-char brand at [8:12]. We
# disambiguate by brand first; if the brand isn't in this table, we
# fall back to the filename extension. Common brands seen in the wild:
#   isom mp41 mp42 mp4v MSNV avc1   → mp4
#   M4V  M4VH M4VP                  → m4v
#   M4A  M4B                        → m4a
#   qt                              → mov
_ISO_BMFF_BRANDS: dict[bytes, str] = {
    b"isom": "video/mp4",
    b"mp41": "video/mp4",
    b"mp42": "video/mp4",
    b"mp4v": "video/mp4",
    b"avc1": "video/mp4",
    b"MSNV": "video/mp4",
    b"M4V ": "video/mp4",
    b"M4VH": "video/mp4",
    b"M4VP": "video/mp4",
    b"M4A ": "audio/mp4",
    b"M4B ": "audio/mp4",
    b"qt  ": "video/quicktime",
    # HEIF/HEIC STILL IMAGES (iPhone photos) + AVIF. These are ISOBMFF
    # too and were previously unmatched here, so detect_magic fell through
    # to the `video/mp4` default below — every iPhone HEIC got routed into
    # the video transcoder, producing an HLS playlist + a garbage poster
    # frame that showed as a B&W blob in the gallery. Map the image brands
    # so they're recognized as images up front.
    b"heic": "image/heic",
    b"heix": "image/heic",
    b"heim": "image/heic",
    b"heis": "image/heic",
    b"hevc": "image/heic",
    b"hevx": "image/heic",
    b"mif1": "image/heif",
    b"msf1": "image/heif",
    b"mif2": "image/heif",
    b"avif": "image/avif",
    b"avis": "image/avif",
}


def _suffix(filename: str | None) -> str:
    if not filename:
        return ""
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


def detect_magic(data: bytes, filename: str | None) -> tuple[str, str]:
    head = data[:512].lstrip()
    lower = head.lower()
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "image"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "image"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        # Camera RAW formats are TIFF-shaped. Disambiguate by extension
        # so the decoder picks the right path — Pillow on a NEF only
        # reads the tiny embedded preview, but rawpy reads the full
        # sensor data. CR3 is ISOBMFF (`ftyp` magic) not TIFF and is
        # caught further down.
        ext_raw = _suffix(filename)
        if ext_raw in _RAW_EXTS:
            return _RAW_EXTS[ext_raw], "image"
        return "image/tiff", "image"
    if data.startswith(b"%PDF-"):
        return "application/pdf", "document"
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"):
        ext = _suffix(filename)
        if ext in _OOXML:
            return _OOXML[ext], "document"
        # VLT-2 — a generic ZIP (incl. .sketch, .key, .numbers, .pages,
        # epub, and plain .zip). Store it as an opaque "other" blob. We
        # never auto-extract user archives, so the zip-bomb concern
        # (which only bites on decompression) doesn't apply — the bytes
        # sit inert in the originals bucket until the user downloads
        # them. MIME forced to octet-stream so it's download-only.
        return "application/octet-stream", "other"
    # ISO Base Media File Format — mp4 / mov / m4a / m4v. The first
    # four bytes are a 32-bit box size, followed by `ftyp` and a 4-char
    # brand. Brand → MIME table catches the common cases; unknown
    # brands fall back to the filename extension when one matches.
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in _ISO_BMFF_BRANDS:
            mime = _ISO_BMFF_BRANDS[brand]
            if mime.startswith("audio/"):
                cat = "audio"
            elif mime.startswith("image/"):
                cat = "image"
            else:
                cat = "video"
            return mime, cat
        ext = _suffix(filename)
        if ext in _VIDEO_EXTS:
            return _VIDEO_EXTS[ext], "video"
        if ext in _AUDIO_EXTS:
            return _AUDIO_EXTS[ext], "audio"
        # HEIC/HEIF/AVIF with an unrecognized brand but a clear image
        # extension is still an image, never a video.
        if ext in {".heic", ".heif", ".hif", ".heics"}:
            return "image/heic", "image"
        if ext == ".avif":
            return "image/avif", "image"
        return "video/mp4", "video"  # safe default for ftyp blobs
    # Matroska / WebM — EBML header magic. Disambiguate by extension
    # because the EBML doctype could be either.
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        ext = _suffix(filename)
        if ext == ".webm":
            return "video/webm", "video"
        return "video/x-matroska", "video"
    # AVI — `RIFF<size>AVI ` (4 + 4 + 4 = first 12 bytes).
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "video/x-msvideo", "video"
    # WAV — `RIFF<size>WAVE`.
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav", "audio"
    # OGG / Opus.
    if data.startswith(b"OggS"):
        # Opus streams have an `OpusHead` packet near the front; a
        # plain `OggS` could carry Vorbis or FLAC too. We surface
        # `audio/opus` when we see OpusHead in the first 4 KiB and
        # `audio/ogg` otherwise.
        if b"OpusHead" in data[:4096]:
            return "audio/opus", "audio"
        return "audio/ogg", "audio"
    # FLAC.
    if data.startswith(b"fLaC"):
        return "audio/flac", "audio"
    # MP3 — either an ID3v2 tag (`ID3` at byte 0) or a raw MPEG
    # audio frame sync (`0xFF` byte where the next byte's top 3 bits
    # are all set). The frame-sync check is permissive on purpose;
    # there's no shorter shibboleth for tag-less MP3s.
    if data.startswith(b"ID3"):
        return "audio/mpeg", "audio"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        ext = _suffix(filename)
        if ext == ".mp3":
            return "audio/mpeg", "audio"
        if ext == ".aac":
            return "audio/aac", "audio"
    # Detect SVG / HTML / script payloads by CONTENT — robustly. `lower` is
    # already lstrip()'d, but a leading XML/HTML *comment* (`<!--…-->`) still
    # slips a `<svg onload=…>` / `<script>` past a naive `startswith` while a
    # browser renders it just fine. Peel any leading comment(s) before
    # matching so a comment-prefixed payload is classified correctly (and, for
    # the reject cases below, can't sneak past with an active MIME).
    sniff = lower
    _peeled = 0
    while sniff.startswith(b"<!--") and _peeled < 8:
        _end = sniff.find(b"-->")
        if _end == -1:
            break
        sniff = sniff[_end + 3:].lstrip()
        _peeled += 1
    ext = _suffix(filename)
    # SVG — previously hard-rejected. Now ACCEPTED as an image, but the bytes
    # are sanitized (script / on* handlers / foreignObject / external refs / XXE
    # stripped) by `_validate_svg` before they're stored. We classify by content
    # (a `<svg …>` root, optionally XML-declared) so a `.svg` mislabeled as
    # something else still lands here, and a `data.xml` that happens to embed an
    # `<svg>` deeper down does NOT (it stays a generic XML document via
    # `_CODE_EXTS`). Category is "image" + mime image/svg+xml so the FE routes it
    # to the image viewer (rendered inertly inside an <img>).
    if b"<svg" in lower and (
        sniff.startswith(b"<svg")
        or (sniff.startswith(b"<?xml") and b"<svg" in sniff)
        or ext == ".svg"
    ):
        return "image/svg+xml", "image"
    # HTML — previously hard-rejected outright. Now ACCEPTED *only when the file
    # is explicitly named* `.html` / `.htm`. Such a file is a document/code-
    # category upload (mime text/html). It is NOT sanitized here: the serve path
    # forces Content-Disposition: attachment + nosniff + `default-src 'none'` CSP
    # for text/html (see backend/api/images.py + SecurityHeadersMiddleware), so a
    # direct navigation downloads it and can never execute script at our origin;
    # the FE renders it only inside a sandboxed (no-allow-scripts) iframe.
    #
    # HTML/script content WITHOUT an .html/.htm extension stays REJECTED — that's
    # the dangerous masquerade case (a `photo.png` or `notes.txt` whose bytes are
    # actually `<script>`/`<html>`). Accepting only the explicitly-named files
    # keeps that guard intact while letting users upload genuine HTML.
    _is_html_content = sniff.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))
    if ext in (".html", ".htm") and _looks_text(data):
        # Recognized HTML document. Falls through to the _CODE_EXTS lookup below,
        # which maps .html/.htm → text/html, document category.
        pass
    elif _is_html_content or sniff.startswith(b"<script"):
        # HTML/script bytes under a non-HTML extension (or no extension): reject,
        # exactly as before. A scriptable payload must never be stored under a
        # benign MIME and rendered inline on a direct navigation.
        raise UploadValidationError("HTML/script uploads are not accepted.", 415)
    if ext in _TEXT_EXTS and _looks_text(data):
        return _text_mime(filename), "document"
    # Source code + structured-text uploads. Same trust line as plain
    # text — we require the bytes to look like UTF-8 with no NUL bytes
    # before accepting them. Catches a binary file masquerading as
    # `evil.py` with NULs inside. The mime carries the language so the
    # FE preview can pick the right Prism grammar.
    code_ext = _suffix(filename)
    if code_ext in _CODE_EXTS and _looks_text(data):
        return _CODE_EXTS[code_ext], "document"
    # Basename-only recognition for Dockerfile / Makefile / Rakefile etc.
    if filename:
        base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        if base in _CODE_BASENAMES and _looks_text(data):
            return _CODE_BASENAMES[base], "document"

    # VLT-2 — generic-binary fallback. Anything that reached here is
    # NOT one of the dangerous content shapes (HTML / SVG / script
    # were rejected above by *content* inspection, regardless of
    # extension), and isn't a recognized media/document type. Rather
    # than reject it — which made cloud-synced libraries lose every
    # .aep, .drawio, .sketch, .psd, .zip, etc. — accept it as an
    # opaque "other" file.
    #
    # Security stance for opaque files:
    #   * MIME is forced to application/octet-stream so the serve layer
    #     never renders it inline — browsers always download octet-
    #     stream, they never execute it. (The serve path also sends
    #     Content-Disposition: attachment for non-previewable types.)
    #   * Bytes are stored unmodified and NEVER auto-extracted /
    #     executed. A zip stored here is an inert blob — the zip-bomb
    #     concern only applies to archives we decompress, and we don't.
    #   * No AI pipeline runs on "other" — no Florence / CLIP / OCR
    #     pass touches an opaque binary.
    #
    # Text-shaped files that slipped past the code/text allow-lists
    # above are now made *viewable* rather than download-only. ANY file
    # whose bytes decode cleanly as UTF-8 (already asserted by
    # `_looks_text` — no NUL bytes, valid UTF-8) earns a viewable
    # `text/x-<lang>` (or `text/plain`) MIME derived from its extension,
    # so the gallery renders it through the syntax-highlighted code
    # preview instead of forcing a download. The script-injection guard
    # is unchanged and runs FIRST: a `.weirdext` (or any unrecognized
    # extension) full of `<script>` / `<svg>` / `javascript:` is still
    # rejected by `_reject_scriptable_text` before we hand back a
    # viewable text MIME, so active HTML/SVG can never be coaxed into an
    # inline render via this broadened path.
    if _looks_text(data):
        _reject_scriptable_text(data)
        return _lang_text_mime(filename), "document"
    return "application/octet-stream", "other"


def _text_mime(filename: str | None) -> str:
    ext = _suffix(filename)
    if ext == ".md":
        return "text/markdown"
    if ext == ".csv":
        return "text/csv"
    if ext == ".json":
        return "application/json"
    return "text/plain"


def _lang_text_mime(filename: str | None) -> str:
    """Derive a *viewable* text MIME for a file that decoded cleanly as
    UTF-8 but isn't in the curated `_CODE_EXTS` allow-list.

    Returns a `text/x-<token>` MIME when the extension yields a usable
    language token (so the FE code preview can attempt a Prism grammar),
    or plain `text/plain` when there's no meaningful extension. Either
    way the result is a viewable `text/*` MIME — never the download-only
    `application/octet-stream`. The token is the lowercased extension
    (sans leading dot) unless remapped in `_EXT_LANG_TOKEN`.

    Note: callers must run `_reject_scriptable_text` on the bytes before
    trusting this MIME — this function only picks a label, it does NOT
    re-check for active `<script>` / `<svg>` content.
    """
    ext = _suffix(filename)
    if not ext:
        return "text/plain"
    token = _EXT_LANG_TOKEN.get(ext.lstrip("."), ext.lstrip("."))
    # A token has to look like a bare identifier to be MIME-safe; a weird
    # extension full of punctuation just falls back to plain text.
    if not token or token == "plain" or not re.fullmatch(r"[a-z0-9][a-z0-9+._-]*", token):
        return "text/plain"
    return f"text/x-{token}"


def _looks_text(data: bytes) -> bool:
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _client_mime_ok(client_mime: str | None, detected: str) -> bool:
    # VLT-2 — when we fell back to the opaque-binary type, the file is
    # stored unmodified and served download-only; the client's claimed
    # MIME is irrelevant (and providers report all sorts of vendor
    # MIMEs for .drawio / .sketch / etc.). Accept any client MIME in
    # that case rather than rejecting on a cosmetic mismatch.
    if detected == "application/octet-stream":
        return True
    ct = (client_mime or "").split(";", 1)[0].lower().strip()
    if ct in _GENERIC_CLIENT_MIME:
        return True
    if ct == detected:
        return True
    if ct.startswith("image/") and detected.startswith("image/"):
        return True
    if ct.startswith("text/") and detected.startswith("text/"):
        return True
    return False


def _reject_scriptable_text(data: bytes) -> None:
    text = data[:32_000].decode("utf-8", errors="ignore").lower()
    if re.search(r"<\s*(script|iframe|object|embed|svg|html)\b", text):
        raise UploadValidationError("Scriptable document content is not accepted.", 415)
    if "javascript:" in text:
        raise UploadValidationError("Scriptable document content is not accepted.", 415)


def _inspect_zip_safety(
    zf: zipfile.ZipFile,
    *,
    error_label: str,
) -> list[zipfile.ZipInfo]:
    """Shared zip-bomb / path-traversal / symlink inspection.

    Used by both `_inspect_ooxml` (OOXML containers — `.docx` / `.xlsx` /
    `.pptx`) and the §C1.5 general archive uploader. Same constants
    (`upload_max_archive_entries`, `upload_max_archive_ratio`,
    `upload_max_archive_depth`) so the security posture is identical
    on every code path that ingests a ZIP.

    `error_label` is spliced into the rejection detail so the user sees
    "Document archive…" vs. "Archive…" depending on the upload route.
    Returns the validated list of entries so the caller can iterate them
    without re-scanning.
    """
    infos = zf.infolist()
    if len(infos) > settings.upload_max_archive_entries:
        raise UploadValidationError(f"{error_label} has too many entries.", 415)
    compressed = sum(max(i.compress_size, 0) for i in infos) or 1
    uncompressed = sum(max(i.file_size, 0) for i in infos)
    # Audit U2 — three layered checks. The cumulative ratio (legacy)
    # only catches archives that are uniformly bomb-shaped. A single
    # bomb entry hidden among many small ones averages out and slips
    # through — until the per-entry ratio + total-uncompressed cap
    # below catch it. All three checks remain so the archive must
    # pass under every angle.
    if uncompressed > compressed * settings.upload_max_archive_ratio:
        raise UploadValidationError(
            f"{error_label} expansion ratio is too high.", 415
        )
    if uncompressed > settings.upload_max_archive_total_uncompressed_bytes:
        raise UploadValidationError(
            f"{error_label} total uncompressed size is too large.", 415
        )
    for info in infos:
        # Per-entry uncompressed cap — refuses the "one giant entry,
        # many tiny ones" zip-bomb shape that defeats the average-
        # based ratio gate above.
        if info.file_size > settings.upload_max_archive_entry_uncompressed_bytes:
            raise UploadValidationError(
                f"{error_label} contains an entry that is too large.", 415
            )
        # Per-entry ratio — refuses any single entry whose declared
        # uncompressed-to-compressed ratio is above the cap, even if
        # the archive's cumulative ratio is fine. Ratio of 1 for
        # zero-size compressed entries is acceptable.
        if (
            info.compress_size > 0
            and info.file_size > info.compress_size * settings.upload_max_archive_ratio
        ):
            raise UploadValidationError(
                f"{error_label} contains an over-expanding entry.", 415
            )
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        parts = [p for p in path.parts if p not in {"", "."}]
        if name.startswith("/") or ".." in parts:
            raise UploadValidationError(
                f"{error_label} contains an unsafe path.", 415
            )
        if len(parts) > settings.upload_max_archive_depth:
            raise UploadValidationError(
                f"{error_label} nesting is too deep.", 415
            )
        # Unix symlink bit in external_attr.
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise UploadValidationError(
                f"{error_label} contains a symlink.", 415
            )
    return infos


def _inspect_ooxml(data: bytes, filename: str | None) -> None:
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            infos = _inspect_zip_safety(zf, error_label="Document archive")
            names = {i.filename for i in infos}
            if "[Content_Types].xml" not in names:
                raise UploadValidationError(
                    "Office document is missing content type metadata.", 415
                )
    except zipfile.BadZipFile as exc:
        raise UploadValidationError("Office document is not a valid ZIP container.", 415) from exc


# ---------- SVG sanitization ----------
#
# SVG is XML that a browser executes: <script>, on* handlers, <foreignObject>
# (arbitrary HTML), javascript:/data: URIs, and external href/<use> refs all run
# code or exfiltrate. Rather than hard-reject every .svg (the old behavior), we
# parse the bytes with a HARDENED lxml parser (DTD/entity resolution OFF — blocks
# XXE / billion-laughs) and walk the tree with a strict ALLOWLIST scrub:
#   * remove every element NOT in `_SVG_ALLOWED_TAGS` (drops <script>, <foreignObject>,
#     <iframe>/<embed>/<object>, <handler>, <set>, animation-with-script, etc.);
#   * strip every attribute that is an on* event handler or not in the per-tag /
#     global allowlist;
#   * scrub URL-bearing attributes (href / xlink:href / src / from / to / values /
#     style url(...)) so only same-document `#fragment` refs and safe inline
#     `data:image/(png|jpeg|gif|webp)` survive — javascript:, external http(s)///,
#     and non-image data: URIs are dropped;
#   * drop <style> contents containing url()/@import/expression/javascript: rather
#     than try to parse CSS (conservative: nuke the whole style block if suspicious).
# The result is re-serialized and stored. If lxml can't parse the bytes we FAIL
# CLOSED (reject), never store unparseable "maybe-svg" bytes with an active MIME.
#
# This is the FINAL server-side guard. The serve path additionally sends
# Content-Disposition: attachment + nosniff + `default-src 'none'` CSP for
# image/svg+xml (see backend/api/images.py `_serve_headers` and the
# SecurityHeadersMiddleware), and the FE renders SVG only inside an <img> tag
# (which never runs SVG script). Defense in depth — but the scrub below stands on
# its own.

# SVG namespace URIs we recognize. lxml reports tags as `{uri}local`; we compare
# on the local name but also reject foreign namespaces (HTML / XSLT smuggled in).
_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"

# Allowlisted SVG element local-names. Anything not here is dropped wholesale.
# Deliberately EXCLUDES: script, foreignObject, iframe, embed, object, handler,
# audio, video, animate*/set (can target attributes to inject), use is allowed
# but its href is scrubbed to same-document refs only, image is allowed but href
# scrubbed to safe data:/# only.
_SVG_ALLOWED_TAGS = frozenset({
    "svg", "g", "defs", "symbol", "use", "switch", "a",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textPath", "tref",
    "title", "desc", "metadata",
    "linearGradient", "radialGradient", "stop", "pattern",
    "clipPath", "mask", "filter",
    # filter primitives — pure raster ops, no script surface
    "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite",
    "feConvolveMatrix", "feDiffuseLighting", "feDisplacementMap",
    "feDistantLight", "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR",
    "feGaussianBlur", "feImage", "feMerge", "feMergeNode", "feMorphology",
    "feOffset", "fePointLight", "feSpecularLighting", "feSpotLight", "feTile",
    "feTurbulence",
    "marker", "view",
    "image", "style",
})

# Global attributes safe on any element. on* handlers are blocked by a separate
# prefix check; URL-bearing attributes are scrubbed by `_SVG_URL_ATTRS`.
_SVG_ALLOWED_ATTRS = frozenset({
    # core / presentation
    "id", "class", "style", "transform", "x", "y", "width", "height",
    "viewBox", "preserveAspectRatio", "version", "baseProfile",
    "d", "points", "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset",
    "stroke-opacity", "stroke-miterlimit", "opacity", "color",
    "stop-color", "stop-opacity", "offset",
    "gradientUnits", "gradientTransform", "spreadMethod",
    "patternUnits", "patternContentUnits", "patternTransform",
    "clip-path", "clip-rule", "mask", "filter",
    "font-family", "font-size", "font-weight", "font-style", "text-anchor",
    "letter-spacing", "word-spacing", "dominant-baseline", "alignment-baseline",
    "dx", "dy", "rotate", "textLength", "lengthAdjust",
    "display", "visibility", "overflow",
    "markerStart", "marker-start", "marker-mid", "marker-end",
    "markerWidth", "markerHeight", "markerUnits", "orient", "refX", "refY",
    "maskUnits", "maskContentUnits", "filterUnits", "primitiveUnits",
    "result", "in", "in2", "mode", "values", "type", "stdDeviation",
    "operator", "k1", "k2", "k3", "k4", "scale", "xChannelSelector",
    "yChannelSelector", "baseFrequency", "numOctaves", "seed", "stitchTiles",
    "tableValues", "slope", "intercept", "amplitude", "exponent",
    "edgeMode", "kernelMatrix", "divisor", "bias", "targetX", "targetY",
    "surfaceScale", "specularConstant", "specularExponent", "diffuseConstant",
    "limitingConeAngle", "pointsAtX", "pointsAtY", "pointsAtZ",
    "azimuth", "elevation", "radius", "flood-color", "flood-opacity",
    "lighting-color", "color-interpolation-filters", "color-interpolation",
    "shape-rendering", "text-rendering", "image-rendering",
    "vector-effect", "paint-order", "isolation", "mix-blend-mode",
    "enable-background", "stop-color", "xml:space", "space",
    "startOffset", "method", "spacing", "side",
})

# Attributes whose value is a URL/IRI and must be scrubbed to a safe subset.
_SVG_URL_ATTRS = frozenset({"href", "src", "from", "to", "values", "begin", "end"})

# Inline data: URIs we permit (raster images only — rendered, never executed).
_SVG_SAFE_DATA_URI_RE = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp);base64,", re.IGNORECASE
)
# Characters that could re-introduce a scheme after whitespace/control stripping.
_SVG_CTRL_RE = re.compile(r"[\x00-\x20\x7f]")
# CSS that smells like it pulls/executes: url(), @import, expression(), behavior,
# javascript:, -moz-binding. If a <style> body or a style="" attr matches, we drop it.
_SVG_DANGEROUS_CSS_RE = re.compile(
    r"url\s*\(|@import|expression\s*\(|behavior\s*:|javascript\s*:|"
    r"-moz-binding|vbscript\s*:|<\s*script",
    re.IGNORECASE,
)


def _svg_safe_url(value: str) -> str | None:
    """Return the value if it's a safe SVG URL, else None (drop the attr).

    Safe = same-document fragment ref (`#id`) or an inline raster `data:` URI.
    Everything else — javascript:, vbscript:, external http(s)/protocol-relative
    `//host`, file:, and non-image data: — is rejected. Control/whitespace chars
    are stripped first so `java\\nscript:` can't sneak through.
    """
    if value is None:
        return None
    stripped = _SVG_CTRL_RE.sub("", value).strip()
    if not stripped:
        return None
    low = stripped.lower()
    # Same-document fragment reference — the common, safe <use href="#id"> case.
    if stripped.startswith("#"):
        return value
    # Inline raster data URI — rendered as an image, never executed.
    if low.startswith("data:"):
        return value if _SVG_SAFE_DATA_URI_RE.match(stripped) else None
    # Anything with a scheme (javascript:, http:, https:, file:, vbscript:, …)
    # or protocol-relative (//host) or a bare external path is dropped.
    return None


def _svg_clean_style(value: str | None) -> str | None:
    """Drop a style value entirely if it contains anything that could fetch or
    execute (url(), @import, expression(), javascript:, …). We don't try to
    parse CSS — conservative nuke-on-suspicion."""
    if not value:
        return value
    if _SVG_DANGEROUS_CSS_RE.search(value):
        return None
    return value


def _sanitize_svg(data: bytes) -> bytes:
    """Parse + allowlist-scrub SVG bytes, returning safe re-serialized SVG.

    Raises UploadValidationError (fail closed) if the bytes can't be parsed as
    XML or don't contain an <svg> root — we never store unparseable bytes under
    an active image/svg+xml MIME.
    """
    try:
        from lxml import etree
    except ImportError as exc:  # pragma: no cover - lxml is a hard dep
        raise UploadValidationError(
            "SVG support requires the `lxml` library on the server.", 500
        ) from exc

    # Hardened parser: resolve_entities=False + no_network=True + DTD load off
    # blocks XXE, billion-laughs, and external-entity SSRF. huge_tree=False keeps
    # the default expansion limits. We also forbid a DOCTYPE outright below.
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
        recover=False,
    )
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise UploadValidationError(
            f"SVG file is not well-formed XML and was rejected: {exc}", 415
        ) from exc
    except ValueError as exc:
        raise UploadValidationError(
            f"SVG file could not be parsed and was rejected: {exc}", 415
        ) from exc

    tree = root.getroottree()
    # Reject any DOCTYPE — even an "empty" one can declare entities (XXE). The
    # hardened parser won't resolve them, but refusing outright is cleaner.
    docinfo = tree.docinfo
    if docinfo is not None and (docinfo.doctype or docinfo.internalDTD is not None):
        raise UploadValidationError(
            "SVG file declares a DOCTYPE/DTD, which is not allowed.", 415
        )

    def _local(tag) -> str:
        # tag may be a comment/PI callable wrapper; only str tags matter.
        if not isinstance(tag, str):
            return ""
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def _ns(tag) -> str | None:
        if not isinstance(tag, str) or not tag.startswith("{"):
            return None
        return tag[1:tag.index("}")]

    # The root MUST be <svg>. A non-svg root means the bytes were misrouted.
    if _local(root.tag) != "svg":
        raise UploadValidationError(
            "File routed as SVG does not have an <svg> root element.", 415
        )

    # Walk the whole tree once. Collect removals first (mutating during iterate
    # is unsafe), then drop them from their parents.
    to_remove = []
    for el in root.iter():
        tag = el.tag
        # Comments / PIs are already stripped by the parser flags, but guard.
        if not isinstance(tag, str):
            to_remove.append(el)
            continue
        local = _local(tag)
        ns = _ns(tag)
        # Drop elements outside the SVG namespace (smuggled HTML / XSLT / etc.)
        # and any tag not on the allowlist (script, foreignObject, …).
        if (ns is not None and ns != _SVG_NS) or local not in _SVG_ALLOWED_TAGS:
            to_remove.append(el)
            continue
        # Scrub attributes on the surviving element.
        for name in list(el.attrib.keys()):
            attr_ns = _ns(name)
            attr_local = _local(name)
            low = attr_local.lower()
            # Drop ALL event handlers (onload, onclick, onbegin, onmouseover…).
            if low.startswith("on"):
                del el.attrib[name]
                continue
            # xlink:* — only xlink:href is meaningful and it's URL-scrubbed.
            if attr_ns == _XLINK_NS:
                if attr_local == "href":
                    safe = _svg_safe_url(el.attrib[name])
                    if safe is None:
                        del el.attrib[name]
                    else:
                        el.attrib[name] = safe
                else:
                    del el.attrib[name]
                continue
            # Drop attributes in any other foreign namespace (keep none-NS and
            # the xml: namespace handled via allowlist).
            if attr_ns is not None and attr_ns not in (None,):
                # xml:space etc. arrive with the XML namespace; allow only the
                # handful in the allowlist by local name.
                if attr_local not in _SVG_ALLOWED_ATTRS:
                    del el.attrib[name]
                    continue
            # URL-bearing attributes (href/src/…) get scrubbed.
            if attr_local in _SVG_URL_ATTRS:
                safe = _svg_safe_url(el.attrib[name])
                if safe is None:
                    del el.attrib[name]
                else:
                    el.attrib[name] = safe
                continue
            # style="" — drop if it can fetch/execute.
            if attr_local == "style":
                cleaned = _svg_clean_style(el.attrib[name])
                if cleaned is None:
                    del el.attrib[name]
                else:
                    el.attrib[name] = cleaned
                continue
            # Everything else must be on the global allowlist.
            if attr_local not in _SVG_ALLOWED_ATTRS:
                del el.attrib[name]
                continue
        # <style> element bodies: nuke text content if it can fetch/execute.
        if local == "style" and el.text and _SVG_DANGEROUS_CSS_RE.search(el.text):
            el.text = ""

    for el in to_remove:
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
        # (root itself is never in to_remove — its tag was validated as svg.)

    # Re-serialize. xml_declaration keeps it a valid standalone SVG document.
    out = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    return out


def _sanitize_image(data: bytes, detected_mime: str) -> tuple[bytes, int, int, str]:
    """Re-decode + re-encode an upload via Pillow to defang malicious
    polyglots, then return the safe re-encoded bytes.

    Preserves the EXIF blob (when present) on JPEG / WebP / TIFF
    outputs. The previous behavior dropped EXIF on every re-encode,
    which silently stripped GPS/camera metadata even when the user
    had granted `gps_retention` — they uploaded an iPhone shot
    expecting GPS to ride along, downloaded the "original" later,
    and found bare bytes. Privacy is still enforced at a different
    layer: GPS coordinates are only persisted to the queryable
    `image_geo` table when `gps_retention` is GRANTED. The blob in
    MinIO retains EXIF either way so the user's file stays faithful
    to what they uploaded.
    """
    PILImage.MAX_IMAGE_PIXELS = settings.upload_max_image_pixels

    # Camera RAW: Pillow only reads the small embedded JPEG preview, so
    # re-encoding through it produces a tiny, soft image. rawpy decodes
    # the full sensor data into a 16-bit RGB array; we then save as
    # JPEG quality 95 so the served version is actually high-resolution.
    # The originals bucket still gets the raw .nef / .cr2 / etc bytes
    # so the user can re-download the source file.
    if detected_mime in RAW_MIMES:
        try:
            import rawpy
            import numpy as np
        except ImportError:
            raise UploadValidationError(
                "RAW image support requires `rawpy` (LibRaw). Reinstall the "
                "backend deps and try again.",
                415,
            )
        try:
            with rawpy.imread(BytesIO(data)) as raw:
                # use_camera_wb keeps the in-camera white balance instead
                # of rawpy's auto-WB, which often shifts NEFs cyan.
                # output_bps=8 returns 0-255 RGB so JPEG can take it
                # directly. no_auto_bright avoids the "brightened beyond
                # what the photographer saw" surprise.
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    output_bps=8,
                    no_auto_bright=True,
                    user_flip=0,
                )
            pil = PILImage.fromarray(rgb)
            width, height = pil.size
            if width * height > settings.upload_max_image_pixels:
                raise UploadValidationError("Image is too large in pixels.", 413)
            out = BytesIO()
            pil.save(out, format="JPEG", quality=95, optimize=True, subsampling=0)
            # MIME flips to JPEG since that's what we're actually serving.
            # The original RAW lives untouched in originals; image_geo
            # extraction will still get GPS via the raw EXIF blob if the
            # RAW file carries one.
            return out.getvalue(), width, height, "image/jpeg"
        except UploadValidationError:
            raise
        except Exception as exc:
            raise UploadValidationError(
                f"Could not decode RAW image: {exc}", 415
            ) from exc

    try:
        with PILImage.open(BytesIO(data)) as pil:
            pil.verify()
        with PILImage.open(BytesIO(data)) as pil:
            pil.load()
            width, height = pil.size
            if width * height > settings.upload_max_image_pixels:
                raise UploadValidationError("Image is too large in pixels.", 413)
            # Snapshot the EXIF blob BEFORE `convert` — converting drops
            # the source image's `info` dict.
            exif_blob = pil.info.get("exif")
            if detected_mime == "image/jpeg":
                out = BytesIO()
                save_kwargs: dict = {"format": "JPEG", "quality": 95, "optimize": True}
                if exif_blob:
                    save_kwargs["exif"] = exif_blob
                pil.convert("RGB").save(out, **save_kwargs)
                return out.getvalue(), width, height, detected_mime
            if detected_mime == "image/png":
                # PNG doesn't carry EXIF in the standard metadata block —
                # nothing to preserve.
                out = BytesIO()
                pil.save(out, format="PNG")
                return out.getvalue(), width, height, detected_mime
            if detected_mime == "image/webp":
                out = BytesIO()
                save_kwargs = {"format": "WEBP", "lossless": True}
                if exif_blob:
                    save_kwargs["exif"] = exif_blob
                pil.save(out, **save_kwargs)
                return out.getvalue(), width, height, detected_mime
            if detected_mime == "image/gif":
                # Pillow's default `.save(format="GIF")` writes a SINGLE
                # frame — animated GIFs would lose every frame after the
                # first, silently turning into stills. The trailing-data
                # polyglot attacks we sanitize against on JPEG/WebP don't
                # apply to GIF (no EXIF, no APP markers), so just verify
                # the bytes decoded and return the original. Animation
                # survives the upload pipeline unchanged.
                pil.verify()  # already done above; cheap idempotency
                return data, width, height, detected_mime
            if detected_mime == "image/tiff":
                # Re-encode TIFF to PNG (browsers don't render TIFF
                # natively); TIFF EXIF doesn't survive the format swap
                # — image_geo extraction already ran on the true
                # original via `original_raw_bytes`.
                out = BytesIO()
                pil.save(out, format="PNG")
                return out.getvalue(), width, height, "image/png"
            if detected_mime in ("image/heic", "image/heif"):
                # Browsers can't render HEIC/HEIF natively, so re-encode the
                # SERVED bytes to JPEG (the true original HEIC still lives
                # untouched in the originals bucket, same as the RAW path).
                # pillow_heif's opener is registered via `backend.codecs`,
                # so PILImage.open already decoded it above. Preserve the
                # EXIF blob so GPS / camera metadata rides along to image_geo
                # and to the user's download, exactly like the JPEG branch.
                # Without this branch HEIC — an iPhone's default format and a
                # type already in our catalog — fell through to "Unsupported
                # image format" and the web uploader rejected every HEIC.
                out = BytesIO()
                save_kwargs = {"format": "JPEG", "quality": 95, "optimize": True}
                if exif_blob:
                    save_kwargs["exif"] = exif_blob
                pil.convert("RGB").save(out, **save_kwargs)
                return out.getvalue(), width, height, "image/jpeg"
    except UploadValidationError:
        raise
    except Exception as exc:
        raise UploadValidationError(f"Could not decode image: {exc}", 415) from exc
    raise UploadValidationError("Unsupported image format.", 415)


# ---------- §A1 — dispatch table by data_kind ----------
#
# `detect_magic` returns a category ("image" / "document" / "video" /
# "other"). Each category gets its own validator function in the table
# below. When §E types (contact / password / save / iot_event) land
# they register here too — adding a new data type is one entry, not
# another `if/elif` in `_validate_sync`. The handler signature mirrors
# `_validate_sync`: it gets the detected MIME + raw bytes + filename
# and returns a `ValidatedUpload`. Handlers may also raise
# UploadValidationError to reject.

ValidatorFn = Callable[[bytes, str | None, str, str], "ValidatedUpload"]


def _validate_image(raw_bytes: bytes, filename: str | None, detected_mime: str, category: str) -> ValidatedUpload:
    # Pillow re-decode + re-encode. The sanitized output goes into
    # `raw_bytes`; the untouched original is retained in
    # `original_raw_bytes` for EXIF GPS extraction (which needs the
    # raw EXIF blob in its source format). The originals bucket only
    # ever sees the sanitized bytes — trailing-data polyglots like
    # "valid-JPEG + appended HTML" are stripped at the encoder boundary.
    sanitized, w, h, sanitized_mime = _sanitize_image(raw_bytes, detected_mime)
    return ValidatedUpload(filename, sanitized, raw_bytes, sanitized_mime, "image", w, h)


def _validate_svg(raw_bytes: bytes, filename: str | None, detected_mime: str, category: str) -> ValidatedUpload:
    """Sanitize an SVG upload and store the SCRUBBED bytes.

    `_sanitize_svg` parses with a hardened (XXE-proof) parser and allowlist-
    scrubs script / on* handlers / foreignObject / external refs / dangerous
    URIs, failing closed (reject) on unparseable input. The sanitized bytes go
    into BOTH raw_bytes (served + originals) and original_raw_bytes — we never
    retain the un-scrubbed original for an SVG. Category stays "image"; the FE
    renders it inside an <img>, which never executes SVG script.
    """
    sanitized = _sanitize_svg(raw_bytes)
    return ValidatedUpload(filename, sanitized, sanitized, "image/svg+xml", "image")


def _validate_document(raw_bytes: bytes, filename: str | None, detected_mime: str, category: str) -> ValidatedUpload:
    # OOXML containers get a zip-shape inspection (entry count, path
    # traversal, depth, ratio, symlinks). Text-shaped documents get
    # script tag rejection. PDFs aren't introspected past magic bytes
    # — that's a deliberate trust line for now.
    if detected_mime in _OOXML.values():
        _inspect_ooxml(raw_bytes, filename)
    # `text/html` is the ONE text type we intentionally allow to contain
    # markup/script: it only reaches here for an explicitly-named .html/.htm
    # file (see detect_magic), and it's neutralized at the serve layer
    # (Content-Disposition: attachment + nosniff + `default-src 'none'` CSP)
    # and rendered only inside a no-allow-scripts sandboxed iframe on the FE.
    # Running `_reject_scriptable_text` on it would reject every real HTML file,
    # so we skip the scriptable-text gate for text/html specifically. Every
    # OTHER text/* + json document is still scrubbed for <script>/<svg>/etc.
    if detected_mime == "text/html":
        pass
    elif detected_mime.startswith("text/") or detected_mime == "application/json":
        _reject_scriptable_text(raw_bytes)
    return ValidatedUpload(filename, raw_bytes, raw_bytes, detected_mime, "document")


def _validate_passthrough(raw_bytes: bytes, filename: str | None, detected_mime: str, category: str) -> ValidatedUpload:
    """Default validator for kinds that don't need transformation —
    video, "other", and any future kind that lacks a specific handler.
    Bytes go to originals unmodified."""
    return ValidatedUpload(filename, raw_bytes, raw_bytes, detected_mime, category)


# §E future entries register against this same table. Until then,
# unknown kinds fall through to the passthrough validator (we'd
# already have rejected them at the magic-byte stage if the bytes
# weren't recognizable — anything that reaches here is at least
# format-shaped).
_VALIDATORS: dict[str, ValidatorFn] = {
    "image":    _validate_image,
    "document": _validate_document,
    "video":    _validate_passthrough,
    "audio":    _validate_passthrough,
    "other":    _validate_passthrough,
}


def register_validator(data_kind: str, fn: ValidatorFn) -> None:
    """Public hook for §E data types. Calling this at app boot adds a
    new entry; an existing entry is replaced (so a test fixture can
    swap a strict validator for a permissive one)."""
    _VALIDATORS[data_kind] = fn


def _validate_sync(
    user_id: UUID, filename: str | None, raw_bytes: bytes, client_mime: str | None,
) -> ValidatedUpload:
    """Pure-CPU portion of validate_upload — runs in a worker thread so
    the asyncio event loop isn't blocked by the PIL re-encode pass on
    every upload. The async wrapper below puts this on `asyncio.to_thread`
    so concurrent uploads don't serialize the API per-file.
    """
    detected_mime, category = detect_magic(raw_bytes, filename)
    if not _client_mime_ok(client_mime, detected_mime):
        raise UploadValidationError(
            f"Content-Type does not match file bytes ({html.escape(detected_mime)}).",
            415,
        )
    # SVG is category "image" but must NOT go through the Pillow re-encode
    # validator (Pillow can't decode vector XML). Route it to the dedicated
    # XML-sanitizing validator instead, which scrubs script/handlers/XXE and
    # stores the cleaned bytes.
    if detected_mime == "image/svg+xml":
        return _validate_svg(raw_bytes, filename, detected_mime, category)
    validator = _VALIDATORS.get(category, _validate_passthrough)
    return validator(raw_bytes, filename, detected_mime, category)


async def validate_upload(
    *,
    user_id: UUID,
    filename: str | None,
    raw_bytes: bytes,
    client_mime: str | None,
) -> ValidatedUpload:
    """Run upload validation through the kind-keyed dispatch table and
    return a `ValidatedUpload` on success.

    §A1 quarantine semantics:
      - Every upload is mirrored to the quarantine bucket in parallel
        with validation.
      - On success: the quarantine blob is deleted (it was just scratch
        — the validated bytes go to `originals` via the caller).
      - On failure: the quarantine blob is KEPT and an `audit_log` row
        is written with the quarantine key, filename, byte count, and
        rejection reason. This gives ops a forensic record of every
        rejected upload (catch'd polyglots, malformed archives, etc.)
        without leaving the bytes in `originals`. A retention sweeper
        (§B4) ages quarantine objects out on a documented schedule.
    """
    if not raw_bytes:
        raise UploadValidationError("Empty upload", 400)
    if len(raw_bytes) > settings.upload_max_bytes:
        raise UploadValidationError("Upload exceeds the per-file size limit.", 413)

    q_key = f"users/{user_id}/quarantine/{uuid4().hex}/{filename or 'upload'}"
    # Parallelize: kick the quarantine write off in a worker thread so
    # the PIL re-decode + re-encode below runs at the same time. Used
    # to be sequential, costing 200-1500 ms per upload on top of the
    # inevitable Pillow work.
    #
    # Use `create_task` (not `gather`) so a validation failure doesn't
    # cancel the in-flight quarantine write — we need that write to
    # complete so the forensic audit row points at a real object.
    quarantine_task = asyncio.create_task(
        asyncio.to_thread(
            storage.put,
            storage.bucket_quarantine, q_key, raw_bytes,
            client_mime or "application/octet-stream",
        )
    )
    try:
        # Validate body runs in a worker thread — PIL pixel ops
        # (verify, decode, re-encode) hold the GIL, so doing this on
        # the asyncio event loop would block every other request for
        # the duration of the encode. The thread offload keeps the
        # API responsive when several uploads land at once.
        validated = await asyncio.to_thread(
            _validate_sync, user_id, filename, raw_bytes, client_mime,
        )
        await quarantine_task  # ensure the write finished
    except Exception as exc:
        # Wait for the quarantine write to finish (or fail) so we
        # don't race the audit row against a half-uploaded blob.
        try:
            await quarantine_task
            quarantine_ok = True
        except Exception:
            quarantine_ok = False
        if not quarantine_ok:
            # Quarantine write itself failed — there's nothing for
            # forensics to inspect, but the rejection still propagates.
            raise
        # Record the rejection. Don't delete the quarantine blob —
        # the §B4 retention sweeper handles that.
        detail = getattr(exc, "detail", None) or str(exc) or exc.__class__.__name__
        status_code = getattr(exc, "status_code", 415)
        try:
            await _audit_quarantine_rejection(
                user_id=user_id, quarantine_key=q_key, filename=filename,
                byte_count=len(raw_bytes), client_mime=client_mime,
                reason=detail, status_code=status_code,
            )
        except Exception:
            logger.exception("upload_validation: failed to audit rejection")
        raise

    # Validation passed — delete the quarantine scratch blob. It was
    # never meant to persist; the caller writes the sanitized bytes
    # to `originals` via `store_upload`.
    try:
        await asyncio.to_thread(storage.delete, storage.bucket_quarantine, q_key)
    except Exception:
        # Non-fatal: a leftover quarantine object is harmless and the
        # retention sweeper will clean it up.
        logger.debug("upload_validation: quarantine cleanup failed (best-effort)")
    return validated


async def _audit_quarantine_rejection(
    *,
    user_id: UUID,
    quarantine_key: str,
    filename: str | None,
    byte_count: int,
    client_mime: str | None,
    reason: str,
    status_code: int,
) -> None:
    """Append an `upload.quarantined` audit row so ops can see every
    rejected upload + the bytes that triggered it (via the
    quarantine_key)."""
    from backend.audit import add_audit
    from backend.db import SessionLocal

    async with SessionLocal() as session:
        await add_audit(
            session,
            user_id=user_id,
            action="upload.quarantined",
            details={
                "quarantine_key": quarantine_key,
                "filename": filename,
                "byte_count": byte_count,
                "client_mime": client_mime,
                "reason": reason,
                "status_code": status_code,
            },
        )
        await session.commit()
