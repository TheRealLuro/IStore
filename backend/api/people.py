"""People + face cluster endpoints (Phase 4).

  GET    /people/                       — named persons + unlabeled clusters
  POST   /people/clusters/{cluster_id}  — name an unlabeled cluster
  PATCH  /people/{person_id}            — rename a person
  DELETE /people/{person_id}            — delete a person + their face rows
  POST   /people/backfill               — run Pass B on pending images for this user
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.consent import is_consent_active
from backend.db import SessionLocal, get_session
from backend.faces_pipeline import process_image_for_faces, update_person_centroid
from backend.models import AuditLog, Face, FaceDetection, Image, Person, User
from backend.storage import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/people", tags=["people"])


# ---------- schemas ----------

class PersonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    display_name: str | None
    face_count: int
    sample_face_id: int | None = None


class ClusterRead(BaseModel):
    cluster_id: int
    face_count: int
    sample_face_id: int


class PeopleResponse(BaseModel):
    persons: list[PersonRead]
    unlabeled_clusters: list[ClusterRead]
    total_faces: int


class NameClusterPayload(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)


class RenamePayload(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=120)


class BackfillResponse(BaseModel):
    queued: int
    consent_active: bool


# ---------- endpoints ----------

@router.get("/", response_model=PeopleResponse)
async def list_people(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PeopleResponse:
    # Sample face per person: pick by HIGHEST detection confidence
    # first, then by bbox area, then by face id.
    #
    # The earlier "largest bbox area first" heuristic backfired on
    # people whose biggest detection was a wide loose box that
    # captured shoulders + hair + a lot of background — `cover` on
    # the tile then framed empty space because the actual face was
    # tiny relative to the crop. Confidence correlates with TIGHT,
    # well-framed face detections (high-confidence = the detector
    # was sure it saw a face filling the box). Bbox area only kicks
    # in as a tiebreaker so a clearly-visible mid-size face still
    # beats a low-resolution one when confidence ties.
    best_face_sq = (
        select(Face.id)
        .join(FaceDetection, FaceDetection.face_id == Face.id)
        .where(
            Face.user_id == user.id,
            Face.person_id == Person.id,
            FaceDetection.user_id == user.id,
        )
        .order_by(
            FaceDetection.detection_confidence.desc().nullslast(),
            (FaceDetection.bbox_w * FaceDetection.bbox_h).desc(),
            Face.id.desc(),
        )
        .limit(1)
        .correlate(Person)
        .scalar_subquery()
    )
    p_stmt = (
        select(Person, best_face_sq.label("sample_face_id"))
        .where(Person.user_id == user.id)
        .order_by(Person.face_count.desc())
    )
    p_rows = (await session.execute(p_stmt)).all()
    persons = [
        PersonRead(
            id=p.id,
            display_name=p.display_name,
            face_count=p.face_count,
            sample_face_id=int(face_id) if face_id is not None else None,
        )
        for (p, face_id) in p_rows
    ]

    # Unlabeled clusters — same logic: highest confidence first,
    # then bbox area, then most recent. (Confidence > area for the
    # same reason as named persons above: tight, well-framed faces.)
    all_unlabeled = (
        await session.execute(
            select(Face.cluster_id, Face.id, FaceDetection.bbox_w, FaceDetection.bbox_h, FaceDetection.detection_confidence)
            .join(FaceDetection, FaceDetection.face_id == Face.id)
            .where(
                Face.user_id == user.id,
                Face.person_id.is_(None),
                Face.cluster_id.is_not(None),
                FaceDetection.user_id == user.id,
            )
            .order_by(
                Face.cluster_id,
                FaceDetection.detection_confidence.desc().nullslast(),
                (FaceDetection.bbox_w * FaceDetection.bbox_h).desc(),
                Face.id.desc(),
            )
        )
    ).all()
    best_per_cluster: dict[int, int] = {}
    counts_per_cluster: dict[int, int] = {}
    for cid, fid, _w, _h, _conf in all_unlabeled:
        if cid is None:
            continue
        if cid not in best_per_cluster:
            best_per_cluster[cid] = fid
        counts_per_cluster[cid] = counts_per_cluster.get(cid, 0) + 1

    clusters = [
        ClusterRead(
            cluster_id=int(cid),
            face_count=int(counts_per_cluster[cid]),
            sample_face_id=int(fid),
        )
        for cid, fid in sorted(
            best_per_cluster.items(),
            key=lambda kv: counts_per_cluster.get(kv[0], 0),
            reverse=True,
        )
    ]

    total = (
        await session.execute(
            select(func.count(Face.id)).where(Face.user_id == user.id)
        )
    ).scalar_one()

    return PeopleResponse(
        persons=persons,
        unlabeled_clusters=clusters,
        total_faces=int(total or 0),
    )


@router.post("/clusters/{cluster_id}", response_model=PersonRead)
async def name_cluster(
    cluster_id: int,
    payload: NameClusterPayload,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Person:
    name = payload.display_name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name required")

    # Reuse an existing person with the same name, or create one.
    existing = (
        await session.execute(
            select(Person).where(
                Person.user_id == user.id, Person.display_name == name
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        person = existing
    else:
        person = Person(user_id=user.id, display_name=name, face_count=0)
        session.add(person)
        await session.flush()

    # Reassign every face in this cluster.
    res = await session.execute(
        update(Face)
        .where(
            Face.user_id == user.id,
            Face.cluster_id == cluster_id,
            Face.person_id.is_(None),
        )
        .values(person_id=person.id)
    )
    n = int(res.rowcount or 0)

    # Recompute the person's centroid from all their faces (including any
    # previously named under the same display_name — see "Me" + "Me" merge).
    # This is the actual "training signal" naming gives us.
    await session.flush()
    await update_person_centroid(session, user.id, person.id)

    session.add(
        AuditLog(
            user_id=user.id,
            action="people.name_cluster",
            details={"cluster_id": cluster_id, "person_id": person.id, "faces": n},
        )
    )
    await session.commit()
    await session.refresh(person)
    return person


@router.patch("/{person_id}", response_model=PersonRead)
async def rename_person(
    person_id: int,
    payload: RenamePayload,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Person:
    person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.user_id == user.id)
        )
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found")
    person.display_name = payload.display_name.strip()
    await session.commit()
    await session.refresh(person)
    return person


@router.delete(
    "/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_person(
    person_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    # Drop crops + face rows for this person.
    crops = (
        await session.execute(
            select(FaceDetection.crop_blob_key)
            .where(
                FaceDetection.user_id == user.id,
                FaceDetection.crop_blob_key.is_not(None),
                FaceDetection.face_id.in_(
                    select(Face.id).where(
                        Face.user_id == user.id, Face.person_id == person_id
                    )
                ),
            )
        )
    ).scalars().all()

    fd_count = (
        await session.execute(
            delete(FaceDetection).where(
                FaceDetection.user_id == user.id,
                FaceDetection.face_id.in_(
                    select(Face.id).where(
                        Face.user_id == user.id, Face.person_id == person_id
                    )
                ),
            )
        )
    ).rowcount or 0
    face_count = (
        await session.execute(
            delete(Face).where(Face.user_id == user.id, Face.person_id == person_id)
        )
    ).rowcount or 0
    await session.execute(
        delete(Person).where(Person.id == person_id, Person.user_id == user.id)
    )
    for key in crops:
        try:
            storage.delete(storage.bucket_faces, key)
        except Exception:
            pass
    session.add(
        AuditLog(
            user_id=user.id,
            action="people.delete_person",
            details={
                "person_id": person_id,
                "face_detections_deleted": int(fd_count),
                "faces_deleted": int(face_count),
                "crops_deleted": len(crops),
            },
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/backfill", response_model=BackfillResponse)
async def backfill(
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BackfillResponse:
    """Process every image with pending_face_scan=True for this user.

    Runs in a background task so the request returns immediately. Returns
    the count of images that will be processed.
    """
    active = await is_consent_active(session, user.id)
    if not active:
        return BackfillResponse(queued=0, consent_active=False)

    pending = (
        await session.execute(
            select(Image.id).where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
                Image.pending_face_scan.is_(True),
                Image.category == "image",
            )
        )
    ).scalars().all()
    image_ids = [i for i in pending]

    if image_ids:
        background.add_task(_run_backfill, user.id, image_ids)

    return BackfillResponse(queued=len(image_ids), consent_active=True)


class RescanResponse(BaseModel):
    queued: int
    consent_active: bool
    cleared_existing_faces: int


@router.post("/rescan", response_model=RescanResponse)
async def rescan(
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RescanResponse:
    """Re-arm every image and re-run Pass B on all of them.

    Useful after a model upgrade, after `pending_face_scan` got pre-emptively
    cleared by an old gating rule, or just to redo clustering with fresh
    weights. Existing face rows for the user are dropped first so the rescan
    starts clean — this keeps cluster IDs from drifting and avoids re-counting
    the same face twice. Named persons survive (their `display_name` is the
    user's input and we never want to lose that), but their `face_count`
    rebuilds as faces re-attach via similarity match in faces_pipeline.
    """
    active = await is_consent_active(session, user.id)
    if not active:
        return RescanResponse(
            queued=0, consent_active=False, cleared_existing_faces=0,
        )

    # Collect crop blob keys before deleting.
    crop_keys = (
        await session.execute(
            select(FaceDetection.crop_blob_key).where(
                FaceDetection.user_id == user.id,
                FaceDetection.crop_blob_key.is_not(None),
            )
        )
    ).scalars().all()

    # Drop face_detections + faces. Persons survive but lose face_count.
    fd_count = (
        await session.execute(
            delete(FaceDetection).where(FaceDetection.user_id == user.id)
        )
    ).rowcount or 0
    face_count = (
        await session.execute(
            delete(Face).where(Face.user_id == user.id)
        )
    ).rowcount or 0
    await session.execute(
        update(Person).where(Person.user_id == user.id).values(face_count=0)
    )

    for key in crop_keys:
        try:
            storage.delete(storage.bucket_faces, key)
        except Exception:
            pass

    # Re-arm pending_face_scan on every image so backfill picks them all up.
    await session.execute(
        text(
            """
            UPDATE images
            SET pending_face_scan = true
            WHERE user_id = :uid
              AND deleted_at IS NULL
              AND category = 'image'
            """
        ),
        {"uid": user.id},
    )
    pending = (
        await session.execute(
            select(Image.id).where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
                Image.pending_face_scan.is_(True),
                Image.category == "image",
            )
        )
    ).scalars().all()
    image_ids = [i for i in pending]

    session.add(
        AuditLog(
            user_id=user.id,
            action="people.rescan_all",
            details={
                "images_queued": len(image_ids),
                "face_detections_deleted": int(fd_count),
                "faces_deleted": int(face_count),
                "crops_deleted": len(crop_keys),
            },
        )
    )
    await session.commit()

    if image_ids:
        background.add_task(_run_backfill, user.id, image_ids)

    return RescanResponse(
        queued=len(image_ids),
        consent_active=True,
        cleared_existing_faces=int(face_count),
    )


async def _run_backfill(user_id, image_ids: list) -> None:
    """Background task: process each image's faces. Uses its own session."""
    from backend.image import storage as _storage  # avoid circular at import

    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            return
        for image_id in image_ids:
            image = (
                await session.execute(select(Image).where(Image.id == image_id))
            ).scalar_one_or_none()
            if image is None or not image.pending_face_scan:
                continue
            try:
                raw = _storage.get(_storage.bucket_originals, image.original_blob_key) \
                    if image.original_blob_key else \
                    _storage.get(_storage.bucket_served, image.served_blob_key)
            except Exception:
                logger.exception("Backfill: could not fetch %s", image_id)
                continue
            try:
                await process_image_for_faces(session, user, image, raw)
            except Exception:
                logger.exception("Backfill: face pipeline failed for %s", image_id)
                continue
            # Yield to the event loop so we don't block.
            await asyncio.sleep(0)
