"""account-deletion grace window (§B4)

Adds `users.scheduled_delete_at` so /account/schedule-delete can stamp
"hard-delete this account on or after T" without nuking the row
immediately. A nightly sweep
(retention.sweep_scheduled_account_deletes) picks up everything past
its timestamp and runs the existing hard-delete path.

The column is nullable; NULL = "no scheduled deletion." The legacy
/account/delete route keeps its immediate-delete semantics so any
existing client behavior is unchanged — only the new
schedule-delete flow uses this column.

Revision ID: 0026_account_grace_delete
Revises: 0025_stripe_billing
Create Date: 2026-05-16 19:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026_account_grace_delete"
down_revision: Union[str, None] = "0025_stripe_billing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "scheduled_delete_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    # Partial index so the sweeper's WHERE scheduled_delete_at IS NOT
    # NULL doesn't scan the whole users table. Most users will have
    # NULL forever, so a partial index keeps the cost minimal.
    op.create_index(
        "users_scheduled_delete_idx",
        "users",
        ["scheduled_delete_at"],
        postgresql_where=sa.text("scheduled_delete_at IS NOT NULL"),
    )

    # §B4 — refine the audit_log append-only trigger to permit the
    # specific anonymization that sweep_audit_log_anonymize needs.
    # Migration 0016 created an unconditional block on UPDATE/DELETE
    # for chain-of-custody, but the retention-after-1y workstream
    # needs to NULL `user_id` on aged rows. We narrowly allow that
    # one transition and continue to reject everything else.
    #
    # Allowed:
    #   OLD.user_id IS NOT NULL AND NEW.user_id IS NULL AND every
    #   other column unchanged.
    # Rejected (RAISE):
    #   - any other column change
    #   - re-pointing user_id at a different user
    #   - re-setting user_id to non-NULL after anonymization
    # DELETE remains blocked.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF OLD.user_id IS NOT NULL
               AND NEW.user_id IS NULL
               AND NEW.id      IS NOT DISTINCT FROM OLD.id
               AND NEW.action  IS NOT DISTINCT FROM OLD.action
               AND NEW.details IS NOT DISTINCT FROM OLD.details
               AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
            THEN
              RETURN NEW;  -- single permitted transition
            END IF;
            RAISE EXCEPTION 'audit_log is append-only (anonymization is the only permitted mutation)';
          END IF;
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'audit_log is append-only (no deletes)';
          END IF;
          RETURN NULL;
        END;
        $$;
        """
    )


def downgrade() -> None:
    # Restore the unconditional reject from migration 0016.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$;
        """
    )
    op.drop_index("users_scheduled_delete_idx", table_name="users")
    op.drop_column("users", "scheduled_delete_at")
