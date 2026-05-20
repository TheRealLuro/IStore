import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    TIMESTAMP,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, JSONB, TSVECTOR, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="user")
    # Stable Google account identifier. Captured during either the SSO
    # sign-in flow OR when an already-signed-in user connects Drive —
    # the Drive consent screen also returns an id_token now that the
    # flow requests `openid email profile` alongside `drive.readonly`.
    # Indexed unique so a second user can't claim the same Google
    # account, and so the SSO callback can resolve "which neuthek user
    # is this Google sub?" with one row lookup.
    google_sub: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    age_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # C8 admin dashboard — per-user storage quota override. NULL means
    # "use the global default" (DEFAULT_QUOTA_BYTES in api/storage.py).
    quota_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Per-user originals-retention policy (migration 0038). Discrete
    # enum so the UI is a radio group and the upload path is a
    # one-switch translation. "30d" is the legacy default — existing
    # accounts see no behavior change until they explicitly pick
    # something else. Validated by a CHECK constraint at the DB
    # layer; values are kept in sync with the application enum
    # (`_RETENTION_TO_TIMEDELTA` in backend/api/storage.py).
    original_retention_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="30d",
    )

    # §1.2.2 — TOTP 2FA. Secret is Fernet-encrypted via secret_box so a
    # DB dump alone doesn't yield working codes. `enabled` flips to True
    # only after the user verifies a code against the freshly-set secret.
    # `verified_at` stamps the last successful verify (setup or login).
    totp_secret_enc: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    totp_verified_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # §B4 — account-deletion grace window. /account/schedule-delete
    # stamps this column to `now + ACCOUNT_DELETE_GRACE_DAYS` instead
    # of nuking the row outright. The nightly sweep
    # (retention.sweep_scheduled_account_deletes) picks up anything
    # whose timestamp has passed. /account/cancel-delete clears it.
    scheduled_delete_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    images: Mapped[list["Image"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Image(Base):
    __tablename__ = "images"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user: Mapped[User] = relationship(back_populates="images")

    original_blob_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    served_blob_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_blob_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="image"
    )

    width: Mapped[Optional[int]] = mapped_column(nullable=True)
    height: Mapped[Optional[int]] = mapped_column(nullable=True)
    byte_size_original: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    byte_size_served: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # 128 is enough headroom for every IANA-registered MIME we accept,
    # including the OOXML ones at 70-74 chars
    # (e.g. `application/vnd.openxmlformats-officedocument.presentationml.presentation`).
    mime_type_original: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    mime_type_served: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Per-image quality-tier map (video / audio only). Populated by the
    # transcode worker — when the source is 1080p+ we emit 1080p,
    # 720p, and 480p variants; lower-res sources emit fewer tiers.
    # `served_blob_key` always points at the default (highest) tier;
    # this dict is the lookup table for the picker. Image rows leave
    # this NULL.
    served_variants: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    sha256: Mapped[Optional[bytes]] = mapped_column(LargeBinary(32), nullable=True)

    codec: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    quality: Mapped[Optional[int]] = mapped_column(nullable=True)
    max_dim: Mapped[Optional[int]] = mapped_column(nullable=True)
    lossless: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Phase 5 LinUCB: which arm produced this encoding + the context vector
    # used at decision time. Replayed by the trainer when feedback arrives.
    bandit_arm_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    context_features: Mapped[Optional[list[float]]] = mapped_column(
        ARRAY(Float), nullable=True
    )

    # Phase 2 vision columns.
    clip_embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vector(768), nullable=True
    )
    # CLIP text-space embedding of `summary`. Populated post-summarize
    # by the worker hook. At search time we score against MAX of
    # (image_emb cosine, summary_emb cosine) so queries that match
    # the image's CAPTION but not its visual content still surface
    # the right row (e.g. "place where I had coffee" → a row whose
    # summary mentions "cafe table" but doesn't visually look like
    # the canonical coffee shop). HNSW-indexed (migration 0037).
    summary_clip_embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vector(768), nullable=True
    )
    content_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    content_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    scene_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    scene_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    face_likelihood: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pending_face_scan: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    indoor_outdoor: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    vision_processed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Phase 11 — AI Vision content summary. Populated by a BackgroundTask
    # after upload (see backend/summarize.py). `pending_summary` lets the
    # frontend show a loading state and the search index re-fetch when
    # ready, mirroring the pending_face_scan pattern.
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_topic: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_points: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)
    pending_summary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    summary_generated_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # §C2 — Limited-Use compliance flag. When True (default for
    # cloud-synced files), every AI pass is skipped: no CLIP
    # embedding, no Florence-2 / Qwen summary, no RetinaFace face
    # scan. The user can opt back in per-source via the cloud-link
    # settings UI; flipping the flag re-queues the file through the
    # normal `summarize-progress` background workers.
    skip_ai_training: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # §C2 — source provider tag ('google_drive', 'github', …). Lets us
    # show "From Drive" badges + power the per-source AI opt-in.
    source_provider: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )

    # Phase 14 — multi-model signal capture. Populated by the C2 image
    # pipeline so re-summarization is idempotent without re-running every
    # stage from scratch. Shape:
    #   {"regions": [...phrases...], "objects": [...labels...],
    #    "concepts": [...clip tags...], "vlm": "rich VLM paragraph"}
    # Any subset may be missing — each stage is best-effort.
    summary_signals: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # `summary_tsv` is a Postgres GENERATED ALWAYS AS (...) STORED
    # tsvector — produced from summary + summary_topic +
    # summary_points + original_filename at row-write time, then
    # GIN-indexed (`ix_images_summary_tsv`). Surfacing it in the
    # ORM lets `_text_search` reference it directly instead of
    # building `to_tsvector(...)` on the fly per row (which
    # forced full table scans even though the index existed).
    #
    # The `Computed()` marker is critical here — WITHOUT it,
    # SQLAlchemy includes this column in every INSERT statement
    # with value NULL, and Postgres rejects it with:
    #   "cannot insert a non-DEFAULT value into column \"summary_tsv\""
    # The expression we pass is a placeholder for DDL purposes
    # only; the actual GENERATED ALWAYS AS expression lives in
    # the migration that created the column. What matters at
    # runtime is the marker, which tells SQLAlchemy "the DB
    # maintains this — exclude it from INSERT and UPDATE."
    # `Mapped[Optional[Any]]` because the column is read-only
    # from Python's perspective.
    summary_tsv: Mapped[Optional[Any]] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english'::regconfig, "
            "coalesce(summary, '') || ' ' || "
            "coalesce(summary_topic, '') || ' ' || "
            "coalesce(original_filename, ''))",
            persisted=True,
        ),
        nullable=True,
    )

    # Phase 12 — folders + project status.
    folder_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True,
    )
    folder: Mapped[Optional["Folder"]] = relationship(back_populates="images")
    # User-defined project status — free text so the user picks their own
    # vocabulary ("not started", "in progress", "submitted", etc.). The
    # color is a lookup key the frontend resolves to a chip tint.
    status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # User-toggled "star" / favorite flag. `starred_at` only updates on the
    # GRANTED transition; un-starring leaves it as the historical timestamp
    # so a re-star preserves "starred X days ago" continuity. Backed by a
    # partial index (see migration 0018) for fast "starred only" listings.
    is_starred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    starred_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    uploaded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # §C9 follow-up — EXIF DateTimeOriginal, populated whenever
    # exif_retention consent is on (independent of GPS retention).
    # NULL means we never had EXIF for this image OR the user hasn't
    # granted exif_retention. list_images / facets COALESCE this with
    # uploaded_at so the date-range filter Just Works either way.
    captured_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    original_expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        server_default=text("now() + interval '30 days'"),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    tags: Mapped[list["ImageTag"]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )

    # Derived presentation flag for the gallery card. Pure Python
    # property — never queried directly, just surfaced via
    # `ImageRead.has_thumbnail` (from_attributes=True). For images
    # the row uses `served_blob_key` itself as the thumb; for video
    # / audio it's the separately-stored JPEG poster the transcode
    # worker writes. Image rows return True when they have ANY
    # served bytes; video / audio rows return True only when the
    # poster is in place.
    @property
    def has_thumbnail(self) -> bool:
        if self.category in {"video", "audio"}:
            return self.thumbnail_blob_key is not None
        return (
            self.served_blob_key is not None
            and (self.mime_type_served or "").startswith("image/")
        )

    __table_args__ = (
        Index("images_user_uploaded_idx", "user_id", desc("uploaded_at")),
    )


