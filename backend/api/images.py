from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.image import fetch_original, fetch_served, store_upload
from backend.models import Image, ImageTag, Tag, User
from backend.schemas import ImageRead

router = APIRouter(prefix="/images", tags=["images"])


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
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File(...)],
) -> Image:
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")
    try:
        return await store_upload(session, user, file.filename, raw, file.content_type)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Could not decode image: {exc}",
        ) from exc


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
) -> list[Image]:
    stmt = select(Image).where(
        Image.user_id == user.id,
        Image.deleted_at.is_(None),
    )
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
    stmt = stmt.order_by(Image.uploaded_at.desc()).limit(limit).offset(offset)

    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{image_id}", response_model=ImageRead)
async def get_image(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Image:
    return await _load_owned_image(image_id, user, session)


@router.get("/{image_id}/original")
async def download_original(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
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
    image = await _load_owned_image(image_id, user, session)
    blob, mime = await fetch_served(image)
    return Response(content=blob, media_type=mime)


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    image = await _load_owned_image(image_id, user, session)
    from datetime import datetime, timezone

    image.deleted_at = datetime.now(timezone.utc)
    await session.commit()
