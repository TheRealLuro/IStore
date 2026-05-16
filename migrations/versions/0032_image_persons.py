"""Add image_persons table for manual image-to-person associations.

When the user multi-selects N photos and tags them as a single Person
(e.g. "Me"), face detection fires on each but only the photos with an
actually-detectable face produce a Face row. The other photos used to
get silently dropped — the user selected 29 photos, only 16 had
detectable faces, and the person view showed 16. From the user's
perspective the manual tag wasn't saved.

This migration adds the `image_persons` table: a direct M:N link
between images and persons that's independent of face detection. The
detect-and-label endpoint now INSERTs a row here for every selected
image, regardless of whether face detection succeeded. The list-images
person filter UNIONs face-detected matches with manual matches, and
the people-list face_count becomes a DISTINCT image count across both
sources.

RLS-forced via the same pattern as 0027 / 0028 — denormalized user_id
column with USING + WITH CHECK matching the session GUC.

Revision ID: 0032_image_persons
Revises: 0031_user_google_sub
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0032_image_persons"
down_revision: Union[str, None] = "0031_user_google_sub"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR user_id::text = current_setting('app.current_user_id', true)"
)


def upgrade() -> None:
    op.create_table(
        "image_persons",
        sa.Column(
            "image_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 'manual' = user tagged the image directly via multi-select.
        # 'auto'   = future use (e.g. inferred from face match without
        #            a per-face row of its own). Today only 'manual' is
        #            written; the column exists so we don't need
        #            another migration if/when the cluster pipeline
        #            grows an image-level inference path.
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "image_persons_person_idx",
        "image_persons",
        ["person_id", "image_id"],
    )

    op.execute("ALTER TABLE image_persons ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE image_persons FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS image_persons_user_isolation ON image_persons")
    op.execute(
        f"""
        CREATE POLICY image_persons_user_isolation ON image_persons
        FOR ALL
        USING ({_RLS_EXPR})
        WITH CHECK ({_RLS_EXPR})
        """
    )

    # Backfill: every existing (image, person) pair implied by a face row
    # gets a matching image_persons entry with source='auto'. This makes
    # the new list_images UNION query consistent with the historical
    # behavior — without the backfill, the per-person photo count would
    # drop on first deploy for any library that had named persons
    # before this migration ran.
    op.execute(
        """
        INSERT INTO image_persons (image_id, person_id, user_id, source)
        SELECT DISTINCT fd.image_id, f.person_id, f.user_id, 'auto'
        FROM face_detections fd
        JOIN faces f ON f.id = fd.face_id
        WHERE f.person_id IS NOT NULL
          AND fd.face_id IS NOT NULL
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS image_persons_user_isolation ON image_persons")
    op.execute("ALTER TABLE image_persons NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE image_persons DISABLE ROW LEVEL SECURITY")
    op.drop_index("image_persons_person_idx", table_name="image_persons")
    op.drop_table("image_persons")
