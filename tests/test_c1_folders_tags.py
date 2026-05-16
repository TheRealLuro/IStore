"""§C1.2 – C1.6 acceptance.

  - suggest_names returns ≤ 3 filename-safe proposals derived from the
    image's summary signals.
  - /folders?contains_type=image hides folders whose subtree has no
    images.
  - DELETE /search/history returns 204 + writes an audit row.
  - /tags CRUD + image/folder attach + per-user isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from tests.conftest import fetch_user_id, register_and_login


async def _create_image(user_id, *, filename: str = "snap.jpg", **kwargs):
    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as s:
        img = Image(
            user_id=user_id,
            category="image",
            original_filename=filename,
            served_blob_key=f"users/{user_id}/served/{uuid.uuid4().hex}.webp",
            original_blob_key=f"users/{user_id}/originals/{uuid.uuid4().hex}",
            pending_face_scan=False,
            pending_summary=False,
            byte_size_original=1024,
            byte_size_served=512,
            summary_topic=kwargs.get("topic"),
            summary=kwargs.get("summary"),
            scene_label=kwargs.get("scene"),
        )
        s.add(img)
        await s.commit()
        return img.id


# ---------- §C1.2 ----------


async def test_suggest_names_returns_proposals_for_image_with_summary(db_client):
    _, headers = await register_and_login(db_client, email="c12-a@example.com")
    user_id = await fetch_user_id("c12-a@example.com")
    img_id = await _create_image(
        user_id,
        filename="IMG_0042.jpg",
        topic="Whiteboard sketch — auth flow",
        summary="A whiteboard sketch outlining the OAuth flow with several boxes and arrows.",
        scene="indoor",
    )

    r = await db_client.get(f"/images/{img_id}/suggest-names", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["image_id"] == str(img_id)
    suggestions = body["suggestions"]
    assert 1 <= len(suggestions) <= 3
    for s in suggestions:
        # Every proposal is filename-safe and keeps the original ext.
        assert s["name"].endswith(".jpg")
        assert "/" not in s["name"] and "\\" not in s["name"]
        assert len(s["name"]) <= 60 + len(".jpg")


async def test_suggest_names_works_when_summary_pending(db_client):
    _, headers = await register_and_login(db_client, email="c12-b@example.com")
    user_id = await fetch_user_id("c12-b@example.com")
    # Mark pending so the suggestion path takes the scene+date fallback.
    from backend.db import SessionLocal
    from backend.models import Image
    img_id = await _create_image(user_id, filename="raw.jpg")
    async with SessionLocal() as s:
        img = (await s.execute(select(Image).where(Image.id == img_id))).scalar_one()
        img.pending_summary = True
        await s.commit()

    r = await db_client.get(f"/images/{img_id}/suggest-names", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["pending_summary"] is True
    assert len(body["suggestions"]) >= 1


# ---------- §C1.3 ----------


async def test_folders_contains_type_image(db_client):
    _, headers = await register_and_login(db_client, email="c13@example.com")
    user_id = await fetch_user_id("c13@example.com")

    photos = await db_client.post(
        "/folders/", json={"name": "Photos"}, headers=headers,
    )
    docs = await db_client.post(
        "/folders/", json={"name": "Docs"}, headers=headers,
    )
    assert photos.status_code == 201 and docs.status_code == 201
    photos_id = photos.json()["id"]
    docs_id = docs.json()["id"]

    # Put an image in Photos only.
    from backend.db import SessionLocal
    from backend.models import Image
    async with SessionLocal() as s:
        img = Image(
            user_id=user_id,
            category="image",
            folder_id=uuid.UUID(photos_id),
            original_filename="a.jpg",
            served_blob_key=f"users/{user_id}/served/x.webp",
            pending_face_scan=False, pending_summary=False,
            byte_size_original=100, byte_size_served=50,
        )
        s.add(img)
        await s.commit()

    # No filter → both folders.
    r_all = await db_client.get("/folders/", headers=headers)
    assert r_all.status_code == 200
    names_all = {f["name"] for f in r_all.json()}
    assert names_all >= {"Photos", "Docs"}

    # contains_type=image → only Photos.
    r_img = await db_client.get("/folders/?contains_type=image", headers=headers)
    assert r_img.status_code == 200
    names_img = {f["name"] for f in r_img.json()}
    assert "Photos" in names_img
    assert "Docs" not in names_img

    # Bad type → 400.
    r_bad = await db_client.get("/folders/?contains_type=bogus", headers=headers)
    assert r_bad.status_code == 400


# ---------- §C1.4 ----------


async def test_clear_search_history_writes_audit(db_client):
    _, headers = await register_and_login(db_client, email="c14@example.com")
    user_id = await fetch_user_id("c14@example.com")

    r = await db_client.delete("/search/history", headers=headers)
    assert r.status_code == 204

    from backend.db import SessionLocal
    from backend.models import AuditLog

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_id,
                    AuditLog.action == "search.history.cleared",
                )
            )
        ).scalars().all()
        assert len(rows) == 1


# ---------- §C1.6 ----------


async def test_tags_crud_and_attach(db_client):
    _, headers_a = await register_and_login(db_client, email="c16-a@example.com")
    _, headers_b = await register_and_login(db_client, email="c16-b@example.com")
    user_a = await fetch_user_id("c16-a@example.com")

    # Create.
    r = await db_client.post(
        "/tags/", json={"label": "Important", "color": "red"}, headers=headers_a,
    )
    assert r.status_code == 201, r.text
    tag = r.json()
    assert tag["label"] == "Important"
    assert tag["color"] == "red"

    # Same label, same user → idempotent (returns the existing tag).
    again = await db_client.post(
        "/tags/", json={"label": "important"}, headers=headers_a,
    )
    assert again.status_code == 201
    assert again.json()["id"] == tag["id"]

    # Different user CAN reuse the label.
    r_b = await db_client.post(
        "/tags/", json={"label": "Important", "color": "blue"}, headers=headers_b,
    )
    assert r_b.status_code == 201
    assert r_b.json()["id"] != tag["id"]

    # Update color.
    r_u = await db_client.patch(
        f"/tags/{tag['id']}", json={"color": "green"}, headers=headers_a,
    )
    assert r_u.status_code == 200
    assert r_u.json()["color"] == "green"

    # Bad color → 400.
    r_bad = await db_client.patch(
        f"/tags/{tag['id']}", json={"color": "neon-pink"}, headers=headers_a,
    )
    assert r_bad.status_code == 400

    # Cross-user mutation → 404.
    r_x = await db_client.patch(
        f"/tags/{tag['id']}", json={"label": "Owned"}, headers=headers_b,
    )
    assert r_x.status_code == 404

    # Attach to an image.
    img_id = await _create_image(user_a, filename="a.jpg")
    r_a = await db_client.post(
        f"/images/{img_id}/tags",
        json={"tag_id": tag["id"]},
        headers=headers_a,
    )
    assert r_a.status_code == 201

    # Image listing surfaces the tag.
    r_list = await db_client.get("/images/", headers=headers_a)
    assert r_list.status_code == 200
    img_row = next(i for i in r_list.json() if i["id"] == str(img_id))
    assert any(t["id"] == tag["id"] for t in img_row.get("tags", []))

    # Re-attach is idempotent.
    r_a2 = await db_client.post(
        f"/images/{img_id}/tags",
        json={"tag_id": tag["id"]},
        headers=headers_a,
    )
    assert r_a2.status_code == 201

    # Detach.
    r_d = await db_client.delete(
        f"/images/{img_id}/tags/{tag['id']}", headers=headers_a,
    )
    assert r_d.status_code == 204

    # Listing again — tag is gone.
    r_list2 = await db_client.get("/images/", headers=headers_a)
    img_row2 = next(i for i in r_list2.json() if i["id"] == str(img_id))
    assert all(t["id"] != tag["id"] for t in img_row2.get("tags", []))


async def test_tag_attach_by_label_creates_on_the_fly(db_client):
    _, headers = await register_and_login(db_client, email="c16-c@example.com")
    user_id = await fetch_user_id("c16-c@example.com")
    img_id = await _create_image(user_id)

    r = await db_client.post(
        f"/images/{img_id}/tags",
        json={"label": "Brand new", "color": "violet"},
        headers=headers,
    )
    assert r.status_code == 201
    tag = r.json()
    assert tag["label"] == "Brand new"
    assert tag["color"] == "violet"

    # Re-attach with the same label → matches the existing tag id.
    r2 = await db_client.post(
        f"/images/{img_id}/tags",
        json={"label": "brand new"},
        headers=headers,
    )
    assert r2.status_code == 201
    assert r2.json()["id"] == tag["id"]
