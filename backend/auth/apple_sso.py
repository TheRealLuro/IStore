"""Sign in with Apple (sign in / register on the auth screen).

The Apple counterpart of `backend/auth/google_sso.py`. Same idea — a
brand-new visitor signs in / registers from the auth screen with one tap,
no password, no email round-trip (Apple verified the email) — but Apple's
web OAuth differs from Google in three ways this module handles:

  1. The "client secret" is NOT a static string. It's a short-lived JWT we
     sign on every request with the `.p8` private key (ES256), claims
     iss=Team ID, sub=Services ID, aud=appleid.apple.com. See
     `_apple_client_secret`.
  2. Apple uses `response_mode=form_post` (required whenever `name`/`email`
     scope is requested), so Apple POSTs the callback — `/auth/apple/callback`
     is a POST, not a GET. Apple also returns the user's NAME only on the
     FIRST authorization, in the POST body's `user` JSON field.
  3. The id_token is signed by Apple (RS256). We verify it against Apple's
     published JWKS with the audience pinned to our Services ID — we never
     trust claims that aren't signed by Apple AND addressed to us.

Endpoints (mirror Google's surface):
  GET  /auth/apple/login          → 302 to Apple's authorize screen.
  POST /auth/apple/callback       → completes the exchange, returns a JWT.
  POST /auth/apple/link           → logged-in user attaches Apple.
  DELETE /auth/apple/link         → unlink.
  POST /auth/apple/complete-totp  → second leg of a TOTP-gated Apple sign-in.

Security parity with Google: HMAC-signed state (namespaced `apple:` so it
can't be confused with a Google state), CR-5 safe email-bind (shared
predicate), F1 neuthek-TOTP second factor, and — crucially — new SSO users
are created with `age_confirmed=False`, so the FE still shows the §B2
consent + age gate on first sign-in. Apple sign-up does NOT skip consents;
it only removes the password friction.

Operational note: Apple rejects `http://localhost` return URLs — the
Services ID Return URL must be https on a verified public domain. Until
`APPLE_CLIENT_ID` / `APPLE_TEAM_ID` / `APPLE_KEY_ID` / `APPLE_PRIVATE_KEY` /
`APPLE_SIGNIN_REDIRECT_URI` are all set, every endpoint stays in the
graceful "not configured" state (503 / `#sso_error=not_configured`).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.config import settings
from backend.db import SessionLocal, get_session
from backend.key_derivation import oauth_sso_state_key
from backend.models import AuditLog, User

# Reuse the security-critical, provider-agnostic helpers from the Google
# module so the CR-5 bind predicate, the F1 TOTP pending token, the session
# cookie, the FE-landing builder, and the echoed-error allow-list all have a
# SINGLE source of truth across both SSO providers.
from backend.auth.google_sso import (
    _ALLOWED_OAUTH_ERRORS,
    SsoEmailTakenError,
    _fe_landing,
    _is_safe_email_bind,
    _set_sso_session_cookie,
    mint_sso_totp_pending,
    verify_sso_totp_pending,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/apple", tags=["auth"])

# Apple's published OAuth / OIDC endpoints.
_APPLE_AUTHORIZE = "https://appleid.apple.com/auth/authorize"
_APPLE_TOKEN = "https://appleid.apple.com/auth/token"
_APPLE_JWKS = "https://appleid.apple.com/auth/keys"
_APPLE_ISSUER = "https://appleid.apple.com"
_SCOPES = "name email"


def _apple_configured() -> bool:
    return bool(
        settings.apple_client_id
        and settings.apple_team_id
        and settings.apple_key_id
        and settings.apple_private_key
        and settings.apple_signin_redirect_uri
    )


def _require_configured() -> None:
    if not _apple_configured():
        raise HTTPException(
            503,
            "Sign in with Apple is not configured. Set APPLE_CLIENT_ID, "
            "APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY and "
            "APPLE_SIGNIN_REDIRECT_URI (https, no localhost) in .env.",
        )


# ---------- HMAC-signed state (mirrors google_sso, `apple:`-namespaced) ----
#
# Reuses oauth_sso_state_key() but namespaces every payload with `apple:` so
# an Apple state can never verify as a Google state (or vice versa) even
# though both are minted under the same CR-3 subkey — defence in depth on top
# of the fact that each provider's callback only processes its own tokens.


def _build_state(link_user_id: UUID | None = None) -> str:
    nonce = secrets.token_urlsafe(16)
    if link_user_id is not None:
        payload = f"apple:link:{link_user_id}.{nonce}"
    else:
        payload = f"apple:{nonce}"
    mac = hmac.new(
        oauth_sso_state_key(), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{mac}"


def _verify_state(state: str | None) -> bool:
    if not isinstance(state, str) or not state.startswith("apple:"):
        return False
    payload, _, mac = state.rpartition(".")
    if not payload or not mac:
        return False
    expected = hmac.new(
        oauth_sso_state_key(), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, mac)


def _extract_link_user_id(state: str | None) -> UUID | None:
    if not state or not state.startswith("apple:link:"):
        return None
    try:
        body = state[len("apple:link:"):]
        return UUID(body.split(".", 1)[0])
    except (ValueError, IndexError):
        return None


# ---------- Apple client secret (ES256 JWT, signed per-request) ------------


def _apple_private_key_pem() -> str:
    """The .p8 PEM. A single-line env value with literal `\\n` escapes (a
    common way to stuff a multi-line key into one env var) is normalised to
    real newlines so cryptography can parse it; `*_FILE` mounts already
    arrive with real newlines."""
    key = settings.apple_private_key or ""
    if "\\n" in key and "\n" not in key:
        key = key.replace("\\n", "\n")
    return key.strip() + "\n"


def _apple_client_secret() -> str:
    """Mint the short-lived ES256 client-secret JWT Apple's token endpoint
    expects in place of a static secret."""
    import jwt  # PyJWT (+cryptography for ES256)

    now = int(time.time())
    payload = {
        "iss": settings.apple_team_id,
        "iat": now,
        "exp": now + 600,  # 10 min — Apple caps at 6 months; we sign per-request
        "aud": _APPLE_ISSUER,
        "sub": settings.apple_client_id,
    }
    return jwt.encode(
        payload,
        _apple_private_key_pem(),
        algorithm="ES256",
        headers={"kid": settings.apple_key_id, "alg": "ES256"},
    )


def _verify_apple_id_token(id_token_str: str) -> dict[str, Any]:
    """Verify Apple's id_token signature + audience + issuer against Apple's
    JWKS. Runs sync (PyJWKClient does a cached network fetch) — callers wrap
    it in asyncio.to_thread so the event loop isn't blocked."""
    import jwt
    from jwt import PyJWKClient

    jwks_client = PyJWKClient(_APPLE_JWKS)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token_str)
    return jwt.decode(
        id_token_str,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.apple_client_id,
        issuer=_APPLE_ISSUER,
    )


