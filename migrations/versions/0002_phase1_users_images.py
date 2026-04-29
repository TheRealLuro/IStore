"""phase 1 users and images

Revision ID: 0002_phase1_users_images
Revises: 0001_init_extensions
Create Date: 2026-04-28 00:01:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_phase1_users_images"
down_revision: Union[str, None] = "0001_init_extensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "is_superuser",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("display_name", sa.String(length=120), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "images",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_blob_key", sa.Text(), nullable=False),
        sa.Column("served_blob_key", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("byte_size_original", sa.BigInteger(), nullable=True),
        sa.Column("byte_size_served", sa.BigInteger(), nullable=True),
        sa.Column("mime_type_original", sa.String(length=64), nullable=True),
        sa.Column("mime_type_served", sa.String(length=64), nullable=True),
        sa.Column("sha256", sa.LargeBinary(length=32), nullable=True),
        sa.Column("codec", sa.String(length=16), nullable=True),
        sa.Column("quality", sa.Integer(), nullable=True),
        sa.Column("max_dim", sa.Integer(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        "CREATE INDEX images_user_uploaded_idx ON images (user_id, uploaded_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS images_user_uploaded_idx")
    op.drop_table("images")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
