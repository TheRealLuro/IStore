"""Per-folder aggregate CLIP embedding for semantic folder search (D4).

A folder's `summary_clip_embedding` is an L2-normalised blend of:
  - the folder NAME encoded in CLIP text space, and
  - the centroid of its descendant images' `summary_clip_embedding`
    (the text-space embeddings of those items' AI summaries).

That lets a query like "the trip to Mexico" match the FOLDER whose
photos' summaries talk about Mexico — not just the photos themselves —
even when the folder name is only "2023". Computed in
backend/folder_embed.py, cached here, recomputed when children are
added / moved / removed or a child's summary lands.

HNSW with cosine_ops mirrors `images_summary_clip_emb_idx` so the
search path uses the same `<=>` distance operator on either table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "0049_folder_embedding"
down_revision: Union[str, None] = "0048_rls_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "folders",
        sa.Column(
            "summary_clip_embedding",
            Vector(768),
            nullable=True,
            comment="Aggregate CLIP embedding (folder name + descendant summary centroid) for semantic folder search (D4)",
        ),
    )
    op.add_column(
        "folders",
        sa.Column(
            "embedding_updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="When summary_clip_embedding was last recomputed; NULL = never / stale",
        ),
    )
    op.execute(
        "CREATE INDEX folders_summary_clip_emb_idx ON folders "
        "USING hnsw (summary_clip_embedding vector_cosine_ops) "
        "WITH (m='16', ef_construction='64')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS folders_summary_clip_emb_idx")
    op.drop_column("folders", "embedding_updated_at")
    op.drop_column("folders", "summary_clip_embedding")
