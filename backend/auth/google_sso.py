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

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import COOKIE_NAME, current_active_user
from backend.config import settings
from backend.db import SessionLocal, get_session
from backend.key_derivation import (
    PURPOSE_SSO_TOTP_PENDING,
    derive_subkey,
    oauth_sso_state_key,
)
from backend.models import AuditLog, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth"])


# Allow-list of `?error=` values the /auth/google/callback handler is
# willing to echo back to the FE via `#sso_error=…`.
#
# Two sources merge here:
#   - RFC 6749 §4.1.2.1 + OIDC core §3.1.2.6 standard authorization
#     error codes (`invalid_request`, `access_denied`, ...). These are
#     what a well-behaved authorization server actually sends.
#   - The small set of internal sso_error codes the FE auth.jsx
#     handler maps to user-facing copy (`not_configured`,
#     `email_unverified`, `totp_required`, `account_inactive`). The
#     /callback path doesn't *normally* set these from Google's
#     `error` param, but pinning them in the allow-list keeps the
#     allow-list authoritative: anything the FE knows how to render
#     is allowed, anything else collapses to `unknown`.
#
# Stored as a dict so the keys are lower-cased for case-insensitive
# matching while the values are the canonical strings echoed back.
_ALLOWED_OAUTH_ERRORS: dict[str, str] = {
    code: code
    for code in (
        # RFC 6749 §4.1.2.1
        "invalid_request",
        "unauthorized_client",
        "access_denied",
        "unsupported_response_type",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable",
        # OIDC core §3.1.2.6
        "interaction_required",
        "login_required",
        "account_selection_required",
        "consent_required",
        "invalid_request_uri",
        "invalid_request_object",
        "request_not_supported",
        "request_uri_not_supported",
        "registration_not_supported",
        # FE-known internal codes (kept in sync with auth.jsx)
        "not_configured",
        "email_unverified",
        "totp_required",
        "account_inactive",
        "email_taken",
        "bad_state",
        "token_exchange_failed",
        "no_id_token",
        "bad_id_token",
        "no_email",
        "no_subject",
        "link_user_missing",
        "google_already_linked",
        "link_internal",
        "internal",
    )
}


class SsoEmailTakenError(Exception):
    """Raised when the SSO email-fallback bind would attach Google to
    a pre-existing local account that hasn't proven email ownership.

    Caught explicitly in the /auth/google/callback handler and
    surfaced as `#sso_error=email_taken` so the FE can tell the user
    the account exists but must complete verification (or link from
    inside the authenticated session via /auth/google/link) before
    they can sign in with Google. See `_is_safe_email_bind` for the
    safety predicate and audit finding CR-5 for the takeover scenario.
    """


