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


async def hard_delete_images(
    session: AsyncSession,
    *,
    user_id: UUID,
    image_ids: list[UUID],
    audit_action: str,
) -> DeleteImagesResult:
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
    blob_keys: list[tuple[str, str]] = []
    for img in images:
        if img.original_blob_key:
            blob_keys.append((storage.bucket_originals, img.original_blob_key))
        if img.served_blob_key and img.served_blob_key != img.original_blob_key:
            blob_keys.append((storage.bucket_served, img.served_blob_key))
        if img.thumbnail_blob_key:
            blob_keys.append((storage.bucket_served, img.thumbnail_blob_key))

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
            blob_keys.append((storage.bucket_faces, crop_key))

    blobs_deleted = 0
    blob_errors = 0
    for bucket, key in blob_keys:
        try:
            storage.delete(bucket, key)
            blobs_deleted += 1
        except Exception as exc:
            blob_errors += 1
            logger.warning("delete image blob failed %s/%s: %s", bucket, key, exc)

    await session.execute(
        delete(CloudFile).where(CloudFile.user_id == user_id, CloudFile.local_image_id.in_(ids))
    )

    # Deleting images cascades image_tags, image_geo, feedback_events and
    # face_detections. Faces/persons are cleaned explicitly afterwards.
    await session.execute(delete(Image).where(Image.user_id == user_id, Image.id.in_(ids)))

    face_deleted = 0
    if face_ids:
        res = await session.execute(delete(Face).where(Face.user_id == user_id, Face.id.in_(face_ids)))
        face_deleted = int(res.rowcount or 0)

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
            delete(Person).where(Person.user_id == user_id, Person.id.in_(orphan_person_ids))
        )
        person_deleted = int(res.rowcount or 0)

    await session.execute(
        update(Person)
        .where(Person.user_id == user_id)
        .values(
            face_count=select(func.count(Face.id))
            .where(Face.user_id == user_id, Face.person_id == Person.id)
            .scalar_subquery()
        )
    )

    # We cannot safely subtract image-specific reward history after the row
    # cascade, so reset this user's learned state.
    bandit = await session.execute(delete(BanditState).where(BanditState.user_id == user_id))
    bandit_reset = bool(bandit.rowcount)

    await add_audit(
        session,
        user_id=user_id,
        action=audit_action,
        details={
            "image_ids": [str(i) for i in ids],
            "blobs_deleted": blobs_deleted,
            "blob_errors": blob_errors,
            "faces_deleted": face_deleted,
            "persons_deleted": person_deleted,
            "bandit_reset": bandit_reset,
        },
    )
    return DeleteImagesResult(
        image_ids=ids,
        blobs_deleted=blobs_deleted,
        blob_errors=blob_errors,
        faces_deleted=face_deleted,
        persons_deleted=person_deleted,
        bandit_reset=bandit_reset,
    )
