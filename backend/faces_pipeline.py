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
from sqlalchemy import func, select, type_coerce, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Face,
    FaceDetection,
    Image,
    Person,
    RejectedFaceEmbedding,
    User,
)
from backend.storage import storage

try:  # pgvector is only present where the [ml]/db extras are installed.
    from pgvector.sqlalchemy import Vector as _Vector
except Exception:  # pragma: no cover - keeps import working in bare envs
    _Vector = None

# The all-zeros placeholder embedding stored on no-embedding faces
# (mediapipe cascade / manual box). Used to exclude those rows from
# nearest-face matching with a plain `!=` — pgvector's `l2_norm(vector)`
# is overloaded (vector + halfvec) and resolves ambiguously, so a norm
# predicate raises AmbiguousFunctionError; an equality test against the
# zero vector is unambiguous and index-friendly.
_PLACEHOLDER_EMBEDDING: list[float] = [0.0] * 512

logger = logging.getLogger(__name__)

# ArcFace cosine similarity thresholds. Same-person pairs on buffalo_l
# typically land in [0.45, 0.85]; different-person pairs usually sit
# below ~0.30. Centroids average out per-person variance, so a *correct*
# match against a centroid is usually 0.50+. We deliberately set the
# auto-attach bar high: merging a face into the WRONG named person
# (the "Jason got merged into Patrick" failure) is far more damaging to
# the user than leaving a face in its own unlabeled cluster that they
# can name in one click. When in doubt we prefer a NEW cluster.
SAME_PERSON_THRESHOLD_FACE = 0.50      # individual-face match (vs. existing Face row)

# Named-person centroid match. Raised 0.40 -> 0.52. At 0.40 a
# different-person face whose embedding happened to land in the
# 0.40-0.50 "ambiguous" band auto-merged into a named person and then
# polluted that person's centroid, snowballing into more wrong matches.
# 0.52 keeps confident frontal matches (which sit 0.55-0.85) while
# refusing the ambiguous band.
SAME_PERSON_THRESHOLD_CENTROID = 0.52

# Detector confidence required to AUTO-ATTACH a face to an EXISTING
# named person. Higher than MIN_DET_CONFIDENCE: a blurry / tiny / heavily
# cropped detection produces a noisier embedding, and a noisy embedding
# is exactly what slips past the centroid bar into the wrong person.
# Borderline detections (between MIN_DET_CONFIDENCE and this) are still
# kept — they just form / grow an UNLABELED cluster instead of merging
# into a named identity. The user can confirm them with one click.
MIN_DET_CONFIDENCE_NAMED_ATTACH = 0.70

# The adaptive "low-confidence but clear-winner" centroid path
# (SAME_PERSON_LOW_THRESHOLD / _MARGIN) has been REMOVED. It accepted a
# centroid match at cosine sim as low as 0.18 as long as it beat the
# runner-up by 0.08 — but 0.18 sim is squarely in different-person
# territory for ArcFace, and the margin gate gave false confidence when
# the true person simply wasn't in the library yet. This path was the
# primary driver of wrong-person merges (e.g. Jason -> Patrick). Faces
# that don't clear SAME_PERSON_THRESHOLD_CENTROID now form their own
# unlabeled cluster, which the user resolves via the People UI.

# Back-compat constant exported for any external callers / tests.
SAME_PERSON_THRESHOLD = SAME_PERSON_THRESHOLD_FACE

# Minimum detection confidence to consider a face "real" at all (filters
# tiny / blurry detections). Faces below this are dropped entirely.
MIN_DET_CONFIDENCE = 0.55

# "Not a person" rejection memory. When the user marks a detected face
# as a false positive, its embedding is stored in
# `rejected_face_embeddings` (see backend/api/people.py + migration
# 0051). Before persisting a NEW face we look up the nearest rejected
# embedding for this user; if cosine similarity to it is >= this
# threshold we DROP the detection instead of re-creating the bogus face
# on every scan. Set in the same neighbourhood as
# SAME_PERSON_THRESHOLD_CENTROID (0.52) but deliberately a touch lower
# (0.50) so a slightly-different angle of the SAME false positive (a
# mask / poster / pattern) still gets suppressed — while staying well
# above the different-person noise floor (~0.30) so we don't suppress a
# real, distinct face that merely sits near a rejected one. Tune UP if a
# real face ever gets wrongly suppressed; tune DOWN if a false positive
# keeps coming back across re-scans.
REJECTED_FACE_THRESHOLD = 0.50

# An ArcFace embedding is L2-normalized to unit length, so a *real*
# embedding has norm ~1.0. The mediapipe-cascade and manual-box paths
# store a placeholder all-zeros vector (norm 0) on faces that have no
# real embedding. Such vectors must NEVER participate in centroid
# matching (cosine distance to a zero vector is undefined and pgvector
# sorts it unpredictably) and must NEVER be averaged into a named
# person's centroid (a zero row drags the centroid toward the origin and
# corrupts every subsequent match). This floor distinguishes a real
# embedding from a placeholder one.
_MIN_EMBEDDING_NORM = 0.5