async def _exchange_code(code: str) -> dict[str, Any]:
    """Exchange the authorization code for tokens at Apple's token endpoint,
    authenticating with the freshly-signed client-secret JWT."""
    import httpx

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.apple_signin_redirect_uri,
        "client_id": settings.apple_client_id,
        "client_secret": _apple_client_secret(),
    }
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(_APPLE_TOKEN, data=data)
        resp.raise_for_status()
        return resp.json()


def _display_name_from_user_field(user_field: str | None) -> str | None:
    """Apple returns the name ONLY on the first authorization, as a JSON
    blob in the POST body's `user` field: {"name": {"firstName": "...",
    "lastName": "..."}, "email": "..."}. Parse it defensively."""
    if not user_field:
        return None
    try:
        data = json.loads(user_field)
        name = data.get("name") or {}
        parts = [name.get("firstName"), name.get("lastName")]
        full = " ".join(p for p in parts if p).strip()
        return full or None
    except (ValueError, TypeError, AttributeError):
        return None


# ---------- /auth/apple/login ---------------------------------------------


@router.get("/enabled")
async def apple_enabled() -> dict:
    """Public — lets the auth screen render 'Continue with Apple' as a live
    button vs a 'coming soon' affordance. Returns a boolean only; no secrets
    and no hint of WHICH setting is missing."""
    return {"enabled": _apple_configured()}


