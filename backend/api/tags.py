"""§C1.6 — user-owned tags + image/folder attach API.

The tag system unifies what used to be three separate surfaces:

  - `images.status` / `images.status_color`  — project status pill
  - `folders.status` / `folders.status_color` — same on folders
  - CLIP-suggested tags from the AI vision pipeline

All three now flow through one `tags` table (per-user, optionally
colored). The legacy `status` columns survive for one release as a
read-only shim so unmigrated FE clients still render — see migration
0028 backfill. Going forward the FE uses these endpoints:

  GET    /tags/                         list current user's tags
  POST   /tags/                         create a tag {label, color?}
  PATCH  /tags/{id}                     rename + recolor
  DELETE /tags/{id}                     delete (cascades image/folder links)
  POST   /images/{id}/tags              attach existing tag id or
                                        create+attach by label
  DELETE /images/{id}/tags/{tag_id}     detach
  POST   /folders/{id}/tags             attach to folder
  DELETE /folders/{id}/tags/{tag_id}    detach

Every endpoint is owner-scoped; the `tags`/`image_tags`/`folder_tags`
tables ship with FORCE'd Row-Level Security (migration 0028) so even
a SQL injection elsewhere can't cross-read.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import (
    Folder,
    FolderTag,
    Image,
    ImageTag,
    Tag,
    User,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tags", tags=["tags"])


# Subset of CSS named colors we accept — the FE chip rendering only
# uses these tone keys to pick a tint from the theme. An attacker
# can't smuggle arbitrary CSS via the color field (no `url(…)`, no
# `expression(…)`) because we'd reject it here.
ALLOWED_TAG_COLORS = {
    "gray", "red", "orange", "amber", "yellow", "lime", "green",
    "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet",
    "purple", "fuchsia", "pink", "rose",
}

# 64 chars is plenty for a label ("Important", "Q3 review", etc.) and
# bounds the storage cost of a `LIKE '%…%'` lookup. The trim happens
# server-side so the FE can be sloppy.
TAG_LABEL_MAX = 64


class TagRead(BaseModel):
    """API surface for a Tag. `image_count` / `folder_count` are
    populated by `list_tags` so the FE can render "Important · 12"
    without N+1 queries."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    color: Optional[str] = None
    source: str = "user"
    image_count: int = 0
    folder_count: int = 0


class TagCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=TAG_LABEL_MAX)
    color: Optional[str] = None


class TagUpdate(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=TAG_LABEL_MAX)
    color: Optional[str] = None  # null clears the chip tint


class TagAttachBody(BaseModel):
    """One of `tag_id` or `label` is required. `label` creates the tag
    on demand when no existing tag with that case-folded label belongs
    to the user — useful for the FE's "type to filter / Enter to
    create" combobox flow."""

    tag_id: Optional[int] = None
    label: Optional[str] = Field(None, min_length=1, max_length=TAG_LABEL_MAX)
    color: Optional[str] = None  # only consulted when creating on the fly


def _validate_color(color: Optional[str]) -> Optional[str]:
    if color is None or color == "":
        return None
    color = color.strip().lower()
    if color not in ALLOWED_TAG_COLORS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"color must be one of {sorted(ALLOWED_TAG_COLORS)} or null.",
        )
    return color


def _normalize_label(label: str) -> str:
    """Trim + collapse internal whitespace. Empty after trim is
    rejected by the caller (Pydantic's `min_length=1`)."""
    return " ".join(label.split())


