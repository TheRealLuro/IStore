"""Browsable archive viewer — list the contents of an archive the
caller owns and extract a single inner file for inline preview.

This is the READ-ONLY counterpart to `backend.archive_upload` (which
explodes an upload into the library). Here the archive itself is an
already-stored `Image` row (category "other" / OOXML — a `.zip`,
`.tar`, `.tar.gz`, `.7z`, `.rar`, …); we read its bytes back from the
originals bucket and let the user browse + peek inside WITHOUT ever
extracting the whole thing to disk.

Safety posture (mirrors the upload-side guard exactly):
  - `_archive_kind` + `_open_archive` from `archive_upload` run the
    SAME zip-bomb / path-traversal / symlink / depth / entry-count
    inspection used at upload time (`_inspect_zip_safety`,
    `_inspect_tar_safety`, `_inspect_generic_member_list`). Opening
    the archive is what validates it; we never trust an entry list we
    didn't vet through that path.
  - Listing reads ONLY the central directory / member headers — no
    member is decompressed during `GET /list`.
  - Extraction is capped (`_MAX_EXTRACT_BYTES`, 50 MB) and the
    requested inner path is re-validated against traversal before we
    match it to a vetted entry.
  - Every query is scoped to the authenticated user (RLS): the image
    is loaded with `Image.user_id == user.id` + `deleted_at IS NULL`,
    the same predicate `backend.api.images._load_owned_image` uses.

7z / RAR support is opt-in via the `py7zr` / `rarfile` extras (the
`archives` extra in pyproject). When the package isn't installed the
open path raises a clean 415 with install instructions — identical to
the uploader's behavior — instead of 500ing.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.archive_upload import (
    ArchiveEntry,
    _archive_kind,
    _open_archive,
)
from backend.auth.users import current_active_user
from backend.config import settings
from backend.db import get_session
from backend.models import Image, User
from backend.storage import storage
from backend.upload_validation import UploadValidationError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/archives", tags=["archives"])


# A single extracted inner file is held fully in memory and streamed
# back, so cap it well below the per-file upload ceiling. 50 MB is
# generous for "preview a doc/image/text inside a zip" without letting
# one request balloon the API's RSS.
_MAX_EXTRACT_BYTES = 50 * 1024 * 1024

# Listing returns at most this many entries. `_open_archive` already
# rejects archives whose entry count exceeds
# `upload_max_archive_entries` (5000), so this is a second, response-
# shape cap: even a just-under-the-limit archive shouldn't ship 5000
# rows of JSON to the browser tree. Truncation is surfaced via the
# `truncated` flag so the FE can show "showing first N".
_MAX_LIST_ENTRIES = 2000

# Only these content types render inline in a browser tab. Mirrors
# `backend.api.images._INLINE_SAFE_EXACT` + the image/video/audio rule:
# everything else (SVG, HTML, octet-stream, office docs) is served with
# `Content-Disposition: attachment` so a direct navigation downloads it
# rather than executing it in the neuthek origin. The FE preview fetches
# bytes via XHR, which `attachment` doesn't affect.
_INLINE_SAFE_EXACT = {"application/pdf", "text/plain"}


class ArchiveNode(BaseModel):
    """One node in the archive contents tree.

    `is_dir` directories are synthesized from entry path prefixes (zip/
    tar/7z list files, not always their parent dirs); `size` is the
    uncompressed byte size for files and the recursive sum for dirs.
    `children` is present only on directories.
    """

    name: str
    path: str
    size: int
    is_dir: bool
    children: list["ArchiveNode"] | None = None


class ArchiveListResponse(BaseModel):
    image_id: UUID
    filename: str | None
    kind: str
    entry_count: int
    total_uncompressed: int
    truncated: bool
    tree: list[ArchiveNode]


async def _load_owned_archive(
    image_id: UUID,
    user: User,
    session: AsyncSession,
) -> Image:
    """Owner-scoped image lookup — identical predicate to
    `backend.api.images._load_owned_image` (RLS: user_id match +
    not soft-deleted). 404s on miss so we never confirm the
    existence of another user's row."""
    img = (
        await session.execute(
            select(Image).where(
                Image.id == image_id,
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archive not found")
    return img


def _resolve_blob_location(image: Image) -> tuple[str, str]:
    """Pick the (bucket, key) the archive bytes live at.

    Archives are stored verbatim in the originals bucket (`detect_magic`
    classifies them "other"/OOXML and the passthrough validator writes
    the bytes unchanged). Prefer the original; fall back to the served
    copy for hybrid-retention rows whose original was swept. Mirrors the
    bucket-selection logic in `backend.image.fetch_served`.
    """
    if image.original_blob_key is not None:
        return storage.bucket_originals, image.original_blob_key
    if image.served_blob_key is not None:
        bucket = (
            storage.bucket_originals
            if image.served_blob_key == image.original_blob_key
            else storage.bucket_served
        )
        return bucket, image.served_blob_key
    raise HTTPException(
        status.HTTP_404_NOT_FOUND, "No bytes stored for this archive"
    )


async def _read_archive_bytes(image: Image) -> bytes:
    """Pull the archive's raw bytes back from object storage.

    The MinIO `get` is blocking, so it runs in a worker thread to keep
    the event loop free.
    """
    bucket, key = _resolve_blob_location(image)
    try:
        blob = await asyncio.to_thread(storage.get, bucket, key)
    except Exception as exc:  # storage miss / network
        logger.exception("archives: could not read blob for %s", image.id)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Archive bytes are unavailable"
        ) from exc
    if not blob:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Archive is empty")
    return blob


def _plain_gzip_entries(raw: bytes, filename: str | None) -> list[ArchiveEntry]:
    """Handle a standalone (non-tar) gzip stream, e.g. `notes.txt.gz`.

    `_archive_kind` returns "tar.gz" for the `\\x1f\\x8b` magic and
    `_open_archive` opens it via `tarfile r:*`; that succeeds for a
    gzipped TAR but raises for a gzipped single file. We try the tar
    path first (in the caller) and fall back here, decompressing the
    single member with a guard against gzip-bomb expansion.

    The inner name is the archive name minus the `.gz` suffix so the
    FE shows `notes.txt` instead of `notes.txt.gz`.
    """
    # Decompress incrementally with a hard ceiling so a small `.gz`
    # can't expand into hundreds of MB in memory. The cap mirrors the
    # archive total-uncompressed ceiling used everywhere else.
    limit = settings.upload_max_archive_total_uncompressed_bytes
    out = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as gz:
            while True:
                chunk = gz.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > limit:
                    raise UploadValidationError(
                        "Gzip stream expands beyond the allowed size.", 415
                    )
    except UploadValidationError:
        raise
    except OSError as exc:
        raise UploadValidationError(
            "Gzip stream could not be read — it may be corrupted.", 415
        ) from exc
    data = out.getvalue()

    base = PurePosixPath((filename or "archive.gz").replace("\\", "/")).name
    inner = base[:-3] if base.lower().endswith(".gz") else (base + ".out")
    inner = inner or "file"
    return [
        ArchiveEntry(
            path=inner,
            size=len(data),
            data_factory=(lambda d=data: d),
        )
    ]


def _list_entries(raw: bytes, filename: str | None) -> tuple[str, list[ArchiveEntry]]:
    """Sniff the archive kind, open it through the safety-vetted
    `_open_archive`, and materialize the (already-validated) entry
    METADATA list. Returns (kind, entries).

    IMPORTANT: the returned entries' `data_factory` closures reference
    the archive handle, which we close here — so they're valid for
    `.path` / `.size` (listing) ONLY. Extraction goes through
    `_extract_one`, which reads inside the open context. This matches
    the read-only listing contract: no member is decompressed here.

    Opening is where all the zip-bomb / traversal / symlink / depth /
    entry-count checks run (they live inside `_inspect_zip_safety` /
    `_inspect_tar_safety` / `_inspect_generic_member_list`, called by
    `_open_archive`). For a standalone gzip we fall back to the
    single-member path.

    Raises `UploadValidationError` (→ 415) on any unsafe / unreadable
    archive, matching the uploader's error vocabulary.
    """
    kind, handle, iter_factory, gz_entries = _open_kind(raw, filename)
    if gz_entries is not None:
        return kind, gz_entries
    try:
        return kind, list(iter_factory())
    finally:
        try:
            handle.close()
        except Exception:
            pass


def _open_kind(raw: bytes, filename: str | None):
    """Open the archive and return `(kind, handle, iter_factory,
    gz_entries)`.

    For the standalone-gzip fallback there is no handle to keep open —
    the whole stream is decompressed eagerly — so `gz_entries` carries
    the single-member list and `handle`/`iter_factory` are None. For
    every other kind, `gz_entries` is None and the caller MUST close
    `handle` when done.
    """
    kind = _archive_kind(raw)
    if kind == "tar.gz":
        # Could be a gzipped TAR or a plain gzipped single file. Try
        # the tar path first; on failure, treat it as a lone gzip.
        try:
            handle, iter_factory = _open_archive(kind, raw)
        except UploadValidationError:
            return "gz", None, None, _plain_gzip_entries(raw, filename)
        return kind, handle, iter_factory, None
    handle, iter_factory = _open_archive(kind, raw)
    return kind, handle, iter_factory, None


def _extract_one(
    raw: bytes, filename: str | None, req_norm: str,
) -> tuple[str, bytes]:
    """Open the archive, find the entry whose normalized path equals
    `req_norm`, read its bytes INSIDE the open context, and return
    `(name, data)`.

    Enforces `_MAX_EXTRACT_BYTES` both on the header-declared size
    (before the read) and on the actual decompressed length (after),
    so a lying header can't sneak an oversized member past the cap.
    Raises `UploadValidationError` for a missing member (404) / over-
    size member (413) / unreadable archive (415).
    """
    kind, handle, iter_factory, gz_entries = _open_kind(raw, filename)
    try:
        entries = gz_entries if gz_entries is not None else list(iter_factory())
        match = next(
            (e for e in entries if e.path.replace("\\", "/").strip("/") == req_norm),
            None,
        )
        if match is None:
            raise UploadValidationError("No such file in archive", 404)
        if int(match.size or 0) > _MAX_EXTRACT_BYTES:
            raise UploadValidationError(
                f"Inner file is larger than the "
                f"{_MAX_EXTRACT_BYTES // (1024 * 1024)} MB preview limit.",
                413,
            )
        try:
            data = match.data_factory()
        except UploadValidationError:
            raise
        except Exception as exc:
            raise UploadValidationError(
                "Could not extract that file from the archive.", 415
            ) from exc
        if len(data) > _MAX_EXTRACT_BYTES:
            raise UploadValidationError(
                f"Inner file is larger than the "
                f"{_MAX_EXTRACT_BYTES // (1024 * 1024)} MB preview limit.",
                413,
            )
        name = PurePosixPath(req_norm).name or "file"
        return name, data
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


def _build_tree(entries: list[ArchiveEntry]) -> list[ArchiveNode]:
    """Turn the flat list of safe entries into a nested folder/file
    tree. Intermediate directories are synthesized from path prefixes
    (archives don't always carry explicit dir entries). Directory
    sizes are the recursive sum of their descendants. Children are
    sorted dirs-first, then case-insensitive by name.
    """
    root: dict = {"dirs": {}, "files": []}

    for entry in entries:
        # Defensive: `_open_archive` already rejected traversal, but
        # never trust a path we're about to use as a tree key.
        norm = entry.path.replace("\\", "/")
        parts = [p for p in PurePosixPath(norm).parts if p not in {"", "."}]
        if not parts or ".." in parts:
            continue
        cursor = root
        # Walk/create the directory chain for everything but the last
        # component (the file itself).
        for comp in parts[:-1]:
            nxt = cursor["dirs"].get(comp)
            if nxt is None:
                nxt = {"dirs": {}, "files": []}
                cursor["dirs"][comp] = nxt
            cursor = nxt
        cursor["files"].append((parts[-1], int(entry.size or 0), "/".join(parts)))

    def _emit(node: dict, prefix: str) -> tuple[list[ArchiveNode], int]:
        out: list[ArchiveNode] = []
        subtotal = 0
        for name in sorted(node["dirs"].keys(), key=str.lower):
            child_path = f"{prefix}{name}"
            children, size = _emit(node["dirs"][name], f"{child_path}/")
            subtotal += size
            out.append(
                ArchiveNode(
                    name=name,
                    path=child_path,
                    size=size,
                    is_dir=True,
                    children=children,
                )
            )
        files = sorted(node["files"], key=lambda f: f[0].lower())
        for fname, fsize, fpath in files:
            subtotal += fsize
            out.append(
                ArchiveNode(name=fname, path=fpath, size=fsize, is_dir=False)
            )
        return out, subtotal

    tree, _ = _emit(root, "")
    return tree


@router.get("/{image_id}/list", response_model=ArchiveListResponse)
async def list_archive(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArchiveListResponse:
    """List the contents of an archive the caller owns as a tree of
    `{name, path, size, is_dir, children}`.

    Reads ONLY the archive's central directory / member headers — no
    member is decompressed here. The open path enforces the full
    upload-side zip-bomb / traversal / symlink / depth / entry-count
    posture; an unsafe archive 415s.
    """
    image = await _load_owned_archive(image_id, user, session)
    raw = await _read_archive_bytes(image)

    # Sniffing + central-directory parse is CPU work (and for tar can
    # walk the whole header chain); run it off the event loop.
    try:
        kind, entries = await asyncio.to_thread(_list_entries, raw, image.original_filename)
    except UploadValidationError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except Exception as exc:
        logger.exception("archives: failed to list %s", image_id)
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Could not read archive: {exc.__class__.__name__}",
        ) from exc

    entry_count = len(entries)
    truncated = entry_count > _MAX_LIST_ENTRIES
    listed = entries[:_MAX_LIST_ENTRIES]
    total_uncompressed = sum(int(e.size or 0) for e in entries)
    tree = _build_tree(listed)

    return ArchiveListResponse(
        image_id=image.id,
        filename=image.original_filename,
        kind=kind,
        entry_count=entry_count,
        total_uncompressed=total_uncompressed,
        truncated=truncated,
        tree=tree,
    )


def _sniff_content_type(name: str, data: bytes) -> str:
    """Best-effort content-type for an extracted inner file.

    Prefer a magic-byte sniff for the common previewable types
    (image / pdf / text), then fall back to the extension via
    `mimetypes`, then octet-stream. We deliberately DON'T return
    `image/svg+xml` or `text/html` as inline-safe — `_serve_headers`
    forces those to download regardless, but normalizing them to
    octet-stream here keeps the FE from trying to render them.
    """
    head = data[:64]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    # Extension-based fallback.
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        # Don't hand back actively-dangerous inline types.
        if guessed in {"image/svg+xml", "text/html", "application/xhtml+xml"}:
            return "application/octet-stream"
        return guessed
    # Content sniff for text: if the first 4 KiB is NUL-free valid
    # UTF-8, treat it as plain text so the FE can render it.
    sample = data[:4096]
    if b"\x00" not in sample:
        try:
            sample.decode("utf-8")
            return "text/plain; charset=utf-8"
        except UnicodeDecodeError:
            pass
    return "application/octet-stream"


def _serve_headers(mime: str, filename: str) -> dict:
    """Inline-vs-attachment decision, mirroring
    `backend.api.images._serve_headers`. Images / audio / video / pdf /
    plain-text render inline; everything else downloads. The filename
    is sanitized into the Content-Disposition so a crafted inner name
    can't inject header bytes."""
    base = (mime or "").split(";", 1)[0].strip()
    safe = (
        (base.startswith("image/") and base != "image/svg+xml")
        or base.startswith("video/")
        or base.startswith("audio/")
        or base in _INLINE_SAFE_EXACT
    )
    # Sanitize the download filename: strip path + CR/LF/quote so it's
    # a clean header token.
    clean_name = PurePosixPath(filename.replace("\\", "/")).name or "file"
    clean_name = clean_name.replace('"', "").replace("\r", "").replace("\n", "")
    disposition = "inline" if safe else "attachment"
    return {
        "Content-Disposition": f'{disposition}; filename="{clean_name}"',
        "Cache-Control": "private, max-age=0, no-store",
        "X-Content-Type-Options": "nosniff",
    }


@router.get("/{image_id}/file")
async def extract_archive_file(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    path: Annotated[str, Query(min_length=1, max_length=4096)],
) -> Response:
    """Extract and stream back ONE inner file by its `path`, so the
    frontend can preview it (image / text / pdf) or download it.

    Size-capped at `_MAX_EXTRACT_BYTES` (50 MB). The requested path is
    re-validated against traversal and then matched against the
    safety-vetted entry list (we never extract by raw user path — only
    a path that exactly matches an entry the open-time inspection
    already cleared). Content-type is sniffed so previewable types
    render inline and everything else downloads.
    """
    image = await _load_owned_archive(image_id, user, session)
    raw = await _read_archive_bytes(image)

    # Re-validate the requested inner path BEFORE touching the archive.
    req = path.replace("\\", "/").strip("/")
    req_parts = [p for p in PurePosixPath(req).parts if p not in {"", "."}]
    if (
        not req
        or req.startswith("/")
        or ".." in req_parts
        or "\x00" in req
        or any(ord(c) < 0x20 for c in req)
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid inner path")
    req_norm = "/".join(req_parts)

    # Open + locate + read the single member, all inside one off-loop
    # call (the archive handle must stay alive across the read, so we
    # can't reuse the listing path which closes it). `_extract_one`
    # enforces the size cap before AND after decompression.
    try:
        name, data = await asyncio.to_thread(
            _extract_one, raw, image.original_filename, req_norm
        )
    except UploadValidationError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except Exception as exc:
        logger.exception("archives: extract failed for %s!%s", image_id, req_norm)
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Could not read archive: {exc.__class__.__name__}",
        ) from exc

    mime = _sniff_content_type(name, data)
    headers = _serve_headers(mime, name)
    headers["Content-Length"] = str(len(data))
    return Response(content=data, media_type=mime, headers=headers)
