"""§G2 — comments on any file.

  GET    /images/{image_id}/comments         — list (threaded)
  POST   /images/{image_id}/comments         — create
  PATCH  /comments/{comment_id}              — edit body (author only)
  DELETE /comments/{comment_id}              — soft delete
                                                (author OR image owner)

Access policy (enforced at the API layer because RLS isn't the right
shape for "row visible if a share grant exists"):

  - The image OWNER can read and write comments on their own files.
  - Any user with a CURRENTLY ACTIVE share grant for the image
    (revoked_at IS NULL AND not expired) can read and write too.
  - Editing a comment is restricted to its author.
  - Deleting a comment is allowed for the author OR the image
    owner (image owners can clean up their own threads).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Comment, Image, ShareGrant, User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["comments"])


# ---------- schemas ----------


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    image_id: UUID
    user_id: Optional[UUID]
    parent_id: Optional[int]
    body: str
    anchor_json: Optional[dict]
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]
    # Convenience fields the FE can render without a second round-trip:
    # author display name (or None if the account was deleted) so the
    # comment byline survives even after the user_id FK is nulled.
    author_display_name: Optional[str] = None
    author_email: Optional[str] = None


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    parent_id: Optional[int] = None
    # Schema-less anchor — see the Comment model docstring for the
    # shapes the FE writes today. Validation is intentionally light;
    # any JSON-serializable dict works.
    anchor_json: Optional[dict] = None


class CommentEdit(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


# ---------- access control ----------


async def _can_access_image(
    session: AsyncSession, image_id: UUID, user: User
) -> Image:
    """Return the Image if the user can read it (owner OR active
    share-grant recipient). Raise 404 if not — we don't tell the
    caller whether the image exists or whether they just can't see
    it, matching the rest of the codebase's enumeration-resistance
    posture."""
    img = (
        await session.execute(
            select(Image).where(
                Image.id == image_id,
                Image.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    if img.user_id == user.id:
        return img
    # Active share grant check. "Active" = not revoked + not expired.
    now = datetime.now(timezone.utc)
    grant = (
        await session.execute(
            select(ShareGrant.id).where(
                ShareGrant.image_id == image_id,
                ShareGrant.recipient_user_id == user.id,
                ShareGrant.revoked_at.is_(None),
                # expires_at IS NULL means "no explicit expiry" — keep.
                (ShareGrant.expires_at.is_(None))
                | (ShareGrant.expires_at > now),
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Image not found")
    return img


async def _attach_author_info(
    session: AsyncSession, rows: list[Comment]
) -> list[CommentRead]:
    """Hydrate `author_display_name` + `author_email` from User. Saves
    the FE a separate /users/{id} fetch per row. Comments whose author
    deleted their account end up with both fields None (the
    "former user" placeholder is rendered by the FE)."""
    user_ids = {r.user_id for r in rows if r.user_id is not None}
    by_id: dict = {}
    if user_ids:
        result = await session.execute(
            select(User.id, User.display_name, User.email).where(
                User.id.in_(user_ids)
            )
        )
        for uid, dn, email in result.all():
            by_id[uid] = (dn, email)

    out: list[CommentRead] = []
    for r in rows:
        author = by_id.get(r.user_id) if r.user_id else None
        out.append(
            CommentRead(
                id=r.id,
                image_id=r.image_id,
                user_id=r.user_id,
                parent_id=r.parent_id,
                body=r.body if r.deleted_at is None else "",
                anchor_json=r.anchor_json,
                created_at=r.created_at,
                updated_at=r.updated_at,
                deleted_at=r.deleted_at,
                author_display_name=author[0] if author else None,
                author_email=author[1] if author else None,
            )
        )
    return out


# ---------- endpoints ----------


@router.get("/images/{image_id}/comments", response_model=list[CommentRead])
async def list_image_comments(
    image_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CommentRead]:
    """Return every comment on this image (oldest first; the FE
    threads them client-side by `parent_id`). Soft-deleted rows are
    included so reply trees don't break — the FE renders "comment
    deleted" placeholders for rows where `deleted_at` is not null."""
    await _can_access_image(session, image_id, user)
    rows = (
        await session.execute(
            select(Comment)
            .where(Comment.image_id == image_id)
            .order_by(Comment.created_at.asc(), Comment.id.asc())
        )
    ).scalars().all()
    return await _attach_author_info(session, list(rows))


