"""Phase 4 acceptance: cross-user isolation.

Two users (A and B) both grant consent and have biometric data in the DB.
Under no API access pattern may user A see, fetch, or mutate user B's
faces, persons, face crops, or filtered images. This is the central
GDPR Art. 9 / BIPA cross-user safety guarantee.

The current implementation enforces isolation in application code via
`WHERE user_id = current_user.id` on every query. This test surface-
exercises every Phase 4 endpoint that returns or accepts biometric IDs to
confirm the filter is consistent.
"""
from __future__ import annotations

from tests.conftest import (
    fetch_user_id,
    grant_consent,
    insert_face,
    register_and_login,
)


async def _seed_two_users(db_client):
    email_a = "leak-a@example.com"
    email_b = "leak-b@example.com"
    _, headers_a = await register_and_login(db_client, email=email_a)
    _, headers_b = await register_and_login(db_client, email=email_b)
    await grant_consent(db_client, headers_a)
    await grant_consent(db_client, headers_b)

    uid_a = await fetch_user_id(email_a)
    uid_b = await fetch_user_id(email_b)

    seed_a = await insert_face(uid_a, person_name="Mom_A", cluster_id=1)
    seed_b = await insert_face(uid_b, person_name="Mom_B", cluster_id=1)
    # Add an unnamed cluster for B too — A must not see it.
    seed_b_unnamed = await insert_face(uid_b, person_name=None, cluster_id=42)

    return {
        "headers_a": headers_a,
        "headers_b": headers_b,
        "uid_a": uid_a,
        "uid_b": uid_b,
        "a": seed_a,
        "b": seed_b,
        "b_unnamed": seed_b_unnamed,
    }


async def test_list_people_isolates_by_user(db_client):
    s = await _seed_two_users(db_client)

    r_a = await db_client.get("/people/", headers=s["headers_a"])
    r_b = await db_client.get("/people/", headers=s["headers_b"])
    assert r_a.status_code == 200 and r_b.status_code == 200
    body_a, body_b = r_a.json(), r_b.json()

    a_persons = {p["display_name"] for p in body_a["persons"]}
    b_persons = {p["display_name"] for p in body_b["persons"]}
    assert a_persons == {"Mom_A"}
    assert b_persons == {"Mom_B"}

    # B has one unnamed cluster, A has none.
    a_clusters = [c["cluster_id"] for c in body_a["unlabeled_clusters"]]
    b_clusters = [c["cluster_id"] for c in body_b["unlabeled_clusters"]]
    assert a_clusters == []
    assert 42 in b_clusters

    # total_faces is per-user.
    assert body_a["total_faces"] == 1
    assert body_b["total_faces"] == 2


async def test_face_crop_endpoint_blocks_other_user(db_client):
    s = await _seed_two_users(db_client)
    b_face_id = s["b"]["face_id"]
    # User A asking for user B's face crop must 404 (not 200, not 403 leaking
    # existence). Endpoint returns 404 to avoid disclosing whether the ID exists.
    r = await db_client.get(f"/faces/{b_face_id}/crop", headers=s["headers_a"])
    assert r.status_code == 404
    # Same request from B succeeds (provided the blob is in the stub).
    r_self = await db_client.get(f"/faces/{b_face_id}/crop", headers=s["headers_b"])
    # 404 acceptable (stub didn't pre-populate the blob); the security check
    # is that A's response is exactly the same shape as if the row didn't exist.
    assert r_self.status_code in (200, 404)


async def test_name_cluster_blocks_other_users_cluster(db_client):
    s = await _seed_two_users(db_client)
    b_cluster = s["b_unnamed"]["cluster_id"]

    # User A tries to claim user B's cluster.
    r = await db_client.post(
        f"/people/clusters/{b_cluster}",
        json={"display_name": "Hijacked"},
        headers=s["headers_a"],
    )
    # Endpoint returns 200 with 0 reassignments — A's WHERE filter matches
    # zero rows, so no faces are mutated. Verify directly.
    assert r.status_code == 200

    from tests.conftest import count_rows
    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import Face, Person

    async with SessionLocal() as session:
        # B's faces in cluster 42 must still be unlabeled (person_id is NULL).
        rows = (
            await session.execute(
                select(Face).where(
                    Face.user_id == s["uid_b"], Face.cluster_id == b_cluster
                )
            )
        ).scalars().all()
        assert all(f.person_id is None for f in rows), \
            "User A must not be able to assign a person to user B's cluster"

        # A may have created an empty 'Hijacked' person under their own
        # account. That's fine — the security property is that no face row
        # of B's was reassigned. count_rows confirms A's persons.
        a_persons = await count_rows("persons", s["uid_a"])
        # A has 'Mom_A' from seeding plus possibly 'Hijacked'. Either count is
        # acceptable as long as no B faces leaked into A's persons.
        assert a_persons in (1, 2)

        # B's persons untouched.
        b_persons = (
            await session.execute(
                select(Person).where(Person.user_id == s["uid_b"])
            )
        ).scalars().all()
        assert {p.display_name for p in b_persons} == {"Mom_B"}


