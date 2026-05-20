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
import os
import secrets
import uuid
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

# Google sometimes returns a *superset* of the requested scopes —
# `email` / `profile` get expanded to the full `userinfo.email` /
# `userinfo.profile` URIs, and previously-granted scopes (e.g.
# `drive.readonly` from the cloud-sync flow) are tacked on when
# `include_granted_scopes=true`. The default `oauthlib` behavior is
# to refuse the token exchange when the response scope set differs
# from the request — the exact failure mode we saw as "token
# exchange failed" on the FE. Setting this env var BEFORE
# google-auth-oauthlib imports requests-oauthlib is the documented
# workaround. We still verify the id_token below so the relaxed
# scope check doesn't loosen the security posture.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.config import settings
from backend.db import SessionLocal, get_session
from backend.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth"])


# ---------- HMAC-signed state (same pattern as cloud_sync) ----------------


def _build_state(link_user_id: UUID | None = None) -> str:
    """Random nonce + HMAC. For the anonymous sign-in flow the state is
    just `<nonce>.<mac>` — no user binding because the caller is a
    not-yet-authenticated visitor. The "link an existing account" flow
    passes `link_user_id`, and the state becomes
    `link:<user_id>.<nonce>.<mac>` so the callback can tell the two
    paths apart and look up the right neuthek user without trusting
    the cookie that came back with Google's redirect (the user might
    be in a different browser session by then)."""
    nonce = secrets.token_urlsafe(16)
    if link_user_id is not None:
        payload = f"link:{link_user_id}.{nonce}"
    else:
        payload = nonce
    mac = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{mac}"


def _verify_state(state: str) -> bool:
    if not isinstance(state, str):
        return False
    # Sign-in state: `<nonce>.<mac>`.
    if state.count(".") == 1:
        nonce, mac = state.split(".", 1)
        expected = hmac.new(
            settings.jwt_secret.encode("utf-8"),
            nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, mac)
    # Link state: `link:<user_id>.<nonce>.<mac>`.
    if state.count(".") == 2 and state.startswith("link:"):
        payload, mac = state.rsplit(".", 1)
        expected = hmac.new(
            settings.jwt_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, mac)
    return False


def _extract_link_user_id(state: str) -> UUID | None:
    """Return the user_id encoded in a `link:<user_id>.<nonce>.<mac>`
    state, or None for a regular sign-in state."""
    if not state or not state.startswith("link:"):
        return None
    try:
        # state is `link:<uuid>.<nonce>.<mac>`. Split off the prefix
        # then take the first dot-separated piece.
        body = state[len("link:"):]
        uuid_str = body.split(".", 1)[0]
        return UUID(uuid_str)
    except (ValueError, IndexError):
        return None


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


# ---------- /auth/google/link (logged-in user adds Google to their account) ----


class LinkInitResponse(BaseModel):
    auth_url: str


@router.post("/link", response_model=LinkInitResponse)
async def google_link_init(
    user: Annotated[User, Depends(current_active_user)],
) -> LinkInitResponse:
    """Start the OAuth flow that attaches a Google account to the
    currently-signed-in neuthek user.

    Returns the consent-screen URL. The state encodes this user's id
    (`link:<uuid>.<nonce>.<mac>`), HMAC-signed so the callback can
    trust it without re-reading the session cookie — Google's redirect
    is a fresh browser navigation and may land in a different tab /
    profile than the one that initiated the request.

    The FE hits this with POST + Bearer auth, then does
    `window.location.href = auth_url` to send the user to Google.
    """
    flow = _google_flow()
    flow.autogenerate_code_verifier = True
    auth_url, state = flow.authorization_url(
        access_type="online",
        include_granted_scopes="true",
        prompt="select_account",
        state=_build_state(link_user_id=user.id),
    )
    if flow.code_verifier:
        await _stash_pkce_verifier(state, flow.code_verifier)
    logger.info(
        "auth.google: link init user=%s state=%s", user.id, state[:8],
    )
    return LinkInitResponse(auth_url=auth_url)


