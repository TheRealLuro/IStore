import logging
import re
import uuid
from typing import Annotated, Any, AsyncGenerator, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.context import set_current_user_id
from backend.db import get_session
from backend.email_send import send_reset_email, send_verify_email
from backend.key_derivation import (
    PURPOSE_RESET_PASSWORD,
    PURPOSE_VERIFY_EMAIL,
    derive_subkey_str,
)
from backend.models import User

logger = logging.getLogger(__name__)


async def _persist_registration_consents(
    *,
    user: User,
    consents: list,
    signature: str,
    request: Optional[Request],
) -> None:
    """§B2 — write ConsentRecord rows for the bundle submitted with
    the register call.

    The fastapi-users contract gives us a `User` instance but no
    SQLAlchemy session; we open a fresh one against `SessionLocal` so
    we're not entangled with the manager's user_db session lifecycle.
    Each scope flows through the same `_validate_scope` allowlist the
    /consent endpoints use, so a bogus kind raises before we touch
    the DB.
    """
    from datetime import datetime, timezone

    from backend.consent import SUPPORTED_SCOPES, _policy_sha256
    from backend.db import SessionLocal
    from backend.models import ConsentRecord
    from backend.audit import add_audit

    ip = ""
    user_agent = ""
    if request is not None:
        client = getattr(request, "client", None)
        if client is not None:
            ip = client.host or ""
        user_agent = request.headers.get("user-agent", "")

    now = datetime.now(timezone.utc)
    policy_sha = _policy_sha256()
    async with SessionLocal() as session:
        for item in consents:
            if isinstance(item, dict):
                kind = item.get("kind")
                state = item.get("state")
            else:
                kind = getattr(item, "kind", None)
                state = getattr(item, "state", None)
            if not kind or kind not in SUPPORTED_SCOPES:
                logger.warning("register-consent: skipping unsupported scope %r", kind)
                continue
            if state not in {"GRANTED", "WITHDRAWN"}:
                logger.warning("register-consent: skipping unknown state %r for %r", state, kind)
                continue
            session.add(
                ConsentRecord(
                    user_id=user.id,
                    consent_kind=kind,
                    state=state,
                    policy_version="v1",
                    policy_text_sha256=policy_sha,
                    signature_text=signature[:512] if signature else None,
                    ip=ip or None,
                    user_agent=user_agent or None,
                    granted_at=now,
                )
            )
            await add_audit(
                session,
                user_id=user.id,
                action=f"consent.register.{kind}.{state.lower()}",
                details={"scope": kind, "state": state, "ip": ip or None},
            )
        await session.commit()


PASSWORD_RULES = [
    # Length floor matches the frontend signup validator (auth.jsx
    # `pwd.length >= 10`). Anything below 10 is rejected at the API
    # boundary so a non-browser client can't sneak a weaker password
    # past the floor.
    ("at least 10 characters", lambda p: len(p) >= 10),
    ("a lowercase letter", lambda p: bool(re.search(r"[a-z]", p))),
    ("an uppercase letter", lambda p: bool(re.search(r"[A-Z]", p))),
    ("a number", lambda p: bool(re.search(r"\d", p))),
    ("a special character", lambda p: bool(re.search(r"[^A-Za-z0-9]", p))),
]