@router.post(
    "/images/{image_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_image_comment(
    image_id: UUID,
    payload: CommentCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentRead:
    # HARDENING — per-user create throttle. Comments are writable by any
    # ACTIVE share recipient (not just the image owner), so without a
    # limit a recipient could script thousands of inserts to flood a
    # thread / inflate the DB / spam the owner. 60/min/user is well above
    # any human commenting cadence while capping automated abuse. Keyed
    # on user id only (not image) so rotating across images can't
    # multiply the budget. Matches the rate-limit pattern used by the
    # other write endpoints (people/images/shares/vault).
    from backend.security import enforce_rate_limit
    await enforce_rate_limit(
        key=f"comment:create:{user.id}",
        limit=60,
        window_seconds=60,
        detail="Too many comments. Slow down and try again in a minute.",
    )

    await _can_access_image(session, image_id, user)

    # If this is a reply, the parent has to belong to the same image —
    # cross-image parenting would let a malicious client construct
    # threads that span photos the user can't even read on their own.
    if payload.parent_id is not None:
        parent = (
            await session.execute(
                select(Comment).where(
                    Comment.id == payload.parent_id,
                    Comment.image_id == image_id,
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Parent comment not found on this image",
            )

    comment = Comment(
        image_id=image_id,
        user_id=user.id,
        parent_id=payload.parent_id,
        body=payload.body.strip(),
        anchor_json=payload.anchor_json,
    )
    session.add(comment)
    await session.flush()
    await add_audit(
        session,
        user_id=user.id,
        action="comment.create",
        details={
            "image_id": str(image_id),
            "comment_id": comment.id,
            "parent_id": payload.parent_id,
            "has_anchor": payload.anchor_json is not None,
        },
    )
    await session.commit()
    await session.refresh(comment)
    rows = await _attach_author_info(session, [comment])
    return rows[0]


@router.patch("/comments/{comment_id}", response_model=CommentRead)
async def edit_comment(
    comment_id: int,
    payload: CommentEdit,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentRead:
    """Editing is author-only. Image owners can DELETE comments on
    their files but cannot rewrite them — that would let an owner
    put words in a sharer's mouth."""
    comment = (
        await session.execute(
            select(Comment).where(Comment.id == comment_id)
        )
    ).scalar_one_or_none()
    if comment is None or comment.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comment not found")
    if comment.user_id != user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only edit your own comments",
        )
    comment.body = payload.body.strip()
    comment.updated_at = datetime.now(timezone.utc)
    await add_audit(
        session,
        user_id=user.id,
        action="comment.edit",
        details={"comment_id": comment.id, "image_id": str(comment.image_id)},
    )
    await session.commit()
    await session.refresh(comment)
    rows = await _attach_author_info(session, [comment])
    return rows[0]


@router.delete(
    "/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_comment(
    comment_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Soft delete. The author OR the image owner can remove. Image
    owners need this to police comments on their own files; authors
    keep self-edit rights even when they're not the image owner."""
    comment = (
        await session.execute(
            select(Comment).where(Comment.id == comment_id)
        )
    ).scalar_one_or_none()
    if comment is None or comment.deleted_at is not None:
        # Idempotent — already gone is success.
        return None
    is_author = comment.user_id == user.id
    is_image_owner = False
    if not is_author:
        # Owner check via a single targeted query — avoids loading the
        # full Image row if the author check already passed.
        img_owner = (
            await session.execute(
                select(Image.user_id).where(Image.id == comment.image_id)
            )
        ).scalar_one_or_none()
        is_image_owner = img_owner == user.id
    if not (is_author or is_image_owner):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only the author or the image owner can delete a comment",
        )
    comment.deleted_at = datetime.now(timezone.utc)
    await add_audit(
        session,
        user_id=user.id,
        action="comment.delete",
        details={
            "comment_id": comment.id,
            "image_id": str(comment.image_id),
            "deleted_by": "author" if is_author else "image_owner",
        },
    )
    await session.commit()
    return None