@router.delete("/link")
async def google_link_clear(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Drop the Google account binding from this user. After this they
    can't Sign in with Google to land in *this* neuthek account until
    they link again."""
    if not user.google_sub:
        return {"linked": False, "changed": False}
    user.google_sub = None
    await session.commit()
    logger.info("auth.google: unlinked user=%s", user.id)
    return {"linked": False, "changed": True}


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
    """Look up an existing user by Google sub first, then email.
    Returns (user, was_created)."""
    # Prefer `google_sub` — it's stable across email changes and gets
    # set whenever the user connects Drive (so a "I signed up with
    # password, then connected Drive, now I want to Sign in with
    # Google" flow lands in the right account).
    existing = (
        await session.execute(
            select(User).where(User.google_sub == google_sub)
        )
    ).scalar_one_or_none()
    if existing is None:
        # Fall back to email. Google's `email_verified=true` is what
        # makes this safe — without it, an attacker who registers a
        # Google account at "victim@neuthek.local" could impersonate
        # the existing user. We refuse unverified emails outright
        # before reaching this point.
        existing = (
            await session.execute(
                select(User).where(User.email == email)
            )
        ).scalar_one_or_none()
    if existing is not None:
        changed = False
        # Backfill google_sub if this is the first time we've seen one.
        if not existing.google_sub:
            existing.google_sub = google_sub
            changed = True
        # If the user existed but Google's verification is fresher,
        # mark them verified — the email_send/verify flow can be
        # skipped now.
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
        google_sub=google_sub,
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

    # Link flow: the state encoded a specific neuthek user_id, so we
    # stamp the Google sub onto THAT row instead of running the
    # sign-in find-or-create. Used by Settings → Account → "Link
    # Google account" — the user is already authenticated and just
    # wants to connect their Google identity. We refuse if the sub
    # is already attached to a *different* neuthek user.
    link_user_id = _extract_link_user_id(state)
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
                        url=f"{fe_root}/#sso_error=link_user_missing", status_code=302
                    )
                conflict = (
                    await session.execute(
                        select(User).where(
                            User.google_sub == google_sub,
                            User.id != link_user_id,
                        )
                    )
                ).scalar_one_or_none()
                if conflict is not None:
                    return RedirectResponse(
                        url=f"{fe_root}/#sso_error=google_already_linked", status_code=302
                    )
                target.google_sub = google_sub
                if not target.display_name and display_name:
                    target.display_name = display_name
                await session.commit()
            except Exception:
                logger.exception("auth.google: link failed for user=%s", link_user_id)
                return RedirectResponse(
                    url=f"{fe_root}/#sso_error=link_internal", status_code=302
                )
        logger.info(
            "auth.google: linked user=%s google_sub=%s", link_user_id, google_sub,
        )
        return RedirectResponse(
            url=f"{fe_root}/#sso_linked=1&email={email}", status_code=302,
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

    # 2FA policy for SSO (2026-05): when the user signed in with Google,
    # Google itself authenticated them — including any 2FA Google has on
    # their account. Requiring our TOTP on top would mean a 2FA-enabled
    # user clicking "Sign in with Google" gets bounced back to the
    # password screen with no clear path forward (the previous behaviour:
    # redirect to `#sso_error=totp_required`, which felt like a lockout).
    # We treat the Google sign-in itself as the second factor and skip
    # our TOTP check on this path.
    #
    # The TOTP gate STILL fires on the password login path
    # (UserManager.on_after_login in backend/auth/users.py), so users
    # who deliberately log in with their password are still required
    # to enter their code. SSO users implicitly relied on Google's
    # 2FA when they granted us their account; that's the contract.
    #
    # An audit row records the bypass so a forensic timeline can see
    # exactly when a user accessed the account via SSO vs. TOTP.
    if user.totp_enabled and user.totp_secret_enc:
        from backend.db import SessionLocal
        from backend.models import AuditLog
        try:
            async with SessionLocal() as s:
                s.add(
                    AuditLog(
                        user_id=user.id,
                        action="auth.sso.bypass_totp",
                        details={
                            "provider": "google",
                            "email": email,
                            "reason": "google_sso_second_factor",
                        },
                    )
                )
                await s.commit()
        except Exception:
            logger.exception("auth.google: audit write failed for sso/totp bypass")

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
