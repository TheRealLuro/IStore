"""Per-user storage accounting + smart-mode reclamation actions.

For YEARS we reported `byte_size_served` as "used" and called it a
day. That number was systematically wrong for two reasons:

  1. **Video has multiple quality variants.** The transcode worker
     emits 480p / 720p / 1080p / 1440p / 2160p side-by-side. We only
     credit the default tier's bytes against the user, so a single
     1080p H.264 / AAC of a phone clip can look like 12 MB in the UI
     while actually consuming ~60 MB on disk.

  2. **Originals are retained for 30 days after upload.** They sit in
     the `originals` bucket waiting for a possible re-summarize or
     re-detect, but they show up nowhere in the user's "used" number
     even though they're real bytes on real disks.

The endpoint now returns BOTH a grand total (`used_bytes`) AND the
component breakdown (`served_bytes`, `variants_bytes`,
`originals_bytes`, `trash_bytes`) so the UI can be honest with the
user — and the user can SEE what's eating their quota and click a
button to reclaim it.

Two reclamation actions live here as well:

  POST /storage/free-originals — for the user who's confident they
       won't re-summarize, this drops every retained original blob
       NOW (instead of waiting for the 30-day sweep). The served
       version remains; future downloads include
       `X-Original-Expired: true` so the UI can say "we no longer
       have the original — you're downloading the compressed copy."

  POST /storage/free-variants — drops the non-default video quality
       variants. Saves ~70% of video-bucket bytes for libraries with
       big screen recordings. The player falls back to the default
       tier; users can re-transcode from the originals (if retained)
       or just accept the default tier going forward.

Both are idempotent and audit-logged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import AuditLog, Image, User, VaultItem
from backend.retention import sweep_expired_originals
from backend.schemas import StorageUsage
from backend.storage import storage


# ---------- retention policy ----------
#
# Per-user "how long do we keep your originals" enum. Used in three
# places:
#   1. Upload (image.py) — stamps original_expires_at at INSERT.
#   2. PATCH /storage/retention-policy — re-bases existing
#      originals when the user dials the knob.
#   3. The frontend Settings UI — radio group + copy.
#
# The DB has a CHECK constraint matching this exact set (migration
# 0038). Adding a new value here requires a follow-up migration.

RETENTION_POLICIES: tuple[str, ...] = (
    "immediate", "24h", "7d", "30d", "forever",
)


def policy_to_expiry(
    policy: str, *, now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Translate the user's retention enum to a concrete
    `original_expires_at` value.

    Returns None when the policy is "forever" — the retention
    sweeper skips rows with `original_expires_at IS NULL`, so the
    bytes stay forever (until the user changes their mind).
    Returns `now` for "immediate" so the next sweep tick (within
    24 h, sooner if the user clicks Free Originals) drops them.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if policy == "forever":
        return None
    if policy == "immediate":
        return now
    delta_map = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    delta = delta_map.get(policy)
    if delta is None:
        # Unknown / NULL / corrupted — fall back to legacy 30d
        # default so a misconfigured row doesn't accidentally drop
        # the user's originals.
        delta = timedelta(days=30)
    return now + delta

router = APIRouter(prefix="/storage", tags=["storage"])

# Hard-coded for now; later move to a `users.quota_bytes` column or a tier table.
DEFAULT_QUOTA_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB


def effective_quota_bytes(user: User) -> int:
    """Resolve `users.quota_bytes` (admin/billing override) → fall back
    to the global default.

    Co-located with `DEFAULT_QUOTA_BYTES` so callers outside this
    module (cloud_sync, archive_upload, …) don't have to duplicate the
    null-coalescing logic and accidentally diverge."""
    return user.quota_bytes if user.quota_bytes is not None else DEFAULT_QUOTA_BYTES


async def vault_used_bytes(session: AsyncSession, user_id) -> tuple[int, int]:
    """End-to-end-encrypted Vault footprint: `(bytes, file_count)`.

    Only Vault FILE items consume metered storage (their ciphertext lives in
    MinIO and `size_bytes` records it). Small secure items (password / note /
    seed / card) are tiny inline ciphertext with `size_bytes IS NULL`, so they
    don't count — same as a password row in the Drive doesn't. The Vault shares
    one quota pool with the Drive, so this sums into the same `used_bytes`."""
    q = select(
        func.coalesce(func.sum(VaultItem.size_bytes), 0),
        func.count(VaultItem.id),
    ).where(VaultItem.user_id == user_id, VaultItem.size_bytes.is_not(None))
    b, n = (await session.execute(q)).one()
    return int(b or 0), int(n or 0)


async def compute_used_bytes_fast(
    session: AsyncSession, user_id,
) -> int:
    """Return a SQL-only estimate of the user's current footprint across the
    whole account — Drive (served + originals + trash) AND the E2E Vault —
    MINUS the MinIO-stat'd variants pass — that loop costs O(N videos) network
    round-trips and we'd be running it at every cloud-sync / vault-upload entry
    to enforce the quota. Variants are almost always a small fraction of total
    bytes and only apply to a subset of videos that the transcoder has
    finished, so the under-count is bounded; callers can apply a safety buffer
    if they care about the gap.

    Used by the cloud-sync quota check (audit CR-4) and the vault-upload gate
    (VLT-8). The public `/storage/usage` response still walks variants on top
    because the user IS owed a precise number.
    """
    served_q = select(
        func.coalesce(func.sum(Image.byte_size_served), 0),
    ).where(Image.user_id == user_id, Image.deleted_at.is_(None))
    served = int((await session.execute(served_q)).scalar_one() or 0)

    orig_q = select(
        func.coalesce(func.sum(Image.byte_size_original), 0),
    ).where(
        Image.user_id == user_id,
        Image.deleted_at.is_(None),
        Image.original_blob_key.is_not(None),
    )
    originals = int((await session.execute(orig_q)).scalar_one() or 0)

    trash_q = select(
        func.coalesce(
            func.sum(
                func.coalesce(Image.byte_size_served, 0)
                + case(
                    (Image.original_blob_key.is_not(None),
                     func.coalesce(Image.byte_size_original, 0)),
                    else_=0,
                )
            ),
            0,
        ),
    ).where(Image.user_id == user_id, Image.deleted_at.is_not(None))
    trash = int((await session.execute(trash_q)).scalar_one() or 0)

    vault, _ = await vault_used_bytes(session, user_id)

    return served + originals + trash + vault

# Video variant labels the transcoder emits. The DEFAULT served tier
# is what `served_blob_key` already points at — typically 1080p — and
# its size lives in `byte_size_served`, so we exclude it from the
# variant scan to avoid double-counting. We DO scan the others via
# MinIO stat() because their per-blob byte sizes aren't recorded in
# Postgres anywhere; the only way to know how big the 2160p tier is
# without re-encoding it is to ask the object store.
_VIDEO_VARIANT_LABELS = ("480p", "720p", "1080p", "1440p", "2160p")


async def _variant_bytes_for_user(
    session: AsyncSession, user: User,
) -> tuple[int, int]:
    """Return `(extra_bytes, extra_count)` for every video quality
    variant the user has on disk OTHER than the default tier.

    Stats each variant blob in MinIO. With <10K videos and the
    network-local MinIO this stays well under a second per call; for
    larger libraries we can move to a side table that records sizes
    at transcode time. The caller paginates by user so a noisy
    neighbour doesn't drag everyone else's panel.
    """
    rows = (
        await session.execute(
            select(Image.served_variants, Image.served_blob_key).where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
                Image.served_variants.is_not(None),
            )
        )
    ).all()
    total = 0
    count = 0
    for variants, default_key in rows:
        if not variants:
            continue
        for label in _VIDEO_VARIANT_LABELS:
            v = variants.get(label)
            if not v:
                continue
            # Per-variant value is `str` (key) in the legacy shape;
            # we tolerate the newer `{"key": ..., "bytes": ...}`
            # shape too in case a later commit migrates the JSON.
            key = v if isinstance(v, str) else v.get("key")
            if not key or key == default_key:
                continue
            size = 0
            if isinstance(v, dict) and isinstance(v.get("bytes"), int):
                size = int(v["bytes"])
            else:
                try:
                    stat = storage.client.stat_object(
                        storage.bucket_served, key,
                    )
                    size = int(getattr(stat, "size", 0) or 0)
                except Exception:
                    # Missing blob (already swept) — skip silently.
                    continue
            total += size
            count += 1
    return total, count


@router.get("/usage", response_model=StorageUsage)
async def storage_usage(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StorageUsage:
    # Per-category served bytes (default tier only) for the
    # existing stacked-bar viz. These also sum into `served_bytes`.
    cat_q = (
        select(
            Image.category,
            func.coalesce(func.sum(Image.byte_size_served), 0).label("bytes"),
            func.count().label("n"),
        )
        .where(Image.user_id == user.id, Image.deleted_at.is_(None))
        .group_by(Image.category)
    )
    cat_rows = (await session.execute(cat_q)).all()
    by_category: dict[str, int] = {}
    by_count: dict[str, int] = {}
    served_total = 0
    for category, bytes_, n in cat_rows:
        by_category[category] = int(bytes_)
        by_count[category] = int(n)
        served_total += int(bytes_)

    # Retained originals — sum AND count. The sweeper drops these
    # when `original_expires_at` passes, but until then the bytes
    # are real and the user should see them.
    orig_q = select(
        func.coalesce(func.sum(Image.byte_size_original), 0),
        func.count(),
    ).where(
        Image.user_id == user.id,
        Image.deleted_at.is_(None),
        Image.original_blob_key.is_not(None),
    )
    originals_bytes, originals_count = (await session.execute(orig_q)).one()
    originals_bytes = int(originals_bytes or 0)
    originals_count = int(originals_count or 0)

    # Soft-deleted (trash). Both the served default AND the retained
    # original count here — every byte still on disk.
    trash_q = select(
        func.coalesce(
            func.sum(
                func.coalesce(Image.byte_size_served, 0)
                + case(
                    (Image.original_blob_key.is_not(None),
                     func.coalesce(Image.byte_size_original, 0)),
                    else_=0,
                )
            ),
            0,
        ),
    ).where(
        Image.user_id == user.id,
        Image.deleted_at.is_not(None),
    )
    trash_bytes = int((await session.execute(trash_q)).scalar_one() or 0)

    # Extra video variants — MinIO stat() pass.
    variants_bytes, variants_count = await _variant_bytes_for_user(session, user)

    # End-to-end encrypted Vault file blobs — same account quota pool.
    vault_bytes, vault_count = await vault_used_bytes(session, user.id)

    # Linked-services summary. Surfaces "how much of your library is
    # mirrored from a cloud account" so the storage panel can
    # honestly say "we hold N MB of compressed copies for X files
    # synced from your Drive — the originals stay on Drive" instead
    # of the user seeing 1 GB and being confused by their 28 GB
    # Drive folder size. The cloud-providers themselves don't
    # expose total-folder-size for free, so we report what we DO
    # know: file count + last sync timestamp + the local footprint.
    from backend.models import CloudLink
    from sqlalchemy import and_
    linked: list[dict] = []
    cl_rows = (
        await session.execute(
            select(
                CloudLink.provider, CloudLink.status,
                CloudLink.last_synced_at, CloudLink.ai_opted_in,
            ).where(CloudLink.user_id == user.id)
        )
    ).all()
    for provider, status_v, last_synced_at, ai_opted_in in cl_rows:
        # Per-provider file + byte count from the images table —
        # only counts rows that survived as Image records, which is
        # exactly what the user's "used storage" sees.
        cnt_q = (
            select(
                func.count().label("n"),
                func.coalesce(func.sum(Image.byte_size_served), 0).label("served"),
                func.coalesce(func.sum(Image.byte_size_original), 0).label("originals"),
            )
            .where(
                Image.user_id == user.id,
                Image.source_provider == provider,
                Image.deleted_at.is_(None),
            )
        )
        n, served, originals = (await session.execute(cnt_q)).one()
        linked.append({
            "provider": provider,
            "status": status_v,
            "files_mirrored": int(n or 0),
            "served_bytes": int(served or 0),
            "originals_bytes": int(originals or 0),
            "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
            "ai_opted_in": bool(ai_opted_in),
        })

    used_total = (
        served_total + variants_bytes + originals_bytes + trash_bytes + vault_bytes
    )

    return StorageUsage(
        used_bytes=used_total,
        quota_bytes=user.quota_bytes if user.quota_bytes is not None else DEFAULT_QUOTA_BYTES,
        by_category=by_category,
        by_count=by_count,
        served_bytes=served_total,
        variants_bytes=variants_bytes,
        originals_bytes=originals_bytes,
        trash_bytes=trash_bytes,
        vault_bytes=vault_bytes,
        originals_count=originals_count,
        variants_count=variants_count,
        vault_count=vault_count,
        linked_services=linked,
    )


# ---------- reclamation actions ----------


class ReclaimResponse(BaseModel):
    bytes_freed: int
    items_dropped: int


@router.post("/free-originals", response_model=ReclaimResponse)
async def free_originals_now(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReclaimResponse:
    """Force-drop every retained original for this user. Same physical
    effect as the 30-day sweep but on demand.

    Implementation: set `original_expires_at = now()` on this user's
    rows where an original is still retained, then call the shared
    sweeper. The sweeper handles bytes counting, MinIO deletes, the
    `original_blob_key=NULL` update, and the audit log row — we just
    expose it to the user-facing API without superuser gating.
    """
    now = datetime.now(timezone.utc)
    await session.execute(
        update(Image)
        .where(
            Image.user_id == user.id,
            Image.deleted_at.is_(None),
            Image.original_blob_key.is_not(None),
        )
        .values(original_expires_at=now)
    )
    await session.flush()
    # Scope the sweep to THIS user only. Earlier the sweep ran
    # globally — fine for the daily cron path, but for a user-
    # triggered action it leaked aggregate info about other tenants
    # (the returned bytes_freed total could include another user's
    # bytes if their TTL happened to elapse between cron ticks) and
    # surprised the audit log. The cron path in app.py lifespan
    # still calls `sweep_expired_originals(session)` with no
    # user_id, which sweeps everyone.
    res = await sweep_expired_originals(session, user_id=user.id)
    session.add(
        AuditLog(
            user_id=user.id,
            action="storage.free_originals",
            details={
                "triggered_by_user": True,
                "bytes_freed": res.bytes_freed,
                "blobs_deleted": res.blobs_deleted,
                "blob_errors": res.blob_errors,
                "swept_at": now.isoformat(),
            },
        )
    )
    await session.commit()
    return ReclaimResponse(
        bytes_freed=res.bytes_freed,
        items_dropped=res.rows_nulled,
    )


@router.post("/free-variants", response_model=ReclaimResponse)
async def free_quality_variants(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReclaimResponse:
    """Drop the non-default video quality variants for this user.

    The default tier (`served_blob_key`) stays. Future downloads
    serve the default. The user can opt to re-transcode later via
    the AI maintenance panel if they retained originals and want
    multi-tier playback back.

    We delete blobs FIRST and only update the JSONB after each
    successful delete, so an interrupted run is idempotent — running
    it again just resumes from wherever the previous attempt left
    off.
    """
    rows = (
        await session.execute(
            select(
                Image.id, Image.served_blob_key, Image.served_variants,
            ).where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
                Image.served_variants.is_not(None),
            )
        )
    ).all()
    bytes_freed = 0
    items_dropped = 0
    for image_id, default_key, variants in rows:
        if not variants:
            continue
        kept: dict = {}
        for label, v in variants.items():
            key = v if isinstance(v, str) else (v or {}).get("key")
            if not key:
                continue
            if key == default_key:
                # Default tier — keep it; user expects playback to work.
                kept[label] = v
                continue
            # Stat for byte accounting, then delete.
            try:
                stat = storage.client.stat_object(
                    storage.bucket_served, key,
                )
                size = int(getattr(stat, "size", 0) or 0)
            except Exception:
                size = 0
            try:
                storage.delete(storage.bucket_served, key)
                bytes_freed += size
                items_dropped += 1
            except Exception:
                # Couldn't delete — keep the entry so a future run
                # tries again rather than orphaning the blob.
                kept[label] = v
        await session.execute(
            update(Image)
            .where(Image.id == image_id)
            .values(served_variants=(kept or None))
        )
    if items_dropped:
        session.add(
            AuditLog(
                user_id=user.id,
                action="storage.free_variants",
                details={
                    "bytes_freed": bytes_freed,
                    "variants_dropped": items_dropped,
                },
            )
        )
    await session.commit()
    return ReclaimResponse(
        bytes_freed=bytes_freed, items_dropped=items_dropped,
    )


# ---------- retention policy: get / set ----------


class RetentionPolicy(BaseModel):
    policy: str = Field(..., description="One of: immediate | 24h | 7d | 30d | forever")


class RetentionPolicyUpdate(BaseModel):
    """User-facing PATCH body. The `rebase_existing` flag asks us to
    re-compute `original_expires_at` on EVERY retained original this
    user owns according to the new policy (e.g. switching from 30d
    to 7d shrinks every TTL by 23 days, and the sweeper will drop
    anything already past due). Default True because that's the
    intuitive behaviour — "I picked a shorter retention" should
    actually shrink retention right away."""

    policy: str
    rebase_existing: bool = True


class RetentionPolicyResponse(BaseModel):
    policy: str
    bytes_freed: int = 0
    items_dropped: int = 0
    rebased: int = 0


@router.get("/retention-policy", response_model=RetentionPolicy)
async def get_retention_policy(
    user: Annotated[User, Depends(current_active_user)],
) -> RetentionPolicy:
    return RetentionPolicy(policy=user.original_retention_policy or "30d")


@router.patch("/retention-policy", response_model=RetentionPolicyResponse)
async def set_retention_policy(
    body: RetentionPolicyUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RetentionPolicyResponse:
    if body.policy not in RETENTION_POLICIES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"policy must be one of {list(RETENTION_POLICIES)}",
        )

    old_policy = user.original_retention_policy or "30d"
    user.original_retention_policy = body.policy
    await session.flush()

    rebased = 0
    bytes_freed = 0
    items_dropped = 0
    now = datetime.now(timezone.utc)

    if body.rebase_existing and body.policy != old_policy:
        # Recompute every retained-original TTL for this user. We
        # base the new TTL off `uploaded_at` (not `now`) so the
        # user's "I want 7d retention" honestly means 7d from
        # upload — files uploaded 8 days ago immediately become
        # expired and the next sweep drops them. That matches the
        # mental model better than "7d from when I changed the
        # setting", which would silently extend the life of old
        # uploads.
        from backend.models import Image as ImageModel

        rows = (
            await session.execute(
                select(ImageModel.id, ImageModel.uploaded_at)
                .where(
                    ImageModel.user_id == user.id,
                    ImageModel.deleted_at.is_(None),
                    ImageModel.original_blob_key.is_not(None),
                )
            )
        ).all()
        rebased = len(rows)
        # Bulk-update via a CASE expression would be cleaner but
        # SQLAlchemy 2.0's update() doesn't compose case-per-row
        # easily; the loop is fine for the ~thousands of files a
        # real user has.
        for image_id, uploaded_at in rows:
            new_expiry = policy_to_expiry(body.policy, now=uploaded_at)
            await session.execute(
                update(ImageModel)
                .where(ImageModel.id == image_id)
                .values(original_expires_at=new_expiry)
            )

        # If the new policy is "immediate" OR rebasing pushed some
        # expirations into the past, run the sweep right now so the
        # user sees the bytes drop without waiting 24 h. Scope to
        # this user only — same reasoning as `free_originals_now`.
        if body.policy == "immediate" or body.policy in {"24h", "7d", "30d"}:
            sweep = await sweep_expired_originals(session, user_id=user.id)
            bytes_freed = sweep.bytes_freed
            items_dropped = sweep.rows_nulled

    session.add(
        AuditLog(
            user_id=user.id,
            action="storage.set_retention_policy",
            details={
                "old_policy": old_policy,
                "new_policy": body.policy,
                "rebased": rebased,
                "bytes_freed": bytes_freed,
                "items_dropped": items_dropped,
                "set_at": now.isoformat(),
            },
        )
    )
    await session.commit()

    return RetentionPolicyResponse(
        policy=body.policy,
        bytes_freed=bytes_freed,
        items_dropped=items_dropped,
        rebased=rebased,
    )
