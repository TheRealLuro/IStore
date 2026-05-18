"""§C4.2 — "Me" -> user.display_name binding.

When the user labels a face cluster (or renames a person) with the
literal token "Me", the backend substitutes their account display
name so AI summaries say their real name. If no display name is set,
the endpoint returns 422 with a structured `code: missing_display_name`
detail the frontend keys on for an inline prompt.
"""
from __future__ import annotations

from tests.conftest import (
    fetch_user_id,
    grant_consent,
    insert_face,
    register_and_login,
)


async def _set_display_name(name: str, email: str) -> None:
    from backend.db import SessionLocal
    from backend.models import User
    from sqlalchemy import update as sa_update

    async with SessionLocal() as s:
        await s.execute(
            sa_update(User).where(User.email == email).values(display_name=name)
        )
        await s.commit()


async def test_name_cluster_substitutes_me_with_display_name(db_client):
    email = "c42-with-name@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    await _set_display_name("Jakub", email)
    user_id = await fetch_user_id(email)

    # Insert an unlabeled cluster.
    f = await insert_face(user_id, person_name=None, cluster_id=42)

    # Name the cluster "Me" — should resolve to "Jakub".
    r = await db_client.post(
        f"/people/clusters/{f['cluster_id']}",
        json={"display_name": "Me"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Jakub"


async def test_name_cluster_returns_422_when_no_display_name(db_client):
    email = "c42-no-name@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    user_id = await fetch_user_id(email)

    f = await insert_face(user_id, person_name=None, cluster_id=7)

    r = await db_client.post(
        f"/people/clusters/{f['cluster_id']}",
        json={"display_name": "Me"},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "missing_display_name"


async def test_name_cluster_passes_through_non_me_names(db_client):
    """Non-'Me' names go through unchanged, even when display name is empty."""
    email = "c42-other-name@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    user_id = await fetch_user_id(email)

    f = await insert_face(user_id, person_name=None, cluster_id=1)

    r = await db_client.post(
        f"/people/clusters/{f['cluster_id']}",
        json={"display_name": "Alice"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Alice"


async def test_name_cluster_me_is_case_insensitive(db_client):
    """'me', 'ME', '  Me  ' all map to the user's display name."""
    email = "c42-case@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    await _set_display_name("Sasha", email)
    user_id = await fetch_user_id(email)

    for cid, typed in [(1, "me"), (2, "ME"), (3, "  Me  ")]:
        await insert_face(user_id, person_name=None, cluster_id=cid)
        r = await db_client.post(
            f"/people/clusters/{cid}",
            json={"display_name": typed},
            headers=headers,
        )
        assert r.status_code == 200, (typed, r.text)
        assert r.json()["display_name"] == "Sasha"


async def test_rename_person_substitutes_me(db_client):
    """PATCH /people/{id} with display_name='Me' also resolves."""
    email = "c42-rename@example.com"
    _, headers = await register_and_login(db_client, email=email)
    await grant_consent(db_client, headers)
    await _set_display_name("Riley", email)
    user_id = await fetch_user_id(email)

    # Create a person named Bob via insert_face, then rename to "Me".
    f = await insert_face(user_id, person_name="Bob", cluster_id=99)
    person_id = f["person_id"]

    r = await db_client.patch(
        f"/people/{person_id}",
        json={"display_name": "Me"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Riley"
