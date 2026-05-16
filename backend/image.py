"""Upload orchestrator.

Pipeline:
  raw upload bytes
    -> hash + decode metadata (Pillow)
    -> Pass-A vision pipeline (CLIP zero-shot) — async-offloaded so the event
       loop isn't blocked. Returns content_type, scene, tags, face_likelihood,
       768-d CLIP embedding.
    -> policy.pick_plan(vision) → CompressionPlan (the screenshot fix lives here)
    -> codecs.compress
    -> store original + served to MinIO
    -> persist Image row + ImageTag rows

If the vision pipeline cannot be loaded (e.g. `[ml]` extras not installed),
we fall back to the Phase 1 default plan and skip vision columns.

Decompression for downloads is just fetching the original bytes back from
MinIO; we never lose the source, so there's no inverse transform to compute.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from io import BytesIO
from uuid import UUID, uuid4

from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.codecs import compress
from backend.config import settings
from backend.consent import is_scope_active
from backend.models import Image, ImageGeo, ImageTag, Tag, User
from backend.policy import VisionContext, pick_plan_with_bandit
from backend.storage import storage
from backend.upload_validation import validate_upload

logger = logging.getLogger(__name__)


def _run_vision_sync(raw_bytes: bytes):
    """Loaded lazily so the FastAPI app doesn't import torch unless ML is installed."""
    from backend.vision.pipeline import process

    return process(raw_bytes)


async def _maybe_run_vision(raw_bytes: bytes):
    if not settings.vision_enabled:
        return None
    # CLIP + scene/content classification go through the shared ML
    # executor too — keeps upload-time vision from blocking the API
    # event loop while a backfill is running.
    from backend.vision.inference_pool import run_in_inference_pool
    try:
        return await run_in_inference_pool(_run_vision_sync, raw_bytes)
    except ImportError as exc:
        logger.warning("Vision pipeline unavailable (install [ml] extras): %s", exc)
        return None
    except Exception:
        logger.exception("Vision pipeline crashed; falling back to default plan")
        return None


async def _upsert_tags(
    session: AsyncSession,
    image: Image,
    tags: list[tuple[str, float]],
) -> None:
    """Persist CLIP-suggested tags for a freshly-stored image.

    §C1.6 made tags per-user — both `tags.user_id` and `image_tags.user_id`
    are NOT NULL. The CLIP-tag path previously created rows without
    either, which silently worked for the very first user (the column
    didn't exist) but blows up with `NotNullViolationError` against
    the current schema. Always scope tag lookups + inserts to the
    image's owner.
    """
    if not tags:
        return
    labels = [t for t, _ in tags]
    # Scope existing-tag lookup to this user — without it, we'd attach
    # a different user's "Sunset" tag to the current user's image and
    # cross-RLS-boundary the row.
    existing = await session.execute(
        select(Tag).where(Tag.user_id == image.user_id, Tag.label.in_(labels))
    )
    label_to_tag = {t.label: t for t in existing.scalars().all()}

    for label, score in tags:
        tag = label_to_tag.get(label)
        if tag is None:
            tag = Tag(user_id=image.user_id, label=label, source="clip")
            session.add(tag)
            await session.flush()
            label_to_tag[label] = tag
        session.add(
            ImageTag(
                image_id=image.id,
                tag_id=tag.id,
                user_id=image.user_id,
                confidence=score,
            )
        )


def _strip_exif_bytes(data: bytes, mime: str | None) -> bytes | None:
    """Return a copy of `data` with the EXIF block removed.

    §B1: when the user hasn't opted into `gps_retention` or
    `exif_retention`, the originals bucket should never see EXIF
    metadata. We re-encode through Pillow without the `exif=` kwarg
    so the output has no APP1 marker / no embedded GPS / camera
    fingerprint. Returns the new bytes, or None when we can't
    safely strip (unknown format, decode failure) — caller falls
    back to the original bytes so we never silently produce broken
    output.

    JPEG / WebP / TIFF are the formats that carry EXIF in our
    pipeline; PNG/GIF don't. For PNG/GIF this is a no-op (returns
    the input bytes unchanged so the caller's hash math stays
    stable).
    """
    if not mime or not mime.startswith("image/"):
        return None
    if mime in {"image/png", "image/gif"}:
        # No EXIF in these formats — nothing to strip.
        return data
    try:
        with PILImage.open(BytesIO(data)) as pil:
            pil.load()
            out = BytesIO()
            if mime == "image/jpeg":
                pil.convert("RGB").save(
                    out, format="JPEG", quality=95, optimize=True,
                )
            elif mime == "image/webp":
                pil.save(out, format="WEBP", lossless=True)
            elif mime == "image/tiff":
                # Sanitize already converts TIFF→PNG for served; for
                # originals we keep TIFF but drop EXIF.
                pil.save(out, format="TIFF")
            else:
                return None
            return out.getvalue()
    except Exception:
        logger.exception("strip_exif: re-encode failed; keeping original bytes")
        return None


