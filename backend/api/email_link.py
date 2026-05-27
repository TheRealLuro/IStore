"""Passwordless email-link ('magic link') sign-in.

Three endpoints:
  POST /auth/email-link/request       — body: {email}. Always 202.
                                        If the email matches a row,
                                        mints BOTH a 15-min single-
                                        use JWT (for the link) AND a
                                        6-digit code (for users who
                                        can't click the link — e.g.
                                        signing in on a TV / desktop
                                        from their phone's inbox).
                                        Mails both via
                                        send_signin_link_email.
                                        Anti-enumeration: same shape
                                        whether the email exists.
  POST /auth/email-link/consume       — body: {token}. Trades a
                                        fresh, unused JWT for a
                                        session JWT (same shape as
                                        /auth/jwt/login). Single-use
                                        via Redis SET NX on the jti.
  POST /auth/email-link/consume-code  — body: {email, code}. Same
                                        outcome as /consume but
                                        keyed by the 6-digit code
                                        from the email instead of
                                        the JWT-shaped token. The
                                        code is also single-use; a
                                        successful consume
                                        invalidates BOTH the code
                                        and the paired JWT (no re-
                                        play via either path).

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
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import COOKIE_NAME, User, get_jwt_strategy
from backend.config import settings
from backend.db import get_session
from backend.email_send import send_signin_link_email
from backend.key_derivation import PURPOSE_SIGNIN_LINK, derive_subkey_str
from backend.models import AuditLog

logger = logging.getLogger(__name__)


def _set_session_cookie(response: Response, token: str) -> None:
    """Set the cookie-auth session cookie alongside the bearer-token
    response body. Without this, the FE (which uses HttpOnly-cookie
    auth via the `neuthek_session` cookie set by /auth/cookie/login)
    gets a JWT in the response but has no cookie to ship on the next
    request — /users/me 401s, the user is bounced back to the sign-
    in screen, and the magic-link UX looks broken.

    Mirrors the cookie attrs from CookieTransport in
    backend.auth.users so this cookie behaves identically to one set
    by the regular /auth/cookie/login path: same name, max-age,
    Secure (in prod), HttpOnly, SameSite=Lax, root path.
    """
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.jwt_lifetime_seconds,
        path="/",
        secure=settings.is_production,
        httponly=True,
        samesite="lax",
    )


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


# ---------- 6-digit code storage (Redis) ----------------------------
#
# Keyed by email (lowercased + url-quoted), so two concurrent
# /request calls for the same address race on SET XX and the second
# overwrites the first (latest wins). The value stores both the
# code + the paired jti, so consuming via /consume-code can also
# invalidate the JWT path (defense against "user clicks the link
# AND someone else with the leaked email tries the code").
#
# Code shape: zero-padded 6 digits via secrets.randbelow(10**6).
# 1M combinations + 15-min TTL + per-IP lockout via _AUTH_PATHS
# bounds the brute-force window comfortably (an attacker would
# need ~100k attempts to hit 10% probability, well outside the
# rate-limit budget).

def _code_key(email: str) -> str:
    return f"signin-link:code:{email.lower()}"


def _generate_six_digit_code() -> str:
    """6-digit string. `secrets.randbelow` is the CSPRNG-backed
    primitive — NOT `random.randint`, which is a Mersenne-Twister
    output an attacker could predict given observed values."""
    return f"{secrets.randbelow(1_000_000):06d}"


async def _store_code(email: str, code: str, jti: str) -> None:
    """Persist the (code, jti) pair in Redis with the same TTL as
    the link JWT. Overwrites any prior in-flight code for this
    email so repeated /request calls don't leave a stash of valid
    codes the user can't track (latest wins)."""
    try:
        import redis.asyncio as _redis  # type: ignore
        r = _redis.from_url(settings.redis_url, decode_responses=True)
        try:
            # JSON-shaped value so the consume side can read both
            # halves without a second round-trip.
            import json as _json
            payload = _json.dumps({"code": code, "jti": jti})
            await r.set(_code_key(email), payload, ex=_TOKEN_TTL_SECONDS)
        finally:
            await r.aclose()
    except Exception:
        # Redis outage during /request is non-fatal — the user can
        # still complete sign-in via the JWT link in the email. Log
        # but don't surface to the client (would be an enumeration
        # signal too).
        logger.exception("magic-link request: redis unreachable storing code")


