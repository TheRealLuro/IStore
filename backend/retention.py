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
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

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


async def sweep_expired_originals(
    session: AsyncSession,
    *,
    user_id: Optional[uuid.UUID] = None,
) -> SweepResult:
    """Find expired originals, drop their blobs, null the column, audit it.

    `user_id` (optional) scopes the sweep to ONE user. Used by the
    user-facing `POST /storage/free-originals` endpoint so clicking
    "Free originals" in user A's settings doesn't sweep user B's
    due-expired rows under user A's transaction (audit-misattribution
    + cross-tenant info leak via the returned `bytes_freed` total).
    When `user_id` is None (the daily cron path), all due originals
    across all users are processed — that's the global behaviour the
    docstring used to describe.
    """
    now = datetime.now(timezone.utc)

    stmt = (
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
    if user_id is not None:
        stmt = stmt.where(Image.user_id == user_id)

    expired = (await session.execute(stmt)).all()

    blobs_deleted = 0
    blob_errors = 0
    bytes_freed = 0
    # Per-user counts of rows where the blob ACTUALLY deleted, so
    # the audit row doesn't claim "X originals dropped" when really
    # only `blobs_deleted` got past MinIO. The total row count
    # (column-nulled) ends up in `rows_nulled` for the SweepResult.
    user_deleted: dict = {}
    user_errors: dict = {}

    for image_id, owner, blob_key, byte_size in expired:
        try:
            storage.delete(storage.bucket_originals, blob_key)
            blobs_deleted += 1
            if byte_size:
                bytes_freed += int(byte_size)
            user_deleted[owner] = user_deleted.get(owner, 0) + 1
        except Exception as exc:
            blob_errors += 1
            logger.warning("Retention sweep: blob delete failed for %s: %s", blob_key, exc)
            user_errors[owner] = user_errors.get(owner, 0) + 1
            # Still null the column below — a missing blob isn't a
            # reason to keep the dead pointer alive. The audit row
            # records the blob_errors so an orphaned-bytes condition
            # in MinIO can be reconciled by a sysadmin later.

    rows_nulled = 0
    if expired:
        ids = [row[0] for row in expired]
        result = await session.execute(
            update(Image)
            .where(Image.id.in_(ids))
            .values(original_blob_key=None)
        )
        rows_nulled = int(result.rowcount or 0)

        # One audit row per affected user. We now record `blobs_deleted`
        # (the actual MinIO ops that succeeded) and `blob_errors`
        # separately so operators can reconcile orphan bytes from the
        # audit log alone, instead of seeing "5 originals dropped" when
        # really only 3 had their blobs deleted.
        for owner in set(list(user_deleted.keys()) + list(user_errors.keys())):
            session.add(
                AuditLog(
                    user_id=owner,
                    action="retention.sweep_originals",
                    details={
                        "blobs_deleted": user_deleted.get(owner, 0),
                        "blob_errors": user_errors.get(owner, 0),
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


# ----- Orphan-blob sweep -----
#
# Background: the `images.original_blob_key` / `served_blob_key` /
# `thumbnail_blob_key` columns and the `served_variants` JSONB are
# the source of truth for which MinIO objects we own. A row insert
# writes the blob BEFORE committing the DB row, so a crash between
# blob-write and row-commit leaves a blob with no reference. Same for
# re-syncs that generate a fresh `uuid4().hex` prefix on every run —
# the new path goes into the DB, the old blob stays in MinIO. Over
# weeks of dev iteration this leaked ~17× the actual library size
# (we measured 1742 original blobs in MinIO vs. 105 referenced by
# the DB).
#
# This sweeper walks the originals + served buckets, computes the
# set of "referenced" keys from the DB, and deletes everything that
# isn't referenced. Idempotent. Slow (one bucket walk per call) so
# we run it on the daily lifespan tick alongside the originals-TTL
# sweep — not per request.


@dataclass
class OrphanSweepResult:
    bucket: str
    listed: int
    deleted: int
    bytes_freed: int
    errors: int


async def sweep_orphan_blobs(session: AsyncSession) -> list[OrphanSweepResult]:
    """Delete every blob in `originals` and `served` that no Image row
    points at. Skips face_crops (those have FK CASCADE handling) and
    the quarantine bucket (sweep_expired_quarantine owns it).

    The "referenced" set covers:
      - Image.original_blob_key
      - Image.served_blob_key
      - Image.thumbnail_blob_key
      - every entry in Image.served_variants (legacy mp4 tier keys)
      - every object under Image.served_variants["hls_prefix"]
    """
    from backend.models import Image as ImageModel

    # Pull every referenced key in one pass. Soft-deleted rows still
    # count — their blobs stick around until Trash is emptied or the
    # 30-day TTL sweep fires. We don't want to nuke their bytes here.
    rows = (
        await session.execute(
            select(
                ImageModel.original_blob_key,
                ImageModel.served_blob_key,
                ImageModel.thumbnail_blob_key,
                ImageModel.served_variants,
            )
        )
    ).all()

    orig_refs: set[str] = set()
    served_refs: set[str] = set()
    hls_prefixes: list[str] = []
    for orig, served, thumb, sv in rows:
        if orig:
            orig_refs.add(orig)
        if served:
            served_refs.add(served)
        if thumb:
            served_refs.add(thumb)
        if isinstance(sv, dict):
            for label, v in sv.items():
                if label == "hls_prefix" and isinstance(v, str):
                    hls_prefixes.append(v.rstrip("/") + "/")
                elif label == "renditions":
                    continue
                elif label == "hls_master" and isinstance(v, str):
                    served_refs.add(v)
                elif isinstance(v, str):
                    served_refs.add(v)
                elif isinstance(v, dict) and isinstance(v.get("key"), str):
                    served_refs.add(v["key"])

    results: list[OrphanSweepResult] = []
    # MinIO listing happens via the sync SDK; run in a thread so the
    # asyncio loop stays free during a multi-bucket walk.
    import asyncio as _asyncio

    def _sweep(bucket: str, refs: set[str], allow_prefixes: list[str]) -> OrphanSweepResult:
        listed = 0
        deleted = 0
        errors = 0
        bytes_freed = 0
        try:
            objects = list(storage.client.list_objects(
                bucket, prefix="users/", recursive=True,
            ))
        except Exception:
            logger.exception("orphan-sweep: list_objects failed for %s", bucket)
            return OrphanSweepResult(bucket=bucket, listed=0, deleted=0, bytes_freed=0, errors=1)
        for obj in objects:
            key = getattr(obj, "object_name", None)
            if not key:
                continue
            listed += 1
            if key in refs:
                continue
            # `allow_prefixes` lets HLS segments under `hls_prefix`
            # survive even though their individual keys aren't in
            # the served_variants top-level entries.
            if any(key.startswith(p) for p in allow_prefixes):
                continue
            try:
                size = int(getattr(obj, "size", 0) or 0)
                storage.delete(bucket, key)
                deleted += 1
                bytes_freed += size
            except Exception:
                errors += 1
        return OrphanSweepResult(
            bucket=bucket, listed=listed, deleted=deleted,
            bytes_freed=bytes_freed, errors=errors,
        )

    results.append(await _asyncio.to_thread(
        _sweep, storage.bucket_originals, orig_refs, [],
    ))
    results.append(await _asyncio.to_thread(
        _sweep, storage.bucket_served, served_refs, hls_prefixes,
    ))

    total_deleted = sum(r.deleted for r in results)
    total_freed = sum(r.bytes_freed for r in results)
    if total_deleted > 0:
        session.add(
            AuditLog(
                user_id=None,
                action="retention.sweep_orphans",
                details={
                    "buckets": [
                        {
                            "bucket": r.bucket, "listed": r.listed,
                            "deleted": r.deleted, "bytes_freed": r.bytes_freed,
                            "errors": r.errors,
                        }
                        for r in results
                    ],
                    "total_deleted": total_deleted,
                    "total_bytes_freed": total_freed,
                    "swept_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        )
        await session.commit()
    return results


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
    """NULL `user_id` AND scrub PII out of `details` on audit rows
    older than the retention horizon.

    "Archive" in the spec — but cold storage would just hide the rows;
    the link from `audit_log` to a user is the data-protection
    concern, so we anonymize in place. The action + timestamp +
    NON-PII details survive (operator can still answer "did a delete
    happen here?" forensically); the per-user identifier goes AND
    every PII-shaped key in `details` is replaced with None.

    PII-key set lives in `backend.audit.PII_DETAILS_KEYS` so every
    audit-row writer and this sweeper stay in sync. Audit F12 — the
    pre-fix sweeper only NULLed `user_id`, leaving emails and IPs in
    the JSONB blob indefinitely. GDPR storage-limitation now extends
    to the blob too.

    Skips rows already anonymized + already-scrubbed (re-running is a
    no-op modulo idempotency on the scrub side, which is also idempotent
    — re-setting a None key to None is a noop).
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

    # Audit F12 — JSONB-blob scrub. We don't try to do this in pure
    # SQL because the PII-key set lives in Python and stays in
    # lock-step with add_audit's allowlist. The set of rows to scrub
    # is bounded by the cutoff anyway, so a Python-side loop is
    # acceptable (cron-driven, daily).
    from backend.audit import PII_DETAILS_KEYS

    rows_with_details = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.created_at < cutoff,
                AuditLog.details.is_not(None),
            )
        )
    ).scalars().all()

    scrubbed_blob_rows = 0
    for row in rows_with_details:
        if not isinstance(row.details, dict) or not row.details:
            continue
        # Only build the scrubbed dict if there's at least one PII
        # key actually present — avoids touching JSONB on every row
        # the sweeper has already processed.
        if not any(k in PII_DETAILS_KEYS for k in row.details.keys()):
            continue
        # Only count if we actually CHANGED something (a row whose
        # PII keys are already None has nothing to do).
        changed = False
        scrubbed = dict(row.details)
        for k in list(scrubbed.keys()):
            if k in PII_DETAILS_KEYS and scrubbed[k] is not None:
                scrubbed[k] = None
                changed = True
        if changed:
            row.details = scrubbed
            scrubbed_blob_rows += 1

    if anon or scrubbed_blob_rows:
        session.add(
            AuditLog(
                user_id=None,
                action="retention.sweep_audit_anonymize",
                details={
                    "rows_anonymized": anon,
                    "rows_blob_scrubbed": scrubbed_blob_rows,
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
        # Lock the user row and RE-CHECK scheduled_delete_at before
        # deleting. Without this, a cancel-delete request that lands
        # between the outer SELECT and this iteration's UPDATE would
        # still result in the account being hard-deleted — losing the
        # user's data after they explicitly asked to abort the deletion.
        locked_row = (
            await session.execute(
                sa_select(User)
                .where(User.id == user_id)
                .with_for_update()
            )
        ).scalars().first()
        if locked_row is None:
            continue
        if locked_row.scheduled_delete_at is None or locked_row.scheduled_delete_at > now:
            # User cancelled (NULL) or pushed the timestamp out while we
            # were iterating. Honor the cancel and skip.
            continue
        image_ids = (
            await session.execute(
                sa_select(Image.id).where(Image.user_id == user_id)
            )
        ).scalars().all()
        try:
            # §A5 — scheduled account delete also wipes bandit state.
            await hard_delete_images(
                session,
                user_id=user_id,
                image_ids=list(image_ids),
                audit_action="account.images.delete",
                reset_bandit=True,
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
