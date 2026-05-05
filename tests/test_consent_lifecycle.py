"""Phase 4 acceptance: consent lifecycle.

Verifies that grant -> backfill-eligible state -> withdraw leaves zero
biometric rows for that user, with an audit_log entry that records the
deletion counts. Also verifies the policy_text_sha256 captured on the
consent record matches the on-disk policy file at consent time, satisfying
the BIPA §15(b) "informed written consent" requirement.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from tests.conftest import (
    count_rows,
    fetch_user_id,
    grant_consent,
    insert_face,
    register_and_login,
)


POLICY_PATH = Path(__file__).resolve().parent.parent / "policies" / "face_recognition_v1.md"


async def test_status_starts_as_none(db_client):
    _, headers = await register_and_login(db_client)
    r = await db_client.get("/consent/face-recognition", headers=headers)
    assert r.status_code == 200
    assert r.json()["state"] == "NONE"


async def test_grant_records_policy_hash_and_audit(db_client):
    _, headers = await register_and_login(db_client)
    body = await grant_consent(db_client, headers)

    assert body["state"] == "GRANTED"
    assert body["policy_version"] == "v1"
    assert body["expires_at"] is not None  # 3y horizon

    # The hash captured on the row must equal sha256 of the on-disk policy
    # at consent time — this is the proof artifact for compliance audits.
    expected = hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import ConsentRecord

    async with SessionLocal() as s:
        rec = (
            await s.execute(select(ConsentRecord).order_by(ConsentRecord.id.desc()))
        ).scalars().first()
        assert rec is not None
        assert rec.policy_text_sha256.hex() == expected
        assert rec.signature_text == "Test User"
        assert rec.state == "GRANTED"

    # Status endpoint reflects new state.
    r = await db_client.get("/consent/face-recognition", headers=headers)
    assert r.json()["state"] == "GRANTED"


async def test_grant_rejects_unchecked_boxes(db_client):
    _, headers = await register_and_login(db_client)
    r = await db_client.post(
        "/consent/face-recognition/grant",
        json={
            "signature_text": "Test User",
            "consent_collection": True,
            "consent_retention": False,
        },
        headers=headers,
    )
    assert r.status_code == 400


async def test_grant_rejects_blank_signature(db_client):
    _, headers = await register_and_login(db_client)
    r = await db_client.post(
        "/consent/face-recognition/grant",
        json={
            "signature_text": "x",  # below min_length=2
            "consent_collection": True,
            "consent_retention": True,
        },
        headers=headers,
    )
    assert r.status_code == 422


async def test_withdraw_purges_biometric_rows_and_audits(db_client):
    email_a = "lifecycle-a@example.com"
    _, headers = await register_and_login(db_client, email=email_a)
    await grant_consent(db_client, headers)

    user_id = await fetch_user_id(email_a)

    # Seed 3 faces — one named, two unlabeled in a cluster.
    await insert_face(user_id, person_name="Mom", cluster_id=1)
    await insert_face(user_id, person_name=None, cluster_id=2)
    await insert_face(user_id, person_name=None, cluster_id=2)

    assert await count_rows("faces", user_id) == 3
    assert await count_rows("face_detections", user_id) == 3
    assert await count_rows("persons", user_id) == 1

    # Withdraw — must hard-delete all biometric rows + crop blobs.
    r = await db_client.post(
        "/consent/face-recognition/withdraw", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["state"] == "WITHDRAWN"

    assert await count_rows("faces", user_id) == 0
    assert await count_rows("face_detections", user_id) == 0
    assert await count_rows("persons", user_id) == 0

    # Audit log records the deletion counts so we can prove what was destroyed.
    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import AuditLog

    async with SessionLocal() as s:
        entries = (
            await s.execute(
                select(AuditLog)
                .where(AuditLog.user_id == user_id)
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()
    actions = [e.action for e in entries]
    assert "consent.face_recognition.grant" in actions
    assert "consent.face_recognition.withdraw" in actions
    withdraw_audit = next(
        e for e in entries if e.action == "consent.face_recognition.withdraw"
    )
    assert withdraw_audit.details["faces_deleted"] == 3
    assert withdraw_audit.details["face_detections_deleted"] == 3
    assert withdraw_audit.details["persons_deleted"] == 1


async def test_withdraw_keeps_originals(db_client):
    """BIPA/GDPR: withdrawal is biometric-only — user's photos must remain."""
    email = "keeporiginals@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    user_id = await fetch_user_id(email)

    seed = await insert_face(user_id, person_name="Mom")
    image_id = seed["image_id"]

    await db_client.post("/consent/face-recognition/withdraw", headers=headers)

    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as s:
        img = (
            await s.execute(select(Image).where(Image.id == image_id))
        ).scalar_one_or_none()
    assert img is not None, "Image must survive consent withdrawal"
    assert img.user_id == user_id


async def test_regrant_after_withdraw_creates_new_record(db_client):
    """Re-grant after withdrawal must produce a fresh GRANTED row, not mutate."""
    email = "regrant@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    await db_client.post("/consent/face-recognition/withdraw", headers=headers)
    await grant_consent(db_client, headers)

    user_id = await fetch_user_id(email)
    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import ConsentRecord

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(ConsentRecord)
                .where(ConsentRecord.user_id == user_id)
                .order_by(ConsentRecord.id.asc())
            )
        ).scalars().all()
    states = [r.state for r in rows]
    assert states == ["GRANTED", "WITHDRAWN", "GRANTED"]

    # Status endpoint returns the latest.
    r = await db_client.get("/consent/face-recognition", headers=headers)
    assert r.json()["state"] == "GRANTED"


async def test_policy_endpoint_matches_file(db_client):
    r = await db_client.get("/consent/face-recognition/policy")
    assert r.status_code == 200
    body = r.json()
    on_disk = POLICY_PATH.read_text(encoding="utf-8")
    assert body["text"] == on_disk
    assert body["sha256_hex"] == hashlib.sha256(on_disk.encode("utf-8")).hexdigest()


async def test_consent_endpoints_require_auth(db_client):
    r = await db_client.get("/consent/face-recognition")
    assert r.status_code == 401
    r = await db_client.post(
        "/consent/face-recognition/grant",
        json={"signature_text": "x", "consent_collection": True, "consent_retention": True},
    )
    assert r.status_code == 401
    r = await db_client.post("/consent/face-recognition/withdraw")
    assert r.status_code == 401
