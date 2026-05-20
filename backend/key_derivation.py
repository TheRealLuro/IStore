"""HKDF-derived per-purpose subkeys.

Audit finding CR-3 — before this module, `settings.jwt_secret` was the raw
HMAC key for every one of:

  - fastapi-users' session JWT (read in get_jwt_strategy)
  - fastapi-users' password-reset and email-verify tokens
    (UserManager.reset_password_token_secret / verification_token_secret)
  - signed download URLs            (signed_urls.sign_download)
  - signed share-download URLs      (signed_urls.sign_share_download)
  - signed video-stream URLs        (signed_urls.sign_stream)
  - Google SSO state HMAC           (auth.google_sso._build_state / _verify_state)
  - Cloud-sync OAuth state HMAC     (cloud_sync._build_state / _verify_state)

A single leak of `jwt_secret` (logs, backup, env dump, OAuth state echoed
into nginx/Caddy access logs as `?state=…`) simultaneously yielded:
session forgery, signed-URL forgery for arbitrary owner downloads /
shares / streams, OAuth-state forgery for both sign-in and Drive-link
flows, and password-reset / email-verify token forgery. Operators won't
rotate the secret in practice because the blast radius of rotation is
"every session, every signed URL, every reset email, every pending OAuth
flow, all at once" — so the same exposure stays in place indefinitely.

HKDF-SHA256 (RFC 5869) lets us keep a single root secret in
configuration but derive cryptographically independent subkeys per
purpose. Recovering one purpose's subkey via cryptanalysis would not
reveal the root or any other purpose's key (HKDF-Expand is HMAC-PRF; the
preimage-resistance of SHA-256 carries through). Operationally that
means a leak of one subkey (e.g. a signed URL HMAC observed via a log
oracle) is bounded to that purpose only.

The session-JWT secret (get_jwt_strategy) intentionally still reads
`settings.jwt_secret` directly in this revision. Changing it now would
invalidate every live cookie session at deploy time without a
mechanism to refresh — that's its own fix tracked under finding A8
(token_version-based revocation). When A8 lands we'll move JWT signing
onto its own subkey too.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.config import settings


# 256-bit subkeys. SHA-256 makes 256-bit the natural fit; signed URLs
# and OAuth states use them as HMAC keys (HMAC of any length works,
# 256 bits is the recommended minimum for SHA-256).
_DERIVED_LEN = 32

# All purpose labels are versioned (`-v1`). Bumping the version (e.g.
# `-v2`) is the rotation lever for a single domain — change the label
# at the call site to invalidate every signature minted under the
# previous one without touching unrelated domains.
_LABEL_PREFIX = b"neuthek:"


def derive_subkey(purpose: bytes) -> bytes:
    """Return a 256-bit subkey for `purpose`.

    Deterministic: same root + same purpose → same bytes. Different
    purpose labels yield independent keys.

    The purpose label MUST be a non-empty bytes value; using the same
    label across call sites means they share the subkey (deliberate
    when two call sites are the SAME domain, e.g. `_build_state` and
    `_verify_state` in google_sso.py). Using different labels for
    distinct domains is what closes finding CR-3.

    Implementation uses HKDF-SHA256 (RFC 5869) via the `cryptography`
    library's canonical `HKDF` primitive — NOT a bare hash. Static
    scanners may try to flag SHA-256 as "weak password hashing", but
    that warning applies to user-password storage (where you want
    argon2 / bcrypt because the input is low-entropy and you want
    work-factor brute-force resistance). The input here is
    `settings.jwt_secret`, a high-entropy server-side key enforced
    ≥32 chars by `validate_production_settings`. HKDF is the correct
    primitive: it's defined precisely to expand a high-entropy
    secret into multiple domain-separated subkeys.

    We pass `salt=None` because the root secret is already required to
    be high-entropy; per RFC 5869 §3.1 a null salt is acceptable and
    HKDF-Extract collapses to HMAC under a fixed zero key.
    """
    if not purpose:
        raise ValueError("purpose must be a non-empty byte string")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_DERIVED_LEN,
        salt=None,
        info=_LABEL_PREFIX + purpose,
    )
    return hkdf.derive(settings.jwt_secret.encode("utf-8"))


def derive_subkey_str(purpose: bytes) -> str:
    """Hex-encoded subkey, for callers that need a `str` secret.

    fastapi-users' `reset_password_token_secret` and
    `verification_token_secret` are typed `SecretType = Union[str,
    pydantic.SecretStr]`; the str path is the simpler wire-up.
    """
    return derive_subkey(purpose).hex()


# ---------- Public purpose labels ----------
#
# Centralized so a typo at a call site can't accidentally land two
# domains on the same subkey. Each label has a `-v1` suffix for
# future rotation.

PURPOSE_RESET_PASSWORD = b"reset-password-token-v1"
PURPOSE_VERIFY_EMAIL = b"verify-email-token-v1"

PURPOSE_SIGNED_DOWNLOAD = b"signed-url-download-v1"
PURPOSE_SIGNED_SHARE = b"signed-url-share-v1"
PURPOSE_SIGNED_STREAM = b"signed-url-stream-v1"

PURPOSE_OAUTH_STATE_SSO = b"oauth-state-google-sso-v1"
PURPOSE_OAUTH_STATE_CLOUD_SYNC = b"oauth-state-cloud-sync-v1"


# Cached lookups for hot-path call sites. `lru_cache(maxsize=1)` per
# purpose avoids re-running the HKDF on every HMAC; the cached value is
# safe to retain for the process lifetime because `settings.jwt_secret`
# is read once at config-load time. Tests that override the root secret
# should call `.cache_clear()` on the relevant accessor.

@lru_cache(maxsize=1)
def _cached_download_key() -> bytes:
    return derive_subkey(PURPOSE_SIGNED_DOWNLOAD)


@lru_cache(maxsize=1)
def _cached_share_key() -> bytes:
    return derive_subkey(PURPOSE_SIGNED_SHARE)


@lru_cache(maxsize=1)
def _cached_stream_key() -> bytes:
    return derive_subkey(PURPOSE_SIGNED_STREAM)


@lru_cache(maxsize=1)
def _cached_oauth_sso_key() -> bytes:
    return derive_subkey(PURPOSE_OAUTH_STATE_SSO)


@lru_cache(maxsize=1)
def _cached_oauth_cloud_sync_key() -> bytes:
    return derive_subkey(PURPOSE_OAUTH_STATE_CLOUD_SYNC)


def signed_download_key() -> bytes:
    """Per-purpose HMAC key for owner download signatures."""
    return _cached_download_key()


def signed_share_key() -> bytes:
    """Per-purpose HMAC key for share-download signatures."""
    return _cached_share_key()


def signed_stream_key() -> bytes:
    """Per-purpose HMAC key for video-stream signatures."""
    return _cached_stream_key()


def oauth_sso_state_key() -> bytes:
    """Per-purpose HMAC key for Google sign-in state."""
    return _cached_oauth_sso_key()


def oauth_cloud_sync_state_key() -> bytes:
    """Per-purpose HMAC key for cloud-sync OAuth state."""
    return _cached_oauth_cloud_sync_key()


def _clear_all_caches() -> None:
    """Test hook — invalidate every cached subkey. Call between tests
    that monkeypatch `settings.jwt_secret`."""
    for fn in (
        _cached_download_key,
        _cached_share_key,
        _cached_stream_key,
        _cached_oauth_sso_key,
        _cached_oauth_cloud_sync_key,
    ):
        fn.cache_clear()
