import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.consent import is_consent_active, is_scope_active
from backend.db import SessionLocal, get_session
from backend.config import settings
from backend import jobs
from backend.deletion import hard_delete_images
from backend.image import fetch_original, fetch_served, store_upload
from backend.name_suggest import NameSuggestion, suggest_names_for_image
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
from backend.schemas import ImageMove, ImageRead, ImageRename, StatusSet
from backend.security import enforce_upload_limits
from backend.signed_urls import make_signed_download, verify_download
from backend.storage import storage
from backend.upload_validation import UploadValidationError, validate_image_filename

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


async def _enqueue_or_inline_fallback(enqueue_fn, *args, inline) -> None:
    """Try enqueueing the job onto the Redis queue; if that fails (Redis
    down, ml-worker not configured), run the work inline via
    asyncio.create_task instead. The inline fallback preserves the
    pre-worker dev experience but pays the cost of holding the GIL in
    the API container — production deployments expect the worker
    container to be running and Redis to be reachable.
    """
    try:
        ok = await enqueue_fn(*args)
    except Exception:
        logger.exception("enqueue raised; falling back to inline")
        ok = False
    if not ok:
        _detach(inline())


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
    include_deleted: bool = False,
) -> Image:
    """Owner-scoped image lookup.

    Live views filter out soft-deleted rows by default; the trash-side
    delete path passes `include_deleted=True` so an already-trashed row
    can still be hard-purged without 404'ing.
    """
    stmt = select(Image).where(
        Image.id == image_id,
        Image.user_id == user.id,
    )
    if not include_deleted:
        stmt = stmt.where(Image.deleted_at.is_(None))
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
    # Defer post-processing to the ml-worker container so the upload
    # response returns immediately AND the API event loop stays free
    # to serve login + other requests during inference. When both
    # Pass B (faces) and the AI Vision summary apply, the worker
    # runs them sequentially so the summary can splice person names
    # from the faces pass.
    needs_faces = image.category == "image" and image.pending_face_scan
    needs_summary = image.pending_summary
    if needs_faces and needs_summary:
        await _enqueue_or_inline_fallback(
            jobs.enqueue_face_scan_then_summarize, user.id, image.id,
            inline=lambda: _run_face_scan_then_summarize(user.id, image.id),
        )
    elif needs_faces:
        await _enqueue_or_inline_fallback(
            jobs.enqueue_face_scan, user.id, image.id,
            inline=lambda: _run_face_scan_one(user.id, image.id),
        )
    elif needs_summary:
        await _enqueue_or_inline_fallback(
            jobs.enqueue_summarize, user.id, image.id,
            inline=lambda: _run_summarize_one(image.id),
        )
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
    # Starred view: when true, returns is_starred rows cross-folder,
    # sorted by starred_at desc. Ignores folder_id / all.
    starred: Annotated[bool, Query()] = False,
    # Trash view: when true, returns soft-deleted rows (deleted_at is
    # not null) cross-folder, sorted by deleted_at desc. Used by the
    # Trash page in the gallery so the user can see what's actually in
    # the bin instead of an opaque "X items in trash" counter.
    trashed: Annotated[bool, Query()] = False,
    # Richer-filter axes — the frontend filter chips drive these.
    # `has_faces=true` keeps only images where at least one face was
    # detected (face_likelihood is the cheap CLIP gate, but join to
    # face_detections is the source of truth for "actually has a face
    # row in the DB"). `has_gps=true` keeps images with an image_geo
    # row. `min_face_likelihood` is a 0-1 cosine threshold on the
    # CLIP face-likelihood column, used to surface "definitely-people"
    # photos even when face detection hasn't run yet.
    has_faces: Annotated[bool | None, Query()] = None,
    has_gps: Annotated[bool | None, Query()] = None,
    min_face_likelihood: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    # C9 multi-axis filtering:
    # `near="lat,lng,radius_km"` — restrict to images whose image_geo
    #   coords fall inside a Haversine-approx bounding box of the
    #   given radius. Cheap: no PostGIS needed, just lat/lng bands.
    #   Requires `gps_retention` consent (same gate as has_gps below).
    # `taken_between="ISO,ISO"` — date range filter. Match either
    #   `image_geo.taken_at` (EXIF capture date when GPS consent is on
    #   and EXIF carried it) OR `images.uploaded_at` as a fallback.
    #   Both ends inclusive; either side can be empty ("," = open).
    near: Annotated[str | None, Query(max_length=64)] = None,
    taken_between: Annotated[str | None, Query(max_length=80)] = None,
) -> list[Image]:
    stmt = select(Image).where(Image.user_id == user.id)
    if trashed:
        stmt = stmt.where(Image.deleted_at.is_not(None))
    else:
        stmt = stmt.where(Image.deleted_at.is_(None))
    if starred:
        stmt = stmt.where(Image.is_starred.is_(True))
    elif not trashed and not all:
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
        # Two ways an image can belong to a person:
        #   (a) Face detection: image → face_detections → faces → persons
        #   (b) Manual tag:     image → image_persons → persons
        #
        # The user's "select 29 photos, name them Me" flow writes (b)
        # for every selected image, even ones where face detection
        # failed. Without considering (b) here, the per-person photo
        # count read low (only the photos with a detected face showed
        # up under the person). The UNION below covers both.
        from backend.models import ImagePerson

        face_subq = (
            select(FaceDetection.image_id)
            .join(Face, Face.id == FaceDetection.face_id)
            .where(FaceDetection.user_id == user.id)
        )
        manual_subq = select(ImagePerson.image_id).where(
            ImagePerson.user_id == user.id
        )
        if person_id is not None:
            face_subq = face_subq.where(Face.person_id == person_id)
            manual_subq = manual_subq.where(ImagePerson.person_id == person_id)
        if person is not None:
            face_subq = face_subq.join(
                Person, Person.id == Face.person_id
            ).where(
                Person.user_id == user.id, Person.display_name == person
            )
            manual_subq = manual_subq.join(
                Person, Person.id == ImagePerson.person_id
            ).where(
                Person.user_id == user.id, Person.display_name == person
            )
        person_image_ids = face_subq.union(manual_subq)
        stmt = stmt.where(Image.id.in_(person_image_ids))
    if has_faces is True:
        # EXISTS subquery — cheaper than a join + DISTINCT and still
        # composes with all the other filters above.
        stmt = stmt.where(
            select(FaceDetection.id)
            .where(
                FaceDetection.image_id == Image.id,
                FaceDetection.user_id == user.id,
            )
            .exists()
        )
    elif has_faces is False:
        stmt = stmt.where(
            ~select(FaceDetection.id)
            .where(
                FaceDetection.image_id == Image.id,
                FaceDetection.user_id == user.id,
            )
            .exists()
        )
    if has_gps is True:
        stmt = stmt.where(
            select(ImageGeo.image_id).where(ImageGeo.image_id == Image.id).exists()
        )
    elif has_gps is False:
        stmt = stmt.where(
            ~select(ImageGeo.image_id).where(ImageGeo.image_id == Image.id).exists()
        )
    if min_face_likelihood is not None:
        stmt = stmt.where(Image.face_likelihood >= min_face_likelihood)
    # ----- C9 multi-axis filters: near + taken_between -----
    # `near` (lat,lng,radius_km) gates on gps_retention consent — the
    # filter implies the user wants to use their stored coordinates.
    # Without consent, image_geo rows aren't populated anyway, so the
    # filter would return empty; we 403 to make the failure mode
    # explicit instead of silently returning [].
    if near is not None:
        if not await is_scope_active(session, user.id, "gps_retention"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="gps_retention consent required to filter by location",
            )
        try:
            lat_s, lng_s, radius_s = near.split(",")
            lat = float(lat_s)
            lng = float(lng_s)
            radius_km = float(radius_s)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="near must be 'lat,lng,radius_km' (three comma-separated floats)",
            )
        if radius_km <= 0 or radius_km > 20_000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="near radius_km must be between 0 and 20000",
            )
        # Haversine bounding box. Cheap, no PostGIS dependency. 1° lat ≈
        # 111 km globally; 1° lng ≈ 111 km × cos(lat) (shrinks toward the
        # poles). The cosine clamp at 0.01 keeps the divisor safe at
        # extreme latitudes — at 89.4° the bound widens to ~10× radius
        # which is fine since we'd be returning everything near the pole
        # anyway. We do NOT compute true great-circle distance here —
        # the bounding box is the cheap pre-filter; if a future caller
        # wants exact distance, layer a Haversine SQL UDF on top.
        dlat = radius_km / 111.0
        dlng = radius_km / (111.0 * max(0.01, math.cos(math.radians(lat))))
        stmt = stmt.where(
            select(ImageGeo.image_id)
            .where(
                ImageGeo.image_id == Image.id,
                ImageGeo.lat.between(lat - dlat, lat + dlat),
                ImageGeo.lng.between(lng - dlng, lng + dlng),
            )
            .exists()
        )
    # `taken_between` — date range filter. Prefer the EXIF capture date
    # from image_geo.taken_at when it exists (and gps_retention is on so
    # the row was actually populated); fall back to uploaded_at when no
    # capture date is recorded. We splice the choice into a correlated
    # COALESCE so a single comparison handles both cases without a JOIN.
    # Either side of the comma can be empty: "2026-01-01," means
    # "from Jan 1 onward".
    if taken_between is not None:
        parts = taken_between.split(",")
        if len(parts) != 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="taken_between must be 'ISO,ISO' (either side can be empty)",
            )
        start_s, end_s = parts[0].strip(), parts[1].strip()
        try:
            start_dt = datetime.fromisoformat(start_s) if start_s else None
            end_dt = datetime.fromisoformat(end_s) if end_s else None
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="taken_between dates must be ISO format (e.g. 2026-05-18 or 2026-05-18T12:00:00)",
            )
        # COALESCE chain: prefer images.captured_at (EXIF DateTimeOriginal,
        # populated whenever exif_retention is on), then image_geo.taken_at
        # (legacy data from before captured_at existed), then uploaded_at
        # as a last resort so unparseable-EXIF / pre-consent images still
        # respect the filter.
        captured_at = func.coalesce(
            Image.captured_at,
            select(ImageGeo.taken_at).where(ImageGeo.image_id == Image.id).scalar_subquery(),
            Image.uploaded_at,
        )
        if start_dt is not None:
            stmt = stmt.where(captured_at >= start_dt)
        if end_dt is not None:
            stmt = stmt.where(captured_at <= end_dt)
    # Starred view sorts by when the user starred each row (newest stars
    # first); trashed view by when the file hit the bin (newest first);
    # everything else sorts by upload recency.
    if starred:
        stmt = stmt.order_by(nulls_last(Image.starred_at.desc()))
    elif trashed:
        stmt = stmt.order_by(Image.deleted_at.desc())
    else:
        stmt = stmt.order_by(Image.uploaded_at.desc())
    stmt = stmt.limit(limit).offset(offset)

    result = await session.execute(stmt)
    images = list(result.scalars().all())
    if not images:
        return []

    # §C1.6 — attach the per-image tag list. One JOIN keyed on
    # image_id; we hydrate per-image lists in Python.
    tag_rows = (
        await session.execute(
            select(ImageTag.image_id, Tag.id, Tag.label, Tag.color, ImageTag.confidence)
            .join(Tag, Tag.id == ImageTag.tag_id)
            .where(
                ImageTag.user_id == user.id,
                ImageTag.image_id.in_([img.id for img in images]),
            )
        )
    ).all()
    by_image: dict = {}
    for image_id, tid, label, color, confidence in tag_rows:
        by_image.setdefault(image_id, []).append({
            "id": tid, "label": label, "color": color, "confidence": confidence,
        })

    return [
        ImageRead.model_validate(img, from_attributes=True).model_copy(
            update={"tags": by_image.get(img.id, [])},
        )
        for img in images
    ]


