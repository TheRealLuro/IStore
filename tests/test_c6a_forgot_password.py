"""§C6a — forgot-password + reset-password end-to-end.

The backend scaffolding was already in place from the audit cycle:
fastapi-users' reset router is mounted at /auth, the UserManager
on_after_forgot_password hook calls send_reset_email, the email
helper writes to SMTP/console, and the rate-limit middleware lists
/auth/forgot-password and /auth/reset-password in its _AUTH_PATHS
set. What was missing was a frontend reset page and the wiring of
the "Forgot password?" link.

These tests prove the BACKEND piece works end-to-end. The FE work
(reset-password.jsx, the modal in auth.jsx) is exercised manually
via the dev preview.

What we test here:

  1. POST /auth/forgot-password responds 202 (or 200 — fastapi-
     users' default is 202) whether or not the email is on file.
     This is the anti-enumeration property.

  2. The hook fires send_reset_email exactly once with the right
     user's email + a token. We monkeypatch the helper to capture
     the call.

  3. The token from step 2 successfully resets the password via
     POST /auth/reset-password.

  4. The user can sign in with the new password.

  5. The old password no longer works (defense in depth — this
     should be a side-effect of the password hash change in step 3
     but worth pinning).

  6. A second use of the same reset token fails (single-use).

  7. A reset attempt with a weak password 4xxs and the password is
     not changed.

  8. Rate-limit: the forgot-password path is in _AUTH_PATHS so
     hammering it from one IP eventually 429s. We can't easily
     test the 429 itself in the integration suite (Redis state
     would need to be primed) but we CAN confirm the path is in
     the set so a future audit doesn't silently drop it.
"""
from __future__ import annotations

import uuid

import pytest

from tests.conftest import register_and_login


PASSWORD = "Aa1!aaaaaa"
NEW_PASSWORD = "Zz9$zzzzzz"


@pytest.fixture
def captured_reset_emails(monkeypatch):
    """Replace the send_reset_email helper with a capture stub so
    tests can read the token without an SMTP round-trip."""
    sent: list[tuple[str, str]] = []

    def _capture(to: str, token: str) -> bool:
        sent.append((to, token))
        return True

    monkeypatch.setattr("backend.email_send.send_reset_email", _capture)
    # Also patch the import inside the UserManager — the hook does
    # `from backend.email_send import send_reset_email` at module
    # load time, so we have to swap the resolved reference too.
    import backend.auth.users as users_mod
    monkeypatch.setattr(users_mod, "send_reset_email", _capture)
    return sent


async def test_forgot_password_returns_2xx_for_known_email(db_client, captured_reset_emails):
    """Real account → 202 (anti-enumeration shape)."""
    email = f"c6a-known-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    r = await db_client.post("/auth/forgot-password", json={"email": email})
    assert r.status_code in (200, 202), r.text

    # Hook fired exactly once with this address.
    assert len(captured_reset_emails) == 1
    assert captured_reset_emails[0][0] == email
    assert captured_reset_emails[0][1]  # token is a non-empty string


async def test_forgot_password_returns_2xx_for_unknown_email(db_client, captured_reset_emails):
    """Unknown account → same 202. This is the anti-enumeration
    guarantee — an attacker can't probe /auth/forgot-password to
    learn which addresses have neuthek accounts."""
    r = await db_client.post(
        "/auth/forgot-password",
        json={"email": "nobody@example.com"},
    )
    assert r.status_code in (200, 202), r.text
    # No email was sent (no row to send TO).
    assert captured_reset_emails == []


async def test_reset_password_with_valid_token_works(db_client, captured_reset_emails):
    """End-to-end: forgot → capture token → reset → log in with
    new password → old password rejected."""
    email = f"c6a-reset-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    # 1. Trigger reset.
    r = await db_client.post("/auth/forgot-password", json={"email": email})
    assert r.status_code in (200, 202)
    assert len(captured_reset_emails) == 1
    _, token = captured_reset_emails[0]

    # 2. Consume the token to set a new password.
    r = await db_client.post(
        "/auth/reset-password",
        json={"token": token, "password": NEW_PASSWORD},
    )
    assert r.status_code == 200, r.text

    # 3. Sign in with the new password works.
    r = await db_client.post(
        "/auth/jwt/login",
        data={"username": email, "password": NEW_PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, r.text

    # 4. Old password is dead.
    r = await db_client.post(
        "/auth/jwt/login",
        data={"username": email, "password": PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code in (400, 401), r.text


async def test_reset_password_token_is_single_use(db_client, captured_reset_emails):
    """A token consumed once shouldn't work a second time. fastapi-
    users marks the user's hashed_password as the source of truth
    for the JWT signature, so once the password changes the token
    becomes invalid for the same row."""
    email = f"c6a-single-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    r = await db_client.post("/auth/forgot-password", json={"email": email})
    assert r.status_code in (200, 202)
    _, token = captured_reset_emails[0]

    # First use — success.
    r = await db_client.post(
        "/auth/reset-password",
        json={"token": token, "password": NEW_PASSWORD},
    )
    assert r.status_code == 200, r.text

    # Second use — failure (token signature doesn't match the new
    # hashed_password).
    r2 = await db_client.post(
        "/auth/reset-password",
        json={"token": token, "password": "OtherZz1!"},
    )
    assert r2.status_code in (400, 422), r2.text


async def test_reset_password_with_bad_token_fails(db_client):
    """Tampered / random / structurally-broken token → 400."""
    for bad in ("not-a-jwt", "x.y.z", ""):
        r = await db_client.post(
            "/auth/reset-password",
            json={"token": bad, "password": NEW_PASSWORD},
        )
        assert r.status_code in (400, 422), (bad, r.status_code, r.text)


async def test_reset_password_rejects_weak_new_password(db_client, captured_reset_emails):
    """fastapi-users InvalidPasswordException (our validator
    rejects <8 chars / no upper / no digit / no symbol) surfaces
    as a 4xx. The password on the row is NOT changed."""
    email = f"c6a-weak-{uuid.uuid4().hex[:6]}@example.com"
    await register_and_login(db_client, email=email)

    r = await db_client.post("/auth/forgot-password", json={"email": email})
    assert r.status_code in (200, 202)
    _, token = captured_reset_emails[0]

    r = await db_client.post(
        "/auth/reset-password",
        json={"token": token, "password": "weak"},
    )
    assert r.status_code in (400, 422), r.text

    # Confirm the old password still works (reset did NOT take).
    r = await db_client.post(
        "/auth/jwt/login",
        data={"username": email, "password": PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, r.text


def test_auth_paths_set_includes_forgot_and_reset():
    """Audit guard: the rate-limit middleware's _AUTH_PATHS set
    must list both endpoints. If a future refactor drops them,
    brute-force lockout silently stops covering reset-password
    (which is the exact path an attacker would target with a
    leaked-token list)."""
    from backend.security import SecurityControlsMiddleware

    auth_paths = SecurityControlsMiddleware._AUTH_PATHS
    assert "/auth/forgot-password" in auth_paths
    assert "/auth/reset-password" in auth_paths
