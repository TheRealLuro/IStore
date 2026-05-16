"""Folders API (Phase 12).

  GET    /folders/                — list folders at a given parent (default root)
  POST   /folders/                — create a folder
  PATCH  /folders/{id}            — rename / move / set status
  DELETE /folders/{id}            — soft-delete (cascades to subtree)
  PATCH  /images/{id}/folder      — defined in api/images.py; moves an image

Folders are scoped to the authenticated user — every query joins on
`user_id`. The unique-name partial index on `(user_id, parent, lower(name))`
in migration 0010 prevents duplicate names in the same parent; surface
the IntegrityError as a 409 Conflict so the FE can prompt for a rename.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.archive_upload import extract_archive_into_folder
from backend.auth.users import current_active_user
from backend.config import settings
from backend.db import get_session
from backend.models import Folder, FolderTag, Image, Tag, User
from backend.schemas import FolderCreate, FolderRead, FolderUpdate
from backend.security import enforce_upload_limits
from backend.upload_validation import UploadValidationError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/folders", tags=["folders"])


def _to_read(
    f: Folder,
    item_count: int = 0,
    subfolder_count: int = 0,
    tags: list[dict] | None = None,
) -> FolderRead:
    return FolderRead(
        id=f.id,
        parent_folder_id=f.parent_folder_id,
        name=f.name,
        status=f.status,
        status_color=f.status_color,
        # §C1.6 — folder-attached tags, hydrated by the caller via a
        # single JOIN query. Empty list when the folder has no tags.
        tags=tags or [],
        item_count=item_count,
        subfolder_count=subfolder_count,
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


# §C1.3 — the category strings the FE type-pill emits. Anything
# else is rejected at the schema boundary so an attacker can't
# inject a SQL identifier.
_VALID_CONTAINS_TYPES = {"image", "video", "document"}


async def _folder_ids_containing_type(
    session: AsyncSession,
    *,
    user_id: UUID,
    contains_type: str,
) -> set[UUID]:
    """Return the set of folder ids (across the WHOLE user's tree) that
    contain at least one image of `contains_type` somewhere in their
    descendant subtree.

    Implementation: a recursive CTE that climbs from each image's
    folder up through `parent_folder_id` and collects every ancestor.
    Cheap-ish on the Postgres side — we deliberately don't materialize
    a folder→descendants closure table for v1 (premature optimization).
    """
    # Inline raw SQL via SQLAlchemy `text()` would also work, but the
    # ORM builder keeps the predicate readable + parameterized.
    from sqlalchemy import literal_column, union_all

    # Anchor: every folder that directly holds an image of the matching
    # category.
    anchor = (
        select(
            Image.folder_id.label("folder_id"),
        )
        .where(
            Image.user_id == user_id,
            Image.deleted_at.is_(None),
            Image.folder_id.is_not(None),
            Image.category == contains_type,
        )
        .distinct()
        .cte("anchor", recursive=True)
    )
    # Recursive step: pull each ancestor by joining `folders.id` to the
    # CTE's `folder_id` and surfacing `parent_folder_id`.
    parent = (
        select(Folder.parent_folder_id.label("folder_id"))
        .join(anchor, Folder.id == anchor.c.folder_id)
        .where(
            Folder.user_id == user_id,
            Folder.deleted_at.is_(None),
            Folder.parent_folder_id.is_not(None),
        )
    )
    closure = anchor.union(parent)
    rows = (
        await session.execute(select(closure.c.folder_id).distinct())
    ).scalars().all()
    return {fid for fid in rows if fid is not None}


@router.get("/", response_model=list[FolderRead])
async def list_folders(
    parent_id: UUID | None = None,
    user: Annotated[User, Depends(current_active_user)] = ...,
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
    # §C1.3 — when the FE has the Images/Videos/Documents type pill
    # active, hide any folder whose entire subtree contains zero files
    # of that category. `contains_type=image|video|document` opts into
    # the recursive-descendant check; omitted returns the legacy
    # "every folder at this parent" listing.
    contains_type: str | None = None,
):
    """List folders directly under `parent_id` (None = root) for this user.

    Each result includes counts of contained images and subfolders so
    the grid can render "12 files · 2 folders" without a follow-up
    request per folder.

    With `?contains_type=image|video|document` the result is filtered
    so only folders whose subtree holds ≥ 1 file of that category come
    back. Files at this parent level are not affected — the FE filters
    those independently via the type pill on the gallery grid.
    """
    if contains_type is not None and contains_type not in _VALID_CONTAINS_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"contains_type must be one of {sorted(_VALID_CONTAINS_TYPES)} or omitted.",
        )

    folder_predicate = (
        Folder.parent_folder_id.is_(None)
        if parent_id is None
        else Folder.parent_folder_id == parent_id
    )
    rows = (
        await session.execute(
            select(Folder)
            .where(
                Folder.user_id == user.id,
                Folder.deleted_at.is_(None),
                folder_predicate,
            )
            .order_by(func.lower(Folder.name))
        )
    ).scalars().all()

    if not rows:
        return []

    # §C1.3 — apply the descendants-contain filter when requested.
    # The CTE walks the entire user's tree once; we just intersect the
    # current parent's children against the result set.
    if contains_type is not None:
        allow = await _folder_ids_containing_type(
            session, user_id=user.id, contains_type=contains_type,
        )
        rows = [f for f in rows if f.id in allow]
        if not rows:
            return []

    folder_ids = [f.id for f in rows]

    # Single aggregate per relation — N+1 would be acceptable at small
    # scale, but two aggregates is the right baseline.
    img_counts = dict(
        (
            await session.execute(
                select(Image.folder_id, func.count())
                .where(
                    Image.user_id == user.id,
                    Image.deleted_at.is_(None),
                    Image.folder_id.in_(folder_ids),
                )
                .group_by(Image.folder_id)
            )
        ).all()
    )
    sub_counts = dict(
        (
            await session.execute(
                select(Folder.parent_folder_id, func.count())
                .where(
                    Folder.user_id == user.id,
                    Folder.deleted_at.is_(None),
                    Folder.parent_folder_id.in_(folder_ids),
                )
                .group_by(Folder.parent_folder_id)
            )
        ).all()
    )

    # §C1.6 — folder tags in one JOIN.
    tag_rows = (
        await session.execute(
            select(FolderTag.folder_id, Tag.id, Tag.label, Tag.color)
            .join(Tag, Tag.id == FolderTag.tag_id)
            .where(
                FolderTag.user_id == user.id,
                FolderTag.folder_id.in_(folder_ids),
            )
        )
    ).all()
    tags_by_folder: dict = {}
    for folder_id_val, tid, label, color in tag_rows:
        tags_by_folder.setdefault(folder_id_val, []).append({
            "id": tid, "label": label, "color": color,
        })

    return [
        _to_read(
            f,
            img_counts.get(f.id, 0),
            sub_counts.get(f.id, 0),
            tags=tags_by_folder.get(f.id, []),
        )
        for f in rows
    ]


@router.post(
    "/",
    response_model=FolderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    body: FolderCreate,
    user: Annotated[User, Depends(current_active_user)] = ...,
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name required")
    if len(name) > 200:
        raise HTTPException(status_code=400, detail="Folder name too long")

    if body.parent_folder_id is not None:
        parent = await session.get(Folder, body.parent_folder_id)
        if (
            parent is None
            or parent.user_id != user.id
            or parent.deleted_at is not None
        ):
            raise HTTPException(status_code=404, detail="Parent folder not found")

    folder = Folder(
        user_id=user.id,
        parent_folder_id=body.parent_folder_id,
        name=name,
        status=body.status,
        status_color=body.status_color,
    )
    session.add(folder)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A folder with this name already exists in this location",
        )
    await session.refresh(folder)
    return _to_read(folder)


@router.patch("/{folder_id}", response_model=FolderRead)
async def update_folder(
    folder_id: UUID,
    body: FolderUpdate,
    user: Annotated[User, Depends(current_active_user)] = ...,
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    folder = await session.get(Folder, folder_id)
    if (
        folder is None
        or folder.user_id != user.id
        or folder.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Folder not found")

    if body.parent_folder_id is not None:
        # Don't let a folder be moved inside itself or its own subtree —
        # would create a cycle and break recursive deletes.
        if body.parent_folder_id == folder_id:
            raise HTTPException(
                status_code=400, detail="Cannot move a folder into itself"
            )
        if await _is_descendant(
            session, ancestor_id=folder_id, candidate_id=body.parent_folder_id
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot move a folder into its own descendant",
            )
        new_parent = await session.get(Folder, body.parent_folder_id)
        if (
            new_parent is None
            or new_parent.user_id != user.id
            or new_parent.deleted_at is not None
        ):
            raise HTTPException(status_code=404, detail="Target parent not found")

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Folder name required")
        folder.name = name
    if body.parent_folder_id is not None:
        folder.parent_folder_id = body.parent_folder_id
    if body.status is not None:
        folder.status = body.status or None
    if body.status_color is not None:
        folder.status_color = body.status_color or None
    folder.updated_at = datetime.now(timezone.utc)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A folder with this name already exists in this location",
        )
    await session.refresh(folder)
    return _to_read(folder)


@router.post(
    "/with-images",
    response_model=FolderRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder_with_images(
    body: dict,
    user: Annotated[User, Depends(current_active_user)] = ...,
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Create a folder AND move N existing images into it in one shot.

    Body: `{ "name": "...", "image_ids": ["<uuid>", ...],
             "parent_folder_id"?: "<uuid>" }`.
    The previous flow forced the user to (1) open New Folder, (2) name
    it, (3) close, (4) re-select all the files, (5) drag them into the
    new folder. This endpoint collapses that into one round trip the
    multi-select toolbar can hit directly.

    Folder creation + image-move both happen in a single transaction —
    if the folder name conflicts with an existing one (409), no images
    move; if image_ids is empty the folder is created on its own.
    """
    from backend.models import Image
    from sqlalchemy import update as sa_update

    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Folder name required")
    if len(name) > 200:
        raise HTTPException(400, "Folder name too long")

    parent_id = body.get("parent_folder_id")
    if parent_id is not None:
        try:
            parent_id = UUID(str(parent_id))
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid parent_folder_id")
        parent = await session.get(Folder, parent_id)
        if (
            parent is None
            or parent.user_id != user.id
            or parent.deleted_at is not None
        ):
            raise HTTPException(404, "Parent folder not found")

    raw_ids = body.get("image_ids") or []
    try:
        image_ids = [UUID(str(x)) for x in raw_ids]
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid image_ids: {exc}")

    folder = Folder(
        user_id=user.id,
        parent_folder_id=parent_id,
        name=name,
    )
    session.add(folder)
    try:
        await session.flush()  # need folder.id before the image update
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            409,
            "A folder with this name already exists in this location",
        )

    moved = 0
    if image_ids:
        res = await session.execute(
            sa_update(Image)
            .where(
                Image.id.in_(image_ids),
                Image.user_id == user.id,
                Image.deleted_at.is_(None),
            )
            .values(folder_id=folder.id)
        )
        moved = int(res.rowcount or 0)

    await session.commit()
    await session.refresh(folder)
    return _to_read(folder, item_count=moved, subfolder_count=0)


