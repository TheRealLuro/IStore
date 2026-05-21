from __future__ import annotations

import hmac
import time
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from backend.config import settings
from backend.key_derivation import (
    signed_download_key,
    signed_share_key,
    signed_stream_key,
)


def _payload(image_id: UUID, user_id: UUID, variant: str, expires: int) -> bytes:
    return f"{image_id}:{user_id}:{variant}:{expires}".encode("utf-8")


def sign_download(image_id: UUID, user_id: UUID, variant: str, expires: int) -> str:
    # Per CR-3: each signed-URL family signs under its own subkey
    # derived from jwt_secret via HKDF, so a leak of one family's
    # HMAC (e.g. via a log-oracle attack against share URLs) doesn't
    # let an attacker forge owner-download URLs or session JWTs.
    return hmac.new(
        signed_download_key(),
        _payload(image_id, user_id, variant, expires),
        sha256,
    ).hexdigest()


def _capped_ttl() -> int:
    """Return the effective signed-URL TTL, capped by the A4 ceiling.

    Operators can tune `download_url_ttl_seconds` *downward* (e.g. to
    60s for high-security deployments) but never above the A4 spec's
    "≤ 5 min" requirement. Centralized so a config bump can't accidentally
    issue a long-lived link.
    """
    return max(
        1,
        min(settings.download_url_ttl_seconds, settings.download_url_ttl_max_seconds),
    )


def make_signed_download(
    *,
    base_url: str,
    image_id: UUID,
    user_id: UUID,
    variant: str,
) -> dict[str, str]:
    ttl = _capped_ttl()
    expires = int(time.time()) + ttl
    sig = sign_download(image_id, user_id, variant, expires)
    root = base_url.rstrip("/")
    return {
        "url": f"{root}/images/{image_id}/signed/{variant}?uid={user_id}&expires={expires}&sig={sig}",
        "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
    }


def verify_download(
    *,
    image_id: UUID,
    user_id: UUID,
    variant: str,
    expires: int,
    sig: str,
) -> bool:
    if variant not in {"original", "served"}:
        return False
    now = int(time.time())
    if expires < now:
        return False
    # Defense-in-depth: even if `make_signed_download` ever drifts above
    # the cap, the verifier rejects any URL that would still be valid
    # more than `download_url_ttl_max_seconds` from now. So a stale
    # config that issued a 24h link gets rejected here, not served.
    if expires - now > settings.download_url_ttl_max_seconds:
        return False
    expected = sign_download(image_id, user_id, variant, expires)
    return hmac.compare_digest(expected, sig)


# ---------- Share grants (todo §1.1 / G1) ----------
#
# Recipients of a share grant aren't the owner of the image, so the
# user-keyed signature above can't authorize them. We sign against
# (share_id, recipient_user_id, variant, expires). Audit U3 — adding
# the recipient identity to the signed payload pins the URL to a
# specific recipient even though the byte-serving endpoint stays
# anonymous; downstream code (audit row at fetch time) records the
# recipient_id from the signed payload, giving the owner a real
# forensic trail when a URL leaks. Verification still re-checks the
# grant row's revoked/expired/recipient state at serve time on top
# of the HMAC match, so a stolen URL can't outlive the grant.


def _share_payload(
    share_id: UUID,
    recipient_user_id: UUID,
    variant: str,
    expires: int,
) -> bytes:
    return (
        f"share:{share_id}:{recipient_user_id}:{variant}:{expires}".encode("utf-8")
    )


def sign_share_download(
    share_id: UUID,
    recipient_user_id: UUID,
    variant: str,
    expires: int,
) -> str:
    # CR-3: distinct subkey from sign_download / sign_stream so a
    # share URL HMAC can't be reused as an owner-download HMAC under
    # the same secret.
    return hmac.new(
        signed_share_key(),
        _share_payload(share_id, recipient_user_id, variant, expires),
        sha256,
    ).hexdigest()


def make_signed_share_download(
    *,
    base_url: str,
    share_id: UUID,
    recipient_user_id: UUID,
    variant: str,
) -> dict[str, str]:
    ttl = _capped_ttl()
    expires = int(time.time()) + ttl
    sig = sign_share_download(share_id, recipient_user_id, variant, expires)
    root = base_url.rstrip("/")
    return {
        "url": (
            f"{root}/shares/{share_id}/signed/{variant}"
            f"?uid={recipient_user_id}&expires={expires}&sig={sig}"
        ),
        "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
    }


def verify_share_download(
    *,
    share_id: UUID,
    recipient_user_id: UUID,
    variant: str,
    expires: int,
    sig: str,
) -> bool:
    if variant not in {"original", "served"}:
        return False
    now = int(time.time())
    if expires < now:
        return False
    if expires - now > settings.download_url_ttl_max_seconds:
        return False
    expected = sign_share_download(share_id, recipient_user_id, variant, expires)
    return hmac.compare_digest(expected, sig)


# ---------- Streaming (video / audio) — separate TTL ----------
#
# Video / audio playback needs a longer-lived URL than a one-shot
# download — a 90-minute watch session with a pause in the middle
# can't survive a 5-minute cap. We sign a separate payload prefix
# ("stream:") so a download URL can't be replayed as a stream URL
# and vice versa, and gate the TTL on a dedicated cap config.


def _stream_payload(
    image_id: UUID, user_id: UUID, expires: int, quality: str = "",
) -> bytes:
    """Quality label is part of the signed payload so a URL minted
    for "720p" can't be replayed for "1080p" or "source" — gives the
    operator a real audit trail on which tier each grant was for.
    The empty string is the default tier (served_blob_key)."""
    return f"stream:{image_id}:{user_id}:{quality}:{expires}".encode("utf-8")


def sign_stream(
    image_id: UUID, user_id: UUID, expires: int, quality: str = "",
) -> str:
    # CR-3: separate stream subkey. Combined with the `stream:`
    # payload prefix this gives two independent guarantees that a
    # download URL can't be replayed as a stream URL and vice-versa
    # — payload domain separation AND key domain separation.
    return hmac.new(
        signed_stream_key(),
        _stream_payload(image_id, user_id, expires, quality),
        sha256,
    ).hexdigest()


def _capped_stream_ttl() -> int:
    return max(
        1,
        min(
            settings.stream_url_ttl_seconds,
            settings.stream_url_ttl_max_seconds,
        ),
    )


def make_signed_stream(
    *,
    base_url: str,
    image_id: UUID,
    user_id: UUID,
    quality: str = "",
) -> dict[str, str]:
    ttl = _capped_stream_ttl()
    expires = int(time.time()) + ttl
    sig = sign_stream(image_id, user_id, expires, quality)
    root = base_url.rstrip("/")
    q_param = f"&q={quality}" if quality else ""
    return {
        "url": f"{root}/images/{image_id}/stream?uid={user_id}&expires={expires}{q_param}&sig={sig}",
        "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        "quality": quality,
    }


def verify_stream(
    *,
    image_id: UUID,
    user_id: UUID,
    expires: int,
    sig: str,
    quality: str = "",
) -> bool:
    now = int(time.time())
    if expires < now:
        return False
    # Same defense-in-depth as `verify_download`: reject URLs whose
    # remaining lifetime exceeds the configured cap.
    if expires - now > settings.stream_url_ttl_max_seconds:
        return False
    expected = sign_stream(image_id, user_id, expires, quality)
    return hmac.compare_digest(expected, sig)
