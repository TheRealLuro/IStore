"""§G2 — comments on shared files (and owner's own files).

Covers:
  - Owner can create, edit, and delete comments on their own image.
  - Cross-tenant: a stranger with no relation to the image gets 404
    on every comment endpoint (enumeration resistance — we don't
    distinguish "image doesn't exist" from "you can't see it").
  - Share recipient: a user with an active share grant can read and
    create comments. They can edit/delete only their own.
  - Threading: replies belong to the same image as their parent.
  - Soft delete: deleted comments stay in the list but the body
    blanks (FE renders a "comment deleted" placeholder).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tests.conftest import fetch_user_id, register_and_login


async def _create_image(user_id, *, filename="snap.jpg"):
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
        )
        s.add(img)
        await s.commit()
        return img.id


async def _grant_share(image_id, sharer_user_id, recipient_user_id):
    """Create an active share grant between two users for an image."""
    from backend.db import SessionLocal
    from backend.models import ShareGrant
    from hashlib import sha256

    async with SessionLocal() as s:
        grant = ShareGrant(
            image_id=image_id,
            sharer_user_id=sharer_user_id,
            recipient_user_id=recipient_user_id,
            recipient_email="recipient@example.com",
            token_hash=sha256(uuid.uuid4().hex.encode()).hexdigest(),
            # The schema requires the sharer-side duration window even
            # when there's no explicit expires_at. 7 days is the
            # default the FE picks; the actual value doesn't matter
            # for the comment-access test.
            sharer_duration_seconds=7 * 24 * 3600,
        )
        s.add(grant)
        await s.commit()
        return grant.id


# ---------- owner happy path ----------


async def test_owner_can_create_and_list_comments(db_client):
    _, headers = await register_and_login(db_client, email="g2-owner@example.com")
    user_id = await fetch_user_id("g2-owner@example.com")
    img_id = await _create_image(user_id)

    r = await db_client.post(
        f"/images/{img_id}/comments",
        json={"body": "first comment"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["body"] == "first comment"
    assert body["user_id"] == str(user_id)
    assert body["parent_id"] is None
    assert body["author_display_name"] is None  # not set in test setup
    assert body["author_email"] == "g2-owner@example.com"

    r = await db_client.get(f"/images/{img_id}/comments", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["body"] == "first comment"


async def test_owner_can_edit_own_comment(db_client):
    _, headers = await register_and_login(db_client, email="g2-edit@example.com")
    user_id = await fetch_user_id("g2-edit@example.com")
    img_id = await _create_image(user_id)

    r = await db_client.post(
        f"/images/{img_id}/comments",
        json={"body": "original"},
        headers=headers,
    )
    cid = r.json()["id"]

    r = await db_client.patch(
        f"/comments/{cid}",
        json={"body": "edited"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["body"] == "edited"


async def test_owner_can_soft_delete_own_comment(db_client):
    _, headers = await register_and_login(db_client, email="g2-del@example.com")
    user_id = await fetch_user_id("g2-del@example.com")
    img_id = await _create_image(user_id)

    r = await db_client.post(
        f"/images/{img_id}/comments",
        json={"body": "to delete"},
        headers=headers,
    )
    cid = r.json()["id"]

    r = await db_client.delete(f"/comments/{cid}", headers=headers)
    assert r.status_code == 204

    # Soft-deleted comments still appear in the list with body blanked
    # so the FE can render "comment deleted" placeholders.
    r = await db_client.get(f"/images/{img_id}/comments", headers=headers)
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["deleted_at"] is not None
    assert rows[0]["body"] == ""


# ---------- threading ----------


async def test_reply_parent_must_belong_to_same_image(db_client):
    _, headers = await register_and_login(db_client, email="g2-thread@example.com")
    user_id = await fetch_user_id("g2-thread@example.com")
    img_a = await _create_image(user_id, filename="a.jpg")
    img_b = await _create_image(user_id, filename="b.jpg")

    r = await db_client.post(
        f"/images/{img_a}/comments",
        json={"body": "root on A"},
        headers=headers,
    )
    root_id = r.json()["id"]

    # Replying on the same image: ok
    r = await db_client.post(
        f"/images/{img_a}/comments",
        json={"body": "reply on A", "parent_id": root_id},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    # Replying on image B with image A's parent: rejected
    r = await db_client.post(
        f"/images/{img_b}/comments",
        json={"body": "cross-image reply attempt", "parent_id": root_id},
        headers=headers,
    )
    assert r.status_code == 400


# ---------- cross-tenant isolation ----------


async def test_stranger_gets_404_on_comments(db_client):
    """A user with no share grant on the image can't list, create,
    edit, or delete its comments. 404 (not 403) so the existence of
    the image isn't leaked."""
    _, owner_headers = await register_and_login(db_client, email="g2-owner-x@example.com")
    owner_id = await fetch_user_id("g2-owner-x@example.com")
    img_id = await _create_image(owner_id)
    r = await db_client.post(
        f"/images/{img_id}/comments",
        json={"body": "owner comment"},
        headers=owner_headers,
    )
    cid = r.json()["id"]

    _, stranger_headers = await register_and_login(db_client, email="g2-stranger@example.com")

    # LIST: 404
    r = await db_client.get(f"/images/{img_id}/comments", headers=stranger_headers)
    assert r.status_code == 404

    # CREATE: 404
    r = await db_client.post(
        f"/images/{img_id}/comments",
        json={"body": "stranger comment"},
        headers=stranger_headers,
    )
    assert r.status_code == 404

    # EDIT: 403 (different error path — comment exists but author check)
    r = await db_client.patch(
        f"/comments/{cid}",
        json={"body": "stranger tampering"},
        headers=stranger_headers,
    )
    assert r.status_code == 403

    # DELETE: 403 (same — neither author nor owner)
    r = await db_client.delete(f"/comments/{cid}", headers=stranger_headers)
    assert r.status_code == 403


