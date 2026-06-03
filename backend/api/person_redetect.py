"""Person re-detection on user signal — "Find more photos of this person."

Sprint I #7 / D8. A NEW, UNWIRED router. The user opens a named person,
clicks "Find more photos of this person," and we re-match that person's
face embedding(s) across their whole library to surface additional faces
that the auto-clustering never attached — presented as ranked candidates
the user confirms or rejects one by one (or all at once).

WIRING (the orchestrator does this — see the report, NOT this file):

    from backend.api.person_redetect import router as person_redetect_router
    app.include_router(person_redetect_router)

Endpoints (router prefix `/people`):

  POST /people/{person_id}/find-more
      Owner-scoped. 404 for an unknown / foreign / unnamed person id.
      Takes the person's representative face embeddings (their existing
      Face rows) and runs a pgvector cosine-KNN over the caller's
      UNASSIGNED faces (`person_id IS NULL`) to find ones that look like
      this person but were never auto-attached. Returns ranked candidate
      matches (one per source image — the best-scoring face in that
      image), each with the face id + detection id + image id + a face
      crop URL + a served image URL + the cosine similarity score.

      INSTANT path: this is a direct synchronous pgvector query against
      embeddings that already exist (every scanned face has an ArcFace
      vector), so the user gets candidates immediately — no worker job.
      Images that were never face-scanned at all (no embedding to match)
      are reported in `unscanned_count` and can be picked up by the
      existing People → "Scan / Backfill" flow; we do NOT block the
      instant result on them.

CONFIRM / REJECT — no new endpoint here.
  * Confirm  → reuse the existing  PATCH /people/faces/{face_id}
               with body {"person_id": <this person>}  (the owner-scoped,
               IDOR-safe `reassign_face` handler in backend/api/people.py;
               it also recomputes the person's centroid so the next scan
               clusters even better).
  * Reject   → reuse the existing  POST  /people/faces/{face_id}/not-a-person
               when the candidate isn't a real face at all, OR simply
               drop it from the review list client-side (a rejected
               candidate stays an unlabeled face; nothing to persist).

PRECISION over recall, by design. We ONLY consider faces that are
currently UNASSIGNED to any person. We never pull a face OFF another
named person (that would silently corrupt a different identity the user
already curated). The acceptance bar is a high cosine-similarity floor —
the same neighbourhood as the auto-attach centroid threshold — so what
surfaces is "almost certainly this person, just not auto-grouped."
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.faces_pipeline import (
    SAME_PERSON_THRESHOLD_CENTROID,
    SAME_PERSON_THRESHOLD_FACE,
    _is_real_embedding,
)
from backend.models import Face, FaceDetection, Image, Person, User

try:  # pgvector is only present where the [ml]/db extras are installed.
    from pgvector.sqlalchemy import Vector as _Vector
except Exception:  # pragma: no cover - keeps import working in bare envs
    _Vector = None

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/people", tags=["people"])

# The all-zeros placeholder vector stored on no-embedding faces
# (mediapipe-cascade / manual-box). Mirrors faces_pipeline so we exclude
# the same rows from KNN — a zero vector has an undefined cosine distance
# and would sort unpredictably.
_PLACEHOLDER_EMBEDDING: list[float] = [0.0] * 512

# Default acceptance floor (cosine similarity in [0,1]) for surfacing a
# candidate. Set to SAME_PERSON_THRESHOLD_FACE (0.50): a candidate's
# BEST match against any of this person's real face embeddings must clear
# it. This is the same bar the pipeline uses to call two individual faces
# the same person, so what surfaces here is consistent with "the model
# would have clustered these together." The user still confirms each, so
# a touch of recall is fine — but we keep it at the individual-face bar
# (not lower) so the grid isn't padded with different-person noise.
DEFAULT_MATCH_THRESHOLD = SAME_PERSON_THRESHOLD_FACE  # 0.50

# How many of the person's OWN faces to use as match anchors. We KNN each
# unassigned face against the person's real embeddings and keep the
# closest. Capping the anchor set keeps the query bounded for a person
# with hundreds of photos while still covering pose/lighting variety
# (the anchors are taken highest-quality-first).
MAX_ANCHORS = 12

# Hard cap on returned candidates so a crafted request can't fan out the
# KNN probe, and the review grid stays a sane size. One row per source
# image (deduped), best-scoring face per image.
MAX_CANDIDATES = 60


# ---------- schemas ----------

class RedetectCandidate(BaseModel):
    """One surfaced face the user can confirm onto the person.

    `face_id` is what the confirm call (PATCH /people/faces/{face_id})
    targets. `image_id` is the source photo; `detection_id` lets the FE
    draw the bbox if it wants. `similarity` is the cosine similarity to
    the person's nearest anchor face (0..1, higher = more confident).
    """
    face_id: int
    detection_id: int | None = None
    image_id: str
    cluster_id: int | None = None
    similarity: float
    bbox: list[int] | None = None
    detection_confidence: float | None = None


class RedetectResponse(BaseModel):
    person_id: int
    display_name: str | None
    threshold: float
    # How many real anchor embeddings the person had to match against. 0
    # means the person has no usable face embedding yet (only placeholder
    # / manual-tag faces) — the FE shows a "scan more photos first" hint.
    anchor_count: int
    # Images in the library that were never face-scanned (no embedding to
    # match against). Surfaced so the FE can nudge the user to run a
    # backfill/scan; the instant result does NOT wait on these.
    unscanned_count: int
    count: int
    candidates: list[RedetectCandidate]


# ---------- endpoint ----------

@router.post("/{person_id}/find-more", response_model=RedetectResponse)
async def find_more_photos(
    person_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    # Cap on candidates returned. Defaults to a comfortable review-grid
    # size; bounded so a crafted value can't widen the KNN fan-out.
    limit: Annotated[int, Query(ge=1, le=MAX_CANDIDATES)] = MAX_CANDIDATES,
    # Let the caller tighten the acceptance floor (never loosen it below
    # the individual-face bar — that's the different-person noise floor).
    # Range [SAME_PERSON_THRESHOLD_FACE, 0.95].
    threshold: Annotated[
        float, Query(ge=SAME_PERSON_THRESHOLD_FACE, le=0.95)
    ] = DEFAULT_MATCH_THRESHOLD,
) -> RedetectResponse:
    """Re-match a person across the library and return ranked candidates.

    Owner-scoped pgvector KNN against the caller's UNASSIGNED faces. Fast
    + synchronous: every scanned face already has an ArcFace embedding, so
    the user gets instant candidates. High-precision: only faces with no
    current person, scoring above `threshold` against the person's nearest
    anchor face, deduped to one (best) candidate per source image.
    """
    # --- Ownership gate. 404 (not 403) for missing / foreign so we don't
    # leak which person ids exist. An UNNAMED person (no display_name)
    # isn't a "find more" target — re-detection is about an identity the
    # user has already named.
    person = (
        await session.execute(
            select(Person).where(
                Person.id == person_id,
                Person.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found")

    # --- Pull this person's anchor embeddings (their REAL face vectors),
    # highest detection-confidence first so the anchors are the cleanest
    # crops we have. Placeholder (all-zeros) faces are skipped — a zero
    # vector can't anchor a cosine match.
    anchor_rows = (
        await session.execute(
            select(Face.id, Face.embedding)
            .outerjoin(FaceDetection, FaceDetection.face_id == Face.id)
            .where(
                Face.user_id == user.id,
                Face.person_id == person_id,
            )
            .order_by(
                FaceDetection.detection_confidence.desc().nullslast(),
                Face.id.desc(),
            )
        )
    ).all()
    # Dedupe by face id (the outer join multiplies a face by its
    # detections) while preserving the confidence ordering, then keep only
    # real embeddings up to MAX_ANCHORS.
    anchors: list[list[float]] = []
    seen_face_ids: set[int] = set()
    for fid, emb in anchor_rows:
        if fid in seen_face_ids:
            continue
        seen_face_ids.add(fid)
        if not _is_real_embedding(emb):
            continue
        anchors.append(list(emb))
        if len(anchors) >= MAX_ANCHORS:
            break

    # How many library images this user has that were never face-scanned
    # at all (still pending) — surfaced as a hint, never blocks the result.
    unscanned_count = int(
        (
            await session.execute(
                select(func.count(Image.id)).where(
                    Image.user_id == user.id,
                    Image.deleted_at.is_(None),
                    Image.pending_face_scan.is_(True),
                    Image.category == "image",
                )
            )
        ).scalar_one()
        or 0
    )

    if not anchors:
        # The person has no usable face embedding (only placeholder /
        # manual-tag faces). Nothing to match against — return an empty
        # set with the hint counts so the FE can guide the user.
        return RedetectResponse(
            person_id=person.id,
            display_name=person.display_name,
            threshold=threshold,
            anchor_count=0,
            unscanned_count=unscanned_count,
            count=0,
            candidates=[],
        )

    # --- KNN per anchor over the caller's UNASSIGNED real faces.
    #
    # For each anchor we ask pgvector for the nearest unassigned faces
    # (walks the HNSW cosine index on faces.embedding), then keep, per
    # face, its BEST similarity across all anchors. Running one bounded
    # query per anchor (LIMIT limit*2 each) keeps each probe index-served
    # and the total work O(anchors * limit) — cheap for the MAX_ANCHORS
    # (≤12) cap. We over-fetch (×2) per anchor so the cross-anchor merge +
    # per-image dedup still has enough rows to fill `limit` distinct
    # images after collisions.
    #
    # Owner-scoped on every branch (Face.user_id == user.id) and gated to
    # person_id IS NULL so we ONLY surface unassigned faces — a face that
    # already belongs to another named person is never pulled off it.
    best_by_face: dict[int, float] = {}
    per_anchor_fetch = min(limit * 2, MAX_CANDIDATES * 2)
    for anchor in anchors:
        where_clauses = [
            Face.user_id == user.id,
            Face.person_id.is_(None),
        ]
        if _Vector is not None:
            # Exclude the all-zeros placeholder rows. `type_coerce` binds
            # the list through pgvector's Vector type so asyncpg
            # serializes it as a vector literal (a plain list bind raises
            # a DataError). Same guard faces_pipeline uses.
            where_clauses.append(
                Face.embedding != type_coerce(_PLACEHOLDER_EMBEDDING, _Vector(512))
            )
        distance = Face.embedding.cosine_distance(anchor)
        rows = (
            await session.execute(
                select(Face.id, distance.label("distance"))
                .where(*where_clauses)
                .order_by(distance.asc())
                .limit(per_anchor_fetch)
            )
        ).all()
        for fid, dist in rows:
            sim = 1.0 - float(dist)
            if sim < threshold:
                # distance-ascending → similarity-descending; the first
                # miss for this anchor means every later row also misses.
                break
            prev = best_by_face.get(fid)
            if prev is None or sim > prev:
                best_by_face[fid] = sim

    if not best_by_face:
        return RedetectResponse(
            person_id=person.id,
            display_name=person.display_name,
            threshold=threshold,
            anchor_count=len(anchors),
            unscanned_count=unscanned_count,
            count=0,
            candidates=[],
        )

    # --- Resolve each matched face to its source image + detection.
    # One detection per face (the representative crop); skip faces whose
    # image is missing / soft-deleted. Dedupe to one candidate per source
    # IMAGE — keep the highest-similarity face in that image so the review
    # grid shows each photo once. Every join is owner-scoped.
    face_ids = list(best_by_face.keys())
    det_rows = (
        await session.execute(
            select(
                FaceDetection.face_id,
                FaceDetection.id,
                FaceDetection.image_id,
                FaceDetection.bbox_x,
                FaceDetection.bbox_y,
                FaceDetection.bbox_w,
                FaceDetection.bbox_h,
                FaceDetection.detection_confidence,
                Face.cluster_id,
            )
            .join(Face, Face.id == FaceDetection.face_id)
            .join(Image, Image.id == FaceDetection.image_id)
            .where(
                FaceDetection.user_id == user.id,
                FaceDetection.face_id.in_(face_ids),
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
            )
            # Highest-confidence detection first so the representative crop
            # per face is the cleanest one.
            .order_by(FaceDetection.detection_confidence.desc().nullslast())
        )
    ).all()

    # Best candidate per (face), then per (image).
    by_image: dict[str, RedetectCandidate] = {}
    seen_faces: set[int] = set()
    for (
        fid,
        det_id,
        image_id,
        bx,
        by,
        bw,
        bh,
        det_conf,
        cluster_id,
    ) in det_rows:
        if fid in seen_faces:
            continue  # already took this face's best detection
        seen_faces.add(fid)
        sim = best_by_face.get(fid)
        if sim is None:
            continue
        image_key = str(image_id)
        cand = RedetectCandidate(
            face_id=int(fid),
            detection_id=int(det_id) if det_id is not None else None,
            image_id=image_key,
            cluster_id=int(cluster_id) if cluster_id is not None else None,
            similarity=round(float(sim), 4),
            bbox=[int(bx), int(by), int(bw), int(bh)],
            detection_confidence=(
                float(det_conf) if det_conf is not None else None
            ),
        )
        existing = by_image.get(image_key)
        if existing is None or cand.similarity > existing.similarity:
            by_image[image_key] = cand

    candidates = sorted(
        by_image.values(), key=lambda c: c.similarity, reverse=True
    )[:limit]

    logger.info(
        "find_more_photos: person=%s anchors=%d threshold=%.2f -> %d "
        "candidate(s) for user %s",
        person_id, len(anchors), threshold, len(candidates), user.id,
    )

    return RedetectResponse(
        person_id=person.id,
        display_name=person.display_name,
        threshold=threshold,
        anchor_count=len(anchors),
        unscanned_count=unscanned_count,
        count=len(candidates),
        candidates=candidates,
    )