@router.get("/login")
async def apple_login() -> RedirectResponse:
    """Kick off the Apple flow — 302 to Apple's authorize screen. The FE just
    does `window.location.href = '/auth/apple/login'`. When Apple isn't
    configured yet we bounce back to the FE with a friendly error rather than
    a raw 503, so a stray click degrades gracefully."""
    if not _apple_configured():
        fe_root = settings.frontend_base_url.rstrip("/")
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=not_configured", status_code=302
        )
    params = {
        "response_type": "code",
        "client_id": settings.apple_client_id,
        "redirect_uri": settings.apple_signin_redirect_uri,
        "scope": _SCOPES,
        # form_post is REQUIRED when name/email scope is requested.
        "response_mode": "form_post",
        "state": _build_state(),
    }
    url = f"{_APPLE_AUTHORIZE}?{urlencode(params)}"
    logger.info("auth.apple: login redirect")
    return RedirectResponse(url=url, status_code=302)


# ---------- /auth/apple/link (logged-in user attaches Apple) ---------------


class LinkInitResponse(BaseModel):
    auth_url: str


@router.post("/link", response_model=LinkInitResponse)
async def apple_link_init(
    user: Annotated[User, Depends(current_active_user)],
) -> LinkInitResponse:
    """Start the flow that attaches an Apple account to the signed-in user.
    The state encodes this user's id (`apple:link:<uuid>...`), HMAC-signed so
    the callback trusts it without re-reading the session cookie."""
    _require_configured()
    params = {
        "response_type": "code",
        "client_id": settings.apple_client_id,
        "redirect_uri": settings.apple_signin_redirect_uri,
        "scope": _SCOPES,
        "response_mode": "form_post",
        "state": _build_state(link_user_id=user.id),
    }
    auth_url = f"{_APPLE_AUTHORIZE}?{urlencode(params)}"
    logger.info("auth.apple: link init user=%s", user.id)
    return LinkInitResponse(auth_url=auth_url)


