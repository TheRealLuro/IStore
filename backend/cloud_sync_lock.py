"""CS9 — per-link sync mutex (Redis-backed, in-process fallback).

Before this module, `cloud_sync` had a single in-memory progress dict
(`backend/api/cloud.py::_SYNC_PROGRESS`) gating duplicate manual sync
clicks. Three holes:

  1. **Per-process.** A multi-worker uvicorn deployment (`gunicorn -w
     4`, or future hot-swap reload) holds the dict per worker, so two
     workers can both decide a sync is "free" and start one.
  2. **Doesn't reach the cron sweep.** `backend/cloud_sync_worker.py`
     calls `sync_user_provider` directly without consulting the
     progress dict, so the hourly tick can collide with a manual
     click.
  3. **Sticks on crash.** If `_run_sync_background` is SIGKILLed
     mid-flight (uvicorn restart, OOM-killer), the entry stays at
     `state=running` forever — the next manual click thinks a sync is
     already happening and returns zero counts.

The Redis mutex closes all three:

  * SET key NX EX ttl gives us an atomic "first one wins" with a
    server-side TTL that auto-clears if the holder dies.
  * Both the HTTP route and the cron worker pass through the same
    helper, so they can't race each other.
  * In-memory fallback (a per-process set) is kept for dev / pytest
    environments where Redis isn't running. The fallback is
    intentionally weaker than the Redis path — it gives correctness
    in single-process runs without forcing tests to spin up Redis.

Lock key is `cloud:sync:lock:{link_id}` so two providers on the same
user (Drive + future Dropbox) don't block each other.
"""
from __future__ import annotations

import asyncio
import logging
import time

from backend.config import settings
from backend.security import _redis_client

logger = logging.getLogger(__name__)

# Default mutex TTL. A sync of a 50k-file Drive takes ~10 minutes on
# the network-local MinIO + low-latency Drive API; we give it 3x that
# headroom so a legitimately-slow sync doesn't auto-release into
# duplicate execution. The release path clears the key on success/
# failure so the TTL only matters when the holder process dies.
DEFAULT_LOCK_TTL_SECONDS = 30 * 60

# In-process fallback when Redis isn't reachable (dev, pytest without
# the redis service). asyncio.Lock-style guard keyed by lock key. Per-
# process, so multi-worker deployments MUST run Redis — which is
# already enforced for production by `require_redis_when_production()`.
_INPROC_LOCKS: dict[str, float] = {}
_INPROC_GUARD = asyncio.Lock()


def lock_key_for(link_id: int) -> str:
    """Stable Redis key namespace for the per-link mutex."""
    return f"cloud:sync:lock:{int(link_id)}"


async def try_acquire(
    key: str, *, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> bool:
    """Atomically claim the lock. Returns True iff this caller now
    holds it. False means someone else is already syncing this link.

    Always pairs with `release(key)` in a try/finally — the TTL is a
    safety net for crash recovery, not the primary release path."""
    redis = await _redis_client()
    if redis is not None:
        try:
            # SET key value NX EX ttl is the canonical atomic
            # acquire. `nx=True` means "only if not already set"; ex=
            # arms the TTL in the same round-trip so there's no race
            # window where we hold the key without an expiry.
            result = await redis.set(
                key, "1", nx=True, ex=int(ttl_seconds),
            )
        finally:
            await redis.aclose()
        return bool(result)

    # In-process fallback — atomic under asyncio.Lock. Treat stale
    # entries (past their TTL) as released.
    now = time.time()
    async with _INPROC_GUARD:
        expires_at = _INPROC_LOCKS.get(key)
        if expires_at is not None and expires_at > now:
            return False
        _INPROC_LOCKS[key] = now + ttl_seconds
        return True


async def release(key: str) -> None:
    """Drop the lock. Idempotent — calling release without a prior
    acquire (e.g. on a fast-path early return) is safe."""
    redis = await _redis_client()
    if redis is not None:
        try:
            await redis.delete(key)
        finally:
            await redis.aclose()
        return
    async with _INPROC_GUARD:
        _INPROC_LOCKS.pop(key, None)


class sync_lock:
    """Context manager wrapper for the try_acquire / release pair.

    Usage:

        async with sync_lock(link_id) as acquired:
            if not acquired:
                return  # someone else is syncing — bail
            await sync_user_provider(...)

    The boolean result is intentional rather than raising — callers
    have different "what to do on contention" policies (HTTP returns
    existing progress, cron logs + skips), so the caller decides."""

    def __init__(self, link_id: int, *, ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS) -> None:
        self._key = lock_key_for(link_id)
        self._ttl = ttl_seconds
        self._acquired = False

    async def __aenter__(self) -> bool:
        self._acquired = await try_acquire(self._key, ttl_seconds=self._ttl)
        return self._acquired

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        if self._acquired:
            await release(self._key)


__all__ = [
    "DEFAULT_LOCK_TTL_SECONDS",
    "lock_key_for",
    "try_acquire",
    "release",
    "sync_lock",
]
