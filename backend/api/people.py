"""People + face cluster endpoints (Phase 4).

  GET    /people/                       — named persons + unlabeled clusters
  POST   /people/clusters/{cluster_id}  — name an unlabeled cluster
  DELETE /people/clusters/{cluster_id}  — delete an unnamed (junk) cluster
  PATCH  /people/faces/{face_id}        — reassign / unassign one face
  PATCH  /people/{person_id}            — rename a person
  DELETE /people/{person_id}            — delete a person + their face rows
  POST   /people/backfill               — run Pass B on pending images for this user
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text, update
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import current_active_user
from backend.consent import is_consent_active, is_scope_active
from backend.db import SessionLocal, get_session
from backend.faces_pipeline import (
    _is_real_embedding,
    _next_cluster_id,
    process_image_for_faces,
    update_person_centroid,
)
from backend.image import fetch_original
from backend.models import (
    AuditLog,
    Face,
    FaceDetection,
    Image,
    ImagePerson,
    Person,
    RejectedFaceEmbedding,
    User,
)
from backend.storage import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/people", tags=["people"])


# §C4.2 — "Me" -> the user's actual display name. When the user labels
# a face cluster (or person) as the literal token "Me", we substitute
# their account display name so AI summaries say "Jakub on the beach"
# instead of "Me on the beach." If the account has no display name
# yet, we return a structured 422 the FE can catch and prompt for one
# inline before retrying.
def resolve_self_name(user: User, raw: str) -> str:
    """Substitute the special token "Me" with the user's display name.

    Returns the resolved name. Raises HTTPException(422) with a
    structured detail when the user typed "Me" but has no display
    name set yet — the FE keys on `detail.code == "missing_display_name"`
    to surface the inline prompt.
    """
    stripped = (raw or "").strip()
    if stripped.lower() != "me":
        return stripped
    name = (user.display_name or "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "missing_display_name",
                "message": (
                    'Set a display name on your account first — AI summaries '
                    'will use it in place of "Me".'
                ),
            },
        )
    return name


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


class ReassignFacePayload(BaseModel):
    """Body for `PATCH /people/faces/{face_id}`.

    `person_id`:
      - `int`  — reassign this face to that (owned) person.
      - `null` — UNASSIGN: detach the face from its current person and
        return it to an unlabeled cluster of its own.

    The field is required so the intent is always explicit (we never
    guess between "unassign" and "no-op").
    """
    person_id: int | None = Field(
        ...,
        description="Target person id, or null to unassign the face.",
    )


class PersonStateRead(BaseModel):
    """Compact per-person state echoed back after a reassign so the FE can
    patch its cache without a full refetch. `deleted=True` means the
    person row was removed because it had no faces left."""
    id: int
    display_name: str | None = None
    face_count: int = 0
    deleted: bool = False


class ReassignFaceResponse(BaseModel):
    face_id: int
    person_id: int | None
    cluster_id: int | None
    # The old and new persons' post-recompute state (either may be null
    # when not applicable — e.g. unassigning a face that had no person).
    old_person: PersonStateRead | None = None
    new_person: PersonStateRead | None = None


class NotAPersonResponse(BaseModel):
    """Result of marking a cluster / face as "not a person".

    `rejected` is how many real face embeddings were recorded into the
    user's rejection memory (placeholder / no-embedding faces are deleted
    but contribute nothing to suppress, so they don't count here).
    `faces_deleted` is how many Face rows were removed. The underlying
    photos are never touched — only the face rows + grouping.
    """
    rejected: int
    faces_deleted: int


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

    # Per-person photo count. The previous shape was a correlated UNION
    # scalar subquery with `Person.id` referenced inside each UNION
    # branch — but SQLAlchemy's auto-correlation doesn't reach into
    # UNION components reliably, so each branch was selecting "all
    # faces of all persons" and the outer COUNT returned every image
    # in the library (every person card showed the same global total).
    # New shape: build ONE derived table mapping (image_id, person_id)
    # from both the face-detection and manual-tag paths, then run a
    # standard correlated COUNT(DISTINCT image_id) WHERE person_id
    # matches the outer Person row.
    # Join Image + require deleted_at IS NULL on BOTH paths so the count
    # only includes LIVE photos. Without this, a soft-deleted image still
    # counted toward a person's total while the person→images view (which
    # filters deleted rows) showed fewer — "count says 3, opens to 1".
    face_pairs = (
        select(
            FaceDetection.image_id.label("image_id"),
            Face.person_id.label("person_id"),
        )
        .join(Face, Face.id == FaceDetection.face_id)
        .join(Image, Image.id == FaceDetection.image_id)
        .where(
            FaceDetection.user_id == user.id,
            Face.person_id.is_not(None),
            Image.deleted_at.is_(None),
        )
    )
    manual_pairs = (
        select(
            _IP.image_id.label("image_id"),
            _IP.person_id.label("person_id"),
        )
        .join(Image, Image.id == _IP.image_id)
        .where(_IP.user_id == user.id, Image.deleted_at.is_(None))
    )
    person_images_sq = face_pairs.union(manual_pairs).subquery("person_images")
    photo_count_sq = (
        select(func.count(func.distinct(person_images_sq.c.image_id)))
        .where(person_images_sq.c.person_id == Person.id)
        .correlate(Person)
        .scalar_subquery()
    )

    p_stmt = (
        select(
            Person,
            best_face_sq.label("sample_face_id"),
            photo_count_sq.label("photo_count"),
        )
        # Hide empty persons. After a rename-merge or a re-detection a
        # person can be left with zero live photos; the user shouldn't see
        # a stale 0-photo name card ("old name stays even with 0 people"),
        # so require at least one non-deleted photo.
        .where(Person.user_id == user.id, photo_count_sq > 0)
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
    # §C4.2 — "Me" -> user.display_name. Raises 422 if no display name.
    name = resolve_self_name(user, name)

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


@router.delete(
    "/clusters/{cluster_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_cluster(
    cluster_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Delete an UNNAMED face cluster (junk detection: mask, cartoon
    face, false positive, …).

    Removes only this user's `Face` rows where
    `cluster_id == cluster_id AND person_id IS NULL` — i.e. an
    unlabeled cluster. Their `FaceDetection` rows + crop blobs are
    cleaned up alongside (the detections reference the face, so we
    delete them explicitly and drop the MinIO crop objects). A cluster
    that has been named (its faces carry a `person_id`) is NOT a
    candidate here: the `person_id IS NULL` filter means a named
    cluster matches zero rows and the route 404s, so the named-person
    delete path (`DELETE /{person_id}`) stays the only way to remove a
    named identity. Other users' clusters are invisible via the
    `user_id` filter and likewise 404.

    Declared with the literal `clusters/{cluster_id}` path + DELETE
    method, so it never collides with `POST /clusters/{cluster_id}`
    (name a cluster) or `DELETE /{person_id}` (delete a named person —
    that path's `{person_id}` would otherwise try to swallow a bare
    int, but the `clusters/` prefix segment wins resolution here).

    RLS-scoped to the caller; returns 204 on success, 404 when the
    cluster has no matching unnamed faces for this user.
    """
    # Target = this user's unnamed faces in the given cluster.
    face_ids = (
        await session.execute(
            select(Face.id).where(
                Face.user_id == user.id,
                Face.cluster_id == cluster_id,
                Face.person_id.is_(None),
            )
        )
    ).scalars().all()
    if not face_ids:
        # Nothing to delete — either the cluster doesn't exist, belongs
        # to another user, or has been named (its faces carry a
        # person_id). Treat all three as "not found" so this endpoint
        # can't be used to probe or delete named/foreign clusters.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cluster not found")

    # Crop blob keys to purge from object storage after the rows go.
    crops = (
        await session.execute(
            select(FaceDetection.crop_blob_key).where(
                FaceDetection.user_id == user.id,
                FaceDetection.crop_blob_key.is_not(None),
                FaceDetection.face_id.in_(face_ids),
            )
        )
    ).scalars().all()

    # Delete the detections first (they FK the faces), then the faces.
    fd_count = (
        await session.execute(
            delete(FaceDetection).where(
                FaceDetection.user_id == user.id,
                FaceDetection.face_id.in_(face_ids),
            )
        )
    ).rowcount or 0
    face_count = (
        await session.execute(
            delete(Face).where(
                Face.user_id == user.id,
                Face.id.in_(face_ids),
            )
        )
    ).rowcount or 0

    # Best-effort blob cleanup — mirrors delete_person; a missing
    # object must not fail the transaction.
    for key in crops:
        try:
            storage.delete(storage.bucket_faces, key)
        except Exception:
            pass

    session.add(
        AuditLog(
            user_id=user.id,
            action="people.delete_cluster",
            details={
                "cluster_id": cluster_id,
                "face_detections_deleted": int(fd_count),
                "faces_deleted": int(face_count),
                "crops_deleted": len(crops),
            },
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _record_rejected_embeddings(
    session: AsyncSession,
    user_id,
    face_ids: list[int],
    source_cluster_id: int | None,
) -> int:
    """Persist the REAL embeddings of `face_ids` into the user's
    "not a person" rejection memory (`rejected_face_embeddings`).

    Returns how many embeddings were recorded. Placeholder / no-embedding
    faces (the all-zeros mediapipe / manual-box vector) are skipped — a
    zero vector is useless as a suppression anchor (undefined cosine
    distance) and storing it would let it match arbitrary future faces.
    So a cluster made only of placeholder faces records nothing here and
    is simply deleted; the suppression memory needs a real embedding to
    anchor on. The caller deletes the face rows afterwards.
    """
    rows = (
        await session.execute(
            select(Face.embedding).where(
                Face.user_id == user_id,
                Face.id.in_(face_ids),
            )
        )
    ).scalars().all()
    recorded = 0
    for emb in rows:
        if not _is_real_embedding(emb):
            continue
        # `emb` comes back as a list[float] (pgvector → Python). Re-bind
        # it straight onto the ORM column; SQLAlchemy serializes it via
        # the Vector type on insert.
        session.add(
            RejectedFaceEmbedding(
                user_id=user_id,
                embedding=list(emb),
                source_cluster_id=source_cluster_id,
            )
        )
        recorded += 1
    return recorded


@router.post(
    "/clusters/{cluster_id}/not-a-person",
    response_model=NotAPersonResponse,
)
async def cluster_not_a_person(
    cluster_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotAPersonResponse:
    """Mark an UNNAMED face cluster as a false positive ("not a person").

    This is the primary "not a person" affordance (the People-tab hover
    control on an unlabeled cluster). It does two things, in order:

      1. Records each face's REAL ArcFace embedding into the user's
         `rejected_face_embeddings` memory, so the next face scan
         suppresses the same bogus detection instead of re-creating it.
      2. Deletes the cluster's Face rows (+ their FaceDetection rows and
         crop blobs) — exactly the same removal `DELETE /clusters/{id}`
         performs, so the junk detection disappears from the UI
         immediately.

    The underlying PHOTOS are never deleted — only the face rows /
    grouping. Like `delete_cluster`, this targets only this user's
    `Face` rows with `cluster_id == cluster_id AND person_id IS NULL`
    (an UNLABELED cluster); a named cluster matches zero rows and 404s,
    and other users' clusters are invisible via the `user_id` filter and
    likewise 404. RLS-scoped to the caller.

    Declared with the literal `clusters/{cluster_id}/not-a-person` path
    so it never collides with `POST /clusters/{cluster_id}` (name a
    cluster) or `DELETE /clusters/{cluster_id}`.
    """
    face_ids = (
        await session.execute(
            select(Face.id).where(
                Face.user_id == user.id,
                Face.cluster_id == cluster_id,
                Face.person_id.is_(None),
            )
        )
    ).scalars().all()
    if not face_ids:
        # Same not-found semantics as delete_cluster: unknown, foreign, or
        # already-named clusters all 404 (can't be probed via this route).
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cluster not found")

    # 1) Remember the rejection BEFORE deleting the faces.
    rejected = await _record_rejected_embeddings(
        session, user.id, list(face_ids), source_cluster_id=cluster_id
    )

    # 2) Delete the faces + detections + crops (mirrors delete_cluster).
    crops = (
        await session.execute(
            select(FaceDetection.crop_blob_key).where(
                FaceDetection.user_id == user.id,
                FaceDetection.crop_blob_key.is_not(None),
                FaceDetection.face_id.in_(face_ids),
            )
        )
    ).scalars().all()
    await session.execute(
        delete(FaceDetection).where(
            FaceDetection.user_id == user.id,
            FaceDetection.face_id.in_(face_ids),
        )
    )
    face_count = (
        await session.execute(
            delete(Face).where(
                Face.user_id == user.id,
                Face.id.in_(face_ids),
            )
        )
    ).rowcount or 0

    for key in crops:
        try:
            storage.delete(storage.bucket_faces, key)
        except Exception:
            pass

    session.add(
        AuditLog(
            user_id=user.id,
            action="people.cluster_not_a_person",
            details={
                "cluster_id": cluster_id,
                "rejected_embeddings": int(rejected),
                "faces_deleted": int(face_count),
                "crops_deleted": len(crops),
            },
        )
    )
    await session.commit()
    return NotAPersonResponse(
        rejected=int(rejected), faces_deleted=int(face_count)
    )


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

    Rate-limited to 10 calls per hour per user. The body is already
    capped at 50 image_ids per call (Pydantic Field max_length), so
    the effective ceiling is 500 detector runs per user per hour —
    plenty for the legitimate "label this batch as Me" flow without
    letting a script DOS the shared GPU thread.
    """
    if not await is_scope_active(session, user.id, "face_recognition"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Grant face recognition consent first (Settings → Privacy).",
        )
    from backend.security import enforce_rate_limit
    await enforce_rate_limit(
        key=f"ml:detect-and-label:{user.id}",
        limit=10,
        window_seconds=3600,
        detail="Too many bulk tag requests. Try again in a few minutes.",
    )

    name = body.display_name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "display_name is required")
    # §C4.2 — "Me" -> user.display_name. Raises 422 if no display name.
    name = resolve_self_name(user, name)

    # Audit D2 — ownership gate. Filter body.image_ids down to the
    # IDs the caller actually owns. The pre-D2 implementation
    # bulk-inserted ImagePerson rows on every body.image_id BEFORE
    # the per-image loop (lines below) checked ownership, so an
    # attacker could plant `(victim_image_id, attacker_person_id,
    # attacker_user_id)` rows. The FK only proves the image exists
    # somewhere, not that the caller owns it.
    #
    # We silently filter (rather than 404) to preserve the existing
    # "skip images that aren't yours" UX the per-image loop already
    # implements — switching to an all-or-nothing reject would
    # surprise legitimate callers and double as an existence-probe
    # oracle. The body cap of 50 image_ids upstream means one extra
    # SELECT per call.
    owned_image_ids = set(
        (
            await session.execute(
                select(Image.id).where(
                    Image.id.in_(body.image_ids),
                    Image.user_id == user.id,
                    Image.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    # Re-bind body.image_ids to the filtered list so every downstream
    # use of `body.image_ids` (pre_existing fetch, ImagePerson bulk
    # insert, per-image scan loop) only sees authorized ids.
    body.image_ids = [iid for iid in body.image_ids if iid in owned_image_ids]
    if not body.image_ids:
        # Nothing to do. Don't allocate a Person row for an attack
        # that submitted only foreign / non-existent ids.
        return {
            "person_id": None,
            "display_name": name,
            "images_scanned": 0,
            "new_faces": 0,
            "no_face_images": [],
        }

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


@router.patch("/faces/{face_id}", response_model=ReassignFaceResponse)
async def reassign_face(
    face_id: int,
    payload: ReassignFacePayload,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReassignFaceResponse:
    """Correct a single face's person assignment.

    The user opens a person, sees a face that isn't them (a wrong-person
    merge from clustering), and fixes it:

      - `{"person_id": <int>}` moves THIS face onto that person.
      - `{"person_id": null}` UNASSIGNS the face — it leaves its current
        person and becomes its own unlabeled cluster the user can rename
        later.

    Unlike the cluster-wide rename, this touches exactly one Face row, so
    correcting one bad face never disturbs the other faces in the cluster.

    Both the OLD person (losing the face) and the NEW person (gaining it)
    have their centroid + face_count recomputed via
    `update_person_centroid`, so future auto-matching reflects the
    correction immediately. A source person left with no faces AND no
    manual image tags is deleted. The whole operation is one transaction
    and idempotent (re-issuing the same target is a no-op recompute).

    Declared before `PATCH /{person_id}` so the literal `faces` segment
    wins route resolution (otherwise FastAPI would try to coerce "faces"
    into the int `person_id` and 422).
    """
    # RLS + defensive user_id filter, mirroring the other handlers.
    face = (
        await session.execute(
            select(Face).where(Face.id == face_id, Face.user_id == user.id)
        )
    ).scalar_one_or_none()
    if face is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Face not found")

    old_person_id = face.person_id
    new_person_id: int | None = payload.person_id

    # Resolve / validate the target person up-front.
    target_person: Person | None = None
    if new_person_id is not None:
        target_person = (
            await session.execute(
                select(Person).where(
                    Person.id == new_person_id, Person.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if target_person is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Target person not found")

    # --- Idempotency.
    if new_person_id == old_person_id:
        if new_person_id is None:
            # Already unassigned and asked to unassign again — true no-op.
            # Don't allocate a new cluster id or write an audit row.
            return ReassignFaceResponse(
                face_id=face.id,
                person_id=None,
                cluster_id=face.cluster_id,
                old_person=None,
                new_person=None,
            )
        # Already on the requested person. Recompute (cheap, keeps
        # face_count honest) and return without churning cluster ids or
        # writing a redundant audit "move".
        count = await update_person_centroid(session, user.id, new_person_id)
        await session.commit()
        return ReassignFaceResponse(
            face_id=face.id,
            person_id=new_person_id,
            cluster_id=face.cluster_id,
            old_person=None,
            new_person=PersonStateRead(
                id=new_person_id,
                display_name=target_person.display_name if target_person else None,
                face_count=count,
                deleted=False,
            ),
        )

    # --- Apply the move on the single Face row.
    if new_person_id is None:
        # Unassign: detach from the person and spin a fresh unlabeled
        # cluster so it surfaces as its own card the user can name. (We
        # don't reuse the old cluster id — the face was explicitly pulled
        # OUT of whatever grouping it was in.)
        face.person_id = None
        face.cluster_id = await _next_cluster_id(session, user.id)
    else:
        face.person_id = new_person_id
        # Adopt the target person's existing cluster id (if any) so the
        # face groups with the rest of that identity; fall back to a new
        # cluster id when the person has no clustered faces yet.
        sibling_cluster = (
            await session.execute(
                select(Face.cluster_id)
                .where(
                    Face.user_id == user.id,
                    Face.person_id == new_person_id,
                    Face.id != face.id,
                    Face.cluster_id.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        face.cluster_id = (
            int(sibling_cluster)
            if sibling_cluster is not None
            else await _next_cluster_id(session, user.id)
        )
    await session.flush()

    # --- Recompute BOTH affected persons' centroid + face_count.
    old_person_state: PersonStateRead | None = None
    if old_person_id is not None:
        old_count = await update_person_centroid(session, user.id, old_person_id)
        old_deleted = False
        if old_count == 0:
            # Empty of faces. Only delete the person row if it ALSO has no
            # manual image tags — otherwise we'd cascade-drop the user's
            # explicit ImagePerson tags. A person kept alive here with 0
            # faces is already hidden by the People listing's photo_count>0
            # filter, so this is safe either way.
            remaining_tags = (
                await session.execute(
                    select(func.count())
                    .select_from(ImagePerson)
                    .where(
                        ImagePerson.user_id == user.id,
                        ImagePerson.person_id == old_person_id,
                    )
                )
            ).scalar_one()
            if int(remaining_tags or 0) == 0:
                # Capture name before deletion for the response/audit.
                old_name_row = (
                    await session.execute(
                        select(Person.display_name).where(
                            Person.id == old_person_id, Person.user_id == user.id
                        )
                    )
                ).scalar_one_or_none()
                await session.execute(
                    delete(Person).where(
                        Person.id == old_person_id, Person.user_id == user.id
                    )
                )
                old_deleted = True
                old_person_state = PersonStateRead(
                    id=old_person_id,
                    display_name=old_name_row,
                    face_count=0,
                    deleted=True,
                )
        if not old_deleted:
            old_name = (
                await session.execute(
                    select(Person.display_name).where(Person.id == old_person_id)
                )
            ).scalar_one_or_none()
            old_person_state = PersonStateRead(
                id=old_person_id,
                display_name=old_name,
                face_count=old_count,
                deleted=False,
            )

    new_person_state: PersonStateRead | None = None
    if new_person_id is not None:
        new_count = await update_person_centroid(session, user.id, new_person_id)
        new_person_state = PersonStateRead(
            id=new_person_id,
            display_name=target_person.display_name if target_person else None,
            face_count=new_count,
            deleted=False,
        )

    await add_audit(
        session,
        user_id=user.id,
        action="people.reassign_face",
        details={
            "face_id": face.id,
            "old_person_id": old_person_id,
            "new_person_id": new_person_id,
            "old_person_deleted": bool(
                old_person_state.deleted if old_person_state else False
            ),
            "cluster_id": face.cluster_id,
        },
    )
    await session.commit()

    return ReassignFaceResponse(
        face_id=face.id,
        person_id=new_person_id,
        cluster_id=face.cluster_id,
        old_person=old_person_state,
        new_person=new_person_state,
    )


@router.post(
    "/faces/{face_id}/not-a-person",
    response_model=NotAPersonResponse,
)
async def face_not_a_person(
    face_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotAPersonResponse:
    """Mark a SINGLE face as a false positive ("not a person").

    The face-chip menu affordance: the user points at one specific
    detected face (which may sit in an unlabeled cluster OR be attached
    to a named person) and says "this isn't a person at all." We:

      1. Record that face's REAL embedding into the user's
         `rejected_face_embeddings` memory so the next scan suppresses it.
      2. Delete that one Face row (+ its FaceDetection rows and crops).
         If the face belonged to a named person, that person's centroid
         is recomputed afterwards (an emptied person is zeroed out, same
         as the reassign path) — but the person row and the user's chosen
         name are kept.

    The underlying PHOTO is never deleted — only the single face row.
    RLS + an explicit `user_id` filter scope this to the caller; an
    unknown or foreign face 404s.

    Declared with the literal `faces/{face_id}/not-a-person` path so it
    resolves ahead of `PATCH /{person_id}` and never collides with
    `PATCH /faces/{face_id}` (the reassign route is a different method).
    """
    face = (
        await session.execute(
            select(Face).where(Face.id == face_id, Face.user_id == user.id)
        )
    ).scalar_one_or_none()
    if face is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Face not found")

    owning_person_id = face.person_id

    # 1) Remember the rejection BEFORE deleting the face.
    rejected = await _record_rejected_embeddings(
        session, user.id, [face.id], source_cluster_id=face.cluster_id
    )

    # 2) Delete the single face + its detections + crops.
    crops = (
        await session.execute(
            select(FaceDetection.crop_blob_key).where(
                FaceDetection.user_id == user.id,
                FaceDetection.crop_blob_key.is_not(None),
                FaceDetection.face_id == face.id,
            )
        )
    ).scalars().all()
    await session.execute(
        delete(FaceDetection).where(
            FaceDetection.user_id == user.id,
            FaceDetection.face_id == face.id,
        )
    )
    face_count = (
        await session.execute(
            delete(Face).where(Face.user_id == user.id, Face.id == face.id)
        )
    ).rowcount or 0

    # If the face was attached to a named person, recompute that person's
    # centroid so it no longer reflects the removed (bogus) face. We keep
    # the person row + name even if it's now empty (the People listing
    # already hides 0-photo persons); this mirrors the reassign-unassign
    # behavior of not destroying a named identity from a single-face edit.
    if owning_person_id is not None:
        try:
            await update_person_centroid(session, user.id, owning_person_id)
        except Exception:
            logger.exception(
                "face_not_a_person: centroid update failed for person=%s",
                owning_person_id,
            )

    for key in crops:
        try:
            storage.delete(storage.bucket_faces, key)
        except Exception:
            pass

    session.add(
        AuditLog(
            user_id=user.id,
            action="people.face_not_a_person",
            details={
                "face_id": face_id,
                "person_id": owning_person_id,
                "rejected_embeddings": int(rejected),
                "faces_deleted": int(face_count),
                "crops_deleted": len(crops),
            },
        )
    )
    await session.commit()
    return NotAPersonResponse(
        rejected=int(rejected), faces_deleted=int(face_count)
    )


@router.patch("/{person_id}", response_model=PersonRead)
async def rename_person(
    person_id: int,
    payload: RenamePayload,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Person:
    """Rename a person — OR merge into an existing one.

    If the new name (case-insensitively) already belongs to ANOTHER of
    this user's persons, we treat the rename as a MERGE request: every
    Face (and, transitively, its FaceDetection rows) and every
    ImagePerson row owned by the renamed person is reassigned onto the
    existing target person, then the now-empty source person row is
    deleted. The whole thing runs in one transaction so a failure
    halfway through never leaves faces split across two persons.

    Why case-insensitive: the `persons_user_name_unique` constraint is
    case-SENSITIVE (a plain btree on `(user_id, display_name)`), so
    renaming "aidyn" -> "Aidyn" would otherwise create a second,
    visually-identical person instead of folding into the existing one.
    Matching on `lower(display_name)` makes "Aidyn" / "aidyn" / "AIDYN"
    all resolve to the same target.
    """
    person = (
        await session.execute(
            select(Person).where(Person.id == person_id, Person.user_id == user.id)
        )
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found")
    # §C4.2 — "Me" -> user.display_name. Raises 422 if no display name.
    new_name = resolve_self_name(user, payload.display_name)

    # Look for ANOTHER of this user's persons that already owns this name
    # (case-insensitive). Never match the person being renamed — a
    # case-only edit of their OWN name ("aidyn" -> "Aidyn") is a plain
    # rename, not a self-merge.
    target = (
        await session.execute(
            select(Person).where(
                Person.user_id == user.id,
                Person.id != person.id,
                func.lower(Person.display_name) == new_name.lower(),
            )
        )
    ).scalar_one_or_none()

    if target is None:
        # No collision — straightforward rename.
        person.display_name = new_name
        await session.commit()
        await session.refresh(person)
        return person

    # ----- MERGE: fold `person` (source) into `target` -----
    #
    # 1. ImagePerson rows. The PK is (image_id, person_id), so a photo
    #    that is ALREADY tagged with both the source AND the target
    #    person would collide on a blind UPDATE. Delete the source-side
    #    duplicates first (the target already covers those images), then
    #    re-point the rest.
    await session.execute(
        delete(ImagePerson).where(
            ImagePerson.user_id == user.id,
            ImagePerson.person_id == person.id,
            ImagePerson.image_id.in_(
                select(ImagePerson.image_id).where(
                    ImagePerson.user_id == user.id,
                    ImagePerson.person_id == target.id,
                )
            ),
        )
    )
    ip_moved = (
        await session.execute(
            sa_update(ImagePerson)
            .where(
                ImagePerson.user_id == user.id,
                ImagePerson.person_id == person.id,
            )
            .values(person_id=target.id)
        )
    ).rowcount or 0

    # 2. Face rows (carry their FaceDetection rows with them via
    #    FaceDetection.face_id -> Face). Re-point person_id; the
    #    detections need no change because they reference the face, not
    #    the person.
    faces_moved = (
        await session.execute(
            sa_update(Face)
            .where(Face.user_id == user.id, Face.person_id == person.id)
            .values(person_id=target.id)
        )
    ).rowcount or 0

    # 3. Delete the now-empty source person.
    await session.execute(
        delete(Person).where(Person.id == person.id, Person.user_id == user.id)
    )

    # 4. Recompute the surviving person's centroid from the combined set
    #    of faces so future detections cluster to the merged identity.
    await session.flush()
    try:
        await update_person_centroid(session, user.id, target.id)
    except Exception:
        logger.exception(
            "rename_person: centroid update failed after merge into %s",
            target.id,
        )

    session.add(
        AuditLog(
            user_id=user.id,
            action="people.merge_person",
            details={
                "source_person_id": person.id,
                "target_person_id": target.id,
                "faces_moved": int(faces_moved),
                "image_persons_moved": int(ip_moved),
                "display_name": new_name,
            },
        )
    )
    await session.commit()
    await session.refresh(target)
    return target


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
    # Audit D1 — cap on batch size. Re-crop = one storage.get +
    # one Pillow re-encode per detection; large batches monopolize
    # the worker pool. Operators wanting a full library re-crop
    # can run multiple batches.
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
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
