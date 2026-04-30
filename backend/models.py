import uuid
from datetime import datetime
from typing import Optional

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    TIMESTAMP,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

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
    mime_type_original: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    mime_type_served: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sha256: Mapped[Optional[bytes]] = mapped_column(LargeBinary(32), nullable=True)

    codec: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    quality: Mapped[Optional[int]] = mapped_column(nullable=True)
    max_dim: Mapped[Optional[int]] = mapped_column(nullable=True)
    lossless: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Phase 2 vision columns.
    clip_embedding: Mapped[Optional[list[float]]] = mapped_column(
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

    uploaded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
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

    __table_args__ = (
        Index("images_user_uploaded_idx", "user_id", desc("uploaded_at")),
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # 'clip' | 'user'

    image_links: Mapped[list["ImageTag"]] = relationship(back_populates="tag")


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
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    image: Mapped[Image] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship(back_populates="image_links")

    __table_args__ = (
        Index("image_tags_tag_idx", "tag_id", "image_id"),
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
    ip: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(
        "details", Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
