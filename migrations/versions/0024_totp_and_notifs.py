"""TOTP 2FA columns + notification_prefs table (todo §1.2.2 + §1.2.3)

Three additions:

  users.totp_secret_enc   — Fernet-encrypted TOTP shared secret (bytea).
                            backend.secret_box.encrypt / decrypt wrap it
                            so a stolen DB dump alone can't read codes.
  users.totp_enabled      — bool; True only after the user has verified
                            a code matches the secret. Setup writes the
                            secret + enabled=False; verify flips it.
  users.totp_verified_at  — timestamp of the most recent successful
                            code verification (login or a fresh setup).
                            Lets the UI show "Last verified …" and lets
                            ops spot stale 2FA configs.

  notification_prefs       — per-(user, kind, channel) opt-in matrix.
                            kinds: 'product_updates', 'security_alerts',
                            'account_activity', 'storage_warnings'.
                            channels: 'email', 'in_app'. Push is
                            deferred (needs service worker + Push API).
                            Rows missing default to enabled=True for
                            security_alerts and false for the rest.

Revision ID: 0024_totp_and_notifs
Revises: 0023_workers_models
Create Date: 2026-05-15 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PgUUID


revision: str = "0024_totp_and_notifs"
down_revision: Union[str, None] = "0023_workers_models"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users.totp_* ---
    op.add_column(
        "users",
        sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("totp_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # --- notification_prefs ---
    op.create_table(
        "notification_prefs",
        sa.Column(
            "user_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("channel", sa.String(16), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_prefs")
    op.drop_column("users", "totp_verified_at")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_enc")