def _is_real_embedding(embedding) -> bool:
    """True only for a genuine (non-placeholder, non-empty) ArcFace vector.

    Guards every auto-attach / centroid path. An empty list (mediapipe
    box with no embedding) or an all-zeros 512-d placeholder both return
    False, so neither can ever be matched against a named person or
    folded into a person centroid — they can only ever become an
    unlabeled face the user names by hand.
    """
    if embedding is None:
        return False
    arr = np.asarray(embedding, dtype=np.float32)
    if arr.size == 0:
        return False
    return bool(np.linalg.norm(arr) >= _MIN_EMBEDDING_NORM)


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
    """Nearest-named-person match by centroid distance.

    Single, conservative acceptance path: the closest named-person
    centroid must clear `SAME_PERSON_THRESHOLD_CENTROID` (0.52 cosine
    similarity). If nothing clears it, we return None and the caller
    forms / grows an UNLABELED cluster instead of guessing.

    The previous "low-confidence clear-winner" path (accept at sim 0.18
    if it beats the runner-up) was removed — it was the main cause of
    wrong-person merges. A face the model can't confidently attribute to
    a known person is better left for the user to confirm with one click
    than silently merged into the wrong identity.

    Callers MUST pre-filter to real embeddings (`_is_real_embedding`)
    before calling this — a placeholder zero vector has an undefined
    cosine distance and would sort unpredictably against centroids.
    """
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
    best_person, best_dist = row[0], float(row[1])
    best_sim = 1.0 - best_dist
    if best_sim >= SAME_PERSON_THRESHOLD_CENTROID:
        return best_person
    logger.debug(
        "match_named: no confident centroid match (best=%s sim=%.3f < %.2f) "
        "-> leaving as unlabeled cluster",
        best_person.display_name, best_sim, SAME_PERSON_THRESHOLD_CENTROID,
    )
    return None


