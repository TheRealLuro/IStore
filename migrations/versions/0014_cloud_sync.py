"""C2 — cloud_links + cloud_files (Drive / GitHub / Dropbox sync)

Scaffold migration for cloud-storage sync. The OAuth callback that
populates `cloud_links.encrypted_refresh_token` is a stub until A2/A3
(secrets at rest) are in place — DO NOT enable the production sync
worker before that lands.

Revision ID: 0014_cloud_sync
Revises: 0013_image_geo
Create Date: 2026-05-04 17:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_cloud_sync"
down_revision: Union[str, None] = "0013_image_geo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cloud_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("last_synced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "cloud_links_user_provider_idx", "cloud_links", ["user_id", "provider"]
    )

    op.create_table(
        "cloud_files",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("remote_id", sa.Text(), nullable=False),
        sa.Column("remote_path", sa.Text(), nullable=True),
        sa.Column(
            "local_image_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("remote_modified", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sha256", sa.LargeBinary(32), nullable=True),
        sa.Column("last_synced_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "cloud_files_user_provider_remote_uq",
        "cloud_files",
        ["user_id", "provider", "remote_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("cloud_files_user_provider_remote_uq", table_name="cloud_files")
    op.drop_table("cloud_files")
    op.drop_index("cloud_links_user_provider_idx", table_name="cloud_links")
    op.drop_table("cloud_links")
