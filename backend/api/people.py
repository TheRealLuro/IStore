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
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import current_active_user
from backend.consent import is_consent_active, is_scope_active
from backend.db import SessionLocal, get_session
from backend.faces_pipeline import process_image_for_faces, update_person_centroid
from backend.image import fetch_original
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
    # Photo count per person is the DISTINCT number of images linked to
    # this person via EITHER (a) a face detection that maps to a face
    # row, OR (b) a manual ImagePerson row from the multi-select tag
    # flow. Previously this read `Person.face_count` (a denormalized
    # counter incremented per Face row), which over-counted group
    # photos with multiple detections of the same person AND missed
    # manually-tagged photos with no detected face. The user saw e.g.
    # "29 photos" in the People tab but "16" in the drill-in. Compute
    # both numbers from the same source so they always match.
    from backend.models import ImagePerson as _IP

    # Per-person scalar subquery: UNION the two image-id sources, then
    # count rows. The UNION (not UNION ALL) deduplicates, so an image
    # that's both face-detected AND manually tagged for this person
    # counts once.
    from sqlalchemy import literal_column, union

    union_sq = union(
        select(FaceDetection.image_id)
            .join(Face, Face.id == FaceDetection.face_id)
            .where(Face.user_id == user.id, Face.person_id == Person.id),
        select(_IP.image_id)
            .where(_IP.user_id == user.id, _IP.person_id == Person.id),
    ).alias("person_image_ids")
    photo_count_sq = (
        select(func.count(literal_column("image_id")))
        .select_from(union_sq)
        .correlate(Person)
        .scalar_subquery()
    )

    p_stmt = (
        select(
            Person,
            best_face_sq.label("sample_face_id"),
            photo_count_sq.label("photo_count"),
        )
        .where(Person.user_id == user.id)
        .order_by(photo_count_sq.desc())
    )
    p_rows = (await session.execute(p_stmt)).all()
    persons = [
        PersonRead(
            id=p.id,
            display_name=p.display_name,
            face_count=int(photo_count or 0),  # now: distinct image count
            sample_face_id=int(face_id) if face_id is not None else None,
        )
        for (p, face_id, photo_count) in p_rows
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


class DetectAndLabelBody(BaseModel):
    """Body for `POST /people/detect-and-label`.

    Triggers face detection on the listed images (synchronously, in a
    worker thread per image) and assigns every newly-detected face to a
    Person with `display_name`. Existing detections on those images are
    left alone — this is purely additive.
    """
    image_ids: list[UUID] = Field(..., min_length=1, max_length=50)
    display_name: str = Field(..., min_length=1, max_length=120)


@router.post("/detect-and-label")
async def detect_and_label(
    body: DetectAndLabelBody,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Multi-select bulk action: run face detection on the selected
    images and label every detected face with `display_name`.

    The user picks photos that share one person, clicks "Detect person",
    types a name. We run the detector on each image, then assign every
    *newly-created* face to the named Person. Re-uses an existing
    Person row with the same display_name if one is already there, so
    the user doesn't end up with "Alice (2)" / "Alice (3)" after a
    couple of bulk passes.

    Requires the user to have granted `face_recognition` consent —
    same gate as the per-image face scan worker.
    """
    if not await is_scope_active(session, user.id, "face_recognition"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Grant face recognition consent first (Settings → Privacy).",
        )

    name = body.display_name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "display_name is required")

    # Find or create the target person.
    target = (
        await session.execute(
            select(Person).where(
                Person.user_id == user.id, Person.display_name == name
            )
        )
    ).scalar_one_or_none()
    if target is None:
        target = Person(user_id=user.id, display_name=name)
        session.add(target)
        await session.flush()

    # Capture the existing Face ids per image BEFORE running the scan
    # so we can identify the new ones afterwards.
    pre_existing: dict[UUID, set[int]] = {}
    for image_id in body.image_ids:
        rows = (
            await session.execute(
                select(FaceDetection.face_id).where(
                    FaceDetection.image_id == image_id,
                    FaceDetection.user_id == user.id,
                    FaceDetection.face_id.is_not(None),
                )
            )
        ).scalars().all()
        pre_existing[image_id] = {int(r) for r in rows}

    from backend.faces_pipeline import process_image_for_faces

    async def _post_face_ids(image_id) -> set[int]:
        rows = (
            await session.execute(
                select(FaceDetection.face_id).where(
                    FaceDetection.image_id == image_id,
                    FaceDetection.user_id == user.id,
                    FaceDetection.face_id.is_not(None),
                )
            )
        ).scalars().all()
        return {int(r) for r in rows}

    # The manual ImagePerson row is the user's intent: "this image is
    # of this person, regardless of whether face detection finds a
    # face." We INSERT one per selected image up-front so the
    # per-person photo count matches what the user selected — even
    # when the cascade detector returns empty. Use ON CONFLICT DO
    # NOTHING so re-tagging the same image is idempotent.
    from backend.models import ImagePerson
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    ip_stmt = pg_insert(ImagePerson).values(
        [
            {
                "image_id": image_id,
                "person_id": target.id,
                "user_id": user.id,
                "source": "manual",
            }
            for image_id in body.image_ids
        ]
    ).on_conflict_do_nothing(index_elements=["image_id", "person_id"])
    await session.execute(ip_stmt)

    images_scanned = 0
    new_faces = 0
    images_no_face = []  # images where every pass came back empty
    for image_id in body.image_ids:
        image = (
            await session.execute(
                select(Image).where(
                    Image.id == image_id,
                    Image.user_id == user.id,
                    Image.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if image is None:
            continue
        try:
            raw, _ = await fetch_original(image)
        except Exception:
            logger.exception("detect_and_label: failed to fetch %s", image_id)
            continue

        # First pass — normal detector + auto-cascade for high
        # face_likelihood images.
        try:
            await process_image_for_faces(session, user, image, raw)
            images_scanned += 1
        except Exception:
            logger.exception("detect_and_label: face pipeline failed on %s", image_id)
            continue

        post_ids = await _post_face_ids(image_id)
        delta = post_ids - pre_existing.get(image_id, set())

        # Retry pass — user explicitly told us this person is in the
        # image, so force the cascade detector at its lowest threshold
        # regardless of face_likelihood (the normal flow gates cascade
        # on likelihood ≥ 0.5). This rescues B&W close-ups, profile
        # shots, partial-face crops, and other "the model isn't sure
        # this is a face" cases.
        if not delta:
            try:
                from backend.vision.faces import detect_with_cascade
                from backend.vision.inference_pool import run_in_inference_pool

                cascaded, stage = await run_in_inference_pool(
                    detect_with_cascade, raw,
                )
                if cascaded:
                    logger.info(
                        "detect_and_label: cascade rescued %s via %s (%d faces)",
                        image_id, stage, len(cascaded),
                    )
                    await process_image_for_faces(
                        session, user, image, raw,
                        detections=cascaded,
                        min_confidence=0.05,
                    )
                    post_ids = await _post_face_ids(image_id)
                    delta = post_ids - pre_existing.get(image_id, set())
            except Exception:
                logger.exception(
                    "detect_and_label: cascade retry failed on %s", image_id,
                )

        if not delta:
            # Still no face after the cascade. Record the image so the
            # FE can show the user which photos got force-labeled
            # without an embedding to anchor on. Future uploads of the
            # same person will still cluster via centroid; this entry
            # exists for the user's awareness ("these N didn't have a
            # detectable face, but we tagged them as you asked").
            images_no_face.append(str(image_id))
            continue

        # Assign the new Face rows to the target Person.
        await session.execute(
            sa_update(Face)
            .where(Face.user_id == user.id, Face.id.in_(delta))
            .values(person_id=target.id)
        )
        new_faces += len(delta)

    # Recompute the Person's centroid from EVERY face attached to them
    # (the new batch plus any prior labelings). Without this step, the
    # face-matching pipeline can't auto-cluster future detections to
    # this person — the user would have to keep manually re-labeling.
    # With the centroid up to date, `match_named_person` picks this
    # person whenever a new face's embedding comes within
    # SAME_PERSON_THRESHOLD_CENTROID (~0.40 cosine) of it.
    if new_faces > 0:
        try:
            await update_person_centroid(session, user.id, target.id)
        except Exception:
            logger.exception(
                "detect_and_label: centroid update failed for person=%s",
                target.id,
            )

    await add_audit(
        session,
        user_id=user.id,
        action="people.detect_and_label",
        details={
            "image_count": images_scanned,
            "new_faces": new_faces,
            "no_face_count": len(images_no_face),
            "person_id": target.id,
            "display_name": name,
        },
    )
    await session.commit()
    return {
        "person_id": target.id,
        "display_name": name,
        "images_scanned": images_scanned,
        "new_faces": new_faces,
        "no_face_images": images_no_face,
    }


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


class RegenerateCropsResponse(BaseModel):
    queued: int


@router.post("/regenerate-crops", response_model=RegenerateCropsResponse)
async def regenerate_crops(
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 1000,
) -> RegenerateCropsResponse:
    """Re-crop every existing face detection with landmark-aligned framing.

    The previous `_crop_jpeg` produced rectangular 15%-padded crops
    that left foreheads cut off on selfies. The new alignment
    centers the crop between the eyes and frames head-and-shoulders
    in a 256×256 square. This route walks the user's existing
    detections, re-renders each crop in place via PyMuPDF, and
    overwrites the existing MinIO key so `GET /faces/{id}/crop`
    serves the upgraded JPEG without any DB row changes.

    Cropping is bounded by `limit` so a 50k-face library doesn't
    lock a single request — repeat the call until queued=0.
    """
    rows = (
        await session.execute(
            select(
                FaceDetection.id,
                FaceDetection.image_id,
                FaceDetection.bbox_x,
                FaceDetection.bbox_y,
                FaceDetection.bbox_w,
                FaceDetection.bbox_h,
                FaceDetection.crop_blob_key,
                FaceDetection.landmarks_json,
            )
            .where(
                FaceDetection.user_id == user.id,
                FaceDetection.crop_blob_key.is_not(None),
            )
            .order_by(FaceDetection.id.desc())
            .limit(limit)
        )
    ).all()

    if rows:
        jobs = [
            (
                int(r[0]),
                r[1],
                int(r[2]),
                int(r[3]),
                int(r[4]),
                int(r[5]),
                r[6],
                list(r[7]) if r[7] else None,
            )
            for r in rows
        ]
        background.add_task(_run_regenerate_crops, user.id, jobs)
    return RegenerateCropsResponse(queued=len(rows))


async def _run_regenerate_crops(user_id, jobs: list) -> None:
    """Background worker: re-crop each detection in place.

    `jobs` is a list of tuples
    `(det_id, image_id, bbox_x, bbox_y, bbox_w, bbox_h, crop_key, landmarks)`.
    We fetch the original image, re-render with the new alignment,
    and overwrite the existing crop blob. The bytes flowing into
    MinIO replace what `GET /faces/{id}/crop` serves — no DB churn.
    """
    from backend.image import storage as _storage
    from backend.models import Image as ImageModel
    from backend.vision.faces import render_avatar_from_image

    image_cache: dict = {}

    async with SessionLocal() as session:
        for det_id, image_id, bx, by, bw, bh, crop_key, landmarks in jobs:
            raw = image_cache.get(image_id)
            if raw is None:
                img = (
                    await session.execute(
                        select(ImageModel).where(ImageModel.id == image_id)
                    )
                ).scalar_one_or_none()
                if img is None:
                    continue
                try:
                    raw = (
                        _storage.get(_storage.bucket_originals, img.original_blob_key)
                        if img.original_blob_key
                        else _storage.get(_storage.bucket_served, img.served_blob_key)
                    )
                except Exception:
                    logger.exception(
                        "regenerate-crops: could not fetch image %s", image_id
                    )
                    continue
                # Keep only the latest image in memory — multi-face
                # photos benefit, but caching every fetch would balloon
                # RAM on a long worker run.
                image_cache.clear()
                image_cache[image_id] = raw

            try:
                new_crop = await asyncio.to_thread(
                    render_avatar_from_image, raw, bx, by, bw, bh, landmarks
                )
            except Exception:
                logger.exception(
                    "regenerate-crops: render failed for detection %s", det_id
                )
                continue

            try:
                storage.put(
                    storage.bucket_faces,
                    crop_key,
                    new_crop,
                    "image/jpeg",
                    sse_scope="biometric",
                )
            except Exception:
                logger.exception(
                    "regenerate-crops: upload failed for detection %s", det_id
                )
                continue

            await asyncio.sleep(0)
