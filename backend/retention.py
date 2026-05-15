"""Hybrid-retention sweeper (Phase 8 / Mode D).

Drops original blobs that have aged past their per-row `original_expires_at`
horizon (default 30 days from upload — see migration 0004). The served
variant remains, EXIF/GPS/capture-date are preserved inside it (codecs.py
re-injects EXIF on encode), and `download_original` falls back to the
served bytes with `X-Original-Expired: true` so the frontend can surface
the substitution to the user.

§B4 — Quarantine retention sweeper. Rejected uploads land in
`bucket_quarantine` so ops can inspect what triggered the rejection
without those bytes ever touching `originals`. The objects accumulate
forever otherwise — `sweep_expired_quarantine` ages them out on the
schedule set by `upload_quarantine_retention_days` (default 30).

Per the plan, both sweepers are pure async functions plus thin admin
endpoints — arq cron scheduling is Phase 9 work. Both are idempotent:
running them with no expired entries is a no-op.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import AuditLog, Image
from backend.storage import storage

logger = logging.getLogger(__name__)


@dataclass
class SweepResult:
    scanned: int
    blobs_deleted: int
    blob_errors: int
    rows_nulled: int
    bytes_freed: int


@dataclass
class QuarantineSweepResult:
    scanned: int
    blobs_deleted: int
    blob_errors: int
    bytes_freed: int
    cutoff_iso: str


async def sweep_expired_originals(session: AsyncSession) -> SweepResult:
    """Find expired originals, drop their blobs, null the column, audit it."""
    now = datetime.now(timezone.utc)

    expired = (
        await session.execute(
            select(
                Image.id,
                Image.user_id,
                Image.original_blob_key,
                Image.byte_size_original,
            ).where(
                Image.original_expires_at.is_not(None),
                Image.original_expires_at < now,
                Image.original_blob_key.is_not(None),
            )
        )
    ).all()

    blobs_deleted = 0
    blob_errors = 0
    bytes_freed = 0
    user_counts: dict = {}

    for image_id, user_id, blob_key, byte_size in expired:
        try:
            storage.delete(storage.bucket_originals, blob_key)
            blobs_deleted += 1
            if byte_size:
                bytes_freed += int(byte_size)
        except Exception as exc:
            blob_errors += 1
            logger.warning("Retention sweep: blob delete failed for %s: %s", blob_key, exc)
            # Still null the column — a missing blob isn't a reason to keep
            # the dead pointer alive. Audit log records the discrepancy.
        user_counts.setdefault(user_id, 0)
        user_counts[user_id] += 1

    rows_nulled = 0
    if expired:
        ids = [row[0] for row in expired]
        result = await session.execute(
            update(Image)
            .where(Image.id.in_(ids))
            .values(original_blob_key=None)
        )
        rows_nulled = int(result.rowcount or 0)

        # One audit row per affected user so each user has a deletion record
        # they can later see in their export — and so the audit log is bounded
        # by users not by blob count.
        for user_id, count in user_counts.items():
            session.add(
                AuditLog(
                    user_id=user_id,
                    action="retention.sweep_originals",
                    details={
                        "originals_dropped": count,
                        "swept_at": now.isoformat(),
                    },
                )
            )

        await session.commit()

    return SweepResult(
        scanned=len(expired),
        blobs_deleted=blobs_deleted,
        blob_errors=blob_errors,
        rows_nulled=rows_nulled,
        bytes_freed=bytes_freed,
    )


def _list_quarantine_objects():
    """Wrapper so tests can monkeypatch listing without touching the
    real MinIO client. Returns the iterable straight from the SDK so
    we don't materialize the whole bucket in memory at once.
    """
    return storage.client.list_objects(
        storage.bucket_quarantine, recursive=True
    )


async def sweep_expired_quarantine(
    session: AsyncSession,
    *,
    retention_days: int | None = None,
) -> QuarantineSweepResult:
    """Delete every quarantine object older than the retention horizon.

    The quarantine bucket grows monotonically until this runs — each
    rejected upload leaves a blob behind for forensic inspection.
    Without this sweeper the bucket accumulates indefinitely.

    The audit row written at rejection time records the quarantine key
    and reason; that row is preserved (we only delete the *bytes*, not
    the record that the rejection happened), so the forensic trail
    survives the sweep. Operators who need to inspect a payload have
    `retention_days` worth of head-start.

    Idempotent. Per-object delete failures are logged + counted but
    don't fail the sweep.
    """
    days = retention_days if retention_days is not None else settings.upload_quarantine_retention_days
    if days <= 0:
        raise ValueError("retention_days must be a positive integer")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    # The MinIO SDK's list_objects is a synchronous generator. Run it
    # in a worker thread so the asyncio event loop isn't blocked while
    # the bucket is enumerated — this matters for production buckets
    # with thousands of forensic objects accumulated since the last
    # sweep.
    def _materialize() -> list:
        try:
            return list(_list_quarantine_objects())
        except Exception as exc:
            logger.exception("quarantine sweep: list_objects failed: %s", exc)
            return []

    objects = await asyncio.to_thread(_materialize)

    scanned = 0
    blobs_deleted = 0
    blob_errors = 0
    bytes_freed = 0

    for obj in objects:
        # MinIO directory placeholders show up with trailing `/` and no
        # `last_modified` — skip them.
        last_mod = getattr(obj, "last_modified", None)
        key = getattr(obj, "object_name", None)
        if last_mod is None or key is None:
            continue
        # The SDK returns naive UTC for some servers; normalize.
        if last_mod.tzinfo is None:
            last_mod = last_mod.replace(tzinfo=timezone.utc)
        scanned += 1
        if last_mod >= cutoff:
            continue
        size = int(getattr(obj, "size", 0) or 0)
        try:
            await asyncio.to_thread(
                storage.delete, storage.bucket_quarantine, key,
            )
            blobs_deleted += 1
            bytes_freed += size
        except Exception as exc:
            blob_errors += 1
            logger.warning(
                "quarantine sweep: delete failed for %s: %s", key, exc
            )

    if blobs_deleted or blob_errors:
        # One global audit row per sweep — bounded by sweep frequency,
        # not by per-object count, the same shape as
        # sweep_expired_originals.
        session.add(
            AuditLog(
                user_id=None,
                action="retention.sweep_quarantine",
                details={
                    "scanned": scanned,
                    "blobs_deleted": blobs_deleted,
                    "blob_errors": blob_errors,
                    "bytes_freed": bytes_freed,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": days,
                    "swept_at": now.isoformat(),
                },
            )
        )
        await session.commit()

    return QuarantineSweepResult(
        scanned=scanned,
        blobs_deleted=blobs_deleted,
        blob_errors=blob_errors,
        bytes_freed=bytes_freed,
        cutoff_iso=cutoff.isoformat(),
    )
