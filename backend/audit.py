from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AuditLog


async def add_audit(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    action: str,
    details: dict[str, Any] | None = None,
    flush: bool = False,
) -> AuditLog:
    row = AuditLog(user_id=user_id, action=action, details=details or {})
    session.add(row)
    if flush:
        await session.flush()
    return row
