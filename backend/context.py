from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID


_current_user_id: ContextVar[str | None] = ContextVar(
    "neuthek_current_user_id", default=None
)

# Explicit RLS-bypass flag for TRUSTED cross-user maintenance paths that
# legitimately span tenants (the Stripe webhook keyed on a Stripe customer id,
# admin/retention sweepers). When set, db.py's `after_begin` listener stamps
# `SET LOCAL app.rls_bypass='on'` on EVERY transaction the session opens — so
# the bypass survives the multiple commits these jobs do, WITHOUT leaking onto
# pooled connections (the GUC is transaction-local and the ContextVar is
# task/request-local). NEVER set this from request input — only from internal,
# audited code paths that cannot resolve a single owning user.
_rls_bypass: ContextVar[bool] = ContextVar("neuthek_rls_bypass", default=False)


def set_current_user_id(user_id: UUID | str | None):
    return _current_user_id.set(str(user_id) if user_id is not None else None)


def reset_current_user_id(token) -> None:
    _current_user_id.reset(token)


def get_current_user_id() -> str | None:
    return _current_user_id.get()


def set_rls_bypass(on: bool = True):
    return _rls_bypass.set(bool(on))


def reset_rls_bypass(token) -> None:
    _rls_bypass.reset(token)


def get_rls_bypass() -> bool:
    return _rls_bypass.get()
