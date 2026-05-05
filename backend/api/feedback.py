"""Feedback ingestion endpoint (Phase 6).

  POST /images/{image_id}/feedback   { rating: 1..5 }
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.feedback import record_rating
from backend.models import Image, User

router = APIRouter(prefix="/images", tags=["feedback"])


class FeedbackPayload(BaseModel):
    rating: int = Field(..., ge=1, le=5)


class FeedbackResponse(BaseModel):
    recorded: bool
    reason: str | None = None


@router.post("/{image_id}/feedback", response_model=FeedbackResponse)
async def post_feedback(
    image_id: UUID,
    payload: FeedbackPayload,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FeedbackResponse:
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

    event = await record_rating(session, user, image, payload.rating)
    if event is None:
        # Hard-rule lossless image — no bandit arm to reward. Politely tell
        # the frontend so it can hide the rating modal next time.
        return FeedbackResponse(
            recorded=False,
            reason="image was encoded by the hard rule (no bandit arm)",
        )
    await session.commit()
    return FeedbackResponse(recorded=True)
