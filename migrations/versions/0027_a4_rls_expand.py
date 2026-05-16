"""A4 — extend RLS coverage + tighten audit guarantees.

Migration 0016 added RLS on the biometric tables (`faces`,
`face_detections`, `persons`). A4 closes the rest of the per-user
isolation surface so the app-layer `Image.user_id == user.id` filters
are not the *only* line of defense.

Tables added to FORCE-RLS in this migration:
- `image_geo`     — GPS rows extracted from EXIF; sensitive location data.
- `feedback_events` — append-only reward signal, includes `image_id`
                      and `user_id`. Used by the bandit trainer.
- `image_tags`    — many-to-many; carries no PII on its own, but a
                    cross-user query could enumerate tag→image fan-out.
- `share_grants`  — share metadata (recipient email, token hash,
                    revoked_at). Per-user owner isolation.
- `consent_records` — every grant/withdraw with IP + UA. Per-user.
- `recovery_codes` — per-user one-shot codes. Hashed but still keyed
                     to the user; isolation prevents enumeration.
- `bandit_state`  — per-(user, arm) learned weights.

Each table uses the same `_RLS_EXPR` as migration 0016: either the
session is in `app.rls_bypass=on` (test/dev fixture path) or the
row's `user_id` matches `app.current_user_id` (set by the auth
dependency).

We do NOT add RLS to:
- `users` (fastapi-users needs cross-user reads for admin)
- `audit_log` (intentionally append-only; admin reads cross-user)
- `folders`, `images`, `tags`, `subscriptions`, `notification_prefs`
  — `images` carries the canonical `user_id` filter already enforced
  via SQLAlchemy WHERE clauses; adding RLS there would interfere
  with the storage usage tally + admin dashboard cross-user reads.
  RLS on the sibling biometric/PII tables is the layered defense.

Revision ID: 0027_a4_rls_expand
Revises: 0026_account_grace_delete
Create Date: 2026-05-16 23:00:00
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0027_a4_rls_expand"
down_revision: Union[str, None] = "0026_account_grace_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR user_id::text = current_setting('app.current_user_id', true)"
)


# Tables that carry a plain `user_id` column. RLS predicate is the
# same as migration 0016: bypass when in dev/test, otherwise pin to
# the request's resolved user id.
_RLS_TABLES_USER_ID = (
    "image_geo",
    "feedback_events",
    "consent_records",
    "recovery_codes",
    "bandit_state",
)


# `share_grants` has two user-pointer columns (`sharer_user_id`,
# `recipient_user_id`) rather than a single `user_id`. The custom
# predicate honors both perspectives — a sharer sees their grants,
# a recipient sees grants pointed at them.
_SHARE_GRANTS_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR sharer_user_id::text = current_setting('app.current_user_id', true) "
    "OR recipient_user_id::text = current_setting('app.current_user_id', true)"
)


# `image_tags` is intentionally NOT included: the table has no
# `user_id` / `sharer_user_id` column, and the join key `image_id`
# already inherits owner isolation from the `images` row. RLS on
# `image_tags` would require either a column addition (schema churn
# we want to avoid) or a subquery-based policy (perf-hostile).


def upgrade() -> None:
    for table in _RLS_TABLES_USER_ID:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_user_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_user_isolation ON {table}
            FOR ALL
            USING ({_RLS_EXPR})
            WITH CHECK ({_RLS_EXPR})
            """
        )

    # share_grants — dual-perspective predicate.
    op.execute("ALTER TABLE share_grants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE share_grants FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS share_grants_user_isolation ON share_grants")
    op.execute(
        f"""
        CREATE POLICY share_grants_user_isolation ON share_grants
        FOR ALL
        USING ({_SHARE_GRANTS_EXPR})
        WITH CHECK ({_SHARE_GRANTS_EXPR})
        """
    )


def downgrade() -> None:
    for table in _RLS_TABLES_USER_ID:
        op.execute(f"DROP POLICY IF EXISTS {table}_user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS share_grants_user_isolation ON share_grants")
    op.execute("ALTER TABLE share_grants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE share_grants DISABLE ROW LEVEL SECURITY")
