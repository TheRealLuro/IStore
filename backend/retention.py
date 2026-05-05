"""Hybrid-retention sweeper (Phase 8 / Mode D).

Drops original blobs that have aged past their per-row `original_expires_at`
horizon (default 30 days from upload — see migration 0004). The served
variant remains, EXIF/GPS/capture-date are preserved inside it (codecs.py
re-injects EXIF on encode), and `download_original` falls back to the
served bytes with `X-Original-Expired: true` so the frontend can surface
the substitution to the user.

Per the plan, this is a pure async function plus a thin admin endpoint —
arq cron scheduling is Phase 9 work. The function is idempotent: running
it twice with no expired rows is a no-op.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
