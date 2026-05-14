"""Pass B database side: detect faces, embed, cluster against per-user gallery.

This module owns:
  - calling backend.vision.faces.detect_and_embed (the model side)
  - storing crops to MinIO faces bucket
  - persisting Face + FaceDetection rows
  - two-stage matching: first against named-person centroids, then against
    individual face embeddings; falls back to a fresh cluster
  - per-person centroid maintenance — every time a face attaches to a
    person, the centroid is recomputed from all that person's faces. This
    is the "training" the user expects from naming photos.

It is gated by the caller (image.py orchestrator) which checks
backend.consent.is_consent_active first.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Face, FaceDetection, Image, Person, User
from backend.storage import storage

logger = logging.getLogger(__name__)

# ArcFace cosine similarity thresholds. Same-person pairs typically land in
# [0.4, 0.8]; different-person pairs are usually <0.3. Centroids average out
# variance so we can match against them with a slightly looser bar.
SAME_PERSON_THRESHOLD_FACE = 0.50      # individual-face match (vs. existing Face row)
SAME_PERSON_THRESHOLD_CENTROID = 0.40  # named-person centroid match

# Back-compat constant exported for any external callers / tests.
SAME_PERSON_THRESHOLD = SAME_PERSON_THRESHOLD_FACE

# Minimum detection confidence to consider a face "real" (filters tiny / blurry detections).
MIN_DET_CONFIDENCE = 0.55


def _detect_sync(raw_bytes: bytes):
    """Module-import lazy: requires insightface + onnxruntime."""
    from backend.vision.faces import detect_and_embed

    return detect_and_embed(raw_bytes)


async def _detect_async(raw_bytes: bytes):
    # Single-thread ML executor so face detection serializes with
    # in-flight Florence/Qwen inferences instead of racing for the
    # GIL — keeps the API event loop responsive during backfill.
    from backend.vision.inference_pool import run_in_inference_pool
    return await run_in_inference_pool(_detect_sync, raw_bytes)


async def _match_named_person(
    session: AsyncSession,
    user_id,
    embedding: list[float],
) -> Optional[Person]:
    """Stage 1: nearest named person by centroid distance."""
    distance = Person.centroid_embedding.cosine_distance(embedding)
    stmt = (
        select(Person, distance.label("distance"))
        .where(
            Person.user_id == user_id,
            Person.centroid_embedding.is_not(None),
            Person.face_count > 0,
        )
        .order_by(distance.asc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    person, dist = row[0], float(row[1])
    if (1.0 - dist) >= SAME_PERSON_THRESHOLD_CENTROID:
        return person
    return None


async def _match_individual_face(
    session: AsyncSession,
    user_id,
    embedding: list[float],
) -> Optional[Face]:
    """Stage 2: nearest individual face row (handles unnamed clusters)."""
    distance = Face.embedding.cosine_distance(embedding)
    stmt = (
        select(Face, distance.label("distance"))
        .where(Face.user_id == user_id)
        .order_by(distance.asc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    face, dist = row[0], float(row[1])
    if (1.0 - dist) >= SAME_PERSON_THRESHOLD_FACE:
        return face
    return None


async def _next_cluster_id(session: AsyncSession, user_id) -> int:
    res = await session.execute(
        select(func.coalesce(func.max(Face.cluster_id), 0)).where(Face.user_id == user_id)
    )
    return int(res.scalar() or 0) + 1


async def update_person_centroid(
    session: AsyncSession, user_id, person_id: int
) -> int:
    """Recompute the centroid for one named person from all their face
    embeddings. Returns the face count averaged. Centroid is L2-normalized
    so cosine similarity is consistent with ArcFace's unit-norm embeddings.
    """
    rows = (
        await session.execute(
            select(Face.embedding).where(
                Face.user_id == user_id, Face.person_id == person_id
            )
        )
    ).scalars().all()
    if not rows:
        await session.execute(
            update(Person)
            .where(Person.id == person_id)
            .values(centroid_embedding=None, face_count=0)
        )
        return 0
    arr = np.asarray(rows, dtype=np.float32)
    centroid = arr.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    await session.execute(
        update(Person)
        .where(Person.id == person_id)
        .values(
            centroid_embedding=centroid.tolist(),
            face_count=len(rows),
            updated_at=datetime.now(timezone.utc),
        )
    )
    return len(rows)


async def process_image_for_faces(
    session: AsyncSession,
    user: User,
    image: Image,
    raw_bytes: bytes,
    *,
    detections: list | None = None,
    min_confidence: float | None = None,
) -> int:
    """Run Pass B for one image. Returns number of faces persisted.

    Caller is responsible for verifying consent before invoking this.
    The image row's `pending_face_scan` flag is cleared on completion.

    `detections` (optional) lets a caller pre-supply detection results —
    used by the D8 re-detect cascade where we run RetinaFace at a lower
    threshold (or mediapipe as a third-stage fallback) and then plug
    those results into the same persistence loop. When None, the
    default detector runs.

    `min_confidence` (optional) overrides `MIN_DET_CONFIDENCE` for this
    call. D8 lowers it to ~0.1 because the cascade already pre-filtered.
    """
    threshold = MIN_DET_CONFIDENCE if min_confidence is None else min_confidence
    if detections is None:
        try:
            detections = await _detect_async(raw_bytes)
            # Auto-cascade trigger: when the default detector finds
            # nothing on a frame CLIP scored as likely-containing-a-
            # person, fall through to RetinaFace 0.15 → mediapipe.
            # This catches the "B&W eye-only close-up" miss case
            # without forcing the user to manually click "this has a
            # person in it" first. We relax `min_confidence` for the
            # cascade results since the cascade already pre-filtered.
            if not detections and getattr(image, "face_likelihood", 0) and image.face_likelihood >= 0.5:
                from backend.vision.faces import detect_with_cascade
                from backend.vision.inference_pool import run_in_inference_pool
                cascaded, stage = await run_in_inference_pool(
                    detect_with_cascade, raw_bytes
                )
                if cascaded:
                    logger.info(
                        "auto-cascade rescued image %s via %s (%d faces)",
                        image.id, stage, len(cascaded),
                    )
                    detections = cascaded
                    threshold = min(threshold, 0.10)
        except ImportError as exc:
            logger.warning(
                "Face pipeline unavailable (install [ml] extras + insightface): %s", exc,
            )
            return 0
        except Exception:
            logger.exception("Face detection crashed for image %s", image.id)
            return 0

    persisted = 0
    persons_to_recentroid: set[int] = set()

    for det in detections:
        if det.detection_confidence < threshold:
            continue
        if not det.embedding:
            # Mediapipe-cascade detections carry no ArcFace embedding —
            # we still want a Face row so the user can label it, but
            # skip the centroid / cluster matching and don't try to
            # average a null vector into a person centroid.
            crop_key = f"users/{user.id}/faces/{uuid4().hex}.jpg"
            try:
                storage.put(
                    storage.bucket_faces, crop_key,
                    det.crop_jpeg, "image/jpeg", sse_scope="biometric",
                )
            except Exception:
                logger.exception("Could not write fallback face crop %s", crop_key)
                continue
            face_row = Face(
                user_id=user.id,
                embedding=[0.0] * 512,  # placeholder; user can re-label manually
                person_id=None,
                cluster_id=None,
                quality_score=det.detection_confidence,
            )
            session.add(face_row)
            await session.flush()
            session.add(
                FaceDetection(
                    image_id=image.id,
                    face_id=face_row.id,
                    user_id=user.id,
                    bbox_x=det.bbox_x,
                    bbox_y=det.bbox_y,
                    bbox_w=det.bbox_w,
                    bbox_h=det.bbox_h,
                    detection_confidence=det.detection_confidence,
                    crop_blob_key=crop_key,
                    landmarks_json=(det.landmarks or None),
                )
            )
            persisted += 1
            continue

        crop_key = f"users/{user.id}/faces/{uuid4().hex}.jpg"
        try:
            storage.put(
                storage.bucket_faces,
                crop_key,
                det.crop_jpeg,
                "image/jpeg",
                sse_scope="biometric",
            )
        except Exception:
            logger.exception("Could not write face crop %s", crop_key)
            crop_key_to_save = None
        else:
            crop_key_to_save = crop_key

        # Stage 1: try a named-person centroid match first (cheaper + more
        # reliable once we have ≥2 named faces, because the centroid averages
        # variance across angles/lighting). Stage 2 is a fall-back over raw
        # face rows so unnamed clusters can still grow.
        person_match = await _match_named_person(session, user.id, det.embedding)
        if person_match is not None:
            sample_face = (
                await session.execute(
                    select(Face)
                    .where(
                        Face.user_id == user.id,
                        Face.person_id == person_match.id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            face_row = Face(
                user_id=user.id,
                embedding=det.embedding,
                person_id=person_match.id,
                cluster_id=sample_face.cluster_id if sample_face else None,
                quality_score=det.detection_confidence,
            )
            session.add(face_row)
            await session.flush()
            persons_to_recentroid.add(person_match.id)
        else:
            face_match = await _match_individual_face(session, user.id, det.embedding)
            if face_match is not None:
                face_row = Face(
                    user_id=user.id,
                    embedding=det.embedding,
                    person_id=face_match.person_id,
                    cluster_id=face_match.cluster_id,
                    quality_score=det.detection_confidence,
                )
                session.add(face_row)
                await session.flush()
                if face_match.person_id is not None:
                    persons_to_recentroid.add(face_match.person_id)
            else:
                cluster_id = await _next_cluster_id(session, user.id)
                face_row = Face(
                    user_id=user.id,
                    embedding=det.embedding,
                    person_id=None,
                    cluster_id=cluster_id,
                    quality_score=det.detection_confidence,
                )
                session.add(face_row)
                await session.flush()

        session.add(
            FaceDetection(
                image_id=image.id,
                user_id=user.id,
                bbox_x=det.bbox_x,
                bbox_y=det.bbox_y,
                bbox_w=det.bbox_w,
                bbox_h=det.bbox_h,
                detection_confidence=det.detection_confidence,
                face_id=face_row.id,
                crop_blob_key=crop_key_to_save,
                landmarks_json=(det.landmarks or None),
            )
        )
        persisted += 1

    image.pending_face_scan = False
    await session.flush()

    # Recompute centroids for any persons that gained faces. Doing this once
    # at end (instead of after each face) keeps a multi-face image cheap.
    for person_id in persons_to_recentroid:
        await update_person_centroid(session, user.id, person_id)

    await session.commit()
    return persisted


async def redetect_image_with_cascade(
    session: AsyncSession,
    user: User,
    image: Image,
    raw_bytes: bytes,
) -> dict:
    """D8 user-signal cascade entry point.

    User clicked "Mark as containing a person" on a photo that the
    default pipeline found nothing in. We run
    `detect_with_cascade` (RetinaFace 0.3 → 0.15 → mediapipe) and feed
    the result into the existing persistence loop with a relaxed
    confidence floor so the borderline detections actually make it.

    Returns `{stage, persisted, detected}` so the FE can show the
    user what worked (and surface the manual-draw flow when "empty").
    """
    from backend.vision.faces import detect_with_cascade
    from backend.vision.inference_pool import run_in_inference_pool

    try:
        cascaded, stage = await run_in_inference_pool(detect_with_cascade, raw_bytes)
    except ImportError as exc:
        logger.warning("Cascade unavailable: %s", exc)
        return {"stage": "empty", "persisted": 0, "detected": 0}
    except Exception:
        logger.exception("Cascade crashed for image %s", image.id)
        return {"stage": "empty", "persisted": 0, "detected": 0}

    if not cascaded:
        return {"stage": stage, "persisted": 0, "detected": 0}

    persisted = await process_image_for_faces(
        session,
        user,
        image,
        raw_bytes,
        detections=cascaded,
        min_confidence=0.1,  # cascade already pre-filtered; trust it
    )
    return {"stage": stage, "persisted": persisted, "detected": len(cascaded)}
