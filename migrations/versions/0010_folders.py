"""folders + project status

Adds a `folders` table separate from `images` (folders carry no blob and
need different required fields, so co-mingling them in the same table
would mean half the columns are NULL). Each image gets a nullable
`folder_id` so it can sit at the user's root or inside one folder.

Folders are recursively nestable — `parent_folder_id` is a self-FK with
`ON DELETE CASCADE` so deleting a parent removes the whole subtree.

Project status (from the user's roadmap "label / status for assignments")
lives on both folders and individual files via a simple `status` text
column with `status_color` for the chip tint. No enum yet — the user
edits the label inline and we let them define their own.

Revision ID: 0010_folders
Revises: 0009_phase11_summary
Create Date: 2026-05-04 04:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_folders"
down_revision: Union[str, None] = "0009_phase11_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "folders",
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
        sa.Column(
            "parent_folder_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        # User-facing project status (e.g. "in progress", "submitted"). Free
        # text so users can define their own vocabulary; we render a colored
        # chip from `status_color`.
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("status_color", sa.String(16), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Soft delete — same pattern as images.deleted_at.
        sa.Column(
            "deleted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        # Was this folder produced by extracting an archive (zip / 7z /
        # tar)? Tracked so we can later offer "re-pack on download" without
        # a separate table. NULL for user-created folders.
        sa.Column("source_archive_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "folders_user_parent_idx",
        "folders",
        ["user_id", "parent_folder_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # Prevent two folders with the same name in the same parent for the
    # same user — keeps the UI honest. NULL parents collide, hence
    # COALESCE in the partial-unique-index expression.
    op.execute(
        "CREATE UNIQUE INDEX folders_user_parent_name_uq "
        "ON folders (user_id, COALESCE(parent_folder_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name)) "
        "WHERE deleted_at IS NULL"
    )

    op.add_column(
        "images",
        sa.Column(
            "folder_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("images", sa.Column("status", sa.Text(), nullable=True))
    op.add_column(
        "images", sa.Column("status_color", sa.String(16), nullable=True)
    )
    op.create_index(
        "images_folder_id_idx",
        "images",
        ["user_id", "folder_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("images_folder_id_idx", table_name="images")
    op.drop_column("images", "status_color")
    op.drop_column("images", "status")
    op.drop_column("images", "folder_id")
    op.execute("DROP INDEX IF EXISTS folders_user_parent_name_uq")
    op.drop_index("folders_user_parent_idx", table_name="folders")
    op.drop_table("folders")
