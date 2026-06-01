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

import time

import redis.asyncio as aioredis

from backend.config import settings

logger = logging.getLogger(__name__)

JOB_QUEUE_KEY = "neuthek:jobs"
# Reliable-queue companions (production hardening). The worker LMOVEs a
# job from JOB_QUEUE_KEY to JOB_INFLIGHT_KEY before processing and LREMs
# it on completion. A job that's still in the in-flight list past the
# visibility timeout means the worker died mid-process; the reaper
# requeues it. A job that fails `job_max_attempts` times is parked in
# JOB_DEAD_KEY instead of looping forever or being silently lost.
JOB_INFLIGHT_KEY = "neuthek:jobs:inflight"
JOB_DEAD_KEY = "neuthek:jobs:dead"
JOB_ACTIVE_SET = "neuthek:jobs:active"
# Sorted set: dedupe_key -> epoch-seconds when its job was last enqueued
# OR moved in-flight. Lets the reaper find dedupe keys that leaked (the
# worker process died before `mark_done` could SREM them) and clear them
# so the row can be re-enqueued. Without this, a crash between BLPOP and
# completion permanently locks an image out of re-processing.
JOB_ACTIVE_TS = "neuthek:jobs:active_ts"

_redis: aioredis.Redis | None = None

# Strong refs for detached delayed-retry tasks so they aren't GC'd before
# they re-push (asyncio.create_task only holds a weak ref). Mirrors the
# pattern in api/images.py:_BACKGROUND_TASKS.
_RETRY_TASKS: set[asyncio.Task] = set()


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

    Backpressure: if the live queue is already at `job_queue_max_depth`
    we refuse the enqueue (return False). The caller leaves the row
    `pending_*` and the reaper / FE drainer pick it up once the queue
    drains — so a runaway producer can't grow Redis without bound.

    Returns True if pushed, False if either Redis is down OR the job is
    already in-flight OR the queue is over its depth cap (caller treats
    all three the same — no-op, row stays pending).
    """
    try:
        client = _client()
        # Backpressure check (best-effort; never blocks the hot path).
        cap = getattr(settings, "job_queue_max_depth", 0) or 0
        if cap > 0:
            try:
                depth = int(await client.llen(JOB_QUEUE_KEY))
                if depth >= cap:
                    logger.warning(
                        "jobs: queue at depth %d >= cap %d — dropping %s "
                        "enqueue (row stays pending for the reaper)",
                        depth, cap, payload.get("kind"),
                    )
                    return False
            except Exception:
                pass  # depth probe failed — fall through and try to enqueue
        added = await client.sadd(JOB_ACTIVE_SET, dedupe_key)
        if added == 0:
            return False  # already pending — drop silently
        # Stamp the active-timestamp ZSET so the reaper can detect a
        # leaked dedupe key (crash before mark_done). Carries the payload
        # so the reaper can re-enqueue the lost job rather than only
        # clearing the lock. Best-effort — a failure here doesn't block.
        try:
            await client.zadd(JOB_ACTIVE_TS, {dedupe_key: time.time()})
            await client.hset(
                "neuthek:jobs:payloads", dedupe_key, json.dumps(payload),
            )
        except Exception:
            pass
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

    Clears the dedupe key from the active set AND the active-timestamp
    ZSET + payload hash so a leaked-key reaper doesn't later think this
    job is still in-flight. All three removes are best-effort and never
    raise into the worker loop.
    """
    key = f"{kind}:{image_id}"
    try:
        client = _client()
        await client.srem(JOB_ACTIVE_SET, key)
        await client.zrem(JOB_ACTIVE_TS, key)
        await client.hdel("neuthek:jobs:payloads", key)
    except Exception:
        pass


