"""§C4.3 — email change forces re-verification.

When a verified user changes their email via PATCH /users/me, two
things must happen server-side, in order, atomically:

  1. `users.is_verified` flips back to false. fastapi-users does
     this inside its default update path — losing it would let an
     attacker who briefly takes over a session change the email to
     one they control while keeping the verified badge.

  2. `on_after_update` fires `request_verify(user)`, which goes
     through `on_after_request_verify` → `send_verify_email` to
     deliver a verification link to the NEW address.

The frontend half is the existing C6b VerifyEmailBanner — it
already keys off `user.is_verified`, so a successful email change
makes the banner reappear automatically. The toast wording on
the Settings → Account save button additionally tells the user
to check their new inbox, but that's a copy nicety, not a
correctness invariant.

What this file pins:
  - Email change → is_verified=false on /users/me.
  - Email change → send_verify_email fires exactly once with the
    NEW address (not the old one).
  - Display-name-only update does NOT fire send_verify_email and
    does NOT clear is_verified (because the value would be
    sticky-true from the original verify).
  - Verifying the new email via the freshly captured token flips
    is_verified back to true — round-trip works on the new row.
"""
from __future__ import annotations

import uuid

import pytest

from tests.conftest import register_and_login


PASSWORD = "Aa1!aaaaaa"


@pytest.fixture
def captured_verify_emails(monkeypatch):
    """Same capture stub as test_c6b_email_verify."""
    sent: list[tuple[str, str]] = []

    def _capture(to: str, token: str) -> bool:
        sent.append((to, token))
        return True

    monkeypatch.setattr("backend.email_send.send_verify_email", _capture)
    import backend.auth.users as users_mod
    monkeypatch.setattr(users_mod, "send_verify_email", _capture)
    return sent


async def _verify_via_token(client, token: str) -> None:
    """Helper: consume a verify token. Used to flip the
    is_verified flag back to true so we can test the email-
    change clearing it from a known-good starting state."""
    r = await client.post("/auth/verify", json={"token": token})
    assert r.status_code == 200, r.text


async def test_email_change_clears_is_verified_and_sends_to_new_address(
    db_client, captured_verify_emails
):
    """Verified user → PATCH email → is_verified flips false and
    send_verify_email fires with the new address."""
    old_email = f"c43-old-{uuid.uuid4().hex[:6]}@example.com"
    new_email = f"c43-new-{uuid.uuid4().hex[:6]}@example.com"

    # Register (signup auto-fires send_verify_email once).
    _, headers = await register_and_login(db_client, email=old_email)
    assert len(captured_verify_emails) == 1
    _, signup_token = captured_verify_emails[0]

    # Verify the signup-time token so is_verified=true (clean
    # starting state for the "change clears it" assertion).
    await _verify_via_token(db_client, signup_token)
    r = await db_client.get("/users/me", headers=headers)
    assert r.json()["is_verified"] is True

    captured_verify_emails.clear()

    # Now change the email.
    r = await db_client.patch(
        "/users/me", json={"email": new_email}, headers=headers
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["email"] == new_email
    assert updated["is_verified"] is False, (
        "fastapi-users must clear is_verified on email change — "
        "otherwise a session takeover could pivot to an attacker-"
        "controlled email while keeping the verified badge."
    )

    # Exactly one fresh verify email fired, and it went to the
    # NEW address (not the old one).
    assert len(captured_verify_emails) == 1
    to_addr, new_token = captured_verify_emails[0]
    assert to_addr == new_email, (to_addr, new_email)
    assert new_token  # non-empty JWT

    # The new token should successfully re-verify the row.
    await _verify_via_token(db_client, new_token)
    r = await db_client.get("/users/me", headers=headers)
    assert r.json()["is_verified"] is True


async def test_display_name_only_update_keeps_is_verified_and_skips_email(
    db_client, captured_verify_emails
):
    """Renaming display_name (no email change) must NOT clear
    is_verified and must NOT fire a new verify email. If a future
    refactor accidentally treats every PATCH as an email change,
    every profile edit would log out / re-nag the user."""
    email = f"c43-rename-{uuid.uuid4().hex[:6]}@example.com"
    _, headers = await register_and_login(db_client, email=email)
    assert len(captured_verify_emails) == 1
    _, signup_token = captured_verify_emails[0]

    # Verify so is_verified=true.
    await _verify_via_token(db_client, signup_token)
    captured_verify_emails.clear()

    # Patch only display_name.
    r = await db_client.patch(
        "/users/me",
        json={"display_name": "Renamed User"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Renamed User"
    assert body["is_verified"] is True

    # No verification email should have fired.
    assert captured_verify_emails == []


async def test_email_change_to_same_value_is_a_noop_for_verification(
    db_client, captured_verify_emails
):
    """PATCH /users/me with the user's CURRENT email should not
    behave as if the email changed — no token, no banner re-pop.

    fastapi-users' update path normalizes the input and only
    considers it a change when the value actually differs from
    the row. We pin that: passing the same email back must NOT
    clear is_verified and must NOT fire a new verify email.
    """
    email = f"c43-same-{uuid.uuid4().hex[:6]}@example.com"
    _, headers = await register_and_login(db_client, email=email)
    assert len(captured_verify_emails) == 1
    _, signup_token = captured_verify_emails[0]

    await _verify_via_token(db_client, signup_token)
    captured_verify_emails.clear()

    r = await db_client.patch(
        "/users/me", json={"email": email}, headers=headers
    )
    assert r.status_code == 200, r.text
    # is_verified should stay true; no new email fired.
    assert r.json()["is_verified"] is True
    assert captured_verify_emails == []
