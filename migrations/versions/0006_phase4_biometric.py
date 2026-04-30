"""phase 4 biometric pipeline

Adds:
  - consent_records (append-only consent log)
  - persons (per-user named people)
  - faces (per-user face embeddings, vector(512))
  - face_detections (occurrences of a face in an image)
  - audit_log (append-only audit trail for biometric ops)

All biometric tables are scoped to user_id and meant to be filtered with
WHERE user_id = $1 in v1; row-level security is a Phase 9 hardening item.

Revision ID: 0006_phase4_biometric
Revises: 0005_file_categories
Create Date: 2026-04-30 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "0006_phase4_biometric"
down_revision: Union[str, None] = "0005_file_categories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Append-only consent records.
    op.create_table(
        "consent_records",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consent_kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),  # GRANTED|WITHDRAWN
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("policy_text_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("signature_text", sa.Text(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "consent_records_user_kind_time_idx",
        "consent_records",
        ["user_id", "consent_kind", sa.text("granted_at DESC")],
    )

    # Persons: per-user named clusters of faces.
    op.create_table(
        "persons",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("centroid_embedding", Vector(512), nullable=True),
        sa.Column("face_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "display_name", name="persons_user_name_unique"),
    )
    op.create_index("persons_user_idx", "persons", ["user_id"])

    # Faces: per-user face embeddings.
    op.create_table(
        "faces",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("persons.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cluster_id", sa.Integer(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("faces_user_person_idx", "faces", ["user_id", "person_id"])
    op.execute(
        "CREATE INDEX faces_embedding_idx ON faces "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # Face detections: occurrences in images.
    op.create_table(
        "face_detections",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "image_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bbox_x", sa.Integer(), nullable=False),
        sa.Column("bbox_y", sa.Integer(), nullable=False),
        sa.Column("bbox_w", sa.Integer(), nullable=False),
        sa.Column("bbox_h", sa.Integer(), nullable=False),
        sa.Column("detection_confidence", sa.Float(), nullable=True),
        sa.Column(
            "face_id",
            sa.BigInteger(),
            sa.ForeignKey("faces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("crop_blob_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "face_detections_user_image_idx",
        "face_detections",
        ["user_id", "image_id"],
    )

    # Append-only audit log for biometric operations.
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("audit_log_user_time_idx", "audit_log", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("audit_log_user_time_idx", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("face_detections_user_image_idx", table_name="face_detections")
    op.drop_table("face_detections")
    op.execute("DROP INDEX IF EXISTS faces_embedding_idx")
    op.drop_index("faces_user_person_idx", table_name="faces")
    op.drop_table("faces")
    op.drop_index("persons_user_idx", table_name="persons")
    op.drop_table("persons")
    op.drop_index("consent_records_user_kind_time_idx", table_name="consent_records")
    op.drop_table("consent_records")
