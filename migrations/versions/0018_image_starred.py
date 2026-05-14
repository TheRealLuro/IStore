"""Image is_starred column + starred_at + partial index

Adds the backend representation behind the Preview Star button. Previously
the FE persisted starred state to `localStorage["neuthek.starred"]`, which
didn't sync across devices and had no representation in search /
filtering. Two columns:

  is_starred boolean NOT NULL DEFAULT false
  starred_at timestamptz NULL

`starred_at` is set on the OFF→ON transition only. Un-starring keeps the
prior timestamp so a re-star preserves "starred X days ago" history. A
partial index keyed on `(user_id, starred_at DESC) WHERE is_starred`
makes "starred only" listings cheap without bloating the index on
mostly-false rows.

Revision ID: 0018_image_starred
Revises: 0017_summary_fts
Create Date: 2026-05-13 11:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_image_starred"
down_revision: Union[str, None] = "0017_summary_fts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "is_starred",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "images",
        sa.Column(
            "starred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.execute(
        """
        CREATE INDEX ix_images_user_starred
          ON images (user_id, starred_at DESC)
          WHERE is_starred = true
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_images_user_starred")
    op.drop_column("images", "starred_at")
    op.drop_column("images", "is_starred")