async def test_rename_other_users_person_404s(db_client):
    s = await _seed_two_users(db_client)
    b_person_id = s["b"]["person_id"]

    r = await db_client.patch(
        f"/people/{b_person_id}",
        json={"display_name": "Renamed_By_A"},
        headers=s["headers_a"],
    )
    assert r.status_code == 404

    # Confirm B's person is unchanged.
    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import Person

    async with SessionLocal() as session:
        person = (
            await session.execute(select(Person).where(Person.id == b_person_id))
        ).scalar_one()
        assert person.display_name == "Mom_B"


async def test_delete_other_users_person_no_op(db_client):
    s = await _seed_two_users(db_client)
    b_person_id = s["b"]["person_id"]

    r = await db_client.delete(
        f"/people/{b_person_id}", headers=s["headers_a"]
    )
    # Endpoint returns 204 either way (idempotent), but the rows must survive.
    assert r.status_code == 204

    from tests.conftest import count_rows

    assert await count_rows("persons", s["uid_b"]) == 1
    assert await count_rows("faces", s["uid_b"]) == 2  # named + unnamed
    assert await count_rows("face_detections", s["uid_b"]) == 2


async def test_image_list_person_filter_isolates(db_client):
    """A person name from user B's gallery must not leak images to user A,
    even if A passes B's display name as a query string."""
    s = await _seed_two_users(db_client)

    r = await db_client.get(
        "/images/?person=Mom_B", headers=s["headers_a"]
    )
    assert r.status_code == 200
    assert r.json() == [], (
        "Filtering by another user's person name must not return their images"
    )

    # Ensure A can still see their own person's images.
    r = await db_client.get(
        "/images/?person=Mom_A", headers=s["headers_a"]
    )
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["id"] == str(s["a"]["image_id"])


async def test_withdraw_does_not_touch_other_user(db_client):
    """Critical: A withdrawing must not delete any of B's biometric data."""
    s = await _seed_two_users(db_client)

    from tests.conftest import count_rows

    assert await count_rows("faces", s["uid_b"]) == 2

    r = await db_client.post(
        "/consent/face-recognition/withdraw", headers=s["headers_a"]
    )
    assert r.status_code == 200

    # A is wiped.
    assert await count_rows("faces", s["uid_a"]) == 0
    assert await count_rows("face_detections", s["uid_a"]) == 0
    assert await count_rows("persons", s["uid_a"]) == 0

    # B is preserved.
    assert await count_rows("faces", s["uid_b"]) == 2
    assert await count_rows("face_detections", s["uid_b"]) == 2
    assert await count_rows("persons", s["uid_b"]) == 1


async def test_backfill_only_queues_own_pending_images(db_client):
    """A's /people/backfill must only consider A's images, even if B has
    pending_face_scan=True images at the same time."""
    s = await _seed_two_users(db_client)

    # Insert pending images for both users.
    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as session:
        for uid, _ in ((s["uid_a"], "a"), (s["uid_b"], "b")):
            for i in range(3):
                session.add(
                    Image(
                        user_id=uid,
                        category="image",
                        served_blob_key=f"users/{uid}/served/p{i}.webp",
                        original_blob_key=f"users/{uid}/originals/p{i}",
                        byte_size_original=100,
                        byte_size_served=80,
                        pending_face_scan=True,
                    )
                )
        await session.commit()

    r = await db_client.post("/people/backfill", headers=s["headers_a"])
    assert r.status_code == 200
    body = r.json()
    assert body["consent_active"] is True
    # Exactly A's 3 pending images. B's 3 must not be included.
    assert body["queued"] == 3
