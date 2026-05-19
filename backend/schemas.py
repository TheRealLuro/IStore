import uuid
from datetime import datetime

from fastapi_users import schemas
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, model_validator


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: str | None = None
    role: str = "user"
    age_confirmed: bool = False
    # §1.2.2 — the FE needs to know whether to show "Enable 2FA" or
    # "Disable 2FA" without a second round trip to /account/2fa/status.
    totp_enabled: bool = False
    # Surfaced so Settings → Account can show "Sign in with Google
    # linked" / "Link Google account" without an extra round trip.
    # Derived in `model_validator` below from `google_sub` so the raw
    # identifier never leaves the server.
    google_linked: bool = False

    @model_validator(mode="before")
    @classmethod
    def _project_google_linked(cls, data):
        # `data` is either a User ORM instance (when fastapi-users
        # serializes via from_attributes) or a dict from a manual
        # construct. Normalize both to set `google_linked` from the
        # presence of `google_sub`.
        if isinstance(data, dict):
            if "google_linked" not in data:
                data["google_linked"] = bool(data.get("google_sub"))
            return data
        sub = getattr(data, "google_sub", None)
        try:
            # Pydantic v2 will read the rest of the attrs via from_attributes;
            # we just need to plant `google_linked` so the projection
            # below picks it up. Attach on the instance for the ORM
            # path — orm_mode/from_attributes reads attributes lazily,
            # so adding a Python attribute is enough.
            object.__setattr__(data, "google_linked", bool(sub))
        except Exception:
            pass
        return data


class ConsentBundleItem(BaseModel):
    """One consent grant submitted in the §B2 registration payload.

    Each item names a `SUPPORTED_SCOPES` entry (face_recognition /
    gps_retention / exif_retention / ai_summary / semantic_search /
    bandit_compression_telemetry) plus the user's explicit decision.
    Decisions are persisted as ConsentRecord rows during the
    `on_after_register` hook; collecting them at signup satisfies the
    legal requirement that informed consent precedes data collection.
    """
    kind: str = Field(..., min_length=1, max_length=64)
    state: str = Field(..., pattern="^(GRANTED|WITHDRAWN)$")


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None
    age_confirmed: bool = Field(..., description="Must be true; under-13 use is prohibited.")
    # §B2 — accept consent decisions at registration time so the
    # consent ledger predates the account row. The legacy flow
    # (consents POSTed AFTER /auth/register) still works — this
    # field is optional, and the user manager folds whatever's here
    # into the ConsentRecord table inside the same on_after_register
    # transaction that finalizes the user.
    consents: list[ConsentBundleItem] | None = None
    # Free-text the user typed as their consent signature; persisted
    # on every grant row for the chain-of-custody record. Optional —
    # the legacy /consent/{kind}/grant endpoint still wants a
    # signature; for the register-bundled path we use display_name or
    # email as the fallback when this field is empty.
    consent_signature: str | None = None

    @model_validator(mode="after")
    def _require_age_gate(self) -> "UserCreate":
        if self.age_confirmed is not True:
            raise ValueError("You must confirm you are old enough to use neuthek.")
        return self

    def create_update_dict(self):
        # fastapi-users folds this dict straight into User(**dict). The
        # `consents` + `consent_signature` fields are §B2 sidecar data
        # that the UserManager.create() override pulls out manually
        # before delegating to super(); strip them here so the User
        # __init__ doesn't choke on unknown columns.
        return {
            k: v for k, v in super().create_update_dict().items()
            if k not in {"consents", "consent_signature"}
        }


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None
    age_confirmed: bool | None = None


class TagRead(BaseModel):
    """§C1.6 — per-image / per-folder attached tag (label + chip color +
    db id). Used as the embedded shape on ImageRead / FolderRead; full
    CRUD has its own response shape in backend/api/tags.py."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    label: str
    color: str | None = None
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
    # §C1.6 — user-attached tags. The default empty list keeps
    # `from_attributes=True` validation away from `Image.tags` (which
    # is a list[ImageTag] join row that doesn't match TagRead's shape).
    # The route handler sets the tags imperatively via
    # `model_copy(update={"tags": [...]})`.
    tags: list[TagRead] = Field(
        default_factory=list,
        validation_alias=AliasChoices("attached_tags"),
    )

    is_starred: bool = False
    starred_at: datetime | None = None

    uploaded_at: datetime
    # Hybrid-retention countdown: when the user's untouched original is
    # eligible for archival/deletion. Surfaced in the preview panel so
    # users see the policy applied to this specific file.
    original_expires_at: datetime | None = None

    # Derived bool — true when this row has a separately-stored
    # thumbnail blob (video / audio poster JPEG written by the
    # transcode worker). The gallery card uses this to decide
    # whether to ask `/served?max_dim=600` for a preview image.
    # Populated from `Image.has_thumbnail` (a hybrid_property on
    # the model that maps to `thumbnail_blob_key is not None`).
    has_thumbnail: bool = False


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
    # §C1.6 — folder-attached tags. Same trick as ImageRead.tags: the
    # route handler sets these imperatively to keep Pydantic's
    # from_attributes path away from `Folder.folder_tags` (which we
    # don't have a relationship for; FolderTag joins via tag_id).
    tags: list[TagRead] = Field(
        default_factory=list,
        validation_alias=AliasChoices("attached_tags"),
    )
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
