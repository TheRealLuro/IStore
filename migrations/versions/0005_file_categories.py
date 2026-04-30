"""file categories + thumbnail blob

Revision ID: 0005_file_categories
Revises: 0004_hybrid_retention
Create Date: 2026-04-29 00:01:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_file_categories"
down_revision: Union[str, None] = "0004_hybrid_retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "category",
            sa.String(length=16),
            nullable=False,
            server_default="image",
        ),
    )
    op.add_column(
        "images",
        sa.Column("thumbnail_blob_key", sa.Text(), nullable=True),
    )
    op.create_index("images_user_category_idx", "images", ["user_id", "category"])


def downgrade() -> None:
    op.drop_index("images_user_category_idx", table_name="images")
    op.drop_column("images", "thumbnail_blob_key")
    op.drop_column("images", "category")
