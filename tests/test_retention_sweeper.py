"""Phase 8 acceptance: hybrid-retention sweeper.

Verifies the sweeper:
  - drops original blobs whose `original_expires_at < now()`
  - nulls `images.original_blob_key`
  - writes an audit row recording the deletion count
  - is idempotent (re-running does nothing on the same DB)
  - leaves `served_blob_key` and the served bucket untouched
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tests.conftest import (
    fetch_user_id,
    register_and_login,
)


async def _insert_image(
    user_id: uuid.UUID,
    original_blob_key: str | None,
    served_blob_key: str,
    expires_at: datetime | None,
    byte_size_original: int = 1000,
):
    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as session:
        img = Image(
            user_id=user_id,
            category="image",
            original_blob_key=original_blob_key,
            served_blob_key=served_blob_key,
            original_filename="test.jpg",
            byte_size_original=byte_size_original,
            byte_size_served=byte_size_original // 2,
            mime_type_served="image/webp",
            original_expires_at=expires_at,
            pending_face_scan=False,
        )
        session.add(img)
        await session.commit()
        await session.refresh(img)
        return img.id


async def test_sweeper_drops_expired_originals(db_client):
    email = "sweep-a@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=29)

    # Two expired, one not-yet-expired, one already-nulled.
    expired_a = await _insert_image(
        uid, "users/a/originals/expA", "users/a/served/expA.webp", past, 5000,
    )
    expired_b = await _insert_image(
        uid, "users/a/originals/expB", "users/a/served/expB.webp", past, 3000,
    )
    fresh = await _insert_image(
        uid, "users/a/originals/fresh", "users/a/served/fresh.webp", future, 1000,
    )
    already_swept = await _insert_image(
        uid, None, "users/a/served/old.webp", past, 0,
    )

    # Pre-populate the storage stub so blob deletions have something to remove.
    from backend import storage as storage_mod

    for key in ("users/a/originals/expA", "users/a/originals/expB",
                "users/a/originals/fresh"):
        storage_mod.storage.put(
            storage_mod.storage.bucket_originals, key, b"X" * 16, "image/jpeg"
        )

    from backend.db import SessionLocal
    from backend.models import AuditLog, Image
    from backend.retention import sweep_expired_originals

    async with SessionLocal() as session:
        result = await sweep_expired_originals(session)

    assert result.scanned == 2
    assert result.blobs_deleted == 2
    assert result.rows_nulled == 2
    assert result.bytes_freed == 8000  # 5000 + 3000

    async with SessionLocal() as session:
        a = (await session.execute(select(Image).where(Image.id == expired_a))).scalar_one()
        b = (await session.execute(select(Image).where(Image.id == expired_b))).scalar_one()
        f = (await session.execute(select(Image).where(Image.id == fresh))).scalar_one()
        s = (await session.execute(select(Image).where(Image.id == already_swept))).scalar_one()

        assert a.original_blob_key is None
        assert b.original_blob_key is None
        assert f.original_blob_key == "users/a/originals/fresh", \
            "Sweeper must not touch images whose expiry is still in the future"
        assert s.original_blob_key is None

        # Served variants always survive — that's the whole point of mode D.
        assert a.served_blob_key == "users/a/served/expA.webp"
        assert b.served_blob_key == "users/a/served/expB.webp"

        # Audit row written, scoped to the user with the dropped count.
        audits = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == uid,
                    AuditLog.action == "retention.sweep_originals",
                )
            )
        ).scalars().all()
        assert len(audits) == 1
        assert audits[0].details["originals_dropped"] == 2


async def test_sweeper_is_idempotent(db_client):
    email = "sweep-idem@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    past = datetime.now(timezone.utc) - timedelta(days=2)
    img_id = await _insert_image(
        uid, "users/idem/originals/x", "users/idem/served/x.webp", past, 100,
    )

    from backend import storage as storage_mod
    storage_mod.storage.put(
        storage_mod.storage.bucket_originals, "users/idem/originals/x",
        b"abc", "image/jpeg",
    )

    from backend.db import SessionLocal
    from backend.retention import sweep_expired_originals

    async with SessionLocal() as session:
        first = await sweep_expired_originals(session)
    async with SessionLocal() as session:
        second = await sweep_expired_originals(session)

    assert first.rows_nulled == 1
    assert second.scanned == 0
    assert second.rows_nulled == 0
    _ = img_id  # silence linter; row identity asserted via scanned counts


async def test_sweeper_handles_missing_blobs(db_client):
    """If MinIO no longer has the blob, the row must still be nulled and the
    audit row written. We just count the failure and move on."""
    email = "sweep-missing@example.com"
    _, _ = await register_and_login(db_client, email=email)
    uid = await fetch_user_id(email)

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    img_id = await _insert_image(
        uid, "users/missing/originals/ghost", "users/missing/served/g.webp",
        past, 0,
    )

    # Force the storage stub to raise so we exercise the error path.
    from backend import storage as storage_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated minio failure")

    original_delete = storage_mod.storage.delete
    storage_mod.storage.delete = _raise  # type: ignore[assignment]
    try:
        from backend.db import SessionLocal
        from backend.models import Image
        from backend.retention import sweep_expired_originals

        async with SessionLocal() as session:
            result = await sweep_expired_originals(session)

        assert result.scanned == 1
        assert result.blob_errors == 1
        assert result.rows_nulled == 1

        async with SessionLocal() as session:
            row = (
                await session.execute(select(Image).where(Image.id == img_id))
            ).scalar_one()
            assert row.original_blob_key is None
    finally:
        storage_mod.storage.delete = original_delete  # type: ignore[assignment]


async def test_sweeper_only_touches_expired_user_blobs(db_client):
    """Two users, only A's row is past expiry — B's must be untouched."""
    _, _ = await register_and_login(db_client, email="sweep-multi-a@example.com")
    _, _ = await register_and_login(db_client, email="sweep-multi-b@example.com")
    uid_a = await fetch_user_id("sweep-multi-a@example.com")
    uid_b = await fetch_user_id("sweep-multi-b@example.com")

    past = datetime.now(timezone.utc) - timedelta(days=10)
    future = datetime.now(timezone.utc) + timedelta(days=10)

    a_img = await _insert_image(uid_a, "u-a/orig/x", "u-a/served/x.webp", past)
    b_img = await _insert_image(uid_b, "u-b/orig/y", "u-b/served/y.webp", future)

    from backend import storage as storage_mod
    storage_mod.storage.put(storage_mod.storage.bucket_originals, "u-a/orig/x", b"a", "image/jpeg")
    storage_mod.storage.put(storage_mod.storage.bucket_originals, "u-b/orig/y", b"b", "image/jpeg")

    from backend.db import SessionLocal
    from backend.models import AuditLog, Image
    from backend.retention import sweep_expired_originals

    async with SessionLocal() as session:
        await sweep_expired_originals(session)

    async with SessionLocal() as session:
        a = (await session.execute(select(Image).where(Image.id == a_img))).scalar_one()
        b = (await session.execute(select(Image).where(Image.id == b_img))).scalar_one()
        assert a.original_blob_key is None
        assert b.original_blob_key == "u-b/orig/y"

        a_audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == uid_a,
                    AuditLog.action == "retention.sweep_originals",
                )
            )
        ).scalars().all()
        b_audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.user_id == uid_b,
                    AuditLog.action == "retention.sweep_originals",
                )
            )
        ).scalars().all()
        assert len(a_audit) == 1
        assert len(b_audit) == 0


async def test_admin_sweep_endpoint_requires_superuser(db_client):
    _, headers = await register_and_login(db_client)
    r = await db_client.post("/admin/retention/sweep", headers=headers)
    assert r.status_code == 403