class Folder(Base):
    """Phase 12 — user-organized folders.

    Folders sit in their own table because they don't have a blob, MIME
    type, or any of the other required image fields — co-mingling would
    make half the columns nullable. Self-FK on `parent_folder_id`
    supports recursive nesting; `ON DELETE CASCADE` removes the whole
    subtree when a parent is deleted.

    `status` / `status_color` mirror the same columns on Image so a
    project label can be applied to either a folder ("Assignment 4 —
    submitted") or a single document.
    """

    __tablename__ = "folders"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_folder_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("folders.id", ondelete="CASCADE"),
        nullable=True,
    )
    parent: Mapped[Optional["Folder"]] = relationship(
        back_populates="children",
        remote_side="Folder.id",
    )
    children: Mapped[list["Folder"]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # Cloud-sync attribution. NULL on every folder the user made
    # manually through the UI; set on the synthesized "Google Drive"
    # root + every subfolder the sync worker mirrored from the
    # provider. Lets `delete_folder` propagate "user said no" back
    # into the sync layer, AND lets `_ensure_remote_folder_tree`
    # recognise an already-deleted synced folder and NOT recreate
    # it on the next sync run.
    cloud_provider: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True,
    )
    cloud_remote_path: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Set when this folder was produced by extracting an archive — kept
    # so we can later offer "re-pack on download" without a separate
    # table mapping folders → archives.
    source_archive_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )

    images: Mapped[list["Image"]] = relationship(back_populates="folder")

    __table_args__ = (
        Index(
            "folders_cloud_path_idx",
            "user_id", "cloud_provider", "cloud_remote_path",
        ),
    )


