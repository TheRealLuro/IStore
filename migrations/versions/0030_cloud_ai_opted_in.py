"""Persist per-link AI opt-in on cloud_links.

Adds `cloud_links.ai_opted_in` so the Cloud sync panel can read its
toggle state on a fresh page load instead of relying on per-session
in-memory state (which evaporated on refresh — the user-reported
"AI enable doesn't stay enabled" bug).

The /cloud/links/{id}/ai-opt-in handler flips this column alongside
the per-image `skip_ai_training` flag inside the same transaction,
so the column is the authoritative source for the UI.

Revision ID: 0030_cloud_ai_opted_in
Revises: 0029_c2_cloud_sync_metadata
"""
from alembic import op
import sqlalchemy as sa


revision = "0030_cloud_ai_opted_in"
down_revision = "0029_c2_cloud_sync_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_links",
        sa.Column(
            "ai_opted_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("cloud_links", "ai_opted_in")
