"""Regression: GET /people/ photo counts MUST be per-person, not the
global library total.

The bug we keep hitting: a correlated UNION scalar subquery where the
inner SELECTs reference Person.id from the outer scope. SQLAlchemy's
auto-correlation doesn't reach into UNION components reliably, so each
branch ends up selecting "all faces of all persons" and the outer
COUNT returns the global image count for every Person card.

Two persons with disjoint images must show disjoint counts. If both
show the library total, the correlation regressed.
"""
from __future__ import annotations

from tests.conftest import (
    fetch_user_id,
    grant_consent,
    insert_face,
    register_and_login,
)


async def test_people_photo_count_is_per_person(db_client):
    _, headers = await register_and_login(db_client, email="people-count@example.com")
    await grant_consent(db_client, headers)
    user_id = await fetch_user_id("people-count@example.com")

    # Alice: 3 face-detected images. Bob: 1 face-detected image.
    # If the bug returns, both cards report 4.
    await insert_face(user_id, person_name="Alice", cluster_id=1)
    await insert_face(user_id, person_name=None, cluster_id=2)  # need Alice's person_id
    # Reuse Alice's person via a second face on a second image.
    from backend.db import SessionLocal
    from backend.models import Face, Person
    from sqlalchemy import select as sa_select

    async with SessionLocal() as s:
        alice_id = (
            await s.execute(
                sa_select(Person.id).where(
                    Person.user_id == user_id, Person.display_name == "Alice"
                )
            )
        ).scalar_one()

    # 2 more face-detected images for Alice
    await insert_face(user_id, person_name=None, cluster_id=1)
    await insert_face(user_id, person_name=None, cluster_id=1)
    async with SessionLocal() as s:
        # Attach those two faces to Alice
        from sqlalchemy import update as sa_update
        await s.execute(
            sa_update(Face)
            .where(Face.user_id == user_id, Face.person_id.is_(None))
            .values(person_id=alice_id)
        )
        await s.commit()

    # Bob: 1 detected face
    await insert_face(user_id, person_name="Bob", cluster_id=3)

    r = await db_client.get("/people/", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {p["display_name"]: p for p in body["persons"]}

    assert "Alice" in by_name and "Bob" in by_name
    # Test setup: 1 face for Alice up front, then 3 orphan faces on
    # 3 new images all updated → Alice. Alice = 4 distinct images.
    # Bob = 1 distinct image. Library total = 5.
    # If the correlation regresses, both will read 5.
    assert by_name["Alice"]["face_count"] == 4, by_name
    assert by_name["Bob"]["face_count"] == 1, by_name
    assert by_name["Alice"]["face_count"] != by_name["Bob"]["face_count"], (
        "people counts collapsed to the same number — correlation regressed"
    )
