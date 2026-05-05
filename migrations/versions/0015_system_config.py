"""system_config table

Stores admin-managed configuration that we'd otherwise force into .env:
- Google OAuth client (so the admin can paste credentials in the UI
  instead of editing .env and restarting uvicorn).
- The Fernet cloud encryption key, auto-generated on first need so a
  fresh install doesn't require the admin to run a separate command.

Single-row table keyed by `key`. Values are stored ciphertext-only —
encryption uses an env-supplied master key (or a generated one bootstrapped
into .env on first boot — see backend/secret_box.py). Defense-in-depth
so a database dump alone can't reveal Google OAuth secrets.

Revision ID: 0015_system_config
Revises: 0014_cloud_sync
Create Date: 2026-05-04 06:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_system_config"
down_revision: Union[str, None] = "0014_cloud_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(64), primary_key=True),
        # Stored as Fernet ciphertext (URL-safe base64 of bytes).
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("system_config")
