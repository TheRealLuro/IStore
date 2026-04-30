import re
import uuid
from typing import AsyncGenerator, Optional

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db import get_session
from backend.models import User


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

current_active_user = fastapi_users.current_user(active=True)
