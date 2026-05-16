"""Account-level GDPR endpoints (Phase 8) + recovery codes (Phase 13 / C6).

  POST   /account/delete                       Hard-delete account + data + blobs.
  GET    /account/export                       GDPR Art. 20 ZIP.
  POST   /account/recovery-codes/regenerate    8 fresh single-use codes
                                               (returned once, hashed in DB).
  POST   /account/recovery-codes/login         Sign in with one code (no JWT
                                               required — used when the user
                                               is locked out of their password).
  POST   /admin/retention/sweep                Drop originals past their TTL.

Deletion is intentionally synchronous and immediate: the user has explicitly
asked. The audit_log entry is written *first* with non-blob counts so we have
a record even if a later step fails. FK ON DELETE CASCADE on every per-user
table handles the row deletion in a single statement.

Recovery codes are 10-character base32 strings (50 bits of entropy each); we
store an argon2 hash and never the plaintext. Logging in with a code returns
a fresh JWT and immediately marks the code used.
"""
from __future__ import annotations

import io
import json
import logging
import secrets
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete as sa_delete, func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import current_active_user, get_jwt_strategy
from backend.config import settings
from backend.db import get_session
from backend.deletion import hard_delete_images
from backend.email_send import send_recovery_codes_email
from backend.models import (
    AuditLog,
    ConsentRecord,
    Face,
    FaceDetection,
    Image,
    ImageGeo,
    NotificationPref,
    Person,
    RecoveryCode,
    Tag,
    User,
)
from backend.retention import (
    sweep_audit_log_anonymize,
    sweep_expired_originals,
    sweep_expired_quarantine,
    sweep_feedback_events,
    sweep_scheduled_account_deletes,
)
from backend.storage import storage
from backend.trainer import apply_drift_discount, consume_feedback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/account", tags=["account"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])

# Argon2 with default parameters (m=65536 KiB / t=3 / p=4) is comfortably
# slow on our target hardware — recovery codes are short and one-shot, so
# the cost of verifying *every* hash on a login attempt is bounded by the
# number of unused codes per user (≤ 8). Single shared instance avoids
# re-allocating the parameter struct per call.
_PASSWORD_HASHER = PasswordHasher()
RECOVERY_CODE_COUNT = 8
# 10-char base32 == 50 bits. Brute-forcing one code over the network would
# need ~10^15 attempts; the recovery-login endpoint is rate-limited at the
# router level (single guess per (email, code) tuple, no enumeration).
RECOVERY_CODE_LENGTH = 10
_RECOVERY_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # base32 minus 0/1/8/9


class DeleteResult(BaseModel):
    deleted_user_id: UUID
    images_deleted: int
    faces_deleted: int
    persons_deleted: int
    blobs_deleted: int
    blob_errors: int


# ---------- DELETE ACCOUNT ----------

async def _collect_user_blob_keys(
    session: AsyncSession, user_id: UUID
) -> tuple[list[tuple[str, str]], int]:
    """Return [(bucket, key), ...] for every blob owned by the user."""
    keys: list[tuple[str, str]] = []

    # Originals + served variants from `images`. Both buckets must be hit.
    img_rows = (
        await session.execute(
            select(Image.original_blob_key, Image.served_blob_key, Image.thumbnail_blob_key).where(
                Image.user_id == user_id
            )
        )
    ).all()
    for original_key, served_key, thumbnail_key in img_rows:
        if original_key:
            keys.append((storage.bucket_originals, original_key))
        if served_key and served_key != original_key:
            keys.append((storage.bucket_served, served_key))
        if thumbnail_key:
            keys.append((storage.bucket_served, thumbnail_key))

    # Face crops.
    face_keys = (
        await session.execute(
            select(FaceDetection.crop_blob_key).where(
                FaceDetection.user_id == user_id,
                FaceDetection.crop_blob_key.is_not(None),
            )
        )
    ).scalars().all()
    for k in face_keys:
        keys.append((storage.bucket_faces, k))

    return keys, len(img_rows)


