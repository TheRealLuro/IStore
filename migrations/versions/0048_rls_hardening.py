"""RLS hardening (security audit F11 + F13).

Two coordinated row-level-security fixes that matter once the app stops
connecting as a Postgres superuser (audit F7 — a superuser bypasses RLS
entirely, so until that lands these policies are inert but harmless):

F11 — tables that had NO row-level security at all now get ENABLE + FORCE
      ROW LEVEL SECURITY with the standard bypass-clause policy:
        * cloud_links        (holds Fernet-encrypted OAuth refresh tokens)
        * cloud_files        (provider file pointers)
        * subscriptions      (billing linkage)
        * notification_prefs (contactability state)

F13 — the vault policies (migrations 0044/0045/0046) were created WITHOUT the
      `app.rls_bypass` escape clause that every other policy (0016/0027) has.
      Without it, legitimate cross-user maintenance paths (the cloud-sync
      worker, the Stripe webhook, retention/deletion sweepers that run with
      `SET LOCAL app.rls_bypass='on'`) silently match zero rows on vault
      tables once RLS is enforced. Recreate them WITH the bypass clause, in
      the same `::text` comparison form 0027 uses (no hard `::uuid` cast that
      could error on a malformed GUC).

The bypass GUC is only ever set to 'on' inside the app's own trusted
cross-user code paths (never from client input), and `app.current_user_id`
is server-derived from the validated session — so this preserves tenant
isolation for every normal request while letting the documented maintenance
paths work.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0048_rls_hardening"
down_revision: Union[str, None] = "0047_vault_public_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Standard single-`user_id` predicate, matching migration 0027's form:
# bypass when the trusted maintenance GUC is on, otherwise pin to the
# request's server-derived user id. `::text` compare avoids a hard `::uuid`
# cast that would error on a malformed/empty GUC.
def _user_id_expr() -> str:
    return (
        "current_setting('app.rls_bypass', true) = 'on' "
        "OR user_id::text = current_setting('app.current_user_id', true)"
    )


# ---- F11: tables that previously had no RLS ----
_F11_TABLES = ("cloud_links", "cloud_files", "subscriptions", "notification_prefs")

# ---- F13: vault policies to recreate with the bypass clause ----
# (policy_name, table, using_expr, with_check_expr)
_UID = "current_setting('app.current_user_id', true)"
_BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
_VAULT_POLICIES = (
    ("vault_meta_user_isolation", "vault_meta",
     f"{_BYPASS} OR user_id::text = {_UID}",
     f"{_BYPASS} OR user_id::text = {_UID}"),
    ("vault_items_user_isolation", "vault_items",
     f"{_BYPASS} OR user_id::text = {_UID}",
     f"{_BYPASS} OR user_id::text = {_UID}"),
    ("vault_folders_user_isolation", "vault_folders",
     f"{_BYPASS} OR user_id::text = {_UID}",
     f"{_BYPASS} OR user_id::text = {_UID}"),
    ("vault_share_grants_access", "vault_share_grants",
     f"{_BYPASS} OR owner_user_id::text = {_UID} OR recipient_user_id::text = {_UID}",
     f"{_BYPASS} OR owner_user_id::text = {_UID}"),
)


def upgrade() -> None:
    # F11 — enable + force RLS on the previously-unprotected tables.
    for tbl in _F11_TABLES:
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {tbl}_user_isolation ON {tbl} "
            f"FOR ALL USING ({_user_id_expr()}) WITH CHECK ({_user_id_expr()})"
        )

    # F13 — recreate vault policies WITH the bypass clause.
    for name, tbl, using_expr, check_expr in _VAULT_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {name} ON {tbl}")
        op.execute(
            f"CREATE POLICY {name} ON {tbl} "
            f"FOR ALL USING ({using_expr}) WITH CHECK ({check_expr})"
        )


def downgrade() -> None:
    # F13 — restore the original (no-bypass) vault policies.
    _orig = (
        ("vault_meta_user_isolation", "vault_meta",
         "user_id = current_setting('app.current_user_id', true)::uuid",
         "user_id = current_setting('app.current_user_id', true)::uuid"),
        ("vault_items_user_isolation", "vault_items",
         "user_id = current_setting('app.current_user_id', true)::uuid",
         "user_id = current_setting('app.current_user_id', true)::uuid"),
        ("vault_folders_user_isolation", "vault_folders",
         "user_id = current_setting('app.current_user_id', true)::uuid",
         "user_id = current_setting('app.current_user_id', true)::uuid"),
        ("vault_share_grants_access", "vault_share_grants",
         "owner_user_id = current_setting('app.current_user_id', true)::uuid "
         "OR recipient_user_id = current_setting('app.current_user_id', true)::uuid",
         "owner_user_id = current_setting('app.current_user_id', true)::uuid"),
    )
    for name, tbl, using_expr, check_expr in _orig:
        op.execute(f"DROP POLICY IF EXISTS {name} ON {tbl}")
        op.execute(
            f"CREATE POLICY {name} ON {tbl} "
            f"FOR ALL USING ({using_expr}) WITH CHECK ({check_expr})"
        )

    # F11 — drop the policies + disable RLS on the previously-unprotected tables.
    for tbl in _F11_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {tbl}_user_isolation ON {tbl}")
        op.execute(f"ALTER TABLE {tbl} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY")
