from __future__ import annotations

import asyncio
import html
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from PIL import Image as PILImage

from backend.config import settings
from backend.storage import storage


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
_OOXML = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
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
        return "image/tiff", "image"
    if data.startswith(b"%PDF-"):
        return "application/pdf", "document"
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"):
        ext = _suffix(filename)
        if ext in _OOXML:
            return _OOXML[ext], "document"
        raise UploadValidationError("Archive uploads are not enabled yet.", 415)
    if lower.startswith((b"<svg", b"<?xml")) and b"<svg" in lower[:256]:
        raise UploadValidationError("SVG uploads are not accepted.", 415)
    if lower.startswith((b"<!doctype html", b"<html", b"<script")):
        raise UploadValidationError("HTML/script uploads are not accepted.", 415)
    if _suffix(filename) in _TEXT_EXTS and _looks_text(data):
        return _text_mime(filename), "document"
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


def _inspect_ooxml(data: bytes, filename: str | None) -> None:
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > settings.upload_max_archive_entries:
                raise UploadValidationError("Document archive has too many entries.", 415)
            compressed = sum(max(i.compress_size, 0) for i in infos) or 1
            uncompressed = sum(max(i.file_size, 0) for i in infos)
            if uncompressed > compressed * settings.upload_max_archive_ratio:
                raise UploadValidationError("Document archive expansion ratio is too high.", 415)
            names = {i.filename for i in infos}
            if "[Content_Types].xml" not in names:
                raise UploadValidationError("Office document is missing content type metadata.", 415)
            for info in infos:
                name = info.filename.replace("\\", "/")
                path = PurePosixPath(name)
                parts = [p for p in path.parts if p not in {"", "."}]
                if name.startswith("/") or ".." in parts:
                    raise UploadValidationError("Document archive contains an unsafe path.", 415)
                if len(parts) > settings.upload_max_archive_depth:
                    raise UploadValidationError("Document archive nesting is too deep.", 415)
                # Unix symlink bit in external_attr.
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise UploadValidationError("Document archive contains a symlink.", 415)
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
                out = BytesIO()
                pil.save(out, format="GIF")
                return out.getvalue(), width, height, detected_mime
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
    if category == "image":
        sanitized, w, h, sanitized_mime = _sanitize_image(raw_bytes, detected_mime)
        return ValidatedUpload(
            filename, sanitized, raw_bytes, sanitized_mime, "image", w, h,
        )
    if detected_mime in _OOXML.values():
        _inspect_ooxml(raw_bytes, filename)
    if detected_mime.startswith("text/") or detected_mime == "application/json":
        _reject_scriptable_text(raw_bytes)
    return ValidatedUpload(filename, raw_bytes, raw_bytes, detected_mime, category)


async def validate_upload(
    *,
    user_id: UUID,
    filename: str | None,
    raw_bytes: bytes,
    client_mime: str | None,
) -> ValidatedUpload:
    if not raw_bytes:
        raise UploadValidationError("Empty upload", 400)
    if len(raw_bytes) > settings.upload_max_bytes:
        raise UploadValidationError("Upload exceeds the per-file size limit.", 413)

    q_key = f"users/{user_id}/quarantine/{uuid4().hex}/{filename or 'upload'}"
    # Parallelize: kick the quarantine write off in a worker thread so
    # the PIL re-decode + re-encode below runs at the same time. They
    # used to be sequential, costing 200-1500 ms per upload on top of
    # the inevitable Pillow work.
    quarantine_task = asyncio.to_thread(
        storage.put,
        storage.bucket_quarantine, q_key, raw_bytes,
        client_mime or "application/octet-stream",
    )
    try:
        # Validate body runs in a worker thread — PIL pixel ops (verify,
        # decode, re-encode) hold the GIL, so doing this on the asyncio
        # event loop blocks every other request for the duration of the
        # encode. The thread offload keeps the API responsive even when
        # several uploads land at once.
        validated, _q = await asyncio.gather(
            asyncio.to_thread(_validate_sync, user_id, filename, raw_bytes, client_mime),
            quarantine_task,
        )
        return validated
    except Exception:
        # Make sure the quarantine task is awaited (or cancelled) even
        # if validation raised, so we don't leak a half-uploaded blob.
        try:
            await quarantine_task
        except Exception:
            pass
        raise
    finally:
        # Best-effort cleanup of the quarantine blob. Run off-thread
        # because storage.delete is also synchronous MinIO.
        try:
            await asyncio.to_thread(storage.delete, storage.bucket_quarantine, q_key)
        except Exception:
            pass
