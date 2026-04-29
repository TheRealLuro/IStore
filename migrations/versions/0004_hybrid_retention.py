"""hybrid retention (option D)

Adds original_expires_at and makes original_blob_key nullable so the retention
sweeper can drop original blobs after 30 days while keeping the served variant.

Revision ID: 0004_hybrid_retention
Revises: 0003_vision_columns
Create Date: 2026-04-29 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_hybrid_retention"
down_revision: Union[str, None] = "0003_vision_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("images", "original_blob_key", existing_type=sa.Text(), nullable=True)
    op.add_column(
        "images",
        sa.Column(
            "original_expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            server_default=sa.text("now() + interval '30 days'"),
        ),
    )
    op.execute(
        "UPDATE images SET original_expires_at = uploaded_at + interval '30 days' "
        "WHERE original_expires_at IS NULL AND original_blob_key IS NOT NULL"
    )
    op.create_index(
        "images_original_expires_idx",
        "images",
        ["original_expires_at"],
        postgresql_where=sa.text("original_blob_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("images_original_expires_idx", table_name="images")
    op.drop_column("images", "original_expires_at")
    op.alter_column("images", "original_blob_key", existing_type=sa.Text(), nullable=False)
