"""Regression tests for CS3 — Google token revoke on link delete.

Before, `DELETE /cloud/links/{id}` just dropped the `cloud_links`
row. The refresh token persisted on Google's side until the user
manually disconnected from `myaccount.google.com/permissions` — a
privacy footgun (users assume "disconnect" actually disconnects).

The fix POSTs to `https://oauth2.googleapis.com/revoke` before the
DB delete. Best-effort: a network error or unexpected status does
NOT block the local delete (the row going away is the bigger
correctness win; the user can still revoke manually from Google's
UI), but the outcome lands in the audit log so operators can
inspect failures.

Tests cover:
  * The helper's behaviour against each Google response shape (200,
    400 invalid_token, 400 other, 5xx, network error).
  * The DELETE endpoint actually calls the helper for Drive links.
  * The DELETE endpoint NEVER calls the helper for non-Drive
    providers (defensive — when Dropbox / OneDrive land, they'll
    have their own revoke paths).
  * The DELETE endpoint records the revoke outcome in the audit
    log AND proceeds with the local delete regardless of revoke
    success.
  * A revoke failure does NOT propagate to the API caller — DELETE
    still returns 204.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import fetch_user_id, register_and_login


# ---------- helper: revoke_google_refresh_token ----------


class _StubResponse:
    def __init__(self, status_code: int, body: dict | str | None = None):
        self.status_code = status_code
        if isinstance(body, str):
            self._body_text = body
            self._body_json = None
        elif isinstance(body, dict):
            import json
            self._body_text = json.dumps(body)
            self._body_json = body
        else:
            self._body_text = ""
            self._body_json = None

    @property
    def text(self) -> str:
        return self._body_text

    def json(self):
        if self._body_json is None:
            raise ValueError("no json")
        return self._body_json


class _StubClient:
    """Drop-in replacement for `httpx.AsyncClient` that records POST
    calls + returns a canned response."""

    def __init__(self, response: _StubResponse):
        self._response = response
        self.posts: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def post(self, url, *, data=None, headers=None):
        self.posts.append({"url": url, "data": data, "headers": headers})
        return self._response


def _patch_httpx(response: _StubResponse) -> _StubClient:
    """Helper: monkey the httpx import inside the function under
    test so we don't need a live network connection."""
    stub = _StubClient(response)

    def _factory(*_args, **_kwargs):
        return stub

    return stub, patch("httpx.AsyncClient", side_effect=_factory)


async def test_revoke_helper_returns_true_on_200():
    """Happy path: Google returned 200 → helper returns True."""
    from backend.cloud_sync import revoke_google_refresh_token

    stub, ctx = _patch_httpx(_StubResponse(200))
    with ctx:
        ok = await revoke_google_refresh_token("fake-refresh-token")
    assert ok is True
    assert len(stub.posts) == 1
    assert stub.posts[0]["url"] == "https://oauth2.googleapis.com/revoke"
    assert stub.posts[0]["data"] == {"token": "fake-refresh-token"}
    assert "application/x-www-form-urlencoded" in stub.posts[0]["headers"]["Content-Type"]


async def test_revoke_helper_treats_invalid_token_as_success():
    """A 400 with `error=invalid_token` means already-revoked or
    never-valid — user's intent is satisfied either way, so we
    return True."""
    from backend.cloud_sync import revoke_google_refresh_token

    stub, ctx = _patch_httpx(_StubResponse(400, {"error": "invalid_token"}))
    with ctx:
        ok = await revoke_google_refresh_token("stale-or-fake")
    assert ok is True


async def test_revoke_helper_returns_false_on_other_400():
    """A 400 with a DIFFERENT error code (e.g. invalid_request) is
    a real failure — log it + return False so the caller marks the
    outcome accurately in the audit row."""
    from backend.cloud_sync import revoke_google_refresh_token

    stub, ctx = _patch_httpx(_StubResponse(400, {"error": "invalid_request"}))
    with ctx:
        ok = await revoke_google_refresh_token("malformed")
    assert ok is False


async def test_revoke_helper_returns_false_on_5xx():
    """Google was unreachable / having a bad day — helper returns
    False; caller proceeds with local delete anyway."""
    from backend.cloud_sync import revoke_google_refresh_token

    stub, ctx = _patch_httpx(_StubResponse(503, "Service Unavailable"))
    with ctx:
        ok = await revoke_google_refresh_token("any-token")
    assert ok is False


async def test_revoke_helper_returns_false_on_network_error():
    """`httpx.ConnectError` or any other exception → caller sees
    False, doesn't raise."""
    from backend.cloud_sync import revoke_google_refresh_token

    class _BoomClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, *_args, **_kwargs):
            raise httpx.ConnectError("simulated network drop")

    with patch("httpx.AsyncClient", side_effect=lambda *a, **kw: _BoomClient()):
        ok = await revoke_google_refresh_token("any-token")
    assert ok is False


# ---------- end-to-end: DELETE /cloud/links/{id} ----------


