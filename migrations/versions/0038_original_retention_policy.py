"""Per-user original-retention policy.

`users.original_retention_policy` is a short text enum picked by the
user that controls how long their uploaded ORIGINALS are kept on
disk. The compressed served copy is unaffected; this only governs
the bytes in the `originals` bucket (and the `original_blob_key`
column).

Values + semantics:

    "immediate"   `original_expires_at = now()` at upload time —
                  the retention sweeper drops the original on its
                  next tick (within 24 h). Reduces storage cost to
                  the served copy alone.
    "24h"         `original_expires_at = now() + 1d`. Useful as a
                  "AI features finish quickly, then I want the
                  space back" buffer.
    "7d"          `original_expires_at = now() + 7d`. Tight but
                  leaves a week for re-summarizes / re-detect.
    "30d"         `original_expires_at = now() + 30d` — the legacy
                  default, kept as the no-op for existing accounts.
    "forever"     `original_expires_at = NULL`. The sweeper never
                  touches it; user trades quota for permanence.

The default is '30d' so existing users see no behaviour change
until they explicitly opt into something else.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0038_original_retention_policy"
down_revision: Union[str, None] = "0037_summary_clip_embedding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "original_retention_policy",
            sa.Text(),
            nullable=False,
            server_default="30d",
            comment="immediate | 24h | 7d | 30d | forever",
        ),
    )
    # Defensive CHECK constraint: the application validates input but
    # a stray UPDATE shouldn't be able to set this to garbage. The
    # constraint stays in sync with the application enum by exact
    # string match — adding a new option means a follow-up migration.
    op.create_check_constraint(
        "users_original_retention_policy_chk",
        "users",
        "original_retention_policy IN ('immediate','24h','7d','30d','forever')",
    )


def downgrade() -> None:
    op.drop_constraint("users_original_retention_policy_chk", "users", type_="check")
    op.drop_column("users", "original_retention_policy")
