"""Sprint D acceptance: per-image share grants (todo §1.1 / G1).

Covers the 9 cases in the plan:

  1. alice -> bob (existing user) at 7d: incoming list shows it.
  2. alice -> charlie@ (no account): grant is `pending`.
  3. charlie signs up + claims: 1-day cap applies regardless of sharer's duration.
  4. dave (random) tries to claim a leaked token: 404.
  5. alice revokes: bob can't fetch the asset URL anymore.
  6. bob tries to claim a token meant for charlie: 404.
  7. claim 11x from same IP within 60s: 429 (with rate limits enabled).
  8. share for an image alice doesn't own: 404.
  9. re-sharing same image to bob: prior row revoked, audit `share.replaced`.

Each test uses the standard `db_client` fixture (Postgres, MinIO stub),
which truncates `share_grants` between tests. Rate-limit-enabled tests
flip the setting via monkeypatch; SECURITY_RATE_LIMITS_ENABLED is off
by default in conftest.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.conftest import fetch_user_id, register_and_login


async def _create_image(user_id: uuid.UUID, *, filename: str = "sunset.jpg") -> uuid.UUID:
    """Insert a minimal Image row via the ORM. No real bytes — the
    blob keys reference whatever the MinIO stub holds (empty if we
    don't pre-seed). Tests that fetch bytes seed the stub explicitly."""
    from backend.db import SessionLocal
    from backend.models import Image

    async with SessionLocal() as session:
        img = Image(
            user_id=user_id,
            category="image",
            served_blob_key=f"users/{user_id}/served/{uuid.uuid4().hex}.webp",
            original_blob_key=f"users/{user_id}/originals/{uuid.uuid4().hex}",
            original_filename=filename,
            mime_type_served="image/webp",
            mime_type_original="image/jpeg",
            byte_size_served=1234,
            byte_size_original=5678,
            pending_face_scan=False,
            pending_summary=False,
        )
        session.add(img)
        await session.commit()
        await session.refresh(img)
        return img.id


def _share_token_from_url(share_url: str) -> str:
    return share_url.rsplit("/", 1)[-1]


# ---------- Case 1 ----------


async def test_existing_user_share_appears_in_incoming(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    _, bob_h = await register_and_login(db_client, email="bob@example.com")

    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id, filename="alpine.jpg")

    seven_days = 7 * 86400
    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "bob@example.com", "duration_seconds": seven_days},
        headers=alice_h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["share_url"].startswith("http")
    assert body["recipient_user_id"] is not None  # bob exists -> bound at create
    assert body["expires_at"] is not None  # set at create for existing users

    # Incoming list for bob shows the grant.
    r = await db_client.get("/shares/incoming", headers=bob_h)
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    assert items[0]["image_id"] == str(image_id)
    assert items[0]["sharer_email"] == "alice@example.com"
    # Expiry is roughly now + 7d (allow 1-min slack for execution time).
    when = datetime.fromisoformat(items[0]["expires_at"].replace("Z", "+00:00"))
    delta = when - datetime.now(timezone.utc)
    assert timedelta(days=7) - timedelta(minutes=2) <= delta <= timedelta(days=7) + timedelta(minutes=2)


# ---------- Case 2 ----------


async def test_new_user_share_starts_pending(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)

    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={
            "recipient_email": "charlie@example.com",  # no account yet
            "duration_seconds": 30 * 86400,
        },
        headers=alice_h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["recipient_user_id"] is None
    assert body["expires_at"] is None  # pending — no clock yet
    assert body["share_url"].startswith("http")


# ---------- Case 3 ----------


async def test_new_user_claim_caps_to_one_day(db_client):
    """Sharer asks for 30 days; charlie is new; claim window is exactly 1 day."""
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)

    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "charlie@example.com", "duration_seconds": 30 * 86400},
        headers=alice_h,
    )
    share_url = r.json()["share_url"]
    token = _share_token_from_url(share_url)

    _, charlie_h = await register_and_login(db_client, email="charlie@example.com")

    r = await db_client.post(
        "/shares/claim", json={"token": token}, headers=charlie_h
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["was_pending"] is True
    assert body["image_id"] == str(image_id)
    when = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    delta = when - datetime.now(timezone.utc)
    # Should be ~1 day, not 30.
    assert timedelta(hours=23, minutes=58) <= delta <= timedelta(hours=24, minutes=2)


# ---------- Case 4 ----------


async def test_random_user_cannot_claim_leaked_token(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)

    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "bob@example.com", "duration_seconds": 86400},
        headers=alice_h,
    )
    token = _share_token_from_url(r.json()["share_url"])

    # Dave registers (random attacker) and tries the token he saw on the wire.
    _, dave_h = await register_and_login(db_client, email="dave@example.com")
    r = await db_client.post(
        "/shares/claim", json={"token": token}, headers=dave_h
    )
    assert r.status_code == 404


# ---------- Case 5 ----------


async def test_revoke_blocks_recipient_asset_url(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    _, bob_h = await register_and_login(db_client, email="bob@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)

    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "bob@example.com", "duration_seconds": 86400},
        headers=alice_h,
    )
    create_body = r.json()
    share_id = create_body["id"]

    # Bob can pull a signed asset URL while the grant is live.
    r = await db_client.get(f"/shares/{share_id}/asset", headers=bob_h)
    assert r.status_code == 200

    # Alice revokes.
    r = await db_client.delete(
        f"/images/{image_id}/shares/{share_id}", headers=alice_h
    )
    assert r.status_code == 204

    # Bob can no longer get a signed URL.
    r = await db_client.get(f"/shares/{share_id}/asset", headers=bob_h)
    assert r.status_code == 404