async def enqueue_summarize(user_or_image_id: UUID, image_id: UUID | None = None) -> bool:
    """Queue a summarize job. Accepts EITHER ``(image_id)`` or
    ``(user_id, image_id)``.

    Most call sites pass the owning ``user_id`` first for parity with the
    other ``enqueue_*`` helpers (enqueue_face_scan, …), but a summarize job
    only needs the image_id — the worker reads the row's owner from the DB.
    The single-arg form (``enqueue_summarize(image_id)``) is kept for the
    cloud-sync caller.

    Supporting both signatures is the fix for a TypeError that made every
    two-arg caller fall back to running Florence-2 / Qwen INLINE in the API
    process; on a 500-image backfill that loaded the heavy models hundreds
    of times in one process and hard-crashed the API. With this, the work
    correctly lands on the ml-worker queue instead.
    """
    iid = image_id if image_id is not None else user_or_image_id
    return await _enqueue({"kind": "summarize", "image_id": str(iid)})


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


async def enqueue_convert_office(user_id: UUID, image_id: UUID) -> bool:
    """Queue a headless-LibreOffice render of an Office document to PDF.

    The worker pulls the original (docx/xlsx/pptx/odt/ods/odp/rtf/doc/…)
    from MinIO, runs `libreoffice --headless --convert-to pdf`, uploads
    the resulting PDF to the served bucket, and stores its key in
    `images.converted_pdf_blob_key`. After that lands the existing PDF
    page-rasterization endpoints serve pages from the converted PDF, so
    the Office file renders through the same server-rasterized PDF viewer
    as a native PDF.

    Deduped on `convert_office:<image_id>` via the shared active-set so a
    re-sync / progress-poll storm can't pile up duplicate conversions for
    the same row. `user_id` is carried for parity with the other
    enqueue_* helpers (and so a future per-user scoping has it), though
    the worker resolves the owner from the row.
    """
    return await _enqueue(
        {
            "kind": "convert_office",
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


# --------------------------------------------------------------------------
# Reliable-queue consumer primitives (crash-safety + retry + dead-letter)
# --------------------------------------------------------------------------
#
# The previous consumer used a plain destructive BLPOP: a job that the
# worker popped but didn't finish (process killed, container OOM, host
# reboot mid-Florence) vanished — no retry, no trace. These helpers give
# the worker an at-least-once contract instead:
#
#   reserve_job()  — BLMOVE from JOB_QUEUE_KEY to JOB_INFLIGHT_KEY. The
#                    job is atomically reserved: it's off the ready queue
#                    but still durable in the in-flight list until acked.
#   ack_job()      — LREM the exact payload from JOB_INFLIGHT_KEY after a
#                    successful run.
#   retry_or_dead()— on failure, bump the attempt counter; re-enqueue
#                    with exponential backoff if under the cap, else move
#                    the payload to JOB_DEAD_KEY (dead-letter) so it's
#                    preserved for inspection instead of looping forever.
#   reap_inflight()— requeue jobs orphaned in JOB_INFLIGHT_KEY past the
#                    visibility timeout (the worker that held them died).
#   reap_dedupe()  — clear leaked dedupe keys (active-set entries whose
#                    job never acked) so the row can be re-enqueued.
#
# All are defensive: any Redis error is logged and swallowed so the
# worker loop keeps turning.


def _attempts_key(raw_payload: str) -> str:
    """Per-job attempt counter key. Hashed on the payload so the same
    logical job (same kind+image) shares a counter across retries even
    though the JSON string is byte-identical each time."""
    import hashlib

    h = hashlib.sha1(raw_payload.encode("utf-8")).hexdigest()[:16]
    return f"neuthek:jobs:attempts:{h}"


async def reserve_job(timeout: int = 5) -> str | None:
    """Atomically move one job from the ready queue to the in-flight
    list and return its raw JSON payload, or None on timeout.

    Uses BLMOVE (Redis 6.2+) so the reservation is a single atomic op —
    the job is never in a state where it's been removed from the ready
    queue but not yet recorded as in-flight. On a Redis older than 6.2
    we fall back to the legacy destructive BLPOP (at-most-once) so the
    worker still functions, just without crash-safety.
    """
    client = _client()
    try:
        raw = await client.blmove(
            JOB_QUEUE_KEY, JOB_INFLIGHT_KEY, timeout, "LEFT", "RIGHT",
        )
        return raw
    except Exception as exc:
        # Older Redis (no BLMOVE) or a transient error — fall back to the
        # legacy pop so the pipeline keeps moving. Logged once at debug.
        logger.debug("reserve_job: BLMOVE unavailable (%s); BLPOP fallback", exc)
        try:
            popped = await client.blpop(JOB_QUEUE_KEY, timeout=timeout)
            if popped is None:
                return None
            _key, raw = popped
            # Mirror into in-flight so the watchdog/ack path is uniform.
            try:
                await client.rpush(JOB_INFLIGHT_KEY, raw)
            except Exception:
                pass
            return raw
        except Exception:
            logger.exception("reserve_job: BLPOP fallback failed")
            return None


async def ack_job(raw_payload: str) -> None:
    """Remove a finished job from the in-flight list + drop its attempt
    counter. Best-effort."""
    try:
        client = _client()
        await client.lrem(JOB_INFLIGHT_KEY, 1, raw_payload)
        await client.delete(_attempts_key(raw_payload))
    except Exception:
        logger.debug("ack_job: cleanup failed", exc_info=True)


async def retry_or_dead(raw_payload: str, error: str) -> str:
    """Decide a failed job's fate: retry with backoff, or dead-letter.

    Increments the per-job attempt counter. If attempts < max, the job
    is removed from in-flight and re-pushed to the ready queue (after an
    exponential-backoff sleep so a transiently-failing job doesn't hot-
    loop). If attempts >= max, the payload is moved to JOB_DEAD_KEY with
    a wrapper recording the error + attempt count, and its dedupe key is
    cleared so the row isn't permanently locked.

    Returns "retried" or "dead" (for the worker's log line).
    """
    client = _client()
    max_attempts = max(1, int(getattr(settings, "job_max_attempts", 5)))
    try:
        attempts = int(await client.incr(_attempts_key(raw_payload)))
        # Expire the counter so a never-acked counter can't leak forever.
        await client.expire(_attempts_key(raw_payload), 86400)
    except Exception:
        attempts = max_attempts  # if we can't count, fail safe to dead-letter

    # Always take it out of in-flight first — we either re-push it or
    # dead-letter it below; leaving it in-flight would let the reaper
    # double-handle it.
    try:
        await client.lrem(JOB_INFLIGHT_KEY, 1, raw_payload)
    except Exception:
        pass

    if attempts < max_attempts:
        base = float(getattr(settings, "job_retry_backoff_base_seconds", 5.0))
        cap = float(getattr(settings, "job_retry_backoff_max_seconds", 300.0))
        delay = min(cap, base * (2 ** (attempts - 1)))
        logger.warning(
            "jobs: retry %d/%d in %.0fs after error: %s",
            attempts, max_attempts, delay, error[:200],
        )

        # Re-push AFTER the backoff, but do it in a DETACHED task so the
        # (single-threaded) worker consume loop isn't blocked for `delay`
        # seconds — otherwise a job in its 5th backoff (up to 300s) would
        # freeze processing of every other queued job. The job is already
        # out of the in-flight list, so if the process dies during the
        # backoff window the job is genuinely dropped — but that's the
        # same exposure window as the legacy code and far smaller than the
        # "lost forever on any failure" behavior we're replacing. The
        # stuck-pending reaper is the catch-all backstop for that rare case.
        async def _delayed_repush() -> None:
            try:
                await asyncio.sleep(delay)
                await _client().rpush(JOB_QUEUE_KEY, raw_payload)
            except Exception:
                logger.exception("retry_or_dead: delayed re-enqueue failed")

        try:
            task = asyncio.create_task(_delayed_repush())
            _RETRY_TASKS.add(task)
            task.add_done_callback(_RETRY_TASKS.discard)
        except Exception:
            # No running loop / scheduling failed — fall back to inline.
            try:
                await asyncio.sleep(delay)
                await client.rpush(JOB_QUEUE_KEY, raw_payload)
            except Exception:
                logger.exception("retry_or_dead: re-enqueue failed")
        return "retried"

    # Exhausted — dead-letter it (capped list so it can't grow without
    # bound either) and clear the dedupe lock + attempt counter.
    try:
        wrapper = json.dumps({
            "payload": json.loads(raw_payload) if raw_payload else None,
            "error": error[:1000],
            "attempts": attempts,
            "dead_at": time.time(),
        })
    except Exception:
        wrapper = json.dumps({"payload_raw": raw_payload[:1000], "error": error[:1000]})
    try:
        await client.rpush(JOB_DEAD_KEY, wrapper)
        # Keep the dead-letter list bounded (newest 10k).
        await client.ltrim(JOB_DEAD_KEY, -10_000, -1)
        await client.delete(_attempts_key(raw_payload))
    except Exception:
        logger.exception("retry_or_dead: dead-letter push failed")
    # Clear the dedupe lock for this job so the row can be re-enqueued by
    # a future request / the reaper (otherwise a poison job locks the
    # image out of ALL future processing).
    try:
        job = json.loads(raw_payload)
        key = f"{job.get('kind')}:{job.get('image_id')}"
        await client.srem(JOB_ACTIVE_SET, key)
        await client.zrem(JOB_ACTIVE_TS, key)
        await client.hdel("neuthek:jobs:payloads", key)
    except Exception:
        pass
    logger.error(
        "jobs: dead-lettered after %d attempts: %s", attempts, error[:200],
    )
    return "dead"


async def reap_inflight() -> int:
    """Requeue jobs orphaned in the in-flight list.

    A job sits in JOB_INFLIGHT_KEY only while a worker is processing it.
    If a worker dies mid-process, its job is stranded there. We can't tell
    per-entry how long it's been stranded (Redis lists carry no
    timestamp), so we use a generation marker: any entry present in the
    in-flight list across two reaper passes spaced >= the visibility
    timeout is presumed orphaned and moved back to the ready queue.

    Implementation: snapshot the in-flight list into a 'seen' set keyed by
    a Redis hash with first-seen timestamps. On each pass, any entry whose
    first-seen is older than the visibility timeout gets requeued.
    Returns the number of jobs requeued.
    """
    client = _client()
    vis = int(getattr(settings, "job_visibility_timeout_seconds", 1800))
    seen_key = "neuthek:jobs:inflight_seen"
    requeued = 0
    try:
        entries = await client.lrange(JOB_INFLIGHT_KEY, 0, -1)
    except Exception:
        logger.debug("reap_inflight: lrange failed", exc_info=True)
        return 0
    now = time.time()
    live: set[str] = set(entries)
    # Record first-seen for any new entry; prune first-seen for entries
    # no longer in-flight (they were acked).
    try:
        recorded = await client.hgetall(seen_key)
    except Exception:
        recorded = {}
    # Drop stale bookkeeping for acked jobs.
    for k in list(recorded.keys()):
        if k not in live:
            try:
                await client.hdel(seen_key, k)
            except Exception:
                pass
    for raw in live:
        first_seen = recorded.get(raw)
        if first_seen is None:
            try:
                await client.hset(seen_key, raw, now)
            except Exception:
                pass
            continue
        try:
            age = now - float(first_seen)
        except (TypeError, ValueError):
            age = 0.0
        if age >= vis:
            # Orphaned — move it back to the ready queue and forget the
            # bookkeeping so a fresh worker re-reserves it cleanly.
            try:
                removed = await client.lrem(JOB_INFLIGHT_KEY, 1, raw)
                if removed:
                    await client.rpush(JOB_QUEUE_KEY, raw)
                    requeued += 1
                await client.hdel(seen_key, raw)
            except Exception:
                logger.exception("reap_inflight: requeue failed")
    if requeued:
        logger.warning("jobs: reaped %d orphaned in-flight job(s)", requeued)
    return requeued


async def reap_dedupe_keys() -> int:
    """Clear (and re-enqueue) dedupe keys that leaked.

    A dedupe key in JOB_ACTIVE_SET should be removed by `mark_done` when
    its job finishes. If the worker process dies between reserving the
    job and `mark_done`, the key is stranded — and because `_enqueue_*`
    refuses to enqueue while the key is present, that image is locked out
    of ALL future processing. This is the exact pathology that left a
    `summarize:<id>` key stuck in production with an empty queue.

    We use JOB_ACTIVE_TS (a ZSET of dedupe_key -> last-touch epoch). Any
    key whose timestamp is older than the visibility timeout AND is not
    currently represented by an in-flight job is presumed leaked. We
    re-enqueue its stored payload (if any) and clear the lock.

    Returns the number of leaked keys cleared.
    """
    client = _client()
    vis = int(getattr(settings, "job_visibility_timeout_seconds", 1800))
    cleared = 0
    now = time.time()
    cutoff = now - vis
    # Backfill: active-set members with NO timestamp in the ZSET are
    # either legacy leaks (enqueued before this bookkeeping existed) or a
    # key whose zadd failed. Stamp them at `cutoff` so they become
    # eligible on THIS pass if they're not actually queued — this is what
    # reclaims a key already stuck before the reaper shipped.
    try:
        members = await client.smembers(JOB_ACTIVE_SET)
        for m in members:
            score = await client.zscore(JOB_ACTIVE_TS, m)
            if score is None:
                await client.zadd(JOB_ACTIVE_TS, {m: cutoff})
    except Exception:
        logger.debug("reap_dedupe_keys: backfill failed", exc_info=True)
    try:
        stale = await client.zrangebyscore(JOB_ACTIVE_TS, 0, cutoff)
    except Exception:
        logger.debug("reap_dedupe_keys: zrangebyscore failed", exc_info=True)
        return 0
    if not stale:
        return 0
    # Build the set of dedupe keys currently represented in-flight or
    # ready, so we never clear a key whose job is legitimately queued.
    busy: set[str] = set()
    try:
        for raw in (
            await client.lrange(JOB_INFLIGHT_KEY, 0, -1)
            + await client.lrange(JOB_QUEUE_KEY, 0, -1)
        ):
            try:
                j = json.loads(raw)
                busy.add(f"{j.get('kind')}:{j.get('image_id')}")
            except Exception:
                continue
    except Exception:
        pass
    for key in stale:
        if key in busy:
            # Job is actually queued/in-flight; refresh its timestamp so
            # we don't keep re-examining it every pass.
            try:
                await client.zadd(JOB_ACTIVE_TS, {key: time.time()})
            except Exception:
                pass
            continue
        # Leaked. Try to re-enqueue the stored payload, then clear lock.
        payload_raw = None
        try:
            payload_raw = await client.hget("neuthek:jobs:payloads", key)
        except Exception:
            pass
        try:
            await client.srem(JOB_ACTIVE_SET, key)
            await client.zrem(JOB_ACTIVE_TS, key)
            await client.hdel("neuthek:jobs:payloads", key)
        except Exception:
            pass
        if payload_raw:
            try:
                # Re-enqueue through the dedupe path so backpressure +
                # the freshly-cleared lock both apply cleanly.
                job = json.loads(payload_raw)
                await _enqueue_dedupe(job, key)
            except Exception:
                logger.exception("reap_dedupe_keys: re-enqueue failed for %s", key)
        cleared += 1
    if cleared:
        logger.warning("jobs: cleared %d leaked dedupe key(s)", cleared)
    return cleared


async def dead_letter_depth() -> int:
    """Size of the dead-letter list (for diagnostics / admin)."""
    try:
        return int(await _client().llen(JOB_DEAD_KEY))
    except Exception:
        return -1


async def aclose() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        finally:
            _redis = None
