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
    ".ml": "text/x-ocaml",
    ".mli": "text/x-ocaml",
    ".nim": "text/x-nim",
    ".zig": "text/x-zig",
    ".v": "text/x-v",
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
    ".properties": "text/x-properties",
    ".xml": "text/xml",
    ".plist": "text/xml",
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
        raise UploadValidationError("Archive uploads are not enabled yet.", 415)
    # ISO Base Media File Format — mp4 / mov / m4a / m4v. The first
    # four bytes are a 32-bit box size, followed by `ftyp` and a 4-char
    # brand. Brand → MIME table catches the common cases; unknown
    # brands fall back to the filename extension when one matches.
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in _ISO_BMFF_BRANDS:
            mime = _ISO_BMFF_BRANDS[brand]
            return mime, "audio" if mime.startswith("audio/") else "video"
        ext = _suffix(filename)
        if ext in _VIDEO_EXTS:
            return _VIDEO_EXTS[ext], "video"
        if ext in _AUDIO_EXTS:
            return _AUDIO_EXTS[ext], "audio"
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
    if lower.startswith((b"<svg", b"<?xml")) and b"<svg" in lower[:256]:
        raise UploadValidationError("SVG uploads are not accepted.", 415)
    if lower.startswith((b"<!doctype html", b"<html", b"<script")):
        raise UploadValidationError("HTML/script uploads are not accepted.", 415)
    if _suffix(filename) in _TEXT_EXTS and _looks_text(data):
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
    raise UploadValidationError("Unsupported or unrecognized file type.", 415)


def _text_mime(filename: str | None) -> str:
    ext = _suffix(filename)
    if ext == ".md":
        return "text/markdown"
    if ext == ".csv":
        return "text/csv"
    if ext == ".json":
        return "application/json"
    return "text/plain"


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


def _validate_document(raw_bytes: bytes, filename: str | None, detected_mime: str, category: str) -> ValidatedUpload:
    # OOXML containers get a zip-shape inspection (entry count, path
    # traversal, depth, ratio, symlinks). Text-shaped documents get
    # script tag rejection. PDFs aren't introspected past magic bytes
    # — that's a deliberate trust line for now.
    if detected_mime in _OOXML.values():
        _inspect_ooxml(raw_bytes, filename)
    if detected_mime.startswith("text/") or detected_mime == "application/json":
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
