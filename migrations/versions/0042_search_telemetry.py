"""Search-score telemetry for empirical blend-weight tuning.

Sprint I D3 — to tune the CLIP/FTS blend weights (currently
0.70/0.30) from real usage instead of hand-guessing, log per-search:
the query, the top-10 result ids + their blend scores, and the
weights in effect at the time. Diffing recall against the held-out
eval set (backend/eval/recall_at_5.py) tells us if a weight change
helped; this table tells us WHICH real queries are underperforming.

Strictly consent-gated: rows are only written when the user has an
active `bandit_compression_telemetry` consent scope (the same scope
that governs the LinUCB compression reward signals). No query text
is logged for users who haven't opted in.

Schema:
  - id            uuid pk
  - user_id       uuid fk → users.id ON DELETE CASCADE (dies with
                  the account; RLS-scoped)
  - query         text — the cleaned query string
  - top_results   jsonb — [{"image_id": "...", "score": 0.66}, ...]
                  capped at 10 entries
  - weights       jsonb — {"clip": 0.70, "text": 0.30}
  - result_count  int — total results returned (after all gates)
  - created_at    timestamptz default now()

Index on (user_id, created_at) for the operator's "recent searches"
analytics query. RLS + FORCE on user_id like the rest of the schema.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0042_search_telemetry"
down_revision: Union[str, None] = "0041_document_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_telemetry",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column(
            "top_results",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "weights",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_search_telemetry_user_created",
        "search_telemetry",
        ["user_id", "created_at"],
    )

    op.execute("ALTER TABLE search_telemetry ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE search_telemetry FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY search_telemetry_user_isolation ON search_telemetry "
        "FOR ALL "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid) "
        "WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS search_telemetry_user_isolation ON search_telemetry")
    op.drop_index("ix_search_telemetry_user_created", table_name="search_telemetry")
    op.drop_table("search_telemetry")
