"""§B4 — retention sweeper coverage.

Three new sweepers + one new account-deletion flow:

  sweep_feedback_events            consumed rows older than 90d → DELETE
  sweep_audit_log_anonymize        rows older than 365d        → user_id=NULL
  sweep_scheduled_account_deletes  users past scheduled_delete_at → hard-delete

  /account/schedule-delete + /account/cancel-delete + GET (read state)

Tests stub the trainer flag where needed so we can simulate
already-consumed feedback rows without running the trainer itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, update as sa_update

from tests.conftest import fetch_user_id, register_and_login


# ---------- Feedback retention ----------


async def test_sweep_feedback_drops_old_consumed_rows(db_client, monkeypatch):
    monkeypatch.setenv("FEEDBACK_RETENTION_DAYS", "90")
    email = "fb-old@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import FeedbackEvent

    long_ago = datetime.now(timezone.utc) - timedelta(days=120)
    recent = datetime.now(timezone.utc) - timedelta(days=30)
    async with SessionLocal() as s:
        # FeedbackEvent requires image_id (FK CASCADE to images) — but
        # ON DELETE CASCADE doesn't apply to inserts; a stray uuid()
        # would 23503-violate the FK. We pre-create a minimal Image
        # row to anchor the events.
        from backend.models import Image
        anchor = Image(
            user_id=uid, category="image",
            served_blob_key=f"users/{uid}/served/anchor.webp",
            pending_face_scan=False,
        )
        s.add(anchor)
        await s.flush()
        s.add(FeedbackEvent(
            user_id=uid, image_id=anchor.id, kind="rating",
            bandit_arm_id=1, weight=1.0, reward=0.8,
            context_features=[0.1] * 32,
            consumed_by_trainer=True, created_at=long_ago,
        ))
        s.add(FeedbackEvent(
            user_id=uid, image_id=anchor.id, kind="rating",
            bandit_arm_id=1, weight=1.0, reward=0.7,
            context_features=[0.1] * 32,
            consumed_by_trainer=False, created_at=long_ago,  # un-consumed → keep
        ))
        s.add(FeedbackEvent(
            user_id=uid, image_id=anchor.id, kind="rating",
            bandit_arm_id=1, weight=1.0, reward=0.9,
            context_features=[0.1] * 32,
            consumed_by_trainer=True, created_at=recent,  # too recent → keep
        ))
        await s.commit()

    from backend.retention import sweep_feedback_events

    async with SessionLocal() as s:
        res = await sweep_feedback_events(s, retention_days=90)
    assert res.rows_deleted == 1

    async with SessionLocal() as s:
        remaining = (
            await s.execute(select(FeedbackEvent).where(FeedbackEvent.user_id == uid))
        ).scalars().all()
        assert len(remaining) == 2


async def test_sweep_feedback_idempotent(db_client):
    email = "fb-idem@example.com"
    _, _ = await register_and_login(db_client, email=email)
    from backend.db import SessionLocal
    from backend.retention import sweep_feedback_events

    async with SessionLocal() as s:
        first = await sweep_feedback_events(s, retention_days=90)
    async with SessionLocal() as s:
        second = await sweep_feedback_events(s, retention_days=90)
    assert first.rows_deleted == 0
    assert second.rows_deleted == 0


async def test_sweep_feedback_rejects_zero_retention(db_client):
    from backend.db import SessionLocal
    from backend.retention import sweep_feedback_events

    async with SessionLocal() as s:
        with pytest.raises(ValueError):
            await sweep_feedback_events(s, retention_days=0)


# ---------- Audit-log anonymization ----------


async def test_sweep_audit_anonymizes_old_rows(db_client):
    email = "audit-old@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import AuditLog

    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    recent = datetime.now(timezone.utc) - timedelta(days=30)
    async with SessionLocal() as s:
        s.add(AuditLog(user_id=uid, action="image.upload", details={}, created_at=long_ago))
        s.add(AuditLog(user_id=uid, action="image.upload", details={}, created_at=recent))
        await s.commit()

    from backend.retention import sweep_audit_log_anonymize

    async with SessionLocal() as s:
        res = await sweep_audit_log_anonymize(s, retention_days=365)
    assert res.rows_anonymized >= 1

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "image.upload")
                .order_by(AuditLog.created_at.asc())
            )
        ).scalars().all()
        # Old row: user_id NULL'd. Recent row: still tied to user.
        assert rows[0].user_id is None
        assert rows[1].user_id == uid


async def test_sweep_audit_logs_its_own_sweep_row(db_client):
    """The sweeper records that it ran via a NEW audit row (user_id=NULL
    by design). Lets operators prove "anonymization happened on T"."""
    email = "audit-self@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import AuditLog
    from backend.retention import sweep_audit_log_anonymize

    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    async with SessionLocal() as s:
        s.add(AuditLog(user_id=uid, action="image.upload", details={}, created_at=long_ago))
        await s.commit()

    async with SessionLocal() as s:
        await sweep_audit_log_anonymize(s, retention_days=365)

    async with SessionLocal() as s:
        self_rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "retention.sweep_audit_anonymize")
            )
        ).scalars().all()
        assert len(self_rows) >= 1


# ---------- Scheduled account deletion ----------


async def test_schedule_delete_sets_timestamp(db_client):
    email = "sched@example.com"
    _, headers = await register_and_login(db_client, email=email)
    r = await db_client.post("/account/schedule-delete", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scheduled_delete_at"] is not None
    assert body["grace_days"] == 30


async def test_cancel_delete_clears_timestamp(db_client):
    email = "cancel@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await db_client.post("/account/schedule-delete", headers=headers)
    r = await db_client.post("/account/cancel-delete", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["scheduled_delete_at"] is None


async def test_get_schedule_delete_reads_state(db_client):
    email = "read-sched@example.com"
    _, headers = await register_and_login(db_client, email=email)
    r = await db_client.get("/account/schedule-delete", headers=headers)
    assert r.status_code == 200
    assert r.json()["scheduled_delete_at"] is None  # nothing scheduled
    await db_client.post("/account/schedule-delete", headers=headers)
    r = await db_client.get("/account/schedule-delete", headers=headers)
    assert r.json()["scheduled_delete_at"] is not None


async def test_sweep_accounts_hard_deletes_past_due(db_client):
    email = "due@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import User

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    async with SessionLocal() as s:
        await s.execute(sa_update(User).where(User.id == uid).values(scheduled_delete_at=past))
        await s.commit()

    from backend.retention import sweep_scheduled_account_deletes

    async with SessionLocal() as s:
        res = await sweep_scheduled_account_deletes(s)
    assert res.accounts_hard_deleted == 1

    async with SessionLocal() as s:
        survivor = (
            await s.execute(select(User).where(User.id == uid))
        ).scalar_one_or_none()
        assert survivor is None


async def test_sweep_accounts_skips_future(db_client):
    email = "future@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import User

    future = datetime.now(timezone.utc) + timedelta(days=30)
    async with SessionLocal() as s:
        await s.execute(sa_update(User).where(User.id == uid).values(scheduled_delete_at=future))
        await s.commit()

    from backend.retention import sweep_scheduled_account_deletes

    async with SessionLocal() as s:
        res = await sweep_scheduled_account_deletes(s)
    assert res.accounts_hard_deleted == 0

    async with SessionLocal() as s:
        survivor = (
            await s.execute(select(User).where(User.id == uid))
        ).scalar_one()
        assert survivor.scheduled_delete_at is not None


# ---------- Admin endpoints ----------


async def _promote_superuser(email: str) -> None:
    uid = await fetch_user_id(email)
    from backend.db import SessionLocal
    from backend.models import User

    async with SessionLocal() as s:
        await s.execute(sa_update(User).where(User.id == uid).values(is_superuser=True))
        await s.commit()


async def test_admin_sweep_endpoints_require_superuser(db_client):
    email = "non-su@example.com"
    _, headers = await register_and_login(db_client, email=email)
    for path in ("/admin/retention/sweep-feedback", "/admin/retention/sweep-audit", "/admin/retention/sweep-accounts"):
        r = await db_client.post(path, headers=headers)
        assert r.status_code == 403, path


async def test_admin_sweep_endpoints_work_for_superuser(db_client):
    email = "su@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await _promote_superuser(email)

    r = await db_client.post("/admin/retention/sweep-feedback", headers=headers)
    assert r.status_code == 200, r.text
    assert "rows_deleted" in r.json()

    r = await db_client.post("/admin/retention/sweep-audit", headers=headers)
    assert r.status_code == 200, r.text
    assert "rows_anonymized" in r.json()

    r = await db_client.post("/admin/retention/sweep-accounts", headers=headers)
    assert r.status_code == 200, r.text
    assert "accounts_hard_deleted" in r.json()
