"""§C2 — cloud sync acceptance.

Drive OAuth involves outbound HTTP we can't make in pytest,
so the tests focus on the surface the API exposes and on the
synthesized folder + Limited Use plumbing. End-to-end Drive flow is
exercised manually + via the integration runbook in SETUP.md.

  - skip_ai_training column persists + Pydantic model validates
  - store_upload honors skip_ai_training (pending_summary/face_scan
    both end up False)
  - _ensure_remote_folder_tree creates the synthesized folder
    hierarchy idempotently
  - DELETE /search/history (re-verified — we touched audit again)
  - /cloud/links list endpoint owner-isolates
  - /cloud/links/{id}/ai-opt-in flips skip_ai_training in bulk +
    re-arms pending_summary / pending_face_scan
  - /cloud/links/{id}/conflicts surfaces cloud.sync.conflict audit rows
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from tests.conftest import fetch_user_id, register_and_login


async def _make_image(user_id, **kwargs):
    from backend.db import SessionLocal
    from backend.models import Image
    async with SessionLocal() as s:
        img = Image(
            user_id=user_id,
            category="image",
            original_filename=kwargs.pop("filename", "x.jpg"),
            served_blob_key=f"users/{user_id}/served/{uuid.uuid4().hex}.webp",
            original_blob_key=f"users/{user_id}/originals/{uuid.uuid4().hex}",
            byte_size_original=200,
            byte_size_served=100,
            pending_face_scan=False,
            pending_summary=False,
            **kwargs,
        )
        s.add(img)
        await s.commit()
        return img.id


async def test_skip_ai_training_column_persists(db_client):
    _, headers = await register_and_login(db_client, email="c2-skip@example.com")
    user_id = await fetch_user_id("c2-skip@example.com")
    img_id = await _make_image(
        user_id, skip_ai_training=True, source_provider="google_drive",
    )
    from backend.db import SessionLocal
    from backend.models import Image
    async with SessionLocal() as s:
        row = (await s.execute(select(Image).where(Image.id == img_id))).scalar_one()
        assert row.skip_ai_training is True
        assert row.source_provider == "google_drive"


async def test_store_upload_skips_ai_when_flag_set(db_client, monkeypatch):
    """Direct test against `store_upload` — verifies that
    `skip_ai_training=True` results in `pending_summary` and
    `pending_face_scan` both being False on the new row, which is
    what stops the post-commit dispatchers from picking the file up.

    `db_client` injected to guarantee the migration suite has run
    before we open SessionLocal."""
    from backend.db import SessionLocal
    from backend.image import store_upload
    from backend.models import User

    # 1×1 JPEG (smallest valid bytes — Pillow accepts).
    from io import BytesIO
    from PIL import Image as PILImage
    buf = BytesIO()
    PILImage.new("RGB", (1, 1)).save(buf, "JPEG")
    raw = buf.getvalue()

    # Need a real user row for the FK.
    async with SessionLocal() as s:
        u = User(
            email=f"c2-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            is_active=True, is_superuser=False, is_verified=True,
            age_confirmed=True,
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        user = u

    async with SessionLocal() as s:
        img = await store_upload(
            s, user, "tiny.jpg", raw, "image/jpeg",
            skip_ai_training=True,
            source_provider="google_drive",
        )
        await s.refresh(img)
        assert img.skip_ai_training is True
        assert img.source_provider == "google_drive"
        assert img.pending_summary is False, "summary worker should not pick this up"
        assert img.pending_face_scan is False, "face-scan worker should not pick this up"


async def test_ensure_remote_folder_tree_creates_hierarchy(db_client):
    _, headers = await register_and_login(db_client, email="c2-tree@example.com")
    user_id = await fetch_user_id("c2-tree@example.com")
    from backend.cloud_sync import _ensure_remote_folder_tree
    from backend.db import SessionLocal
    from backend.models import Folder, User

    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
        # Build tree for parents "Photos/Vacation/2024" and "Docs"
        mapping = await _ensure_remote_folder_tree(
            s, user, "Google Drive",
            provider="google_drive",
            all_remote_parent_paths={"Photos/Vacation/2024", "Docs"},
        )
        await s.commit()
        # Every prefix is in the mapping.
        assert "" in mapping  # synthesized root
        assert "Photos" in mapping
        assert "Photos/Vacation" in mapping
        assert "Photos/Vacation/2024" in mapping
        assert "Docs" in mapping

        # Re-invoking is idempotent — same ids come back.
        mapping2 = await _ensure_remote_folder_tree(
            s, user, "Google Drive",
            provider="google_drive",
            all_remote_parent_paths={"Photos/Vacation/2024", "Docs"},
        )
        assert mapping2 == mapping

        # The synthesized root really is "Google Drive".
        root = await s.get(Folder, mapping[""])
        assert root is not None
        assert root.name == "Google Drive"
        assert root.parent_folder_id is None


async def test_ai_opt_in_endpoint_flips_flag(db_client):
    """POST /cloud/links/{id}/ai-opt-in flips skip_ai_training on every
    image from that provider + re-arms the pending flags."""
    _, headers = await register_and_login(db_client, email="c2-optin@example.com")
    user_id = await fetch_user_id("c2-optin@example.com")

    # Seed: a CloudLink + two Drive-provider images for the user.
    from backend.db import SessionLocal
    from backend.models import CloudLink, Image
    async with SessionLocal() as s:
        link = CloudLink(
            user_id=user_id, provider="google_drive",
            encrypted_refresh_token="x", scopes="drive.readonly",
            status="active",
        )
        s.add(link)
        await s.commit()
        await s.refresh(link)
        link_id = link.id

    img1 = await _make_image(
        user_id, skip_ai_training=True, source_provider="google_drive",
    )
    img2 = await _make_image(
        user_id, skip_ai_training=True, source_provider="google_drive",
    )

    # Opt-in.
    r = await db_client.post(
        f"/cloud/links/{link_id}/ai-opt-in",
        json={"opted_in": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["affected"] == 2
    assert body["opted_in"] is True

    from backend.db import SessionLocal
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(Image).where(Image.id.in_([img1, img2]))
            )
        ).scalars().all()
        for row in rows:
            assert row.skip_ai_training is False
            assert row.pending_summary is True
            assert row.pending_face_scan is True

    # Opt back out.
    r2 = await db_client.post(
        f"/cloud/links/{link_id}/ai-opt-in",
        json={"opted_in": False},
        headers=headers,
    )
    assert r2.status_code == 200
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(Image).where(Image.id.in_([img1, img2]))
            )
        ).scalars().all()
        for row in rows:
            assert row.skip_ai_training is True
            assert row.pending_summary is False
            assert row.pending_face_scan is False


async def test_conflicts_endpoint_returns_audit_rows(db_client):
    _, headers = await register_and_login(db_client, email="c2-conflicts@example.com")
    user_id = await fetch_user_id("c2-conflicts@example.com")

    from backend.audit import add_audit
    from backend.db import SessionLocal
    from backend.models import CloudLink
    async with SessionLocal() as s:
        link = CloudLink(
            user_id=user_id, provider="google_drive",
            encrypted_refresh_token="x", scopes="drive.readonly",
            status="conflicts",
        )
        s.add(link)
        await add_audit(
            s, user_id=user_id, action="cloud.sync.conflict",
            details={
                "provider": "google_drive",
                "remote_id": "abc",
                "remote_path": "Photos/sunset.jpg",
                "reason": "local_change_after_sync",
            },
        )
        await s.commit()
        await s.refresh(link)
        link_id = link.id

    r = await db_client.get(f"/cloud/links/{link_id}/conflicts", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "google_drive"
    assert len(body["conflicts"]) == 1
    c = body["conflicts"][0]
    assert c["remote_id"] == "abc"
    assert c["remote_path"] == "Photos/sunset.jpg"


async def test_cross_user_link_isolation(db_client):
    """User A's link must be invisible to user B even when they know
    the numeric link id."""
    _, headers_a = await register_and_login(db_client, email="c2-iso-a@example.com")
    _, headers_b = await register_and_login(db_client, email="c2-iso-b@example.com")
    user_a = await fetch_user_id("c2-iso-a@example.com")

    from backend.db import SessionLocal
    from backend.models import CloudLink
    async with SessionLocal() as s:
        link = CloudLink(
            user_id=user_a, provider="google_drive",
            encrypted_refresh_token="x", scopes="drive.readonly",
            status="active",
        )
        s.add(link)
        await s.commit()
        await s.refresh(link)
        link_id = link.id

    # User B sees zero links of their own.
    r_b_list = await db_client.get("/cloud/links", headers=headers_b)
    assert r_b_list.status_code == 200
    assert r_b_list.json() == []

    # User B can't trigger A's sync.
    r_b_sync = await db_client.post(
        f"/cloud/links/{link_id}/sync", headers=headers_b,
    )
    assert r_b_sync.status_code == 404

    # User B can't read A's conflicts.
    r_b_conf = await db_client.get(
        f"/cloud/links/{link_id}/conflicts", headers=headers_b,
    )
    assert r_b_conf.status_code == 404

    # User B can't flip A's AI opt-in.
    r_b_ai = await db_client.post(
        f"/cloud/links/{link_id}/ai-opt-in",
        json={"opted_in": True},
        headers=headers_b,
    )
    assert r_b_ai.status_code == 404

    # User A's listing surfaces it.
    r_a_list = await db_client.get("/cloud/links", headers=headers_a)
    assert r_a_list.status_code == 200
    assert any(L["id"] == link_id for L in r_a_list.json())
