import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.consent import is_consent_active, is_scope_active
from backend.db import SessionLocal, get_session
from backend.config import settings
from backend.deletion import hard_delete_images
from backend.image import fetch_original, fetch_served, store_upload
from backend.models import (
    Face,
    FaceDetection,
    Image,
    ImageGeo,
    ImageTag,
    Person,
    Tag,
    User,
)
from backend.schemas import ImageMove, ImageRead, StatusSet
from backend.security import enforce_upload_limits
from backend.signed_urls import make_signed_download, verify_download
from backend.storage import storage
from backend.upload_validation import UploadValidationError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/images", tags=["images"])

# FastAPI BackgroundTasks delay the HTTP response until they finish — that
# turns the upload progress bar into a 10-30 s "Processing…" stall while
# Florence-2/RetinaFace run. We instead schedule via asyncio.create_task so
# the response flushes immediately. The strong-reference set keeps tasks
# alive against GC (asyncio docs §"Creating Tasks" warning #1).
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _detach(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _run_face_scan_one(user_id: UUID, image_id: UUID) -> None:
    """Background task: detect faces on one image after the upload response
    has already been sent. Loads its own session + re-fetches the image so it
    isn't tied to the request's transaction lifecycle."""
    from backend.faces_pipeline import process_image_for_faces

    async with SessionLocal() as session:
        active = await is_consent_active(session, user_id)
        if not active:
            return
        image = (
            await session.execute(select(Image).where(Image.id == image_id))
        ).scalar_one_or_none()
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if image is None or user is None or not image.pending_face_scan:
            return
        try:
            raw = (
                storage.get(storage.bucket_originals, image.original_blob_key)
                if image.original_blob_key
                else storage.get(storage.bucket_served, image.served_blob_key)
            )
        except Exception:
            logger.exception("Face scan: could not fetch blob for %s", image_id)
            return
        try:
            await process_image_for_faces(session, user, image, raw)
        except Exception:
            logger.exception("Face scan: pipeline crashed for %s", image_id)


async def _run_summarize_one(image_id: UUID) -> None:
    """Background task: generate the AI Vision content summary after upload.

    Same pattern as `_run_face_scan_one` — own session, never blocks the
    HTTP response. BLIP + sumy take 2-15 s depending on category and
    hardware; running inline would stall every upload that long.
    """
    from backend.summarize import summarize_image_id

    async with SessionLocal() as session:
        try:
            await summarize_image_id(session, image_id)
        except Exception:
            logger.exception("Summarize: pipeline crashed for %s", image_id)


async def _run_face_scan_then_summarize(user_id: UUID, image_id: UUID) -> None:
    """Pass B then AI Vision summary, sequentially.

    Running them in parallel races: BLIP captions "a man" before face
    recognition has named the person, so the summary misses the name.
    Sequential ordering means the summary can splice "Me" into the caption
    on the very first pass. Combined runtime is still well under 60 s on
    CPU; we're not on the request's critical path either way.
    """
    await _run_face_scan_one(user_id, image_id)
    await _run_summarize_one(image_id)


async def _load_owned_image(
    image_id: UUID,
    user: User,
    session: AsyncSession,
) -> Image:
    stmt = select(Image).where(
        Image.id == image_id,
        Image.user_id == user.id,
        Image.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    img = result.scalar_one_or_none()
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    return img


@router.post("/", response_model=ImageRead, status_code=status.HTTP_201_CREATED)
async def upload_image(
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    background: BackgroundTasks,
    file: Annotated[UploadFile, File(...)],
) -> Image:
    raw = await file.read(settings.upload_max_bytes + 1)
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")
    if len(raw) > settings.upload_max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Upload exceeds the per-file size limit.",
        )
    await enforce_upload_limits(str(user.id), request, len(raw))
    try:
        image = await store_upload(session, user, file.filename, raw, file.content_type)
    except UploadValidationError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Could not decode image: {exc}",
        ) from exc
    # Defer post-processing so the upload response returns immediately.
    # When both Pass B (faces) and the AI Vision summary apply, run them
    # sequentially: the summary spliced person names from the faces pass.
    needs_faces = image.category == "image" and image.pending_face_scan
    needs_summary = image.pending_summary
    if needs_faces and needs_summary:
        _detach(_run_face_scan_then_summarize(user.id, image.id))
    elif needs_faces:
        _detach(_run_face_scan_one(user.id, image.id))
    elif needs_summary:
        _detach(_run_summarize_one(image.id))
    return image