# ---------- share recipient ----------


async def test_share_recipient_can_read_and_write_comments(db_client):
    """A user with an active share grant on an image gets the same
    comment-create / comment-list access as the owner."""
    _, owner_headers = await register_and_login(db_client, email="g2-share-owner@example.com")
    owner_id = await fetch_user_id("g2-share-owner@example.com")
    img_id = await _create_image(owner_id)

    _, recipient_headers = await register_and_login(db_client, email="g2-share-recipient@example.com")
    recipient_id = await fetch_user_id("g2-share-recipient@example.com")

    await _grant_share(img_id, owner_id, recipient_id)

    # Recipient creates a comment
    r = await db_client.post(
        f"/images/{img_id}/comments",
        json={"body": "thanks for sharing"},
        headers=recipient_headers,
    )
    assert r.status_code == 201, r.text
    comment_id = r.json()["id"]

    # Owner can see it
    r = await db_client.get(f"/images/{img_id}/comments", headers=owner_headers)
    assert r.status_code == 200
    rows = r.json()
    assert any(c["id"] == comment_id and c["body"] == "thanks for sharing" for c in rows)

    # Recipient can edit their own
    r = await db_client.patch(
        f"/comments/{comment_id}",
        json={"body": "thanks for sharing (edited)"},
        headers=recipient_headers,
    )
    assert r.status_code == 200
    assert r.json()["body"] == "thanks for sharing (edited)"


async def test_image_owner_can_delete_recipient_comments(db_client):
    """Image owner can clean up comments on their own files — even if
    they're not the author."""
    _, owner_headers = await register_and_login(db_client, email="g2-mod-owner@example.com")
    owner_id = await fetch_user_id("g2-mod-owner@example.com")
    img_id = await _create_image(owner_id)

    _, recipient_headers = await register_and_login(db_client, email="g2-mod-recipient@example.com")
    recipient_id = await fetch_user_id("g2-mod-recipient@example.com")
    await _grant_share(img_id, owner_id, recipient_id)

    r = await db_client.post(
        f"/images/{img_id}/comments",
        json={"body": "spam by recipient"},
        headers=recipient_headers,
    )
    cid = r.json()["id"]

    # Owner deletes
    r = await db_client.delete(f"/comments/{cid}", headers=owner_headers)
    assert r.status_code == 204

    # But owner cannot EDIT the recipient's comment — that would let
    # the owner put words in the recipient's mouth.
    # First create a new one to test edit.
    r = await db_client.post(
        f"/images/{img_id}/comments",
        json={"body": "another from recipient"},
        headers=recipient_headers,
    )
    new_cid = r.json()["id"]
    r = await db_client.patch(
        f"/comments/{new_cid}",
        json={"body": "owner rewriting"},
        headers=owner_headers,
    )
    assert r.status_code == 403
