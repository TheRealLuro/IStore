"""Per-chunk document embeddings for jump-to-section semantic search.

Today, document summaries are produced by map-reducing the document
text through Qwen2.5 — the final summary string is great for surfacing
the doc at search time, but if the user asks "where in this doc does
it talk about X", we can only return the doc itself with no anchor.

This migration adds a `document_chunks` table that persists per-chunk
text + CLIP text-space embedding. The same chunk-split the summary
pipeline already does for map-reduce now ALSO emits one row per chunk
here. At search time, we can rank chunks via cosine on the embedding
and return (image_id, chunk_index, snippet) so the FE can deep-link
to the relevant page / passage.

Schema:
  - id              uuid primary key (gen_random_uuid())
  - image_id        uuid not null, foreign key → images.id ON DELETE CASCADE
                    (chunks die with the parent doc, including soft-delete
                    purges + account deletion)
  - user_id         uuid not null — denormalized for RLS + bulk delete
  - chunk_index     int not null — 0-based position within the doc, so
                    "where in the doc" maps to a page-ish marker
  - text            text not null — the chunk content (~500 tokens)
  - embedding       vector(768) — OpenCLIP ViT-L-14 text-space; nullable
                    because the embedding step is best-effort (CLIP may
                    not be loadable in a test env)
  - created_at      timestamptz default now()

Constraints + indexes:
  - unique (image_id, chunk_index) — re-summarize overwrites in place
  - btree index (image_id) for the "chunks belonging to this doc" query
  - hnsw index on embedding for the cosine-ANN search path
  - RLS policy on user_id, mirroring the rest of the schema

The vector index uses cosine_ops to match the existing
`images_clip_embedding_idx` shape so the same query operator (<=>)
applies uniformly across image-level + chunk-level retrieval.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "0041_document_chunks"
down_revision: Union[str, None] = "0040_user_token_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "image_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "embedding",
            Vector(768),
            nullable=True,
            comment="OpenCLIP ViT-L-14 text-space embedding of `text`",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "image_id", "chunk_index",
            name="uq_document_chunks_image_chunk",
        ),
    )

    op.create_index(
        "ix_document_chunks_image_id",
        "document_chunks",
        ["image_id"],
    )
    op.create_index(
        "ix_document_chunks_user_id",
        "document_chunks",
        ["user_id"],
    )

    # HNSW cosine index — same params as the image clip-embedding index
    # so query-time tuning (ef_search, etc.) applies uniformly.
    op.execute(
        "CREATE INDEX document_chunks_embedding_idx ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m='16', ef_construction='64')"
    )

    # Row-Level Security — every chunk row scoped to its owner. Mirror
    # the policy shape used on `images` and `image_persons` (see audit
    # remediation D2).
    op.execute("ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY document_chunks_user_isolation ON document_chunks "
        "FOR ALL "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid) "
        "WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS document_chunks_user_isolation ON document_chunks")
    op.execute("DROP INDEX IF EXISTS document_chunks_embedding_idx")
    op.drop_index("ix_document_chunks_user_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_image_id", table_name="document_chunks")
    op.drop_table("document_chunks")
