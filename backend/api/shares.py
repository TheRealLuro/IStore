"""Per-image share grants (todo §1.1 / G1).

Sharer endpoints (auth = current_active_user, image owner check):
  POST   /images/{image_id}/shares             Create grant; returns share_url once.
  GET    /images/{image_id}/shares             List active grants on the image.
  DELETE /images/{image_id}/shares/{share_id}  Soft-revoke a grant.

Recipient endpoints (auth = current_active_user):
  GET    /shares/incoming                      Cards for the "Shared" sidebar.
  POST   /shares/claim                         Bind a token to the calling user.
  GET    /shares/{share_id}/asset              Mint a 5-min signed-URL for the bytes.

Public:
  GET    /shares/preview/{token}?email=...     Minimal landing-page preview.
  GET    /shares/{share_id}/signed/{variant}   HMAC-verified blob stream.

Tokens are random 32-byte urlsafe strings; only the argon2 hash is
persisted. Plaintext is returned exactly once on create. Recipients
are pinned by email — case-insensitive (citext + .lower() on input)
— and the first-claimer-wins rule means once `recipient_user_id` is
set on a row, no other user can claim that token even if they share
the email (e.g. address re-use after deletion). Brand-new recipients
always get a 1-day window measured from claim, regardless of the
sharer's chosen `duration_seconds` — this is the deliberate cap on
unverified-identity access.

Every state change writes an audit_log row (`share.created`,
`share.replaced`, `share.claimed`, `share.revoked`,
`share.asset.viewed`) with IP + user_agent so the trail survives a
recipient deleting their account.
"""
from __future__ import annotations

import logging
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import and_, func as sa_func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.images import _load_owned_image
from backend.audit import add_audit
from backend.auth.users import current_active_user
from backend.config import settings
from backend.db import get_session
from backend.image import fetch_original, fetch_served
from backend.models import Image, ShareGrant, User
from backend.schemas import (
    SHARE_MAX_DURATION_SECONDS,
    SHARE_NEW_USER_WINDOW_SECONDS,
    IncomingShareRead,
    ShareClaimBody,
    ShareClaimResult,
    ShareGrantCreate,
    ShareGrantRead,
    SharePreviewResult,
    ShareSignedUrl,
)
from backend.security import client_ip, enforce_rate_limit
from backend.signed_urls import make_signed_share_download, verify_share_download

logger = logging.getLogger(__name__)
router = APIRouter(tags=["shares"])

# Single shared argon2 hasher — the recovery-codes path uses the same
# instance under a different name. Defaults (m=64MiB / t=3 / p=4) are
# fine for our tiny per-recipient candidate set.
_HASHER = PasswordHasher()

# Constant-time placeholder used when a lookup miss would otherwise
# return early — running an argon2 verify against this hash keeps the
# response time roughly equivalent to a real-token-but-no-match path
# so an attacker can't time-distinguish "no grants for this user" from
# "wrong token". The plaintext "noop" never matches the hash.
_DUMMY_ARGON2_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)


def _normalize_email(value: str) -> str:
    """Normalize an email for case-insensitive equality on the recipient pin.

    Beyond `.strip().lower()`:
    - Apply NFKC unicode normalization so zero-width / compatibility
      variants don't sneak past the pin.
    - Strip control chars (CR / LF / NUL / zero-width spaces) that some
      mail clients tolerate but which would never appear in a legitimate
      pin and could be used to create lookalike addresses.
    - `.casefold()` (not `.lower()`) so the comparison is correct for
      non-ASCII scripts where Unicode has special casing rules.
    """
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    # Drop control chars + zero-width chars commonly used in spoofing.
    s = "".join(
        ch for ch in s
        if not (
            0x00 <= ord(ch) <= 0x1F
            or ord(ch) == 0x7F
            or ch in "​‌‍﻿⁠"
        )
    )
    return s


def _generate_token() -> str:
    # 32 bytes urlsafe-base64 = 43 chars, ~256 bits of entropy. The URL
    # gets `f"{frontend_base_url}/share/{token}"` so length is fine.
    return secrets.token_urlsafe(32)


async def _resolve_recipient_user(
    session: AsyncSession, email: str
) -> User | None:
    """Resolve a recipient by email. `email` must already be the output
    of `_normalize_email`. We compare `lower(users.email)` in SQL — for
    plain ASCII this matches `.casefold()`. For unicode-heavy emails the
    SQL `lower()` and Python `.casefold()` can diverge; we therefore
    fall back to a candidate sweep when the direct comparison misses
    and the input contains non-ASCII bytes.
    """
    normalized = _normalize_email(email)
    direct = (
        await session.execute(
            select(User).where(sa_func.lower(User.email) == normalized)
        )
    ).scalar_one_or_none()
    if direct is not None:
        return direct
    if normalized.isascii():
        return None
    # Non-ASCII path: scan a narrow window of candidates whose lowercased
    # email shares the local part with the query, then casefold-compare
    # in Python. The cardinality is bounded by users in the deployment;
    # this stays cheap and only fires on the unicode-spoof path.
    local = normalized.split("@", 1)[0][:32]
    if not local:
        return None
    candidates = (
        await session.execute(
            select(User).where(sa_func.lower(User.email).like(f"{local}%"))
        )
    ).scalars().all()
    for candidate in candidates:
        if _normalize_email(candidate.email) == normalized:
            return candidate
    return None


