"""Regression tests for CS9 — per-link Redis sync mutex.

Before this fix, `_SYNC_PROGRESS` (a per-process Python dict in
`backend/api/cloud.py`) was the only gate against duplicate syncs.
That held inside a single uvicorn worker, but didn't reach the
hourly cron sweep (which calls `sync_user_provider` directly), and a
gunicorn `-w 4` deployment would have one dict per worker — four
workers each thinking the link was free, all firing simultaneously.

Tests:
  * `try_acquire` is atomic — two concurrent acquires of the same
    key never both return True.
  * `release` lets the next acquire succeed.
  * `sync_lock` context manager pairs acquire+release across
    success AND exception paths.
  * The lock key namespace doesn't collide across link IDs.

These tests run against the in-process fallback because pytest
doesn't always have a live Redis. The Redis path is the same code
in `backend/security.py::_redis_client`, exercised every time the
docker stack is up.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.cloud_sync_lock import (
    _INPROC_LOCKS,
    lock_key_for,
    release,
    sync_lock,
    try_acquire,
)


@pytest.fixture(autouse=True)
async def _reset_locks():
    """Clear BOTH the in-process lock table AND any Redis keys we
    might have left over from a previous test run.

    Without the Redis cleanup, re-running the suite against the live
    docker-compose stack would hit stale `cloud:sync:lock:test:*` /
    `cloud:sync:lock:1` keys from the prior run and the acquire
    tests would fail with "lock already held."
    """
    from backend.cloud_sync_lock import _redis_client

    async def _purge():
        _INPROC_LOCKS.clear()
        redis = await _redis_client()
        if redis is not None:
            try:
                # Wipe the test-scoped lock keys + the numeric ones
                # used by the integration-style tests (1, 2, 123,
                # 456). A scan-and-delete over the namespace is
                # cleaner than enumerating each.
                async for key in redis.scan_iter("cloud:sync:lock:*"):
                    await redis.delete(key)
            finally:
                await redis.aclose()

    await _purge()
    yield
    await _purge()


async def test_lock_key_distinct_per_link():
    """Different link IDs get different keys — otherwise two unrelated
    cloud accounts would block each other."""
    assert lock_key_for(1) != lock_key_for(2)
    assert lock_key_for(1) == "cloud:sync:lock:1"


async def test_acquire_then_release_round_trip():
    """First caller acquires; second caller fails; after release,
    third caller succeeds."""
    key = "cloud:sync:lock:test:basic"
    assert await try_acquire(key, ttl_seconds=60) is True
    assert await try_acquire(key, ttl_seconds=60) is False
    await release(key)
    assert await try_acquire(key, ttl_seconds=60) is True


async def test_concurrent_acquire_only_one_wins():
    """Race two acquires on the same key; only one should return
    True. The in-process fallback uses an asyncio.Lock so the race
    is deterministic; on Redis the SET NX is atomic at the server
    side."""
    key = "cloud:sync:lock:test:race"
    results = await asyncio.gather(
        try_acquire(key, ttl_seconds=60),
        try_acquire(key, ttl_seconds=60),
        try_acquire(key, ttl_seconds=60),
    )
    assert sum(1 for r in results if r) == 1


async def test_release_idempotent():
    """Releasing a key that was never held shouldn't raise. The
    contract is "best-effort drop" so callers don't have to track
    whether they ever acquired."""
    await release("cloud:sync:lock:test:never-held")
    # If this didn't raise, the test passes.


async def test_sync_lock_context_manager_success():
    """`async with sync_lock(link_id) as acquired:` — first entry
    returns True, second entry on the same link while the first is
    held returns False, and after the first exits the second can
    finally win."""
    held = sync_lock(123)
    acquired_outer = await held.__aenter__()
    assert acquired_outer is True

    contended = sync_lock(123)
    acquired_inner = await contended.__aenter__()
    assert acquired_inner is False
    # No need to release the contended one — `__aexit__` short-
    # circuits when `_acquired` is False, so it's idempotent.
    await contended.__aexit__(None, None, None)

    await held.__aexit__(None, None, None)

    # Now we can grab it again.
    third = sync_lock(123)
    acquired_third = await third.__aenter__()
    assert acquired_third is True
    await third.__aexit__(None, None, None)


async def test_sync_lock_releases_on_exception():
    """If the body of `async with sync_lock(...)` raises, the lock
    still drops so a subsequent acquire can succeed. Otherwise a
    sync that hit an unexpected exception would lock the link out
    until the TTL expired."""
    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError):
        async with sync_lock(456) as acquired:
            assert acquired is True
            raise _BoomError("simulated sync crash")

    # The lock should be released now.
    follow_up = sync_lock(456)
    acquired_after = await follow_up.__aenter__()
    assert acquired_after is True
    await follow_up.__aexit__(None, None, None)


async def test_distinct_links_dont_block_each_other():
    """Holding the lock on link 1 must not prevent acquiring link 2
    — otherwise a user with Drive + (future) Dropbox connections
    would have them serialised pointlessly."""
    one = sync_lock(1)
    two = sync_lock(2)
    assert await one.__aenter__() is True
    assert await two.__aenter__() is True
    await one.__aexit__(None, None, None)
    await two.__aexit__(None, None, None)


async def test_ttl_lets_stale_lock_clear(monkeypatch):
    """When a holder dies without releasing, the next caller should
    be able to acquire after the TTL elapses.

    Exercises the in-process fallback explicitly (force `_redis_client`
    to return None) since Redis's SET NX EX has its own server-side
    TTL — testing that path properly requires a real Redis and is
    covered by manual smoke testing instead."""
    import backend.cloud_sync_lock as mod

    async def _no_redis():
        return None

    monkeypatch.setattr(mod, "_redis_client", _no_redis)

    real_time = mod.time.time
    base = real_time()

    monkeypatch.setattr(mod.time, "time", lambda: base)
    assert await try_acquire("cloud:sync:lock:test:ttl", ttl_seconds=30) is True
    # Same instant — second caller blocked.
    assert await try_acquire("cloud:sync:lock:test:ttl", ttl_seconds=30) is False

    # Fast-forward past the TTL. The next acquire should win because
    # the existing entry's expires_at is in the past.
    monkeypatch.setattr(mod.time, "time", lambda: base + 31)
    assert await try_acquire("cloud:sync:lock:test:ttl", ttl_seconds=30) is True
