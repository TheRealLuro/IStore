"""§B4 — quarantine retention sweeper acceptance.

Verifies:
- Objects older than the retention window are deleted; younger ones stay.
- The audit row records the sweep (one row per sweep, not per object).
- Idempotent — re-running with no expired objects is a no-op.
- A custom retention_days override works.
- Per-object delete failures don't fail the whole sweep.
- The admin endpoint requires superuser.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tests.conftest import register_and_login


@dataclass
class _FakeObj:
    object_name: str
    last_modified: datetime
    size: int


def _install_fake_listing(monkeypatch, objects: list[_FakeObj]) -> list[str]:
    """Patch the sweeper's `_list_quarantine_objects` to return a
    canned list and return a deletion log the test can assert on."""
    from backend import retention as retention_mod
    from backend import storage as storage_mod

    deleted: list[str] = []
    original_delete = storage_mod.storage.delete

    def _delete(bucket, key):
        deleted.append(key)
        original_delete(bucket, key)

    monkeypatch.setattr(storage_mod.storage, "delete", _delete)
    monkeypatch.setattr(retention_mod, "_list_quarantine_objects", lambda: iter(objects))
    return deleted


async def test_sweeper_drops_aged_quarantine_blobs(db_client, monkeypatch):
    _, _ = await register_and_login(db_client, email="qsweep-a@example.com")

    now = datetime.now(timezone.utc)
    old_a = _FakeObj("users/u/quarantine/old-a/file", now - timedelta(days=45), 100)
    old_b = _FakeObj("users/u/quarantine/old-b/file", now - timedelta(days=31), 250)
    fresh = _FakeObj("users/u/quarantine/fresh/file", now - timedelta(days=1), 50)
    deleted = _install_fake_listing(monkeypatch, [old_a, old_b, fresh])

    from backend.db import SessionLocal
    from backend.models import AuditLog
    from backend.retention import sweep_expired_quarantine

    async with SessionLocal() as session:
        result = await sweep_expired_quarantine(session)

    assert result.scanned == 3
    assert result.blobs_deleted == 2
    assert result.bytes_freed == 350
    assert set(deleted) == {old_a.object_name, old_b.object_name}

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "retention.sweep_quarantine")
            )
        ).scalars().all()
        assert len(rows) == 1
        details = rows[0].details
        assert details["scanned"] == 3
        assert details["blobs_deleted"] == 2
        assert details["bytes_freed"] == 350


async def test_sweeper_is_idempotent(db_client, monkeypatch):
    _, _ = await register_and_login(db_client, email="qsweep-idem@example.com")

    now = datetime.now(timezone.utc)
    fresh = _FakeObj("users/u/quarantine/fresh/file", now - timedelta(days=1), 10)
    _install_fake_listing(monkeypatch, [fresh])

    from backend.db import SessionLocal
    from backend.models import AuditLog
    from backend.retention import sweep_expired_quarantine

    async with SessionLocal() as session:
        first = await sweep_expired_quarantine(session)
    async with SessionLocal() as session:
        second = await sweep_expired_quarantine(session)

    assert first.blobs_deleted == 0
    assert second.blobs_deleted == 0
    # No deletions → no audit row from either sweep.
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "retention.sweep_quarantine")
            )
        ).scalars().all()
        assert len(rows) == 0


async def test_retention_days_override(db_client, monkeypatch):
    _, _ = await register_and_login(db_client, email="qsweep-override@example.com")

    now = datetime.now(timezone.utc)
    # Five days old — outside a 1-day override but inside the default 30.
    obj = _FakeObj("users/u/quarantine/abc/file", now - timedelta(days=5), 7)
    _install_fake_listing(monkeypatch, [obj])

    from backend.db import SessionLocal
    from backend.retention import sweep_expired_quarantine

    async with SessionLocal() as session:
        default_result = await sweep_expired_quarantine(session)
    assert default_result.blobs_deleted == 0  # younger than default 30 days

    _install_fake_listing(monkeypatch, [obj])  # re-stub for the second call
    async with SessionLocal() as session:
        forced = await sweep_expired_quarantine(session, retention_days=1)
    assert forced.blobs_deleted == 1


async def test_sweeper_records_blob_errors(db_client, monkeypatch):
    _, _ = await register_and_login(db_client, email="qsweep-err@example.com")

    now = datetime.now(timezone.utc)
    target = _FakeObj("users/u/quarantine/boom/file", now - timedelta(days=60), 100)

    from backend import retention as retention_mod
    from backend import storage as storage_mod

    monkeypatch.setattr(retention_mod, "_list_quarantine_objects", lambda: iter([target]))

    def _raise(bucket, key):
        raise RuntimeError("simulated S3 failure")

    monkeypatch.setattr(storage_mod.storage, "delete", _raise)

    from backend.db import SessionLocal
    from backend.models import AuditLog
    from backend.retention import sweep_expired_quarantine

    async with SessionLocal() as session:
        result = await sweep_expired_quarantine(session)

    assert result.scanned == 1
    assert result.blobs_deleted == 0
    assert result.blob_errors == 1

    # Audit row still written so ops can see the failure.
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "retention.sweep_quarantine")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].details["blob_errors"] == 1


async def test_admin_quarantine_sweep_requires_superuser(db_client):
    _, headers = await register_and_login(db_client, email="qsweep-403@example.com")
    r = await db_client.post("/admin/quarantine/sweep", headers=headers)
    assert r.status_code == 403


async def test_admin_quarantine_sweep_superuser_runs(db_client, monkeypatch):
    """Promote the test user to superuser and hit the endpoint."""
    email = "qsweep-super@example.com"
    _, headers = await register_and_login(db_client, email=email)

    from tests.conftest import fetch_user_id
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import User
    from sqlalchemy import update as sa_update

    async with SessionLocal() as session:
        await session.execute(sa_update(User).where(User.id == uid).values(is_superuser=True))
        await session.commit()

    now = datetime.now(timezone.utc)
    old = _FakeObj("users/u/quarantine/old/file", now - timedelta(days=60), 20)
    _install_fake_listing(monkeypatch, [old])

    r = await db_client.post("/admin/quarantine/sweep", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blobs_deleted"] == 1
    assert body["bytes_freed"] == 20


async def test_invalid_retention_days_rejected(db_client, monkeypatch):
    email = "qsweep-bad-arg@example.com"
    _, headers = await register_and_login(db_client, email=email)

    from tests.conftest import fetch_user_id
    uid = await fetch_user_id(email)

    from backend.db import SessionLocal
    from backend.models import User
    from sqlalchemy import update as sa_update

    async with SessionLocal() as session:
        await session.execute(sa_update(User).where(User.id == uid).values(is_superuser=True))
        await session.commit()

    r = await db_client.post(
        "/admin/quarantine/sweep?retention_days=0", headers=headers
    )
    assert r.status_code == 400
