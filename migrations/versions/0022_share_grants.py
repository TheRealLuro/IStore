"""share_grants table

Per-image share grant primitive (todo §1.1 / G1). One row per
(image, recipient_email) pair. Plaintext token shown to the sharer
once at create; stored as argon2 hash. Recipient identified by
email so sharing works before signup; `recipient_user_id` is bound
when the recipient first claims. Brand-new recipients always get a
1-day window from the moment of claim regardless of the sharer's
chosen duration. citext is already enabled in 0001.

Revision ID: 0022_share_grants
Revises: 0021_face_landmarks
Create Date: 2026-05-14 23:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import CITEXT, UUID as PgUUID


revision: str = "0022_share_grants"
down_revision: Union[str, None] = "0021_face_landmarks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "share_grants",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "image_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("images.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sharer_user_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient_email", CITEXT(), nullable=False),
        sa.Column(
            "recipient_user_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "permission",
            sa.String(16),
            nullable=False,
            server_default="view_download",
        ),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("sharer_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "share_grants_recipient_user_active_idx",
        "share_grants",
        ["recipient_user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "share_grants_pending_email_idx",
        "share_grants",
        ["recipient_email"],
        postgresql_where=sa.text("recipient_user_id IS NULL"),
    )
    op.create_index(
        "share_grants_image_idx",
        "share_grants",
        ["image_id", "revoked_at"],
    )
    op.create_index(
        "share_grants_image_recipient_uq",
        "share_grants",
        ["image_id", "recipient_email"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("share_grants_image_recipient_uq", table_name="share_grants")
    op.drop_index("share_grants_image_idx", table_name="share_grants")
    op.drop_index("share_grants_pending_email_idx", table_name="share_grants")
    op.drop_index("share_grants_recipient_user_active_idx", table_name="share_grants")
    op.drop_table("share_grants")
