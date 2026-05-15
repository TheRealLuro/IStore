"""stripe billing: plans + subscriptions + stripe_events

Adds three tables so we can:
  - List paid tiers + features without hard-coding them at three
    different layers (FE pricing page, backend tier resolution,
    quota enforcement).
  - Persist a row per user's Stripe customer + subscription so the
    rest of the system (rate limits, quota, /storage/usage) can
    look up "what tier is this user?" without round-tripping to
    Stripe on every request.
  - Idempotency-check incoming webhooks against `stripe_events.id`
    — Stripe retries on 5xx and a duplicate `subscription.updated`
    shouldn't double-apply a quota change.

`plans` is seeded with the three tiers (free / pro / business). The
Stripe Price IDs live in env vars (`STRIPE_PRICE_ID_PRO_MONTHLY` etc.)
not in this migration — operators create the Products + Prices in the
Stripe dashboard at deploy time and point the env vars at them. The
seed row stores the *name* of the env var so the API can look up the
current Price ID without hot-reloading the table.

Revision ID: 0025_stripe_billing
Revises: 0024_totp_and_notifs
Create Date: 2026-05-15 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025_stripe_billing"
down_revision: Union[str, None] = "0024_totp_and_notifs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("tier", sa.String(length=16), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        # Cents because float currency math is a footgun. NULL on
        # `monthly_cents` for free.
        sa.Column("monthly_cents", sa.Integer(), nullable=True),
        sa.Column("annual_cents", sa.Integer(), nullable=True),
        # The Price ID for each interval lives in env vars; the table
        # records the env var NAME so a deploy can flip Prices without
        # a migration. NULL for free.
        sa.Column("monthly_price_id_env", sa.String(length=64), nullable=True),
        sa.Column("annual_price_id_env", sa.String(length=64), nullable=True),
        sa.Column("quota_bytes", sa.BigInteger(), nullable=False),
        sa.Column("upload_max_per_hour", sa.Integer(), nullable=False),
        sa.Column("upload_max_bytes_per_day", sa.BigInteger(), nullable=False),
        # Feature flags exposed to the FE pricing card. Free-form so
        # we can add tier-specific perks (e.g. "priority_queue": true)
        # without another migration.
        sa.Column("features", sa.dialects.postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )

    # Three-tier seed. Quotas and limits match the pricing matrix the
    # operator approved. The env-var-name columns let the runtime pull
    # the actual Stripe Price IDs without baking them into the DB —
    # the same row works in test mode (sk_test_…) and prod (sk_live_…)
    # by pointing at different env values.
    op.bulk_insert(
        sa.table(
            "plans",
            sa.column("tier", sa.String),
            sa.column("display_name", sa.Text),
            sa.column("monthly_cents", sa.Integer),
            sa.column("annual_cents", sa.Integer),
            sa.column("monthly_price_id_env", sa.String),
            sa.column("annual_price_id_env", sa.String),
            sa.column("quota_bytes", sa.BigInteger),
            sa.column("upload_max_per_hour", sa.Integer),
            sa.column("upload_max_bytes_per_day", sa.BigInteger),
            sa.column("features", sa.dialects.postgresql.JSONB),
            sa.column("sort_order", sa.Integer),
        ),
        [
            {
                "tier": "free",
                "display_name": "Free",
                "monthly_cents": None,
                "annual_cents": None,
                "monthly_price_id_env": None,
                "annual_price_id_env": None,
                "quota_bytes": 50 * 1024 * 1024 * 1024,            # 50 GB
                "upload_max_per_hour": 100,
                "upload_max_bytes_per_day": 2 * 1024 * 1024 * 1024,  # 2 GB
                "features": {
                    "search": True,
                    "ai_summaries": True,
                    "sharing": True,
                    "priority_queue": False,
                    "audit_export": False,
                },
                "sort_order": 0,
            },
            {
                "tier": "pro",
                "display_name": "Pro",
                "monthly_cents": 3000,           # $30.00
                "annual_cents": 30000,           # $300.00 (2 months free)
                "monthly_price_id_env": "STRIPE_PRICE_ID_PRO_MONTHLY",
                "annual_price_id_env": "STRIPE_PRICE_ID_PRO_ANNUAL",
                "quota_bytes": 500 * 1024 * 1024 * 1024,           # 500 GB
                "upload_max_per_hour": 300,
                "upload_max_bytes_per_day": 50 * 1024 * 1024 * 1024,  # 50 GB
                "features": {
                    "search": True,
                    "ai_summaries": True,
                    "sharing": True,
                    "priority_queue": True,
                    "audit_export": False,
                },
                "sort_order": 1,
            },
            {
                "tier": "business",
                "display_name": "Business",
                "monthly_cents": 9900,           # $99.00
                "annual_cents": 99000,           # $990.00 (2 months free)
                "monthly_price_id_env": "STRIPE_PRICE_ID_BUSINESS_MONTHLY",
                "annual_price_id_env": "STRIPE_PRICE_ID_BUSINESS_ANNUAL",
                "quota_bytes": 2 * 1024 * 1024 * 1024 * 1024,      # 2 TB
                "upload_max_per_hour": 1000,
                "upload_max_bytes_per_day": 200 * 1024 * 1024 * 1024,  # 200 GB
                "features": {
                    "search": True,
                    "ai_summaries": True,
                    "sharing": True,
                    "priority_queue": True,
                    "audit_export": True,
                    "b2b_migration": True,
                },
                "sort_order": 2,
            },
        ],
    )

    op.create_table(
        "subscriptions",
        # One subscription row per user; the FK is the PK to enforce
        # that. Switching tiers updates this row instead of creating a
        # new one — the Stripe subscription_id changes when interval
        # flips (monthly ↔ annual) so we always write the latest.
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="free"),
        # 'active' | 'trialing' | 'past_due' | 'canceled' | 'incomplete' | 'incomplete_expired' | 'unpaid'
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        # 'month' | 'year' — null for free.
        sa.Column("interval", sa.String(length=8), nullable=True),
        sa.Column("stripe_customer_id", sa.Text(), nullable=True),
        sa.Column("stripe_subscription_id", sa.Text(), nullable=True),
        sa.Column("current_period_start", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "subscriptions_stripe_customer_idx",
        "subscriptions",
        ["stripe_customer_id"],
        unique=False,
    )
    op.create_index(
        "subscriptions_stripe_subscription_idx",
        "subscriptions",
        ["stripe_subscription_id"],
        unique=False,
    )

    # Webhook idempotency. Stripe retries on 5xx; without this table a
    # `customer.subscription.updated` could apply twice and double-bump
    # quotas. The id is the Stripe event id (`evt_…`), which is unique
    # per delivery attempt.
    op.create_table(
        "stripe_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "processed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "stripe_events_received_idx",
        "stripe_events",
        ["received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("stripe_events_received_idx", table_name="stripe_events")
    op.drop_table("stripe_events")
    op.drop_index("subscriptions_stripe_subscription_idx", table_name="subscriptions")
    op.drop_index("subscriptions_stripe_customer_idx", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("plans")
