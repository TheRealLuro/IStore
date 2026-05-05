"""C3 — image_geo sibling table for EXIF GPS coordinates

Lat/lng (and taken_at, captured_with) live in their own table rather
than on `images` so revoking `gps_retention` consent is a single DELETE
without ALTER TABLE-style column nulling. PK image_id makes the join
trivial and ON DELETE CASCADE keeps us aligned with image deletion.

Revision ID: 0013_image_geo
Revises: 0012_admin_quota
Create Date: 2026-05-04 17:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_image_geo"
down_revision: Union[str, None] = "0012_admin_quota"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "image_geo",
        sa.Column(
            "image_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column(
            "taken_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("captured_with", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Per-user lookup is the access pattern (`GET /images/geo` filters by
    # user_id). Composite (user_id, lat, lng) helps future bbox queries.
    op.create_index("image_geo_user_idx", "image_geo", ["user_id"])
    op.create_index(
        "image_geo_user_bbox_idx", "image_geo", ["user_id", "lat", "lng"]
    )


def downgrade() -> None:
    op.drop_index("image_geo_user_bbox_idx", table_name="image_geo")
    op.drop_index("image_geo_user_idx", table_name="image_geo")
    op.drop_table("image_geo")
