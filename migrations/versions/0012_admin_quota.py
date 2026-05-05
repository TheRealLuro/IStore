"""C8 admin dashboard — per-user quota override

Adds a nullable `users.quota_bytes` column. NULL keeps the user on the
global default (DEFAULT_QUOTA_BYTES); a non-NULL value is honored by
api/storage.py when computing usage.

Revision ID: 0012_admin_quota
Revises: 0011_phase13_account_recovery
Create Date: 2026-05-04 16:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_admin_quota"
down_revision: Union[str, None] = "0011_phase13_account_recovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("quota_bytes", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "quota_bytes")
