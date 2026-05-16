"""§A5 — "deletion that actually deletes."

`hard_delete_images` is the single source of truth for purging image
data. The A5 checklist (todo.md §A5) lists every artifact that must
disappear when a user deletes an image or their account:

  * `originals` bucket object              ← blob delete
  * `served` bucket variant                ← blob delete
  * thumbnail blob in `served`             ← blob delete
  * CLIP embedding row                     ← cascades on Image row delete
  * AI summary text / topic / points       ← cascades on Image row delete
  * EXIF row → image_geo (GPS + timestamp) ← FK CASCADE on image_id
  * detected face crops in `faces` bucket  ← collected before row delete
  * face embeddings (faces.embedding)      ← explicit cleanup
  * face detections (face_detections)      ← FK CASCADE on image_id
  * person rows with no remaining faces    ← orphan sweep
  * image_tags many-to-many                ← FK CASCADE on image_id
  * share_grants for these images          ← FK CASCADE on image_id
  * feedback_events for these images       ← FK CASCADE on image_id
  * cloud_files pointers (foreign-keyed
    with SET NULL)                         ← explicit delete by user_id
  * bandit reward state                    ← opt-in via `reset_bandit=True`
                                             (account-delete only;
                                             single-image delete keeps
                                             learned weights)
  * audit_log entries                      ← NOT deleted (legal retention,
                                             append-only trigger
                                             enforces this anyway)

`audit_action` is emitted as a single row capturing the counts so a
later operator can trace what disappeared and why without scraping
multi-step server logs.

Idempotency:
  Calling this function twice with the same ids is a no-op the second
  time — the first call removes the rows and blob keys, the second
  call's `select(Image).where(...)` returns empty and we bail at
  line 65 with a zero-result DeleteImagesResult.

Backups:
  The `originals` and `served` buckets are part of the nightly
  encrypted backup (see SECURITY.md "Encrypted backups"). Once a row
  is deleted from the DB, subsequent backups won't include it, but
  past backups retained per the documented retention window WILL
  still contain the bytes. Operators are required to honor a user's
  hard-delete by either restoring + re-purging a backup, OR letting
  the encrypted backup age out per the retention window. Either path
  satisfies §A5 "Backup invalidation eventually (document the
  retention)."
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.models import (
    BanditState,
    CloudFile,
    Face,
    FaceDetection,
    Image,
    Person,
)
from backend.storage import storage

logger = logging.getLogger(__name__)


@dataclass
class DeleteImagesResult:
    image_ids: list[UUID] = field(default_factory=list)
    blobs_deleted: int = 0
    blob_errors: int = 0
    faces_deleted: int = 0
    persons_deleted: int = 0
    bandit_reset: bool = False
    # Per-bucket counts so an integration test can assert each one
    # without re-reading the bucket inventory. Defense-in-depth proof.
    originals_deleted: int = 0
    served_deleted: int = 0
    thumbnails_deleted: int = 0
    face_crops_deleted: int = 0


async def hard_delete_images(
    session: AsyncSession,
    *,
    user_id: UUID,
    image_ids: list[UUID],
    audit_action: str,
    reset_bandit: bool = False,
) -> DeleteImagesResult:
    """Hard-delete a set of images owned by `user_id`.

    `reset_bandit=False` (default) preserves the user's learned
    bandit weights — appropriate for single-image / bulk delete where
    the per-(user, arm) statistics remain meaningful after one image
    leaves. `reset_bandit=True` (account-level delete) drops the
    entire `bandit_state` for the user because the account itself is
    going away.
    """
    if not image_ids:
        return DeleteImagesResult()

    images = (
        await session.execute(
            select(Image).where(Image.user_id == user_id, Image.id.in_(image_ids))
        )
    ).scalars().all()
    if not images:
        return DeleteImagesResult()

    ids = [img.id for img in images]

    # Phase 1 — enumerate every blob this set of images owns.
    # We collect (bucket, key, kind) triples so we can report per-kind
    # counts in the result (originals vs. served vs. thumbnails vs.
    # face crops) — useful for the A5 integration test + operator
    # forensics.
    blob_targets: list[tuple[str, str, str]] = []
    for img in images:
        if img.original_blob_key:
            blob_targets.append((storage.bucket_originals, img.original_blob_key, "original"))
        if img.served_blob_key and img.served_blob_key != img.original_blob_key:
            blob_targets.append((storage.bucket_served, img.served_blob_key, "served"))
        if img.thumbnail_blob_key:
            blob_targets.append((storage.bucket_served, img.thumbnail_blob_key, "thumbnail"))

    # Face detections live in their own table with FK CASCADE on
    # images.id — the row deletion below will drop them automatically.
    # We pre-fetch the rows BEFORE the cascade so we know which face
    # crops in MinIO need to be deleted (the FK doesn't reach into
    # object storage) and which `faces` rows are at risk of being
    # orphaned (we delete those explicitly).
    face_rows = (
        await session.execute(
            select(FaceDetection.face_id, FaceDetection.crop_blob_key).where(
                FaceDetection.user_id == user_id,
                FaceDetection.image_id.in_(ids),
            )
        )
    ).all()
    face_ids = [fid for fid, _ in face_rows if fid is not None]
    for _, crop_key in face_rows:
        if crop_key:
            blob_targets.append((storage.bucket_faces, crop_key, "face_crop"))

    # Phase 2 — delete blobs. Best-effort: a single failure logs and
    # continues so a transient bucket outage on one object doesn't
    # block the row cleanup. The per-kind counters let callers verify
    # which class of object was affected.
    kind_count: dict[str, int] = {"original": 0, "served": 0, "thumbnail": 0, "face_crop": 0}
    blob_errors = 0
    for bucket, key, kind in blob_targets:
        try:
            storage.delete(bucket, key)
            kind_count[kind] = kind_count.get(kind, 0) + 1
        except Exception as exc:
            blob_errors += 1
            logger.warning(
                "delete image blob failed bucket=%s key=%s kind=%s err=%s",
                bucket, key, kind, exc,
            )

    # Phase 3 — drop cloud_files pointers for these images. The FK
    # column has ondelete=SET NULL, so without this the rows survive
    # account deletion with a NULL local_image_id; we'd rather drop
    # them since they no longer point anywhere meaningful.
    await session.execute(
        delete(CloudFile).where(
            CloudFile.user_id == user_id, CloudFile.local_image_id.in_(ids),
        )
    )

    # Phase 4 — delete the image rows. FK CASCADEs run for:
    #   * image_tags          (CASCADE on image_id)
    #   * image_geo           (CASCADE on image_id) — GPS + capture timestamp
    #   * feedback_events     (CASCADE on image_id)
    #   * share_grants        (CASCADE on image_id)
    #   * face_detections     (CASCADE on image_id)
    # Columns inside `images` (clip_embedding, summary, summary_topic,
    # summary_points, summary_signals, scene_label, etc.) disappear
    # with the row itself.
    await session.execute(
        delete(Image).where(Image.user_id == user_id, Image.id.in_(ids))
    )

    # Phase 5 — clean up `faces` rows whose detections all vanished.
    # `Face.person_id` is SET NULL on Person delete, so person orphan
    # cleanup below stays safe regardless of order.
    face_deleted = 0
    if face_ids:
        res = await session.execute(
            delete(Face).where(Face.user_id == user_id, Face.id.in_(face_ids))
        )
        face_deleted = int(res.rowcount or 0)

    # Phase 6 — orphan-person sweep. Any `persons` row whose face count
    # has dropped to zero is no longer associated with any visible
    # biometric data; drop it. Per the A5 checklist: "Person rows that
    # have no remaining faces."
    orphan_person_ids = (
        await session.execute(
            select(Person.id)
            .outerjoin(Face, (Face.person_id == Person.id) & (Face.user_id == user_id))
            .where(Person.user_id == user_id)
            .group_by(Person.id)
            .having(func.count(Face.id) == 0)
        )
    ).scalars().all()
    person_deleted = 0
    if orphan_person_ids:
        res = await session.execute(
            delete(Person).where(
                Person.user_id == user_id, Person.id.in_(orphan_person_ids)
            )
        )
        person_deleted = int(res.rowcount or 0)

    # Phase 7 — recompute the cached `persons.face_count` so the
    # admin/People-tab views don't show stale counters.
    await session.execute(
        update(Person)
        .where(Person.user_id == user_id)
        .values(
            face_count=select(func.count(Face.id))
            .where(Face.user_id == user_id, Face.person_id == Person.id)
            .scalar_subquery()
        )
    )

    # Phase 8 — bandit reward state. Per the A5 checklist:
    #   "Bandit reward / arm history (or anonymized)"
    # The bandit weights are per-(user, arm), NOT per-image, so a
    # single-image delete doesn't need to reset them. For account
    # deletion the caller passes `reset_bandit=True` and we wipe the
    # whole user's learned state; otherwise we leave the matrices
    # alone because they encode preferences across the entire
    # library, not pointers to specific bytes.
    bandit_reset = False
    if reset_bandit:
        res = await session.execute(
            delete(BanditState).where(BanditState.user_id == user_id)
        )
        bandit_reset = bool(res.rowcount)

    # Phase 9 — single audit row capturing the counts. The audit_log
    # is append-only (migration 0016 trigger) and intentionally
    # outlives the user row per the "Audit log entries: referenced but
    # NOT deleted (legal retention)" clause in A5.
    await add_audit(
        session,
        user_id=user_id,
        action=audit_action,
        details={
            "image_ids": [str(i) for i in ids],
            "image_count": len(ids),
            "blobs_deleted": sum(kind_count.values()),
            "originals_deleted": kind_count["original"],
            "served_deleted": kind_count["served"],
            "thumbnails_deleted": kind_count["thumbnail"],
            "face_crops_deleted": kind_count["face_crop"],
            "blob_errors": blob_errors,
            "faces_deleted": face_deleted,
            "persons_deleted": person_deleted,
            "bandit_reset": bandit_reset,
        },
    )
    return DeleteImagesResult(
        image_ids=ids,
        blobs_deleted=sum(kind_count.values()),
        blob_errors=blob_errors,
        faces_deleted=face_deleted,
        persons_deleted=person_deleted,
        bandit_reset=bandit_reset,
        originals_deleted=kind_count["original"],
        served_deleted=kind_count["served"],
        thumbnails_deleted=kind_count["thumbnail"],
        face_crops_deleted=kind_count["face_crop"],
    )
