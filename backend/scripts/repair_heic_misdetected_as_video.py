"""One-off repair: iPhone HEIC photos misdetected as videos.

Background
---------
HEIC/HEIF files share the ISO-BMFF ``ftyp`` container with MP4, so the
OLD ``detect_magic`` returned ``video/mp4`` for them. Those uploads were
stored with ``category='video'``, ``mime_type_original='video/mp4'``,
pushed through the video transcoder, and ended up with ``served_blob_key``
pointing at an HLS playlist (``master.m3u8``) plus a garbage poster frame
(the B&W blob the gallery shows). ``detect_magic`` is now fixed so NEW
uploads are correct; this script repairs the EXISTING rows.

The ORIGINAL bytes in ``original_blob_key`` are intact, valid HEIC, so we
re-run them through the SAME image pipeline a normal upload uses
(``codecs.compress`` with the default plan) to produce a browser-safe
served image, then flip the row back to an image and delete the stale HLS
artifacts.

Scope (the misclassified set, ALL users):
    category = 'video'
    AND lower(original_filename) ends in .heic / .heif / .hif
    AND deleted_at IS NULL
    AND original_blob_key IS NOT NULL   (need the source bytes)

Idempotent: a repaired row becomes ``category='image'`` and no longer
matches the scope, so re-running skips it. Each row is repaired in its
own transaction under the owning user's RLS context.

Run it::

    docker exec -i neuthek-backend python - < \
        backend/scripts/repair_heic_misdetected_as_video.py

or copy it in and run it as a module. It imports ``backend.codecs`` at
module load, which registers the pillow-heif opener so ``PIL.Image.open``
decodes HEIC.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from io import BytesIO
from uuid import uuid4

# Importing backend.codecs runs pillow_heif.register_heif_opener(), which
# is what makes PIL.Image.open() decode HEIC bytes. Verified empirically:
# in a process where this import has run, PIL.Image.open(BytesIO(heic))
# returns a real HEIF image (format='HEIF'), so no codecs.py fallback is
# needed. Keep this import FIRST so the opener is registered before any
# decode happens.
from backend.codecs import compress, pick_default_plan  # noqa: E402

from PIL import Image as PILImage  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.db import SessionLocal  # noqa: E402
from backend.storage import storage  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("repair_heic")

# Candidate selection. Cast user_id to text for logging convenience; the
# UPDATE re-reads the row inside the per-user RLS context.
_SELECT_CANDIDATES = text(
    """
    SELECT id::text          AS id,
           user_id::text     AS user_id,
           original_blob_key AS original_blob_key,
           original_filename AS original_filename,
           served_blob_key   AS served_blob_key,
           served_variants   AS served_variants
    FROM images
    WHERE category = 'video'
      AND (
            lower(original_filename) LIKE '%.heic'
         OR lower(original_filename) LIKE '%.heif'
         OR lower(original_filename) LIKE '%.hif'
      )
      AND deleted_at IS NULL
      AND original_blob_key IS NOT NULL
    ORDER BY uploaded_at
    """
)

# Per-row update. We re-check ``category = 'video'`` in the WHERE so a
# concurrent/duplicate run can't double-process a row that's already been
# flipped to 'image'. RETURNING lets us confirm exactly one row changed.
_UPDATE_ROW = text(
    """
    UPDATE images
    SET category          = 'image',
        mime_type_original = 'image/heic',
        mime_type_served   = :served_mime,
        served_blob_key    = :served_key,
        byte_size_served   = :served_size,
        width              = :width,
        height             = :height,
        codec              = :codec,
        quality            = :quality,
        max_dim            = :max_dim,
        lossless           = :lossless,
        -- Clear all video / HLS state. There is no duration/transcode
        -- column on this table; video state lives entirely in these two.
        served_variants    = NULL,
        thumbnail_blob_key = NULL,
        -- These are real photos of people: re-run image summary + faces.
        pending_summary    = TRUE,
        pending_face_scan  = TRUE
    WHERE id = :image_id
      AND category = 'video'
    RETURNING id
    """
)


def _hls_prefix_for(served_blob_key: str | None, served_variants) -> str | None:
    """Resolve the single MinIO prefix that holds the whole HLS bundle.

    Primary source is ``served_variants['hls_prefix']`` (written by the
    transcode worker). Fallback: strip the trailing ``/master.m3u8`` off
    ``served_blob_key`` (which always pointed at the master manifest).
    """
    if isinstance(served_variants, dict):
        p = served_variants.get("hls_prefix")
        if p:
            return p.rstrip("/")
    if served_blob_key and served_blob_key.endswith("/master.m3u8"):
        return served_blob_key[: -len("/master.m3u8")]
    return None


def _delete_hls_artifacts(prefix: str) -> int:
    """Delete every object under the HLS prefix (master.m3u8, rendition
    playlists, .ts segments, poster.jpg). Returns the count removed.

    Best-effort: a leftover blob is harmless (it's just orphaned bytes),
    but we try to remove the whole bundle so storage isn't wasted.
    """
    removed = 0
    try:
        objs = list(
            storage.client.list_objects(
                storage.bucket_served, prefix=prefix + "/", recursive=True
            )
        )
    except Exception:
        logger.exception("  list_objects failed for prefix %s", prefix)
        return 0
    for obj in objs:
        try:
            storage.delete(storage.bucket_served, obj.object_name)
            removed += 1
        except Exception:
            logger.exception("  failed to delete %s", obj.object_name)
    return removed


def _build_served_image(raw: bytes) -> tuple[bytes, str, str, int, int, object]:
    """Re-run HEIC bytes through the SAME image pipeline as a normal
    upload: decode for dimensions, then ``codecs.compress`` with the
    default plan (WebP q=82, 4096px cap). Returns
    (served_bytes, served_mime, codec, width, height, plan).
    """
    with PILImage.open(BytesIO(raw)) as im:
        im.load()
        width, height = im.size
    plan = pick_default_plan(
        input_format="image/heic", width=width, height=height, byte_size=len(raw)
    )
    served_bytes = compress(raw, plan)
    return served_bytes, plan.mime, plan.codec, width, height, plan


async def _repair_one(row) -> bool:
    """Repair a single row. Returns True if the row was repaired.

    Order of operations (so a crash never leaves a row pointing at a blob
    that doesn't exist):
      1. Fetch original HEIC bytes.
      2. Re-encode to the served image variant + put the new blob.
      3. In ONE transaction under the owner's RLS context, flip the row.
      4. Only after commit: delete the stale HLS artifacts + enqueue
         summary/face jobs.
    """
    image_id = row.id
    user_id = row.user_id
    fname = row.original_filename

    # 1) Original bytes.
    try:
        raw = await asyncio.to_thread(
            storage.get, storage.bucket_originals, row.original_blob_key
        )
    except Exception:
        logger.exception("[%s] %s: could not fetch original; skipping", image_id, fname)
        return False

    # 2) Re-encode through the real pipeline.
    try:
        served_bytes, served_mime, codec, width, height, plan = await asyncio.to_thread(
            _build_served_image, raw
        )
    except Exception:
        logger.exception("[%s] %s: re-encode failed; skipping", image_id, fname)
        return False

    served_key = f"users/{user_id}/served/{uuid4().hex}.{plan.extension}"
    try:
        await asyncio.to_thread(
            storage.put,
            storage.bucket_served,
            served_key,
            served_bytes,
            served_mime,
        )
    except Exception:
        logger.exception("[%s] %s: served blob put failed; skipping", image_id, fname)
        return False

    # 3) Flip the row, transactionally, under the owner's RLS context.
    async with SessionLocal() as session:
        # Transaction-local GUC (is_local=true) so RLS USING/WITH CHECK
        # match this owner for the UPDATE; it resets at COMMIT.
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": user_id},
        )
        result = await session.execute(
            _UPDATE_ROW,
            {
                "image_id": image_id,
                "served_mime": served_mime,
                "served_key": served_key,
                "served_size": len(served_bytes),
                "width": width,
                "height": height,
                "codec": codec,
                "quality": plan.quality,
                "max_dim": plan.max_dim,
                "lossless": bool(plan.lossless),
            },
        )
        updated = result.fetchone()
        if updated is None:
            # Already repaired by a prior/parallel run (no longer
            # category='video'). Roll back and clean up the orphan blob
            # we just wrote so we don't leak storage.
            await session.rollback()
            logger.info(
                "[%s] %s: already repaired (skipped); removing unused new blob",
                image_id, fname,
            )
            try:
                await asyncio.to_thread(storage.delete, storage.bucket_served, served_key)
            except Exception:
                pass
            return False
        await session.commit()

    logger.info(
        "[%s] %s: repaired -> image, %dx%d, served=%s (%d bytes, %s)",
        image_id, fname, width, height, served_mime, len(served_bytes), codec,
    )

    # 4) Post-commit side effects. Delete stale HLS bundle.
    prefix = _hls_prefix_for(row.served_blob_key, row.served_variants)
    if prefix:
        removed = await asyncio.to_thread(_delete_hls_artifacts, prefix)
        logger.info("[%s] removed %d stale HLS artifact(s) under %s", image_id, removed, prefix)
    else:
        logger.warning(
            "[%s] could not resolve HLS prefix (served=%s); no artifacts deleted",
            image_id, row.served_blob_key,
        )

    # Enqueue real face scan + summary (face-first, mirrors the upload
    # path for people photos). Best-effort: if Redis is down the job just
    # doesn't run; the pending_* flags are already set so a later requeue
    # picks it up.
    try:
        from backend import jobs as job_q
        from uuid import UUID

        await job_q.enqueue_face_scan_then_summarize(UUID(user_id), UUID(image_id))
    except Exception:
        logger.exception("[%s] enqueue face/summary failed (flags still set)", image_id)

    return True


async def main() -> None:
    # Read the candidate list with an RLS bypass (documented maintenance
    # GUC) so we can see rows across all users in one pass. Each row is
    # then repaired under its OWNER's RLS context in _repair_one.
    async with SessionLocal() as session:
        await session.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
        rows = (await session.execute(_SELECT_CANDIDATES)).fetchall()

    logger.info("Found %d HEIC-misdetected-as-video row(s) to repair.", len(rows))
    repaired = 0
    failed = 0
    for row in rows:
        try:
            if await _repair_one(row):
                repaired += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            logger.exception("[%s] unexpected error during repair", row.id)

    logger.info(
        "Done. repaired=%d, skipped/failed=%d, total=%d",
        repaired, failed, len(rows),
    )


if __name__ == "__main__":
    asyncio.run(main())
