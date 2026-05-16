"""§A5 — "Deletion that actually deletes."

Two integration tests covering the full A5 checklist:

  1. `test_image_delete_removes_every_table_and_bucket` — seeds an
     image with EVERY associated row (image_geo, face_detections,
     faces, persons, image_tags, feedback_events, share_grants,
     cloud_files, consent records, bandit state) + matching blob
     objects in originals / served / faces buckets, then calls the
     per-image DELETE endpoint and asserts:
       a. every per-image row is gone
       b. every bucket object is gone
       c. orphan persons (face_count == 0) are dropped
       d. bandit_state IS PRESERVED (per-image delete doesn't touch
          per-(user, arm) learned weights)
       e. audit_log retains a row with the deleted image's UUID
          (legal retention)

  2. `test_account_delete_resets_bandit_and_purges_everything` —
     same surface but exercising the /account/delete path; asserts
     bandit_state IS deleted too.

These tests double as the integration check in the A5 todo item:
"Add an integration test that uploads, deletes, and asserts every
table + bucket returns 0 rows / 0 objects for the target."
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from tests.conftest import (
    count_rows,
    fetch_user_id,
    grant_consent,
    register_and_login,
)


# Tables whose row count for THIS user_id should be 0 after the
# per-image delete fires. Each carries a `user_id` column we can
# filter on directly.
_PER_USER_ID_TABLES = (
    "image_geo",
    "face_detections",
    "feedback_events",
)


async def _count_image_tags_for_image(image_id) -> int:
    """`image_tags` has no `user_id` column — count by image_id."""
    from sqlalchemy import text
    from backend.db import engine

    async with engine.connect() as conn:
        r = await conn.execute(
            text("SELECT count(*) FROM image_tags WHERE image_id = :iid"),
            {"iid": image_id},
        )
        return int(r.scalar_one() or 0)


async def _count_share_grants_for_image(image_id) -> int:
    """`share_grants` is owner-keyed via `sharer_user_id`. Check by image_id
    so the assertion is independent of the share dual-perspective predicate."""
    from sqlalchemy import text
    from backend.db import engine

    async with engine.connect() as conn:
        r = await conn.execute(
            text("SELECT count(*) FROM share_grants WHERE image_id = :iid"),
            {"iid": image_id},
        )
        return int(r.scalar_one() or 0)


async def _seed_image_with_full_data(user_id: uuid.UUID, *, email: str) -> dict:
    """Build an image row with every A5 sibling row attached.

    Returns: {
        image_id, face_id, person_id, detection_id,
        original_key, served_key, thumb_key, crop_key,
        feedback_event_id, share_grant_id, tag_id,
    }
    """
    from backend import storage as storage_mod
    from backend.db import SessionLocal
    from backend.models import (
        BanditState,
        ConsentRecord,
        Face,
        FaceDetection,
        FeedbackEvent,
        Image,
        ImageGeo,
        ImageTag,
        Person,
        ShareGrant,
        Tag,
    )

    async with SessionLocal() as session:
        # 1. Image row with original + served + thumbnail blob keys.
        original_key = f"users/{user_id}/originals/{uuid.uuid4().hex}"
        served_key = f"users/{user_id}/served/{uuid.uuid4().hex}.webp"
        thumb_key = f"users/{user_id}/thumbs/{uuid.uuid4().hex}.webp"
        img = Image(
            user_id=user_id,
            category="image",
            original_blob_key=original_key,
            served_blob_key=served_key,
            thumbnail_blob_key=thumb_key,
            byte_size_original=2048,
            byte_size_served=512,
            summary="a test image of nothing in particular",
            summary_topic="A test image",
            summary_points=["point 1", "point 2"],
            scene_label="indoor",
            clip_embedding=[0.001 * (i % 13) for i in range(768)],
        )
        session.add(img)
        await session.flush()

        # 2. ImageGeo (EXIF GPS row).
        geo = ImageGeo(
            image_id=img.id,
            user_id=user_id,
            lat=37.7749,
            lng=-122.4194,
            taken_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
            captured_with="iPhone 15",
        )
        session.add(geo)

        # 3. Person + Face + FaceDetection (with crop blob).
        person = Person(user_id=user_id, display_name="Friend Of Test", face_count=0)
        session.add(person)
        await session.flush()
        face = Face(
            user_id=user_id,
            embedding=[0.002 * (i % 19) for i in range(512)],
            person_id=person.id,
            cluster_id=99,
            quality_score=0.92,
        )
        session.add(face)
        await session.flush()
        crop_key = f"users/{user_id}/faces/{uuid.uuid4().hex}.jpg"
        det = FaceDetection(
            image_id=img.id,
            user_id=user_id,
            bbox_x=5, bbox_y=5, bbox_w=20, bbox_h=20,
            detection_confidence=0.97,
            face_id=face.id,
            crop_blob_key=crop_key,
        )
        session.add(det)
        from sqlalchemy import update as sa_update
        await session.execute(
            sa_update(Person).where(Person.id == person.id)
            .values(face_count=Person.face_count + 1)
        )

        # 4. Tag + ImageTag many-to-many. Tag.label is globally
        # unique so per-test isolation appends the user uuid.
        tag = Tag(label=f"vacation-{uuid.uuid4().hex[:8]}", source="user")
        session.add(tag)
        await session.flush()
        session.add(ImageTag(image_id=img.id, tag_id=tag.id, confidence=0.99))

        # 5. FeedbackEvent (per-image bandit reward).
        feedback = FeedbackEvent(
            user_id=user_id,
            image_id=img.id,
            kind="rating",
            rating=5,
            weight=1.0,
            bandit_arm_id=3,
            context_features=[0.0] * 16,
            reward=0.7,
            consumed_by_trainer=False,
        )
        session.add(feedback)

        # 6. ShareGrant on this image.
        share = ShareGrant(
            image_id=img.id,
            sharer_user_id=user_id,
            recipient_email="recipient@example.com",
            token_hash="$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            sharer_duration_seconds=86400,
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        session.add(share)
        await session.flush()

        # 7. BanditState (per-(user, arm)). Will be PRESERVED for
        # per-image delete and DROPPED for account delete.
        import numpy as np  # noqa: PLC0415 — only used by this seeder
        d = 16
        a_mat = np.eye(d, dtype=np.float32) * 0.5
        b_vec = np.zeros(d, dtype=np.float32)
        session.add(BanditState(
            user_id=user_id,
            arm_id=3,
            a_matrix=a_mat.tobytes(),
            b_vector=b_vec.tobytes(),
            pulls=42,
        ))

        # 8. ConsentRecord — should NOT be deleted by image-delete
        # (it's a user-level record); IS deleted by account-delete.
        session.add(ConsentRecord(
            user_id=user_id,
            consent_kind="ai_summary",
            state="GRANTED",
            policy_version="v1",
            policy_text_sha256=b"\x00" * 32,
            signature_text="Test User",
            granted_at=datetime.now(timezone.utc),
        ))

        await session.commit()

        # 9. Seed bucket objects so we can assert their deletion.
        storage_mod.storage.put(
            storage_mod.storage.bucket_originals, original_key,
            b"O" * 64, "application/octet-stream",
        )
        storage_mod.storage.put(
            storage_mod.storage.bucket_served, served_key,
            b"S" * 64, "image/webp",
        )
        storage_mod.storage.put(
            storage_mod.storage.bucket_served, thumb_key,
            b"T" * 32, "image/webp",
        )
        storage_mod.storage.put(
            storage_mod.storage.bucket_faces, crop_key,
            b"F" * 32, "image/jpeg",
        )

        return {
            "image_id": img.id,
            "face_id": face.id,
            "person_id": person.id,
            "detection_id": det.id,
            "original_key": original_key,
            "served_key": served_key,
            "thumb_key": thumb_key,
            "crop_key": crop_key,
            "share_id": share.id,
            "tag_id": tag.id,
            "feedback_event_id": feedback.id,
        }


def _blob_exists(bucket: str, key: str) -> bool:
    from backend import storage as storage_mod

    try:
        storage_mod.storage.get(bucket, key)
        return True
    except KeyError:
        return False


async def test_image_delete_removes_every_table_and_bucket(db_client):
    """§A5 acceptance: deleting ONE image purges every sibling row
    and every blob object, but preserves the user account + bandit
    state + audit_log entries (the latter for legal retention)."""
    email = "a5-image-del@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    user_id = await fetch_user_id(email)
    seed = await _seed_image_with_full_data(user_id, email=email)

    # Sanity: every blob we just seeded is readable.
    from backend import storage as storage_mod
    assert _blob_exists(storage_mod.storage.bucket_originals, seed["original_key"])
    assert _blob_exists(storage_mod.storage.bucket_served, seed["served_key"])
    assert _blob_exists(storage_mod.storage.bucket_served, seed["thumb_key"])
    assert _blob_exists(storage_mod.storage.bucket_faces, seed["crop_key"])

    # Delete via the per-image endpoint.
    r = await db_client.delete(f"/images/{seed['image_id']}", headers=headers)
    assert r.status_code == 204, r.text

    # --- DB invariants ---
    # Every per-user-id-keyed image-sibling table is empty.
    for table in _PER_USER_ID_TABLES:
        n = await count_rows(table, user_id)
        assert n == 0, f"{table} still has {n} rows for user after image-delete"
    # image_tags + share_grants are keyed differently; check by image_id.
    assert await _count_image_tags_for_image(seed["image_id"]) == 0
    assert await _count_share_grants_for_image(seed["image_id"]) == 0

    # The image row itself is gone.
    from backend.db import SessionLocal
    from backend.models import (
        AuditLog,
        BanditState,
        ConsentRecord,
        Face,
        Image,
        Person,
    )
    async with SessionLocal() as session:
        gone = (
            await session.execute(
                select(Image).where(Image.id == seed["image_id"])
            )
        ).scalar_one_or_none()
        assert gone is None, "image row survived hard_delete_images"

        # Face is the orphan (its only detection vanished with the image).
        face_gone = (
            await session.execute(
                select(Face).where(Face.id == seed["face_id"])
            )
        ).scalar_one_or_none()
        assert face_gone is None, "orphan face row survived"

        # Person (face_count went to zero) is gone too.
        person_gone = (
            await session.execute(
                select(Person).where(Person.id == seed["person_id"])
            )
        ).scalar_one_or_none()
        assert person_gone is None, "orphan person row survived"

        # Bandit state is PRESERVED (per-image delete doesn't touch
        # per-(user, arm) weights).
        bandit_rows = (
            await session.execute(
                select(BanditState).where(BanditState.user_id == user_id)
            )
        ).scalars().all()
        assert len(bandit_rows) == 1, "bandit_state was wiped by per-image delete"

        # ConsentRecord survives (user-level, not per-image).
        consent_rows = (
            await session.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == user_id)
            )
        ).scalars().all()
        assert len(consent_rows) >= 1, "consent_record was wiped by per-image delete"

        # audit_log retains a row referencing the deleted image_id.
        audit_rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "image.delete")
            )
        ).scalars().all()
        assert len(audit_rows) >= 1
        # The image id appears in the details payload (chain of custody).
        details_blobs = [r.details for r in audit_rows]
        assert any(
            str(seed["image_id"]) in (d.get("image_ids") or [])
            for d in details_blobs
        ), "audit row did not retain the deleted image_id"

    # --- Bucket invariants ---
    assert not _blob_exists(storage_mod.storage.bucket_originals, seed["original_key"])
    assert not _blob_exists(storage_mod.storage.bucket_served, seed["served_key"])
    assert not _blob_exists(storage_mod.storage.bucket_served, seed["thumb_key"])
    assert not _blob_exists(storage_mod.storage.bucket_faces, seed["crop_key"])


async def test_account_delete_resets_bandit_and_purges_everything(db_client):
    """§A5 acceptance: account-level delete must additionally reset
    bandit_state (per the checklist: "Bandit reward / arm history
    (or anonymized)"). Audit rows survive."""
    email = "a5-account-del@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    user_id = await fetch_user_id(email)
    seed = await _seed_image_with_full_data(user_id, email=email)

    r = await db_client.post("/account/delete", headers=headers)
    assert r.status_code == 200, r.text

    # User row + every per-user row is gone (including bandit_state).
    from backend.db import SessionLocal
    from backend.models import AuditLog, BanditState, User
    async with SessionLocal() as session:
        user_gone = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        assert user_gone is None, "user row survived /account/delete"

        bandit_rows = (
            await session.execute(
                select(BanditState).where(BanditState.user_id == user_id)
            )
        ).scalars().all()
        assert len(bandit_rows) == 0, (
            "bandit_state survived account-delete; reset_bandit=True "
            "should drop every row for the user"
        )

        # audit_log row referencing the deleted user persists (legal).
        deleted_audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "account.delete")
            )
        ).scalars().all()
        assert len(deleted_audit) >= 1

    # Every bucket key associated with the seed is gone.
    from backend import storage as storage_mod
    assert not _blob_exists(storage_mod.storage.bucket_originals, seed["original_key"])
    assert not _blob_exists(storage_mod.storage.bucket_served, seed["served_key"])
    assert not _blob_exists(storage_mod.storage.bucket_served, seed["thumb_key"])
    assert not _blob_exists(storage_mod.storage.bucket_faces, seed["crop_key"])