class Tag(Base):
    """§C1.6 — per-user, optionally colored label.

    Migration 0028 introduced `user_id` (FK CASCADE on `users.id`),
    `color` (chip tint key), and `created_at` / `updated_at`. The
    uniqueness rule is `(user_id, lower(label))` — two users can use
    the same label, and `tag-folder` vs `Tag-Folder` are the same tag
    for one user.
    """

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # 'clip' | 'user'
    color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    image_links: Mapped[list["ImageTag"]] = relationship(back_populates="tag")
    folder_links: Mapped[list["FolderTag"]] = relationship(back_populates="tag")

    __table_args__ = (
        # NB: the case-folded uniqueness is a functional index created
        # in migration 0028 (`tags_user_label_idx`). SQLAlchemy can't
        # express `unique(user_id, lower(label))` directly via UniqueConstraint,
        # so we declare the regular `Index` here just for ORM
        # awareness and rely on the DB-level functional index for
        # enforcement.
        Index("tags_user_label_orm_idx", "user_id", "label"),
    )


class ImageTag(Base):
    __tablename__ = "image_tags"

    image_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("images.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # §C1.6 — denormalized owner so the per-user RLS predicate works
    # without a join. Always equal to `images.user_id` for the linked
    # row; we enforce that at the API boundary, and the FK CASCADE on
    # `users.id` keeps the row from outliving the user.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    image: Mapped[Image] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship(back_populates="image_links")

    __table_args__ = (
        Index("image_tags_tag_idx", "tag_id", "image_id"),
    )


class FolderTag(Base):
    """§C1.6 — many-to-many folder ↔ tag link.

    Mirrors `image_tags` but on folders. `user_id` is denormalized
    for the same RLS reason; FK CASCADE on `folders.id` and
    `tags.id` keeps the table consistent without manual cleanup
    when the user deletes either side.
    """

    __tablename__ = "folder_tags"

    folder_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("folders.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    tag: Mapped[Tag] = relationship(back_populates="folder_links")

    __table_args__ = (
        Index("folder_tags_tag_idx", "tag_id", "folder_id"),
    )


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_text_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    signature_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    centroid_embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vector(512), nullable=True
    )
    face_count: Mapped[int] = mapped_column(nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Face(Base):
    __tablename__ = "faces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(512), nullable=False)
    person_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )
    cluster_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class ImagePerson(Base):
    """Direct image-to-person association independent of face detection.

    The user multi-select tag-as-person flow writes here for every
    selected image — including ones where face detection fired no
    detections. Without this table, an image with no detected face
    has no path back to its owning Person, so the user's "tag these 29
    photos as Me" surfaced 16 in the drill-in (only the ones with
    detected faces). The list_images person filter UNIONs face-derived
    matches with image_persons rows so the per-person count is
    consistent with what the user intended.

    `source='manual'` means the user explicitly tagged the image.
    `source='auto'` is the backfill for historical face-derived rows
    (added in migration 0032) and reserved for future image-level
    inference paths that bypass per-face rows.
    """

    __tablename__ = "image_persons"

    image_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("images.id", ondelete="CASCADE"),
        primary_key=True,
    )
    person_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("persons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="manual"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Comment(Base):
    """§G2 — threaded comments on any file (owner OR active share
    recipient can read/write).

    `anchor_json` is schema-less so new asset types (slides, video,
    etc.) can add new shapes without a migration. Today's shapes:

        {"kind": "image", "x": 0.5, "y": 0.7}     # normalized 0-1
        {"kind": "pdf", "page": 3, "rect": [...]}
        {"kind": "slide", "index": 5}
        {"kind": "video", "t_start": 12.3, "t_end": 15.0}

    NULL `anchor_json` = "general comment on the file" — surfaces in
    the thread panel but draws no pin overlay.

    Soft delete via `deleted_at` so the FE can render "comment
    deleted" placeholders in a thread instead of orphaning replies.
    """

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    image_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("images.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: when an author deletes their account, the comment row
    # stays but `user_id` flips to NULL ("former user"). CASCADE would
    # orphan replies under deleted accounts.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Reply threading. CASCADE so deleting a root deletes its tree.
    parent_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class FaceDetection(Base):
    __tablename__ = "face_detections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    image_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("images.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    bbox_x: Mapped[int] = mapped_column(nullable=False)
    bbox_y: Mapped[int] = mapped_column(nullable=False)
    bbox_w: Mapped[int] = mapped_column(nullable=False)
    bbox_h: Mapped[int] = mapped_column(nullable=False)
    detection_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    face_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("faces.id", ondelete="CASCADE"),
        nullable=True,
    )
    crop_blob_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 5-point RetinaFace landmarks in source-image pixel coordinates:
    # [[lx_eye, ly_eye], [rx_eye, ry_eye], [nose_x, nose_y],
    #  [ml_x, ml_y], [mr_x, mr_y]]. Lets the avatar regenerator re-crop
    # with eye-line framing without re-running detection. See migration
    # 0021.
    landmarks_json: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class BanditState(Base):
    """Per-(user, arm) sufficient statistics for disjoint LinUCB.

    `a_matrix` is a d×d float32 matrix; `b_vector` is a d float32 vector.
    Stored as raw `bytes` (numpy.tobytes); decode with `numpy.frombuffer`.
    """

    __tablename__ = "bandit_state"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    arm_id: Mapped[int] = mapped_column(primary_key=True)
    a_matrix: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    b_vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    pulls: Mapped[int] = mapped_column(nullable=False, server_default="0")
    last_updated: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class BanditGlobalPrior(Base):
    """Cold-start prior keyed by arm. Bootstrapped offline (Phase 5
    deliverable: synthetic SSIM/LPIPS-based ratings on a fixture set)."""

    __tablename__ = "bandit_global_prior"

    arm_id: Mapped[int] = mapped_column(primary_key=True)
    a_matrix: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    b_vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    pulls: Mapped[int] = mapped_column(nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class FeedbackEvent(Base):
    """Append-only reward signals for the LinUCB trainer.

    Reward, arm_id, and context_features are denormalized at ingest time so
    the trainer's consume loop does no JOINs. `consumed_by_trainer=false`
    rows are picked up on the next trainer pass; idempotent because the
    flag flips on success.
    """

    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("images.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'rating' (explicit), 'implicit_negative', 'implicit_positive'
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    rating: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    bandit_arm_id: Mapped[int] = mapped_column(nullable=False)
    context_features: Mapped[list[float]] = mapped_column(
        ARRAY(Float), nullable=False
    )
    reward: Mapped[float] = mapped_column(Float, nullable=False)
    consumed_by_trainer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CloudLink(Base):
    """C2 — third-party storage link (Drive / GitHub / Dropbox / …).

    `encrypted_refresh_token` is a placeholder for the eventual
    A2/A3-gated implementation: today the column stores a plaintext
    sentinel value (`"PLACEHOLDER"`); the OAuth callback that fills it
    is intentionally a stub until secrets-at-rest is in place. Don't
    ship to production with this column unencrypted.
    """

    __tablename__ = "cloud_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scopes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # §C2 — per-link AI opt-in. Defaults to False so cloud sources stay
    # outside the training pipeline by default (Google Drive Limited
    # Use compliance + the principle that AI is opt-in everywhere). The
    # /cloud/links/{id}/ai-opt-in endpoint flips this AND the
    # `skip_ai_training` flag on every image from this source, so the
    # FE reads its toggle state from here on a fresh page load instead
    # of having to infer it by looking at a sampled image's flag.
    ai_opted_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CloudFile(Base):
    """C2 — diff index for synced files. `local_image_id` is NULL until
    the corresponding image is downloaded and stored locally.
    `remote_modified` + `sha256` together drive the "should we re-pull
    this file" decision."""

    __tablename__ = "cloud_files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_id: Mapped[str] = mapped_column(Text, nullable=False)
    remote_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # §C2 — slash-separated parent folder path the file lived under at
    # ingest time. Used by the sync worker to materialize a matching
    # neuthek folder tree under the per-provider root ("Google Drive").
    # NULL means "remote root" / "unfileable" (e.g. shared-with-me).
    remote_parent_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    local_image_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("images.id", ondelete="SET NULL"),
        nullable=True,
    )
    remote_modified: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    sha256: Mapped[Optional[bytes]] = mapped_column(LargeBinary(32), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Tombstone for user-driven deletion of a synced file. When the
    # user deletes the local Image attached to this row, we stamp
    # this column. Sync skips any cloud_files entry with
    # `excluded_at IS NOT NULL` so the file doesn't re-ingest on
    # the next walk. Without this, every soft- or hard-delete was
    # undone by the next sync run (the "I deleted it 20 times and
    # it keeps coming back" bug — migration 0039).
    excluded_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("cloud_files_user_provider_remote_uq", "user_id", "provider", "remote_id", unique=True),
        Index("cloud_files_excluded_at_idx", "user_id", "provider", "excluded_at"),
    )


class SystemConfig(Base):
    """Admin-managed configuration (key/value), values stored Fernet-
    ciphertext only. Used for Google OAuth client credentials so the
    admin can paste them in the UI instead of editing .env, plus the
    cloud encryption key itself (auto-generated on first need)."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class ImageGeo(Base):
    """C3 — image GPS coordinates extracted from EXIF.

    Sibling table rather than columns on `images` so a clean delete is
    one statement when the user revokes `gps_retention` consent. PK is
    image_id (1:1) — every coordinate belongs to exactly one image.
    `taken_at` stores the EXIF capture timestamp when present; useful
    for time-aware map clustering later.
    """

    __tablename__ = "image_geo"

    image_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("images.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    taken_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    captured_with: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    # Human-readable location (e.g. "Big Sur, California"). Filled lazily
    # by `POST /images/geo/backfill-places`, which calls Nominatim with
    # a polite 1-rps rate limit and caches by rounded coords.
    place: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class RecoveryCode(Base):
    """Phase 13 (C6) — single-use account-recovery codes.

    Generated 8 at a time when the user opts into recovery codes. Codes
    are hashed (argon2) — only the user ever sees the plaintext. A code
    is consumed by writing `used_at` and is then dead permanently. We
    never re-use codes; "regenerate" deletes the prior set.
    """

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class ShareGrant(Base):
    """Per-image share grant (todo §1.1 / G1).

    A row represents one (image, recipient_email) pairing. The plaintext
    share token is shown to the sharer exactly once (at create time) and
    stored as an argon2 hash; the token URL the sharer copies takes the
    form `{frontend_base_url}/share/{plaintext_token}`.

    The recipient is identified by **email** rather than by user id so
    sharing works before the recipient has signed up. On first successful
    claim:
      - `recipient_user_id` is bound to the claiming user.
      - `expires_at` is set: for an existing-user recipient that's
        already done at create time using the sharer-chosen duration;
        for a brand-new recipient it's `now() + 1 day` regardless of
        what the sharer requested (deliberate cap so an unverified
        identity never gets long-lived access).

    `revoked_at` is the soft-delete column — rows are never DELETEd in
    normal operation so the audit trail (`AuditLog` rows referencing
    `share_id` in `details`) stays meaningful.
    """

    __tablename__ = "share_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    image_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("images.id", ondelete="CASCADE"),
        nullable=False,
    )
    sharer_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    recipient_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    permission: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="view_download"
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    sharer_duration_seconds: Mapped[int] = mapped_column(nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "share_grants_recipient_user_active_idx",
            "recipient_user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "share_grants_pending_email_idx",
            "recipient_email",
            postgresql_where=text("recipient_user_id IS NULL"),
        ),
        Index("share_grants_image_idx", "image_id", "revoked_at"),
        Index(
            "share_grants_image_recipient_uq",
            "image_id",
            "recipient_email",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class WorkerHeartbeat(Base):
    """C8.2 — per-worker liveness signal.

    Background workers (`backend/worker/main.py` and friends) upsert a
    row here every 30 s with their identifier, kind, hostname, pid, and
    an arbitrary metadata JSONB blob (e.g. last job kind processed,
    queue depth observed). The admin Processes tab unions psutil rows
    with these heartbeats so containers running in a sibling namespace
    are visible from the API side. A worker is considered "alive" when
    `last_seen` is within ~120 s of now (4× the heartbeat interval)."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    hostname: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pid: Mapped[Optional[int]] = mapped_column(nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extra_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True,
    )


class ModelRun(Base):
    """C8.2 — ml-worker reports model load/unload/error transitions.

    Latest row per (model_id, worker_id) wins for "current state"
    queries; older rows act as an audit trail of what was loaded when.
    Memory numbers come from torch.cuda.memory_allocated when the
    model lives on CUDA, otherwise null. `device` is the torch device
    string ('cuda:0', 'cpu', 'xpu', 'npu', 'mps') so the dashboard can
    show "Florence-2 on cuda:0" or "Qwen on cpu" without inferring."""

    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_id: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    device: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    memory_allocated_bytes: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    extra_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True,
    )


