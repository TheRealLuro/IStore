"""rejected face embeddings (false-positive "not a person" memory)

Adds `rejected_face_embeddings` — a per-user log of face embeddings the
user has explicitly marked as "not a person" (a false-positive detection:
a mask, a poster, a pattern the detector mistook for a face, …).

The face-scan pipeline (`backend.faces_pipeline.process_image_for_faces`)
consults this table BEFORE persisting a newly-detected face: if the new
embedding's nearest rejected embedding for this user is within the
rejection threshold (cosine), the detection is dropped instead of
re-creating the same bogus Face row on every re-scan. This is the
"remember it" half of the feature — deleting the bad cluster alone
wouldn't stop the next scan from re-detecting it.

Mirrors the `faces` table on purpose:
  - `embedding vector(512)` (ArcFace buffalo_l, same dimensionality)
  - HNSW cosine index with the same (m=16, ef_construction=64) params
    used by `faces_embedding_idx` (migration 0006), so the nearest-
    rejection lookup is index-backed.
  - RLS ENABLE + FORCE + a `{table}_user_isolation` policy using the
    exact bypass-clause predicate every other per-user table carries
    (migrations 0016 / 0027 / 0048).

Purely additive + reversible: new table, new index, new policy. No
existing data is touched.

Revision ID: 0051_rejected_face_embeddings
Revises: 0050_converted_pdf_blob_key
Create Date: 2026-05-31 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "0051_rejected_face_embeddings"
down_revision: Union[str, None] = "0050_converted_pdf_blob_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Standard single-`user_id` RLS predicate, matching migrations 0016 /
# 0027 / 0048: bypass when the trusted maintenance GUC is on, otherwise
# pin to the request's server-derived user id. `::text` compare avoids a
# hard `::uuid` cast that would error on a malformed / empty GUC.
_RLS_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR user_id::text = current_setting('app.current_user_id', true)"
)


def upgrade() -> None:
    # pgvector is already enabled (migration 0001); the Vector column
    # below relies on it. No CREATE EXTENSION needed here.
    op.create_table(
        "rejected_face_embeddings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 512-d ArcFace embedding of the rejected (false-positive) face.
        sa.Column("embedding", Vector(512), nullable=False),
        # The unlabeled cluster the rejected face(s) came from, when the
        # rejection was issued at the cluster level. Nullable: a future
        # per-face rejection path may have no single owning cluster, and
        # the cluster is deleted right after the rejection is recorded
        # anyway (so this is provenance only, not a live FK).
        sa.Column("source_cluster_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "rejected_face_embeddings_user_idx",
        "rejected_face_embeddings",
        ["user_id"],
    )
    # HNSW cosine index — same opclass + build params as faces_embedding_idx
    # (migration 0006) so the suppression nearest-neighbour lookup is
    # index-backed rather than a sequential scan.
    op.execute(
        "CREATE INDEX rejected_face_embeddings_embedding_idx "
        "ON rejected_face_embeddings "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # RLS — same shape as every other per-user table.
    op.execute(
        "ALTER TABLE rejected_face_embeddings ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE rejected_face_embeddings FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "DROP POLICY IF EXISTS rejected_face_embeddings_user_isolation "
        "ON rejected_face_embeddings"
    )
    op.execute(
        f"""
        CREATE POLICY rejected_face_embeddings_user_isolation
        ON rejected_face_embeddings
        FOR ALL
        USING ({_RLS_EXPR})
        WITH CHECK ({_RLS_EXPR})
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS rejected_face_embeddings_user_isolation "
        "ON rejected_face_embeddings"
    )
    op.execute(
        "ALTER TABLE rejected_face_embeddings NO FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE rejected_face_embeddings DISABLE ROW LEVEL SECURITY"
    )
    op.execute("DROP INDEX IF EXISTS rejected_face_embeddings_embedding_idx")
    op.drop_index(
        "rejected_face_embeddings_user_idx",
        table_name="rejected_face_embeddings",
    )
    op.drop_table("rejected_face_embeddings")
