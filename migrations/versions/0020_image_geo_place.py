"""image_geo.place column for reverse-geocoded location names

Backs §1.3 (was tracked in todo.md as "Map: pins still bare lat/lng").
A background worker calls Nominatim to fill this column with a short
display name (e.g. "Big Sur, California") and the Map popup reads it
instead of bare coords.

Revision ID: 0020_image_geo_place
Revises: 0019_image_summary_signals
Create Date: 2026-05-14 15:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_image_geo_place"
down_revision: Union[str, None] = "0019_image_summary_signals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "image_geo",
        sa.Column("place", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("image_geo", "place")
