"""Per-image CLIP text-space embedding of the AI summary.

The image-side `clip_embedding` already gives CLIP visual semantics.
Adding a separate embedding of the SUMMARY in CLIP text space gives
text-to-text semantic matching at search time:

    score = max(cos(query, image_emb), cos(query, summary_emb))

For natural-language queries whose vocabulary doesn't match what the
image visually contains but DOES match what the summary describes
(e.g. "place where I had coffee" matching a summary that says
"cafe table with espresso"), the summary embedding bridges that
gap on its own — search no longer has to rely on CLIP visual cosine
to pull the right answer.

OpenCLIP ViT-L-14 text encoder produces 768-dim vectors. We use
HNSW with cosine_ops (same shape as the existing
`images_clip_embedding_idx`) so the search query path can use
either index with the same `<=>` distance operator.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "0037_summary_clip_embedding"
down_revision: Union[str, None] = "0036_served_variants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "summary_clip_embedding",
            Vector(768),
            nullable=True,
            comment="CLIP text-space embedding of `summary` for text-to-text semantic search",
        ),
    )
    # HNSW index for fast cosine-ANN search. Same parameters as the
    # image-side index so query-time tuning (ef_search, etc.) applies
    # to both uniformly.
    op.execute(
        "CREATE INDEX images_summary_clip_emb_idx ON images "
        "USING hnsw (summary_clip_embedding vector_cosine_ops) "
        "WITH (m='16', ef_construction='64')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS images_summary_clip_emb_idx")
    op.drop_column("images", "summary_clip_embedding")