@router.get("/facets")
async def list_facets(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Available filter axes + counts for the gallery filter chips.

    Returns the set of `scene_label`, `content_type`, and
    `indoor_outdoor` values present in the user's library, each with
    a count, so the frontend can render only the chips that would
    actually return results. Also includes `with_gps`, `with_faces`,
    and an overall total so the chips can show context like
    "Indoor (12)" without a per-chip query.
    """
    base = (
        Image.user_id == user.id,
        Image.deleted_at.is_(None),
    )

    async def _agg(col):
        rows = (
            await session.execute(
                select(col, func.count(Image.id))
                .where(*base, col.is_not(None))
                .group_by(col)
                .order_by(func.count(Image.id).desc())
            )
        ).all()
        return [{"value": v, "count": int(c)} for v, c in rows]

    scenes = await _agg(Image.scene_label)
    content_types = await _agg(Image.content_type)
    indoor_outdoor = await _agg(Image.indoor_outdoor)

    total = (
        await session.execute(select(func.count(Image.id)).where(*base))
    ).scalar_one()
    with_gps = (
        await session.execute(
            select(func.count(Image.id))
            .select_from(Image)
            .join(ImageGeo, ImageGeo.image_id == Image.id)
            .where(*base)
        )
    ).scalar_one()
    with_faces = (
        await session.execute(
            select(func.count(func.distinct(Image.id)))
            .select_from(Image)
            .join(FaceDetection, FaceDetection.image_id == Image.id)
            .where(*base, FaceDetection.user_id == user.id)
        )
    ).scalar_one()

    # Cross-folder per-category counts for the sidebar pills. Previously
    # the FE derived these from `baseFiles`, which is folder-scoped, so
    # Photos / Videos / Documents counts read low any time the user was
    # inside a folder. Pull them straight from the DB so the count is
    # what the user expects: "every image in my library".
    cat_rows = (
        await session.execute(
            select(Image.category, func.count(Image.id))
            .where(*base)
            .group_by(Image.category)
        )
    ).all()
    by_category = {row[0]: int(row[1] or 0) for row in cat_rows if row[0]}

    # Trash count — also cross-folder. The /account/trash endpoint
    # returns this too but the sidebar uses /images/facets for the
    # rest, so co-locate the count here to skip a round trip.
    trash_count = (
        await session.execute(
            select(func.count(Image.id))
            .where(
                Image.user_id == user.id,
                Image.deleted_at.is_not(None),
            )
        )
    ).scalar_one()

    # Starred count — drives the "Starred" filter chip badge.
    starred_count = (
        await session.execute(
            select(func.count(Image.id)).where(*base, Image.is_starred.is_(True))
        )
    ).scalar_one()

    # Date range — earliest + latest uploaded_at (with image_geo.taken_at
    # preferred when present). The FE uses this to size the date-range
    # picker (don't suggest a "From 1970" placeholder when the user's
    # library starts in 2024).
    date_min, date_max = (
        await session.execute(
            select(
                func.min(
                    func.coalesce(
                        Image.captured_at,
                        select(ImageGeo.taken_at)
                        .where(ImageGeo.image_id == Image.id)
                        .scalar_subquery(),
                        Image.uploaded_at,
                    )
                ),
                func.max(
                    func.coalesce(
                        Image.captured_at,
                        select(ImageGeo.taken_at)
                        .where(ImageGeo.image_id == Image.id)
                        .scalar_subquery(),
                        Image.uploaded_at,
                    )
                ),
            ).where(*base)
        )
    ).one()

    # Top tags — gated only by being on a non-trashed image owned by
    # this user. Returns id + label + color + count so the FE doesn't
    # need a second round-trip to /tags/ to color the chip.
    tag_rows = (
        await session.execute(
            select(Tag.id, Tag.label, Tag.color, func.count(ImageTag.image_id))
            .join(ImageTag, ImageTag.tag_id == Tag.id)
            .join(Image, Image.id == ImageTag.image_id)
            .where(*base, Tag.user_id == user.id)
            .group_by(Tag.id)
            .order_by(func.count(ImageTag.image_id).desc())
            .limit(50)
        )
    ).all()
    tags = [
        {"id": tid, "label": label, "color": color, "count": int(c)}
        for tid, label, color, c in tag_rows
    ]

    # Top persons — drives the People chip group. Gated on
    # face_recognition consent: without consent, return an empty list
    # so the FE renders no chips and no PII leaks via the count.
    # Counts via the same UNION shape list_images uses (face_detections
    # + image_persons) so the per-person count matches what the gallery
    # would show after clicking the chip.
    persons: list = []
    if await is_scope_active(session, user.id, "face_recognition"):
        from backend.models import ImagePerson  # local import — see list_images

        # Faces path: image → face_detections → faces.person_id
        face_image_ids = (
            select(FaceDetection.image_id, Face.person_id)
            .join(Face, Face.id == FaceDetection.face_id)
            .where(FaceDetection.user_id == user.id, Face.person_id.is_not(None))
        )
        # Manual path: image_persons rows
        manual_image_ids = (
            select(ImagePerson.image_id, ImagePerson.person_id)
            .where(ImagePerson.user_id == user.id)
        )
        union_subq = face_image_ids.union(manual_image_ids).subquery()
        person_rows = (
            await session.execute(
                select(
                    Person.id,
                    Person.display_name,
                    func.count(func.distinct(union_subq.c.image_id)),
                )
                .join(union_subq, union_subq.c.person_id == Person.id)
                .join(Image, Image.id == union_subq.c.image_id)
                .where(*base, Person.user_id == user.id)
                .group_by(Person.id)
                .order_by(func.count(func.distinct(union_subq.c.image_id)).desc())
                .limit(50)
            )
        ).all()
        persons = [
            {"id": pid, "display_name": name, "count": int(c)}
            for pid, name, c in person_rows
        ]

    return {
        "total": int(total or 0),
        "scenes": scenes,
        "content_types": content_types,
        "indoor_outdoor": indoor_outdoor,
        "with_gps": int(with_gps or 0),
        "with_faces": int(with_faces or 0),
        "by_category": by_category,
        "trash_count": int(trash_count or 0),
        "starred_count": int(starred_count or 0),
        "date_range": {
            "earliest": date_min.isoformat() if date_min else None,
            "latest": date_max.isoformat() if date_max else None,
        },
        "tags": tags,
        "persons": persons,
    }


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
                ImageGeo.place,
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
                "place": place,
                "original_filename": fname,
            }
            for image_id, lat, lng, taken_at, place, fname in rows
        ],
    }


# In-process set of image ids currently being summarized. Lets the
# progress-poll endpoint auto-drain stuck pending rows without queueing
# the same image twice. Drained by `_run_summarize_one` (added in
# `_detach_summarize_tracked` below) when each task completes.
_SUMMARIZE_IN_FLIGHT: set[UUID] = set()


async def _run_summarize_tracked(image_id: UUID) -> None:
    """Wrap `_run_summarize_one` so the in-flight set unsets on exit.
    Used by the progress endpoint's auto-drain so the same image can't
    queue twice in a row."""
    try:
        await _run_summarize_one(image_id)
    finally:
        _SUMMARIZE_IN_FLIGHT.discard(image_id)


@router.get("/summarize-progress")
async def summarize_progress(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Tier-2 progress poll for the Library Maintenance UI and the top
    sticky banner.

    Returns `{total, pending, completed, has_any_summary}` for the
    user's own, non-deleted images. Two aggregate counts, no joins —
    cheap enough to poll every 2 s while a backfill is running.

    Also acts as a low-effort drainer: any pending row that isn't
    already being worked on (tracked in `_SUMMARIZE_IN_FLIGHT`) gets a
    fresh task scheduled. This unblocks the "stuck at 7/9" failure
    mode where an upload's one-shot task crashed and left the row
    pending forever. The cap of 4 fresh tasks per poll keeps a runaway
    library from spawning hundreds of concurrent vision jobs.

    `has_any_summary` lets the FE distinguish a brand-new account
    (everything pending for normal-upload reasons; no banner) from an
    account that's actively re-summarizing (banner shown).
    """
    # A row is "pending" iff the worker hasn't even attempted it yet.
    # That means `summary_generated_at IS NULL` — the worker either
    # hasn't picked the job up, or hasn't finished.
    #
    # A row with `summary_generated_at IS NOT NULL AND summary IS NULL`
    # is dead-letter: the worker ran, the model returned no usable text
    # (corrupt image, OOM, content unsupported), and re-running would
    # just spin. We exclude those from the progress count so the user
    # doesn't see "58/59" forever after the queue actually drains.
    # The per-item "Force re-summarize" action is the manual retry path.
    #
    # Previously this also checked `pending_summary=True` as an OR, but
    # the older failure path stamped `summary_generated_at` while
    # leaving `pending_summary=True`, so stuck rows were double-counted
    # — the counter stuck at N-1/N forever. _mark_done now sets
    # pending_summary=False on failure too, but this predicate is the
    # belt-and-suspenders for any historical rows that pre-date that fix.
    # SELF-HEAL: rows can get stuck in `pending_summary=True AND
    # summary_generated_at IS NULL` forever when the worker crashes
    # before _mark_done (OOM kill, container restart, Redis hiccup).
    # If a row's been sitting in that state for > 10 minutes, we
    # force-dead-letter it the same way an explicit failure would —
    # stamp summary_generated_at + clear pending_summary so the
    # banner counter clears. The user can still "Force re-summarize"
    # any specific row from the per-item menu. Run this BEFORE we
    # compute the pending count so the cleanup is reflected in the
    # same response.
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update as sa_update
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    await session.execute(
        sa_update(Image)
        .where(
            Image.user_id == user.id,
            Image.deleted_at.is_(None),
            Image.summary.is_(None),
            Image.summary_generated_at.is_(None),
            Image.uploaded_at < cutoff,
        )
        .values(
            summary_generated_at=datetime.now(timezone.utc),
            pending_summary=False,
        )
    )
    await session.commit()

    pending_predicate = (
        Image.summary.is_(None) & Image.summary_generated_at.is_(None)
    )
    row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(pending_predicate).label("pending"),
                func.count().filter(Image.summary.is_not(None)).label("with_summary"),
            ).where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
            )
        )
    ).one()
    total = int(row.total or 0)
    pending = int(row.pending or 0)
    with_summary = int(row.with_summary or 0)

    # Drain a few pending rows per poll. We rely on the Redis SADD
    # dedupe inside `enqueue_summarize` to drop duplicates, so the
    # earlier "100 duplicate jobs per minute" pathology doesn't happen
    # — a row already in-flight is silently skipped here. Without this
    # path, cloud-sync + AI-opt-in flips `pending_summary=True` but
    # nothing ever queues the work; users see the counter stuck at
    # "0 of 58 summarized" forever.
    if pending > 0:
        from backend import jobs as job_q

        candidate_ids = (
            await session.execute(
                select(Image.id)
                .where(
                    Image.user_id == user.id,
                    Image.deleted_at.is_(None),
                    Image.skip_ai_training.is_(False),
                    # Match `pending_predicate` above. Rows with
                    # summary_generated_at set + summary NULL are
                    # dead-letter; manual re-summarize via the per-item
                    # "Force re-summarize" action or
                    # /images/backfill-summaries?force=true if needed.
                    Image.summary.is_(None),
                    Image.summary_generated_at.is_(None),
                )
                .order_by(Image.uploaded_at.desc())
                .limit(8)
            )
        ).scalars().all()
        for img_id in candidate_ids:
            try:
                # user_id is required by the fair scheduler so this row
                # lands in this user's per-user queue. We're inside
                # `summarize_progress` which is already user-scoped.
                await job_q.enqueue_summarize(user.id, img_id)
            except Exception:
                logger.exception("summarize-progress: enqueue failed for %s", img_id)

    return {
        "total": total,
        "pending": pending,
        "completed": max(0, total - pending),
        "has_any_summary": with_summary > 0,
    }


