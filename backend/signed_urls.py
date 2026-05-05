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


def make_signed_download(
    *,
    base_url: str,
    image_id: UUID,
    user_id: UUID,
    variant: str,
) -> dict[str, str]:
    ttl = min(settings.download_url_ttl_seconds, 300)
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
    if expires < int(time.time()):
        return False
    expected = sign_download(image_id, user_id, variant, expires)
    return hmac.compare_digest(expected, sig)
