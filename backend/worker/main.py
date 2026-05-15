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
        try:
            await process_image_for_faces(s, user, image, raw)
        except Exception:
            logger.exception("worker.face_scan: pipeline failed for %s", image_id)


async def _process_job(session_factory, job: dict) -> None:
    kind = job.get("kind")
    image_id_s = job.get("image_id")
    if not kind or not image_id_s:
        logger.warning("worker: skipping malformed job %s", job)
        return
    image_id = UUID(image_id_s)
    user_id = UUID(job["user_id"]) if job.get("user_id") else None

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
    else:
        logger.warning("worker: unknown job kind %s", kind)


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
    try:
        await redis_client.aclose()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    asyncio.run(main())