class NotificationPref(Base):
    """§1.2.3 — per-user notification opt-in matrix.

    Composite PK (user_id, kind, channel) — a single row per
    (audience, event-kind, delivery-channel). Rows missing from the
    table default to:
      - True  for kind='security_alerts'  (operator can't silence
              security mail without an explicit opt-out)
      - False for everything else.
    The default-true case is enforced in `backend/api/account.py`
    when loading, not at the column level — that keeps the table
    sparse (most users never visit the Notifications tab and have
    zero rows).
    """

    __tablename__ = "notification_prefs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # 'product_updates' | 'security_alerts' | 'account_activity' |
    # 'storage_warnings' — extend as we add more notification types.
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    # 'email' | 'in_app'. 'push' will land when we wire the service
    # worker + Push API in a future pass.
    channel: Mapped[str] = mapped_column(String(16), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


# ----- Stripe billing (migration 0025) -----


class Plan(Base):
    """Seeded tier catalog (free / pro / business). Stripe Price IDs
    live in env vars; the row records the env-var name so the runtime
    can pull the live ID without a migration when Prices rotate."""

    __tablename__ = "plans"

    tier: Mapped[str] = mapped_column(String(16), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    monthly_cents: Mapped[Optional[int]] = mapped_column(nullable=True)
    annual_cents: Mapped[Optional[int]] = mapped_column(nullable=True)
    monthly_price_id_env: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    annual_price_id_env: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    upload_max_per_hour: Mapped[int] = mapped_column(nullable=False)
    upload_max_bytes_per_day: Mapped[int] = mapped_column(BigInteger, nullable=False)
    features: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    sort_order: Mapped[int] = mapped_column(nullable=False, server_default="0")


class Subscription(Base):
    """One row per user (PK = user_id). Tier flips and interval swaps
    (monthly ↔ annual) update this row in place — Stripe issues a new
    subscription_id on plan change, so the latest ID overwrites the
    previous."""

    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False, server_default="free")
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="active")
    # 'month' | 'year' — NULL on the free seed row.
    interval: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_period_start: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class StripeEvent(Base):
    """Webhook idempotency table. `id` is the Stripe `evt_…` id;
    presence means we've seen the event. `processed_at` flips once
    the handler completes — a row with NULL processed_at is a
    half-handled event that the next delivery can re-attempt."""

    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