@router.post("/delete", response_model=DeleteResult)
async def delete_account(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeleteResult:
    """Hard-delete the calling user's account and every byte of their data.

    Order:
      1. Pre-count rows for the audit entry.
      2. Audit-log the deletion intent (user_id will become NULL via FK once
         the row is gone, but `details` retains the original UUID + counts).
      3. Delete blobs (best-effort; collect error count).
      4. Delete the user row → FK CASCADE wipes images, faces, persons,
         face_detections, consent_records, image_tags.
    """
    user_id = user.id

    # 1. Pre-counts.
    images_count = (
        await session.execute(
            select(Image).where(Image.user_id == user_id)
        )
    ).all()
    faces_count = (
        await session.execute(
            select(Face).where(Face.user_id == user_id)
        )
    ).all()
    persons_count = (
        await session.execute(
            select(Person).where(Person.user_id == user_id)
        )
    ).all()

    image_ids = [row[0].id for row in images_count]
    # §A5 — full account delete: drop the user's learned bandit state
    # along with the image rows. For per-image bulk-delete the
    # caller leaves `reset_bandit` at its False default since
    # per-(user, arm) weights survive the loss of one image.
    image_delete = await hard_delete_images(
        session,
        user_id=user_id,
        image_ids=image_ids,
        audit_action="account.images.delete",
        reset_bandit=True,
    )
    await add_audit(
        session,
        user_id=user_id,
        action="account.delete",
        details={
            "user_id": str(user_id),
            "email": user.email,
            "images": len(images_count),
            "faces": len(faces_count),
            "persons": len(persons_count),
            "blobs_deleted": image_delete.blobs_deleted,
            "blob_errors": image_delete.blob_errors,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    await session.execute(sa_delete(User).where(User.id == user_id))
    await session.flush()
    remaining = await _verify_account_deleted(session, user_id)
    if remaining:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Account deletion incomplete: {remaining}",
        )
    await session.commit()
    return DeleteResult(
        deleted_user_id=user_id,
        images_deleted=len(images_count),
        faces_deleted=len(faces_count),
        persons_deleted=len(persons_count),
        blobs_deleted=image_delete.blobs_deleted,
        blob_errors=image_delete.blob_errors,
    )

    # 2. Collect blob keys (must happen BEFORE delete; rows are cascading).
    blob_keys, _ = await _collect_user_blob_keys(session, user_id)

    # 3. Audit log first — proof artifact survives even if next steps fail.
    session.add(
        AuditLog(
            user_id=user_id,
            action="account.delete",
            details={
                "user_id": str(user_id),
                "email": user.email,
                "images": len(images_count),
                "faces": len(faces_count),
                "persons": len(persons_count),
                "blobs_planned": len(blob_keys),
                "deleted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    )
    await session.commit()

    # 4. Drop blobs from MinIO.
    blobs_deleted = 0
    blob_errors = 0
    for bucket, key in blob_keys:
        try:
            storage.delete(bucket, key)
            blobs_deleted += 1
        except Exception as exc:
            blob_errors += 1
            logger.warning("account.delete: blob %s/%s removal failed: %s", bucket, key, exc)

    # 5. Delete the user row. FKs cascade per-table.
    await session.execute(sa_delete(User).where(User.id == user_id))
    await session.commit()

    return DeleteResult(
        deleted_user_id=user_id,
        images_deleted=len(images_count),
        faces_deleted=len(faces_count),
        persons_deleted=len(persons_count),
        blobs_deleted=blobs_deleted,
        blob_errors=blob_errors,
    )


# ---------- §B4 — scheduled deletion with grace window ----------


class ScheduledDeleteStatus(BaseModel):
    scheduled_delete_at: datetime | None
    grace_days: int


@router.post("/schedule-delete", response_model=ScheduledDeleteStatus)
async def schedule_account_delete(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScheduledDeleteStatus:
    """Stage account deletion with a grace window.

    Sets `users.scheduled_delete_at = now + ACCOUNT_DELETE_GRACE_DAYS`.
    The nightly `sweep_scheduled_account_deletes` worker hard-deletes
    everything past that timestamp via the same path
    `/account/delete` uses today. The user can call
    `/account/cancel-delete` before the timestamp to abort.

    Idempotent on the timestamp: re-calling extends the deadline
    forward by another grace window. The new value goes to the audit
    log so we can prove the user's intent + reset chain.

    Why a separate route from /account/delete: the existing immediate
    route is what callers expect to nuke the account on click; the
    grace flow is a friendlier option for self-serve teardown that
    a UI prompt can cancel from.
    """
    grace_days = settings.account_delete_grace_days
    if grace_days <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Grace days must be positive")
    new_at = datetime.now(timezone.utc) + timedelta(days=grace_days)
    refreshed = (
        await session.execute(select(User).where(User.id == user.id))
    ).scalar_one()
    refreshed.scheduled_delete_at = new_at
    await add_audit(
        session,
        user_id=user.id,
        action="account.delete.scheduled",
        details={
            "scheduled_delete_at": new_at.isoformat(),
            "grace_days": grace_days,
        },
    )
    await session.commit()
    return ScheduledDeleteStatus(
        scheduled_delete_at=new_at, grace_days=grace_days,
    )


@router.post("/cancel-delete", response_model=ScheduledDeleteStatus)
async def cancel_account_delete(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScheduledDeleteStatus:
    """Abort a previously scheduled deletion before the sweeper runs.

    Idempotent: no-op if `scheduled_delete_at` is already NULL. Audit
    row written either way so the cancellation trail survives even
    after the user later schedules + cancels multiple times.
    """
    refreshed = (
        await session.execute(select(User).where(User.id == user.id))
    ).scalar_one()
    prev = refreshed.scheduled_delete_at
    refreshed.scheduled_delete_at = None
    await add_audit(
        session,
        user_id=user.id,
        action="account.delete.canceled",
        details={"previous_scheduled_at": prev.isoformat() if prev else None},
    )
    await session.commit()
    return ScheduledDeleteStatus(
        scheduled_delete_at=None,
        grace_days=settings.account_delete_grace_days,
    )


@router.get("/schedule-delete", response_model=ScheduledDeleteStatus)
async def get_scheduled_delete(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScheduledDeleteStatus:
    """Read-only — current scheduled-delete state for the calling user.
    Drives the settings UI's "deletion scheduled for X — cancel?" row."""
    refreshed = (
        await session.execute(select(User).where(User.id == user.id))
    ).scalar_one()
    return ScheduledDeleteStatus(
        scheduled_delete_at=refreshed.scheduled_delete_at,
        grace_days=settings.account_delete_grace_days,
    )


async def _verify_account_deleted(session: AsyncSession, user_id: UUID) -> dict[str, int]:
    checks = {
        "users": "SELECT count(*) FROM users WHERE id = :uid",
        "images": "SELECT count(*) FROM images WHERE user_id = :uid",
        "image_geo": "SELECT count(*) FROM image_geo WHERE user_id = :uid",
        "face_detections": "SELECT count(*) FROM face_detections WHERE user_id = :uid",
        "faces": "SELECT count(*) FROM faces WHERE user_id = :uid",
        "persons": "SELECT count(*) FROM persons WHERE user_id = :uid",
        "consent_records": "SELECT count(*) FROM consent_records WHERE user_id = :uid",
        "feedback_events": "SELECT count(*) FROM feedback_events WHERE user_id = :uid",
        "bandit_state": "SELECT count(*) FROM bandit_state WHERE user_id = :uid",
        "cloud_links": "SELECT count(*) FROM cloud_links WHERE user_id = :uid",
        "cloud_files": "SELECT count(*) FROM cloud_files WHERE user_id = :uid",
        "folders": "SELECT count(*) FROM folders WHERE user_id = :uid",
        "recovery_codes": "SELECT count(*) FROM recovery_codes WHERE user_id = :uid",
    }
    remaining: dict[str, int] = {}
    for name, sql in checks.items():
        count = (await session.execute(text(sql), {"uid": user_id})).scalar_one()
        if count:
            remaining[name] = int(count)
    return remaining


# ---------- EXPORT ACCOUNT (GDPR Art. 20) ----------


def _safe_filename(name: str | None, fallback: str) -> str:
    """Strip path separators and control chars from user-supplied filenames
    so they're safe to embed in a ZIP."""
    if not name:
        return fallback
    out = "".join(c for c in name if c.isprintable() and c not in '/\\<>:"|?*')
    return out.strip() or fallback


async def _build_export_zip(
    session: AsyncSession, user: User
) -> bytes:
    """Build the export ZIP fully in memory and return its bytes.

    A pure-streaming implementation is tempting but `zipfile.ZipFile` writes
    a coherent stream into a single underlying file object — we can't
    truncate the buffer between entries without corrupting the archive
    (the central directory at close uses absolute file offsets). Buffering
    is the simple, correct choice; user data is bounded by what the user
    has uploaded so memory use is acceptable for v1.
    """
    buffer = io.BytesIO()
    zf = zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED)

    # Build the metadata first so consumers see it without reading blobs.
    images = (
        await session.execute(select(Image).where(Image.user_id == user.id))
    ).scalars().all()
    persons = (
        await session.execute(select(Person).where(Person.user_id == user.id))
    ).scalars().all()
    faces = (
        await session.execute(select(Face).where(Face.user_id == user.id))
    ).scalars().all()
    detections = (
        await session.execute(
            select(FaceDetection).where(FaceDetection.user_id == user.id)
        )
    ).scalars().all()
    consents = (
        await session.execute(
            select(ConsentRecord).where(ConsentRecord.user_id == user.id)
        )
    ).scalars().all()
    audits = (
        await session.execute(
            select(AuditLog).where(AuditLog.user_id == user.id)
        )
    ).scalars().all()
    image_tag_rows = (
        await session.execute(
            select(Image.id, Tag.label).select_from(Image)
            .join(Image.tags)
            .join(Tag)
            .where(Image.user_id == user.id)
        )
    ).all()
    tags_by_image: dict = {}
    for img_id, label in image_tag_rows:
        tags_by_image.setdefault(str(img_id), []).append(label)

    # §B3 — GDPR Article 20 completeness: GPS retention rows are user
    # data and must be included in the portability export when the
    # user holds the corresponding `gps_retention` consent. The
    # in-app map view reads from this table, so omitting it from the
    # export was a portability gap.
    geo_rows = (
        await session.execute(
            select(ImageGeo).where(ImageGeo.user_id == user.id)
        )
    ).scalars().all()
    geo_by_image: dict = {}
    for g in geo_rows:
        geo_by_image[str(g.image_id)] = {
            "lat": g.lat,
            "lng": g.lng,
            "taken_at": g.taken_at.isoformat() if g.taken_at else None,
            "captured_with": g.captured_with,
        }

    metadata = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": {
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
        },
        "images": [
            {
                "id": str(i.id),
                "original_filename": i.original_filename,
                "category": i.category,
                "width": i.width,
                "height": i.height,
                "byte_size_original": i.byte_size_original,
                "byte_size_served": i.byte_size_served,
                "mime_type_original": i.mime_type_original,
                "mime_type_served": i.mime_type_served,
                "codec": i.codec,
                "quality": i.quality,
                "scene_label": i.scene_label,
                "content_type": i.content_type,
                "uploaded_at": i.uploaded_at.isoformat() if i.uploaded_at else None,
                "deleted_at": i.deleted_at.isoformat() if i.deleted_at else None,
                "tags": tags_by_image.get(str(i.id), []),
                "original_retained": i.original_blob_key is not None,
                # §B3 — CLIP embedding is the user's own data; ship it
                # in the export. 768 floats per row; the user knows
                # what to do with it (search export tooling, model
                # fingerprinting, deletion verification). Note in the
                # readme that exporting + re-sharing the embedding
                # exposes a privacy-sensitive vector.
                "clip_embedding": (
                    list(i.clip_embedding)
                    if getattr(i, "clip_embedding", None) is not None
                    else None
                ),
                "summary": i.summary,
                "summary_topic": i.summary_topic,
                "summary_points": i.summary_points,
                "geo": geo_by_image.get(str(i.id)),
            }
            for i in images
        ],
        "geo": [
            {
                "image_id": str(g.image_id),
                "lat": g.lat,
                "lng": g.lng,
                "taken_at": g.taken_at.isoformat() if g.taken_at else None,
                "captured_with": g.captured_with,
            }
            for g in geo_rows
        ],
        "persons": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "face_count": p.face_count,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in persons
        ],
        "faces": [
            {
                "id": f.id,
                "person_id": f.person_id,
                "cluster_id": f.cluster_id,
                "quality_score": f.quality_score,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in faces
        ],
        "face_detections": [
            {
                "id": d.id,
                "image_id": str(d.image_id),
                "face_id": d.face_id,
                "bbox": [d.bbox_x, d.bbox_y, d.bbox_w, d.bbox_h],
                "detection_confidence": d.detection_confidence,
            }
            for d in detections
        ],
        "consent_records": [
            {
                "kind": c.consent_kind,
                "state": c.state,
                "policy_version": c.policy_version,
                "policy_text_sha256_hex": c.policy_text_sha256.hex()
                if c.policy_text_sha256 else None,
                "signature_text": c.signature_text,
                "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            }
            for c in consents
        ],
        "audit_log": [
            {
                "action": a.action,
                "details": a.details,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in audits
        ],
    }

    zf.writestr("metadata.json", json.dumps(metadata, indent=2, default=str))

    # Originals (if still retained) + served variants.
    for img in images:
        safe = _safe_filename(img.original_filename, f"image-{img.id}")
        if img.original_blob_key:
            try:
                blob = storage.get(storage.bucket_originals, img.original_blob_key)
            except Exception as exc:
                logger.warning("export: original %s missing: %s", img.id, exc)
                blob = None
            if blob is not None:
                zf.writestr(f"originals/{img.id}__{safe}", blob)
        if img.served_blob_key and img.served_blob_key != img.original_blob_key:
            try:
                served = storage.get(storage.bucket_served, img.served_blob_key)
            except Exception as exc:
                logger.warning("export: served %s missing: %s", img.id, exc)
                served = None
            if served is not None:
                ext = (img.mime_type_served or "").split("/")[-1] or "bin"
                zf.writestr(f"served/{img.id}.{ext}", served)

    # Face crops (biometric data — included so the user has the full record;
    # they can delete the export ZIP afterwards).
    for det in detections:
        if not det.crop_blob_key:
            continue
        try:
            crop = storage.get(storage.bucket_faces, det.crop_blob_key)
        except Exception:
            continue
        zf.writestr(f"face_crops/face-{det.face_id}-det-{det.id}.jpg", crop)

    zf.close()
    return buffer.getvalue()


@router.get("/activity")
async def get_account_activity(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
) -> list[dict]:
    """User-visible activity log. Returns the caller's most recent audit
    events — `auth.login`, `consent.*.grant`, `images.rename`,
    `images.bulk-delete`, etc. Already-existing `AuditLog` row is the
    source of truth; this endpoint is just the user-scoped slice.
    """
    rows = (
        await session.execute(
            select(AuditLog)
            .where(AuditLog.user_id == user.id)
            .order_by(AuditLog.created_at.desc())
            .limit(min(limit, 200))
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "action": r.action,
            "details": r.details or {},
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/trash")
async def get_account_trash(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Soft-deleted images for the caller. Returns counts so the FE can
    show "42 items in trash · 1.2 GB" without paginating individual
    rows; the actual list view comes later if needed.
    """
    row = (
        await session.execute(
            select(
                sa_func.count().label("count"),
                sa_func.coalesce(sa_func.sum(Image.byte_size_original), 0).label("bytes"),
            ).where(
                Image.user_id == user.id,
                Image.deleted_at.is_not(None),
            )
        )
    ).one()
    return {
        "count": int(row.count or 0),
        "total_bytes": int(row.bytes or 0),
    }


@router.post("/trash/empty")
async def empty_account_trash(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Permanently delete every soft-deleted image owned by the caller.
    Uses the existing `hard_delete_images` helper so blobs / face data /
    summaries cascade like a normal account-delete; just scoped to the
    trashed subset.
    """
    rows = (
        await session.execute(
            select(Image.id).where(
                Image.user_id == user.id,
                Image.deleted_at.is_not(None),
            )
        )
    ).scalars().all()
    if not rows:
        return {"deleted": 0}
    result = await hard_delete_images(
        session,
        user_id=user.id,
        image_ids=list(rows),
        audit_action="account.trash.empty.image",
    )
    await add_audit(
        session, user.id, "account.trash.empty",
        details={
            "count": len(result.image_ids),
            "blobs_deleted": result.blobs_deleted,
            "faces_deleted": result.faces_deleted,
        },
    )
    await session.commit()
    return {"deleted": len(result.image_ids)}


async def _enforce_export_rate_limit(
    session: AsyncSession, user_id: UUID
) -> None:
    """§B3 — one full export per `account_export_min_hours_between` per
    user. The audit log is the canonical source so the limit holds
    across restarts / replicas; no need for Redis.

    Raises 429 with a `Retry-After` header so a polite client can
    schedule the retry without polling.
    """
    hours = settings.account_export_min_hours_between
    if hours <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.user_id == user_id,
                AuditLog.action == "account.export",
                AuditLog.created_at >= cutoff,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if recent is None:
        return
    next_allowed = recent.created_at + timedelta(hours=hours)
    retry_after = max(1, int((next_allowed - datetime.now(timezone.utc)).total_seconds()))
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Account export is rate-limited to once every {hours} hours. "
        f"Try again at {next_allowed.isoformat()}.",
        headers={"Retry-After": str(retry_after)},
    )


@router.get("/export")
async def export_account(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Returns a ZIP of every byte the system holds about the caller.

    §B3 — rate-limited to once per `account_export_min_hours_between`
    (default 24h). Tracks via an `account.export` audit row written
    on each successful export. Exceeding the cap returns 429 with
    `Retry-After`.
    """
    await _enforce_export_rate_limit(session, user.id)
    fname = f"neuthek-export-{user.id}.zip"
    blob = await _build_export_zip(session, user)
    await add_audit(
        session,
        user_id=user.id,
        action="account.export",
        details={"bytes": len(blob), "filename": fname},
    )
    await session.commit()
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Cache-Control": "private, no-store",
        },
    )


# ---------- ADMIN: RETENTION SWEEP ----------


class SweepResponse(BaseModel):
    scanned: int
    blobs_deleted: int
    blob_errors: int
    rows_nulled: int
    bytes_freed: int


@admin_router.post("/retention/sweep", response_model=SweepResponse)
async def admin_retention_sweep(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SweepResponse:
    if not user.is_superuser:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Superuser required"
        )
    res = await sweep_expired_originals(session)
    return SweepResponse(
        scanned=res.scanned,
        blobs_deleted=res.blobs_deleted,
        blob_errors=res.blob_errors,
        rows_nulled=res.rows_nulled,
        bytes_freed=res.bytes_freed,
    )


class QuarantineSweepResponse(BaseModel):
    scanned: int
    blobs_deleted: int
    blob_errors: int
    bytes_freed: int
    cutoff: str


@admin_router.post("/quarantine/sweep", response_model=QuarantineSweepResponse)
async def admin_quarantine_sweep(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    retention_days: int | None = None,
) -> QuarantineSweepResponse:
    """§B4 — delete forensic-quarantine bytes past the retention window.

    The `audit_log` row written at rejection time is preserved; only
    the bytes in `bucket_quarantine` go away. Pass `?retention_days=N`
    to override the configured default for a single sweep (useful
    after a security incident when ops need a deeper trim).
    """
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")
    try:
        res = await sweep_expired_quarantine(
            session, retention_days=retention_days
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return QuarantineSweepResponse(
        scanned=res.scanned,
        blobs_deleted=res.blobs_deleted,
        blob_errors=res.blob_errors,
        bytes_freed=res.bytes_freed,
        cutoff=res.cutoff_iso,
    )


# ----- §B4 retention sweep endpoints -----


class FeedbackSweepResponse(BaseModel):
    rows_deleted: int
    cutoff: str
    retention_days: int


@admin_router.post("/retention/sweep-feedback", response_model=FeedbackSweepResponse)
async def admin_sweep_feedback(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    retention_days: int | None = None,
) -> FeedbackSweepResponse:
    """§B4 — delete consumed feedback_events older than the horizon
    (default 90 days). Optional `?retention_days=N` overrides for a
    one-off deeper trim."""
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")
    try:
        res = await sweep_feedback_events(session, retention_days=retention_days)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return FeedbackSweepResponse(
        rows_deleted=res.rows_deleted,
        cutoff=res.cutoff_iso,
        retention_days=res.retention_days,
    )


class AuditAnonymizeResponse(BaseModel):
    rows_anonymized: int
    cutoff: str
    retention_days: int


@admin_router.post("/retention/sweep-audit", response_model=AuditAnonymizeResponse)
async def admin_sweep_audit(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    retention_days: int | None = None,
) -> AuditAnonymizeResponse:
    """§B4 — anonymize audit_log rows older than the horizon
    (default 365 days) by NULLing `user_id`. Action + timestamp +
    details survive so forensics still works without tying entries
    to a specific person."""
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")
    try:
        res = await sweep_audit_log_anonymize(session, retention_days=retention_days)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return AuditAnonymizeResponse(
        rows_anonymized=res.rows_anonymized,
        cutoff=res.cutoff_iso,
        retention_days=res.retention_days,
    )


class AccountSweepResponse(BaseModel):
    accounts_hard_deleted: int
    swept_at: str


@admin_router.post("/retention/sweep-accounts", response_model=AccountSweepResponse)
async def admin_sweep_accounts(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountSweepResponse:
    """§B4 — hard-delete users whose `scheduled_delete_at` has passed.
    Runs the same path /account/delete uses today."""
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")
    res = await sweep_scheduled_account_deletes(session)
    return AccountSweepResponse(
        accounts_hard_deleted=res.accounts_hard_deleted,
        swept_at=res.swept_at_iso,
    )


class TrainerRunResponse(BaseModel):
    consumed: int
    updated_arms: int
    failed: int
    drift_applied_rows: int = 0


@admin_router.post("/trainer/run", response_model=TrainerRunResponse)
async def admin_trainer_run(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    apply_drift: bool = False,
) -> TrainerRunResponse:
    """Consume pending feedback into bandit_state. Idempotent.

    Pass ?apply_drift=true to also shrink every user's (A, b) toward the
    prior — typically once per 30 days, not per pass.
    """
    if not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Superuser required")
    res = await consume_feedback(session)
    drift_n = 0
    if apply_drift:
        drift_n = await apply_drift_discount(session)
    return TrainerRunResponse(
        consumed=res.consumed,
        updated_arms=res.updated_arms,
        failed=res.failed,
        drift_applied_rows=drift_n,
    )


# ---------- RECOVERY CODES (Phase 13 / C6) ----------


def _generate_code() -> str:
    """Cryptographically random 10-char base32 code, dash after the 5th char.
    Format `XXXXX-XXXXX` is human-friendly to write down on paper."""
    raw = "".join(
        secrets.choice(_RECOVERY_CODE_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH)
    )
    return f"{raw[:5]}-{raw[5:]}"


def _normalize_code(code: str) -> str:
    """Strip whitespace, dashes, and lower → upper so the user can paste
    in any reasonable form (`abcde-fghij`, `abcdefghij`, `ABCDE FGHIJ`, …)
    and still match."""
    return "".join(c for c in code.strip().upper() if c in _RECOVERY_CODE_ALPHABET)


class RecoveryCodesResponse(BaseModel):
    codes: list[str]


class RecoveryCodesStatus(BaseModel):
    has_codes: bool
    remaining: int
    generated_at: datetime | None = None


@router.get("/recovery-codes", response_model=RecoveryCodesStatus)
async def recovery_codes_status(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecoveryCodesStatus:
    rows = (
        await session.execute(
            select(RecoveryCode)
            .where(RecoveryCode.user_id == user.id)
            .order_by(RecoveryCode.created_at.desc())
        )
    ).scalars().all()
    if not rows:
        return RecoveryCodesStatus(has_codes=False, remaining=0)
    remaining = sum(1 for r in rows if r.used_at is None)
    return RecoveryCodesStatus(
        has_codes=True,
        remaining=remaining,
        generated_at=rows[0].created_at,
    )


@router.post(
    "/recovery-codes/regenerate",
    response_model=RecoveryCodesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_recovery_codes(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecoveryCodesResponse:
    """Drop the user's existing codes and issue 8 new ones.

    Returns the plaintext codes — this is the only time the server ever
    sees them. We also email the codes; the user can save either copy.
    """
    # Wipe prior set (used + unused).
    await session.execute(
        sa_delete(RecoveryCode).where(RecoveryCode.user_id == user.id)
    )

    codes = [_generate_code() for _ in range(RECOVERY_CODE_COUNT)]
    for code in codes:
        session.add(
            RecoveryCode(
                user_id=user.id,
                code_hash=_PASSWORD_HASHER.hash(_normalize_code(code)),
            )
        )
    session.add(
        AuditLog(
            user_id=user.id,
            action="account.recovery_codes.regenerate",
            details={"count": len(codes)},
        )
    )
    await session.commit()

    # Best-effort email — never fails the request.
    try:
        send_recovery_codes_email(user.email, codes)
    except Exception:
        logger.exception("recovery codes email failed for %s", user.id)

    return RecoveryCodesResponse(codes=codes)


class RecoveryLoginPayload(BaseModel):
    email: EmailStr
    code: str


class RecoveryLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post(
    "/recovery-codes/login",
    response_model=RecoveryLoginResponse,
    # Public — no current-user dependency. Used precisely when the user
    # cannot log in normally.
)
async def recovery_login(
    payload: RecoveryLoginPayload,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecoveryLoginResponse:
    """Trade a recovery code for a JWT.

    On success: marks the code used (one-shot), audit-logs the action,
    issues a session JWT for the user. On failure: 400 Bad Request with
    a generic message — never enumerate which step failed (email vs.
    code) so attackers can't probe for valid emails.
    """
    code = _normalize_code(payload.code)
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid recovery code")

    user = (
        await session.execute(
            select(User).where(User.email == payload.email.lower())
        )
    ).scalar_one_or_none()
    if user is None:
        # Constant-time fail: still verify a dummy hash so timing doesn't
        # leak whether the email exists.
        try:
            _PASSWORD_HASHER.verify(
                "$argon2id$v=19$m=65536,t=3,p=4$AAAAAAAAAAAAAAAAAAAAAA$"
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                "noop",
            )
        except Exception:
            pass
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid recovery code")

    candidates = (
        await session.execute(
            select(RecoveryCode)
            .where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.used_at.is_(None),
            )
        )
    ).scalars().all()

    matched: RecoveryCode | None = None
    for rc in candidates:
        try:
            _PASSWORD_HASHER.verify(rc.code_hash, code)
            matched = rc
            break
        except VerifyMismatchError:
            continue

    if matched is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid recovery code")

    # Lock the matched row and re-read it. Without this, two concurrent
    # POSTs with the same valid code can both find it unused (step 1),
    # both verify it (step 2), and both set used_at + return a JWT —
    # turning a "single-use" recovery code into a multi-use credential.
    locked = (
        await session.execute(
            select(RecoveryCode)
            .where(
                RecoveryCode.id == matched.id,
                RecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        # Another concurrent request consumed the code between our
        # candidate fetch and the lock acquisition. Treat as invalid.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid recovery code")

    locked.used_at = datetime.now(timezone.utc)
    session.add(
        AuditLog(
            user_id=user.id,
            action="account.recovery_codes.login",
            details={"recovery_code_id": locked.id},
        )
    )
    await session.commit()

    # Issue a JWT via the same strategy /auth/jwt/login uses.
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return RecoveryLoginResponse(access_token=token)


# ---------- §1.2.3 — notification preferences ----------
#
# `notification_prefs` is a sparse table — rows only exist when a user
# has explicitly set a preference. Loading merges in defaults so the
# FE always sees a complete matrix. Saving inserts/updates one row per
# changed (kind, channel) pair.

NOTIFICATION_KINDS = (
    "product_updates",
    "security_alerts",
    "account_activity",
    "storage_warnings",
)
NOTIFICATION_CHANNELS = ("email", "in_app")
# security_alerts default to ON; everything else defaults to OFF.
# This is the "informed-consent default": we don't spam users by
# default, but we never let an operator silently lose security mail.
_DEFAULTS: dict[tuple[str, str], bool] = {
    (k, c): (k == "security_alerts")
    for k in NOTIFICATION_KINDS for c in NOTIFICATION_CHANNELS
}


class NotificationPrefRow(BaseModel):
    kind: str
    channel: str
    enabled: bool


class NotificationPrefsResponse(BaseModel):
    kinds: list[str]
    channels: list[str]
    prefs: list[NotificationPrefRow]


class NotificationPrefsUpdate(BaseModel):
    """Body for PATCH /account/notifications. Each entry overrides
    that (kind, channel) pair. Pairs not in the list keep their state."""

    prefs: list[NotificationPrefRow]


@router.get("/notifications", response_model=NotificationPrefsResponse)
async def get_notification_prefs(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationPrefsResponse:
    rows = (
        await session.execute(
            select(NotificationPref).where(NotificationPref.user_id == user.id)
        )
    ).scalars().all()
    overrides = {(r.kind, r.channel): r.enabled for r in rows}
    prefs = [
        NotificationPrefRow(
            kind=k, channel=c,
            enabled=overrides.get((k, c), _DEFAULTS.get((k, c), False)),
        )
        for k in NOTIFICATION_KINDS for c in NOTIFICATION_CHANNELS
    ]
    return NotificationPrefsResponse(
        kinds=list(NOTIFICATION_KINDS),
        channels=list(NOTIFICATION_CHANNELS),
        prefs=prefs,
    )


@router.patch("/notifications", response_model=NotificationPrefsResponse)
async def update_notification_prefs(
    body: NotificationPrefsUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationPrefsResponse:
    """Upsert rows from `body.prefs`. Unknown (kind, channel) pairs
    are silently dropped — extending the matrix is a backend-side
    operation, not driven by FE submission."""
    valid_pairs = {(k, c) for k in NOTIFICATION_KINDS for c in NOTIFICATION_CHANNELS}
    existing = {
        (r.kind, r.channel): r
        for r in (
            await session.execute(
                select(NotificationPref).where(NotificationPref.user_id == user.id)
            )
        ).scalars().all()
    }
    now = datetime.now(timezone.utc)
    changed = 0
    for p in body.prefs:
        key = (p.kind, p.channel)
        if key not in valid_pairs:
            continue
        if key in existing:
            row = existing[key]
            if row.enabled != p.enabled:
                row.enabled = p.enabled
                row.updated_at = now
                changed += 1
        else:
            session.add(NotificationPref(
                user_id=user.id, kind=p.kind, channel=p.channel,
                enabled=p.enabled, updated_at=now,
            ))
            changed += 1
    if changed:
        session.add(AuditLog(
            user_id=user.id,
            action="account.notifications.update",
            details={"changes": changed},
        ))
        await session.commit()
    return await get_notification_prefs(user=user, session=session)
