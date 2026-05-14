"""Image summary_signals JSONB column

Captures the structured signals produced by the C2 multi-model image
pipeline (Florence-2 dense-region phrases, Florence-2 OD labels,
OpenCLIP concept tags, optional InternVL2-4B rich description). Stored
as a single JSONB blob so re-summarization is idempotent and the new
C9 multi-axis filter can query individual axes via PG JSONB path
operators when it ships.

Shape:
  {
    "regions": ["a kettle on the floor", ...],
    "objects": ["kettle", "person", "whiteboard", ...],
    "concepts": ["golden hour", "candid", "classroom", ...],
    "vlm": "Rich VLM paragraph..."  // only when heavy_vlm_enabled
  }

Any subset may be missing — each pipeline stage is best-effort and
returns None on failure.

Revision ID: 0019_image_summary_signals
Revises: 0018_image_starred
Create Date: 2026-05-14 14:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0019_image_summary_signals"
down_revision: Union[str, None] = "0018_image_starred"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column("summary_signals", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("images", "summary_signals")
