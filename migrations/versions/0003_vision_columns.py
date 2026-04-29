"""phase 2 vision columns

Revision ID: 0003_vision_columns
Revises: 0002_phase1_users_images
Create Date: 2026-04-28 00:02:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "0003_vision_columns"
down_revision: Union[str, None] = "0002_phase1_users_images"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column("lossless", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column("clip_embedding", Vector(768), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column("content_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column("content_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column("scene_label", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column("scene_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column("face_likelihood", sa.Float(), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column(
            "pending_face_scan",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "images",
        sa.Column("indoor_outdoor", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column("vision_processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "tags",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.UniqueConstraint("label", name="tags_label_unique"),
    )
    op.create_table(
        "image_tags",
        sa.Column(
            "image_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.BigInteger(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.create_index("image_tags_tag_idx", "image_tags", ["tag_id", "image_id"])

    op.execute(
        "CREATE INDEX images_clip_embedding_idx ON images "
        "USING hnsw (clip_embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX images_scene_idx ON images (user_id, scene_label) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS images_scene_idx")
    op.execute("DROP INDEX IF EXISTS images_clip_embedding_idx")
    op.drop_index("image_tags_tag_idx", table_name="image_tags")
    op.drop_table("image_tags")
    op.drop_table("tags")
    op.drop_column("images", "vision_processed_at")
    op.drop_column("images", "indoor_outdoor")
    op.drop_column("images", "pending_face_scan")
    op.drop_column("images", "face_likelihood")
    op.drop_column("images", "scene_confidence")
    op.drop_column("images", "scene_label")
    op.drop_column("images", "content_confidence")
    op.drop_column("images", "content_type")
    op.drop_column("images", "clip_embedding")
    op.drop_column("images", "lossless")
