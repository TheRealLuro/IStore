"""C8.2 — worker heartbeats + model_runs write helpers.

`emit_heartbeat` upserts a row into worker_heartbeats every 30 s so the
admin Processes tab can see workers running in sibling containers
(psutil only sees the API's own process namespace).

`record_model_state` writes a model_runs row whenever an ml-worker
loads, unloads, or errors a model — the admin Models tab reads the
latest row per (model_id, worker_id) so it can show actual device +
memory state rather than just configuration.

Both helpers are best-effort: any DB write that fails is logged and
swallowed so a downed Postgres doesn't take the worker offline. The
heartbeat loop returns silently when the worker is told to shut down.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default 30 s heartbeat. The admin /admin/processes endpoint considers
# a row stale at 4× this interval, so a single missed beat doesn't take
# a worker offline; two consecutive misses does.
HEARTBEAT_INTERVAL_SECONDS = 30


def _build_worker_id(kind: str) -> str:
    """Deterministic per-pod identifier — hostname + kind + pid. The
    hostname segment lets a single docker-compose service replicate to
    N containers without collisions. PID is included for the rare case
    where two workers of the same kind run on the same host (dev)."""
    return f"{platform.node() or 'localhost'}/{kind}/{os.getpid()}"


async def emit_heartbeat(
    *,
    kind: str = "ml-worker",
    worker_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Upsert one heartbeat row. Safe to call repeatedly; the ON
    CONFLICT clause keeps the table at exactly one row per worker_id."""
    try:
        from backend.db import SessionLocal
        from backend.models import WorkerHeartbeat
        from sqlalchemy.dialects.postgresql import insert as pg_insert
    except Exception:
        logger.exception("heartbeat: import failed (skipping)")
        return

    wid = worker_id or _build_worker_id(kind)
    try:
        async with SessionLocal() as session:
            stmt = pg_insert(WorkerHeartbeat).values(
                worker_id=wid,
                kind=kind,
                last_seen=datetime.now(timezone.utc),
                hostname=platform.node() or None,
                pid=os.getpid(),
                version="0.1.0",
                extra_metadata=metadata or {},
            )
            # `metadata` is a reserved SQLAlchemy attribute on Insert.excluded,
            # so we reach the column through dict-style access. The set_ key
            # must be the DB column name (`metadata`), not the ORM attribute
            # (`extra_metadata`) — the mapping is `mapped_column("metadata", JSONB)`.
            stmt = stmt.on_conflict_do_update(
                index_elements=["worker_id"],
                set_={
                    "last_seen": stmt.excluded.last_seen,
                    "kind": stmt.excluded.kind,
                    "hostname": stmt.excluded.hostname,
                    "pid": stmt.excluded.pid,
                    "version": stmt.excluded.version,
                    "metadata": stmt.excluded["metadata"],
                },
            )
            await session.execute(stmt)
            await session.commit()
    except Exception:
        logger.exception("heartbeat: write failed (continuing)")


async def heartbeat_loop(
    *,
    kind: str = "ml-worker",
    interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS,
    is_running: Optional[Any] = None,
    metadata_provider: Optional[Any] = None,
) -> None:
    """Background task: call `emit_heartbeat` every `interval_seconds`
    until the caller flips `is_running()` to False (or the task is
    cancelled). `metadata_provider`, if supplied, is called each tick
    so the worker can include live numbers — e.g. queue depth, last
    job kind — in the heartbeat without us having to thread them
    through every entry point."""
    wid = _build_worker_id(kind)
    logger.info("heartbeat: started for worker_id=%s every %ss", wid, interval_seconds)
    try:
        while is_running is None or is_running():
            meta = None
            if metadata_provider is not None:
                try:
                    meta = metadata_provider()
                    if asyncio.iscoroutine(meta):
                        meta = await meta
                except Exception:
                    logger.exception("heartbeat: metadata_provider raised (skipping)")
                    meta = None
            await emit_heartbeat(kind=kind, worker_id=wid, metadata=meta)
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
    finally:
        logger.info("heartbeat: stopped for worker_id=%s", wid)


# Dedicated NullPool sessionmaker for best-effort telemetry writes.
#
# record_model_state is fired from model loaders that run in a thread with
# NO running event loop (torch loads inside the inference-pool thread), so
# record_model_state_sync falls to `asyncio.run(...)` which spins a fresh,
# throwaway loop. The shared `backend.db` asyncpg engine pools connections
# bound to the app's MAIN loop — touching that pool from the throwaway loop
# corrupted connections ("got Future attached to a different loop" /
# "unknown protocol state"), which then 500'd unrelated requests (login,
# gallery) and spewed "connection is closed" rollbacks.
#
# NullPool opens a brand-new connection on whatever loop is current and
# closes it immediately, so it is always loop-correct and never shares
# state with the request-serving pool. Model-state writes are rare
# (load/loaded/unloaded/error) so the per-call connect cost is irrelevant.
_telemetry_sessionmaker = None


def _get_telemetry_sessionmaker():
    global _telemetry_sessionmaker
    if _telemetry_sessionmaker is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from backend.config import settings

        _telemetry_sessionmaker = async_sessionmaker(
            create_async_engine(settings.database_url, poolclass=NullPool),
            expire_on_commit=False,
        )
    return _telemetry_sessionmaker


async def record_model_state(
    *,
    model_id: str,
    state: str,
    device: Optional[str] = None,
    memory_allocated_bytes: Optional[int] = None,
    worker_id: Optional[str] = None,
    kind: str = "ml-worker",
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Insert a model_runs row. `state` is 'loading' | 'loaded' |
    'unloaded' | 'error'. The admin Models tab reads the latest row
    per (model_id, worker_id) for current device + memory.

    Called from `backend.vision.runtime` at model load time. Uses a
    NullPool engine (see above) so it is safe to invoke from a throwaway
    event loop without poisoning the shared connection pool. Failures are
    swallowed — we never want a transient DB hiccup to mask the real
    problem with the model itself."""
    try:
        from backend.models import ModelRun
    except Exception:
        return
    wid = worker_id or _build_worker_id(kind)
    try:
        Session = _get_telemetry_sessionmaker()
        async with Session() as session:
            session.add(ModelRun(
                model_id=model_id,
                worker_id=wid,
                state=state,
                device=device,
                memory_allocated_bytes=memory_allocated_bytes,
                last_used_at=datetime.now(timezone.utc) if state == "loaded" else None,
                extra_metadata=metadata or {},
            ))
            await session.commit()
    except Exception:
        logger.exception("model_runs: insert failed (continuing)")


def record_model_state_sync(**kwargs: Any) -> None:
    """Sync wrapper for `record_model_state` so non-async callers (e.g.
    a torch model loader inside the vision runtime) can fire-and-forget.
    Uses `asyncio.run` only when no loop is active; otherwise schedules
    on the running loop via `ensure_future`."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None or loop.is_closed():
        try:
            asyncio.run(record_model_state(**kwargs))
        except Exception:
            logger.exception("record_model_state_sync: asyncio.run failed")
    else:
        loop.create_task(record_model_state(**kwargs))