async def _grant_to_read(
    session: AsyncSession, grant: ShareGrant, *, share_url: str | None = None
) -> ShareGrantRead:
    display_name: str | None = None
    if grant.recipient_user_id is not None:
        display_name = (
            await session.execute(
                select(User.display_name).where(User.id == grant.recipient_user_id)
            )
        ).scalar_one_or_none()
    return ShareGrantRead(
        id=grant.id,
        image_id=grant.image_id,
        recipient_email=grant.recipient_email,
        recipient_user_id=grant.recipient_user_id,
        recipient_display_name=display_name,
        permission=grant.permission,
        sharer_duration_seconds=grant.sharer_duration_seconds,
        expires_at=grant.expires_at,
        claimed_at=grant.claimed_at,
        revoked_at=grant.revoked_at,
        created_at=grant.created_at,
        share_url=share_url,
    )


def _is_active(grant: ShareGrant) -> bool:
    if grant.revoked_at is not None:
        return False
    if grant.expires_at is not None and grant.expires_at <= datetime.now(timezone.utc):
        return False
    return True


# ---------- Sharer endpoints ----------


@router.post(
    "/images/{image_id}/shares",
    response_model=ShareGrantRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_share(
    image_id: UUID,
    body: ShareGrantCreate,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShareGrantRead:
    image = await _load_owned_image(image_id, user, session)

    recipient_email = _normalize_email(body.recipient_email)
    if recipient_email == _normalize_email(user.email):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "You can't share an image to yourself."
        )

    # Server-side cap (Pydantic already enforces ≤ SHARE_MAX_DURATION_SECONDS,
    # but be defensive in case the schema is bypassed by a future internal call).
    duration = max(60, min(int(body.duration_seconds), SHARE_MAX_DURATION_SECONDS))

    # If a live grant exists for the same (image, recipient) pair,
    # soft-revoke it so the new token supersedes. The unique index
    # `share_grants_image_recipient_uq` is partial on
    # `revoked_at IS NULL`, so an INSERT after revoke is allowed.
    existing = (
        await session.execute(
            select(ShareGrant).where(
                ShareGrant.image_id == image.id,
                ShareGrant.recipient_email == recipient_email,
                ShareGrant.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.revoked_at = datetime.now(timezone.utc)
        await add_audit(
            session,
            user_id=user.id,
            action="share.replaced",
            details={
                "share_id": str(existing.id),
                "image_id": str(image.id),
                "recipient_email": recipient_email,
            },
        )
        await session.flush()

    recipient = await _resolve_recipient_user(session, recipient_email)
    now = datetime.now(timezone.utc)

    expires_at: datetime | None = None
    if recipient is not None:
        expires_at = now + timedelta(seconds=duration)

    plaintext_token = _generate_token()
    grant = ShareGrant(
        image_id=image.id,
        sharer_user_id=user.id,
        recipient_email=recipient_email,
        recipient_user_id=recipient.id if recipient else None,
        permission=body.permission,
        token_hash=_HASHER.hash(plaintext_token),
        sharer_duration_seconds=duration,
        expires_at=expires_at,
    )
    session.add(grant)

    try:
        await session.flush()
    except IntegrityError:
        # Concurrent create from two browser tabs / a retry. The
        # partial unique index protects us; surface a friendly error.
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An active share for this recipient already exists.",
        )

    await add_audit(
        session,
        user_id=user.id,
        action="share.created",
        details={
            "share_id": str(grant.id),
            "image_id": str(image.id),
            "recipient_email": recipient_email,
            "recipient_user_id": str(recipient.id) if recipient else None,
            "permission": body.permission,
            "duration_seconds": duration,
            "ip": client_ip(request),
            "user_agent": request.headers.get("user-agent"),
        },
    )
    await session.commit()
    await session.refresh(grant)

    fe_root = settings.frontend_base_url.rstrip("/")
    share_url = f"{fe_root}/share/{plaintext_token}"
    return await _grant_to_read(session, grant, share_url=share_url)


@router.get(
    "/images/{image_id}/shares",
    response_model=list[ShareGrantRead],
)
async def list_shares(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ShareGrantRead]:
    image = await _load_owned_image(image_id, user, session)
    rows = (
        await session.execute(
            select(ShareGrant)
            .where(
                ShareGrant.image_id == image.id,
                ShareGrant.revoked_at.is_(None),
            )
            .order_by(ShareGrant.created_at.desc())
        )
    ).scalars().all()
    return [await _grant_to_read(session, g) for g in rows]


@router.delete(
    "/images/{image_id}/shares/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_share(
    image_id: UUID,
    share_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    image = await _load_owned_image(image_id, user, session)
    grant = (
        await session.execute(
            select(ShareGrant).where(
                ShareGrant.id == share_id,
                ShareGrant.image_id == image.id,
                ShareGrant.sharer_user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(timezone.utc)
        await add_audit(
            session,
            user_id=user.id,
            action="share.revoked",
            details={
                "share_id": str(grant.id),
                "image_id": str(image.id),
                "ip": client_ip(request),
                "user_agent": request.headers.get("user-agent"),
            },
        )
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Recipient endpoints ----------


@router.get("/shares/incoming", response_model=list[IncomingShareRead])
async def incoming_shares(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[IncomingShareRead]:
    now = datetime.now(timezone.utc)
    rows = (
        await session.execute(
            select(ShareGrant, Image, User)
            .join(Image, Image.id == ShareGrant.image_id)
            .join(User, User.id == ShareGrant.sharer_user_id)
            .where(
                ShareGrant.recipient_user_id == user.id,
                ShareGrant.revoked_at.is_(None),
                or_(
                    ShareGrant.expires_at.is_(None),
                    ShareGrant.expires_at > now,
                ),
                Image.deleted_at.is_(None),
            )
            .order_by(ShareGrant.created_at.desc())
        )
    ).all()

    return [
        IncomingShareRead(
            share_id=g.id,
            image_id=img.id,
            image_filename=img.original_filename,
            image_category=img.category,
            sharer_display_name=sharer.display_name,
            sharer_email=sharer.email,
            permission=g.permission,
            expires_at=g.expires_at,
            claimed_at=g.claimed_at,
        )
        for (g, img, sharer) in rows
    ]


async def _verify_token_against_grants(
    token: str, candidates: list[ShareGrant]
) -> ShareGrant | None:
    for g in candidates:
        try:
            _HASHER.verify(g.token_hash, token)
            return g
        except VerifyMismatchError:
            continue
        except Exception:
            continue
    return None


@router.post("/shares/claim", response_model=ShareClaimResult)
async def claim_share(
    body: ShareClaimBody,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ShareClaimResult:
    # Per-IP burst limiter; the SecurityControlsMiddleware extension
    # adds longer-window brute-force lockout on top of this.
    await enforce_rate_limit(
        key=f"share:claim:{client_ip(request)}",
        limit=10,
        window_seconds=60,
        detail="Too many share claim attempts",
    )

    user_email = _normalize_email(user.email)
    candidates = (
        await session.execute(
            select(ShareGrant)
            .where(
                ShareGrant.recipient_email == user_email,
                ShareGrant.revoked_at.is_(None),
                or_(
                    ShareGrant.recipient_user_id.is_(None),
                    ShareGrant.recipient_user_id == user.id,
                ),
            )
        )
    ).scalars().all()

    matched = await _verify_token_against_grants(body.token, candidates)
    if matched is None:
        # Constant-time fail — same shape as the recovery-codes endpoint.
        try:
            _HASHER.verify(_DUMMY_ARGON2_HASH, "noop")
        except Exception:
            pass
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Invalid or expired share link."
        )

    # Take a row lock + re-read so two concurrent claims for the same
    # grant (same user, two tabs, ~simultaneous POSTs) don't both flip
    # was_pending=True. Without the lock, both flows audit-log a "new
    # claim" and racily set expires_at — only one wins the write but
    # both saw was_pending=True at evaluation time.
    locked = (
        await session.execute(
            select(ShareGrant).where(ShareGrant.id == matched.id).with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        # Grant was revoked between candidate fetch and lock acquisition.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Invalid or expired share link."
        )
    matched = locked

    # Re-check expiry on a row that *was* already bound to this user.
    if matched.expires_at is not None and matched.expires_at <= datetime.now(
        timezone.utc
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Invalid or expired share link."
        )
    if matched.revoked_at is not None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Invalid or expired share link."
        )

    was_pending = matched.recipient_user_id is None
    now = datetime.now(timezone.utc)
    if was_pending:
        matched.recipient_user_id = user.id
        # Deliberate cap: new-user recipients always get exactly 1 day
        # from the moment of claim, regardless of what the sharer set.
        matched.expires_at = now + timedelta(seconds=SHARE_NEW_USER_WINDOW_SECONDS)
        matched.claimed_at = now
    elif matched.claimed_at is None:
        matched.claimed_at = now

    await add_audit(
        session,
        user_id=user.id,
        action="share.claimed",
        details={
            "share_id": str(matched.id),
            "image_id": str(matched.image_id),
            "was_pending": was_pending,
            "ip": client_ip(request),
            "user_agent": request.headers.get("user-agent"),
        },
    )
    await session.commit()
    await session.refresh(matched)

    return ShareClaimResult(
        share_id=matched.id,
        image_id=matched.image_id,
        expires_at=matched.expires_at,
        was_pending=was_pending,
    )


async def _load_active_grant_for_recipient(
    session: AsyncSession, share_id: UUID, user: User
) -> ShareGrant:
    grant = (
        await session.execute(
            select(ShareGrant).where(
                ShareGrant.id == share_id,
                ShareGrant.recipient_user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if grant is None or not _is_active(grant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not available.")
    return grant


@router.get("/shares/{share_id}/asset", response_model=ShareSignedUrl)
async def share_asset_url(
    share_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    variant: str = Query(default="served", pattern="^(original|served)$"),
) -> ShareSignedUrl:
    grant = await _load_active_grant_for_recipient(session, share_id, user)

    if variant == "original" and grant.permission != "view_download":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This share is view-only; original download not permitted.",
        )

    # Make sure the underlying image still exists and isn't trashed.
    image = (
        await session.execute(
            select(Image).where(
                Image.id == grant.image_id,
                Image.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not available.")

    await add_audit(
        session,
        user_id=user.id,
        action="share.asset.viewed",
        details={
            "share_id": str(grant.id),
            "image_id": str(grant.image_id),
            "variant": variant,
            "ip": client_ip(request),
            "user_agent": request.headers.get("user-agent"),
        },
    )
    await session.commit()

    signed = make_signed_share_download(
        base_url=str(request.base_url),
        share_id=grant.id,
        variant=variant,
    )
    return ShareSignedUrl(url=signed["url"], expires_at=signed["expires_at"])


@router.get("/shares/{share_id}/signed/{variant}")
async def share_signed_download(
    share_id: UUID,
    variant: str,
    expires: int,
    sig: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if not verify_share_download(
        share_id=share_id, variant=variant, expires=expires, sig=sig
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Invalid or expired download URL"
        )
    grant = (
        await session.execute(select(ShareGrant).where(ShareGrant.id == share_id))
    ).scalar_one_or_none()
    if grant is None or not _is_active(grant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not available.")
    if variant == "original" and grant.permission != "view_download":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This share is view-only; original download not permitted.",
        )
    image = (
        await session.execute(
            select(Image).where(
                Image.id == grant.image_id,
                Image.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not available.")

    if variant == "original":
        if image.original_blob_key is None:
            blob, mime = await fetch_served(image)
            return Response(
                content=blob,
                media_type=mime,
                headers={"X-Original-Expired": "true"},
            )
        blob, mime = await fetch_original(image)
        return Response(content=blob, media_type=mime)
    blob, mime = await fetch_served(image)
    return Response(content=blob, media_type=mime)


# ---------- Public preview ----------


@router.get("/shares/preview/{token}", response_model=SharePreviewResult)
async def preview_share(
    token: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    email: str = Query(
        ..., min_length=3, max_length=320,
        description="Recipient email the sharer addressed this link to. "
        "Pinning by email caps the candidate set we argon2-verify "
        "against and prevents an unauthenticated bulk-scan attack.",
    ),
) -> SharePreviewResult:
    await enforce_rate_limit(
        key=f"share:preview:{client_ip(request)}",
        limit=20,
        window_seconds=60,
        detail="Too many share preview requests",
    )

    normalized = _normalize_email(email)
    candidates = (
        await session.execute(
            select(ShareGrant).where(
                ShareGrant.recipient_email == normalized,
                ShareGrant.revoked_at.is_(None),
            )
        )
    ).scalars().all()

    matched = await _verify_token_against_grants(token, candidates)
    if matched is None:
        try:
            _HASHER.verify(_DUMMY_ARGON2_HASH, "noop")
        except Exception:
            pass
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Invalid or expired share link."
        )

    if not _is_active(matched):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Invalid or expired share link."
        )

    image = (
        await session.execute(
            select(Image).where(
                Image.id == matched.image_id,
                Image.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Invalid or expired share link."
        )

    sharer = (
        await session.execute(
            select(User).where(User.id == matched.sharer_user_id)
        )
    ).scalar_one_or_none()

    return SharePreviewResult(
        sharer_display_name=sharer.display_name if sharer else None,
        image_filename=image.original_filename,
        image_category=image.category,
        requires_signup=matched.recipient_user_id is None,
    )
