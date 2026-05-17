"""Redis-backed job queue for offloading ML inference to a worker, with
per-user fair scheduling.

# Why per-user fairness

The earlier shape was a single Redis list (`neuthek:jobs`) processed
FIFO by BLPOP. With one ML worker holding the GPU thread, a user who
hit "Reclassify entire library" first pushed 500 jobs onto the queue
and starved every other user behind them — even a single new upload
from another account waited for the whole 500-job pass to finish
before its summary started. That's the "it's a mess" the user
reported.

The new shape splits the queue per user and round-robins through
active users. Per-user concurrency cap of 1 means: as soon as one
user's job lands, the worker moves on to the next user with pending
work; the first user only gets another slot after every other active
user has had a turn. The result is "fair share of the model" rather
than "first in, first served forever".

# Anti-spam

Two limits keep a malicious or runaway client from filling Redis:

- `PER_USER_QUEUE_LIMIT` (1000): we refuse to enqueue if the user
  already has this many pending jobs. The backfill endpoints honor
  this — they batch up to the limit, then stop and report counts.
- The HTTP-side rate limits in `backend/security.py` cap how often a
  user can ASK for more work (backfill / resummarize / etc.). Even
  inside the queue limit, the rate limiter prevents a script from
  sustaining a high enqueue rate.

# Keys

  neuthek:jobs:active            SET    <kind>:<image_id> in-flight dedup
  neuthek:jobs:user:<user_id>    LIST   per-user FIFO queue
  neuthek:jobs:active_users      ZSET   user_id -> last_dequeue_ts (for RR)
  neuthek:jobs:inflight:<uid>    STR    counter of in-flight jobs per user

# Failure mode

If Redis is down, enqueue returns False and the caller falls back to
the inline asyncio.create_task path same as before. The worker idles
on poll errors.

# Worker contract

The worker calls `fair_dequeue()` to get the next job (returns a job
dict that includes `user_id`, or None after the timeout) and
`mark_done(user_id, kind, image_id)` after processing — even on
failure — so the dedupe set + inflight counter clear.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from backend.config import settings

logger = logging.getLogger(__name__)

# Legacy key — kept around so the admin /admin/system endpoint can
# still call queue_depth() and get a number that includes any
# pre-rewrite jobs that landed in the old single list before the
# new fair-queue shape took effect. New jobs land in per-user keys.
JOB_QUEUE_KEY = "neuthek:jobs"
ACTIVE_SET_KEY = "neuthek:jobs:active"
ACTIVE_USERS_KEY = "neuthek:jobs:active_users"


# Per-user knobs. Conservative defaults — a typical user firing one
# backfill against 500 photos fits inside the queue cap and the
# concurrency-of-1 means one user can never starve the others.
PER_USER_QUEUE_LIMIT = 1000   # max pending jobs per user
PER_USER_INFLIGHT_CAP = 1     # max simultaneous jobs per user on the GPU thread
INFLIGHT_TTL_SECONDS = 3600   # safety expiry in case mark_done is missed

# Worker dequeue polling cadence when no user has work eligible to run.
# Tight enough to feel responsive; loose enough to not hammer Redis.
_DEQUEUE_POLL_SECONDS = 0.5


_redis: aioredis.Redis | None = None


def _client() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _user_queue_key(user_id: str) -> str:
    return f"neuthek:jobs:user:{user_id}"


def _inflight_key(user_id: str) -> str:
    return f"neuthek:jobs:inflight:{user_id}"


# --- enqueue --------------------------------------------------------------

async def _enqueue(user_id: str, payload: dict[str, Any], dedupe_key: str) -> bool:
    """Push a job into the named user's queue.

    Three gates, in order:
      1. Redis SADD on the dedupe key — returns False if a job for the
         same image is already pending/in-flight.
      2. Per-user queue cap — refuse if the user has >= 1000 pending.
      3. RPUSH onto the user's list + register them in active_users
         with NX so we don't reset their round-robin position.

    Returns True only on a successful push.
    """
    try:
        client = _client()
        # 1. Dedupe — atomic enough on its own; SADD-then-RPUSH lives
        #    or dies as a pair (we SREM the dedupe key if push fails).
        added = await client.sadd(ACTIVE_SET_KEY, dedupe_key)
        if added == 0:
            return False
        # 2. Per-user queue cap — anti-spam. The caller (backfill etc.)
        #    can re-try after the worker drains, but a script can't
        #    push us into millions of pending rows.
        depth = int(await client.llen(_user_queue_key(user_id)) or 0)
        if depth >= PER_USER_QUEUE_LIMIT:
            await client.srem(ACTIVE_SET_KEY, dedupe_key)
            logger.info(
                "jobs.enqueue: user %s at queue cap (%d) — refusing %s",
                user_id, depth, payload.get("kind"),
            )
            return False
        # 3. Push + register. NX on the zadd preserves whatever
        #    last-dequeue score this user already had, so a user who
        #    just got served doesn't jump back to the front by adding
        #    a new job. New users come in at score=0 so they're at the
        #    front of the round-robin until they get their first slot.
        await client.rpush(_user_queue_key(user_id), json.dumps(payload))
        await client.zadd(ACTIVE_USERS_KEY, {user_id: 0.0}, nx=True)
        return True
    except Exception:
        logger.exception("jobs: enqueue failed for %s", payload.get("kind"))
        # Best-effort cleanup so a flaky push doesn't permanently lock
        # the image out of being re-enqueued.
        try: await _client().srem(ACTIVE_SET_KEY, dedupe_key)
        except Exception: pass
        return False


async def mark_done(user_id: str, kind: str, image_id: str) -> None:
    """Worker calls this after a job completes (success or failure) so
    the next enqueue for the same image can land and the user becomes
    eligible for another slot.
    """
    try:
        client = _client()
        # Pipeline both operations — they're independent and one
        # round-trip is fine.
        pipe = client.pipeline()
        pipe.srem(ACTIVE_SET_KEY, f"{kind}:{image_id}")
        pipe.decr(_inflight_key(user_id))
        await pipe.execute()
        # Floor at zero — a defensive DECR floor for the case where
        # mark_done is called twice for the same job (shouldn't happen
        # but worth not letting the counter wedge negative).
        val = await client.get(_inflight_key(user_id))
        if val is not None and int(val) < 0:
            await client.set(_inflight_key(user_id), 0, ex=INFLIGHT_TTL_SECONDS)
    except Exception:
        logger.exception("jobs.mark_done: cleanup failed for %s/%s", kind, image_id)


# --- public enqueue API ---------------------------------------------------

async def enqueue_summarize(user_id: UUID, image_id: UUID) -> bool:
    """Queue a Florence-2 + Qwen summarize for one image.

    `user_id` is now required so the fair scheduler can isolate this
    user's queue. Old call sites that passed only image_id need
    updating — they should already have access to the owning user
    (every Image row has user_id NOT NULL).
    """
    return await _enqueue(
        str(user_id),
        {"kind": "summarize", "image_id": str(image_id), "user_id": str(user_id)},
        f"summarize:{image_id}",
    )


async def enqueue_face_scan(user_id: UUID, image_id: UUID) -> bool:
    return await _enqueue(
        str(user_id),
        {"kind": "face_scan", "image_id": str(image_id), "user_id": str(user_id)},
        f"face_scan:{image_id}",
    )


async def enqueue_face_scan_then_summarize(user_id: UUID, image_id: UUID) -> bool:
    return await _enqueue(
        str(user_id),
        {
            "kind": "face_scan_then_summarize",
            "image_id": str(image_id),
            "user_id": str(user_id),
        },
        # The dedupe key shares the `summarize:` prefix so the
        # subsequent /resummarize call can't double-enqueue while the
        # combined job is still in flight.
        f"summarize:{image_id}",
    )


# --- worker-side dequeue --------------------------------------------------

async def fair_dequeue(timeout: float = 5.0) -> dict | None:
    """Return the next job to process, picking across users round-robin.

    Algorithm:
      - List active users ordered by last_dequeue_ts ascending.
      - For each candidate, skip if their inflight count is at the cap.
      - LPOP their queue. If the list is now empty, ZREM from
        active_users so we don't bother again on the next tick.
      - Atomically bump the user's last_dequeue_ts (back of round-robin)
        and increment inflight.
      - Return the decoded job dict.

    Returns None when nothing is eligible within `timeout` seconds.
    The worker treats None the same as "no jobs" and loops again.
    """
    client = _client()
    deadline = time.monotonic() + timeout
    while True:
        try:
            user_ids = await client.zrange(ACTIVE_USERS_KEY, 0, -1)
        except Exception:
            logger.exception("jobs.fair_dequeue: zrange failed")
            await asyncio.sleep(_DEQUEUE_POLL_SECONDS)
            if time.monotonic() >= deadline:
                return None
            continue

        for user_id in user_ids:
            # Concurrency cap — one in-flight per user keeps the
            # round-robin moving. Bigger cap would let a single user
            # double-dip when others are idle, which is fine for
            # throughput but bad for fairness under load.
            try:
                inflight = int(await client.get(_inflight_key(user_id)) or 0)
            except Exception:
                inflight = 0
            if inflight >= PER_USER_INFLIGHT_CAP:
                continue

            try:
                raw = await client.lpop(_user_queue_key(user_id))
            except Exception:
                logger.exception("jobs.fair_dequeue: lpop failed for %s", user_id)
                continue
            if raw is None:
                # Race: queue drained between zrange and lpop.
                # Clean up the active set so we don't pick this user
                # again next tick.
                try: await client.zrem(ACTIVE_USERS_KEY, user_id)
                except Exception: pass
                continue

            # Reorder: this user just got served, push them to the
            # back of the round-robin (highest score wins next-skip).
            now = time.monotonic()
            try:
                pipe = client.pipeline()
                pipe.zadd(ACTIVE_USERS_KEY, {user_id: now})
                pipe.incr(_inflight_key(user_id))
                pipe.expire(_inflight_key(user_id), INFLIGHT_TTL_SECONDS)
                await pipe.execute()
            except Exception:
                logger.exception(
                    "jobs.fair_dequeue: post-pop bookkeeping failed for %s", user_id
                )

            # Drop the user from active_users if their queue is now
            # empty — keeps zrange cheap as users come and go.
            try:
                depth = int(await client.llen(_user_queue_key(user_id)) or 0)
                if depth == 0:
                    await client.zrem(ACTIVE_USERS_KEY, user_id)
            except Exception:
                pass

            try:
                job = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "jobs.fair_dequeue: invalid json on user %s queue: %r",
                    user_id, raw[:200],
                )
                # Undo the inflight increment since we won't process it.
                try: await client.decr(_inflight_key(user_id))
                except Exception: pass
                continue

            return job

        # Nothing eligible this tick. Sleep then retry until timeout.
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(_DEQUEUE_POLL_SECONDS)


# --- diagnostics for admin dashboard -------------------------------------

async def total_queue_depth() -> int:
    """Sum of pending jobs across every per-user queue."""
    try:
        client = _client()
        user_ids = await client.zrange(ACTIVE_USERS_KEY, 0, -1)
        if not user_ids:
            # Honor any leftover jobs on the legacy list, too.
            return int(await client.llen(JOB_QUEUE_KEY) or 0)
        pipe = client.pipeline()
        for uid in user_ids:
            pipe.llen(_user_queue_key(uid))
        sizes = await pipe.execute()
        return sum(int(s or 0) for s in sizes)
    except Exception:
        return -1


# Back-compat: a lot of admin code calls `queue_depth()` (old name).
async def queue_depth() -> int:
    return await total_queue_depth()


async def per_user_queue_depths(limit: int = 50) -> list[dict]:
    """Snapshot of per-user pending counts + inflight counters.

    Sorted by `pending` descending so the dashboard surfaces the
    biggest queues first. Returns at most `limit` users; the
    administrative dashboard typically shows top-50.
    """
    try:
        client = _client()
        user_ids = await client.zrange(ACTIVE_USERS_KEY, 0, -1)
        if not user_ids:
            return []
        pipe = client.pipeline()
        for uid in user_ids:
            pipe.llen(_user_queue_key(uid))
        sizes = await pipe.execute()
        pipe = client.pipeline()
        for uid in user_ids:
            pipe.get(_inflight_key(uid))
        inflights = await pipe.execute()
        # Get last-dequeue scores too so admins can see who's been
        # served recently vs. waiting forever.
        scores = await client.zrange(
            ACTIVE_USERS_KEY, 0, -1, withscores=True
        )
        score_by_uid = {uid: s for uid, s in scores}
        rows = [
            {
                "user_id": uid,
                "pending": int(s or 0),
                "inflight": int(i or 0),
                "last_dequeue_score": score_by_uid.get(uid, 0.0),
            }
            for uid, s, i in zip(user_ids, sizes, inflights)
        ]
        rows.sort(key=lambda r: r["pending"], reverse=True)
        return rows[:limit]
    except Exception:
        logger.exception("jobs.per_user_queue_depths: failed")
        return []


async def drain_user_queue(user_id: UUID) -> dict:
    """Admin-only — remove every pending job for one user.

    Useful when a backfill went sideways or a user is stuck behind a
    crashed job. Doesn't affect in-flight work; the worker finishes
    what it's already pulled.
    """
    try:
        client = _client()
        uid = str(user_id)
        pipe = client.pipeline()
        pipe.llen(_user_queue_key(uid))
        pipe.delete(_user_queue_key(uid))
        pipe.zrem(ACTIVE_USERS_KEY, uid)
        depth, _, _ = await pipe.execute()
        return {"user_id": uid, "removed": int(depth or 0)}
    except Exception:
        logger.exception("jobs.drain_user_queue failed")
        return {"user_id": str(user_id), "removed": 0, "error": True}


async def aclose() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        finally:
            _redis = None
