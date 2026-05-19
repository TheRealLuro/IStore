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


def _handle_signal(signum: int, _frame: Any) -> None:
    global _RUNNING
    logger.info("worker: signal %s received, draining", signum)
    _RUNNING = False


async def _process_summarize(session_factory, image_id: UUID) -> None:
    from backend.summarize import summarize_image_id

    async with session_factory() as s:
        await summarize_image_id(s, image_id)


async def _process_face_scan(session_factory, user_id: UUID, image_id: UUID) -> None:
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
        # pipeline expects pixels. Extract a middle keyframe via the
        # existing ffmpeg helper, then run detection on that. We
        # deliberately pick ONE frame instead of every keyframe
        # because the same face appears across all of them in a
        # talking-head video and the existing ArcFace clusterer
        # would only collapse them on a second pass — running once
        # is faster and gives the same final clustering when the
        # video really is one person.
        if image.category == "video":
            from backend.summarize import _extract_keyframe, _probe_video_duration
            duration = _probe_video_duration(raw)
            seek = (duration / 2) if duration and duration > 0 else 5.0
            frame = _extract_keyframe(raw, seek)
            if not frame:
                logger.info(
                    "worker.face_scan: no keyframe extractable for video %s",
                    image_id,
                )
                return
            raw = frame

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

    from backend.models import Image
    from backend.storage import storage
    from backend.transcode import transcode_video_async

    async with session_factory() as s:
        image = (
            await s.execute(select(Image).where(Image.id == image_id))
        ).scalar_one_or_none()
        if image is None:
            return
        if image.original_blob_key is None:
            # Already transcoded (or original expired) — nothing to do.
            return
        if image.category not in {"video", "audio"}:
            logger.warning(
                "worker.transcode: %s has category %r, skipping",
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
            result = await transcode_video_async(src_path, work)
        except Exception:
            logger.exception("worker.transcode: ffmpeg failed for %s", image_id)
            return

        logger.info(
            "worker.transcode: %s — %d tiers, %dx%d default, %.1fMB total, %s, %.1fs duration",
            image_id, len(result.variants),
            result.width, result.height,
            sum(v.size for v in result.variants) / 1_000_000,
            "GPU" if result.used_gpu else "CPU", result.duration_s,
        )

        # Upload one MP4 per quality tier. Variant keys share a per-job
        # prefix so a future bulk-delete by prefix is one S3 op rather
        # than N. The default tier's key also goes into
        # `served_blob_key` so existing read paths (which don't know
        # about quality variants) keep working.
        upload_prefix = f"users/{user_id}/served/{uuid4().hex}/{image.id}"
        served_variants_map: dict[str, str] = {}
        served_key: str | None = None
        try:
            for v in result.variants:
                key = f"{upload_prefix}_{v.label}.mp4"
                await asyncio.to_thread(
                    storage.put,
                    storage.bucket_served, key,
                    v.path.read_bytes(),
                    "video/mp4",
                )
                served_variants_map[v.label] = key
                if v.label == result.default_label:
                    served_key = key
        except Exception:
            logger.exception(
                "worker.transcode: served upload failed for %s", image_id,
            )
            return
        if served_key is None:
            # Defensive — _tiers_for_source always yields at least one.
            logger.error("worker.transcode: no default tier for %s", image_id)
            return

        # Upload poster JPEG into the served bucket too (same lifecycle
        # as the video — they're a unit).
        poster_key: str | None = None
        try:
            poster_key = f"users/{user_id}/served/{uuid4().hex}/{image.id}_poster.jpg"
            await asyncio.to_thread(
                storage.put,
                storage.bucket_served, poster_key,
                result.poster_path.read_bytes(),
                "image/jpeg",
            )
        except Exception:
            logger.exception(
                "worker.transcode: poster upload failed for %s (skipping)",
                image_id,
            )
            poster_key = None

        # Persist column updates BEFORE deleting the original. If the
        # commit fails we'd rather have orphaned served bytes than a
        # row pointing at a deleted original.
        original_key_to_delete: str | None = None
        async with session_factory() as s:
            image2 = (
                await s.execute(select(Image).where(Image.id == image_id))
            ).scalar_one_or_none()
            if image2 is None:
                # Row deleted while we were transcoding — clean up
                # every served blob we just wrote so nothing orphans.
                cleanup_keys: list[str] = [poster_key] if poster_key else []
                cleanup_keys.extend(served_variants_map.values())
                for k in cleanup_keys:
                    try:
                        await asyncio.to_thread(
                            storage.delete, storage.bucket_served, k,
                        )
                    except Exception:
                        pass
                return
            original_key_to_delete = image2.original_blob_key
            image2.served_blob_key = served_key
            image2.mime_type_served = "video/mp4"
            image2.byte_size_served = result.served_size
            image2.served_variants = served_variants_map
            image2.width = result.width
            image2.height = result.height
            # `thumbnail_blob_key` is the column the gallery card pulls
            # for its background-image. Setting it here means the video
            # card stops showing the generic video glyph and starts
            # showing the actual poster frame.
            if poster_key:
                image2.thumbnail_blob_key = poster_key
            # Drop the original per user policy: only keep the
            # served copy. The blob delete happens AFTER commit so
            # a transaction failure doesn't strand us with bytes
            # gone + row still pointing at them.
            image2.original_blob_key = None
            image2.byte_size_original = None
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
    try:
        from backend.vision.runtime import warm_transformers
        warm_transformers()
        logger.info("worker: transformers warmed")
    except Exception:
        logger.exception("worker: warm_transformers failed (continuing anyway)")

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

    logger.info("worker: ready, polling %s", JOB_QUEUE_KEY)
    while _RUNNING:
        try:
            payload = await redis_client.blpop(JOB_QUEUE_KEY, timeout=5)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("worker: redis poll failed (sleeping 2s)")
            await asyncio.sleep(2)
            continue
        if payload is None:
            continue
        _key, raw = payload
        try:
            job = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("worker: invalid json on queue: %r", raw[:200])
            continue
        logger.info("worker: processing %s/%s", job.get("kind"), job.get("image_id"))
        try:
            await _process_job(Session, job)
        except Exception:
            logger.exception("worker: job %s crashed", job)

    logger.info("worker: shutting down")
    heartbeat_task.cancel()
    try:
        await heartbeat_task
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
