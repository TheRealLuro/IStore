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
from uuid import uuid4

from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.codecs import compress
from backend.config import settings
from backend.models import Image, ImageTag, Tag, User
from backend.policy import VisionContext, pick_plan
from backend.storage import storage

logger = logging.getLogger(__name__)


def _run_vision_sync(raw_bytes: bytes):
    """Loaded lazily so the FastAPI app doesn't import torch unless ML is installed."""
    from backend.vision.pipeline import process

    return process(raw_bytes)


async def _maybe_run_vision(raw_bytes: bytes):
    if not settings.vision_enabled:
        return None
    try:
        return await asyncio.to_thread(_run_vision_sync, raw_bytes)
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
    if not tags:
        return
    labels = [t for t, _ in tags]
    existing = await session.execute(select(Tag).where(Tag.label.in_(labels)))
    label_to_tag = {t.label: t for t in existing.scalars().all()}

    for label, score in tags:
        tag = label_to_tag.get(label)
        if tag is None:
            tag = Tag(label=label, source="clip")
            session.add(tag)
            await session.flush()
            label_to_tag[label] = tag
        session.add(ImageTag(image_id=image.id, tag_id=tag.id, confidence=score))


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
) -> Image:
    sha = hashlib.sha256(raw_bytes).digest()
    category = _detect_category(content_type, filename)

    if category != "image":
        return await _store_non_image(session, user, filename, raw_bytes, content_type, sha, category)

    with PILImage.open(BytesIO(raw_bytes)) as pil:
        pil.load()
        width, height = pil.size

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
    plan = pick_plan(vctx, width, height, len(raw_bytes))

    served_bytes = compress(raw_bytes, plan)

    safe_name = filename or "image"
    original_key = f"users/{user.id}/originals/{uuid4().hex}/{safe_name}"
    served_key = f"users/{user.id}/served/{uuid4().hex}.{plan.extension}"

    storage.put(
        storage.bucket_originals,
        original_key,
        raw_bytes,
        content_type or "application/octet-stream",
    )
    storage.put(storage.bucket_served, served_key, served_bytes, plan.mime)

    image = Image(
        user_id=user.id,
        category="image",
        original_blob_key=original_key,
        served_blob_key=served_key,
        original_filename=filename,
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
    )

    if vision is not None:
        image.clip_embedding = vision.clip_embedding
        image.content_type = vision.content_type
        image.content_confidence = vision.content_confidence
        image.scene_label = vision.scene_label
        image.scene_confidence = vision.scene_confidence
        image.face_likelihood = vision.face_likelihood
        image.indoor_outdoor = vision.indoor_outdoor
        image.pending_face_scan = vision.face_likelihood > 0.5
        image.vision_processed_at = datetime.now(timezone.utc)
    # else: pending_face_scan stays at its default (true) — safer to scan
    # later when we have a vision result, than to assume no face.

    session.add(image)
    await session.flush()  # need image.id before inserting image_tags

    if vision is not None:
        await _upsert_tags(session, image, vision.tags)

    await session.commit()
    await session.refresh(image)
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
    """Documents / videos / other: stored as-is, no compression, no vision."""
    safe_name = filename or "upload"
    original_key = f"users/{user.id}/originals/{uuid4().hex}/{safe_name}"
    served_key = original_key  # served == original for non-image categories

    storage.put(
        storage.bucket_originals,
        original_key,
        raw_bytes,
        content_type or "application/octet-stream",
    )

    image = Image(
        user_id=user.id,
        category=category,
        original_blob_key=original_key,
        served_blob_key=served_key,
        original_filename=filename,
        byte_size_original=len(raw_bytes),
        byte_size_served=len(raw_bytes),
        mime_type_original=content_type,
        mime_type_served=content_type,
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


async def fetch_original(image: Image) -> tuple[bytes, str]:
    blob = storage.get(storage.bucket_originals, image.original_blob_key)
    return blob, image.mime_type_original or "application/octet-stream"


async def fetch_served(image: Image) -> tuple[bytes, str]:
    blob = storage.get(storage.bucket_served, image.served_blob_key)
    return blob, image.mime_type_served or "application/octet-stream"