@router.post("/geo/backfill")
async def backfill_image_geo(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Re-extract EXIF GPS from this user's existing originals and
    populate `image_geo`. Uploads that happened *before* the user
    granted `gps_retention` consent leave the originals in MinIO but
    skip the geo row — the comment in `image.py:store_upload`
    promises a backfill path; this is it.

    No-op when the consent scope is not active. Returns counts so the
    UI can toast "wired N points."
    """
    from backend.image import _exif_gps  # local import — same module

    if not await is_scope_active(session, user.id, "gps_retention"):
        raise HTTPException(
            status_code=403,
            detail="GPS retention consent is not active. Grant it in Settings → Privacy first.",
        )

    # Walk this user's images that don't already have a geo row. Only
    # touch images with EXIF-bearing originals (JPEG/HEIC/TIFF); skip
    # WebP/PNG/etc. since they don't carry EXIF GPS in practice.
    images = (
        await session.execute(
            select(Image)
            .outerjoin(ImageGeo, ImageGeo.image_id == Image.id)
            .where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
                ImageGeo.image_id.is_(None),
                Image.mime_type_original.in_(["image/jpeg", "image/heic", "image/heif", "image/tiff"]),
            )
            .limit(2000)
        )
    ).scalars().all()

    inserted = 0
    examined = 0
    for image in images:
        examined += 1
        try:
            raw, _mime = await fetch_original(image)
        except Exception:
            continue
        if not raw:
            continue
        gps = _exif_gps(raw)
        if gps is None:
            continue
        session.add(
            ImageGeo(
                image_id=image.id,
                user_id=user.id,
                lat=gps["lat"],
                lng=gps["lng"],
                taken_at=gps["taken_at"],
                captured_with=gps["captured_with"],
            )
        )
        inserted += 1

    if inserted:
        await session.commit()

    return {"examined": examined, "inserted": inserted}


# In-process Nominatim cache. Keys are `(round(lat, 3), round(lng, 3))`
# which gives ~110 m precision and dramatically cuts duplicate requests
# (a burst of photos from the same trip all hash to the same cell).
# Bounded by `_NOMINATIM_CACHE_CAP` so a runaway library can't OOM the
# process; oldest entries are evicted FIFO once the cap is hit.
_NOMINATIM_CACHE: dict[tuple[float, float], str | None] = {}
_NOMINATIM_CACHE_CAP = 50_000
_NOMINATIM_RATE_LIMIT_S = 1.1  # Nominatim ToS: 1 rps. Pad for jitter.


async def _reverse_geocode(lat: float, lng: float) -> str | None:
    """Call Nominatim. Returns a short display string ("Big Sur, California")
    or None on any failure. Honors the 1-rps rate limit and caches by
    rounded coords.

    Best-effort throughout — Nominatim is a free public service, no SLA;
    a failure returns None and the caller leaves `place` null so the
    next backfill run tries again.
    """
    key = (round(lat, 3), round(lng, 3))
    if key in _NOMINATIM_CACHE:
        return _NOMINATIM_CACHE[key]
    try:
        import httpx  # type: ignore
    except ImportError:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=8.0,
            headers={
                # Nominatim ToS requires a unique UA. Plain product
                # name is enough — the email-contact form was reading
                # as a "scrapy" UA to some Nominatim mirrors and
                # earning silent 200s with empty address objects.
                "User-Agent": "neuthek-self-host/0.1",
                "Accept-Language": "en",
            },
        ) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": lat,
                    "lon": lng,
                    "format": "jsonv2",
                    # zoom 14 = "suburb / large village" granularity.
                    # Previously zoom=10 ("city") returned an address
                    # object missing city/town/village for some valid
                    # urban coords (the road object got picked instead).
                    # 14 reliably populates city + state.
                    "zoom": 14,
                    "addressdetails": 1,
                },
            )
        if resp.status_code != 200:
            logger.warning(
                "reverse-geocode HTTP %s for (%s, %s): %s",
                resp.status_code, lat, lng, resp.text[:200],
            )
            return None
        data = resp.json()
        # Prefer a compact "City, Region" form over the full display_name.
        addr = data.get("address") or {}
        city = (
            addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("hamlet") or addr.get("suburb") or addr.get("neighbourhood")
            or addr.get("county") or addr.get("state_district")
            or addr.get("municipality")
        )
        region = addr.get("state") or addr.get("country")
        if city and region:
            short = f"{city}, {region}"
        elif data.get("display_name"):
            # Trim "Suburb, City, County, State, Country" → first two parts.
            parts = [p.strip() for p in data["display_name"].split(",")]
            short = ", ".join(parts[:2]) if len(parts) >= 2 else parts[0]
        else:
            short = None
        if not short:
            logger.warning(
                "reverse-geocode produced no place for (%s, %s): "
                "address=%s, display_name=%s",
                lat, lng,
                addr,
                data.get("display_name"),
            )
    except Exception:
        logger.exception("reverse-geocode failed for (%s, %s)", lat, lng)
        short = None

    # Only cache POSITIVE results. A None means Nominatim returned no
    # usable name OR the request failed — both should be retried later
    # (transient network, rate limit, slow upstream). Previously we
    # cached None too, which permanently poisoned coords that had a
    # blip on first lookup: every subsequent `_reverse_geocode` would
    # return the cached None and the image_geo.place column stayed
    # NULL forever, leaving the preview stuck on "Looking up
    # location…" even when the same coord on the map showed a valid
    # pin (because the lat/lng existed but place didn't).
    if short:
        if len(_NOMINATIM_CACHE) >= _NOMINATIM_CACHE_CAP:
            oldest = next(iter(_NOMINATIM_CACHE))
            _NOMINATIM_CACHE.pop(oldest, None)
        _NOMINATIM_CACHE[key] = short
    return short


@router.post("/geo/backfill-places")
async def backfill_image_places(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Reverse-geocode every `image_geo` row that has lat/lng but no
    `place` yet. Politely rate-limited (1 rps) per Nominatim's ToS.

    Idempotent: subsequent runs only touch rows still missing a name,
    so calling this repeatedly is safe (and useful as the cache fills).
    Cached aggressively by rounded coords — clusters of photos from the
    same trip share one call.
    """
    if not await is_scope_active(session, user.id, "gps_retention"):
        raise HTTPException(
            status_code=403,
            detail="GPS retention consent is not active.",
        )

    rows = (
        await session.execute(
            select(ImageGeo)
            .where(
                ImageGeo.user_id == user.id,
                ImageGeo.place.is_(None),
            )
            .limit(500)
        )
    ).scalars().all()

    examined = 0
    filled = 0
    # Cache-only fast path: drain anything we already know without
    # waiting on Nominatim's rate limit. Defensively re-queue cached
    # None entries (a leftover from before we stopped caching negative
    # results) so they get a fresh Nominatim attempt instead of
    # silently being skipped.
    pending: list[ImageGeo] = []
    for row in rows:
        examined += 1
        key = (round(row.lat, 3), round(row.lng, 3))
        cached = _NOMINATIM_CACHE.get(key)
        if cached:
            row.place = cached
            filled += 1
        else:
            # Either cache miss OR poisoned-None — either way, retry.
            pending.append(row)

    # Slow path: rate-limited Nominatim calls for cache misses.
    for row in pending:
        place = await _reverse_geocode(row.lat, row.lng)
        if place:
            row.place = place
            filled += 1
        await asyncio.sleep(_NOMINATIM_RATE_LIMIT_S)

    if filled:
        await session.commit()
    return {"examined": examined, "filled": filled}


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

    # Dedupe by person_id so a single named person doesn't render twice
    # in the preview when multiple detections (cascade re-run, slight
    # bbox jitter, etc.) all attached to the same Person row. Keep the
    # detection with the highest confidence as the representative — its
    # face crop is the one we want to show. Unnamed detections (no
    # person_id) keep their per-row entry so the user can still label
    # each one individually.
    out: list[dict] = []
    seen_person_ids: dict[int, dict] = {}
    for det, face, person in rows:
        entry = {
            "face_id": face.id,
            "detection_id": det.id,
            "person_id": person.id if person else None,
            "person_display_name": person.display_name if person else None,
            "cluster_id": face.cluster_id,
            "bbox": [det.bbox_x, det.bbox_y, det.bbox_w, det.bbox_h],
            "detection_confidence": det.detection_confidence,
        }
        if person is not None:
            existing = seen_person_ids.get(person.id)
            if existing is None:
                seen_person_ids[person.id] = entry
                out.append(entry)
            elif (det.detection_confidence or 0) > (existing.get("detection_confidence") or 0):
                # Replace the previous lower-confidence entry in place
                # so the order stays stable (first-occurrence position).
                idx = out.index(existing)
                out[idx] = entry
                seen_person_ids[person.id] = entry
        else:
            out.append(entry)
    return out


@router.get("/{image_id}/original")
async def download_original(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    # The `is_production` gate used to bypass the require_signed_downloads
    # setting in dev/staging — that meant operators couldn't actually
    # test their signed-URL flow before going to prod. The setting now
    # honors itself regardless of env; the dev default stays False so
    # nothing changes for fresh installs.
    if settings.require_signed_downloads:
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
    max_dim: Annotated[int | None, Query(ge=64, le=4096)] = None,
) -> Response:
    """Return the served (compressed) image bytes.

    When `max_dim` is set, the served image is resized so its longest
    side is at most `max_dim` pixels and re-encoded as WebP q=75. This
    is the gallery-card thumbnail path: serving a 4K-wide WebP into a
    200px card forced the browser to decode 1-3 MB per card just to
    render a tiny thumb, which is the worst hit on the perf budget.
    Resized variants are LRU-cached in process memory so repeated
    scrolls don't keep re-encoding the same image.

    Animated GIFs are exempt from the resize path — their served bytes
    are passthrough (we don't have a single-frame thumb of an animation
    without losing it), so the request returns the full GIF unchanged.
    """
    if settings.require_signed_downloads:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Use a signed download URL")
    image = await _load_owned_image(image_id, user, session)
    blob, mime = await fetch_served(image)

    if max_dim is not None and mime and mime.startswith("image/") and mime != "image/gif":
        try:
            blob, mime = await asyncio.to_thread(
                _resized_thumb_cached, image.id, blob, max_dim,
            )
        except Exception:
            logger.exception("served thumb resize failed for %s; returning original", image.id)

    headers = {
        # 1 day private cache — the image content for a given (id, size)
        # is content-addressed by the stored blob's hash; we never
        # update served bytes in place. Lets the browser skip the
        # network entirely on repeat scroll-backs.
        "Cache-Control": "private, max-age=86400",
    }
    return Response(content=blob, media_type=mime, headers=headers)


# Process-local LRU cache for resized served variants. Keyed on
# (image_id, max_dim) → (jpeg/webp bytes, mime). Capped at ~256 MB
# total bytes so a busy gallery doesn't exhaust container memory.
# Eviction is FIFO by insertion order which works fine because the
# gallery's most-recent items are the most-likely re-requested.
_THUMB_CACHE: "dict[tuple[UUID, int], tuple[bytes, str]]" = {}
_THUMB_CACHE_BYTES: list[int] = [0]  # mutable counter inside list to avoid `global`
_THUMB_CACHE_MAX_BYTES = 256 * 1024 * 1024


def _resized_thumb_cached(image_id: UUID, blob: bytes, max_dim: int) -> tuple[bytes, str]:
    key = (image_id, max_dim)
    hit = _THUMB_CACHE.get(key)
    if hit is not None:
        return hit
    from io import BytesIO
    from PIL import Image as PILImage

    with PILImage.open(BytesIO(blob)) as pil:
        pil.load()
        longest = max(pil.size)
        if longest <= max_dim:
            # Source is already small enough — no resize, just cache the
            # original bytes so repeat hits skip the decode pass.
            mime_out = pil.get_format_mimetype() or "image/webp"
            _store_in_thumb_cache(key, blob, mime_out)
            return blob, mime_out
        scale = max_dim / longest
        new_size = (
            max(1, int(round(pil.size[0] * scale))),
            max(1, int(round(pil.size[1] * scale))),
        )
        # `WebP q=75` strikes a good ratio for grid thumbnails — at
        # 400 px wide a typical photo lands around 12-25 KB, two
        # orders of magnitude smaller than the 4K served original.
        if pil.mode == "P":
            pil = pil.convert("RGB")
        out = BytesIO()
        pil.resize(new_size, PILImage.LANCZOS).save(out, format="WEBP", quality=75, method=4)
        out_bytes = out.getvalue()
    _store_in_thumb_cache(key, out_bytes, "image/webp")
    return out_bytes, "image/webp"


def _store_in_thumb_cache(key, blob: bytes, mime: str) -> None:
    _THUMB_CACHE[key] = (blob, mime)
    _THUMB_CACHE_BYTES[0] += len(blob)
    # Evict oldest entries until the byte budget fits. dict in 3.7+
    # preserves insertion order, so iterating yields oldest-first.
    while _THUMB_CACHE_BYTES[0] > _THUMB_CACHE_MAX_BYTES and _THUMB_CACHE:
        oldest_key = next(iter(_THUMB_CACHE))
        oldest_blob, _ = _THUMB_CACHE.pop(oldest_key)
        _THUMB_CACHE_BYTES[0] -= len(oldest_blob)


def _pdf_meta_sync(raw: bytes) -> dict:
    """Read page count + per-page dimensions from a PDF without rendering.

    Dimensions are reported in PDF user-space points (1/72 inch). The
    frontend uses the per-page aspect ratio to reserve scroll height
    before the corresponding raster JPEG lazy-loads, so the scrollbar
    never jumps as pages stream in.
    """
    import fitz  # type: ignore

    with fitz.open(stream=raw, filetype="pdf") as doc:
        pages = [
            {"w": float(p.rect.width), "h": float(p.rect.height)}
            for p in doc
        ]
    return {"page_count": len(pages), "pages": pages}


def _pdf_page_jpeg_sync(raw: bytes, page: int, target_width: int) -> bytes:
    """Rasterize page N to a JPEG at the requested CSS-pixel width.

    The matrix zoom is chosen so the output bitmap is exactly
    `target_width` pixels wide (snapped against the page's native
    user-space width). Quality 85 keeps a 2000-px wide page around
    150-300 KB — small enough that streaming a 30-page PDF over a
    LAN connection completes in under two seconds.
    """
    import fitz  # type: ignore

    target_width = max(64, min(target_width, 4096))
    with fitz.open(stream=raw, filetype="pdf") as doc:
        if page < 0 or page >= len(doc):
            raise ValueError("page out of range")
        pg = doc[page]
        zoom = target_width / max(1.0, pg.rect.width)
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("jpeg", jpg_quality=85)


def _is_pdf(image: Image) -> bool:
    mime = (image.mime_type_original or "").lower()
    if mime == "application/pdf":
        return True
    name = (image.original_filename or "").lower()
    return name.endswith(".pdf")


@router.get("/{image_id}/pdf-meta")
async def pdf_meta(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Return `{page_count, pages: [{w, h}, ...]}` for a PDF document.

    Used by the preview-modal page stack so the scroll height is correct
    before any page raster arrives. Non-PDF rows 415 because the rest of
    the modal would never call this for them.
    """
    image = await _load_owned_image(image_id, user, session)
    if not _is_pdf(image):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Not a PDF"
        )
    raw, _mime = await fetch_original(image)
    try:
        return await asyncio.to_thread(_pdf_meta_sync, raw)
    except Exception as exc:
        logger.exception("pdf-meta: parse failed for %s", image_id)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Could not read PDF: {type(exc).__name__}",
        )


@router.get("/{image_id}/pdf-page/{page}")
async def pdf_page(
    image_id: UUID,
    page: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    width: int = Query(default=1400, ge=64, le=4096),
) -> Response:
    """Rasterize one PDF page to JPEG at the requested width.

    Cached aggressively (`Cache-Control: private, max-age=86400`) keyed
    on (image_id, page, width). The blob URL the frontend wraps this
    in is per-page-per-tab, so even at the modal's max DPI request
    each page is fetched at most once per session.
    """
    image = await _load_owned_image(image_id, user, session)
    if not _is_pdf(image):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Not a PDF"
        )
    raw, _mime = await fetch_original(image)
    try:
        jpeg = await asyncio.to_thread(
            _pdf_page_jpeg_sync, raw, page, width
        )
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    except Exception as exc:
        logger.exception("pdf-page: render failed for %s p%s", image_id, page)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Render failed: {type(exc).__name__}",
        )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.post("/backfill-doc-thumbs")
async def backfill_doc_thumbs(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Generate first-page thumbnails for the caller's existing PDFs
    that don't have one yet. Walks `images.category == 'document'`
    rows whose `mime_type_served` is not an image (i.e. the served blob
    is still the raw PDF, so the gallery shows a generic icon).
    Rasterizes page 1 via PyMuPDF, uploads to the `served` bucket,
    updates the row. Returns `{examined, generated}`.
    """
    from uuid import uuid4

    from backend.storage import storage
    from backend.image import _pdf_page_one_thumb, fetch_original

    candidates = (
        await session.execute(
            select(Image).where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
                Image.category == "document",
                # Either no served mime, or it's not an image yet.
                (
                    Image.mime_type_served.is_(None)
                    | (~Image.mime_type_served.like("image/%"))
                ),
            ).limit(500)
        )
    ).scalars().all()

    examined = 0
    generated = 0
    for image in candidates:
        examined += 1
        # Only attempt PDFs.
        is_pdf = (
            (image.mime_type_original == "application/pdf")
            or (
                image.original_filename
                and image.original_filename.lower().endswith(".pdf")
            )
        )
        if not is_pdf:
            continue
        try:
            raw, _mime = await fetch_original(image)
        except Exception:
            continue
        if not raw:
            continue
        thumb = _pdf_page_one_thumb(raw)
        if not thumb:
            continue
        served_key = f"users/{user.id}/served/{uuid4().hex}.png"
        try:
            storage.put(
                storage.bucket_served,
                served_key,
                thumb,
                "image/png",
                sse_scope="content",
            )
        except Exception:
            continue
        image.served_blob_key = served_key
        image.mime_type_served = "image/png"
        image.byte_size_served = len(thumb)
        generated += 1

    if generated:
        await session.commit()
    return {"examined": examined, "generated": generated}


@router.post("/backfill-summaries", status_code=status.HTTP_202_ACCEPTED)
async def backfill_summaries(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    background: BackgroundTasks,
    limit: int = 500,
    force: bool = False,
) -> dict:
    """Queue summarize jobs for owned images.

    Rate-limited to 3 calls per hour per user — a full backfill of
    500 images takes several minutes of GPU time and a script that
    hammers this would starve every other user behind it on the
    shared ML worker. The fair-queue's per-user inflight cap already
    spreads execution; this gate also stops a user from filling
    Redis with thousands of pending jobs in one click.

    Default mode picks up images still missing a summary (`pending_summary=true`).
    `force=true` regenerates every image — useful after switching captioning
    models or fixing a model that previously crashed and left fallback
    summaries on every row. Capped at `limit` so a 10k-image library
    doesn't overload the worker in one request.
    """
    from backend.security import enforce_rate_limit
    await enforce_rate_limit(
        key=f"ml:backfill-summaries:{user.id}",
        limit=3,
        window_seconds=3600,
        detail="Too many summary backfills. Try again in an hour.",
    )

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
        await _enqueue_or_inline_fallback(
            jobs.enqueue_summarize, user.id, image_id,
            inline=lambda iid=image_id: _run_summarize_one(iid),
        )
    return {"queued": len(ids), "limit": limit, "force": force}


class BestOfBody(BaseModel):
    """Body for POST /images/best-of.

    `mode`:
      - overall:  single ranked list — top result is the keeper.
      - burst:    cluster by CLIP cosine sim, return best per cluster.
      - use_case: composite quality × CLIP cosine to a use-case prompt.

    `use_case` accepts either:
      - A preset key from USE_CASE_PROMPTS (e.g. "portrait", "sunset")
      - A free-text prompt up to 80 chars (e.g. "vintage car",
        "garden plants"). The backend wraps it in
        "a sharp well-composed photograph of <text>" before encoding.
    """
    image_ids: list[UUID] = Field(..., min_length=2, max_length=30)
    mode: str = Field(default="overall", pattern="^(overall|burst|use_case)$")
    use_case: str | None = Field(default=None, max_length=100)


@router.post("/best-of")
async def images_best_of(
    body: BestOfBody,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Score N user-selected images, return them ranked best-first.

    Per-criterion breakdown (sharpness / exposure / face / use_case)
    drives the bars in the BestOfModal UI. The user can override the
    AI's pick by clicking any other frame; the modal's "Keep this one"
    button trashes the rest via /images/bulk-delete.

    Rate-limited to 30 calls per hour per user. Each call runs OpenCV
    Laplacian + (optional) CLIP text-encode against up to 30 photos —
    a script hammering this would pin the ML thread.
    """
    from backend.best_of import best_of
    from backend.security import enforce_rate_limit

    await enforce_rate_limit(
        key=f"ml:best-of:{user.id}",
        limit=30,
        window_seconds=3600,
        detail="Too many best-of requests. Try again in a few minutes.",
    )

    results, resolved_prompt = await best_of(
        session=session,
        user_id=user.id,
        image_ids=body.image_ids,
        mode=body.mode,
        use_case=body.use_case,
    )
    return {
        "mode": body.mode,
        "use_case": body.use_case,
        # The actual CLIP prompt we ran (after preset lookup + free-text
        # wrapping). Echo so the FE can show "Scored by: …" and the
        # user can confirm their typed prompt was applied.
        "resolved_prompt": resolved_prompt,
        "results": [
            {
                "image_id": str(r.image_id),
                "score": r.score,
                "breakdown": r.breakdown,
                "cluster_id": r.cluster_id,
                "reasons": r.reasons,
            }
            for r in results
        ],
    }


@router.post("/backfill-vision", status_code=status.HTTP_202_ACCEPTED)
async def backfill_vision(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    background: BackgroundTasks,
    limit: int = 500,
) -> dict:
    """Re-run the vision classification pass on images that don't have a
    scene label / content type set.

    Mostly fills in the gap for cloud-synced images: the Drive sync path
    intentionally skips vision at upload (Limited Use policy
    interpretation) so those rows have NULL `scene_label`,
    `content_type`, `indoor_outdoor`. Without those columns set, the
    gallery's filter chips show no choices because the facets query
    returns empty lists for everything except `with_faces`.

    Hits the vision pipeline inline rather than dispatching to the
    worker — the work is lighter than full summarization and the user
    is usually waiting in the maintenance UI. Skips rows the
    pipeline already covered (`vision_processed_at IS NOT NULL`) so
    repeated calls are cheap.

    Rate-limited to 3 calls per hour per user. Each call runs CLIP +
    scene-classify on up to 500 images inline (no queue), so a script
    spamming this would tie up the ML thread for tens of minutes.
    """
    from backend.security import enforce_rate_limit
    await enforce_rate_limit(
        key=f"ml:backfill-vision:{user.id}",
        limit=3,
        window_seconds=3600,
        detail="Too many vision backfills. Try again in an hour.",
    )

    from sqlalchemy import or_, update as sa_update
    from backend.image import _maybe_run_vision, fetch_original
    from backend.image import _upsert_tags

    candidate_ids = (
        await session.execute(
            select(Image.id)
            .where(
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
                Image.category == "image",
                Image.vision_processed_at.is_(None),
                or_(
                    Image.scene_label.is_(None),
                    Image.content_type.is_(None),
                ),
            )
            .order_by(Image.uploaded_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    processed = 0
    failed = 0
    for image_id in candidate_ids:
        try:
            image = await session.get(Image, image_id)
            if image is None or image.deleted_at is not None:
                continue
            raw, _ = await fetch_original(image)
            vision = await _maybe_run_vision(raw)
            if vision is None:
                failed += 1
                continue
            await session.execute(
                sa_update(Image)
                .where(Image.id == image_id)
                .values(
                    clip_embedding=vision.clip_embedding,
                    content_type=vision.content_type,
                    content_confidence=vision.content_confidence,
                    scene_label=vision.scene_label,
                    scene_confidence=vision.scene_confidence,
                    face_likelihood=vision.face_likelihood,
                    indoor_outdoor=vision.indoor_outdoor,
                    vision_processed_at=datetime.now(timezone.utc),
                )
            )
            # Tags are written per-image and depend on the live Image row.
            await session.refresh(image)
            try:
                await _upsert_tags(session, image, vision.tags)
            except Exception:
                logger.exception("backfill_vision: tag upsert failed for %s", image_id)
            await session.commit()
            processed += 1
        except Exception:
            logger.exception("backfill_vision: failed on %s", image_id)
            failed += 1
            try: await session.rollback()
            except Exception: pass

    return {
        "examined": len(candidate_ids),
        "processed": processed,
        "failed": failed,
        "limit": limit,
    }


@router.post("/{image_id}/resummarize", status_code=status.HTTP_202_ACCEPTED)
async def resummarize_image(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Clear the AI Vision columns and reschedule summarization.

    Useful after upgrading models or when a summary came back empty/wrong.
    Owner-scoped via `_load_owned_image`.

    Rate-limited to 30 per hour per user — allows occasional manual
    retries on photos where the caption came back useless without
    letting a script hit it 1000 times in a row.
    """
    from backend.security import enforce_rate_limit
    await enforce_rate_limit(
        key=f"ml:resummarize:{user.id}",
        limit=30,
        window_seconds=3600,
        detail="Too many re-summarize requests. Try again in a few minutes.",
    )

    image = await _load_owned_image(image_id, user, session)
    image.summary = None
    image.summary_topic = None
    image.summary_points = None
    image.summary_generated_at = None
    image.pending_summary = True
    await session.commit()

    await _enqueue_or_inline_fallback(
        jobs.enqueue_summarize, user.id, image.id,
        inline=lambda: _run_summarize_one(image.id),
    )
    return {"image_id": str(image.id), "pending_summary": True}


@router.post("/{image_id}/redetect-faces")
async def redetect_faces(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """D8 — user-signal face re-detect cascade.

    When the default pipeline found no faces but the user knows there's
    a person in the photo, this endpoint runs:
        RetinaFace 0.3 → RetinaFace 0.15 → mediapipe face_detection
    Each later stage costs ~1-2 s more on GPU; we stop at the first
    that returns boxes. Mediapipe-detected faces are persisted without
    an ArcFace embedding — the user will need to label them manually
    (a Face row with placeholder embedding is created so the UI sees
    something to attach a name to).

    Requires `face_recognition` consent — same gate as the bulk scan.
    Returns `{stage, detected, persisted}` so the FE can tell which
    detector worked and offer the user-drawn-box fallback when stage
    == "empty".
    """
    if not await is_consent_active(session, user.id):
        raise HTTPException(
            status_code=403,
            detail="Face recognition consent is not active. Enable it in Settings → Privacy first.",
        )

    # Rate limit: 30/hour per user. The cascade detector runs three
    # passes per call (~1-2 s extra each); without this gate a script
    # could pin the GPU thread on a single user's library.
    from backend.security import enforce_rate_limit
    await enforce_rate_limit(
        key=f"ml:redetect-faces:{user.id}",
        limit=30,
        window_seconds=3600,
        detail="Too many face re-detection requests. Try again in a few minutes.",
    )

    image = await _load_owned_image(image_id, user, session)
    try:
        raw_bytes, _mime = await fetch_original(image)
    except Exception:
        raise HTTPException(status_code=410, detail="Original is no longer available.")
    if not raw_bytes:
        raise HTTPException(status_code=410, detail="Original is no longer available.")

    from backend.faces_pipeline import redetect_image_with_cascade

    result = await redetect_image_with_cascade(session, user, image, raw_bytes)
    return result


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


@router.post("/{image_id}/star", response_model=ImageRead)
async def toggle_image_starred(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Image:
    """Toggle the star/favorite flag on an image.

    No request body — toggle is the only UX surface. `starred_at` is set
    on the OFF→ON transition only; un-starring leaves the timestamp
    intact so a re-star preserves "starred X days ago" history. Returns
    the updated image so the FE can swap optimistic state for the
    server's truth in one round trip.
    """
    image = await _load_owned_image(image_id, user, session)
    image.is_starred = not image.is_starred
    if image.is_starred:
        image.starred_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(image)
    return image


@router.get("/{image_id}/suggest-names")
async def suggest_names(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=3, ge=1, le=5),
) -> dict:
    """§C1.2 — return ≤ N filename proposals for this image.

    Read-only. Reuses the existing AI summary signals (topic, summary,
    points, scene, captured place, named people) — we never fire
    Florence-2 / Qwen from this endpoint, so a rapid-fire "Regenerate"
    is essentially free. When the image still has `pending_summary=True`,
    the helper falls back to scene + capture date so the modal isn't
    empty.

    The caller validates + applies the chosen name via
    `PATCH /images/{id}/name`. This endpoint NEVER auto-renames.
    """
    image = await _load_owned_image(image_id, user, session)
    suggestions = await suggest_names_for_image(
        session, image=image, user_id=user.id, limit=limit,
    )
    return {
        "image_id": str(image.id),
        "current_name": image.original_filename,
        "pending_summary": bool(image.pending_summary),
        "suggestions": [
            {"name": s.name, "why": s.why} for s in suggestions
        ],
    }


@router.patch("/{image_id}/name", response_model=ImageRead)
async def rename_image(
    image_id: UUID,
    body: ImageRename,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Image:
    """Rename an image's display filename.

    Storage key stays UUID; only `images.original_filename` mutates.
    Validation is centralized in
    `backend.upload_validation.validate_image_filename` so the same rules
    apply to the upload path (when we eventually let users name uploads
    in-flight) and the rename path here. The validator enforces:
    no path separators, no Windows-reserved names, extension preserved,
    ≤ 255 UTF-8 bytes, no control chars.

    Returns the updated image so the FE can swap optimistic state.
    """
    image = await _load_owned_image(image_id, user, session)
    try:
        sanitized = validate_image_filename(body.name, image.original_filename)
    except UploadValidationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    image.original_filename = sanitized
    await session.commit()
    await session.refresh(image)
    return image


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    purge: Annotated[bool, Query()] = False,
) -> None:
    """Soft-delete by default: mark `deleted_at` so the row moves to
    Trash, and the user can restore it from there. Pass `?purge=true`
    to bypass the trash and hard-delete immediately — used by the
    "Empty Trash" / "Permanently delete" flows.

    Previously this hard-deleted on every call, which is why nothing
    ever appeared in the Trash view.
    """
    image = await _load_owned_image(image_id, user, session, include_deleted=True)
    if purge or image.deleted_at is not None:
        # Already trashed → user is asking for the final purge. Or the
        # caller explicitly opted in via ?purge=true.
        await hard_delete_images(
            session,
            user_id=user.id,
            image_ids=[image.id],
            audit_action="image.purge" if purge else "image.delete",
        )
    else:
        from datetime import datetime, timezone
        image.deleted_at = datetime.now(timezone.utc)
    await session.commit()


@router.post("/bulk-delete")
async def bulk_delete(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    ids: list[UUID],
    purge: Annotated[bool, Query()] = False,
) -> dict:
    """Bulk-soft-delete to Trash. Pass `?purge=true` to skip the trash
    and hard-purge immediately (used by the "Empty Trash" path)."""
    # When purging, we need to find ALREADY-deleted rows (they sit in
    # the Trash with deleted_at set). When soft-deleting, only find rows
    # not yet trashed. Previously this filtered `deleted_at IS NULL`
    # unconditionally — which meant "Delete forever" from the Trash
    # view never matched any rows and silently reported "skipped".
    stmt = select(Image).where(
        Image.id.in_(ids),
        Image.user_id == user.id,
    )
    if not purge:
        stmt = stmt.where(Image.deleted_at.is_(None))
    result = await session.execute(stmt)
    images = list(result.scalars().all())
    if purge:
        res = await hard_delete_images(
            session,
            user_id=user.id,
            image_ids=[img.id for img in images],
            audit_action="image.bulk_purge",
        )
        moved_ids = res.image_ids
    else:
        # Soft delete — set deleted_at on each selected row. Stays
        # listable via /images/?trashed=true so the Trash view can
        # render + restore.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for img in images:
            img.deleted_at = now
        moved_ids = [img.id for img in images]
    await session.commit()
    requested = {str(i) for i in ids}
    moved = {str(i) for i in moved_ids}
    skipped = sorted(requested - moved)
    return {
        "deleted": sorted(moved),
        "count": len(moved_ids),
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


@router.post("/bulk-move")
async def bulk_move(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    body: dict,
) -> dict:
    """Move N images to a folder (or back to root with `folder_id=null`).

    Body: `{ "ids": ["<uuid>", ...], "folder_id": "<uuid>" | null }`.
    Owner-scoped — silently skips ids that don't belong to this user.
    Validates the destination folder exists for this user (when not
    null). Returns the count actually moved.
    """
    from sqlalchemy import update as sa_update
    from backend.models import Folder

    raw_ids = body.get("ids") or []
    try:
        image_ids = [UUID(str(x)) for x in raw_ids]
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Invalid id list: {exc}"
        )
    if not image_ids:
        return {"moved": 0}

    folder_id = body.get("folder_id")
    if folder_id is not None:
        try:
            folder_id = UUID(str(folder_id))
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid folder_id")
        # Verify the folder belongs to this user and isn't deleted —
        # otherwise the FK update would either fail (cross-user write
        # blocked by RLS) or hide the moved images from the user.
        folder = await session.get(Folder, folder_id)
        if (
            folder is None
            or folder.user_id != user.id
            or folder.deleted_at is not None
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")

    res = await session.execute(
        sa_update(Image)
        .where(
            Image.id.in_(image_ids),
            Image.user_id == user.id,
            Image.deleted_at.is_(None),
        )
        .values(folder_id=folder_id)
    )
    moved = int(res.rowcount or 0)
    await session.commit()
    skipped = max(0, len(image_ids) - moved)
    return {
        "moved": moved,
        "skipped": skipped,
        "folder_id": str(folder_id) if folder_id else None,
    }


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
