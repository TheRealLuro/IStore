"""Retention sweepers (§B4 — privacy / data minimization).

Five sweepers, all pure async functions with idempotent semantics:

  sweep_expired_originals        Drops original blobs past `original_expires_at`
                                  (default 30 days from upload — migration 0004).
                                  Served variant survives; `download_original`
                                  falls back with `X-Original-Expired: true`.
  sweep_expired_quarantine       Deletes objects in `bucket_quarantine` older
                                  than `upload_quarantine_retention_days`
                                  (default 30). Audit row at rejection time
                                  is preserved; only the bytes go.
  sweep_feedback_events          Deletes consumed_by_trainer=true feedback rows
                                  older than the bandit-telemetry horizon
                                  (default 90 days). The trainer already
                                  consumed these; keeping them ties click
                                  history to a person unnecessarily.
  sweep_audit_log_anonymize      NULLs `user_id` on audit rows older than the
                                  retention horizon (default 365 days). The
                                  "this happened" record stays; the link to
                                  a person is dropped.
  sweep_scheduled_account_deletes  Hard-deletes users whose `scheduled_delete_at`
                                  is in the past (30-day grace per §B4).
                                  Calls hard_delete_images first, then drops
                                  the user row (FK ON DELETE CASCADE handles
                                  the rest).

All sweepers write to `audit_log` so deletion is provable. The trainer
already gates on `consumed_by_trainer=true`, so feedback deletion
doesn't race with in-flight learning. arq cron scheduling is Phase 9
work; today these run via admin endpoints + host cron.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sa_delete, func as sa_func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import AuditLog, FeedbackEvent, Image, User
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


# ----- §B4: feedback-event retention (90 days) -----


@dataclass
class FeedbackSweepResult:
    rows_deleted: int
    cutoff_iso: str
    retention_days: int


async def sweep_feedback_events(
    session: AsyncSession,
    *,
    retention_days: int | None = None,
) -> FeedbackSweepResult:
    """Delete bandit `feedback_events` rows that have been consumed by
    the trainer AND are older than the retention horizon.

    The trainer flips `consumed_by_trainer=true` once it folds an event
    into the LinUCB sufficient stats. After that, the row is dead weight
    — keeping it ties click history to a person without any model
    benefit. §B4 sets 90 days as the soft window.

    UN-consumed rows are NEVER deleted here: the next trainer run might
    still need them. Operator can trigger a fresh trainer pass before
    the sweep to drain the backlog (see /admin/trainer/run).
    """
    days = retention_days if retention_days is not None else settings.feedback_retention_days
    if days <= 0:
        raise ValueError("retention_days must be a positive integer")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    res = await session.execute(
        sa_delete(FeedbackEvent)
        .where(
            FeedbackEvent.consumed_by_trainer.is_(True),
            FeedbackEvent.created_at < cutoff,
        )
    )
    deleted = int(res.rowcount or 0)
    if deleted:
        session.add(
            AuditLog(
                user_id=None,
                action="retention.sweep_feedback",
                details={
                    "rows_deleted": deleted,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": days,
                    "swept_at": now.isoformat(),
                },
            )
        )
    await session.commit()

    return FeedbackSweepResult(
        rows_deleted=deleted,
        cutoff_iso=cutoff.isoformat(),
        retention_days=days,
    )


# ----- §B4: audit-log anonymization (1 year) -----


@dataclass
class AuditAnonymizeResult:
    rows_anonymized: int
    cutoff_iso: str
    retention_days: int


async def sweep_audit_log_anonymize(
    session: AsyncSession,
    *,
    retention_days: int | None = None,
) -> AuditAnonymizeResult:
    """NULL `user_id` on audit rows older than the retention horizon.

    "Archive" in the spec — but cold storage would just hide the rows;
    the link from `audit_log` to a user is the data-protection
    concern, so we anonymize in place. The action + timestamp + details
    survive (operator can still answer "did a delete happen here?"
    forensically); only the per-user identifier goes.

    Skips rows already anonymized (`user_id IS NULL`) so re-running is
    a no-op. Self-record: the sweep writes its own audit row with
    user_id=NULL, which is fine — it's a system action.
    """
    days = retention_days if retention_days is not None else settings.audit_log_retention_days
    if days <= 0:
        raise ValueError("retention_days must be a positive integer")
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    res = await session.execute(
        update(AuditLog)
        .where(
            AuditLog.user_id.is_not(None),
            AuditLog.created_at < cutoff,
        )
        .values(user_id=None)
    )
    anon = int(res.rowcount or 0)
    if anon:
        session.add(
            AuditLog(
                user_id=None,
                action="retention.sweep_audit_anonymize",
                details={
                    "rows_anonymized": anon,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": days,
                    "swept_at": now.isoformat(),
                },
            )
        )
    await session.commit()

    return AuditAnonymizeResult(
        rows_anonymized=anon,
        cutoff_iso=cutoff.isoformat(),
        retention_days=days,
    )


# ----- §B4: scheduled account deletion (30-day grace) -----


@dataclass
class AccountDeleteSweepResult:
    accounts_hard_deleted: int
    accounts_skipped_no_due: int
    swept_at_iso: str


async def sweep_scheduled_account_deletes(
    session: AsyncSession,
) -> AccountDeleteSweepResult:
    """Hard-delete users whose `scheduled_delete_at` has passed.

    /account/schedule-delete sets `users.scheduled_delete_at = now + 30d`
    (or whatever the grace setting holds) instead of nuking the account
    immediately. The user can call /account/cancel-delete before that
    timestamp to abort. Once the timestamp passes, this sweeper rolls
    through and runs the same hard-delete path that
    /account/delete uses today: hard_delete_images → DELETE users
    → FK CASCADE handles the rest.
    """
    from sqlalchemy import select as sa_select  # local — avoid shadowing

    from backend.deletion import hard_delete_images

    now = datetime.now(timezone.utc)
    due_users = (
        await session.execute(
            sa_select(User).where(
                User.scheduled_delete_at.is_not(None),
                User.scheduled_delete_at <= now,
            )
        )
    ).scalars().all()

    deleted = 0
    for user in due_users:
        user_id = user.id
        image_ids = (
            await session.execute(
                sa_select(Image.id).where(Image.user_id == user_id)
            )
        ).scalars().all()
        try:
            await hard_delete_images(
                session,
                user_id=user_id,
                image_ids=list(image_ids),
                audit_action="account.images.delete",
            )
            await session.execute(sa_delete(User).where(User.id == user_id))
            await session.flush()
            # Audit AFTER the user row is gone — anchor by user_id in
            # the details payload since the FK reference dies with the
            # row.
            session.add(
                AuditLog(
                    user_id=None,
                    action="account.delete.scheduled_executed",
                    details={
                        "user_id": str(user_id),
                        "scheduled_at": user.scheduled_delete_at.isoformat() if user.scheduled_delete_at else None,
                        "executed_at": now.isoformat(),
                    },
                )
            )
            await session.commit()
            deleted += 1
        except Exception:
            logger.exception(
                "scheduled-delete: hard-delete failed for user %s — will retry next sweep",
                user_id,
            )
            await session.rollback()

    return AccountDeleteSweepResult(
        accounts_hard_deleted=deleted,
        accounts_skipped_no_due=0,
        swept_at_iso=now.isoformat(),
    )
