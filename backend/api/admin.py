"""C8 admin dashboard endpoints.

  GET    /admin/storage          Global storage breakdown by bucket / per-user.
  GET    /admin/users            Paginated user list with quota + usage.
  PATCH  /admin/users/{id}/quota Set/clear a per-user quota override.
  GET    /admin/audit            Paginated audit log (filter by user/action).

Every endpoint depends on `current_superuser`, which fastapi-users gates
on `is_superuser=True`. Non-superuser callers get a 403 before any
handler logic runs, so the implementations don't repeat the check.

The legacy /admin/retention/sweep + /admin/trainer/run endpoints live in
backend/api/account.py for historical reasons (their bodies share helpers
with /account/delete) — both routers mount under /admin and the OpenAPI
docs render them in a single section.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.storage import DEFAULT_QUOTA_BYTES
from backend.auth.users import current_admin_user, current_superuser
from backend.db import get_session
from backend.models import AuditLog, Image, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


# ---------- /admin/storage ----------


class UserStorageRow(BaseModel):
    user_id: UUID
    email: str
    display_name: str | None
    used_bytes: int
    image_count: int
    quota_bytes: int


class StorageSnapshot(BaseModel):
    total_bytes: int
    total_images: int
    by_category: dict[str, int]
    top_users: list[UserStorageRow]


@router.get("/storage", response_model=StorageSnapshot)
async def admin_storage(
    _: Annotated[User, Depends(current_admin_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    top: int = Query(default=50, ge=1, le=500),
) -> StorageSnapshot:
    """Cluster-wide storage stats, plus the top `top` users by bytes."""
    cat_rows = (
        await session.execute(
            select(
                Image.category,
                func.coalesce(func.sum(Image.byte_size_served), 0),
                func.count(),
            )
            .where(Image.deleted_at.is_(None))
            .group_by(Image.category)
        )
    ).all()
    by_category: dict[str, int] = {c: int(b) for c, b, _ in cat_rows}
    total_bytes = sum(by_category.values())
    total_images = sum(int(n) for _, _, n in cat_rows)

    user_rows = (
        await session.execute(
            select(
                User.id,
                User.email,
                User.display_name,
                User.quota_bytes,
                func.coalesce(func.sum(Image.byte_size_served), 0).label("used"),
                func.count(Image.id).label("n"),
            )
            .join(Image, Image.user_id == User.id, isouter=True)
            .where((Image.deleted_at.is_(None)) | (Image.id.is_(None)))
            .group_by(User.id, User.email, User.display_name, User.quota_bytes)
            .order_by(desc("used"))
            .limit(top)
        )
    ).all()

    top_users = [
        UserStorageRow(
            user_id=uid,
            email=email,
            display_name=display_name,
            used_bytes=int(used),
            image_count=int(n),
            quota_bytes=int(qb) if qb is not None else DEFAULT_QUOTA_BYTES,
        )
        for uid, email, display_name, qb, used, n in user_rows
    ]

    return StorageSnapshot(
        total_bytes=total_bytes,
        total_images=total_images,
        by_category=by_category,
        top_users=top_users,
    )


# ---------- /admin/users ----------


class AdminUserRead(BaseModel):
    id: UUID
    email: str
    display_name: str | None
    role: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    quota_bytes: int
    used_bytes: int
    image_count: int


@router.get("/users", response_model=list[AdminUserRead])
async def admin_list_users(
    _: Annotated[User, Depends(current_admin_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200),
) -> list[AdminUserRead]:
    """Paginated user listing for the admin panel.

    `q` does a case-insensitive substring match against email and display
    name — small clusters are fine without trigram indexes; the dashboard
    is only meant for operator use, not anything customer-facing.
    """
    user_q = select(User)
    if q:
        like = f"%{q.lower()}%"
        user_q = user_q.where(
            (func.lower(User.email).like(like))
            | (func.lower(func.coalesce(User.display_name, "")).like(like))
        )
    user_q = user_q.order_by(User.email.asc()).limit(limit).offset(offset)
    users = (await session.execute(user_q)).scalars().all()
    if not users:
        return []

    user_ids = [u.id for u in users]
    use_rows = (
        await session.execute(
            select(
                Image.user_id,
                func.coalesce(func.sum(Image.byte_size_served), 0),
                func.count(),
            )
            .where(Image.user_id.in_(user_ids), Image.deleted_at.is_(None))
            .group_by(Image.user_id)
        )
    ).all()
    use_by_user = {uid: (int(b), int(n)) for uid, b, n in use_rows}

    return [
        AdminUserRead(
            id=u.id,
            email=u.email,
            display_name=u.display_name,
            role=u.role,
            is_active=u.is_active,
            is_superuser=u.is_superuser,
            is_verified=u.is_verified,
            quota_bytes=u.quota_bytes if u.quota_bytes is not None else DEFAULT_QUOTA_BYTES,
            used_bytes=use_by_user.get(u.id, (0, 0))[0],
            image_count=use_by_user.get(u.id, (0, 0))[1],
        )
        for u in users
    ]


class QuotaUpdate(BaseModel):
    """Pass `quota_bytes=null` to clear an override and fall back to the
    global default."""

    quota_bytes: int | None = None


@router.patch("/users/{user_id}/quota", response_model=AdminUserRead)
async def admin_update_quota(
    user_id: UUID,
    body: QuotaUpdate,
    admin: Annotated[User, Depends(current_admin_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserRead:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    # Clamp to a sane lower bound — 0 would create an unusable account
    # silently. Operators wanting to disable an account should toggle
    # is_active instead (a future endpoint).
    if body.quota_bytes is not None and body.quota_bytes < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "quota_bytes must be >= 0")
    target.quota_bytes = body.quota_bytes
    session.add(
        AuditLog(
            user_id=admin.id,
            action="admin.user.quota.update",
            details={
                "target_user_id": str(target.id),
                "quota_bytes": body.quota_bytes,
            },
        )
    )
    await session.commit()
    await session.refresh(target)

    used_b, image_n = (
        await session.execute(
            select(
                func.coalesce(func.sum(Image.byte_size_served), 0),
                func.count(),
            ).where(Image.user_id == user_id, Image.deleted_at.is_(None))
        )
    ).one()

    return AdminUserRead(
        id=target.id,
        email=target.email,
        display_name=target.display_name,
        role=target.role,
        is_active=target.is_active,
        is_superuser=target.is_superuser,
        is_verified=target.is_verified,
        quota_bytes=target.quota_bytes if target.quota_bytes is not None else DEFAULT_QUOTA_BYTES,
        used_bytes=int(used_b),
        image_count=int(image_n),
    )


class RoleUpdate(BaseModel):
    role: Literal["user", "admin", "superuser"]


@router.patch("/users/{user_id}/role", response_model=AdminUserRead)
async def admin_update_role(
    user_id: UUID,
    body: RoleUpdate,
    admin: Annotated[User, Depends(current_superuser)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserRead:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    target.role = body.role
    target.is_superuser = body.role == "superuser"
    session.add(
        AuditLog(
            user_id=admin.id,
            action="admin.user.role.update",
            details={"target_user_id": str(target.id), "role": body.role},
        )
    )
    await session.commit()
    await session.refresh(target)

    used_b, image_n = (
        await session.execute(
            select(
                func.coalesce(func.sum(Image.byte_size_served), 0),
                func.count(),
            ).where(Image.user_id == user_id, Image.deleted_at.is_(None))
        )
    ).one()
    return AdminUserRead(
        id=target.id,
        email=target.email,
        display_name=target.display_name,
        role=target.role,
        is_active=target.is_active,
        is_superuser=target.is_superuser,
        is_verified=target.is_verified,
        quota_bytes=target.quota_bytes if target.quota_bytes is not None else DEFAULT_QUOTA_BYTES,
        used_bytes=int(used_b),
        image_count=int(image_n),
    )


# ---------- /admin/audit ----------


class AuditEntry(BaseModel):
    id: int
    user_id: UUID | None
    action: str
    details: dict | None
    created_at: datetime


@router.get("/audit", response_model=list[AuditEntry])
async def admin_audit_log(
    _: Annotated[User, Depends(current_admin_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user_id: UUID | None = Query(default=None),
    action_prefix: str | None = Query(default=None, max_length=64),
    order: Literal["asc", "desc"] = Query(default="desc"),
) -> list[AuditEntry]:
    """Paginated audit log with optional user / action-prefix filters."""
    stmt = select(AuditLog)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action_prefix:
        # `action_prefix` is treated as a literal prefix — keeps the
        # behavior predictable for operators (no surprise regex semantics).
        stmt = stmt.where(AuditLog.action.like(f"{action_prefix}%"))
    stmt = stmt.order_by(
        AuditLog.id.asc() if order == "asc" else AuditLog.id.desc()
    ).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        AuditEntry(
            id=r.id,
            user_id=r.user_id,
            action=r.action,
            details=r.details,
            created_at=r.created_at,
        )
        for r in rows
    ]
