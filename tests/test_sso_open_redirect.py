"""Regression tests for the CodeQL "URL redirection from remote source"
finding on /auth/google/callback.

Before, the `?error=` query param on the callback was interpolated
straight into the Location header:

    return RedirectResponse(url=f"{fe_root}/#sso_error={error}", ...)

The fragment lives after `#` which sandboxes the value from path /
host (so this is NOT a classic open-redirect-to-evil.com primitive),
but an attacker could still:

  * Stuff CRLFs or escape sequences in to confuse downstream proxies
    and log scrapers.
  * Send oversized garbage that bloats the Location header.
  * Burn user-visible error copy with attacker-chosen strings via the
    FE's `Google sign-in failed (${ssoError})` fallback.

The fix pins `error` to an allow-list of RFC 6749 + OIDC standard
codes plus the FE-known internal codes. Anything else collapses to
`unknown` and the raw value is logged once for diagnostics.
"""
from __future__ import annotations

import pytest


# These are the canonical OAuth 2.0 error codes the spec allows the
# authorization server to send. Each should pass through verbatim.
_STANDARD_OAUTH_CODES = [
    "invalid_request",
    "unauthorized_client",
    "access_denied",
    "unsupported_response_type",
    "invalid_scope",
    "server_error",
    "temporarily_unavailable",
]


@pytest.mark.parametrize("code", _STANDARD_OAUTH_CODES)
def test_standard_oauth_codes_pass_through(code: str) -> None:
    """The standard codes should appear verbatim in the allow-list."""
    from backend.auth.google_sso import _ALLOWED_OAUTH_ERRORS
    assert _ALLOWED_OAUTH_ERRORS[code] == code


@pytest.mark.parametrize(
    "attacker_input",
    [
        # CRLF injection — pre-fix this would land verbatim in the
        # Location header
        "x\r\nSet-Cookie: a=b",
        # Path/host escape attempt — fragment-sandboxed, but still
        # worth refusing
        "../../evil.com",
        "javascript:alert(1)",
        "//evil.com",
        # XSS via the FE's `Google sign-in failed (${ssoError})`
        # text-node interpolation (text-node is safe, but the
        # attacker-controlled error message still ends up rendered)
        "<script>alert(1)</script>",
        # Oversized garbage
        "A" * 5000,
        # Empty / whitespace / null-y values
        " ",
        "\x00",
        # Unicode lookalikes
        "аccess_denied",  # Cyrillic 'а'
        # Plausibly-named but unknown codes
        "totally_legit_error",
        "consent_revoked",
    ],
)
def test_non_allowlisted_collapses_to_unknown(attacker_input: str) -> None:
    """Anything the allow-list doesn't recognise must collapse to the
    canonical `unknown` string. The raw value is NOT echoed."""
    from backend.auth.google_sso import _ALLOWED_OAUTH_ERRORS
    sanitized = _ALLOWED_OAUTH_ERRORS.get(attacker_input.lower(), "unknown")
    assert sanitized == "unknown"


def test_case_insensitive_match() -> None:
    """OAuth servers occasionally upper-case error codes; the lookup
    lower-cases before matching so canonical strings still flow through."""
    from backend.auth.google_sso import _ALLOWED_OAUTH_ERRORS
    assert _ALLOWED_OAUTH_ERRORS.get("ACCESS_DENIED".lower(), "unknown") == "access_denied"
    assert _ALLOWED_OAUTH_ERRORS.get("Access_Denied".lower(), "unknown") == "access_denied"


def test_callback_error_param_sanitized(monkeypatch) -> None:
    """End-to-end check: hitting /auth/google/callback with a malicious
    `error=` value lands at `#sso_error=unknown`, not at the raw value.

    Built with TestClient so the routing + RedirectResponse construction
    is exercised, not just the allow-list dict."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.auth import google_sso

    app = FastAPI()
    app.include_router(google_sso.router)

    # Override the frontend base URL so we have a stable assertion
    # target without touching real config.
    monkeypatch.setattr(
        google_sso.settings, "frontend_base_url", "https://app.example.test"
    )

    client = TestClient(app)
    attacker = "x\r\nSet-Cookie: a=b"
    r = client.get(
        "/auth/google/callback",
        params={"error": attacker},
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    # Sanitized: the raw attacker payload must NOT appear; the canonical
    # `unknown` string must.
    assert "sso_error=unknown" in location
    assert "Set-Cookie" not in location
    assert "\r" not in location
    assert "\n" not in location


def test_callback_error_param_standard_code_pass_through(monkeypatch) -> None:
    """A well-behaved Google response with `error=access_denied` lands
    at `#sso_error=access_denied` verbatim — the allow-list shouldn't
    over-sanitize legitimate codes."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.auth import google_sso

    app = FastAPI()
    app.include_router(google_sso.router)
    monkeypatch.setattr(
        google_sso.settings, "frontend_base_url", "https://app.example.test"
    )

    client = TestClient(app)
    r = client.get(
        "/auth/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["location"] == "https://app.example.test/#sso_error=access_denied"