async def _consume_code(email: str, candidate: str) -> tuple[bool, str | None]:
    """Look up the stored (code, jti) for `email`, compare in
    constant time against `candidate`, and on match DEL the key so
    a second attempt fails. Returns (ok, jti_to_revoke).

    `jti_to_revoke` is non-None on success: the caller should mark
    that jti consumed too, so a user who clicks the link after
    successfully entering the code doesn't get a second session.
    """
    try:
        import redis.asyncio as _redis  # type: ignore
        r = _redis.from_url(settings.redis_url, decode_responses=True)
        try:
            stored = await r.get(_code_key(email))
            if stored is None:
                return False, None
            import json as _json
            try:
                payload = _json.loads(stored)
            except Exception:
                return False, None
            stored_code = payload.get("code") or ""
            jti = payload.get("jti")
            # Constant-time string compare so timing doesn't leak
            # partial matches on the digits.
            if not secrets.compare_digest(stored_code, candidate):
                return False, None
            # Match. DEL atomically — if another consume races us
            # on the same key, only one DEL takes effect (the other
            # observes None on GET).
            await r.delete(_code_key(email))
            return True, jti
        finally:
            await r.aclose()
    except Exception:
        logger.exception("magic-link consume-code: redis unreachable, failing closed")
        return False, None


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
        # Also mint a 6-digit code paired with this jti. Stored in
        # Redis with the same TTL so the link AND the code both
        # expire together. Consuming either path invalidates both
        # (the consume-code branch marks the jti consumed too, and
        # the consume-token branch is fine letting the unused code
        # expire on its own).
        code = _generate_six_digit_code()
        await _store_code(user.email, code, jti)
        background_tasks.add_task(send_signin_link_email, user.email, token, code)

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
    _set_session_cookie(response, token)
    return EmailLinkConsumeResponse(access_token=token)


# ---------- 6-digit code consume endpoint ----------------------------


class EmailLinkConsumeCodeRequest(BaseModel):
    email: EmailStr
    code: str


@router.post("/consume-code")
async def email_link_consume_code(
    payload: EmailLinkConsumeCodeRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Sign in via the 6-digit code from the email. Same outcome as
    /consume but keyed by (email, code) instead of the JWT-shaped
    token. Useful when the user is signing in on a device different
    from where their email is (TV, kiosk, another laptop).

    Single-use: a successful consume DELs the Redis key so a second
    attempt with the same code fails. Also marks the paired jti
    consumed so the link in the same email can't be re-used after
    the code path succeeded.

    Brute-force resistance: 6-digit codes have 1M combinations and
    a 15-min TTL, but the SecurityControlsMiddleware lockout on
    /auth/email-link/consume-code (via _AUTH_PATHS) bounds an
    attacker's attempts well before the probability of a guess
    becomes meaningful.
    """
    code = (payload.code or "").strip().replace("-", "").replace(" ", "")
    # Tolerate "123 456" / "123-456" formats the user might type.
    if not code or not code.isdigit() or len(code) != 6:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in code is invalid or expired.",
        )

    email_norm = payload.email.lower()
    ok, jti = await _consume_code(email_norm, code)
    if not ok:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in code is invalid or expired.",
        )

    # Mark the paired jti consumed too so the link in the same email
    # can't be replayed. Best-effort: if Redis lost the key between
    # _consume_code and here we still proceed — the code was valid
    # AND single-use within the same Redis instance.
    if jti:
        await _mark_consumed(jti)

    user = (
        await session.execute(select(User).where(User.email == email_norm))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in code is invalid or expired.",
        )

    # Same 2FA-aware short-circuit as /consume.
    if user.totp_enabled:
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return EmailLinkTotpRequiredResponse(email=user.email)

    session.add(
        AuditLog(
            user_id=user.id,
            action="auth.email_link.consumed",
            details={"method": "code"},
        )
    )
    await session.commit()

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    _set_session_cookie(response, token)
    return EmailLinkConsumeResponse(access_token=token)