def _is_safe_email_bind(existing: User | None) -> bool:
    """Return True iff it's safe to attach a freshly-authenticated
    Google identity to an existing local row found by email-fallback.

    The dangerous case (audit finding CR-5) is:

      1. Attacker calls POST /auth/register with the victim's address
         and an attacker-chosen password. fastapi-users creates the
         row with `is_verified=False` and the attacker's hashed
         password. The attacker never clicks the verify link.
      2. Real victim later signs in with Google.
      3. SSO callback finds the row by email, sets `google_sub`,
         flips `is_verified=True`, returns it as the authenticated
         user. The attacker's password keeps working too — silent
         account-hybrid takeover.

    Safe bindings:
      - `existing is None` — no row exists yet; the caller will
        create a fresh SSO-only row.
      - `existing.is_verified` — the user proved ownership of the
        address through the email-verify flow already.
      - `existing.hashed_password is None` — SSO-only signup row;
        the email IS the identity in this case.

    Refused binding:
      - An unverified row that DOES have a password. We cannot
        distinguish "the legitimate user just hasn't verified yet"
        from "an attacker pre-registered this address", so we err
        on the side of refusing. The user can recover by clicking
        the verify link in their inbox; once verified they can
        sign in with Google.
    """
    if existing is None:
        return True
    if existing.is_verified:
        return True
    if existing.hashed_password is None:
        return True
    return False


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
    # CR-3: HKDF-derived subkey, distinct from cloud_sync's OAuth state
    # key. Without this, an SSO state minted here was indistinguishable
    # from a cloud-sync state under the same secret — confused-deputy
    # risk if either codepath ever loosened its verifier. The keys also
    # differ from session-JWT / signed-URL / reset-token keys, so one
    # leak no longer compromises everything.
    mac = hmac.new(
        oauth_sso_state_key(),
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
            oauth_sso_state_key(),
            nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, mac)
    # Link state: `link:<user_id>.<nonce>.<mac>`.
    if state.count(".") == 2 and state.startswith("link:"):
        payload, mac = state.rsplit(".", 1)
        expected = hmac.new(
            oauth_sso_state_key(),
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
        by_email = (
            await session.execute(
                select(User).where(User.email == email)
            )
        ).scalar_one_or_none()
        # Audit finding CR-5 — pre-registration takeover. An attacker
        # who registered the victim's email but never verified would
        # otherwise have their hashed_password bound to the Google
        # identity the victim brings on first SSO sign-in. Refuse
        # the bind unless the existing row has already proven email
        # ownership (is_verified) OR was created as SSO-only
        # (hashed_password is None). See `_is_safe_email_bind`.
        if not _is_safe_email_bind(by_email):
            raise SsoEmailTakenError(email)
        existing = by_email
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


def _set_sso_session_cookie(response: Response, token: str) -> None:
    """Set the HttpOnly session cookie, mirroring CookieTransport /
    email_link._set_session_cookie. Same name/attrs as /auth/cookie/login so
    the browser ships it on the next /users/me call (same-origin prod)."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.jwt_lifetime_seconds,
        path="/",
        secure=settings.is_production,
        httponly=True,
        samesite="lax",
    )


# ---------- F1: SSO + neuthek TOTP second factor ----------
#
# When a TOTP-enabled user signs in with Google, the SSO leg alone is NOT
# sufficient — the user explicitly enabled an authenticator on THIS account, so
# we require their 6-digit code before minting a session (previously SSO
# silently bypassed it). The callback issues this short-lived, single-purpose
# HMAC pending token instead of a session; /auth/google/complete-totp redeems
# it together with a valid TOTP code. The token alone is useless without the
# live code, and it expires in 5 minutes.

_SSO_TOTP_PENDING_TTL = 300  # seconds


def _sso_totp_sig(user_id: str, exp: int) -> str:
    mac = hmac.new(
        derive_subkey(PURPOSE_SSO_TOTP_PENDING),
        f"{user_id}.{exp}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def mint_sso_totp_pending(user_id: UUID) -> str:
    exp = int(time.time()) + _SSO_TOTP_PENDING_TTL
    uid = str(user_id)
    return f"{uid}.{exp}.{_sso_totp_sig(uid, exp)}"


def verify_sso_totp_pending(token: str) -> UUID | None:
    """Return the bound user id if the pending token is well-formed, unexpired,
    and the HMAC verifies; else None."""
    if not token or token.count(".") < 2:
        return None
    try:
        uid, exp_str, sig = token.rsplit(".", 2)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return None
    if exp < int(time.time()):
        return None
    if not hmac.compare_digest(sig, _sso_totp_sig(uid, exp)):
        return None
    try:
        return UUID(uid)
    except ValueError:
        return None


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
        # CodeQL "URL redirection from remote source": the `error` query
        # param is set by Google (or whoever else hits the callback URL),
        # so we cannot trust its contents. The fragment lives after `#`
        # which sandboxes the value from path/host, but an attacker
        # could still slip CRLFs, escape sequences, or oversize garbage
        # in to confuse downstream proxies + log scrapers. Pin to the
        # RFC 6749 §4.1.2.1 / OIDC standard codes plus the small set of
        # FE-known sso_error strings; anything else collapses to
        # `unknown` and the raw value is logged for diagnostics.
        sanitized = _ALLOWED_OAUTH_ERRORS.get(error.lower(), "unknown")
        if sanitized == "unknown":
            logger.warning(
                "auth.google: callback received non-allowlisted error code (logged but not echoed): %r",
                error[:64],
            )
        return RedirectResponse(
            url=f"{fe_root}/#sso_error={sanitized}", status_code=302
        )
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
        except SsoEmailTakenError:
            # CR-5 — email-fallback refused on an unverified
            # password-bearing row. The FE renders the generic
            # `Google sign-in failed (email_taken)` message; users
            # who legitimately want to link Google to an existing
            # account can sign in with their password and use
            # /auth/google/link from settings instead.
            logger.info(
                "auth.google: email-takeover bind refused for %s (unverified existing account)",
                email,
            )
            return RedirectResponse(
                url=f"{fe_root}/#sso_error=email_taken", status_code=302
            )
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

    # F1 — 2FA policy for SSO. A TOTP-enabled user explicitly put an
    # authenticator on THIS neuthek account, so the Google sign-in alone is NOT
    # sufficient: we require their 6-digit code before minting a session.
    # (Previously SSO silently bypassed neuthek TOTP and leaned entirely on
    # Google's own 2FA — an MFA-downgrade if the Google account had weak/no
    # 2FA or a live session on a shared device.) Instead of a session we mint a
    # short-lived, single-purpose pending token and bounce the SPA to a TOTP
    # step; the session is only issued by /auth/google/complete-totp once the
    # code verifies.
    if user.totp_enabled and user.totp_secret_enc:
        pending = mint_sso_totp_pending(user.id)
        try:
            async with SessionLocal() as s:
                s.add(
                    AuditLog(
                        user_id=user.id,
                        action="auth.sso.totp_challenge",
                        details={"provider": "google", "email": email},
                    )
                )
                await s.commit()
        except Exception:
            logger.exception("auth.google: audit write failed for sso totp challenge")
        logger.info("auth.google: totp challenge issued user=%s email=%s", user.id, email)
        qs = {
            "sso_totp": pending,
            "sso_new": "1" if was_created else "0",
        }
        return RedirectResponse(url=_fe_landing(qs), status_code=302)

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
    resp = RedirectResponse(url=_fe_landing(qs), status_code=302)
    # Also set the HttpOnly session cookie so /users/me works on same-origin
    # prod without relying solely on the fragment token.
    _set_sso_session_cookie(resp, token)
    return resp


# ---------- F1: SSO TOTP completion ----------


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
    """Second leg of a TOTP-gated Google sign-in.

    The callback redirected the SPA with `#sso_totp=<pending>`; the SPA collects
    the user's 6-digit code and POSTs it here. We re-verify the pending token
    (proves the Google leg completed for THIS user, unexpired) AND the TOTP
    code, then mint the real session. Listed in `_AUTH_PATHS` so failed code
    guesses count toward per-IP + per-identity lockout — the pending token's
    1M-space code can't be brute-forced.
    """
    # Lazy imports mirror the callback's pattern + avoid import cycles.
    from backend.api.two_factor import _verify_code
    from backend.auth.users import get_jwt_strategy
    from backend.secret_box import decrypt

    user_id = verify_sso_totp_pending(body.pending)
    if user_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This sign-in step expired — start the Google sign-in again.",
        )
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid sign-in.")
    if not user.totp_enabled or not user.totp_secret_enc:
        # 2FA was disabled between the callback and now — nothing to verify;
        # refuse rather than silently letting the pending token mint a session.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Two-factor is no longer enabled — sign in again.",
        )
    secret = decrypt(user.totp_secret_enc)
    if not _verify_code(secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid 2FA code.")

    from datetime import datetime, timezone

    user.totp_verified_at = datetime.now(timezone.utc)
    session.add(
        AuditLog(
            user_id=user.id,
            action="auth.sso.totp_completed",
            details={"provider": "google"},
        )
    )
    await session.commit()

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    _set_sso_session_cookie(response, token)
    logger.info("auth.google: totp-gated sign-in completed user=%s", user.id)
    return SsoTotpCompleteResponse(sso_token=token)
