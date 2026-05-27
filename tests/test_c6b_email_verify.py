"""§C6b — email-verification end-to-end.

Backend wiring was already in place from the audit cycle:
  - fastapi-users' verify router is mounted at /auth (POST
    /auth/request-verify-token + POST /auth/verify).
  - UserManager.on_after_register calls request_verify, which
    fires on_after_request_verify → send_verify_email.
  - users.is_verified is exposed on UserRead so the frontend can
    decide whether to show the "Confirm your email" banner.
  - SecurityControlsMiddleware lists /auth/verify and
    /auth/request-verify-token in _AUTH_PATHS for lockout.

What was missing was the frontend landing page (/verify?token=…)
and the per-session banner — both shipped in this PR. These tests
pin the backend half end-to-end so a future refactor of the auth
hook chain can't silently break either piece.

What we test here:

  1. Signing up fires send_verify_email exactly once with the
     new account's email + a non-empty token.

  2. POST /auth/verify with that token flips users.is_verified
     to true (visible on GET /users/me).

  3. POST /auth/request-verify-token responds 2xx for both
     known and unknown emails (anti-enumeration — same shape
     as /auth/forgot-password).

  4. A second use of the same verify token after success is
     rejected (single-use — fastapi-users invalidates the
     token once is_verified flips).

  5. Tampered / structurally-broken tokens 4xx.

  6. The rate-limit middleware's _AUTH_PATHS set includes
     both /auth/verify and /auth/request-verify-token. Audit
     guard against a refactor silently dropping them.

  7. UserRead exposes the `is_verified` field. The frontend
     banner keys off this — if it stops being exposed the
     banner would render forever for everyone.
"""
from __future__ import annotations

import uuid

import pytest

from tests.conftest import register_and_login


PASSWORD = "Aa1!aaaaaa"


@pytest.fixture
def captured_verify_emails(monkeypatch):
    """Replace the send_verify_email helper with a capture stub
    so tests can read the token without an SMTP round-trip.

    Same pattern as captured_reset_emails in test_c6a_forgot_password
    — we have to patch both the source module AND the resolved
    reference inside backend.auth.users (which did `from
    backend.email_send import send_verify_email` at import time).
    """
    sent: list[tuple[str, str]] = []

    def _capture(to: str, token: str) -> bool:
        sent.append((to, token))
        return True

    monkeypatch.setattr("backend.email_send.send_verify_email", _capture)
    import backend.auth.users as users_mod
    monkeypatch.setattr(users_mod, "send_verify_email", _capture)
    return sent


async def test_register_fires_send_verify_email_once(db_client, captured_verify_emails):
    """Signup → on_after_register → request_verify →
    send_verify_email. The hook should fire exactly once with
    the new account's address and a non-empty JWT-shaped token."""
    email = f"c6b-reg-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    assert len(captured_verify_emails) == 1
    sent_to, token = captured_verify_emails[0]
    assert sent_to == email
    assert token  # non-empty
    assert token.count(".") == 2  # JWT shape: header.payload.sig


async def test_verify_endpoint_flips_is_verified_true(db_client, captured_verify_emails):
    """End-to-end: register → capture token → POST /auth/verify →
    GET /users/me shows is_verified=true.

    A newly registered user starts with is_verified=False (fastapi-
    users default). Consuming the verify token via /auth/verify
    flips it. The frontend banner reads is_verified on /users/me
    and disappears once this round-trip completes.
    """
    email = f"c6b-flip-{uuid.uuid4().hex[:6]}@example.com"
    _, headers = await register_and_login(db_client, email=email)

    # Confirm the fresh row is unverified.
    r = await db_client.get("/users/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json().get("is_verified") is False

    # Capture token and consume it.
    assert len(captured_verify_emails) == 1
    _, token = captured_verify_emails[0]
    r = await db_client.post("/auth/verify", json={"token": token})
    assert r.status_code == 200, r.text

    # Row should now read is_verified=true.
    r = await db_client.get("/users/me", headers=headers)
    assert r.status_code == 200
    assert r.json().get("is_verified") is True


async def test_request_verify_token_2xx_for_known_email(
    db_client, captured_verify_emails
):
    """POST /auth/request-verify-token for a real account → 202.
    A second send-email fires."""
    email = f"c6b-req-known-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    # Signup itself already fired one.
    assert len(captured_verify_emails) == 1
    captured_verify_emails.clear()

    r = await db_client.post(
        "/auth/request-verify-token", json={"email": email}
    )
    assert r.status_code in (200, 202), r.text
    assert len(captured_verify_emails) == 1
    assert captured_verify_emails[0][0] == email


async def test_request_verify_token_2xx_for_unknown_email(
    db_client, captured_verify_emails
):
    """Unknown account → same 202. Anti-enumeration: an attacker
    can't probe /auth/request-verify-token to learn which
    addresses have neuthek accounts."""
    r = await db_client.post(
        "/auth/request-verify-token",
        json={"email": "nobody-c6b@example.com"},
    )
    assert r.status_code in (200, 202), r.text
    # No email fired (no row to send TO).
    assert captured_verify_emails == []


async def test_verify_token_is_single_use(db_client, captured_verify_emails):
    """Once is_verified flips, the same token shouldn't work
    again. fastapi-users encodes is_verified into the JWT
    payload, so re-presenting the same token after the row
    flips fails the equality check."""
    email = f"c6b-single-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)
    assert len(captured_verify_emails) == 1
    _, token = captured_verify_emails[0]

    # First use — success.
    r = await db_client.post("/auth/verify", json={"token": token})
    assert r.status_code == 200, r.text

    # Second use — fastapi-users raises VERIFY_USER_BAD_TOKEN or
    # VERIFY_USER_ALREADY_VERIFIED (both 400).
    r2 = await db_client.post("/auth/verify", json={"token": token})
    assert r2.status_code in (400, 422), r2.text


async def test_verify_with_bad_token_4xx(db_client):
    """Tampered / random / structurally-broken token → 4xx.
    Covers the three shapes we've actually seen in the wild:
    non-JWT garbage, a syntactically valid but unsigned JWT-
    looking string, and the empty string (which httpx routes
    cleanly through but should still 4xx, not 500)."""
    for bad in ("not-a-jwt", "x.y.z", ""):
        r = await db_client.post("/auth/verify", json={"token": bad})
        assert r.status_code in (400, 422), (bad, r.status_code, r.text)


def test_auth_paths_set_includes_verify_endpoints():
    """Audit guard: the rate-limit middleware must list both
    verify endpoints. If a future refactor drops them, brute-
    force lockout silently stops covering /auth/verify
    (which is the exact path an attacker would target with
    a leaked-token list)."""
    from backend.security import SecurityControlsMiddleware

    auth_paths = SecurityControlsMiddleware._AUTH_PATHS
    assert "/auth/verify" in auth_paths
    assert "/auth/request-verify-token" in auth_paths


def test_user_read_exposes_is_verified():
    """Frontend invariant: the VerifyEmailBanner keys off
    user.is_verified on /users/me. If a future schema cleanup
    drops the field from UserRead, the banner would render
    forever for every signed-in user. Pin it."""
    from backend.schemas import UserRead

    fields = set(UserRead.model_fields.keys())
    assert "is_verified" in fields, fields
