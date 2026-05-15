"""§C1.5 — general archive uploader.

Accepts a single multipart upload of a zip or tar (`.zip`, `.tar`,
`.tar.gz` / `.tgz`, `.tar.bz2`, `.tar.xz`) and routes every entry
through the standard `store_upload` pipeline so MIME / magic-bytes /
Pillow re-encode / quarantine / bandit-compression all apply per-entry.

Pre-extraction inspection reuses `_inspect_zip_safety` from
`upload_validation` (and a mirrored tar pass below) so zip-bomb
detection uses identical constants on every ZIP-shaped surface.

Why not stream extract?
  We hold the archive in memory and process entries one at a time. The
  outer per-file cap (`upload_max_bytes`, 200 MB by default) plus the
  expansion-ratio gate (≤ 5×) bound peak memory. Streaming the source
  through a tempfile would help only for archives larger than the cap,
  which we reject up-front anyway.

7z / RAR support:
  Not in scope here — would need optional `py7zr` / `rarfile` extras.
  The endpoint returns a clear 415 with the supported formats; adding
  7z / RAR is a follow-up that hooks into `_dispatch_archive`.
"""
from __future__ import annotations

import asyncio
import io
import logging
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Iterable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.image import store_upload
from backend.models import Folder, User
from backend.upload_validation import (
    UploadValidationError,
    _inspect_zip_safety,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchiveEntry:
    """Normalized view of one extractable entry from a zip or tar."""
    path: str
    size: int
    data_factory: Callable[[], bytes]  # called once during extraction


@dataclass(frozen=True)
class ArchiveExtractResult:
    folder_id: UUID
    source_archive_id: UUID
    accepted: int
    rejected: int
    skipped_dirs: int
    rejected_details: list[dict]


# Magic-byte sniffing for the supported archive containers. The first
# match wins; we don't fall through to "try every parser" because that
# would let a polyglot pass both gates with different parsed shapes.
def _archive_kind(data: bytes) -> str:
    head = data[:6]
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06":
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "tar.gz"
    if head[:3] == b"BZh":
        return "tar.bz2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "tar.xz"
    # Plain tar: ustar magic sits at byte 257 within the header.
    if len(data) >= 263 and data[257:262] in (b"ustar", b"ustar\x00"):
        return "tar"
    # Known-but-unsupported archive shapes: 7z and RAR. Surface a clear
    # error rather than a generic "unsupported."
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        raise UploadValidationError(
            "7z archives are not supported yet — repack as .zip or .tar.gz.",
            415,
        )
    if head[:4] == b"Rar!":
        raise UploadValidationError(
            "RAR archives are not supported yet — repack as .zip or .tar.gz.",
            415,
        )
    raise UploadValidationError(
        "Unrecognized archive format. Supported: .zip, .tar, .tar.gz, .tar.bz2, .tar.xz.",
        415,
    )


def _path_is_safe(name: str) -> bool:
    """Reject absolute paths and `..` components.

    Identical predicate to `_inspect_zip_safety`'s loop, mirrored here
    for tar (which doesn't use ZipInfo). Returns True iff the path is
    safe to use as a relative entry name.
    """
    norm = name.replace("\\", "/")
    if norm.startswith("/"):
        return False
    parts = [p for p in PurePosixPath(norm).parts if p not in {"", "."}]
    return ".." not in parts


def _inspect_tar_safety(tf: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Tar-equivalent of `_inspect_zip_safety`. Same constants, same
    error vocabulary.

    Tar files don't expose a single compressed-size field per member
    (the *file* compression — gzip / bzip2 / xz — wraps the whole
    stream). For the ratio check we compare uncompressed total against
    the on-disk archive size measured at call time; that's strictly
    looser than zip's per-entry ratio but still catches catastrophic
    expansion ratios.
    """
    infos = tf.getmembers()
    if len(infos) > settings.upload_max_archive_entries:
        raise UploadValidationError("Archive has too many entries.", 415)
    for info in infos:
        if not _path_is_safe(info.name):
            raise UploadValidationError("Archive contains an unsafe path.", 415)
        parts = [
            p for p in PurePosixPath(info.name.replace("\\", "/")).parts
            if p not in {"", "."}
        ]
        if len(parts) > settings.upload_max_archive_depth:
            raise UploadValidationError("Archive nesting is too deep.", 415)
        if info.issym() or info.islnk():
            raise UploadValidationError("Archive contains a symlink.", 415)
        if info.isdev() or info.isfifo() or info.ischr() or info.isblk():
            raise UploadValidationError("Archive contains a device or FIFO entry.", 415)
    return infos


def _iter_zip_entries(zf: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> Iterable[ArchiveEntry]:
    for info in infos:
        # Skip directory entries — they exist only to preserve empty
        # folders, which the upload pipeline doesn't model.
        if info.is_dir():
            continue
        yield ArchiveEntry(
            path=info.filename.replace("\\", "/"),
            size=int(info.file_size or 0),
            data_factory=(lambda i=info: zf.read(i)),
        )


def _iter_tar_entries(tf: tarfile.TarFile, infos: list[tarfile.TarInfo]) -> Iterable[ArchiveEntry]:
    for info in infos:
        if not info.isfile():
            continue
        member = info
        def _read(m=member, _tf=tf) -> bytes:
            extracted = _tf.extractfile(m)
            if extracted is None:
                return b""
            try:
                return extracted.read()
            finally:
                extracted.close()
        yield ArchiveEntry(
            path=info.name.replace("\\", "/"),
            size=int(info.size or 0),
            data_factory=_read,
        )


def _open_archive(kind: str, raw: bytes):
    """Open the archive for streaming reads + return (handle, entry_iter
    factory, total_uncompressed_bytes_optional). Caller is responsible
    for closing the handle.
    """
    if kind == "zip":
        zf = zipfile.ZipFile(io.BytesIO(raw))
        infos = _inspect_zip_safety(zf, error_label="Archive")
        return zf, lambda: _iter_zip_entries(zf, infos)
    # All tar flavors go through tarfile with `r:*` mode autodetect.
    try:
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
    except tarfile.TarError as exc:
        raise UploadValidationError(
            "Tar archive could not be opened — it may be corrupted.", 415
        ) from exc
    try:
        infos = _inspect_tar_safety(tf)
    except UploadValidationError:
        tf.close()
        raise
    # Ratio check for tar: total uncompressed bytes / wire size.
    total_uncompressed = sum(int(i.size or 0) for i in infos)
    compressed = max(len(raw), 1)
    if total_uncompressed > compressed * settings.upload_max_archive_ratio:
        tf.close()
        raise UploadValidationError("Archive expansion ratio is too high.", 415)
    return tf, lambda: _iter_tar_entries(tf, infos)


def _stem_from_filename(filename: str | None) -> str:
    """Pull the bare archive stem to seed the auto-created folder name.

    `photos.tar.gz` → `photos`; `vacation.zip` → `vacation`. Falls back
    to a uuid-derived label when the upload had no usable name.
    """
    if not filename:
        return f"archive-{uuid4().hex[:8]}"
    base = PurePosixPath(filename.replace("\\", "/")).name
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            break
    else:
        # PurePosixPath.stem handles the single-extension case.
        base = PurePosixPath(base).stem or base
    base = base.strip() or f"archive-{uuid4().hex[:8]}"
    if len(base) > 200:
        base = base[:200].rstrip()
    return base


async def _create_archive_folder(
    session: AsyncSession,
    user: User,
    parent_folder_id: UUID | None,
    name: str,
    source_archive_id: UUID,
) -> Folder:
    """Create the destination folder for an archive extraction, retrying
    with a numeric suffix on name collision.

    The `(user_id, parent, lower(name))` partial unique index will
    409 a duplicate — we resolve that automatically since the user
    didn't pick this name themselves.
    """
    from sqlalchemy.exc import IntegrityError

    candidate = name
    for attempt in range(20):
        folder = Folder(
            user_id=user.id,
            parent_folder_id=parent_folder_id,
            name=candidate,
            source_archive_id=source_archive_id,
        )
        session.add(folder)
        try:
            await session.flush()
            return folder
        except IntegrityError:
            await session.rollback()
            candidate = f"{name} ({attempt + 2})"
    raise UploadValidationError(
        "Could not pick a unique folder name for the archive.", 409
    )


async def extract_archive_into_folder(
    *,
    session: AsyncSession,
    user: User,
    raw_bytes: bytes,
    filename: str | None,
    parent_folder_id: UUID | None,
) -> ArchiveExtractResult:
    """Validate the archive, create a destination folder, and route every
    entry through `store_upload`. Returns counts the route handler turns
    into the API response.

    The wider archive size cap (`upload_max_bytes`) was enforced upstream;
    we trust it here and focus on per-entry policy.
    """
    if not raw_bytes:
        raise UploadValidationError("Empty upload", 400)
    kind = _archive_kind(raw_bytes)

    handle, iter_factory = _open_archive(kind, raw_bytes)
    try:
        entries = list(iter_factory())

        # The wire-level safety pass already enforced entry count, depth,
        # path traversal, and symlinks. The per-entry size cap is the
        # standard `upload_max_bytes` — applied below so each entry is
        # treated as if it had been uploaded directly.
        source_archive_id = uuid4()
        stem = _stem_from_filename(filename)
        folder = await _create_archive_folder(
            session, user, parent_folder_id, stem, source_archive_id
        )

        accepted = 0
        rejected = 0
        skipped_dirs = 0
        rejected_details: list[dict] = []

        for entry in entries:
            if entry.size > settings.upload_max_bytes:
                rejected += 1
                rejected_details.append({
                    "path": entry.path,
                    "reason": "Entry exceeds the per-file size limit.",
                    "status": 413,
                })
                continue

            try:
                data = await asyncio.to_thread(entry.data_factory)
            except Exception as exc:
                rejected += 1
                rejected_details.append({
                    "path": entry.path,
                    "reason": f"Could not extract entry: {exc.__class__.__name__}",
                    "status": 415,
                })
                continue

            if not data:
                # Empty file inside an otherwise-valid archive. Skip
                # rather than 400 — these come up a lot in real archives
                # (placeholder files, `.gitkeep`, etc.) and aren't worth
                # rejecting the whole upload over.
                skipped_dirs += 1
                continue

            entry_name = PurePosixPath(entry.path).name or "upload"
            try:
                image = await store_upload(
                    session,
                    user,
                    entry_name,
                    data,
                    None,  # No client MIME — let detect_magic decide.
                )
            except UploadValidationError as exc:
                rejected += 1
                rejected_details.append({
                    "path": entry.path,
                    "reason": exc.detail,
                    "status": exc.status_code,
                })
                continue
            except Exception as exc:
                logger.exception("archive upload: store_upload failed for %s", entry.path)
                rejected += 1
                rejected_details.append({
                    "path": entry.path,
                    "reason": f"Internal error: {exc.__class__.__name__}",
                    "status": 500,
                })
                continue

            image.folder_id = folder.id
            accepted += 1

        await session.commit()
        await session.refresh(folder)
        return ArchiveExtractResult(
            folder_id=folder.id,
            source_archive_id=source_archive_id,
            accepted=accepted,
            rejected=rejected,
            skipped_dirs=skipped_dirs,
            rejected_details=rejected_details,
        )
    finally:
        try:
            handle.close()
        except Exception:
            pass
