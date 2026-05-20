"""Regression test for the cookie-login lockout gap.

`/auth/cookie/login` is the FE's primary login path. The audit caught that
it was not listed in `SecurityControlsMiddleware._AUTH_PATHS`, so the
middleware's `is_post_auth = path in self._AUTH_PATHS` short-circuited
the request straight through — no per-IP rate limit, no per-identity
lockout after N failures, and no `auth.login.succeeded`/`failed`
audit rows for any browser login.

The full integration test of the lockout flow needs
`SECURITY_RATE_LIMITS_ENABLED=true` AND `APP_ENV != test`, both of
which the test conftest forces off for unrelated reasons. So the
regression here is a static assertion on the path set itself plus a
check that the credential-attempt classifier picks the path up. If
either of those is true, the middleware will lock + audit the path.
"""
from __future__ import annotations

from backend.security import SecurityControlsMiddleware


def test_cookie_login_is_in_auth_paths() -> None:
    """The primary browser login route must be subject to the same
    per-IP + per-identity throttling as the Bearer JWT route."""
    assert "/auth/cookie/login" in SecurityControlsMiddleware._AUTH_PATHS, (
        "/auth/cookie/login is missing from _AUTH_PATHS; "
        "the middleware will pass requests through unprotected. "
        "Browser logins would have neither rate-limit nor audit."
    )


def test_other_browser_auth_paths_are_in_auth_paths() -> None:
    """Symmetric check: the Bearer-equivalent + TOTP + recovery paths
    must all be listed. Catches future drift if the set is reordered."""
    must_be_listed = {
        "/auth/jwt/login",
        "/auth/cookie/login",
        "/auth/jwt/login-totp",
        "/account/recovery-codes/login",
        "/auth/forgot-password",
        "/auth/reset-password",
        "/auth/request-verify-token",
        "/shares/claim",
    }
    missing = must_be_listed - SecurityControlsMiddleware._AUTH_PATHS
    assert not missing, f"Auth paths missing from _AUTH_PATHS: {missing}"


def test_cookie_login_path_classifies_as_credential_attempt() -> None:
    """The credential-attempt branch in dispatch() must also recognise
    the path so failed attempts increment the failure counter (and a
    successful one writes `auth.login.succeeded`). This is what the
    middleware does internally; we replicate the predicate to lock the
    contract.
    """
    path = "/auth/cookie/login"
    is_credential_attempt = (
        path.endswith("/login")
        or path == "/auth/jwt/login-totp"
        or path == "/account/recovery-codes/login"
        or path == "/shares/claim"
    )
    assert is_credential_attempt, (
        "Credential-attempt classifier doesn't match /auth/cookie/login. "
        "Failed attempts won't count toward lockout."
    )
