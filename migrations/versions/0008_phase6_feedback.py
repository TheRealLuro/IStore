"""phase 6 feedback events

Adds the append-only `feedback_events` table that the trainer consumes to
update bandit_state. Reward + context are denormalized onto the row at
ingest time (copied from `images`) so the trainer doesn't need to JOIN —
that keeps the consume loop O(N) over fresh rows and idempotent on replay.

Revision ID: 0008_phase6_feedback
Revises: 0007_phase5_bandit
Create Date: 2026-05-02 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0008_phase6_feedback"
down_revision: Union[str, None] = "0007_phase5_bandit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback_events",
        sa.Column(
            "id", sa.BigInteger(), primary_key=True, autoincrement=True
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "image_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=True),
        sa.Column(
            "weight", sa.Float(), nullable=False, server_default="1.0"
        ),
        sa.Column("bandit_arm_id", sa.Integer(), nullable=False),
        sa.Column(
            "context_features",
            postgresql.ARRAY(sa.Float()),
            nullable=False,
        ),
        sa.Column("reward", sa.Float(), nullable=False),
        sa.Column(
            "consumed_by_trainer",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Partial index speeds up the trainer's `WHERE consumed_by_trainer = false`
    # poll — index size stays bounded by the unconsumed backlog, not the
    # full event history.
    op.create_index(
        "feedback_events_unprocessed_idx",
        "feedback_events",
        ["created_at"],
        postgresql_where=sa.text("consumed_by_trainer = false"),
    )
    op.create_index(
        "feedback_events_user_image_idx",
        "feedback_events",
        ["user_id", "image_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "feedback_events_user_image_idx", table_name="feedback_events"
    )
    op.drop_index(
        "feedback_events_unprocessed_idx", table_name="feedback_events"
    )
    op.drop_table("feedback_events")
