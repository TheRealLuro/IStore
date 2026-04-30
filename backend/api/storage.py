from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import Image, User
from backend.schemas import StorageUsage

router = APIRouter(prefix="/storage", tags=["storage"])

# Hard-coded for now; later move to a `users.quota_bytes` column or a tier table.
DEFAULT_QUOTA_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB


@router.get("/usage", response_model=StorageUsage)
async def storage_usage(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StorageUsage:
    stmt = (
        select(
            Image.category,
            func.coalesce(func.sum(Image.byte_size_served), 0).label("bytes"),
            func.count().label("n"),
        )
        .where(Image.user_id == user.id, Image.deleted_at.is_(None))
        .group_by(Image.category)
    )
    result = await session.execute(stmt)
    by_category: dict[str, int] = {}
    by_count: dict[str, int] = {}
    total = 0
    for category, bytes_, n in result.all():
        by_category[category] = int(bytes_)
        by_count[category] = int(n)
        total += int(bytes_)
    return StorageUsage(
        used_bytes=total,
        quota_bytes=DEFAULT_QUOTA_BYTES,
        by_category=by_category,
        by_count=by_count,
    )
