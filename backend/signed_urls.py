from __future__ import annotations

import hmac
import time
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from backend.config import settings


def _payload(image_id: UUID, user_id: UUID, variant: str, expires: int) -> bytes:
    return f"{image_id}:{user_id}:{variant}:{expires}".encode("utf-8")


def sign_download(image_id: UUID, user_id: UUID, variant: str, expires: int) -> str:
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
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
# user-keyed signature above can't authorize them. We sign against the
# `share_id` instead — verification re-checks the grant row's
# revoked/expired/recipient state at serve time on top of the HMAC
# match, so a stolen URL can't outlive the grant it came from.


def _share_payload(share_id: UUID, variant: str, expires: int) -> bytes:
    return f"share:{share_id}:{variant}:{expires}".encode("utf-8")


def sign_share_download(share_id: UUID, variant: str, expires: int) -> str:
    return hmac.new(
        settings.jwt_secret.encode("utf-8"),
        _share_payload(share_id, variant, expires),
        sha256,
    ).hexdigest()


def make_signed_share_download(
    *,
    base_url: str,
    share_id: UUID,
    variant: str,
) -> dict[str, str]:
    ttl = _capped_ttl()
    expires = int(time.time()) + ttl
    sig = sign_share_download(share_id, variant, expires)
    root = base_url.rstrip("/")
    return {
        "url": f"{root}/shares/{share_id}/signed/{variant}?expires={expires}&sig={sig}",
        "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
    }


def verify_share_download(
    *,
    share_id: UUID,
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
    expected = sign_share_download(share_id, variant, expires)
    return hmac.compare_digest(expected, sig)
