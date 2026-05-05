"""Section A public deployment hardening

Revision ID: 0016_section_a_hardening
Revises: 0015_system_config
Create Date: 2026-05-04 17:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_section_a_hardening"
down_revision: Union[str, None] = "0015_system_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR user_id::text = current_setting('app.current_user_id', true)"
)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(16), nullable=False, server_default="user"),
    )
    op.add_column(
        "users",
        sa.Column("age_confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute("UPDATE users SET role = 'superuser' WHERE is_superuser = true")
    op.create_check_constraint(
        "users_role_check",
        "users",
        "role IN ('user', 'admin', 'superuser')",
    )

    # Audit rows are legal-retention artifacts: preserve the UUID and prevent
    # UPDATE/DELETE. Drop the FK that previously nulled user_id on account
    # deletion, but keep an index for filtering.
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS audit_log_user_id_fkey")
    op.create_index("audit_log_action_time_idx", "audit_log", ["action", "created_at"])
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
    op.execute(
        """
        DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
        CREATE TRIGGER audit_log_no_update
        BEFORE UPDATE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
        DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;
        CREATE TRIGGER audit_log_no_delete
        BEFORE DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
        """
    )

    for table in ("faces", "face_detections", "persons"):
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


def downgrade() -> None:
    for table in ("faces", "face_detections", "persons"):
        op.execute(f"DROP POLICY IF EXISTS {table}_user_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_mutation()")
    op.drop_index("audit_log_action_time_idx", table_name="audit_log")
    op.create_foreign_key(
        "audit_log_user_id_fkey",
        "audit_log",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("users_role_check", "users", type_="check")
    op.drop_column("users", "age_confirmed")
    op.drop_column("users", "role")
