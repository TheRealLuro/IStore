import uuid
from datetime import datetime

from fastapi_users import schemas
from pydantic import BaseModel, ConfigDict, Field, model_validator


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: str | None = None
    role: str = "user"
    age_confirmed: bool = False


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None
    age_confirmed: bool = Field(..., description="Must be true; under-13 use is prohibited.")

    @model_validator(mode="after")
    def _require_age_gate(self) -> "UserCreate":
        if self.age_confirmed is not True:
            raise ValueError("You must confirm you are old enough to use IStore.")
        return self


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None
    age_confirmed: bool | None = None


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    label: str
    confidence: float | None = None


class ImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str = "image"
    original_filename: str | None
    width: int | None
    height: int | None
    byte_size_original: int | None
    byte_size_served: int | None
    mime_type_original: str | None
    mime_type_served: str | None
    codec: str | None
    quality: int | None
    max_dim: int | None
    lossless: bool | None = None

    content_type: str | None = None
    content_confidence: float | None = None
    scene_label: str | None = None
    scene_confidence: float | None = None
    face_likelihood: float | None = None
    pending_face_scan: bool = True
    indoor_outdoor: str | None = None
    vision_processed_at: datetime | None = None
    bandit_arm_id: int | None = None

    summary: str | None = None
    summary_topic: str | None = None
    summary_points: list[str] | None = None
    pending_summary: bool = True
    summary_generated_at: datetime | None = None

    folder_id: uuid.UUID | None = None
    status: str | None = None
    status_color: str | None = None

    uploaded_at: datetime


class FolderRead(BaseModel):
    """Folder shape returned by /folders. The grid renders folders as
    cards mixed with image rows (sorted folders-first), so the schema
    intentionally overlaps the FE's FileItem shape — `id`, `name`,
    `category="folder"` so the same component branch can render both."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_folder_id: uuid.UUID | None = None
    name: str
    status: str | None = None
    status_color: str | None = None
    # Convenience counts so the card can show "12 files · 2 folders"
    # without the FE needing a second round-trip per folder.
    item_count: int = 0
    subfolder_count: int = 0
    created_at: datetime
    updated_at: datetime


class FolderCreate(BaseModel):
    name: str
    parent_folder_id: uuid.UUID | None = None
    status: str | None = None
    status_color: str | None = None


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_folder_id: uuid.UUID | None = None
    status: str | None = None
    status_color: str | None = None


class ImageMove(BaseModel):
    """Body for `PATCH /images/{id}/move`. `null` folder_id moves the
    image back to the root."""

    folder_id: uuid.UUID | None = None


class StatusSet(BaseModel):
    """Body for `PATCH /images/{id}/status` and `PATCH /folders/{id}` (the
    latter just reuses status fields). Both null clears the status."""

    status: str | None = None
    status_color: str | None = None


class StorageUsage(BaseModel):
    used_bytes: int
    quota_bytes: int
    by_category: dict[str, int]
    by_count: dict[str, int]


class ImageSearchHit(ImageRead):
    score: float