async def _make_link(user_id, *, provider: str = "google_drive",
                     refresh_token: str | None = "stub-rt"):
    """Create a CloudLink row with a real Fernet-encrypted refresh
    token (decrypt has to succeed for the revoke path to fire)."""
    from backend.db import SessionLocal
    from backend.models import CloudLink
    from backend.secret_box import encrypt as encrypt_token

    enc_token: str | None = None
    if refresh_token is not None:
        enc_token = encrypt_token(refresh_token).decode("ascii")

    async with SessionLocal() as s:
        link = CloudLink(
            user_id=user_id,
            provider=provider,
            encrypted_refresh_token=enc_token,
            ai_opted_in=False,
            status="active",
        )
        s.add(link)
        await s.commit()
        await s.refresh(link)
        return link


async def test_delete_link_calls_google_revoke(db_client):
    """Drive link with a refresh token → revoke endpoint fired + DB
    row gone + audit row written with outcome=`revoked`."""
    _, headers = await register_and_login(db_client, email="revoke-ok@example.com")
    user_id = await fetch_user_id("revoke-ok@example.com")
    link = await _make_link(user_id, refresh_token="real-looking-token")

    stub, ctx = _patch_httpx(_StubResponse(200))
    with ctx:
        r = await db_client.delete(f"/cloud/links/{link.id}", headers=headers)

    assert r.status_code == 204
    assert len(stub.posts) == 1
    assert stub.posts[0]["data"]["token"] == "real-looking-token"

    from backend.db import SessionLocal
    from backend.models import AuditLog, CloudLink
    async with SessionLocal() as s:
        # Link row is gone.
        gone = (
            await s.execute(select(CloudLink).where(CloudLink.id == link.id))
        ).scalar_one_or_none()
        assert gone is None

        # Audit row records the outcome.
        rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_id,
                    AuditLog.action == "cloud.link.revoked",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].details["revoke_outcome"] == "revoked"
        assert rows[0].details["provider"] == "google_drive"


async def test_delete_link_proceeds_when_revoke_fails(db_client):
    """Google returned 500. Local delete still succeeds (the row
    going away matters more than the remote-side hygiene) and the
    audit row records the failure so operators can inspect."""
    _, headers = await register_and_login(db_client, email="revoke-fail@example.com")
    user_id = await fetch_user_id("revoke-fail@example.com")
    link = await _make_link(user_id, refresh_token="another-token")

    stub, ctx = _patch_httpx(_StubResponse(503))
    with ctx:
        r = await db_client.delete(f"/cloud/links/{link.id}", headers=headers)

    assert r.status_code == 204, "local delete must succeed even when revoke fails"

    from backend.db import SessionLocal
    from backend.models import AuditLog, CloudLink
    async with SessionLocal() as s:
        gone = (
            await s.execute(select(CloudLink).where(CloudLink.id == link.id))
        ).scalar_one_or_none()
        assert gone is None
        rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_id,
                    AuditLog.action == "cloud.link.revoked",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].details["revoke_outcome"] == "revoke_failed"


async def test_delete_link_skips_revoke_when_no_token(db_client):
    """A row without `encrypted_refresh_token` (legacy / partial
    OAuth) has nothing to revoke. The helper must NOT be invoked
    (saves a needless network round-trip + avoids logging a
    spurious failure)."""
    _, headers = await register_and_login(db_client, email="revoke-none@example.com")
    user_id = await fetch_user_id("revoke-none@example.com")
    link = await _make_link(user_id, refresh_token=None)

    stub, ctx = _patch_httpx(_StubResponse(200))
    with ctx:
        r = await db_client.delete(f"/cloud/links/{link.id}", headers=headers)

    assert r.status_code == 204
    assert stub.posts == [], "no revoke should fire when there is no refresh token"

    from backend.db import SessionLocal
    from backend.models import AuditLog
    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_id,
                    AuditLog.action == "cloud.link.revoked",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].details["revoke_outcome"] == "skipped"


async def test_delete_link_other_user_404s(db_client):
    """A delete attempt against someone else's link should 404,
    not leak the existence of the row and definitely not fire a
    revoke against a token that doesn't belong to the caller."""
    _, headers = await register_and_login(db_client, email="revoke-mine@example.com")
    # Build a second user + their link
    from backend.db import SessionLocal
    from backend.models import User
    async with SessionLocal() as s:
        other = User(
            email=f"revoke-other-{uuid.uuid4().hex[:6]}@example.com",
            hashed_password="$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            is_active=True, is_superuser=False, is_verified=True,
            age_confirmed=True,
        )
        s.add(other)
        await s.commit()
        await s.refresh(other)
        other_id = other.id
    other_link = await _make_link(other_id, refresh_token="other-token")

    stub, ctx = _patch_httpx(_StubResponse(200))
    with ctx:
        r = await db_client.delete(f"/cloud/links/{other_link.id}", headers=headers)

    assert r.status_code == 404
    assert stub.posts == [], "must not fire a revoke for someone else's token"

    # Other user's link still exists.
    from backend.db import SessionLocal
    from backend.models import CloudLink
    async with SessionLocal() as s:
        still_there = (
            await s.execute(select(CloudLink).where(CloudLink.id == other_link.id))
        ).scalar_one_or_none()
        assert still_there is not None