@router.get("/{image_id}/download-url")
async def signed_download_url(
    image_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    variant: str = Query(default="served", pattern="^(original|served)$"),
) -> dict[str, str]:
    image = await _load_owned_image(image_id, user, session)
    return make_signed_download(
        base_url=str(request.base_url),
        image_id=image.id,
        user_id=user.id,
        variant=variant,
    )


@router.get("/{image_id}/signed/{variant}")
async def signed_download(
    image_id: UUID,
    variant: str,
    uid: UUID,
    expires: int,
    sig: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if not verify_download(
        image_id=image_id,
        user_id=uid,
        variant=variant,
        expires=expires,
        sig=sig,
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or expired download URL")
    image = (
        await session.execute(
            select(Image).where(
                Image.id == image_id,
                Image.user_id == uid,
                Image.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    if variant == "original":
        if image.original_blob_key is None:
            blob, mime = await fetch_served(image)
            return Response(
                content=blob,
                media_type=mime,
                headers={"X-Original-Expired": "true"},
            )
        blob, mime = await fetch_original(image)
        return Response(content=blob, media_type=mime)
    blob, mime = await fetch_served(image)
    return Response(content=blob, media_type=mime)


@router.get("/", response_model=list[ImageRead])
async def list_images(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 100,
    offset: int = 0,
    scene: Annotated[str | None, Query(max_length=64)] = None,
    content_type: Annotated[str | None, Query(max_length=32)] = None,
    tag: Annotated[str | None, Query(max_length=64)] = None,
    indoor_outdoor: Annotated[str | None, Query(max_length=8)] = None,
    category: Annotated[str | None, Query(max_length=16)] = None,
    person: Annotated[str | None, Query(max_length=120)] = None,
    person_id: Annotated[int | None, Query(ge=1)] = None,
    # Phase 12 — folder scoping. `folder_id=null` (the default) returns
    # images that don't have a parent folder assigned (= "root view").
    # Pass `folder_id=<uuid>` to fetch the contents of a specific folder.
    # Pass `all=true` to ignore the folder filter entirely (used by
    # search and the bulk people-tray view).
    folder_id: Annotated[UUID | None, Query()] = None,
    all: Annotated[bool, Query()] = False,
) -> list[Image]:
    stmt = select(Image).where(
        Image.user_id == user.id,
        Image.deleted_at.is_(None),
    )
    if not all:
        if folder_id is None:
            stmt = stmt.where(Image.folder_id.is_(None))
        else:
            stmt = stmt.where(Image.folder_id == folder_id)
    if category is not None:
        stmt = stmt.where(Image.category == category)
    if scene is not None:
        stmt = stmt.where(Image.scene_label == scene)
    if content_type is not None:
        stmt = stmt.where(Image.content_type == content_type)
    if indoor_outdoor is not None:
        stmt = stmt.where(Image.indoor_outdoor == indoor_outdoor)
    if tag is not None:
        stmt = stmt.join(ImageTag, ImageTag.image_id == Image.id).join(
            Tag, Tag.id == ImageTag.tag_id
        ).where(Tag.label == tag)
    if person_id is not None or person is not None:
        # Join images -> face_detections -> faces -> persons.
        stmt = (
            stmt.join(FaceDetection, FaceDetection.image_id == Image.id)
            .join(Face, Face.id == FaceDetection.face_id)
            .where(FaceDetection.user_id == user.id)
        )
        if person_id is not None:
            stmt = stmt.where(Face.person_id == person_id)
        if person is not None:
            stmt = stmt.join(Person, Person.id == Face.person_id).where(
                Person.user_id == user.id, Person.display_name == person
            )
        stmt = stmt.distinct()
    stmt = stmt.order_by(Image.uploaded_at.desc()).limit(limit).offset(offset)

    result = await session.execute(stmt)
    return list(result.scalars().all())


# IMPORTANT: register `/geo` *before* `/{image_id}` so FastAPI matches
# the literal path first. With `/{image_id}` registered first, GET
# /images/geo gets routed to `get_image(image_id="geo")` which fails
# UUID parsing with 422. Same pattern applies to /backfill-summaries
# below — keep all literal-path GETs above the parameterized ones.
@router.get("/geo")
async def list_image_geo(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """C3 — image coordinates for the map view.

    Returns `{points: [{id, lat, lng, taken_at}], consent: bool}`. The
    `consent` flag lets the FE distinguish between "no GPS data" and
    "GPS retention disabled by the user" so it can prompt the right
    follow-up. We never leak coordinates without an active consent
    record, even if rows pre-date a withdraw (withdraw cascades the
    deletion, but defense in depth).
    """
    consent = await is_scope_active(session, user.id, "gps_retention")
    if not consent:
        return {"consent": False, "points": []}

    rows = (
        await session.execute(
            select(
                ImageGeo.image_id,
                ImageGeo.lat,
                ImageGeo.lng,
                ImageGeo.taken_at,
                Image.original_filename,
            )
            .join(Image, Image.id == ImageGeo.image_id)
            .where(
                ImageGeo.user_id == user.id,
                Image.deleted_at.is_(None),
            )
            .order_by(nulls_last(ImageGeo.taken_at.desc()))
            .limit(5000)
        )
    ).all()

    return {
        "consent": True,
        "points": [
            {
                "id": str(image_id),
                "lat": float(lat),
                "lng": float(lng),
                "taken_at": taken_at.isoformat() if taken_at else None,
                "original_filename": fname,
            }
            for image_id, lat, lng, taken_at, fname in rows
        ],
    }


@router.get("/{image_id}", response_model=ImageRead)
async def get_image(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Image:
    return await _load_owned_image(image_id, user, session)


@router.get("/{image_id}/people")
async def image_people(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Faces detected in this image, joined with their (named) persons.

    Used by the preview panel to show "who is in this photo" above the
    metadata box. Returns one entry per face_detection with stable IDs the
    frontend can use to render face-crop avatars and trigger naming.
    """
    img = await _load_owned_image(image_id, user, session)
    rows = (
        await session.execute(
            select(FaceDetection, Face, Person)
            .join(Face, Face.id == FaceDetection.face_id)
            .outerjoin(Person, Person.id == Face.person_id)
            .where(
                FaceDetection.image_id == img.id,
                FaceDetection.user_id == user.id,
            )
            .order_by(FaceDetection.bbox_x.asc())  # left-to-right ordering
        )
    ).all()

    return [
        {
            "face_id": face.id,
            "detection_id": det.id,
            "person_id": person.id if person else None,
            "person_display_name": person.display_name if person else None,
            "cluster_id": face.cluster_id,
            "bbox": [det.bbox_x, det.bbox_y, det.bbox_w, det.bbox_h],
            "detection_confidence": det.detection_confidence,
        }
        for det, face, person in rows
    ]


@router.get("/{image_id}/original")
async def download_original(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if settings.require_signed_downloads and settings.is_production:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Use a signed download URL")
    image = await _load_owned_image(image_id, user, session)
    if image.original_blob_key is None:
        # Hybrid-retention mode D: original was dropped after expiry; serve the
        # compressed variant in its place. EXIF/GPS/capture date are preserved
        # in the served file's metadata chunks.
        blob, mime = await fetch_served(image)
        return Response(
            content=blob,
            media_type=mime,
            headers={"X-Original-Expired": "true"},
        )
    blob, mime = await fetch_original(image)
    return Response(content=blob, media_type=mime)


@router.get("/{image_id}/served")
async def download_served(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if settings.require_signed_downloads and settings.is_production:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Use a signed download URL")
    image = await _load_owned_image(image_id, user, session)
    blob, mime = await fetch_served(image)
    return Response(content=blob, media_type=mime)


@router.post("/backfill-summaries", status_code=status.HTTP_202_ACCEPTED)
async def backfill_summaries(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    background: BackgroundTasks,
    limit: int = 500,
    force: bool = False,
) -> dict:
    """Queue summarize jobs for owned images.

    Default mode picks up images still missing a summary (`pending_summary=true`).
    `force=true` regenerates every image — useful after switching captioning
    models or fixing a model that previously crashed and left fallback
    summaries on every row. Capped at `limit` so a 10k-image library
    doesn't overload the worker in one request.
    """
    from sqlalchemy import or_

    stmt = (
        select(Image.id)
        .where(
            Image.user_id == user.id,
            Image.deleted_at.is_(None),
        )
        .order_by(Image.uploaded_at.desc())
        .limit(limit)
    )
    if not force:
        stmt = stmt.where(
            or_(Image.pending_summary.is_(True), Image.summary.is_(None))
        )
    ids = [row for row in (await session.execute(stmt)).scalars().all()]

    if force and ids:
        # Mark them pending so the AI Vision panel shows the loading skeleton
        # and search consumers know the text is about to be replaced.
        from sqlalchemy import update as sa_update

        await session.execute(
            sa_update(Image)
            .where(Image.id.in_(ids))
            .values(pending_summary=True)
        )
        await session.commit()

    for image_id in ids:
        _detach(_run_summarize_one(image_id))
    return {"queued": len(ids), "limit": limit, "force": force}


@router.post("/{image_id}/resummarize", status_code=status.HTTP_202_ACCEPTED)
async def resummarize_image(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Clear the AI Vision columns and reschedule summarization.

    Useful after upgrading models or when a summary came back empty/wrong.
    Owner-scoped via `_load_owned_image`.
    """
    image = await _load_owned_image(image_id, user, session)
    image.summary = None
    image.summary_topic = None
    image.summary_points = None
    image.summary_generated_at = None
    image.pending_summary = True
    await session.commit()

    _detach(_run_summarize_one(image.id))
    return {"image_id": str(image.id), "pending_summary": True}


@router.patch("/{image_id}/move", response_model=ImageRead)
async def move_image(
    image_id: UUID,
    body: ImageMove,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Image:
    """Move an image into a folder, or back to the root with `folder_id=null`.

    Both endpoints (this and `/folders/{id}/move-images`) are kept narrow
    so the FE can drag-drop a single card to a folder without needing a
    bulk wrapper. Bulk move is a thin loop over this in the FE.
    """
    image = await _load_owned_image(image_id, user, session)

    if body.folder_id is not None:
        from backend.models import Folder

        folder = await session.get(Folder, body.folder_id)
        if (
            folder is None
            or folder.user_id != user.id
            or folder.deleted_at is not None
        ):
            raise HTTPException(status_code=404, detail="Folder not found")

    image.folder_id = body.folder_id
    await session.commit()
    await session.refresh(image)
    return image


@router.patch("/{image_id}/status", response_model=ImageRead)
async def set_image_status(
    image_id: UUID,
    body: StatusSet,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Image:
    """Set or clear the project status label on an image. Pass both
    fields as null to clear; otherwise label + an optional color key."""
    image = await _load_owned_image(image_id, user, session)
    image.status = body.status or None
    image.status_color = body.status_color or None
    await session.commit()
    await session.refresh(image)
    return image


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    image = await _load_owned_image(image_id, user, session)
    await hard_delete_images(
        session,
        user_id=user.id,
        image_ids=[image.id],
        audit_action="image.delete",
    )
    await session.commit()


@router.post("/bulk-delete")
async def bulk_delete(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ids: list[UUID],
) -> dict:
    stmt = select(Image).where(
        Image.id.in_(ids),
        Image.user_id == user.id,
        Image.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    images = list(result.scalars().all())
    res = await hard_delete_images(
        session,
        user_id=user.id,
        image_ids=[img.id for img in images],
        audit_action="image.bulk_delete",
    )
    await session.commit()
    return {"deleted": [str(i) for i in res.image_ids], "count": len(res.image_ids)}


@router.post("/bulk-restore")
async def bulk_restore(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ids: list[UUID],
) -> dict:
    """Undo bulk-delete within the soft-delete window (used by frontend Undo)."""
    stmt = select(Image).where(
        Image.id.in_(ids),
        Image.user_id == user.id,
        Image.deleted_at.is_not(None),
    )
    result = await session.execute(stmt)
    images = list(result.scalars().all())
    for img in images:
        img.deleted_at = None
    await session.commit()
    return {"restored": [str(i.id) for i in images], "count": len(images)}
