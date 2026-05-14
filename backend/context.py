from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID


_current_user_id: ContextVar[str | None] = ContextVar(
    "neuthek_current_user_id", default=None
)


def set_current_user_id(user_id: UUID | str | None):
    return _current_user_id.set(str(user_id) if user_id is not None else None)


def reset_current_user_id(token) -> None:
    _current_user_id.reset(token)


def get_current_user_id() -> str | None:
    return _current_user_id.get()
