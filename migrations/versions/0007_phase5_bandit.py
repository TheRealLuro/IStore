"""phase 5 LinUCB bandit state

Adds:
  - bandit_state: per-(user, arm) sufficient statistics A and b for disjoint LinUCB
  - bandit_global_prior: shared cold-start prior keyed by arm_id
  - images.bandit_arm_id: which arm produced this image's encoding
  - images.context_features: real[] for replay & debugging

The (A, b) pair is stored as raw float32 BYTEA — A is d×d (1024 floats at d=32),
b is d (32 floats). Decoded application-side via numpy.frombuffer.

Revision ID: 0007_phase5_bandit
Revises: 0006_phase4_biometric
Create Date: 2026-05-02 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_phase5_bandit"
down_revision: Union[str, None] = "0006_phase4_biometric"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bandit_state",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("arm_id", sa.Integer(), nullable=False),
        sa.Column("a_matrix", sa.LargeBinary(), nullable=False),
        sa.Column("b_vector", sa.LargeBinary(), nullable=False),
        sa.Column("pulls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_updated",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "arm_id", name="bandit_state_pk"),
    )
    op.create_index("bandit_state_user_idx", "bandit_state", ["user_id"])

    op.create_table(
        "bandit_global_prior",
        sa.Column("arm_id", sa.Integer(), primary_key=True),
        sa.Column("a_matrix", sa.LargeBinary(), nullable=False),
        sa.Column("b_vector", sa.LargeBinary(), nullable=False),
        sa.Column("pulls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.add_column(
        "images",
        sa.Column("bandit_arm_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "images",
        sa.Column(
            "context_features",
            postgresql.ARRAY(sa.Float()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("images", "context_features")
    op.drop_column("images", "bandit_arm_id")
    op.drop_table("bandit_global_prior")
    op.drop_index("bandit_state_user_idx", table_name="bandit_state")
    op.drop_table("bandit_state")