# ---------- Case 6 ----------


async def test_token_pinned_to_recipient_email(db_client):
    """Bob can't claim a token alice issued for charlie@."""
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    _, bob_h = await register_and_login(db_client, email="bob@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)

    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "charlie@example.com", "duration_seconds": 86400},
        headers=alice_h,
    )
    token = _share_token_from_url(r.json()["share_url"])

    r = await db_client.post(
        "/shares/claim", json={"token": token}, headers=bob_h
    )
    assert r.status_code == 404


# ---------- Case 7 ----------


async def test_claim_rate_limit_per_ip(db_client, monkeypatch):
    """11th claim attempt within 60 s from one IP -> 429."""
    from backend import security as security_mod
    from backend.config import settings

    monkeypatch.setattr(settings, "security_rate_limits_enabled", True)

    # Pre-seed the per-IP claim counter so the next attempt trips the
    # rate limit immediately. Avoids spamming 11 real requests.
    await security_mod.increment_window(
        key="share:claim:127.0.0.1", window_seconds=60, amount=10
    )

    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    _, bob_h = await register_and_login(db_client, email="bob@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)
    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "bob@example.com", "duration_seconds": 86400},
        headers=alice_h,
    )
    token = _share_token_from_url(r.json()["share_url"])

    r = await db_client.post(
        "/shares/claim", json={"token": token}, headers=bob_h
    )
    assert r.status_code == 429

    # Cleanup so other tests aren't poisoned by the pre-seeded counter.
    await security_mod.clear_counter("share:claim:127.0.0.1")


# ---------- Case 8 ----------


async def test_cannot_share_image_owned_by_another_user(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    _, bob_h = await register_and_login(db_client, email="bob@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)

    # Bob tries to share alice's image to charlie -> 404.
    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "charlie@example.com", "duration_seconds": 86400},
        headers=bob_h,
    )
    assert r.status_code == 404


# ---------- Case 9 ----------


async def test_re_sharing_supersedes_and_audits_replacement(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    await register_and_login(db_client, email="bob@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)

    r1 = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "bob@example.com", "duration_seconds": 3600},
        headers=alice_h,
    )
    assert r1.status_code == 201
    first_id = uuid.UUID(r1.json()["id"])

    r2 = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "bob@example.com", "duration_seconds": 7200},
        headers=alice_h,
    )
    assert r2.status_code == 201
    second_id = uuid.UUID(r2.json()["id"])
    assert first_id != second_id

    # The owner's listing only shows the live grant (the second one).
    r = await db_client.get(f"/images/{image_id}/shares", headers=alice_h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == str(second_id)

    # Database confirms the first row was soft-revoked and the audit
    # log carries the `share.replaced` event.
    from backend.db import SessionLocal
    from backend.models import AuditLog, ShareGrant

    async with SessionLocal() as session:
        first = (
            await session.execute(
                select(ShareGrant).where(ShareGrant.id == first_id)
            )
        ).scalar_one()
        assert first.revoked_at is not None

        replaced = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "share.replaced")
            )
        ).scalars().all()
        assert any(
            row.details.get("share_id") == str(first_id) for row in replaced
        )


# ---------- Bonus coverage ----------


async def test_self_share_rejected(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)
    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "alice@example.com", "duration_seconds": 86400},
        headers=alice_h,
    )
    assert r.status_code == 400


async def test_owner_list_does_not_leak_token(db_client):
    """share_url is plaintext token in the URL — must NOT come back on list."""
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    await register_and_login(db_client, email="bob@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)
    await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "bob@example.com", "duration_seconds": 86400},
        headers=alice_h,
    )
    r = await db_client.get(f"/images/{image_id}/shares", headers=alice_h)
    assert r.status_code == 200
    for row in r.json():
        assert row.get("share_url") is None


async def test_unauth_preview_returns_minimal_shape(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id, filename="memo.pdf")
    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "newbie@example.com", "duration_seconds": 86400},
        headers=alice_h,
    )
    token = _share_token_from_url(r.json()["share_url"])

    # No auth header — public endpoint.
    r = await db_client.get(
        f"/shares/preview/{token}",
        params={"email": "newbie@example.com"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["image_filename"] == "memo.pdf"
    assert body["requires_signup"] is True
    # No bytes / no thumbnail / no GPS / no size — just minimal landing copy.
    assert set(body.keys()) == {
        "sharer_display_name", "image_filename", "image_category", "requires_signup"
    }


async def test_unauth_preview_wrong_email_returns_404(db_client):
    _, alice_h = await register_and_login(db_client, email="alice@example.com")
    alice_id = await fetch_user_id("alice@example.com")
    image_id = await _create_image(alice_id)
    r = await db_client.post(
        f"/images/{image_id}/shares",
        json={"recipient_email": "newbie@example.com", "duration_seconds": 86400},
        headers=alice_h,
    )
    token = _share_token_from_url(r.json()["share_url"])

    r = await db_client.get(
        f"/shares/preview/{token}",
        params={"email": "wrong@example.com"},  # email pinning enforced
    )
    assert r.status_code == 404
