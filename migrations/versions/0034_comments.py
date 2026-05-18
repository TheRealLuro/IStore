"""§G2 — comments on any file (owner OR active share recipient).

Adds a single `comments` table with:
  - per-image attachment (FK CASCADE on image delete — comments don't
    survive their subject)
  - threaded replies via self-FK `parent_id`
  - optional `anchor_json` for pin-on-content coordinates (page+rect
    for PDFs, slide index, video time-range, etc. — schema-less so
    new asset types don't need a migration)
  - soft-delete via `deleted_at` so the FE can render
    "comment deleted" placeholders in a thread (matches most chat
    UIs) instead of orphaning replies

RLS is intentionally lighter than the other per-user tables in the
codebase. Comments have THREE legitimate readers (the commenter, the
image owner, and any active share recipient), and Postgres RLS isn't
the right shape for "row visible if a join exists" — the share-grant
membership check lives in the API layer instead. The DB predicate
here only blocks the cheapest accident: a tenant trying to read or
write rows that are neither their own comments nor on their own
images. Defense in depth, not the primary access control.

Revision ID: 0034_comments
Revises: 0033_image_captured_at
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0034_comments"
down_revision: Union[str, None] = "0033_image_captured_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR user_id::text = current_setting('app.current_user_id', true) "
    "OR image_id IN ("
    "    SELECT id FROM images "
    "    WHERE user_id::text = current_setting('app.current_user_id', true)"
    ")"
)


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "image_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NULL when the author later deletes their account — the
        # comment text stays but the byline degrades to "former user."
        # CASCADE would orphan replies under deleted accounts.
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Reply threading. CASCADE so deleting a root deletes its
        # entire reply tree.
        sa.Column(
            "parent_id",
            sa.BigInteger(),
            sa.ForeignKey("comments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        # Optional pin coordinates. Schema-less so new asset types
        # (slides, video) can land without a migration. Shape today:
        #   PDFs:   {"kind": "pdf", "page": 3, "rect": [x, y, w, h]}
        #   slides: {"kind": "slide", "index": 5}
        #   video:  {"kind": "video", "t_start": 12.3, "t_end": 15.0}
        #   image:  {"kind": "image", "x": 0.5, "y": 0.7}  (normalized)
        # NULL = "general comment on the file" — surfaces in the
        # thread but not as a pin overlay.
        sa.Column(
            "anchor_json",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Soft delete so the thread can render "comment deleted"
        # instead of dropping replies into orphan land.
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "comments_image_idx",
        "comments",
        ["image_id", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "comments_user_idx",
        "comments",
        ["user_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "comments_parent_idx",
        "comments",
        ["parent_id"],
        postgresql_where=sa.text("parent_id IS NOT NULL AND deleted_at IS NULL"),
    )

    op.execute("ALTER TABLE comments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE comments FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS comments_user_isolation ON comments")
    op.execute(
        f"""
        CREATE POLICY comments_user_isolation ON comments
        FOR ALL
        USING ({_RLS_EXPR})
        WITH CHECK ({_RLS_EXPR})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS comments_user_isolation ON comments")
    op.execute("ALTER TABLE comments NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE comments DISABLE ROW LEVEL SECURITY")
    op.drop_index("comments_parent_idx", table_name="comments")
    op.drop_index("comments_user_idx", table_name="comments")
    op.drop_index("comments_image_idx", table_name="comments")
    op.drop_table("comments")