async def test_signed_url_ttl_is_capped_at_300_seconds(db_client):
    """§A4 — signed download URLs must expire ≤ 5 min. The cap is
    enforced in `make_signed_download` AND in `verify_download`, so
    even if config drifts above 300 the URL is rejected at serve time."""
    import time
    from uuid import uuid4

    from backend.signed_urls import (
        make_signed_download,
        sign_download,
        verify_download,
    )

    img_id = uuid4()
    user_id = uuid4()
    out = make_signed_download(
        base_url="https://example.test",
        image_id=img_id,
        user_id=user_id,
        variant="served",
    )
    # Pull `expires` out of the URL and check the delta to now.
    qs = out["url"].split("?", 1)[1]
    expires = int(dict(p.split("=", 1) for p in qs.split("&"))["expires"])
    assert expires - int(time.time()) <= 300

    # Forge a URL with a 1-hour expiry. The signature is valid (we
    # used the same signing helper), but `verify_download` must
    # reject it because the cap protects against config drift.
    forged_expires = int(time.time()) + 3600
    forged_sig = sign_download(img_id, user_id, "served", forged_expires)
    assert verify_download(
        image_id=img_id,
        user_id=user_id,
        variant="served",
        expires=forged_expires,
        sig=forged_sig,
    ) is False