def _exif_gps(raw_bytes: bytes) -> dict | None:
    """Return `{lat, lng, taken_at, captured_with}` from a JPEG/HEIC/TIFF
    EXIF block, or None if GPS isn't present / valid.

    Pillow exposes EXIF via `Image.getexif()`; the GPS sub-IFD lives at
    tag 34853. Coordinates are stored as three rationals (deg/min/sec)
    plus a hemisphere reference char ('N'/'S'/'E'/'W'). Anything that
    looks malformed → None so we never persist garbage.
    """
    try:
        with PILImage.open(BytesIO(raw_bytes)) as pil:
            exif = pil.getexif()
            if not exif:
                return None
            gps = exif.get_ifd(0x8825)  # GPSInfo IFD
            if not gps:
                return None
            lat_dms = gps.get(2)
            lat_ref = gps.get(1)
            lng_dms = gps.get(4)
            lng_ref = gps.get(3)
            if not (lat_dms and lng_dms and lat_ref and lng_ref):
                return None

            def _dms_to_decimal(dms) -> float:
                d, m, s = (float(x) for x in dms)
                return d + m / 60 + s / 3600

            lat = _dms_to_decimal(lat_dms)
            lng = _dms_to_decimal(lng_dms)
            if lat_ref in ("S", "s"):
                lat = -lat
            if lng_ref in ("W", "w"):
                lng = -lng
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                return None  # filed-but-bogus EXIF — ignore

            # Capture timestamp (DateTimeOriginal lives in the EXIF IFD,
            # not the GPS IFD). Optional; persist if parseable.
            exif_ifd = exif.get_ifd(0x8769)
            taken_raw = exif_ifd.get(0x9003) if exif_ifd else None
            taken_at = None
            if taken_raw:
                try:
                    taken_at = datetime.strptime(taken_raw, "%Y:%m:%d %H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                except (ValueError, TypeError):
                    taken_at = None

            captured_with = exif.get(272)  # Make+Model collapsed; Model is 272
            if isinstance(captured_with, bytes):
                captured_with = captured_with.decode("ascii", errors="replace")
            if captured_with and len(captured_with) > 120:
                captured_with = captured_with[:120]

            return {
                "lat": float(lat),
                "lng": float(lng),
                "taken_at": taken_at,
                "captured_with": captured_with,
            }
    except Exception:
        # Any decoder failure → no GPS. We never crash the upload over EXIF.
        logger.debug("EXIF GPS parse failed", exc_info=True)
        return None


def _detect_category(content_type: str | None, filename: str | None) -> str:
    ct = (content_type or "").lower()
    name = (filename or "").lower()
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    if ct in {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/markdown",
        "text/csv",
    } or any(name.endswith(ext) for ext in
              (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
               ".txt", ".md", ".csv")):
        return "document"
    if any(name.endswith(ext) for ext in (".mp4", ".mov", ".avi", ".mkv", ".webm")):
        return "video"
    return "other"


async def store_upload(
    session: AsyncSession,
    user: User,
    filename: str | None,
    raw_bytes: bytes,
    content_type: str | None,
    *,
    # §C2 — set True when ingesting from a cloud provider whose ToS
    # forbids AI training on the content (Google Drive Limited Use).
    # Skips the vision pass, leaves `pending_face_scan=False` and
    # `pending_summary=False` so the post-commit background workers
    # never pick the row up. The flag is persisted to
    # `images.skip_ai_training` and can later be flipped per-source
    # via the cloud-link settings UI; flipping it re-queues the file
    # through the normal summarize/face-scan workers.
    skip_ai_training: bool = False,
    # §C2 — provider identifier ('google_drive', 'github', …) when the
    # ingest came from a cloud sync; mirrored into
    # `images.source_provider` for the UI badge + per-source bulk ops.
    source_provider: str | None = None,
    # §C2 — optional pre-assigned folder for the synthesized remote
    # folder tree. The sync worker creates the folder rows up-front
    # and passes the leaf id here so we don't have to round-trip.
    folder_id: UUID | None = None,
) -> Image:
    validated = await validate_upload(
        user_id=user.id,
        filename=filename,
        raw_bytes=raw_bytes,
        client_mime=content_type,
    )
    raw_for_metadata = validated.original_raw_bytes
    raw_bytes = validated.raw_bytes
    content_type = validated.mime_type
    filename = validated.filename
    sha = hashlib.sha256(raw_bytes).digest()
    category = validated.category

    if category != "image":
        return await _store_non_image(session, user, filename, raw_bytes, content_type, sha, category)

    # §B1 — strip EXIF from originals unless the user has explicitly
    # opted in. `gps_retention` keeps GPS subset, `exif_retention` keeps
    # camera/lens subset; if neither is granted we re-encode without
    # the EXIF blob entirely. We do this BEFORE handing bytes to the
    # bandit compressor / originals bucket so neither path ever sees
    # the unstripped variant. raw_for_metadata is preserved so the
    # gps-extraction code below can still read GPS *iff* the consent
    # is on (it'll have been kept in raw_bytes too in that case).
    keep_gps = await is_scope_active(session, user.id, "gps_retention")
    keep_camera = await is_scope_active(session, user.id, "exif_retention")
    if not (keep_gps or keep_camera):
        stripped = _strip_exif_bytes(raw_bytes, content_type)
        if stripped is not None:
            raw_bytes = stripped
            sha = hashlib.sha256(raw_bytes).digest()

    # Width / height come pre-computed from validate_upload's PIL pass.
    # The previous duplicate `PILImage.open(...)` here was a 50-200 ms
    # synchronous block per upload that ran on the event loop. The
    # fallback path (post-merge upload paths that didn't surface the
    # dims) is kept for safety but should never fire.
    width = validated.width
    height = validated.height
    if width is None or height is None:
        with PILImage.open(BytesIO(raw_bytes)) as pil:
            pil.load()
            width, height = pil.size

    # §C2 — skip the CLIP + scene + face-likelihood pass when the
    # caller is ingesting from a Limited-Use cloud source. The bandit
    # context still has to pick a compression plan, so we fall back to
    # the size-only path with no vision context.
    if skip_ai_training:
        vision = None
    else:
        vision = await _maybe_run_vision(raw_bytes)

    vctx = (
        VisionContext(
            content_type=vision.content_type,
            content_confidence=vision.content_confidence,
            face_likelihood=vision.face_likelihood,
        )
        if vision
        else None
    )
    decision = await pick_plan_with_bandit(
        session=session,
        user_id=user.id,
        vision=vctx,
        scene_label=vision.scene_label if vision else None,
        indoor_outdoor=vision.indoor_outdoor if vision else None,
        clip_embedding=vision.clip_embedding if vision else None,
        width=width,
        height=height,
        byte_size=len(raw_bytes),
        mime_in=content_type,
    )
    plan = decision.plan

    # Compress the served variant on a worker thread so the PIL pass
    # doesn't block the event loop. Same encoder + quality as before
    # — same output bytes — just runs off the asyncio main thread.
    served_bytes = await asyncio.to_thread(compress, raw_bytes, plan)

    safe_name = filename or "image"
    original_key = f"users/{user.id}/originals/{uuid4().hex}/{safe_name}"
    served_key = f"users/{user.id}/served/{uuid4().hex}.{plan.extension}"

    # Push both blobs to MinIO in parallel — they're independent writes
    # on different bucket keys. Sequential `put`s used to add another
    # 100-500 ms per upload waiting on the second round-trip after the
    # first finished.
    await asyncio.gather(
        asyncio.to_thread(
            storage.put,
            storage.bucket_originals, original_key, raw_bytes,
            content_type or "application/octet-stream",
        ),
        asyncio.to_thread(
            storage.put,
            storage.bucket_served, served_key, served_bytes,
            plan.mime,
        ),
    )

    image = Image(
        user_id=user.id,
        category="image",
        original_blob_key=original_key,
        served_blob_key=served_key,
        original_filename=filename,
        # §C2 — Limited-Use flags + the optional pre-assigned folder
        # from the cloud-sync worker. `pending_*` start at False on
        # this path so the post-commit background workers skip the
        # row; flipping `skip_ai_training` off later (per-source
        # opt-in) sets them back to True to re-queue.
        skip_ai_training=skip_ai_training,
        source_provider=source_provider,
        folder_id=folder_id,
        pending_face_scan=not skip_ai_training,
        pending_summary=not skip_ai_training,
        width=width,
        height=height,
        byte_size_original=len(raw_bytes),
        byte_size_served=len(served_bytes),
        mime_type_original=content_type,
        mime_type_served=plan.mime,
        sha256=sha,
        codec=plan.codec,
        quality=plan.quality,
        max_dim=plan.max_dim,
        lossless=plan.lossless,
        bandit_arm_id=decision.arm_id,
        context_features=decision.context_features,
    )

    if vision is not None:
        image.clip_embedding = vision.clip_embedding
        image.content_type = vision.content_type
        image.content_confidence = vision.content_confidence
        image.scene_label = vision.scene_label
        image.scene_confidence = vision.scene_confidence
        image.face_likelihood = vision.face_likelihood
        image.indoor_outdoor = vision.indoor_outdoor
        image.vision_processed_at = datetime.now(timezone.utc)
    # `pending_face_scan` is left at its default (true). RetinaFace is the
    # authoritative face detector; CLIP zero-shot proved unreliable at the
    # binary "are there people" question (e.g. 0.03 score on a clear portrait),
    # so we no longer gate Pass B on it. Pass B itself flips this flag to
    # false after running.

    session.add(image)
    await session.flush()  # need image.id before inserting image_tags

    if vision is not None:
        await _upsert_tags(session, image, vision.tags)

    # C3 — persist EXIF GPS only when the user has granted gps_retention
    # consent. Originals retain EXIF in MinIO so a later backfill can
    # populate this table once consent is granted.
    geo_for_geocode: dict | None = None
    if await is_scope_active(session, user.id, "gps_retention"):
        gps = _exif_gps(raw_for_metadata)
        if gps is not None:
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
            # Stash for the post-commit reverse-geocode task. We don't
            # call Nominatim inline because the upload response should
            # return immediately; rate-limited HTTP would add up to a
            # second.
            geo_for_geocode = {
                "image_id": image.id,
                "user_id": user.id,
                "lat": gps["lat"],
                "lng": gps["lng"],
            }

    await session.commit()
    await session.refresh(image)

    # Fire-and-forget reverse-geocode for this upload's GPS row so the
    # preview Location field reads "Salt Lake City, Utah" instead of
    # decimal degrees. Rate-limited by Nominatim's ToS, runs against
    # its own session so it doesn't block the upload response.
    if geo_for_geocode is not None:
        asyncio.create_task(_reverse_geocode_one(**geo_for_geocode))

    # Pass B (RetinaFace + ArcFace) is intentionally NOT run here. On CPU it
    # adds 10-30 s per upload and would block the HTTP response. The route
    # handler in api/images.py kicks it off as a BackgroundTask so the
    # client gets its image row immediately and the People tray populates
    # asynchronously. `pending_face_scan` is true so the next backfill
    # (or rescan, or new face job) picks it up.

    return image


async def _store_non_image(
    session: AsyncSession,
    user: User,
    filename: str | None,
    raw_bytes: bytes,
    content_type: str | None,
    sha: bytes,
    category: str,
) -> Image:
    """Documents / videos / other: stored as-is, no compression, no vision.

    PDFs get a page-1 raster stored under the `served` bucket so the
    gallery + preview can render a thumbnail instead of a generic icon.
    Reuses the C2c PyMuPDF helper. Best-effort: if PyMuPDF isn't
    available or rasterization fails, `served` falls back to the
    original blob like the other non-image categories.
    """
    safe_name = filename or "upload"
    original_key = f"users/{user.id}/originals/{uuid4().hex}/{safe_name}"

    storage.put(
        storage.bucket_originals,
        original_key,
        raw_bytes,
        content_type or "application/octet-stream",
        sse_scope="content",
    )

    served_key = original_key
    served_bytes_len = len(raw_bytes)
    served_mime = content_type
    is_pdf = (content_type == "application/pdf") or (
        filename and filename.lower().endswith(".pdf")
    )
    if is_pdf:
        thumb_png = _pdf_page_one_thumb(raw_bytes)
        if thumb_png:
            served_key = f"users/{user.id}/served/{uuid4().hex}.png"
            try:
                storage.put(
                    storage.bucket_served,
                    served_key,
                    thumb_png,
                    "image/png",
                    sse_scope="content",
                )
                served_bytes_len = len(thumb_png)
                served_mime = "image/png"
            except Exception:
                logger.exception("Could not write PDF thumb %s", served_key)
                served_key = original_key  # fall back to original

    image = Image(
        user_id=user.id,
        category=category,
        original_blob_key=original_key,
        served_blob_key=served_key,
        original_filename=filename,
        byte_size_original=len(raw_bytes),
        byte_size_served=served_bytes_len,
        mime_type_original=content_type,
        mime_type_served=served_mime,
        sha256=sha,
        codec=None,
        quality=None,
        max_dim=None,
        lossless=None,
        pending_face_scan=False,
    )
    session.add(image)
    await session.commit()
    await session.refresh(image)
    return image


def _pdf_page_one_thumb(raw_bytes: bytes) -> bytes | None:
    """Reuse `summarize._pdf_pages_to_images` for the upload-time PDF
    thumbnail. Imported lazily so the basic upload path doesn't pull in
    the [ml] extras when only docs are uploaded but PyMuPDF isn't
    installed. Returns None on any failure."""
    try:
        from backend.summarize import _pdf_pages_to_images
        pages = _pdf_pages_to_images(raw_bytes, max_pages=1)
        return pages[0] if pages else None
    except Exception:
        logger.exception("PDF thumb rasterize failed")
        return None


async def fetch_original(image: Image) -> tuple[bytes, str]:
    blob = storage.get(storage.bucket_originals, image.original_blob_key)
    return blob, image.mime_type_original or "application/octet-stream"


async def fetch_served(image: Image) -> tuple[bytes, str]:
    # For non-image categories (documents, videos) we don't compress; the
    # served variant *is* the original blob. `_store_non_image` writes only
    # to the originals bucket and sets `served_blob_key = original_blob_key`.
    # When that's the shape, read from the originals bucket — otherwise the
    # served bucket is the right place (Phase 3 compressed images).
    bucket = (
        storage.bucket_originals
        if image.served_blob_key == image.original_blob_key
        else storage.bucket_served
    )
    blob = storage.get(bucket, image.served_blob_key)
    return blob, image.mime_type_served or "application/octet-stream"


async def _reverse_geocode_one(image_id, user_id, lat: float, lng: float) -> None:
    """Best-effort reverse-geocode for a single just-uploaded image.

    Runs as a fire-and-forget task post-commit so the upload response
    flushes immediately. Uses its own session because the request's
    session is closed by the time we get here. Failures are silent —
    the next call to `/images/geo/backfill-places` will retry.
    """
    from backend.api.images import _reverse_geocode  # local — avoids cycle

    try:
        place = await _reverse_geocode(lat, lng)
    except Exception:
        logger.exception("upload reverse-geocode failed for %s", image_id)
        return
    if not place:
        return
    from sqlalchemy import update as sa_update

    from backend.db import SessionLocal

    try:
        async with SessionLocal() as session:
            await session.execute(
                sa_update(ImageGeo)
                .where(ImageGeo.image_id == image_id, ImageGeo.user_id == user_id)
                .values(place=place)
            )
            await session.commit()
    except Exception:
        logger.exception("upload reverse-geocode commit failed for %s", image_id)
