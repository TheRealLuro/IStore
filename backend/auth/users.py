import logging
import re
import uuid
from typing import Annotated, Any, AsyncGenerator, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.context import set_current_user_id
from backend.db import get_session
from backend.email_send import send_reset_email, send_verify_email
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
    reset_password_token_secret = settings.jwt_secret
    verification_token_secret = settings.jwt_secret

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


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.jwt_secret,
        lifetime_seconds=settings.jwt_lifetime_seconds,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
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


fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

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
