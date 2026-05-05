"""Phase 8 acceptance: account deletion (GDPR Art. 17).

  - User row gone, FK cascades wipe images / faces / persons / consent_records
  - Blobs removed from MinIO (stub)
  - audit_log row remains with the original user UUID retained for legal retention
  - Other users' data is untouched
"""
from __future__ import annotations

from sqlalchemy import select, text

from tests.conftest import (
    fetch_user_id,
    grant_consent,
    insert_face,
    register_and_login,
)


async def _user_row_count(uid) -> int:
    from backend.db import engine

    async with engine.connect() as conn:
        r = await conn.execute(
            text("SELECT count(*) FROM users WHERE id = :uid"),
            {"uid": uid},
        )
        return int(r.scalar_one() or 0)


async def _seed_user_with_data(db_client, email: str):
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    uid = await fetch_user_id(email)
    seed = await insert_face(uid, person_name="Test", cluster_id=1)

    # Pre-populate the storage stub so account.delete has blobs to remove.
    from backend import storage as storage_mod
    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as session:
        img = (
            await session.execute(select(Image).where(Image.id == seed["image_id"]))
        ).scalar_one()
        if img.original_blob_key:
            storage_mod.storage.put(
                storage_mod.storage.bucket_originals, img.original_blob_key,
                b"orig", "application/octet-stream",
            )
        if img.served_blob_key and img.served_blob_key != img.original_blob_key:
            storage_mod.storage.put(
                storage_mod.storage.bucket_served, img.served_blob_key,
                b"served", "image/webp",
            )
    if seed["crop_blob_key"]:
        storage_mod.storage.put(
            storage_mod.storage.bucket_faces, seed["crop_blob_key"],
            b"crop", "image/jpeg",
        )

    return headers, uid, seed


async def test_delete_account_cascades_all_user_rows(db_client):
    headers, uid, _seed = await _seed_user_with_data(db_client, "del-a@example.com")

    r = await db_client.post("/account/delete", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["images_deleted"] >= 1
    assert body["faces_deleted"] == 1
    assert body["persons_deleted"] == 1
    assert body["blob_errors"] == 0
    assert body["blobs_deleted"] >= 2  # original + crop at minimum

    # Every per-user table is empty for this user.
    from tests.conftest import count_rows

    assert await _user_row_count(uid) == 0
    assert await count_rows("images", uid) == 0
    assert await count_rows("faces", uid) == 0
    assert await count_rows("face_detections", uid) == 0
    assert await count_rows("persons", uid) == 0
    assert await count_rows("consent_records", uid) == 0


async def test_delete_account_preserves_audit_with_user_id(db_client):
    headers, uid, _ = await _seed_user_with_data(db_client, "del-audit@example.com")
    await db_client.post("/account/delete", headers=headers)

    from backend.db import SessionLocal
    from backend.models import AuditLog
    from sqlalchemy import select

    async with SessionLocal() as session:
        audits = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "account.delete")
            )
        ).scalars().all()
    assert any(
        a.details.get("user_id") == str(uid) and str(a.user_id) == str(uid)
        for a in audits
    ), (
        "An account.delete audit row must persist after the user row is gone, "
        "with the original UUID retained."
    )


async def test_delete_account_does_not_touch_other_users(db_client):
    # User A is going to be deleted; user B must survive untouched.
    headers_a, uid_a, _ = await _seed_user_with_data(db_client, "del-multi-a@example.com")
    _h_b, uid_b, seed_b = await _seed_user_with_data(db_client, "del-multi-b@example.com")

    await db_client.post("/account/delete", headers=headers_a)

    from tests.conftest import count_rows

    assert await _user_row_count(uid_a) == 0
    assert await _user_row_count(uid_b) == 1
    assert await count_rows("images", uid_b) >= 1
    assert await count_rows("faces", uid_b) == 1
    assert await count_rows("persons", uid_b) == 1
    _ = seed_b


async def test_delete_account_requires_auth(db_client):
    r = await db_client.post("/account/delete")
    assert r.status_code == 401
