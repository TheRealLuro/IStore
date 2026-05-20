"""Regression test for A5 — auth-lockout DoS keyed by email.

Before this patch, `SecurityControlsMiddleware` built the lockout key
as `auth:lock:{path}:{identity or ip}`. When `identity` (the
submitted email) was present, anyone who knew the victim's address
could lock them out by sending 5 failed `/auth/jwt/login` attempts
from a single attacker IP. The victim — on a totally different IP —
would then see 423 Locked when they tried to log in. Sustained
attack windows: 60s exponentially up to 15min.

Fix: include the IP in the lockout scope. The key is now
`auth:lock:{path}:{identity}:{ip}` (or `auth:lock:{path}:{ip}` when
no identity is present), so attacker-IP-A's failures only lock
(victim-email, attacker-IP-A). The victim on their own IP keeps a
clean counter.

Tradeoff documented inline: distributed brute force from many IPs
against one identity now bypasses the per-(identity, ip) lockout.
The per-IP burst limit (`auth:ip:{ip}:{path}` at 5/min) still
catches single-IP guessing. Catching distributed brute force
needs a CAPTCHA / WAF escalation, out of audit scope.
"""
from __future__ import annotations

import inspect

from backend.security import SecurityControlsMiddleware


def test_lockout_key_construction_includes_ip() -> None:
    """The middleware's dispatch() must build the lockout key with
    BOTH identity and ip when identity is non-empty. We check the
    source rather than running the middleware end-to-end (which
    would need Redis + a running app); a regression would flip the
    key back to `{identity or ip}` and this test catches it."""
    src = inspect.getsource(SecurityControlsMiddleware.dispatch)
    # The fixed form binds identity AND ip into the scope.
    assert "lock_scope" in src, (
        "Lockout key no longer goes through `lock_scope` — the A5 fix "
        "has been reverted or refactored away. Re-introduce the "
        "(identity, ip) tupling so a known-email attacker can't DoS "
        "the victim from any single IP."
    )
    # The legacy `or ip` shortcut for the lockout key MUST be gone.
    legacy = '"auth:lock:{request.url.path}:{identity or ip}"'
    assert legacy not in src, (
        "Lockout key reverted to the legacy `{identity or ip}` form. "
        "An attacker on one IP can DoS the victim across the network."
    )


def test_failure_counter_uses_same_scope_as_lockout() -> None:
    """The failure counter must use the same scope as the lockout —
    otherwise a one-IP attacker could increment the global victim
    counter without ever tripping their own lockout."""
    src = inspect.getsource(SecurityControlsMiddleware.dispatch)
    # Both keys reference `lock_scope`.
    assert src.count("lock_scope") >= 2, (
        "lock_key and fail_key should both be derived from "
        "`lock_scope` so the lockout-trigger arithmetic stays "
        "consistent across both counters."
    )


def test_fallback_to_ip_only_when_identity_is_empty() -> None:
    """Some auth paths (share-preview via GET) have no identity in
    the body — the ip-only fallback IS the intended unit there.
    The code must keep that path working."""
    src = inspect.getsource(SecurityControlsMiddleware.dispatch)
    # The fix uses `{identity}:{ip} if identity else ip`. Confirm
    # the conditional is present.
    assert 'if identity else' in src, (
        "The ip-only fallback for the no-identity case (share-preview) "
        "is missing. Without it, the lockout key would contain a "
        "leading colon and every share-preview request from a fresh "
        "IP would share the same lockout counter."
    )