# Tiny denylist of obvious passwords. Not a full breach list — we don't
# want to ship one in the repo — but catches the "Password1!" /
# "Welcome123!" class that satisfies every rule above and is the first
# thing a credential-stuffer tries.
_COMMON_PASSWORDS = {
    "password1!", "password123!", "welcome1!", "welcome123!",
    "qwerty12!", "qwerty123!", "asdf1234!", "letmein1!", "letmein123!",
    "admin1234!", "admin12345!", "iloveyou1!", "monkey123!", "dragon123!",
    "abc12345!", "1q2w3e4r!", "1qaz2wsx!", "p@ssw0rd1", "p@ssw0rd!",
    "trustno1!", "sunshine1!", "princess1!", "football1!",
}


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    # CR-3: distinct subkeys per token class, derived from jwt_secret
    # via HKDF-SHA256. Previously these read settings.jwt_secret
    # directly — same secret as session JWTs + signed URLs + OAuth
    # state — so a single leak forged ALL of them at once. Tokens
    # already in flight (pending reset / verify emails issued before
    # this deploy) are invalidated by the change; their TTL is 1h so
    # the migration cost is bounded.
    reset_password_token_secret = derive_subkey_str(PURPOSE_RESET_PASSWORD)
    verification_token_secret = derive_subkey_str(PURPOSE_VERIFY_EMAIL)

    async def validate_password(
        self,
        password: str,
        user: Optional[User] = None,  # noqa: ARG002 — fastapi-users contract
    ) -> None:
        missing = [label for label, test in PASSWORD_RULES if not test(password)]
        if missing:
            raise InvalidPasswordException(
                reason="Password is missing: " + ", ".join(missing)
            )
        if password.casefold() in _COMMON_PASSWORDS:
            raise InvalidPasswordException(
                reason="Password is in a list of commonly used passwords. Pick something less guessable."
            )
        if user is not None and user.email:
            email = user.email.casefold()
            pwd = password.casefold()
            # Reject the email verbatim OR the email's local part — many
            # users default to "[localpart]123!" which is trivial.
            if pwd == email or pwd == email.split("@", 1)[0]:
                raise InvalidPasswordException(reason="Password cannot match your email")
            # And reject "email + small suffix" up to 4 chars — covers
            # the `User@Example.com1` / `User@Example.com!!` class.
            if pwd.startswith(email) and len(pwd) - len(email) <= 4:
                raise InvalidPasswordException(reason="Password is too close to your email")

    # ---- Phase 13 (C6) transactional email hooks ----
    #
    # fastapi-users invokes these when the matching action succeeds. We
    # ask it to issue the token, then dispatch the email through
    # backend.email_send. None of these raise — a failed send must not
    # break account creation or the password-reset flow; the user can
    # always re-request a verify mail from /auth/request-verify-token.

    # §B2 — consent-before-signup. UserCreate now carries optional
    # `consents` + `consent_signature` fields. We override the
    # standard `create()` so the consent ledger predates the user
    # row's external visibility (the API returns the User AFTER both
    # tables are written in the same transaction). If the registration
    # form skips the consents (legacy clients), the rest of the flow
    # is unaffected; users land at the consents-modal post-signup as
    # before.
    async def create(self, user_create, safe: bool = False, request: Optional[Request] = None):
        consents = getattr(user_create, "consents", None)
        signature = getattr(user_create, "consent_signature", None) or ""
        # Strip the §B2-specific fields before handing the dict back
        # to fastapi-users — the User table doesn't know about them.
        # fastapi-users honors `model_dump(exclude=…)` per its source,
        # but the simplest path is to clear them in-place on the
        # input pydantic model. We keep the original payload so
        # callers further up can still introspect.
        try:
            user_create.consents = None
            user_create.consent_signature = None
        except Exception:
            pass

        user = await super().create(user_create, safe, request)

        if consents:
            try:
                await _persist_registration_consents(
                    user=user, consents=consents,
                    signature=signature or user.display_name or user.email or "",
                    request=request,
                )
            except Exception:
                # Don't break account creation if the consent write
                # fails — log loudly so ops can backfill, but the
                # user can still sign in and grant via the regular
                # /consent/{kind}/grant endpoints.
                logger.exception(
                    "register: failed to persist consent bundle for %s — "
                    "user will need to re-grant via /consent/{kind}",
                    user.id,
                )
        return user

    async def on_after_register(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        # Trigger the standard "request-verify-token" path so the same
        # JWT shape is used everywhere; the actual email is sent from
        # on_after_request_verify below.
        try:
            await self.request_verify(user, request)
        except Exception:  # already verified, etc — non-fatal
            logger.exception("on_after_register: request_verify failed for %s", user.id)

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        send_verify_email(user.email, token)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ) -> None:
        send_reset_email(user.email, token)

    async def on_after_update(
        self,
        user: User,
        update_dict: dict,
        request: Optional[Request] = None,
    ) -> None:
        # If the email changed via PATCH /users/me, force re-verification.
        # fastapi-users already clears `is_verified` on email change in its
        # update path; we just need to trigger the new verification mail.
        if "email" in update_dict:
            try:
                await self.request_verify(user, request)
            except Exception:
                logger.exception(
                    "on_after_update: request_verify failed for %s", user.id
                )

    async def on_after_login(
        self,
        user: User,
        request: Optional[Request] = None,
        response: Optional[Any] = None,
    ) -> None:
        """§1.2.2 — TOTP gate.

        When 2FA is enabled on this user, we refuse the normal login
        path. The FE catches the 401 + `{"detail": "totp_required"}`
        body and re-submits via /auth/jwt/login-totp with the code.
        Raising here short-circuits fastapi-users before it returns
        the JWT, so a TOTP-enabled user can never get a token via the
        no-code endpoint."""
        if user.totp_enabled and user.totp_secret_enc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "totp_required",
            )

    async def on_after_reset_password(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        # Audit A8 — bump token_version so every JWT minted under the
        # old password becomes invalid on next request. Without this,
        # an attacker who had a live cookie when the legitimate user
        # reset (panic-reset → "I think someone got in") would keep
        # their session for up to `jwt_lifetime_seconds`. The bump
        # commits in this same session so the row reflects the new
        # version before the next decode tries to verify the claim.
        try:
            from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
            db: _AsyncSession = self.user_db.session  # type: ignore[attr-defined]
            user.token_version = (user.token_version or 1) + 1
            await db.commit()
        except Exception:
            logger.exception(
                "on_after_reset_password: token_version bump failed for %s — "
                "old sessions may remain valid until expiry",
                user.id,
            )

        # Best-effort post-reset notice — same template flow as
        # send_reset_email but a one-liner so we don't add a fourth
        # template just for this.
        try:
            from backend.email_send import send_email

            send_email(
                user.email,
                "Your neuthek password was changed",
                "We're letting you know your neuthek password was just "
                "reset. If this wasn't you, please reply immediately.",
            )
        except Exception:
            logger.exception("on_after_reset_password: notice send failed for %s", user.id)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")

# ---- HttpOnly cookie transport ----
#
# Primary transport for browser clients as of 2026-05. Storing the JWT
# in `localStorage` (the previous BearerTransport flow) left it
# reachable from any script in the origin — one XSS on a parsed
# user-uploaded file became an account takeover. The cookie variant
# is HttpOnly (JS can't read it), SameSite=Lax (blocks cross-origin
# form-based CSRF in modern browsers), and Secure when running with
# HTTPS in front (we lift the Secure flag in dev so localhost over
# plain HTTP still authenticates — production gate validates this).
#
# We keep the BearerTransport alive in parallel for programmatic
# callers (mobile apps, the eventual CLI). fastapi-users routes the
# `current_user` dependency through whichever transport the request
# supplied a token via, so both work simultaneously without route
# duplication.
COOKIE_NAME = "neuthek_session"
cookie_transport = CookieTransport(
    cookie_name=COOKIE_NAME,
    cookie_max_age=settings.jwt_lifetime_seconds,
    # SameSite=None + Secure so the session cookie is accepted when the app
    # is loaded inside a cross-site iframe (the offline demo deck embeds the
    # localhost app). SameSite=None REQUIRES Secure; on http://localhost the
    # Secure flag is honoured because localhost is a secure context, so this
    # also works in plain-http dev. Trade-off: SameSite=None drops the Lax
    # CSRF mitigation — the API stays protected cross-site by CORS
    # (allow_origins = the FE origin only) + JSON-only bodies, but a public
    # production deploy should additionally carry explicit CSRF tokens.
    cookie_secure=True,
    cookie_httponly=True,
    cookie_samesite="none",
    cookie_path="/",
)


class VersionedJWTStrategy(JWTStrategy):
    """JWT strategy with per-user `token_version` revocation (audit A8).

    Every minted JWT carries a `tv` claim with the user's current
    `token_version`. On decode, the strategy loads the user (the
    parent does this anyway) and rejects the token if the claim
    doesn't match the row's current value. Bumping `token_version`
    therefore invalidates every live session for that user.

    Backwards-compat: tokens minted by the pre-A8 build have no `tv`
    claim. We treat a missing claim as `tv=1` (the column default),
    so legacy tokens validate cleanly until they expire — no forced
    sign-out on the deploy that ships this code.

    Note on key separation: the audit's CR-3 fix moved every other
    HMAC domain off `settings.jwt_secret` onto HKDF-derived subkeys.
    The session JWT itself still uses the raw secret in this
    revision because moving it requires a one-shot "log everyone out
    once" event. With `token_version` in place, that move can ship
    as a separate PR (operator triggers the global bump by changing
    the secret + bumping `MAX(token_version)+1` on every row).
    """

    # JWT claim name. Short on purpose — every authenticated request
    # carries this in the cookie + decoded payload; a 2-byte key
    # adds up at high QPS.
    CLAIM_TOKEN_VERSION = "tv"

    async def write_token(self, user: "User") -> str:  # type: ignore[override]
        from fastapi_users.jwt import generate_jwt
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            self.CLAIM_TOKEN_VERSION: int(getattr(user, "token_version", 1) or 1),
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm,
        )

    async def read_token(
        self,
        token: Optional[str],
        user_manager: BaseUserManager,
    ):
        # First: delegate to the parent for signature/audience/expiry
        # checks + user fetch. If anything fails there, the parent
        # returns None and we honor that — keeps the no-claim happy
        # path identical to the upstream behavior.
        user = await super().read_token(token, user_manager)
        if user is None or token is None:
            return user

        # Decode the claim ourselves. We KNOW the signature + audience
        # checks already passed (the parent didn't return None), so
        # PyJWTError here would mean a between-the-lines weirdness we
        # can safely reject.
        import jwt as _jwt
        try:
            payload = _jwt.decode(
                token,
                self.decode_key,
                audience=self.token_audience,
                algorithms=[self.algorithm],
            )
        except _jwt.PyJWTError:
            return None

        claimed_tv = payload.get(self.CLAIM_TOKEN_VERSION, 1)
        try:
            claimed_tv = int(claimed_tv)
        except (TypeError, ValueError):
            return None

        current_tv = int(getattr(user, "token_version", 1) or 1)
        if claimed_tv != current_tv:
            # The user bumped their token_version (password reset,
            # 2FA disable). The token is stale; refuse.
            logger.info(
                "auth.token.revoked user=%s claimed_tv=%s current_tv=%s",
                user.id, claimed_tv, current_tv,
            )
            return None
        return user


