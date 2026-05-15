import uuid
from datetime import datetime

from fastapi_users import schemas
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: str | None = None
    role: str = "user"
    age_confirmed: bool = False
    # §1.2.2 — the FE needs to know whether to show "Enable 2FA" or
    # "Disable 2FA" without a second round trip to /account/2fa/status.
    totp_enabled: bool = False


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None
    age_confirmed: bool = Field(..., description="Must be true; under-13 use is prohibited.")

    @model_validator(mode="after")
    def _require_age_gate(self) -> "UserCreate":
        if self.age_confirmed is not True:
            raise ValueError("You must confirm you are old enough to use neuthek.")
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
    # Multi-model image pipeline output (Phase 14 C2): per-image dict
    # with keys `regions` (Florence-2 DENSE_REGION_CAPTION phrases),
    # `objects` (Florence-2 OD labels), `concepts` (OpenCLIP top-K
    # against the curated vocab), `vlm` (InternVL2 description when
    # heavy_vlm_enabled). Any subset may be null.
    summary_signals: dict | None = None

    folder_id: uuid.UUID | None = None
    status: str | None = None
    status_color: str | None = None

    is_starred: bool = False
    starred_at: datetime | None = None

    uploaded_at: datetime
    # Hybrid-retention countdown: when the user's untouched original is
    # eligible for archival/deletion. Surfaced in the preview panel so
    # users see the policy applied to this specific file.
    original_expires_at: datetime | None = None


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


class ImageRename(BaseModel):
    """Body for `PATCH /images/{id}/name`. Server-side validation enforces
    the rules in `backend.upload_validation.validate_image_filename` —
    this Pydantic schema only catches gross misuse (empty, > 255 chars)."""

    name: str = Field(..., min_length=1, max_length=255)


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


# ---------- Sharing (todo §1.1 / G1) ----------

# Server-side cap on how long an existing-user share can live. The UI
# only offers 1h / 1d / 7d / 30d so this is a defense-in-depth bound,
# not the primary limit.
SHARE_MAX_DURATION_SECONDS = 30 * 86400
# Hard window applied at claim time when the recipient was new (had no
# account at create). Sharer's `duration_seconds` is ignored for this
# path — see `claim_share` in backend/api/shares.py.
SHARE_NEW_USER_WINDOW_SECONDS = 86400


class ShareGrantCreate(BaseModel):
    """Body for `POST /images/{image_id}/shares`. The sharer chooses a
    recipient email and a duration (in seconds). Duration is the
    requested window for an *existing-user* recipient; new users get
    SHARE_NEW_USER_WINDOW_SECONDS regardless."""

    recipient_email: EmailStr
    duration_seconds: int = Field(
        ..., ge=60, le=SHARE_MAX_DURATION_SECONDS,
        description="Requested window in seconds; capped at 30 days. "
        "Ignored for new-user recipients (always 1 day at claim).",
    )
    permission: str = Field(default="view_download", pattern="^view(_download)?$")


class ShareGrantRead(BaseModel):
    """Owner-side view of a grant. `share_url` is only populated on
    create — `GET /images/{id}/shares` omits it because the plaintext
    token isn't stored. Recipient sees `IncomingShareRead` instead."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    image_id: uuid.UUID
    recipient_email: str
    recipient_user_id: uuid.UUID | None = None
    recipient_display_name: str | None = None
    permission: str
    sharer_duration_seconds: int
    expires_at: datetime | None = None
    claimed_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    share_url: str | None = None


class IncomingShareRead(BaseModel):
    """Recipient-side card for the "Shared" sidebar."""

    model_config = ConfigDict(from_attributes=True)

    share_id: uuid.UUID
    image_id: uuid.UUID
    image_filename: str | None
    image_category: str
    sharer_display_name: str | None
    sharer_email: str
    permission: str
    expires_at: datetime | None
    claimed_at: datetime | None


class ShareClaimBody(BaseModel):
    token: str = Field(..., min_length=16, max_length=128)


class ShareClaimResult(BaseModel):
    share_id: uuid.UUID
    image_id: uuid.UUID
    expires_at: datetime | None
    was_pending: bool


class SharePreviewResult(BaseModel):
    """Unauthenticated minimal preview for the public landing page.

    Carries the absolute minimum so the FE can render
    "Bob shared 'sunset.jpg' with you". No bytes, no thumbnails,
    no EXIF, no GPS — those require an authenticated claim."""

    sharer_display_name: str | None
    image_filename: str | None
    image_category: str
    requires_signup: bool


class ShareSignedUrl(BaseModel):
    url: str
    expires_at: str