@router.delete("/link")
async def apple_link_clear(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Drop the Apple binding from this user."""
    if not user.apple_sub:
        return {"linked": False, "changed": False}
    user.apple_sub = None
    await session.commit()
    logger.info("auth.apple: unlinked user=%s", user.id)
    return {"linked": False, "changed": True}


# ---------- find-or-create (mirror google, keyed on apple_sub) -------------


async def _find_or_create_user(
    session: AsyncSession,
    *,
    email: str,
    display_name: str | None,
    apple_sub: str,
    request: Request | None,
) -> tuple[User, bool]:
    """Look up by Apple sub first, then email (CR-5-safe). Returns
    (user, was_created). Mirrors google_sso._find_or_create_user."""
    existing = (
        await session.execute(select(User).where(User.apple_sub == apple_sub))
    ).scalar_one_or_none()
    if existing is None:
        by_email = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        # CR-5 — refuse to bind a fresh Apple identity onto an unverified,
        # password-bearing local row (pre-registration takeover). Shared
        # predicate with the Google path.
        if not _is_safe_email_bind(by_email):
            raise SsoEmailTakenError(email)
        existing = by_email
    if existing is not None:
        changed = False
        if not existing.apple_sub:
            existing.apple_sub = apple_sub
            changed = True
        # Apple already verified the email (relay or real), so a previously
        # unverified local account can be marked verified now.
        if not existing.is_verified:
            existing.is_verified = True
            changed = True
        if not existing.display_name and display_name:
            existing.display_name = display_name
            changed = True
        if changed:
            await session.flush()
        return existing, False

    # New Apple sign-up. Random unguessable password (fastapi-users requires
    # a non-null hash; the user never types it). age_confirmed stays False so
    # the FE shows the §B2 consent + age gate on first sign-in.
    from fastapi_users.password import PasswordHelper

    hashed = PasswordHelper().hash(secrets.token_urlsafe(48))
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        is_verified=True,
        is_superuser=False,
        display_name=display_name,
        role="user",
        age_confirmed=False,
        apple_sub=apple_sub,
    )
    session.add(user)
    await session.flush()

    from backend.audit import add_audit

    ip = ""
    ua = ""
    if request is not None:
        client = getattr(request, "client", None)
        if client is not None:
            ip = client.host or ""
        ua = request.headers.get("user-agent", "")
    await add_audit(
        session,
        user_id=user.id,
        action="auth.signup.apple",
        details={
            "email": email,
            "apple_sub": apple_sub,
            "ip": ip or None,
            "ua": ua[:200] if ua else None,
        },
    )
    return user, True


# ---------- /auth/apple/callback (POST — response_mode=form_post) ----------


@router.post("/callback")
async def apple_callback(request: Request) -> RedirectResponse:
    """Complete the Apple exchange and bounce back to the FE with a JWT in
    the URL fragment. Apple POSTs here (form_post), so we read the form body
    rather than query params. Errors surface as `#sso_error=...` so the FE
    can render a useful message."""
    fe_root = settings.frontend_base_url.rstrip("/")
    form = await request.form()
    error = form.get("error")
    code = form.get("code")
    state = form.get("state")
    user_field = form.get("user")  # JSON, first authorization only

    if error:
        sanitized = _ALLOWED_OAUTH_ERRORS.get(str(error).lower(), "unknown")
        if sanitized == "unknown":
            logger.warning(
                "auth.apple: callback non-allowlisted error (logged not echoed): %r",
                str(error)[:64],
            )
        return RedirectResponse(
            url=f"{fe_root}/#sso_error={sanitized}", status_code=302
        )
    if not code or not state or not _verify_state(state):
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=bad_state", status_code=302
        )
    if not _apple_configured():
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=not_configured", status_code=302
        )

    # Exchange the code for tokens (proves we hold the Services ID's key).
    try:
        token_resp = await _exchange_code(str(code))
    except Exception:
        logger.exception("auth.apple: token exchange failed")
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=token_exchange_failed", status_code=302
        )

    id_token_raw = token_resp.get("id_token")
    if not id_token_raw:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=no_id_token", status_code=302
        )

    # Verify the id_token against Apple's JWKS (sig + aud=Services ID + iss).
    try:
        claims = await asyncio.to_thread(_verify_apple_id_token, id_token_raw)
    except Exception:
        logger.exception("auth.apple: id_token verification failed")
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=bad_id_token", status_code=302
        )

    apple_sub = claims.get("sub")
    email = (claims.get("email") or "").lower().strip()
    # Apple sends email_verified as bool or the string "true"/"false".
    ev = claims.get("email_verified")
    email_verified = ev is True or str(ev).lower() == "true"
    display_name = _display_name_from_user_field(
        user_field if isinstance(user_field, str) else None
    )

    if not apple_sub:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=no_subject", status_code=302
        )
    if not email:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=no_email", status_code=302
        )
    if not email_verified:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=email_unverified", status_code=302
        )

    # Link flow — stamp the Apple sub onto the specific user the state names.
    link_user_id = _extract_link_user_id(str(state))
    if link_user_id is not None:
        async with SessionLocal() as session:
            try:
                target = (
                    await session.execute(
                        select(User).where(User.id == link_user_id)
                    )
                ).scalar_one_or_none()
                if target is None:
                    return RedirectResponse(
                        url=f"{fe_root}/#sso_error=link_user_missing",
                        status_code=302,
                    )
                conflict = (
                    await session.execute(
                        select(User).where(
                            User.apple_sub == apple_sub,
                            User.id != link_user_id,
                        )
                    )
                ).scalar_one_or_none()
                if conflict is not None:
                    return RedirectResponse(
                        url=f"{fe_root}/#sso_error=google_already_linked",
                        status_code=302,
                    )
                target.apple_sub = apple_sub
                if not target.display_name and display_name:
                    target.display_name = display_name
                await session.commit()
            except Exception:
                logger.exception("auth.apple: link failed user=%s", link_user_id)
                return RedirectResponse(
                    url=f"{fe_root}/#sso_error=link_internal", status_code=302
                )
        logger.info("auth.apple: linked user=%s", link_user_id)
        return RedirectResponse(
            url=f"{fe_root}/#sso_linked=1&email={email}", status_code=302
        )

    # Sign-in / sign-up.
    async with SessionLocal() as session:
        try:
            user, was_created = await _find_or_create_user(
                session,
                email=email,
                display_name=display_name,
                apple_sub=apple_sub,
                request=request,
            )
            await session.commit()
            await session.refresh(user)
        except SsoEmailTakenError:
            logger.info(
                "auth.apple: email-takeover bind refused for %s (unverified existing account)",
                email,
            )
            return RedirectResponse(
                url=f"{fe_root}/#sso_error=email_taken", status_code=302
            )
        except Exception:
            logger.exception("auth.apple: find/create failed for %s", email)
            return RedirectResponse(
                url=f"{fe_root}/#sso_error=internal", status_code=302
            )

    if not user.is_active:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=account_inactive", status_code=302
        )

    # F1 — neuthek TOTP second factor. A user who put an authenticator on THIS
    # account must enter their code; Apple's leg alone isn't sufficient. Mint
    # the provider-agnostic short-lived pending token and bounce to the TOTP
    # step instead of a session (shared with the Google path).
    if user.totp_enabled and user.totp_secret_enc:
        pending = mint_sso_totp_pending(user.id)
        try:
            async with SessionLocal() as s:
                s.add(
                    AuditLog(
                        user_id=user.id,
                        action="auth.sso.totp_challenge",
                        details={"provider": "apple", "email": email},
                    )
                )
                await s.commit()
        except Exception:
            logger.exception("auth.apple: audit write failed for sso totp challenge")
        logger.info("auth.apple: totp challenge issued user=%s", user.id)
        qs = {"sso_totp": pending, "sso_new": "1" if was_created else "0"}
        return RedirectResponse(url=_fe_landing(qs), status_code=302)

    from backend.auth.users import get_jwt_strategy

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    logger.info(
        "auth.apple: signed in user=%s email=%s new=%s", user.id, email, was_created
    )
    qs = {"sso_token": token, "sso_new": "1" if was_created else "0"}
    resp = RedirectResponse(url=_fe_landing(qs), status_code=302)
    _set_sso_session_cookie(resp, token)
    return resp


# ---------- /auth/apple/complete-totp (mirror google) ----------------------


class SsoTotpCompleteRequest(BaseModel):
    pending: str
    code: str


class SsoTotpCompleteResponse(BaseModel):
    sso_token: str
    sso_new: bool = False


@router.post("/complete-totp", response_model=SsoTotpCompleteResponse)
async def complete_sso_totp(
    body: SsoTotpCompleteRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SsoTotpCompleteResponse:
    """Second leg of a TOTP-gated Apple sign-in — re-verify the pending token
    (provider-agnostic) AND the TOTP code, then mint the real session."""
    from datetime import datetime, timezone

    from backend.api.two_factor import _verify_code
    from backend.auth.users import get_jwt_strategy
    from backend.secret_box import decrypt

    user_id = verify_sso_totp_pending(body.pending)
    if user_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This sign-in step expired — start the Apple sign-in again.",
        )
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid sign-in.")
    if not user.totp_enabled or not user.totp_secret_enc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Two-factor is no longer enabled — sign in again.",
        )
    secret = decrypt(user.totp_secret_enc)
    if not _verify_code(secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid 2FA code.")

    user.totp_verified_at = datetime.now(timezone.utc)
    session.add(
        AuditLog(
            user_id=user.id,
            action="auth.sso.totp_completed",
            details={"provider": "apple"},
        )
    )
    await session.commit()

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    _set_sso_session_cookie(response, token)
    logger.info("auth.apple: totp-gated sign-in completed user=%s", user.id)
    return SsoTotpCompleteResponse(sso_token=token)
