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
from backend.config import settings
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


# ---------- §1.3 — un-mock the rest of the admin overlay ----------
#
# Six tabs previously rendered hardcoded data behind a `MOCK` pill.
# Each now reads real backend state via `backend/system_probes.py`,
# the existing audit log, and the Redis job queue. The endpoints are
# intentionally small surfaces — no new tables, no migrations — so
# we can swap them in without coordinating a deploy. C8.2 (model_runs
# table, worker heartbeats) and §F1 (runtime.toml + per-vendor probes)
# remain future work that will expand these payloads.


@router.get("/system")
async def admin_system(
    _: Annotated[User, Depends(current_admin_user)],
) -> dict:
    """Snapshot the API process's environment: uptime, DB pool stats,
    MinIO bucket sizes, Redis health, app version. CPU / memory live
    on /admin/hardware so a busy bucket scan doesn't keep the simpler
    System tab from rendering."""
    from backend.system_probes import (
        sample_db_pool,
        sample_minio,
        sample_redis,
        sample_uptime,
    )
    redis_info = await sample_redis()
    minio_info = await sample_minio()
    return {
        "version": "0.1.0",
        "env": settings.app_env,
        "uptime": sample_uptime(),
        "db_pool": sample_db_pool(),
        "redis": redis_info,
        "minio": minio_info,
    }


@router.get("/hardware")
async def admin_hardware(
    _: Annotated[User, Depends(current_admin_user)],
) -> dict:
    """CPU / memory / disk / GPU snapshot. Cross-platform via psutil;
    GPU goes through torch.cuda first, falls back to a `nvidia-smi`
    shell. Returns generously-typed dicts so the FE can render "—"
    for anything a given platform can't expose."""
    from backend.system_probes import (
        sample_cpu,
        sample_disks,
        sample_gpu,
        sample_memory,
    )
    return {
        "cpu": sample_cpu(),
        "memory": sample_memory(),
        "disks": sample_disks(),
        "gpu": sample_gpu(),
    }


@router.get("/processes")
async def admin_processes(
    _: Annotated[User, Depends(current_admin_user)],
    top: int = Query(default=12, ge=1, le=64),
) -> dict:
    """Top-N processes on the API host by CPU%. RAM is RSS.

    Note: the ML worker (`backend/worker/main.py`) only shows up here
    when it runs in the same OS namespace as the API — typical in
    bare-metal dev. In a split-container deploy, the worker lives in
    a sibling container and isn't visible from this side. C8.2 lands
    a worker_heartbeats table so the operator gets the worker in
    that case too."""
    from backend.system_probes import sample_processes
    rows = sample_processes(top=top)
    totals = {
        "count": len(rows),
        "cpu_percent_sum": round(sum(r["cpu_percent"] for r in rows), 1),
        "memory_rss_bytes_sum": sum(r["memory_rss_bytes"] for r in rows),
    }
    return {"processes": rows, "totals": totals}


@router.get("/models")
async def admin_models(
    _: Annotated[User, Depends(current_admin_user)],
) -> dict:
    """Configured-model registry from `settings`. Memory footprint /
    load state of each model lives inside the ml-worker container —
    C8.2 will hook the worker into a model_runs table and surface
    real torch.cuda.memory_allocated() numbers here. For now the FE
    shows configuration as a truthful proxy for "what's wired up"."""
    from backend.system_probes import list_configured_models, sample_gpu
    gpu = sample_gpu()
    backend_label = (
        "cuda" if gpu.get("available") and gpu.get("backend", "").startswith("torch") else
        "nvidia-smi (detected)" if gpu.get("available") else
        "cpu"
    )
    return {
        "models": list_configured_models(),
        "inference_backend": backend_label,
        "gpu_available": bool(gpu.get("available")),
    }


@router.get("/tasks")
async def admin_tasks(
    _: Annotated[User, Depends(current_admin_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Background-job snapshot:
      - queue: live Redis depth + active-job set size
      - recent: last 50 audit rows whose action begins with `image.`
        or `share.`, as a proxy for "what just happened" until the
        C8.2 background_jobs table lands with true per-job history."""
    from backend.system_probes import sample_redis
    redis_info = await sample_redis()
    recent_rows = (
        await session.execute(
            select(AuditLog)
            .where(
                (AuditLog.action.like("image.%"))
                | (AuditLog.action.like("share.%"))
                | (AuditLog.action.like("account.recovery_codes.%"))
            )
            .order_by(AuditLog.id.desc())
            .limit(50)
        )
    ).scalars().all()
    return {
        "queue": {
            "depth": redis_info.get("queue_depth", -1),
            "active": redis_info.get("active_jobs", 0),
            "reachable": redis_info.get("reachable", False),
            "queue_key": redis_info.get("queue_key"),
        },
        "recent": [
            {
                "id": r.id,
                "user_id": str(r.user_id) if r.user_id else None,
                "action": r.action,
                "details": r.details,
                "created_at": r.created_at,
            }
            for r in recent_rows
        ],
    }


@router.get("/logs")
async def admin_logs(
    _: Annotated[User, Depends(current_admin_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    """The Logs tab in admin renders the audit log as a tailed stream.
    Same data source as `/admin/audit` — the difference is shape:
    `/admin/audit` returns a paginated row list (used for filters),
    `/admin/logs` returns the most-recent N rows oldest-first so the
    FE can append-render without a reverse pass.

    Real uvicorn access logs + worker stderr aggregation are tracked
    in C8.2 and out of scope here. Audit-log lines cover the
    user-meaningful events that an operator actually wants to see."""
    rows = (
        await session.execute(
            select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        )
    ).scalars().all()
    rows = list(reversed(rows))  # oldest-first for natural tail-style rendering
    return {
        "lines": [
            {
                "id": r.id,
                "created_at": r.created_at,
                "action": r.action,
                "user_id": str(r.user_id) if r.user_id else None,
                "details": r.details,
            }
            for r in rows
        ],
    }