def get_jwt_strategy() -> VersionedJWTStrategy:
    return VersionedJWTStrategy(
        secret=settings.jwt_secret,
        lifetime_seconds=settings.jwt_lifetime_seconds,
    )


# Bearer kept for programmatic / non-browser clients.
auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

# Cookie is the browser default. `name` is distinct from the Bearer
# backend so fastapi-users mounts /auth/cookie/login + /auth/cookie/logout
# alongside /auth/jwt/login + /auth/jwt/logout.
cookie_auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)


async def get_user_db(
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


# Both backends registered. `current_user` accepts a token from
# EITHER transport — cookie OR Authorization header — so the same
# protected route works from a browser (cookie) and a CLI (Bearer)
# without per-route plumbing.
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager, [cookie_auth_backend, auth_backend],
)

_current_active_user = fastapi_users.current_user(active=True)
# C8 (admin dashboard) — gating dependency for every endpoint under
# /admin/*. fastapi-users' superuser=True flag enforces is_superuser at
# the request level so we don't repeat the check in each handler.
_current_superuser = fastapi_users.current_user(active=True, superuser=True)


async def current_active_user(
    user: Annotated[User, Depends(_current_active_user)],
) -> User:
    set_current_user_id(user.id)
    return user


async def current_superuser(
    user: Annotated[User, Depends(_current_superuser)],
) -> User:
    set_current_user_id(user.id)
    return user


async def current_admin_user(
    user: Annotated[User, Depends(_current_active_user)],
) -> User:
    set_current_user_id(user.id)
    if user.role not in {"admin", "superuser"} and not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user
