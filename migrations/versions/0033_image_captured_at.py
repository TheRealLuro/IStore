"""§C9 follow-up — add images.captured_at for EXIF DateTimeOriginal,
independent of GPS consent.

The C9 date-range filter wants the actual EXIF capture date so users
can filter "everything from January" by when the photo was taken, not
when it was uploaded. Today the capture timestamp only lives on
image_geo.taken_at, which only exists when the user has granted
gps_retention consent AND the EXIF carried GPS coordinates. A user
who turns off location retention (or whose photos have no GPS) gets
no capture date at all.

This migration:
  - Adds a nullable images.captured_at column (TIMESTAMP WITH TZ).
  - Backfills it from image_geo.taken_at for any existing row that
    has one, so the filter "just works" for users who'd already had
    GPS-bearing uploads.
  - Indexes captured_at descending so the typical "newest first by
    capture date" query stays a single index scan.

Population path: backend/image.py extracts DateTimeOriginal in a new
helper that runs whenever exif_retention is granted — independent of
gps_retention. list_images and /images/facets COALESCE captured_at
> image_geo.taken_at > uploaded_at so the filter prefers EXIF when
available and falls back gracefully.

Revision ID: 0033_image_captured_at
Revises: 0032_image_persons
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0033_image_captured_at"
down_revision: Union[str, None] = "0032_image_persons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "captured_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_images_captured_at",
        "images",
        [sa.text("captured_at DESC NULLS LAST")],
    )
    # Backfill from image_geo.taken_at — same column, same semantics,
    # just moved to a non-GPS-gated location.
    op.execute(
        """
        UPDATE images
        SET captured_at = ig.taken_at
        FROM image_geo ig
        WHERE ig.image_id = images.id
          AND ig.taken_at IS NOT NULL
          AND images.captured_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_images_captured_at", table_name="images")
    op.drop_column("images", "captured_at")