async def _get_or_create_tag(
    session: AsyncSession,
    *,
    user_id: UUID,
    label: str,
    color: Optional[str],
) -> Tag:
    """Idempotent get-or-create against the case-folded uniqueness
    index. Returns the existing tag if a same-cased label exists, else
    creates a new one with the supplied color."""
    label = _normalize_label(label)
    if not label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tag label can't be empty.")
    if len(label) > TAG_LABEL_MAX:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Tag label is too long (max {TAG_LABEL_MAX} chars).",
        )
    existing = (
        await session.execute(
            select(Tag).where(
                Tag.user_id == user_id,
                func.lower(Tag.label) == label.casefold(),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    tag = Tag(
        user_id=user_id,
        label=label,
        source="user",
        color=_validate_color(color),
    )
    session.add(tag)
    try:
        await session.flush()
    except IntegrityError:
        # Raced with another request to create the same label — pull
        # the now-existing row and use that.
        await session.rollback()
        existing = (
            await session.execute(
                select(Tag).where(
                    Tag.user_id == user_id,
                    func.lower(Tag.label) == label.casefold(),
                )
            )
        ).scalar_one()
        return existing
    return tag


async def _load_owned_tag(
    session: AsyncSession, *, user_id: UUID, tag_id: int,
) -> Tag:
    tag = await session.get(Tag, tag_id)
    if tag is None or tag.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag not found")
    return tag


# ---------------------------------------------------------------- tag CRUD


@router.get("/", response_model=list[TagRead])
async def list_tags(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[TagRead]:
    """Every tag owned by this user, plus the count of images +
    folders carrying it. Used for the chip picker + the filter strip.
    Sorted by label for stable rendering."""
    rows = (
        await session.execute(
            select(Tag).where(Tag.user_id == user.id).order_by(func.lower(Tag.label))
        )
    ).scalars().all()
    if not rows:
        return []
    tag_ids = [t.id for t in rows]
    image_counts = dict(
        (
            await session.execute(
                select(ImageTag.tag_id, func.count())
                .where(ImageTag.user_id == user.id, ImageTag.tag_id.in_(tag_ids))
                .group_by(ImageTag.tag_id)
            )
        ).all()
    )
    folder_counts = dict(
        (
            await session.execute(
                select(FolderTag.tag_id, func.count())
                .where(FolderTag.user_id == user.id, FolderTag.tag_id.in_(tag_ids))
                .group_by(FolderTag.tag_id)
            )
        ).all()
    )
    return [
        TagRead(
            id=t.id, label=t.label, color=t.color, source=t.source,
            image_count=int(image_counts.get(t.id, 0)),
            folder_count=int(folder_counts.get(t.id, 0)),
        )
        for t in rows
    ]


@router.post("/", response_model=TagRead, status_code=status.HTTP_201_CREATED)
async def create_tag(
    body: TagCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TagRead:
    color = _validate_color(body.color)
    tag = await _get_or_create_tag(
        session, user_id=user.id, label=body.label, color=color,
    )
    await add_audit(
        session,
        user_id=user.id,
        action="tag.create",
        details={"tag_id": tag.id, "label": tag.label, "color": tag.color},
    )
    await session.commit()
    return TagRead(id=tag.id, label=tag.label, color=tag.color, source=tag.source)


@router.patch("/{tag_id}", response_model=TagRead)
async def update_tag(
    tag_id: int,
    body: TagUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TagRead:
    tag = await _load_owned_tag(session, user_id=user.id, tag_id=tag_id)
    changed: dict = {}
    if body.label is not None:
        label = _normalize_label(body.label)
        if not label:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tag label can't be empty.")
        if label.casefold() != tag.label.casefold():
            # Case-fold collision with another tag of theirs?
            clash = (
                await session.execute(
                    select(Tag.id).where(
                        Tag.user_id == user.id,
                        func.lower(Tag.label) == label.casefold(),
                        Tag.id != tag.id,
                    )
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "You already have a tag with that label.",
                )
        changed["label"] = label
        tag.label = label
    if body.color is not None or "color" in body.model_fields_set:
        # body.color may be `None` to explicitly clear the chip tint.
        new_color = _validate_color(body.color)
        changed["color"] = new_color
        tag.color = new_color
    if changed:
        tag.updated_at = func.now()
        await add_audit(
            session,
            user_id=user.id,
            action="tag.update",
            details={"tag_id": tag.id, **changed},
        )
    await session.commit()
    await session.refresh(tag)
    return TagRead(id=tag.id, label=tag.label, color=tag.color, source=tag.source)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    tag = await _load_owned_tag(session, user_id=user.id, tag_id=tag_id)
    label = tag.label
    await session.delete(tag)  # FK CASCADE drops image_tags + folder_tags
    await add_audit(
        session, user_id=user.id, action="tag.delete",
        details={"tag_id": tag_id, "label": label},
    )
    await session.commit()
    return None


# --------------------------------------------------------- image attach/detach


image_attach_router = APIRouter(prefix="/images", tags=["tags"])


@image_attach_router.post(
    "/{image_id}/tags",
    response_model=TagRead,
    status_code=status.HTTP_201_CREATED,
)
async def attach_image_tag(
    image_id: UUID,
    body: TagAttachBody,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TagRead:
    """Attach a tag to an image. Pass `tag_id` for an existing tag, or
    `label` (with optional `color`) to create-and-attach in one round
    trip. Idempotent — re-attaching the same tag returns 201 with the
    existing link."""
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")

    if body.tag_id is not None:
        tag = await _load_owned_tag(session, user_id=user.id, tag_id=body.tag_id)
    elif body.label:
        tag = await _get_or_create_tag(
            session, user_id=user.id, label=body.label, color=body.color,
        )
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide tag_id or label to attach a tag.",
        )

    existing = await session.get(ImageTag, (image.id, tag.id))
    if existing is None:
        session.add(ImageTag(image_id=image.id, tag_id=tag.id, user_id=user.id))
        await add_audit(
            session, user_id=user.id, action="tag.attach.image",
            details={"image_id": str(image.id), "tag_id": tag.id, "label": tag.label},
        )
        await session.commit()
    return TagRead(id=tag.id, label=tag.label, color=tag.color, source=tag.source)


@image_attach_router.delete(
    "/{image_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def detach_image_tag(
    image_id: UUID,
    tag_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    image = (
        await session.execute(
            select(Image.id).where(
                Image.id == image_id,
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    res = await session.execute(
        sa_delete(ImageTag).where(
            ImageTag.image_id == image_id,
            ImageTag.tag_id == tag_id,
            ImageTag.user_id == user.id,
        )
    )
    if res.rowcount:
        await add_audit(
            session, user_id=user.id, action="tag.detach.image",
            details={"image_id": str(image_id), "tag_id": tag_id},
        )
    await session.commit()
    return None


# -------------------------------------------------------- folder attach/detach


folder_attach_router = APIRouter(prefix="/folders", tags=["tags"])


@folder_attach_router.post(
    "/{folder_id}/tags",
    response_model=TagRead,
    status_code=status.HTTP_201_CREATED,
)
async def attach_folder_tag(
    folder_id: UUID,
    body: TagAttachBody,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TagRead:
    folder = (
        await session.execute(
            select(Folder).where(
                Folder.id == folder_id,
                Folder.user_id == user.id,
                Folder.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")

    if body.tag_id is not None:
        tag = await _load_owned_tag(session, user_id=user.id, tag_id=body.tag_id)
    elif body.label:
        tag = await _get_or_create_tag(
            session, user_id=user.id, label=body.label, color=body.color,
        )
    else:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide tag_id or label to attach a tag.",
        )

    existing = await session.get(FolderTag, (folder.id, tag.id))
    if existing is None:
        session.add(FolderTag(folder_id=folder.id, tag_id=tag.id, user_id=user.id))
        await add_audit(
            session, user_id=user.id, action="tag.attach.folder",
            details={"folder_id": str(folder.id), "tag_id": tag.id, "label": tag.label},
        )
        await session.commit()
    return TagRead(id=tag.id, label=tag.label, color=tag.color, source=tag.source)


@folder_attach_router.delete(
    "/{folder_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def detach_folder_tag(
    folder_id: UUID,
    tag_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    folder = (
        await session.execute(
            select(Folder.id).where(
                Folder.id == folder_id,
                Folder.user_id == user.id,
                Folder.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found")
    res = await session.execute(
        sa_delete(FolderTag).where(
            FolderTag.folder_id == folder_id,
            FolderTag.tag_id == tag_id,
            FolderTag.user_id == user.id,
        )
    )
    if res.rowcount:
        await add_audit(
            session, user_id=user.id, action="tag.detach.folder",
            details={"folder_id": str(folder_id), "tag_id": tag_id},
        )
    await session.commit()
    return None
