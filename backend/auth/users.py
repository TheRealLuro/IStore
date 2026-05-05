import logging
import re
import uuid
from typing import Annotated, AsyncGenerator, Optional

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


PASSWORD_RULES = [
    ("at least 8 characters", lambda p: len(p) >= 8),
    ("a lowercase letter", lambda p: bool(re.search(r"[a-z]", p))),
    ("an uppercase letter", lambda p: bool(re.search(r"[A-Z]", p))),
    ("a number", lambda p: bool(re.search(r"\d", p))),
    ("a special character", lambda p: bool(re.search(r"[^A-Za-z0-9]", p))),
]


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
        if user is not None and password.lower() == (user.email or "").lower():
            raise InvalidPasswordException(reason="Password cannot match your email")

    # ---- Phase 13 (C6) transactional email hooks ----
    #
    # fastapi-users invokes these when the matching action succeeds. We
    # ask it to issue the token, then dispatch the email through
    # backend.email_send. None of these raise — a failed send must not
    # break account creation or the password-reset flow; the user can
    # always re-request a verify mail from /auth/request-verify-token.

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
                "Your IStore password was changed",
                "We're letting you know your IStore password was just "
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
