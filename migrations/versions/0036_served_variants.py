"""Per-image served-variants map (video / audio multi-quality).

`served_variants` is a JSONB column on `images` mapping quality
label → blob key in the served bucket. For video / audio:

    {
      "1080p": "users/<uid>/served/<hex>/1080p.mp4",
      "720p":  "users/<uid>/served/<hex>/720p.mp4",
      "480p":  "users/<uid>/served/<hex>/480p.mp4"
    }

The single `served_blob_key` column still points at the default
tier (highest available) so existing read paths keep working
unchanged. The streaming endpoint reads `served_variants[q]` when
the player asks for a specific quality and falls back to
`served_blob_key` when q is omitted or unrecognized.

Image rows never populate this column — only video / audio.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0036_served_variants"
down_revision: Union[str, None] = "0035_widen_mime_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "served_variants",
            JSONB(),
            nullable=True,
            comment="quality_label -> served-bucket blob key (video/audio only)",
        ),
    )


def downgrade() -> None:
    op.drop_column("images", "served_variants")
