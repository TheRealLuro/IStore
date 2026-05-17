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
from backend.jobs import JOB_QUEUE_KEY, fair_dequeue, total_queue_depth

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
        try:
            await process_image_for_faces(s, user, image, raw)
        except Exception:
            logger.exception("worker.face_scan: pipeline failed for %s", image_id)


async def _process_job(session_factory, job: dict) -> None:
    from backend.jobs import mark_done

    kind = job.get("kind")
    image_id_s = job.get("image_id")
    user_id_s = job.get("user_id")
    if not kind or not image_id_s or not user_id_s:
        # The fair-queue enqueue always stamps user_id; a missing one
        # means a legacy job from the pre-rewrite queue. We still need
        # to clear the dedup set so the image can be re-enqueued, but
        # without a user_id we can't decrement the per-user inflight
        # counter (it was never incremented either, since the legacy
        # path didn't go through fair_dequeue).
        logger.warning("worker: skipping malformed job %s", job)
        return
    image_id = UUID(image_id_s)
    user_id = UUID(user_id_s)

    try:
        if kind == "summarize":
            await _process_summarize(session_factory, image_id)
        elif kind == "face_scan":
            await _process_face_scan(session_factory, user_id, image_id)
        elif kind == "face_scan_then_summarize":
            await _process_face_scan(session_factory, user_id, image_id)
            await _process_summarize(session_factory, image_id)
        else:
            logger.warning("worker: unknown job kind %s", kind)
    finally:
        # Always run cleanup — clears the dedupe key AND decrements the
        # user's inflight counter so the round-robin scheduler picks
        # them again next time. In `finally` so a crashing job still
        # frees its slot rather than wedging the user's queue.
        await mark_done(user_id_s, kind, image_id_s)


async def _queue_depth_safe(redis_client: aioredis.Redis) -> int:
    # Sum across per-user queues now that we round-robin.
    try:
        return int(await total_queue_depth())
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

    logger.info("worker: ready, polling per-user fair queue")
    while _RUNNING:
        try:
            # Replaces the old BLPOP on the single list with a
            # round-robin dequeue across per-user queues. Returns None
            # after the timeout if nothing is eligible; we just loop.
            job = await fair_dequeue(timeout=5.0)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("worker: fair_dequeue failed (sleeping 2s)")
            await asyncio.sleep(2)
            continue
        if job is None:
            continue
        logger.info(
            "worker: processing %s/%s for user %s",
            job.get("kind"), job.get("image_id"), job.get("user_id"),
        )
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