async def _match_individual_face(
    session: AsyncSession,
    user_id,
    embedding: list[float],
) -> Optional[Face]:
    """Stage 2: nearest individual face row (handles unnamed clusters).

    Excludes placeholder rows: a Face whose embedding is the all-zeros
    placeholder (mediapipe / manual-box faces with no real ArcFace
    vector) has an undefined cosine distance and must not be a match
    target — otherwise a brand-new real embedding could "match" a
    placeholder and inherit its (usually None) person/cluster. We filter
    them out at the SQL level via the L2-norm guard.
    """
    distance = Face.embedding.cosine_distance(embedding)
    where_clauses = [Face.user_id == user_id]
    if _Vector is not None:
        # Real embeddings only — exclude the all-zeros placeholder rows.
        # `type_coerce` binds the list through pgvector's Vector type so
        # asyncpg serializes it as a vector literal (a plain list bind
        # raises a DataError).
        where_clauses.append(
            Face.embedding != type_coerce(_PLACEHOLDER_EMBEDDING, _Vector(512))
        )
    stmt = (
        select(Face, distance.label("distance"))
        .where(*where_clauses)
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


async def _is_rejected_embedding(
    session: AsyncSession,
    user_id,
    embedding: list[float],
) -> bool:
    """True if `embedding` matches one this user marked "not a person".

    Nearest-rejected-embedding lookup: the closest row in
    `rejected_face_embeddings` for this user must be within
    `REJECTED_FACE_THRESHOLD` cosine similarity for the new detection to
    count as a known false positive (and therefore be dropped instead of
    re-created). Uses the HNSW cosine index (one indexed nearest-neighbour
    query, LIMIT 1 — not a Python scan over all rejections).

    FAIL-OPEN by contract: this guards face *scanning*, which must never
    break because of the rejection feature. Any error here (table not yet
    migrated, query failure, malformed embedding, …) is logged and
    returns False so normal detection proceeds. Callers MUST pre-filter to
    real embeddings (`_is_real_embedding`) — a placeholder zero vector has
    an undefined cosine distance and the rejected rows are always real
    ArcFace vectors, so there's nothing meaningful to compare.
    """
    try:
        distance = RejectedFaceEmbedding.embedding.cosine_distance(embedding)
        stmt = (
            select(distance.label("distance"))
            .where(RejectedFaceEmbedding.user_id == user_id)
            .order_by(distance.asc())
            .limit(1)
        )
        nearest = (await session.execute(stmt)).first()
        if nearest is None:
            return False
        sim = 1.0 - float(nearest[0])
        if sim >= REJECTED_FACE_THRESHOLD:
            logger.info(
                "faces: suppressing detection — matches a 'not a person' "
                "rejection (sim=%.3f >= %.2f) for user %s",
                sim, REJECTED_FACE_THRESHOLD, user_id,
            )
            return True
        return False
    except Exception:
        # Fail-open: never let the rejection lookup break face scanning.
        logger.exception(
            "faces: rejected-embedding lookup failed (proceeding with "
            "normal detection) for user %s", user_id,
        )
        return False


async def _next_cluster_id(session: AsyncSession, user_id) -> int:
    res = await session.execute(
        select(func.coalesce(func.max(Face.cluster_id), 0)).where(Face.user_id == user_id)
    )
    return int(res.scalar() or 0) + 1


async def update_person_centroid(
    session: AsyncSession, user_id, person_id: int
) -> int:
    """Recompute the centroid for one named person from all their face
    embeddings. Returns the count of REAL faces averaged. Centroid is
    L2-normalized so cosine similarity is consistent with ArcFace's
    unit-norm embeddings.

    Placeholder (all-zeros) embeddings are excluded from the average:
    a manual-box / mediapipe face the user later assigns to this person
    must not drag the centroid toward the origin (which would corrupt
    every subsequent centroid match). `face_count` therefore counts only
    the faces that actually contribute to the identity — which is also
    the right number for the `_match_named_person` `face_count > 0` gate
    (a person with ONLY placeholder faces has no usable centroid, so it
    correctly stays out of auto-matching until a real face lands).
    """
    rows = (
        await session.execute(
            select(Face.embedding).where(
                Face.user_id == user_id, Face.person_id == person_id
            )
        )
    ).scalars().all()
    real = [r for r in rows if _is_real_embedding(r)]
    if not real:
        # Either no faces at all, or only placeholder faces. Clear the
        # centroid and zero the count so this person can't be an
        # auto-match target until a real embedding is attached. Note the
        # person row itself survives (the user's chosen name is kept);
        # the People listing already hides 0-photo persons.
        await session.execute(
            update(Person)
            .where(Person.id == person_id)
            .values(centroid_embedding=None, face_count=0)
        )
        return 0
    arr = np.asarray(real, dtype=np.float32)
    centroid = arr.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    await session.execute(
        update(Person)
        .where(Person.id == person_id)
        .values(
            centroid_embedding=centroid.tolist(),
            face_count=len(real),
            updated_at=datetime.now(timezone.utc),
        )
    )
    return len(real)


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
        # "Not a person" suppression (the user-trained memory). If this
        # detection's embedding matches one the user previously marked as
        # a false positive, DROP it — don't re-create the bogus face on
        # this (re-)scan. Only real ArcFace vectors are checked: the
        # rejected rows are always real embeddings, and a placeholder
        # zero vector has no meaningful cosine distance. The lookup is
        # fail-open (see `_is_rejected_embedding`), so a missing table or
        # query error never blocks scanning.
        if _is_real_embedding(det.embedding) and await _is_rejected_embedding(
            session, user.id, det.embedding
        ):
            continue
        if not _is_real_embedding(det.embedding):
            # Mediapipe-cascade detections (and any future no-embedding
            # path) carry no real ArcFace vector — we still want a Face
            # row so the user can label it, but it stores the all-zeros
            # placeholder and is EXCLUDED from every auto-match / centroid
            # path. It can only ever become an unlabeled face the user
            # names by hand; it never auto-merges into a named person and
            # is never averaged into a person centroid. Guarding on
            # `_is_real_embedding` (not just `not det.embedding`) also
            # rejects a degenerate all-zeros non-empty vector.
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

        # Stage 1: try a named-person centroid match — but ONLY for a
        # high-confidence detection. A blurry / tiny / heavily-cropped
        # detection (confidence in [MIN_DET_CONFIDENCE,
        # MIN_DET_CONFIDENCE_NAMED_ATTACH)) yields a noisy embedding that
        # is exactly what slips past the centroid bar into the WRONG
        # person, so we skip the named-person attach for it and let it
        # grow an unlabeled cluster instead (Stage 2). High-confidence
        # detections take the centroid path, which averages per-person
        # variance across angles/lighting and is reliable once a person
        # has a couple of named faces.
        person_match = None
        if det.detection_confidence >= MIN_DET_CONFIDENCE_NAMED_ATTACH:
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
                # A Stage-2 match may point at a face that already belongs
                # to a named person. Only INHERIT that person_id (and thus
                # feed the person's centroid) when this detection is itself
                # high-confidence — same gate as Stage 1. A borderline
                # detection still joins the matched cluster, but stays
                # UNLABELED so it can't drag a named centroid. The user
                # confirming the cluster promotes the whole group at once.
                inherit_person_id = face_match.person_id
                if (
                    inherit_person_id is not None
                    and det.detection_confidence < MIN_DET_CONFIDENCE_NAMED_ATTACH
                ):
                    inherit_person_id = None
                face_row = Face(
                    user_id=user.id,
                    embedding=det.embedding,
                    person_id=inherit_person_id,
                    cluster_id=face_match.cluster_id,
                    quality_score=det.detection_confidence,
                )
                session.add(face_row)
                await session.flush()
                if inherit_person_id is not None:
                    persons_to_recentroid.add(inherit_person_id)
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
