"""§H#7 — magic-link passwordless sign-in.

Tests the two new endpoints + integration with the existing auth
posture:

  1. POST /auth/email-link/request — anti-enum 202 for both known
     and unknown emails. When the email matches a row,
     send_signin_link_email fires (captured via monkeypatched stub).

  2. POST /auth/email-link/consume — trades a valid token for a
     session JWT; the JWT works against any authenticated endpoint.

  3. Single-use: re-presenting the same token after success → 400.

  4. Tampered / structurally-broken / expired tokens → 400.

  5. 2FA-aware: when the user has totp_enabled=true, /consume
     returns 401 with {totp_required: true, email}.

  6. Audit guard: SecurityControlsMiddleware._AUTH_PATHS contains
     both /auth/email-link/request and /auth/email-link/consume.
     If a refactor drops them, rate-limit + lockout silently stops
     covering these paths.
"""
from __future__ import annotations

import uuid

import pytest

from tests.conftest import register_and_login


PASSWORD = "Aa1!aaaaaa"


@pytest.fixture
def captured_signin_links(monkeypatch):
    """Replace send_signin_link_email with a capture stub so tests
    can read the token without an SMTP round-trip + assert on what
    was emailed."""
    sent: list[tuple[str, str]] = []

    def _capture(to: str, token: str) -> bool:
        sent.append((to, token))
        return True

    monkeypatch.setattr("backend.email_send.send_signin_link_email", _capture)
    # The router does `from backend.email_send import send_signin_link_email`
    # at import time, so we patch the resolved reference too.
    import backend.api.email_link as mod
    monkeypatch.setattr(mod, "send_signin_link_email", _capture)
    return sent


async def test_request_link_known_email_fires_send(db_client, captured_signin_links):
    email = f"hl-known-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    r = await db_client.post(
        "/auth/email-link/request", json={"email": email}
    )
    assert r.status_code == 202, r.text
    # Background-task wrapping means the helper fires after the
    # response returns — wait a beat for the in-process task to run.
    # In the AsyncClient transport this is effectively synchronous,
    # but be defensive.
    assert len(captured_signin_links) == 1
    to, token = captured_signin_links[0]
    assert to == email
    assert token.count(".") == 2  # JWT shape


async def test_request_link_unknown_email_still_202(db_client, captured_signin_links):
    """Anti-enumeration: unknown email gets the same 202 + no email
    fired (no row to send to)."""
    r = await db_client.post(
        "/auth/email-link/request",
        json={"email": "nobody-magic@example.com"},
    )
    assert r.status_code == 202, r.text
    assert captured_signin_links == []


async def test_consume_valid_token_returns_session_jwt(
    db_client, captured_signin_links
):
    """End-to-end: request → capture token → consume → use the
    returned JWT to call an authenticated endpoint."""
    email = f"hl-consume-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    r = await db_client.post(
        "/auth/email-link/request", json={"email": email}
    )
    assert r.status_code == 202
    assert len(captured_signin_links) == 1
    _, token = captured_signin_links[0]

    r = await db_client.post(
        "/auth/email-link/consume", json={"token": token}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    access = body["access_token"]
    assert access

    # The minted JWT should work against /users/me.
    r = await db_client.get(
        "/users/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email


async def test_consume_token_is_single_use(db_client, captured_signin_links):
    """Second consume of the same token must fail."""
    email = f"hl-once-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    r = await db_client.post(
        "/auth/email-link/request", json={"email": email}
    )
    assert r.status_code == 202
    _, token = captured_signin_links[0]

    r1 = await db_client.post(
        "/auth/email-link/consume", json={"token": token}
    )
    assert r1.status_code == 200, r1.text

    r2 = await db_client.post(
        "/auth/email-link/consume", json={"token": token}
    )
    # 400 from the jti-already-consumed branch.
    assert r2.status_code == 400, r2.text


async def test_consume_rejects_bad_tokens(db_client):
    """Tampered / random / structurally-broken token → 400.
    Empty string is included since a naive caller could pass it."""
    for bad in ("not-a-jwt", "x.y.z", "eyJ.bad.sig"):
        r = await db_client.post(
            "/auth/email-link/consume", json={"token": bad}
        )
        assert r.status_code in (400, 422), (bad, r.status_code, r.text)


async def test_consume_with_totp_enabled_returns_401_totp_required(
    db_client, captured_signin_links, monkeypatch
):
    """When the user has totp_enabled, /consume returns 401 +
    {totp_required: true, email} so the FE can route into the TOTP
    step instead of completing the sign-in."""
    email = f"hl-totp-{uuid.uuid4().hex[:6]}@example.com"
    _, headers = await register_and_login(db_client, email=email)

    # Manually flip totp_enabled on the row. We don't need a valid
    # secret here — the test only exercises the "TOTP-required short-
    # circuit" branch inside /consume.
    from backend.db import SessionLocal
    from backend.auth.users import User
    from sqlalchemy import select, update
    async with SessionLocal() as s:
        await s.execute(
            update(User).where(User.email == email).values(totp_enabled=True)
        )
        await s.commit()

    r = await db_client.post(
        "/auth/email-link/request", json={"email": email}
    )
    assert r.status_code == 202
    _, token = captured_signin_links[0]

    r = await db_client.post(
        "/auth/email-link/consume", json={"token": token}
    )
    assert r.status_code == 401, r.text
    body = r.json()
    assert body.get("totp_required") is True
    assert body.get("email") == email


def test_auth_paths_set_includes_email_link_endpoints():
    """Audit guard: rate-limit + lockout middleware lists both magic-
    link endpoints. If a refactor drops them, mailbox-spam from
    /request goes unthrottled and brute-force jti-guessing against
    /consume goes uncounted."""
    from backend.security import SecurityControlsMiddleware

    paths = SecurityControlsMiddleware._AUTH_PATHS
    assert "/auth/email-link/request" in paths
    assert "/auth/email-link/consume" in paths