@router.post("/upload-archive", status_code=status.HTTP_201_CREATED)
async def upload_archive(
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File(...)],
    parent_folder_id: Annotated[UUID | None, Form()] = None,
) -> dict:
    """§C1.5 — Extract a zip / tar archive into a new auto-named folder.

    Each entry is routed through the standard `store_upload` pipeline,
    so per-file MIME / magic-bytes / Pillow re-encode / quarantine /
    bandit-compression all apply. Archive-level safety (entry count,
    expansion ratio, path traversal, symlinks, depth) uses the same
    constants as `_inspect_ooxml` so OOXML and general archives share
    one security posture.

    Body: multipart with a single `file` field. Optional
    `parent_folder_id` form field places the auto-created folder under
    an existing folder; default is root.

    Response shape:
      {
        "folder_id":         "<uuid>",
        "source_archive_id": "<uuid>",
        "accepted":          int,   # entries that landed as Image rows
        "rejected":          int,   # entries `store_upload` rejected
        "skipped":           int,   # directory / empty entries
        "rejected_details":  [ { "path", "reason", "status" }, ... ]
      }
    """
    raw = await file.read(settings.upload_max_bytes + 1)
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty upload")
    if len(raw) > settings.upload_max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Archive exceeds the per-file size limit.",
        )
    # The archive is one upload as far as the rate limiter is concerned —
    # per-entry counts would let a small zip flood the daily byte ceiling
    # without the limiter noticing.
    await enforce_upload_limits(str(user.id), request, len(raw))

    if parent_folder_id is not None:
        parent = await session.get(Folder, parent_folder_id)
        if (
            parent is None
            or parent.user_id != user.id
            or parent.deleted_at is not None
        ):
            raise HTTPException(404, "Parent folder not found")

    try:
        result = await extract_archive_into_folder(
            session=session,
            user=user,
            raw_bytes=raw,
            filename=file.filename,
            parent_folder_id=parent_folder_id,
        )
    except UploadValidationError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {
        "folder_id": str(result.folder_id),
        "source_archive_id": str(result.source_archive_id),
        "accepted": result.accepted,
        "rejected": result.rejected,
        "skipped": result.skipped_dirs,
        "rejected_details": result.rejected_details,
    }


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: UUID,
    user: Annotated[User, Depends(current_active_user)] = ...,
    session: Annotated[AsyncSession, Depends(get_session)] = ...,
):
    """Soft-delete a folder + its entire subtree of subfolders.

    Images inside detached folders have their `folder_id` set NULL via
    the FK ON DELETE SET NULL — they re-appear at root rather than
    disappearing with the folder. Hard delete (and image cleanup) lives
    in the retention sweeper, same as image soft-delete.
    """
    folder = await session.get(Folder, folder_id)
    if (
        folder is None
        or folder.user_id != user.id
        or folder.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="Folder not found")

    now = datetime.now(timezone.utc)
    # Soft-delete the whole subtree in one statement using a recursive CTE.
    # Avoids issuing N updates for deeply nested folders.
    descendants = (
        await session.execute(
            _descendants_query(folder_id)
        )
    ).scalars().all()
    ids = [folder_id, *descendants]
    await session.execute(
        update(Folder)
        .where(Folder.id.in_(ids), Folder.user_id == user.id)
        .values(deleted_at=now, updated_at=now)
    )
    await session.commit()


def _descendants_query(folder_id: UUID):
    """Recursive CTE that returns every folder.id descended from
    `folder_id`. Returns an executable Select."""
    from sqlalchemy.orm import aliased

    base = (
        select(Folder.id)
        .where(Folder.parent_folder_id == folder_id)
        .cte(name="d", recursive=True)
    )
    base_alias = aliased(Folder, name="f")
    recursive = (
        select(base_alias.id)
        .join(base, base_alias.parent_folder_id == base.c.id)
    )
    full = base.union_all(recursive)
    return select(full.c.id)


async def _is_descendant(
    session: AsyncSession, ancestor_id: UUID, candidate_id: UUID
) -> bool:
    """True iff `candidate_id` sits anywhere under `ancestor_id`."""
    rows = (
        await session.execute(_descendants_query(ancestor_id))
    ).scalars().all()
    return candidate_id in rows
