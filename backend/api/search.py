"""Semantic search.

GET /search?q=...
  → encode `q` via CLIP text encoder
  → cosine-KNN against `images.clip_embedding` filtered to the user
  → return top-k hits with similarity scores
"""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Image, User
from backend.schemas import ImageRead, ImageSearchHit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


def _encode_text_sync(query: str):
    from backend.vision.runtime import encode_text_cached

    return encode_text_cached(query)


@router.get("/", response_model=list[ImageSearchHit])
async def semantic_search(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: int = 30,
) -> list[dict]:
    try:
        query_vec = await asyncio.to_thread(_encode_text_sync, q)
    except ImportError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Semantic search requires the [ml] extras to be installed.",
        )

    distance = Image.clip_embedding.cosine_distance(query_vec.tolist())

    stmt = (
        select(Image, distance.label("distance"))
        .where(
            Image.user_id == user.id,
            Image.deleted_at.is_(None),
            Image.clip_embedding.is_not(None),
        )
        .order_by(distance.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)

    hits = []
    for image, dist in result.all():
        score = 1.0 - float(dist)  # cosine_distance = 1 - cosine_similarity
        base = ImageRead.model_validate(image, from_attributes=True).model_dump()
        hits.append(ImageSearchHit(**base, score=score))
    return hits
