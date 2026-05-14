"""Summary FTS index — generated tsvector + GIN

Adds a stored generated column `images.summary_tsv` that is the
`to_tsvector('english', ...)` of summary + topic + points + filename,
plus a GIN index on it. Hybrid search in `backend/api/search.py` can
then run FTS without computing the tsvector inline at query time
(O(rows) vs O(1) lookup once the index is warm).

The search code currently builds the tsvector inline and works without
this migration; the migration is purely a perf improvement that the
query planner picks up automatically once present (Postgres recognizes
the matching expression).

Revision ID: 0017_summary_fts
Revises: 0016_section_a_hardening
Create Date: 2026-05-09 10:00:00
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0017_summary_fts"
down_revision: Union[str, None] = "0016_section_a_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Generated column expression. Must match the haystack built by
# `backend.api.search._build_haystack` so the planner can use the index
# for the inline tsvector query.
#
# `summary_points` is stored as `jsonb` (a JSON array of strings), so we
# can't pass it to `array_to_string()` — that function only accepts SQL
# arrays. Casting jsonb to text yields `["a","b"]` literal text; the
# tsvector tokenizer strips brackets/quotes/commas so `a` and `b` still
# index as separate lexemes. That's good enough for FTS without spinning
# up a `jsonb_array_elements_text(...) | string_agg(...)` subquery, which
# Postgres won't allow inside a STORED generated column anyway (must be
# IMMUTABLE; subqueries break immutability).
_HAYSTACK_EXPR = (
    "to_tsvector('english', "
    "coalesce(summary, '') || ' ' || "
    "coalesce(summary_topic, '') || ' ' || "
    "coalesce(summary_points::text, '') || ' ' || "
    "coalesce(original_filename, '')"
    ")"
)


def upgrade() -> None:
    # Postgres 12+ supports stored generated columns. We use STORED so the
    # GIN index can reference it directly (functional GIN indexes work
    # too, but a generated column makes the EXPLAIN output legible and
    # lets us inspect the tsvector for individual rows).
    op.execute(
        f"""
        ALTER TABLE images
        ADD COLUMN summary_tsv tsvector
        GENERATED ALWAYS AS ({_HAYSTACK_EXPR}) STORED
        """
    )
    op.execute(
        "CREATE INDEX ix_images_summary_tsv ON images USING GIN (summary_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_images_summary_tsv")
    op.execute("ALTER TABLE images DROP COLUMN IF EXISTS summary_tsv")
