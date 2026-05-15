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


async def _enqueue(payload: dict[str, Any]) -> bool:
    """Push a job onto the queue. Returns True on success, False on failure
    (caller may then fall back to running the job inline).
    """
    try:
        await _client().rpush(JOB_QUEUE_KEY, json.dumps(payload))
        return True
    except Exception:
        logger.exception("jobs: enqueue failed for %s", payload.get("kind"))
        return False


async def enqueue_summarize(image_id: UUID) -> bool:
    return await _enqueue({"kind": "summarize", "image_id": str(image_id)})


async def enqueue_face_scan(user_id: UUID, image_id: UUID) -> bool:
    return await _enqueue(
        {"kind": "face_scan", "image_id": str(image_id), "user_id": str(user_id)}
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
