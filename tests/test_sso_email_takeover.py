"""Regression test for CR-5 — SSO email-match silent account takeover.

Threat model:
  1. Attacker calls POST /auth/register with the victim's email and
     an attacker-chosen password. Account is created with
     `is_verified=False` and the attacker's hashed_password. The
     attacker never clicks the verify link.
  2. Victim later signs in with Google.
  3. Before this patch, the SSO callback's email-fallback path
     bound `google_sub` to the existing row and flipped
     `is_verified=True`. The attacker's password kept working
     alongside the SSO link — silent hybrid takeover.

`_is_safe_email_bind` is the structural defense: it returns True
only when the existing row already proved email ownership
(`is_verified`) OR was created as SSO-only (no `hashed_password`).
The unsafe case raises `SsoEmailTakenError`, which the callback
surfaces as `#sso_error=email_taken`.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.auth.google_sso import (
    SsoEmailTakenError,
    _is_safe_email_bind,
)


def _stub_user(*, is_verified: bool, hashed_password: str | None) -> SimpleNamespace:
    """Minimal stand-in for a User row. `_is_safe_email_bind` only
    reads `is_verified` and `hashed_password`, so the namespace
    keeps the test independent of the full SQLAlchemy model
    (which needs the DB to instantiate cleanly)."""
    return SimpleNamespace(is_verified=is_verified, hashed_password=hashed_password)


def test_safe_bind_when_no_existing_row() -> None:
    """No row → safe; caller creates a fresh SSO-only user."""
    assert _is_safe_email_bind(None) is True


def test_safe_bind_when_existing_row_is_already_verified() -> None:
    """Verified row proves email ownership; binding Google is fine."""
    row = _stub_user(is_verified=True, hashed_password="$argon2id$...")
    assert _is_safe_email_bind(row) is True


def test_safe_bind_when_existing_row_has_no_password() -> None:
    """SSO-only signup row (no password). The email IS the identity
    in this case, so the bind doesn't share the account with anyone."""
    row = _stub_user(is_verified=False, hashed_password=None)
    assert _is_safe_email_bind(row) is True


def test_takeover_refused_for_unverified_password_bearing_row() -> None:
    """The exact attack scenario. Unverified + has password = an
    attacker who pre-registered the address. The bind MUST be
    refused; the patch raises SsoEmailTakenError, callers convert
    that to `#sso_error=email_taken`."""
    row = _stub_user(is_verified=False, hashed_password="$argon2id$...")
    assert _is_safe_email_bind(row) is False


def test_predicate_treats_empty_password_string_as_password_present() -> None:
    """Defense-in-depth: an empty-string hashed_password is still
    truthy-on-falsy-check noise we want to avoid. The predicate
    keys off `is None` precisely, so an empty string is treated as
    "password present" (refused if unverified). If a future
    migration ever surfaced empty strings, the predicate must NOT
    treat them as SSO-only."""
    row = _stub_user(is_verified=False, hashed_password="")
    # Empty string is not None, so the row is treated as having a
    # password. Combined with is_verified=False → unsafe.
    assert _is_safe_email_bind(row) is False


def test_ssoemailtakenerror_is_a_distinct_exception_class() -> None:
    """The callback catches SsoEmailTakenError BEFORE the broad
    `except Exception → #sso_error=internal` fallback. Subclass-of-
    Exception is required (the broad except still catches it if our
    typed branch is ever removed), but the type must be a distinct
    class so the typed branch fires first."""
    assert issubclass(SsoEmailTakenError, Exception)
    err = SsoEmailTakenError("victim@example.com")
    assert "victim@example.com" in str(err)
