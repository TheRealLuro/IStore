"""Waitlist signups for the marketing site (real signup endpoint)

Backs the public POST /waitlist/signup endpoint and the admin viewer.
Stores only what the visitor actually submitted plus the IP / user-agent
of the request — no tracking pixels, no referrer chain.

  waitlist_signups
    id              BIGSERIAL PK
    email           CITEXT (case-insensitive, lowercased on write) UNIQUE
    use_case        VARCHAR(32) — picked from a closed enum on the FE
    source          VARCHAR(32) — 'marketing-site' / 'cli' / etc.
    ip              INET NULLABLE — best-effort, may be the proxy hop
    user_agent      TEXT NULLABLE
    notified        BOOLEAN — flips True once we email the launch ping
    notified_at     TIMESTAMPTZ NULLABLE
    created_at      TIMESTAMPTZ DEFAULT now()

UNIQUE(email) is on the CITEXT so a user can't double-sign with mixed
case. ON CONFLICT bumps `created_at` and updates `use_case` only —
we never overwrite the IP / UA with a later submit because the first
one is the more honest signal for fraud review.

Revision ID: 0025_waitlist_signups
Revises: 0024_totp_and_notifs
Create Date: 2026-05-15 13:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import CITEXT, INET


revision: str = "0025_waitlist_signups"
down_revision: Union[str, None] = "0024_totp_and_notifs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "waitlist_signups",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("email", CITEXT, nullable=False, unique=True),
        sa.Column("use_case", sa.String(32), nullable=False, server_default="personal"),
        sa.Column("source", sa.String(32), nullable=False, server_default="marketing-site"),
        sa.Column("ip", INET, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column(
            "notified", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index(
        "ix_waitlist_signups_created_at_desc",
        "waitlist_signups",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_waitlist_signups_created_at_desc", table_name="waitlist_signups")
    op.drop_table("waitlist_signups")
