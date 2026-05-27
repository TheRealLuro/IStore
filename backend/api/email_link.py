"""Passwordless email-link ('magic link') sign-in.

Two endpoints:
  POST /auth/email-link/request   — body: {email}. Always 202.
                                    If the email matches a row, mints
                                    a 15-min single-use JWT-shaped
                                    token and mails it via
                                    send_signin_link_email. Anti-
                                    enumeration: returns the same
                                    response shape whether the email
                                    exists or not.
  POST /auth/email-link/consume   — body: {token}. Trades a fresh,
                                    unused token for a session JWT
                                    (same shape as /auth/jwt/login).
                                    Marks the token consumed via a
                                    Redis SET-on-jti so subsequent
                                    presentation of the same token
                                    fails — fixes the "user forwards
                                    the email to a teammate" / re-use
                                    case.

Security notes:
  - Token format: JWT signed with the HKDF-derived PURPOSE_SIGNIN_LINK
    subkey (NOT the raw jwt_secret — CR-3 key separation). Claims:
      sub  — user UUID (str)
      aud  — "neuthek:signin-link" (fixed string)
      exp  — now() + 900s (15 min)
      jti  — random UUID4 (for single-use tracking)
  - 2FA-aware: if the user has totp_enabled, /consume returns a 401
    with a {totp_required: true} payload — the FE then routes the
    user through the existing TOTP step. This prevents email
    compromise from bypassing the second factor entirely.
  - Email change re-verify: a magic-link consume does NOT touch
    is_verified. If the row has is_verified=false (because the user
    changed their email recently), the consume still succeeds but
    the in-app banner from C6b/C4.3 will prompt them to confirm
    the new address.
  - Rate limiting: /auth/email-link/request is added to the
    SecurityControlsMiddleware._AUTH_PATHS set so an attacker
    can't hammer it to spam mailboxes / enumerate addresses.
  - Audit row written on every successful consume.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import User, get_jwt_strategy
from backend.config import settings
from backend.db import get_session
from backend.email_send import send_signin_link_email
from backend.key_derivation import PURPOSE_SIGNIN_LINK, derive_subkey_str
from backend.models import AuditLog

logger = logging.getLogger(__name__)


_TOKEN_AUDIENCE = "neuthek:signin-link"
_TOKEN_TTL_SECONDS = 900  # 15 minutes
_TOKEN_ALG = "HS256"


def _signin_link_secret() -> str:
    """HKDF-derived subkey for signing magic-link tokens."""
    return derive_subkey_str(PURPOSE_SIGNIN_LINK)


# ---------- Redis-backed single-use jti tracking --------------------
#
# We use Redis SET NX with TTL to guarantee single-use semantics
# without adding a new SQL table. The key encodes the token's jti
# (the random UUID embedded in the JWT) so:
#   - Two concurrent consumes of the same token race on SET NX, exactly
#     one wins, the other gets the "already consumed" branch.
#   - TTL matches the token's max lifetime so the namespace doesn't
#     grow unbounded.
#
# If Redis is unreachable (rare in our deploy — it's healthchecked)
# we fail closed: refuse to consume. That's safer than letting a
# legitimate-looking token be replayed under a partial outage.

def _jti_key(jti: str) -> str:
    return f"signin-link:consumed:{jti}"


async def _mark_consumed(jti: str) -> bool:
    """Returns True on first consume, False if already consumed.

    Uses the same redis-from-url pattern as backend.security so we
    don't need a separate client. If redis is unreachable we fail
    CLOSED (return False) — better to lock a legitimate user out for
    one click than to silently allow replay during a partial outage.
    """
    try:
        import redis.asyncio as _redis  # type: ignore
        r = _redis.from_url(settings.redis_url, decode_responses=True)
        try:
            # SET NX with TTL = token lifetime so we expire the marker
            # exactly when the JWT itself would no longer be valid.
            result = await r.set(_jti_key(jti), "1", nx=True, ex=_TOKEN_TTL_SECONDS)
            return bool(result)
        finally:
            await r.aclose()
    except Exception:
        logger.exception("magic-link consume: redis unreachable, failing closed")
        return False


# ---------- request endpoint ---------------------------------------


class EmailLinkRequest(BaseModel):
    email: EmailStr


class EmailLinkRequestResponse(BaseModel):
    # Identical shape regardless of whether the email exists. Anti-
    # enumeration parity with /auth/forgot-password.
    ok: bool = True


router = APIRouter(prefix="/auth/email-link", tags=["auth"])


@router.post(
    "/request",
    response_model=EmailLinkRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def email_link_request(
    payload: EmailLinkRequest,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EmailLinkRequestResponse:
    user = (
        await session.execute(
            select(User).where(User.email == payload.email.lower())
        )
    ).scalar_one_or_none()

    # Always return 202. The actual email send happens in a background
    # task so the timing doesn't leak whether SMTP was invoked.
    if user is not None and user.is_active:
        now = datetime.now(timezone.utc)
        jti = uuid.uuid4().hex
        token_payload = {
            "sub": str(user.id),
            "aud": _TOKEN_AUDIENCE,
            "exp": now + timedelta(seconds=_TOKEN_TTL_SECONDS),
            "iat": now,
            "jti": jti,
        }
        token = jwt.encode(
            token_payload, _signin_link_secret(), algorithm=_TOKEN_ALG,
        )
        background_tasks.add_task(send_signin_link_email, user.email, token)

    return EmailLinkRequestResponse()


# ---------- consume endpoint ---------------------------------------


class EmailLinkConsumeRequest(BaseModel):
    token: str


class EmailLinkConsumeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class EmailLinkTotpRequiredResponse(BaseModel):
    # When the user has 2FA on, /consume bumps them through a second
    # factor. The FE catches this shape and routes to the TOTP step
    # carrying the email so the next /auth/jwt/login-totp call has
    # what it needs. Returning 401 keeps clients (curl) from mistaking
    # this for a successful sign-in.
    totp_required: bool = True
    email: str


@router.post("/consume")
async def email_link_consume(
    payload: EmailLinkConsumeRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    # Verify the token. PyJWT validates signature + audience + exp; any
    # failure surfaces a generic "invalid or expired" so we don't
    # leak which step failed (signature vs. expiry vs. audience).
    try:
        claims = jwt.decode(
            payload.token,
            _signin_link_secret(),
            algorithms=[_TOKEN_ALG],
            audience=_TOKEN_AUDIENCE,
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in link is invalid or expired.",
        )

    sub = claims.get("sub")
    jti = claims.get("jti")
    if not sub or not jti:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in link is invalid or expired.",
        )

    # Single-use enforcement BEFORE we mint the session token. If two
    # concurrent consumes race we want exactly one to win.
    first_use = await _mark_consumed(jti)
    if not first_use:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in link has already been used.",
        )

    try:
        user_id = uuid.UUID(sub)
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in link is invalid or expired.",
        )

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        # Don't reveal "user exists but is_active=false" — generic
        # message.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in link is invalid or expired.",
        )

    # 2FA-aware: a magic link is one factor (something you control
    # the email of). If the user opted into TOTP, require it here too
    # so a compromised mailbox can't bypass the second factor.
    # Status code 401 keeps non-browser clients (curl) from mistaking
    # this for a successful sign-in; the JSON shape tells the FE to
    # route into the TOTP step.
    if user.totp_enabled:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return EmailLinkTotpRequiredResponse(email=user.email)

    # Audit + mint session JWT.
    session.add(
        AuditLog(
            user_id=user.id,
            action="auth.email_link.consumed",
            details={"jti": jti},
        )
    )
    await session.commit()

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return EmailLinkConsumeResponse(access_token=token)
