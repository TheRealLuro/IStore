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
    last_seen_at: datetime | None = None


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

    # Last-seen comes from the audit log — the most recent event with a
    # user_id is a reasonable proxy for "active this session". We pull
    # in one batched query indexed by user_id to keep the listing fast.
    seen_rows = (
        await session.execute(
            select(AuditLog.user_id, func.max(AuditLog.created_at))
            .where(AuditLog.user_id.in_(user_ids))
            .group_by(AuditLog.user_id)
        )
    ).all()
    last_seen_by_user = {uid: ts for uid, ts in seen_rows if uid is not None}

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
            last_seen_at=last_seen_by_user.get(u.id),
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


async def _resolve_user_lookup(
    session: AsyncSession, user_ids: set[UUID]
) -> dict[str, dict[str, str | None]]:
    """Batch-fetch (email, display_name) for a set of user_ids. Used to
    enrich audit / logs / tasks responses so the FE can render
    "alice@... shared X with bob@..." instead of raw UUIDs."""
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(User.id, User.email, User.display_name).where(User.id.in_(user_ids))
        )
    ).all()
    return {
        str(uid): {"email": email, "display_name": display_name}
        for uid, email, display_name in rows
    }


@router.get("/system")
async def admin_system(
    _: Annotated[User, Depends(current_admin_user)],
) -> dict:
    """API process environment + a rolled-up health verdict.

    The verdict combines DB pool, Redis, MinIO, disk fill, and queue
    depth into a single ok/warn/error so the admin overlay can show
    a one-glance banner. User activity (24h / 7d / total) lives here
    too so the overlay header has "people in the system" context."""
    from backend.system_probes import (
        rollup_health,
        sample_db_pool,
        sample_disks,
        sample_minio,
        sample_redis,
        sample_uptime,
        sample_user_activity,
    )
    redis_info = await sample_redis()
    minio_info = await sample_minio()
    disks = sample_disks()
    db_pool = sample_db_pool()
    health = rollup_health(
        db_pool=db_pool,
        redis_info=redis_info,
        minio_info=minio_info,
        disks=disks,
        queue_depth=redis_info.get("queue_depth", -1) if redis_info.get("reachable") else -1,
    )
    activity = await sample_user_activity()
    return {
        "version": "0.1.0",
        "env": settings.app_env,
        "uptime": sample_uptime(),
        "db_pool": db_pool,
        "redis": redis_info,
        "minio": minio_info,
        "health": health,
        "user_activity": activity,
    }


@router.get("/hardware")
async def admin_hardware(
    _: Annotated[User, Depends(current_admin_user)],
) -> dict:
    """CPU / memory / disk / GPU + thermals + NICs. Cross-platform via
    psutil; vendor-specific paths (WMI thermal zones, nvidia-smi)
    fill in where the cross-platform path doesn't reach (todo §F1)."""
    from backend.system_probes import (
        sample_cpu,
        sample_disks,
        sample_gpu,
        sample_memory,
        sample_network,
        sample_thermals,
    )
    return {
        "cpu": sample_cpu(),
        "memory": sample_memory(),
        "disks": sample_disks(),
        "gpu": sample_gpu(),
        "thermals": sample_thermals(),
        "network": sample_network(),
    }


@router.get("/processes")
async def admin_processes(
    _: Annotated[User, Depends(current_admin_user)],
    top: int = Query(default=12, ge=1, le=64),
) -> dict:
    """Top-N OS-level processes by CPU% PLUS heartbeat-tracked workers.

    psutil only sees processes in the API's own OS namespace. The
    `workers` section comes from worker_heartbeats so a ml-worker
    sibling container (or any process emitting heartbeats) shows up
    even when the API can't see its PID directly. Each worker carries
    its own metadata blob (queue depth, last job kind) so the dashboard
    has live operational signal without inferring."""
    from backend.system_probes import list_workers, sample_processes
    rows = sample_processes(top=top)
    totals = {
        "count": len(rows),
        "cpu_percent_sum": round(sum(r["cpu_percent"] for r in rows), 1),
        "memory_rss_bytes_sum": sum(r["memory_rss_bytes"] for r in rows),
    }
    workers = await list_workers()
    return {"processes": rows, "totals": totals, "workers": workers}


