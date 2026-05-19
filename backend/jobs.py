"""Redis-backed job queue for offloading ML inference to a worker.

The API container stays light — uvicorn handles HTTP and DB only.
Florence-2, Qwen2.5, RetinaFace, ArcFace, OpenCLIP, and the doc-summary
LLMs all live in `neuthek-ml-worker`, a sibling container that runs
`python -m backend.worker.main` (see `docker-compose.yml`). The API
publishes jobs here; the worker consumes them and writes results back
to Postgres directly.

Why a queue instead of subprocess.Popen or asyncio.to_thread:
  - A long Florence beam search holds the GIL for ~30s on CPU. With
    ML in-process, the asyncio event loop starves and `POST
    /auth/jwt/login` (and every other endpoint) hangs until inference
    finishes. Splitting processes is the only fix that keeps the API
    truly responsive.
  - Redis lists give us BLPOP semantics for free — the worker blocks
    on the queue without polling, and bookkeeping (pending count)
    is one LLEN call away.

Job shape (JSON):
  { "kind": "summarize" | "face_scan" | "face_scan_then_summarize",
    "image_id": "<uuid>",
    "user_id": "<uuid>"   # only required for face scans
  }

Failure mode: if Redis is down the API falls back to running the job
inline via `asyncio.create_task`, same as before this split. That
preserves the dev experience when redis isn't running yet.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from backend.config import settings

logger = logging.getLogger(__name__)

JOB_QUEUE_KEY = "neuthek:jobs"

_redis: aioredis.Redis | None = None


def _client() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def _enqueue_dedupe(payload: dict[str, Any], dedupe_key: str) -> bool:
    """Push a job onto the queue ONLY if no job with the same dedupe key
    is already pending or in-flight.

    The dedupe set (`neuthek:jobs:active`) is keyed `<kind>:<image_id>`.
    The worker removes its key from the set when it finishes. The atomic
    SADD-then-RPUSH avoids the case where the API enqueues twice in a
    single tick — common for upload + face_scan_then_summarize +
    progress-poll spam, which previously piled up 100+ duplicate jobs
    for the same image inside a couple of minutes.

    Returns True if pushed, False if either Redis is down OR the job is
    already in-flight (caller treats both the same — no-op).
    """
    try:
        client = _client()
        added = await client.sadd("neuthek:jobs:active", dedupe_key)
        if added == 0:
            return False  # already pending — drop silently
        await client.rpush(JOB_QUEUE_KEY, json.dumps(payload))
        return True
    except Exception:
        logger.exception("jobs: enqueue failed for %s", payload.get("kind"))
        return False


async def _enqueue(payload: dict[str, Any]) -> bool:
    """Back-compat wrapper that uses kind+image_id as the dedupe key."""
    key = f"{payload.get('kind')}:{payload.get('image_id')}"
    return await _enqueue_dedupe(payload, key)


async def mark_done(kind: str, image_id: str) -> None:
    """Worker calls this after a job completes (success or failure) so
    the API can re-enqueue if a new request for the same image comes in.
    """
    try:
        await _client().srem("neuthek:jobs:active", f"{kind}:{image_id}")
    except Exception:
        pass


async def enqueue_summarize(image_id: UUID) -> bool:
    return await _enqueue({"kind": "summarize", "image_id": str(image_id)})


async def enqueue_face_scan(user_id: UUID, image_id: UUID) -> bool:
    return await _enqueue(
        {"kind": "face_scan", "image_id": str(image_id), "user_id": str(user_id)}
    )


async def enqueue_transcode_video(user_id: UUID, image_id: UUID) -> bool:
    """Queue an ffmpeg pass for an uploaded video. Worker pulls the
    original from MinIO, transcodes to H.264 + AAC + faststart, uploads
    the result to the served bucket, writes a poster frame, and drops
    the original. Browser-compatible playback after one job."""
    return await _enqueue(
        {
            "kind": "transcode_video",
            "image_id": str(image_id),
            "user_id": str(user_id),
        }
    )


async def enqueue_face_scan_then_summarize(user_id: UUID, image_id: UUID) -> bool:
    return await _enqueue(
        {
            "kind": "face_scan_then_summarize",
            "image_id": str(image_id),
            "user_id": str(user_id),
        }
    )


async def queue_depth() -> int:
    """Used by the diagnostic endpoint + Account UI to show backlog size."""
    try:
        return int(await _client().llen(JOB_QUEUE_KEY))
    except Exception:
        return -1


async def aclose() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        finally:
            _redis = None
