"""Per-face endpoints — fetch the JPEG crop so the frontend can show thumbnails."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Face, FaceDetection, Person, User
from backend.storage import storage

router = APIRouter(prefix="/faces", tags=["faces"])


@router.get("/{face_id}/crop")
async def face_crop(
    face_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    # Verify ownership and find a detection that carries the crop blob.
    own = (
        await session.execute(select(Face).where(Face.id == face_id, Face.user_id == user.id))
    ).scalar_one_or_none()
    if own is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Face not found")

    det = (
        await session.execute(
            select(FaceDetection)
            .where(
                FaceDetection.user_id == user.id,
                FaceDetection.face_id == face_id,
                FaceDetection.crop_blob_key.is_not(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if det is None or det.crop_blob_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No crop available")

    try:
        blob = storage.get(storage.bucket_faces, det.crop_blob_key)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Crop missing")
    return Response(
        content=blob,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


class DetectionReassignBody(BaseModel):
    """Body for `POST /faces/detections/{id}/reassign`.

    Provide exactly one:
      - `person_id` — re-target the detection to an existing Person.
      - `display_name` — create a new Person with this name and re-target.

    Pass `person_id=null` together with no `display_name` to *unlink*
    the detection (clear the label).
    """
    person_id: Optional[int] = None
    display_name: Optional[str] = Field(default=None, max_length=120)


@router.post("/detections/{detection_id}/reassign")
async def reassign_detection(
    detection_id: int,
    body: DetectionReassignBody,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Re-label a single face detection without touching the rest of the
    cluster.

    Why this exists: the old "rename person" flow renamed every face the
    clusterer grouped together — fine when the clusterer was right, a
    disaster when one person's photo got misgrouped with another's. The
    user would correct the label and find every photo of the *correct*
    person silently re-labeled with the wrong name.

    Implementation: rather than mutating the shared Face row's
    person_id (which fans out to every detection that points at it),
    we clone the Face row — same embedding, same quality_score — and
    point THIS detection at the new Face. Detections that share the
    embedding stay attached to the original Face, untouched.
    """
    det = (
        await session.execute(
            select(FaceDetection).where(
                FaceDetection.id == detection_id,
                FaceDetection.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if det is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Detection not found")
    if det.face_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This detection isn't linked to a Face row yet — re-run face scan first.",
        )

    # Resolve the target Person — existing id, new name, or unlink.
    target_person_id: Optional[int] = None
    if body.display_name and body.display_name.strip():
        name = body.display_name.strip()
        # Reuse an existing Person with that exact name if there is one
        # (avoids duplicate "Alice" rows from rapid relabels).
        existing = (
            await session.execute(
                select(Person).where(
                    Person.user_id == user.id, Person.display_name == name
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            target_person_id = existing.id
        else:
            person = Person(user_id=user.id, display_name=name)
            session.add(person)
            await session.flush()
            target_person_id = person.id
    elif body.person_id is not None:
        # Verify the target person belongs to this user.
        target = (
            await session.execute(
                select(Person).where(
                    Person.id == body.person_id, Person.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Target person not found")
        target_person_id = target.id

    # Fetch the original Face so we can copy its embedding into a new
    # row dedicated to this detection.
    orig_face = (
        await session.execute(
            select(Face).where(Face.id == det.face_id, Face.user_id == user.id)
        )
    ).scalar_one_or_none()
    if orig_face is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Original face row not found"
        )

    new_face = Face(
        user_id=user.id,
        embedding=orig_face.embedding,
        quality_score=orig_face.quality_score,
        cluster_id=None,  # explicit relabel removes it from the cluster
        person_id=target_person_id,
    )
    session.add(new_face)
    await session.flush()
    det.face_id = new_face.id

    # Refresh the target person's centroid so future detections cluster
    # to this corrected label without another manual relabel.
    if target_person_id is not None:
        try:
            from backend.faces_pipeline import update_person_centroid
            await update_person_centroid(session, user.id, target_person_id)
        except Exception:
            pass

    await add_audit(
        session,
        user_id=user.id,
        action="people.reassign_detection",
        details={
            "detection_id": detection_id,
            "old_face_id": orig_face.id,
            "new_face_id": new_face.id,
            "person_id": target_person_id,
            "display_name": body.display_name,
        },
    )
    await session.commit()

    return {
        "detection_id": detection_id,
        "face_id": new_face.id,
        "person_id": target_person_id,
    }