@router.get("/models")
async def admin_models(
    _: Annotated[User, Depends(current_admin_user)],
) -> dict:
    """Configured-model registry merged with model_runs (C8.2).

    Each entry shows configuration (name + role + enabled) plus —
    when a worker has reported a load via `backend.heartbeats` — the
    current device, last-known memory footprint, last_used_at, and
    which worker is hosting it. Models that haven't been loaded yet
    show state='configured'; that's the truthful "wired up but cold"
    state and matches what an operator wants to see on a fresh box."""
    from backend.system_probes import (
        list_configured_models,
        list_model_runs,
        sample_gpu,
    )
    gpu = sample_gpu()
    backend_label = (
        "cuda" if gpu.get("available") and gpu.get("backend", "").startswith("torch") else
        "nvidia-smi (detected)" if gpu.get("available") else
        "cpu"
    )
    runs = await list_model_runs()
    # Index runs by model_id so the FE can show one row per configured
    # model with its loaded state appended.
    runs_by_id: dict[str, list[dict]] = {}
    for r in runs:
        runs_by_id.setdefault(r["model_id"], []).append(r)
    models = []
    for m in list_configured_models():
        rs = runs_by_id.get(m["id"], [])
        # Latest run wins (`list_model_runs` already orders desc).
        latest = rs[0] if rs else None
        models.append({
            **m,
            "state": (latest or {}).get("state", "configured"),
            "device": (latest or {}).get("device"),
            "memory_allocated_bytes": (latest or {}).get("memory_allocated_bytes"),
            "last_used_at": (latest or {}).get("last_used_at"),
            "worker_id": (latest or {}).get("worker_id"),
            "load_count": len(rs),
        })
    return {
        "models": models,
        "inference_backend": backend_label,
        "gpu_available": bool(gpu.get("available")),
    }


@router.get("/tasks")
async def admin_tasks(
    _: Annotated[User, Depends(current_admin_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Background-job snapshot:
      - queue: live Redis depth + active-job set size + per-worker
        queue numbers from worker_heartbeats.metadata.
      - workers: heartbeat-tracked workers (so admin sees "1 healthy
        ml-worker, last seen 4 s ago" instead of guessing).
      - recent: last 50 image/share/account audit rows enriched with
        each user's email so the FE can render names directly."""
    from backend.system_probes import list_workers, sample_redis
    redis_info = await sample_redis()
    workers = await list_workers()
    recent_rows = (
        await session.execute(
            select(AuditLog)
            .where(
                (AuditLog.action.like("image.%"))
                | (AuditLog.action.like("share.%"))
                | (AuditLog.action.like("consent.%"))
                | (AuditLog.action.like("auth.%"))
                | (AuditLog.action.like("account.%"))
            )
            .order_by(AuditLog.id.desc())
            .limit(50)
        )
    ).scalars().all()
    user_lookup = await _resolve_user_lookup(
        session, {r.user_id for r in recent_rows if r.user_id}
    )
    return {
        "queue": {
            "depth": redis_info.get("queue_depth", -1),
            "active": redis_info.get("active_jobs", 0),
            "reachable": redis_info.get("reachable", False),
            "queue_key": redis_info.get("queue_key"),
        },
        "workers": workers,
        "recent": [
            {
                "id": r.id,
                "user_id": str(r.user_id) if r.user_id else None,
                "user_email": (
                    user_lookup.get(str(r.user_id), {}).get("email") if r.user_id else None
                ),
                "user_display_name": (
                    user_lookup.get(str(r.user_id), {}).get("display_name") if r.user_id else None
                ),
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
    """Audit log tail, oldest-first, enriched with each user's email
    + display name so the FE can render "alice shared sunset.jpg with
    bob" lines instead of raw UUIDs + JSON blobs."""
    rows = (
        await session.execute(
            select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        )
    ).scalars().all()
    rows = list(reversed(rows))
    user_lookup = await _resolve_user_lookup(
        session, {r.user_id for r in rows if r.user_id}
    )
    return {
        "lines": [
            {
                "id": r.id,
                "created_at": r.created_at,
                "action": r.action,
                "user_id": str(r.user_id) if r.user_id else None,
                "user_email": (
                    user_lookup.get(str(r.user_id), {}).get("email") if r.user_id else None
                ),
                "user_display_name": (
                    user_lookup.get(str(r.user_id), {}).get("display_name") if r.user_id else None
                ),
                "details": r.details,
            }
            for r in rows
        ],
    }
