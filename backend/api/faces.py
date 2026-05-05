"""Per-face endpoints — fetch the JPEG crop so the frontend can show thumbnails."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Face, FaceDetection, User
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
