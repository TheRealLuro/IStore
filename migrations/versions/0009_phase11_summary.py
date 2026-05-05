"""phase 11 ai vision summaries

Adds per-file content summary columns + a pg_trgm GIN index for fast
substring search over the summary text. Populated asynchronously by the
new `backend/summarize.py` BackgroundTask after upload, same pattern as
Pass B face scans (`pending_face_scan`).

Revision ID: 0009_phase11_summary
Revises: 0008_phase6_feedback
Create Date: 2026-05-03 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009_phase11_summary"
down_revision: Union[str, None] = "0008_phase6_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column("images", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column(
        "images", sa.Column("summary_topic", sa.Text(), nullable=True)
    )
    op.add_column(
        "images",
        sa.Column(
            "summary_points", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.add_column(
        "images",
        sa.Column(
            "pending_summary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "images",
        sa.Column(
            "summary_generated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )

    # Backlog of work for the summarizer — same shape as the pending_face_scan
    # partial index so a single `WHERE pending_summary = true` poll is cheap.
    op.create_index(
        "images_pending_summary_idx",
        "images",
        ["uploaded_at"],
        postgresql_where=sa.text(
            "pending_summary = true AND deleted_at IS NULL"
        ),
    )

    # Trigram index for ILIKE / similarity search on summary text. Used
    # by an optional Phase 11.5 server-side `?content=` search param;
    # client-side Fuse.js doesn't need this, but having it ready keeps the
    # cost of that follow-up at zero.
    op.execute(
        "CREATE INDEX images_summary_trgm "
        "ON images USING gin (summary gin_trgm_ops) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS images_summary_trgm")
    op.drop_index("images_pending_summary_idx", table_name="images")
    op.drop_column("images", "summary_generated_at")
    op.drop_column("images", "pending_summary")
    op.drop_column("images", "summary_points")
    op.drop_column("images", "summary_topic")
    op.drop_column("images", "summary")
