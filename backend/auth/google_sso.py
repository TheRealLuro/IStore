"""Google Sign-In (sign in / register on the auth screen).

Distinct from the cloud-sync OAuth flow in `backend/cloud_sync.py`:

* That one asks for `drive.readonly` so we can pull a user's Drive files
  *after* they've already signed in to neuthek.
* This one asks for `openid email profile` so a brand-new visitor can
  sign in / register from the auth screen with one click — no password
  to remember, no email verification round-trip (Google already did it).

Endpoints
---------
GET  /auth/google/login        → 302 to Google's consent screen.
GET  /auth/google/callback     → completes the exchange, returns a JWT.

Both flows share the same Google Cloud project — only the redirect URI
list in the Console needs both entries.

Security notes
--------------
* State is HMAC-signed (jwt_secret + nonce) so the callback can verify
  it issued the request — same defense against OAuth CSRF as cloud_sync.
* PKCE is enabled via `google_auth_oauthlib`'s autogen; the verifier is
  stashed in Redis (key TTL 600s) and retrieved on callback. Without
  this, Google rejects the exchange with "Missing code verifier."
* Email verification is set from Google's `email_verified` claim. We
  refuse to sign in users whose Google email is unverified (extremely
  rare but a real attack surface — an attacker controlling DNS for a
  newly-registered domain could otherwise impersonate a brand-new
  google.com account that points at a victim's address).
* New users get `age_confirmed=False`; the FE redirects to the consent
  modal on first sign-in so the user still goes through the §B2 gate
  before any uploads happen.
* The JWT we issue uses the *exact* same `JWTStrategy` as
  /auth/jwt/login — same secret, same lifetime — so a Google-SSO JWT
  is indistinguishable from a password-login JWT in every downstream
  check.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db import SessionLocal
from backend.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth"])


# ---------- HMAC-signed state (same pattern as cloud_sync) ----------------


def _build_state() -> str:
    """Random nonce + HMAC. Unlike the cloud-sync version we don't bind
    a user_id because the sign-in flow has no caller-user (anonymous
    visitor)."""
    nonce = secrets.token_urlsafe(16)
    mac = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{mac}"


def _verify_state(state: str) -> bool:
    if not isinstance(state, str) or state.count(".") != 1:
        return False
    nonce, mac = state.split(".", 1)
    expected = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, mac)


# ---------- PKCE verifier storage (reuses cloud_sync's Redis helpers) -----


async def _stash_pkce_verifier(state: str, verifier: str) -> None:
    """Mirror of cloud_sync._stash_pkce_verifier but with a distinct
    key prefix so an SSO state can never collide with a Drive state."""
    key = "auth:google:pkce:" + hashlib.sha256(state.encode()).hexdigest()
    try:
        import redis.asyncio as redis  # type: ignore

        client = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await client.set(key, verifier, ex=600)
        finally:
            await client.aclose()
        return
    except Exception:
        _PKCE_FALLBACK[key] = verifier


async def _pop_pkce_verifier(state: str) -> str | None:
    key = "auth:google:pkce:" + hashlib.sha256(state.encode()).hexdigest()
    try:
        import redis.asyncio as redis  # type: ignore

        client = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            value = await client.get(key)
            if value is not None:
                await client.delete(key)
                return value
        finally:
            await client.aclose()
    except Exception:
        pass
    return _PKCE_FALLBACK.pop(key, None)


_PKCE_FALLBACK: dict[str, str] = {}


# ---------- Google Flow ---------------------------------------------------

_SCOPES = ["openid", "email", "profile"]


def _google_flow():
    """Build the SSO-flavored Flow. Distinct from the Drive flow by its
    scope list + redirect_uri. Same client_id/secret since both flows
    live under the same Google Cloud project."""
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise HTTPException(
            503,
            "Google sign-in is not configured. Set "
            "GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET in .env.",
        )
    try:
        from google_auth_oauthlib.flow import Flow  # type: ignore
    except ImportError as exc:
        raise HTTPException(
            503,
            "google-auth-oauthlib is not installed. Install the [cloud] "
            "extra: `pip install -e \".[cloud]\"`.",
        ) from exc

    client_config = {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_signin_redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=_SCOPES,
        redirect_uri=settings.google_signin_redirect_uri,
    )


# ---------- /auth/google/login -------------------------------------------


@router.get("/login")
async def google_login() -> RedirectResponse:
    """Kick off the SSO flow. Returns a 302 straight to Google's consent
    screen — the FE just does `window.location.href = '/auth/google/login'`."""
    flow = _google_flow()
    flow.autogenerate_code_verifier = True
    auth_url, state = flow.authorization_url(
        access_type="online",  # we don't need a refresh token for SSO
        include_granted_scopes="true",
        prompt="select_account",  # let returning users pick their account
        state=_build_state(),
    )
    if flow.code_verifier:
        await _stash_pkce_verifier(state, flow.code_verifier)
    logger.info("auth.google: login redirect state=%s", state[:8])
    return RedirectResponse(url=auth_url, status_code=302)


# ---------- /auth/google/callback ----------------------------------------


async def _find_or_create_user(
    session: AsyncSession,
    *,
    email: str,
    email_verified: bool,
    display_name: str | None,
    google_sub: str,
    request: Request | None,
) -> tuple[User, bool]:
    """Look up an existing user by email or create one. Returns
    (user, was_created)."""
    # We rely on email as the join key. Google's `email_verified=true`
    # is what makes this safe — without it, an attacker who registers
    # a Google account at "victim@neuthek.local" could impersonate the
    # existing user. We refuse unverified emails outright before
    # reaching this point.
    existing = (
        await session.execute(
            select(User).where(User.email == email)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # If the user existed but Google's verification is fresher,
        # mark them verified — the email_send/verify flow can be
        # skipped now.
        changed = False
        if not existing.is_verified:
            existing.is_verified = True
            changed = True
        if not existing.display_name and display_name:
            existing.display_name = display_name
            changed = True
        if changed:
            await session.flush()
        return existing, False

    # New SSO sign-up. Generate an unguessable random password — the
    # user will never type it, but fastapi-users requires hashed_password
    # to be non-null, and we want a hash so a future password-reset
    # flow works seamlessly if they ever want to add a password.
    from fastapi_users.password import PasswordHelper

    random_pw = secrets.token_urlsafe(48)
    hashed = PasswordHelper().hash(random_pw)
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=hashed,
        is_active=True,
        # Google's email_verified=true is sufficient — same standard
        # the /auth/verify flow lives by.
        is_verified=True,
        is_superuser=False,
        display_name=display_name,
        role="user",
        # We deliberately leave age_confirmed=False so the FE still
        # shows the §B2 consent + age modal on first sign-in. SSO
        # doesn't skip the legal gate — it just removes the password
        # friction.
        age_confirmed=False,
    )
    session.add(user)
    await session.flush()

    # Audit the sign-up so admins can see SSO conversions in the
    # admin dashboard (same `auth.*` action prefix the rest of the
    # auth flow uses).
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
        action="auth.signup.google",
        details={"email": email, "google_sub": google_sub, "ip": ip or None, "ua": ua[:200] if ua else None},
    )
    return user, True


def _fe_landing(qs: dict[str, str]) -> str:
    """Build the FE landing URL with the JWT in a `#fragment` so the
    token never appears in server access logs (fragments are not sent
    to the server on subsequent requests)."""
    fe_root = settings.frontend_base_url.rstrip("/")
    return f"{fe_root}/#" + urlencode(qs)


@router.get("/callback")
async def google_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Complete the SSO exchange and bounce back to the FE with a JWT
    in the URL fragment.

    Errors are surfaced the same way (`#sso_error=...`) so the FE can
    toast a useful message rather than showing a blank backend page.
    """
    fe_root = settings.frontend_base_url.rstrip("/")
    if error:
        return RedirectResponse(url=f"{fe_root}/#sso_error={error}", status_code=302)
    if not code or not state or not _verify_state(state):
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=bad_state", status_code=302
        )

    try:
        flow = _google_flow()
    except HTTPException as exc:
        logger.warning("auth.google: callback hit a not-configured flow: %s", exc.detail)
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=not_configured", status_code=302
        )

    verifier = await _pop_pkce_verifier(state)
    if verifier:
        flow.code_verifier = verifier

    try:
        flow.fetch_token(code=code)
    except Exception:
        logger.exception("auth.google: token exchange failed")
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=token_exchange_failed", status_code=302
        )

    creds = flow.credentials
    id_token_raw = getattr(creds, "id_token", None)
    if not id_token_raw:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=no_id_token", status_code=302
        )

    # Verify the id_token signature + audience against Google's published
    # public keys. We do NOT trust the claims unless they're signed by
    # Google AND addressed to our client_id.
    try:
        from google.oauth2 import id_token as google_id_token  # type: ignore
        from google.auth.transport import requests as google_requests  # type: ignore

        claims: dict[str, Any] = google_id_token.verify_oauth2_token(
            id_token_raw,
            google_requests.Request(),
            settings.google_oauth_client_id,
        )
    except Exception:
        logger.exception("auth.google: id_token verification failed")
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=bad_id_token", status_code=302
        )

    email = (claims.get("email") or "").lower().strip()
    email_verified = bool(claims.get("email_verified"))
    google_sub = claims.get("sub")
    display_name = claims.get("name") or claims.get("given_name")

    if not email:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=no_email", status_code=302
        )
    if not email_verified:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=email_unverified", status_code=302
        )
    if not google_sub:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=no_subject", status_code=302
        )

    # Use a fresh session — the callback isn't under `Depends(get_session)`.
    async with SessionLocal() as session:
        try:
            user, was_created = await _find_or_create_user(
                session,
                email=email,
                email_verified=email_verified,
                display_name=display_name,
                google_sub=google_sub,
                request=request,
            )
            await session.commit()
            await session.refresh(user)
        except Exception:
            logger.exception("auth.google: failed to find/create user for %s", email)
            return RedirectResponse(
                url=f"{fe_root}/#sso_error=internal", status_code=302
            )

    # Block locked-out / banned accounts the same way the JWT login
    # path does. `is_active=False` is the global kill switch.
    if not user.is_active:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=account_inactive", status_code=302
        )

    # 2FA gate: if the user has TOTP enabled, refuse the auto-login —
    # SSO doesn't carry a TOTP code. Send them to the password sign-in
    # path with a hint. This is the same posture as on_after_login in
    # users.py: we never grant a JWT to a TOTP-enabled user without
    # a verified code.
    if user.totp_enabled and user.totp_secret_enc:
        return RedirectResponse(
            url=f"{fe_root}/#sso_error=totp_required", status_code=302
        )

    # Issue a JWT using the same strategy as /auth/jwt/login so every
    # downstream `current_active_user` dependency accepts it transparently.
    from backend.auth.users import get_jwt_strategy

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

    logger.info(
        "auth.google: signed in user=%s email=%s new=%s",
        user.id, email, was_created,
    )
    qs = {
        "sso_token": token,
        "sso_new": "1" if was_created else "0",
    }
    return RedirectResponse(url=_fe_landing(qs), status_code=302)
