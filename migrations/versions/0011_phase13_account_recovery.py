"""Phase 13 (C6) account recovery + email verification

Adds a `recovery_codes` table for single-use codes. fastapi-users already
owns email verification + reset-password tokens (signed JWTs, no DB
needed) so we don't add columns there — `users.is_verified` is created
by the SQLAlchemyBaseUserTableUUID mixin in 0002.

Revision ID: 0011_phase13_account_recovery
Revises: 0010_folders
Create Date: 2026-05-04 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_phase13_account_recovery"
down_revision: Union[str, None] = "0010_folders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recovery_codes",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column(
            "used_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Lookup-by-user is the only read pattern: list "are codes set up?"
    # and find candidates for a recovery_login attempt.
    op.create_index(
        "recovery_codes_user_idx",
        "recovery_codes",
        ["user_id"],
        postgresql_where=sa.text("used_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("recovery_codes_user_idx", table_name="recovery_codes")
    op.drop_table("recovery_codes")
