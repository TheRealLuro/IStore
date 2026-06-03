"""ML worker entry point.

Loops on the Redis job queue (`backend/jobs.py`). For each job:
  - `summarize`                 → run Florence-2 + Qwen rewrite, write
                                  summary to DB.
  - `face_scan`                 → run RetinaFace + ArcFace, persist
                                  Face / FaceDetection rows.
  - `face_scan_then_summarize`  → face scan first (so the summarizer
                                  sees the named-people splice), then
                                  summarize. Same sequencing the old
                                  in-API `_run_face_scan_then_summarize`
                                  used.

Models load lazily on first call via `backend.vision.runtime` (cached
afterwards). One worker process keeps the models warm across jobs;
running multiple worker replicas would cost N× the RAM with no
parallelism gain because torch ops compete for the same CPU.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.jobs import JOB_QUEUE_KEY

logger = logging.getLogger(__name__)

_RUNNING = True


async def _reaper_loop(redis_client: aioredis.Redis) -> None:
    """Periodically requeue orphaned in-flight jobs + clear leaked dedupe
    keys. Runs alongside the main consume loop in the same process so a
    single worker is self-healing even with no API container help.

    Each pass is fully guarded — a reaper failure never touches the
    consume loop. The interval is a fraction of the visibility timeout so
    an orphaned job is reclaimed within ~1.x the timeout, not 2x.
    """
    from backend.jobs import reap_dedupe_keys, reap_inflight

    vis = int(getattr(settings, "job_visibility_timeout_seconds", 1800))
    interval = max(30, vis // 4)
    # Small initial delay so the first pass doesn't fire during model warmup.
    await asyncio.sleep(min(interval, 60))
    while _RUNNING:
        try:
            await reap_inflight()
        except Exception:
            logger.exception("worker.reaper: reap_inflight crashed")
        try:
            await reap_dedupe_keys()
        except Exception:
            logger.exception("worker.reaper: reap_dedupe_keys crashed")
        # Sleep in short slices so shutdown is responsive.
        slept = 0
        while _RUNNING and slept < interval:
            await asyncio.sleep(min(5, interval - slept))
            slept += 5


def _handle_signal(signum: int, _frame: Any) -> None:
    global _RUNNING
    logger.info("worker: signal %s received, draining", signum)
    _RUNNING = False


async def _process_summarize(session_factory, image_id: UUID) -> None:
    from backend.summarize import summarize_image_id

    async with session_factory() as s:
        await summarize_image_id(s, image_id)

    # #174 — visual near-duplicate dedup. Runs AFTER summarize has
    # persisted its embedding so the row's CLIP vector is in place. The
    # image-space `clip_embedding` itself is written at UPLOAD time (see
    # backend/image.py::store_upload); by the time the summarize job runs
    # it's already committed on the row, so this read-then-merge is safe.
    # Fully guarded — a dedup failure must never affect the summarize
    # result that just landed.
    try:
        await _dedup_image_against_oldest(session_factory, image_id)
    except Exception:
        logger.exception("worker.dedup: failed for %s", image_id)


# #174 — embedding-based visual dedup threshold. Cosine SIMILARITY in
# [0,1]; the CLIP vectors are L2-normalised (vision/pipeline.py) so
# similarity == 1 - cosine_distance, matching the search code's
# `sim = 1.0 - dist` convention (api/search.py::_clip_search). 0.98 is
# deliberately conservative — it fires only on near-IDENTICAL images
# (the iCloud "lp_image.heic" logo saved 33× case), NOT on merely
# similar scenes. Raising it makes dedup stricter; lowering it risks
# false merges of distinct-but-alike photos.
#
# Two-tier, FILENAME-AWARE rule so we catch re-saves of the SAME file
# without merging genuinely-distinct burst shots (e.g. IMG_9852 vs
# IMG_9853 sit at ~0.98 cosine but are sequential captures the user wants
# to keep):
#   * EXACT     — cosine >= 0.995 merges regardless of filename (a true
#     duplicate / the same photo re-imported under a different name);
#   * SAME-NAME — cosine >= 0.98 merges ONLY when the base filename matches
#     (the "same logo saved many times as lp_image.heic" case).
DEDUP_COSINE_THRESHOLD = 0.98     # same-base-filename re-saves
DEDUP_EXACT_THRESHOLD = 0.995     # near-exact dup, any filename


async def _dedup_image_against_oldest(session_factory, image_id: UUID) -> None:
    """If an OLDER near-identical image of the same user already exists,
    fold the just-processed image into it.

    Mirrors the content-hash dedup in cloud_sync.py (the `dup_image_id`
    block ~L2202): repoint every CloudFile off the duplicate onto the
    surviving image, THEN soft-delete the duplicate. Because the next
    sync's skip-check (cloud_sync.py ~L1970-1984) only skips a remote
    file when its CloudFile points at a LIVE (deleted_at IS NULL) image,
    repointing to the survivor — which we never touch the blob of — is
    what stops the same logo re-importing on every sync.

    Guards:
      * same user only;
      * skip entirely if the image has no `clip_embedding`;
      * pick the OLDEST other match and only merge when it is strictly
        older than this image (uploaded_at, id tiebreak) — so we always
        keep the original and delete the newcomer, never the reverse;
      * never reads or mutates the survivor's blob.
    """
    from datetime import datetime, timezone

    from sqlalchemy import update as sa_update

    from backend.models import CloudFile, Image

    async with session_factory() as s:
        x = (
            await s.execute(select(Image).where(Image.id == image_id))
        ).scalar_one_or_none()
        if x is None or x.deleted_at is not None:
            return
        if x.clip_embedding is None:
            # No visual vector (e.g. skip_ai_training source, or a
            # non-image category that never gets a CLIP embedding) —
            # nothing to compare against. Hash-dedup already covers
            # byte-identical files; embedding dedup simply doesn't apply.
            return

        # Cosine distance against the SAME column + operator the semantic
        # search uses (Image.clip_embedding.cosine_distance). Threshold in
        # distance space: similarity >= T  <=>  distance <= (1 - T).
        import re as _re

        from sqlalchemy import and_, func, or_

        samename_max = 1.0 - DEDUP_COSINE_THRESHOLD   # 0.02 distance
        exact_max = 1.0 - DEDUP_EXACT_THRESHOLD       # 0.005 distance
        # Base filename (last extension stripped, lower-cased). The SQL
        # regexp below MUST mirror this so the same-name tier agrees in
        # both spaces.
        x_base = _re.sub(r"\.[^.]+$", "", x.original_filename or "").lower()
        cand_base = func.lower(
            func.regexp_replace(Image.original_filename, r"\.[^.]+$", "")
        )
        dist = Image.clip_embedding.cosine_distance(x.clip_embedding)
        candidate = (
            await s.execute(
                select(Image.id, Image.uploaded_at, dist.label("distance"))
                .where(
                    Image.user_id == x.user_id,
                    Image.id != x.id,
                    Image.deleted_at.is_(None),
                    Image.clip_embedding.is_not(None),
                    or_(
                        # near-exact dup, any filename
                        dist <= exact_max,
                        # looser, but only for a same-base-filename re-save
                        and_(dist <= samename_max, cand_base == x_base),
                    ),
                )
                # Oldest first; id tiebreak keeps it deterministic when
                # two rows share a timestamp (common for a batch import).
                .order_by(Image.uploaded_at.asc(), Image.id.asc())
                .limit(1)
            )
        ).first()
        if candidate is None:
            return

        y_id, y_uploaded_at, distance = candidate
        # Only merge into a STRICTLY-OLDER survivor. If the only match is
        # newer than this image, this image IS the original — leave it,
        # and the newer one will fold into it when its own job runs.
        x_key = (x.uploaded_at, x.id)
        y_key = (y_uploaded_at, y_id)
        if not (y_key < x_key):
            return

        similarity = 1.0 - float(distance)

        # Repoint FIRST (so the survivor is live + linked before the dup
        # disappears), THEN soft-delete — same ordering as the cloud_sync
        # hash-dedup block. We deliberately do NOT touch remote_modified /
        # sha256 / last_synced_at: the skip-check matches on remote_modified,
        # so leaving it intact is what makes the next sync skip the file.
        await s.execute(
            sa_update(CloudFile)
            .where(CloudFile.local_image_id == x.id)
            .values(local_image_id=y_id)
        )
        x.deleted_at = datetime.now(timezone.utc)
        await s.commit()

        logger.info(
            "dedup: merged image %s into %s (cosine=%.4f)",
            x.id, y_id, similarity,
        )


async def _process_face_scan(session_factory, user_id: UUID, image_id: UUID) -> None:
    from backend.consent import is_consent_active
    from backend.faces_pipeline import process_image_for_faces
    from backend.models import Image, User
    from backend.storage import storage

    async with session_factory() as s:
        image = (
            await s.execute(select(Image).where(Image.id == image_id))
        ).scalar_one_or_none()
        if image is None or not image.pending_face_scan:
            return
        user = (
            await s.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            return
        # CONSENT GATE (mirrors the inline path in api/images.py:_run_face_scan_one).
        # `pending_face_scan` is set at UPLOAD time and does NOT reflect the
        # user's *current* biometric-consent state. Re-check consent HERE, at
        # scan time, so a user who never granted — or has since REVOKED —
        # face/biometric consent is never run through ArcFace. Without this the
        # worker (the live production path) would generate biometric embeddings
        # regardless of consent. Clear the flag so the job isn't retried forever.
        if not await is_consent_active(s, user_id):
            image.pending_face_scan = False
            await s.commit()
            logger.info(
                "worker.face_scan: consent inactive for user %s — skipping "
                "biometric scan of %s", user_id, image_id,
            )
            return
        try:
            raw = (
                storage.get(storage.bucket_originals, image.original_blob_key)
                if image.original_blob_key
                else storage.get(storage.bucket_served, image.served_blob_key)
            )
        except Exception:
            logger.exception("worker.face_scan: blob fetch failed for %s", image_id)
            return

        # Video rows: the bytes above are an MP4, not an image — face
        # pipeline expects pixels. Sample SEVERAL keyframes across the
        # clip and run face detection on each.
        #
        # We used to do a single mid-clip frame on the assumption that
        # the same face appears throughout. In practice talking-head
        # videos have moments where the subject looks down / off
        # camera / their face is partially occluded by hand
        # gestures — and a single mid-clip sample lands on those
        # often enough that real users (the ones who recorded the
        # video!) don't get detected. Running detection on 4 frames
        # across the timeline catches the face on at least one of
        # them. The ArcFace clusterer collapses duplicate embeddings
        # of the same person into a single Person row, so multiple
        # detections of the user across keyframes don't fragment.
        if image.category == "video":
            from backend.summarize import _extract_keyframe, _probe_video_duration
            duration = _probe_video_duration(raw)
            if duration and duration > 2.0:
                # Sample 1 keyframe per 5s of duration, capped at 24
                # frames. The OLD hardcoded 4-frame scan missed people
                # in videos with multiple subjects across the timeline:
                # 4 evenly-spaced samples on a 60s clip mean a person
                # who only appears 30-40s in could land entirely
                # between samples. Matching the summarize-side density
                # (also 1/5s, capped at 24) means the same frames are
                # captioned AND face-scanned — same coverage budget,
                # better recall on multi-person videos.
                #
                # The ArcFace clusterer dedupes the same person across
                # frames via cosine similarity, so running on 12-24
                # frames doesn't fragment one person into many rows.
                MAX_FRAMES = 32
                SECONDS_PER_FRAME = 4.0
                n = max(4, min(MAX_FRAMES, int(round(duration / SECONDS_PER_FRAME))))
                if n == 1:
                    offsets = [duration * 0.5]
                else:
                    # Span ~4%–96% of the clip (was 10%–90%) so a face in
                    # the opening or closing seconds isn't outside the
                    # sampled window, and denser (1/4s, cap 32 — was 1/5s,
                    # cap 24) to shrink the gap a face can hide between on
                    # a long clip like a 3-minute talking-head recording.
                    offsets = [
                        max(0.5, duration * (0.04 + (0.92 * i / (n - 1))))
                        for i in range(n)
                    ]
            else:
                offsets = [0.5, 1.5, 3.0, 5.0]
            frames: list[bytes] = []
            for t in offsets:
                f = _extract_keyframe(raw, t)
                if f:
                    frames.append(f)
            if not frames:
                logger.info(
                    "worker.face_scan: no keyframes extractable for video %s "
                    "(tried %d offsets)",
                    image_id, len(offsets),
                )
                return
            logger.info(
                "worker.face_scan: %d/%d keyframes extracted for video %s",
                len(frames), len(offsets), image_id,
            )
            # Run face detection on each keyframe. Each call appends
            # any detected faces to the image's Face rows; the
            # clusterer dedupes via cosine similarity so the same
            # person isn't duplicated across keyframes.
            detected_any = False
            for idx, frame in enumerate(frames):
                try:
                    n = await process_image_for_faces(s, user, image, frame)
                    if n:
                        detected_any = True
                        logger.info(
                            "worker.face_scan: keyframe %d/%d detected %d face(s)",
                            idx + 1, len(frames), n,
                        )
                except Exception:
                    logger.exception(
                        "worker.face_scan: pipeline failed on keyframe %d for %s",
                        idx, image_id,
                    )
            if not detected_any:
                # Cascade rescue. The per-frame default detector
                # (RetinaFace @0.3) found nothing — but videos never get
                # the auto-cascade INSIDE process_image_for_faces, because
                # that path is gated on image.face_likelihood, which is
                # only ever set for still images (the vision pipeline
                # doesn't run on videos). So re-run each keyframe through
                # the explicit cascade (RetinaFace @0.15 → mediapipe) and
                # persist with a relaxed confidence floor, mirroring the
                # D8 re-detect path. This is what lets a long talking-head
                # clip — whose face the 0.3 pass skimmed past — still
                # register a person.
                from backend.vision.faces import detect_with_cascade
                from backend.vision.inference_pool import run_in_inference_pool
                for idx, frame in enumerate(frames):
                    try:
                        cascaded, stage = await run_in_inference_pool(
                            detect_with_cascade, frame
                        )
                    except Exception:
                        logger.exception(
                            "worker.face_scan: cascade failed on keyframe %d for %s",
                            idx, image_id,
                        )
                        continue
                    if not cascaded:
                        continue
                    try:
                        got = await process_image_for_faces(
                            s, user, image, frame,
                            detections=cascaded, min_confidence=0.10,
                        )
                    except Exception:
                        logger.exception(
                            "worker.face_scan: cascade persist failed on "
                            "keyframe %d for %s", idx, image_id,
                        )
                        continue
                    if got:
                        detected_any = True
                        logger.info(
                            "worker.face_scan: cascade (%s) rescued %d face(s) on "
                            "keyframe %d/%d for %s",
                            stage, got, idx + 1, len(frames), image_id,
                        )
                if not detected_any:
                    logger.info(
                        "worker.face_scan: no faces detected across %d keyframes "
                        "for %s (cascade included)",
                        len(frames), image_id,
                    )
            return

        try:
            await process_image_for_faces(s, user, image, raw)
        except Exception:
            logger.exception("worker.face_scan: pipeline failed for %s", image_id)


async def _process_transcode_video(
    session_factory, user_id: UUID, image_id: UUID,
) -> None:
    """Pull the original from MinIO, run it through the ffmpeg
    transcode pipeline, upload the served MP4 + poster JPEG, update
    the row's served_blob_key / mime_type_served / thumbnail_blob_key /
    width / height, and (per user policy) drop the original blob +
    `original_blob_key`. After this lands the stream endpoint serves
    the H.264 / AAC variant, browsers play immediately, and storage
    cost is the served size only.
    """
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from uuid import uuid4

    from backend.hls import transcode_to_hls_async
    from backend.models import Image
    from backend.storage import storage

    async with session_factory() as s:
        image = (
            await s.execute(select(Image).where(Image.id == image_id))
        ).scalar_one_or_none()
        if image is None:
            return
        if image.original_blob_key is None:
            # Already transcoded (or original expired) — nothing to do.
            return
        if image.category != "video":
            # HLS transcode is video-ONLY — `_probe_source` raises "no video
            # stream" on anything without a video track. Audio is served
            # straight from its original (no HLS ladder), so an audio file
            # reaching here was mis-enqueued; skip it cleanly instead of
            # raising (which logged an error AND left the row in a state the
            # stuck-job reaper kept re-enqueueing — a noisy fail loop).
            logger.info(
                "worker.transcode: %s is category %r (not video) — skipping HLS",
                image_id, image.category,
            )
            return

    # MinIO download + ffmpeg + upload happen in a temp dir that
    # auto-cleans even if the job crashes mid-flight. Keeping bytes
    # off the host filesystem long-term — only the served blob
    # outlives this scope.
    with TemporaryDirectory(prefix="transcode-") as td:
        work = Path(td)
        src_path = work / "source"
        try:
            raw = await asyncio.to_thread(
                storage.get, storage.bucket_originals, image.original_blob_key,
            )
            src_path.write_bytes(raw)
        except Exception:
            logger.exception(
                "worker.transcode: failed to fetch original for %s", image_id,
            )
            return

        try:
            result = await transcode_to_hls_async(src_path, work)
        except Exception:
            logger.exception("worker.transcode: hls encode failed for %s", image_id)
            return

        master = result.renditions[0]
        logger.info(
            "worker.transcode: %s — HLS, %d renditions, master %dx%d, "
            "%.1f MB total, %s, %.1fs duration",
            image_id, len(result.renditions),
            master.width, master.height,
            result.total_bytes / 1_000_000,
            "GPU" if result.used_gpu else "CPU", result.duration_s,
        )

        # Upload the HLS bundle to the served bucket. Layout:
        #   users/<uid>/hls/<job>/master.m3u8
        #   users/<uid>/hls/<job>/<label>/playlist.m3u8
        #   users/<uid>/hls/<job>/<label>/segment_NNN.ts
        # The whole bundle shares ONE prefix per job so a future
        # bulk-delete-by-prefix is one S3 op rather than N. The
        # prefix also doubles as the auth boundary the stream-HLS
        # endpoint resolves against — every fetch under this prefix
        # is gated to this image's owner.
        hls_prefix = f"users/{user_id}/hls/{uuid4().hex}/{image.id}"
        master_key = f"{hls_prefix}/master.m3u8"
        uploaded_keys: list[str] = []  # for cleanup on later failure

        async def _put(key: str, data: bytes, mime: str) -> None:
            await asyncio.to_thread(
                storage.put, storage.bucket_served, key, data, mime,
            )
            uploaded_keys.append(key)

        try:
            # Master variant playlist.
            await _put(
                master_key,
                result.master_playlist_path.read_bytes(),
                "application/vnd.apple.mpegurl",
            )
            # Each rendition: playlist + every segment.
            for r in result.renditions:
                rkey = f"{hls_prefix}/{r.label}/playlist.m3u8"
                await _put(
                    rkey, r.playlist_path.read_bytes(),
                    "application/vnd.apple.mpegurl",
                )
                for seg in r.segment_paths:
                    skey = f"{hls_prefix}/{r.label}/{seg.name}"
                    await _put(
                        skey, seg.read_bytes(), "video/mp2t",
                    )
        except Exception:
            logger.exception(
                "worker.transcode: HLS upload failed for %s — cleaning up",
                image_id,
            )
            for k in uploaded_keys:
                try:
                    await asyncio.to_thread(
                        storage.delete, storage.bucket_served, k,
                    )
                except Exception:
                    pass
            return

        # Poster JPEG into the served bucket too. Same lifecycle as
        # the HLS bundle — gallery card reads `thumbnail_blob_key`
        # for its background-image.
        poster_key: str | None = None
        try:
            poster_key = f"{hls_prefix}/poster.jpg"
            await _put(
                poster_key,
                result.poster_path.read_bytes(),
                "image/jpeg",
            )
        except Exception:
            logger.exception(
                "worker.transcode: poster upload failed for %s (skipping)",
                image_id,
            )
            poster_key = None

        # `served_variants` now records the HLS prefix + the
        # rendition list with their bandwidths + resolutions. The
        # streaming endpoint reads `hls_prefix` to resolve segment
        # requests, and the frontend player reads `renditions` to
        # decide whether to use hls.js at all.
        served_variants = {
            "hls_master": master_key,
            "hls_prefix": hls_prefix,
            "renditions": [
                {
                    "label": r.label,
                    "width": r.width,
                    "height": r.height,
                    "bandwidth_bps": r.bandwidth_bps,
                }
                for r in result.renditions
            ],
        }

        # Persist column updates BEFORE deleting the original.
        original_key_to_delete: str | None = None
        async with session_factory() as s:
            image2 = (
                await s.execute(select(Image).where(Image.id == image_id))
            ).scalar_one_or_none()
            if image2 is None:
                # Row deleted while we were transcoding — clean up
                # every blob we just wrote.
                for k in uploaded_keys:
                    try:
                        await asyncio.to_thread(
                            storage.delete, storage.bucket_served, k,
                        )
                    except Exception:
                        pass
                return
            original_key_to_delete = image2.original_blob_key
            # `served_blob_key` points to the master manifest. The
            # stream endpoint's HLS handler reads from served_variants;
            # legacy callers that try to GET this as a single file
            # will fetch the tiny m3u8 (a few KB) which surfaces a
            # clean error path rather than mp4 bytes.
            image2.served_blob_key = master_key
            image2.mime_type_served = "application/vnd.apple.mpegurl"
            image2.byte_size_served = result.total_bytes
            image2.served_variants = served_variants
            image2.width = master.width
            image2.height = master.height
            if poster_key:
                image2.thumbnail_blob_key = poster_key

            # Respect the user's originals-retention policy. Before
            # 2026-05 video uploads always dropped the original after
            # transcode (the served mp4 was treated as the master).
            # With HLS that's wrong: the renditions are re-encoded,
            # so dropping the original means losing the only
            # byte-identical copy. Now we set `original_expires_at`
            # from the user's policy and let the daily sweeper drop
            # it when the TTL passes. "immediate" policy still drops
            # it right here (no point waiting); "forever" sets
            # `original_expires_at = NULL` so the sweep skips it.
            from backend.api.storage import policy_to_expiry
            from backend.models import User as UserModel
            owner = (
                await s.execute(
                    select(UserModel).where(UserModel.id == image2.user_id)
                )
            ).scalar_one()
            policy = owner.original_retention_policy or "30d"
            if policy == "immediate":
                # Drop the bytes now — same as the legacy behavior.
                image2.original_blob_key = None
                image2.byte_size_original = None
            else:
                # Keep the original; just (re-)stamp the expiry to
                # match the user's policy. The retention sweeper
                # picks it up when due.
                image2.original_expires_at = policy_to_expiry(policy)
                original_key_to_delete = None  # don't delete blob below
            await s.commit()

        if original_key_to_delete:
            try:
                await asyncio.to_thread(
                    storage.delete,
                    storage.bucket_originals, original_key_to_delete,
                )
            except Exception:
                logger.exception(
                    "worker.transcode: original-blob delete failed for %s "
                    "(row already updated, blob is now orphaned)", image_id,
                )


# Office document extensions → the source filename suffix LibreOffice
# needs to pick the right import filter. We write the original bytes to a
# temp file with this suffix before invoking soffice. PDF is intentionally
# absent (native PDFs already render through the page endpoints directly);
# images / text / csv are handled by their own viewers, not this path.
_OFFICE_SUFFIX_BY_MIME: dict[str, str] = {
    # OOXML (modern Office)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    # Legacy binary Office
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    # OpenDocument
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    # Rich Text
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}

# File-extension fallback when the MIME is generic
# (application/octet-stream is common for cloud-synced / legacy files).
_OFFICE_EXTS: set[str] = {
    ".docx", ".xlsx", ".pptx",
    ".doc", ".xls", ".ppt",
    ".odt", ".ods", ".odp",
    ".rtf",
}


def _office_source_suffix(mime: str | None, filename: str | None) -> str | None:
    """Return the LibreOffice import suffix for an Office doc, else None.

    Prefer the MIME map; fall back to the filename extension so a
    generic `application/octet-stream` upload still converts when the
    name carries a known Office extension."""
    import os

    m = (mime or "").lower().split(";")[0].strip()
    if m in _OFFICE_SUFFIX_BY_MIME:
        return _OFFICE_SUFFIX_BY_MIME[m]
    _, ext = os.path.splitext((filename or "").lower())
    if ext in _OFFICE_EXTS:
        return ext
    return None


def _libreoffice_convert_to_pdf_sync(src_bytes: bytes, suffix: str) -> bytes:
    """Render Office-document bytes to PDF via headless LibreOffice.

    Writes `src_bytes` to a temp file (named with `suffix` so soffice
    selects the right import filter), runs

        soffice --headless --convert-to pdf --outdir <tmp> <src>

    in an isolated, per-call user-installation profile, and returns the
    produced PDF bytes. Raises RuntimeError on any failure (non-zero exit,
    timeout, no output file) so the caller can log + leave the row
    unconverted for a later retry.

    Runs in a thread (via asyncio.to_thread) because soffice is a
    blocking subprocess that can take several seconds on a complex deck.

    Isolation notes:
      * `-env:UserInstallation` points at a fresh per-call profile dir so
        two concurrent conversions (and any stale lock from a previously
        crashed soffice) never collide on the default ~/.config profile.
        Without it a second invocation fails with "another instance is
        running" until the lock clears.
      * The whole thing lives in one TemporaryDirectory that auto-cleans
        even if the conversion raises.
    """
    import glob
    import os
    import subprocess
    import tempfile

    # Resolve the binary once — both names ship the same engine; some
    # distro packages only expose one symlink.
    soffice_bin = None
    for candidate in ("libreoffice", "soffice"):
        from shutil import which
        if which(candidate):
            soffice_bin = candidate
            break
    if soffice_bin is None:
        raise RuntimeError(
            "LibreOffice (libreoffice/soffice) not found on PATH — the "
            "worker image needs the libreoffice-core + writer/calc/impress "
            "packages installed for Office→PDF conversion."
        )

    with tempfile.TemporaryDirectory(prefix="office2pdf-") as td:
        src_path = os.path.join(td, f"source{suffix}")
        out_dir = os.path.join(td, "out")
        profile_dir = os.path.join(td, "profile")
        os.makedirs(out_dir, exist_ok=True)
        with open(src_path, "wb") as f:
            f.write(src_bytes)

        cmd = [
            soffice_bin,
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nofirststartwizard",
            # Per-call isolated profile (file:// URL form required).
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to", "pdf",
            "--outdir", out_dir,
            src_path,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"LibreOffice conversion timed out after 180s for {suffix}"
            ) from exc

        if proc.returncode != 0:
            raise RuntimeError(
                "LibreOffice conversion failed (exit "
                f"{proc.returncode}): "
                f"{proc.stderr.decode('utf-8', errors='replace')[:500]}"
            )

        produced = glob.glob(os.path.join(out_dir, "*.pdf"))
        if not produced:
            raise RuntimeError(
                "LibreOffice reported success but produced no PDF "
                f"(stdout: {proc.stdout.decode('utf-8', errors='replace')[:300]})"
            )
        with open(produced[0], "rb") as f:
            pdf = f.read()
        if not pdf:
            raise RuntimeError("LibreOffice produced an empty PDF")
        return pdf


async def _process_convert_office(
    session_factory, image_id: UUID,
) -> None:
    """Render an Office document to PDF and stash it under
    `images.converted_pdf_blob_key` so the existing PDF page endpoints
    can serve it through the same viewer as a native PDF.

    Idempotent: if the row already has a `converted_pdf_blob_key`, or it
    isn't an Office doc, we no-op. Best-effort end to end — any failure is
    logged and the row simply stays unconverted (the FE shows the
    download-only fallback) rather than crashing the worker loop.
    """
    from pathlib import PurePosixPath
    from uuid import uuid4

    from backend.models import Image
    from backend.storage import storage

    async with session_factory() as s:
        image = (
            await s.execute(select(Image).where(Image.id == image_id))
        ).scalar_one_or_none()
        if image is None:
            return
        if image.converted_pdf_blob_key:
            # Already converted (e.g. a duplicate enqueue raced us).
            return
        suffix = _office_source_suffix(
            image.mime_type_original, image.original_filename
        )
        if suffix is None:
            logger.info(
                "worker.convert_office: %s is not a recognized Office "
                "document (mime=%r name=%r) — skipping",
                image_id, image.mime_type_original, image.original_filename,
            )
            return
        if not image.original_blob_key:
            logger.info(
                "worker.convert_office: %s has no original blob "
                "(dropped by retention?) — cannot convert",
                image_id,
            )
            return
        user_id = image.user_id
        original_key = image.original_blob_key

    # Fetch the source bytes off the row-scoped session.
    try:
        raw = await asyncio.to_thread(
            storage.get, storage.bucket_originals, original_key,
        )
    except Exception:
        logger.exception(
            "worker.convert_office: failed to fetch original for %s", image_id,
        )
        return

    # Heavy blocking subprocess → thread pool so the worker loop stays
    # responsive to its other (face-scan) jobs.
    try:
        pdf_bytes = await asyncio.to_thread(
            _libreoffice_convert_to_pdf_sync, raw, suffix,
        )
    except Exception as exc:
        logger.warning(
            "worker.convert_office: LibreOffice render failed for %s "
            "(suffix=%s) — leaving row unconverted: %s",
            image_id, suffix, exc,
        )
        return

    # Upload the PDF to the served bucket. Keyed under a `converted/`
    # prefix per user so a future bulk cleanup / quota walk can find it.
    pdf_key = f"users/{user_id}/converted/{uuid4().hex}.pdf"
    try:
        await asyncio.to_thread(
            storage.put,
            storage.bucket_served, pdf_key, pdf_bytes, "application/pdf",
        )
    except Exception:
        logger.exception(
            "worker.convert_office: PDF upload failed for %s", image_id,
        )
        return

    # Persist the key. Re-fetch the row in a fresh session in case it was
    # deleted while we were converting; clean up the orphan blob if so.
    async with session_factory() as s:
        image2 = (
            await s.execute(select(Image).where(Image.id == image_id))
        ).scalar_one_or_none()
        if image2 is None:
            try:
                await asyncio.to_thread(
                    storage.delete, storage.bucket_served, pdf_key,
                )
            except Exception:
                pass
            return
        image2.converted_pdf_blob_key = pdf_key
        await s.commit()
    logger.info(
        "worker.convert_office: %s rendered %s → PDF (%d KB) at %s",
        image_id, suffix, len(pdf_bytes) // 1024,
        PurePosixPath(pdf_key).name,
    )


async def _process_job(session_factory, job: dict) -> None:
    from backend.jobs import mark_done

    kind = job.get("kind")
    image_id_s = job.get("image_id")
    if not kind or not image_id_s:
        logger.warning("worker: skipping malformed job %s", job)
        return
    image_id = UUID(image_id_s)
    user_id = UUID(job["user_id"]) if job.get("user_id") else None

    try:
        if kind == "summarize":
            await _process_summarize(session_factory, image_id)
        elif kind == "face_scan":
            if user_id is None:
                logger.warning("worker: face_scan missing user_id %s", job)
                return
            await _process_face_scan(session_factory, user_id, image_id)
        elif kind == "face_scan_then_summarize":
            if user_id is None:
                logger.warning("worker: face_scan_then_summarize missing user_id %s", job)
                return
            await _process_face_scan(session_factory, user_id, image_id)
            await _process_summarize(session_factory, image_id)
        elif kind == "transcode_video":
            if user_id is None:
                logger.warning("worker: transcode_video missing user_id %s", job)
                return
            await _process_transcode_video(session_factory, user_id, image_id)
        elif kind == "convert_office":
            # Office doc → PDF (headless LibreOffice). No user_id needed —
            # the handler resolves the owner from the row. Leaving the
            # row unconverted on failure is fine; the FE falls back to a
            # download link.
            await _process_convert_office(session_factory, image_id)
        else:
            logger.warning("worker: unknown job kind %s", kind)
    finally:
        # Always remove the dedupe key so a new request for the same
        # image can re-enqueue. We pop in `finally` rather than after
        # the try-block so a job that crashes mid-process doesn't
        # permanently lock that image out of being re-summarized.
        await mark_done(kind, image_id_s)


async def _queue_depth_safe(redis_client: aioredis.Redis) -> int:
    try:
        return int(await redis_client.llen(JOB_QUEUE_KEY))
    except Exception:
        return -1


async def main() -> None:
    logging.basicConfig(
        level=os.environ.get("WORKER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    # Warm transformers on startup so the first job doesn't pay the
    # cold-import cost (which can be 30+ seconds the first time torch
    # is imported alongside open_clip/insightface).
    # Warm the heavy models on startup so the first job doesn't eat the
    # ~30s cold-load. (`warm_transformers` was removed from runtime; call
    # the real lru_cached loaders directly to populate the cache.) Each is
    # individually guarded — a loader that can't warm yet just logs and the
    # model loads lazily on first use instead of crashing startup.
    import backend.vision.runtime as _rt
    for _loader in ("get_clip", "get_florence2", "get_summary_rewriter"):
        try:
            getattr(_rt, _loader)()
            logger.info("worker: warmed %s", _loader)
        except Exception as _e:
            logger.warning("worker: warm %s skipped (%s)", _loader, _e)

    # C8.2 — emit heartbeats every 30 s so /admin/processes can see us
    # even though we live in a sibling container outside the API's
    # psutil reach. Metadata carries live queue depth AND a one-shot
    # GPU enumeration so the dashboard's Hardware tab can render the
    # accelerators this worker actually has access to (torch.cuda /
    # IPEX-XPU / OpenVINO / NPU). The GPU probe runs once at startup —
    # querying every tick would hammer torch/openvino unnecessarily.
    from backend.heartbeats import heartbeat_loop
    from backend.system_probes import probe_accelerators_full
    gpu_snapshot = probe_accelerators_full()
    logger.info(
        "worker: accelerator snapshot — %d device(s) on %s",
        len(gpu_snapshot.get("devices", [])), gpu_snapshot.get("backend"),
    )
    async def _meta() -> dict:
        return {
            "queue_depth": await _queue_depth_safe(redis_client),
            "gpu": gpu_snapshot,
        }
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(
            kind="ml-worker",
            is_running=lambda: _RUNNING,
            metadata_provider=_meta,
        )
    )

    # Reliable-queue reaper: requeues jobs orphaned by a crashed worker
    # and clears leaked dedupe keys. Runs in-process so one worker is
    # self-healing without depending on the API container.
    reaper_task = asyncio.create_task(_reaper_loop(redis_client))

    # Reliable-queue consumer primitives (crash-safe at-least-once).
    from backend.jobs import ack_job, reserve_job, retry_or_dead

    watchdog = int(getattr(settings, "job_watchdog_timeout_seconds", 1500))

    logger.info(
        "worker: ready, consuming %s (reliable queue, watchdog %ds)",
        JOB_QUEUE_KEY, watchdog,
    )
    while _RUNNING:
        # Reserve one job: atomically moves it from the ready queue to the
        # in-flight list so a mid-process crash doesn't lose it (the reaper
        # requeues anything stranded in-flight past the visibility timeout).
        try:
            raw = await reserve_job(timeout=5)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("worker: redis reserve failed (sleeping 2s)")
            await asyncio.sleep(2)
            continue
        if raw is None:
            continue
        try:
            job = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("worker: invalid json on queue: %r", raw[:200])
            # Malformed payload — ack it out of in-flight so it doesn't
            # get reaped forever. It carries no recoverable work.
            await ack_job(raw)
            continue
        logger.info("worker: processing %s/%s", job.get("kind"), job.get("image_id"))
        try:
            # Per-job watchdog: a single job can't stall the (single-
            # threaded) worker past `watchdog` seconds. A wedged ffmpeg /
            # LibreOffice subprocess that ignores its own internal timeout,
            # or a hung model call, is abandoned so the queue keeps moving.
            await asyncio.wait_for(_process_job(Session, job), timeout=watchdog)
        except asyncio.TimeoutError:
            logger.error(
                "worker: job %s/%s exceeded watchdog (%ds) — abandoning",
                job.get("kind"), job.get("image_id"), watchdog,
            )
            # Clear the dedupe lock so the row isn't permanently stuck,
            # then let retry/backoff/dead-letter decide its fate.
            try:
                from backend.jobs import mark_done
                if job.get("kind") and job.get("image_id"):
                    await mark_done(job["kind"], job["image_id"])
            except Exception:
                pass
            await retry_or_dead(raw, f"watchdog timeout after {watchdog}s")
            continue
        except Exception as exc:
            logger.exception("worker: job %s crashed", job)
            # Bounded retry with backoff; dead-letter on exhaustion so a
            # poison job is preserved for inspection, not lost or looping.
            await retry_or_dead(raw, f"{type(exc).__name__}: {exc}")
            continue
        # Success — remove from in-flight + drop the attempt counter.
        await ack_job(raw)

    logger.info("worker: shutting down")
    heartbeat_task.cancel()
    reaper_task.cancel()
    for t in (heartbeat_task, reaper_task):
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    try:
        await redis_client.aclose()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(main())
