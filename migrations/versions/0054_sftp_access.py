"""SFTP / SSH access — per-user authorized keys + SFTP password (SFTP-1).

Introduces the two tables that back the read-only SFTP server
(`backend/sftp_server.py`), which exposes a neuthek user's folders +
files as a mountable filesystem (sshfs / WinSCP / Finder / `sftp` CLI).

Threat model + why a DEDICATED table (not the zero-knowledge vault):
  * The vault `ssh_key` item kind (migration 0053) stores SSH keys as
    AES-256-GCM ciphertext the SERVER CANNOT READ — that's the whole
    point of the vault. The SFTP server has to VERIFY a presented key
    against a stored PUBLIC key at connection time, server-side, with
    NO user session and NO master password available. A zero-knowledge
    blob is unusable for that. So authorized PUBLIC keys live here, in
    clear (a public key is not a secret), fenced by RLS to the owner.
  * The optional SFTP PASSWORD is an Argon2 hash (same hasher as the
    account password). It is a SEPARATE credential from the account
    login password on purpose: SFTP sends the password to the server on
    every connect (the server must be able to check it), so it must
    never be the account password. The user sets it explicitly in
    Settings / via `POST /sftp/password`.

Both tables:
  * carry `user_id` (FK CASCADE on users.id) so "delete my account"
    wipes SFTP access with everything else;
  * are ENABLE + FORCE ROW LEVEL SECURITY with the standard
    bypass-clause policy (matching migrations 0027 / 0048), so a user
    can only ever read/modify their OWN keys + credential, and the
    documented trusted maintenance paths (which run with
    `SET LOCAL app.rls_bypass='on'`) still work. The SFTP server's auth
    lookups run under `app.rls_bypass='on'` because at auth time the
    server hasn't yet resolved "which user is this" — it's RESOLVING
    that by looking the key/username up. Once resolved, every
    filesystem query runs pinned to that user_id (RLS + explicit
    WHERE), so the broad-read auth lookup never leaks data to a client.

Reversible: drops the policies, RLS flags, indexes, and tables.

Revision ID: 0054_sftp_access
Revises: 0053_vault_ssh_id_kinds
Create Date: 2026-05-31 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0054_sftp_access"
down_revision: Union[str, None] = "0053_vault_ssh_id_kinds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Standard single-`user_id` predicate, matching migrations 0027 / 0048:
# bypass when the trusted maintenance GUC is on, otherwise pin to the
# request's server-derived user id. `::text` compare avoids a hard
# `::uuid` cast that errors on an empty/malformed GUC.
def _user_id_expr() -> str:
    return (
        "current_setting('app.rls_bypass', true) = 'on' "
        "OR user_id::text = current_setting('app.current_user_id', true)"
    )


def upgrade() -> None:
    # ---- authorized public keys ----
    op.create_table(
        "sftp_authorized_keys",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The full OpenSSH single-line public key, e.g.
        # `ssh-ed25519 AAAAC3Nz... user@host`. NOT secret. Stored verbatim
        # so the server can re-parse it with asyncssh at auth time.
        sa.Column("public_key", sa.Text(), nullable=False),
        # SHA256 fingerprint (`SHA256:base64...`) of the key blob, computed
        # server-side at insert. Used for the unique constraint + so the
        # Settings UI can show "which key is this" without rendering the
        # whole blob. NOT a security boundary on its own.
        sa.Column("fingerprint", sa.Text(), nullable=False),
        # Free-text user label ("my laptop"). Optional.
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "last_used_at", sa.DateTime(timezone=True), nullable=True,
        ),
    )
    op.create_index(
        "ix_sftp_authorized_keys_user_id", "sftp_authorized_keys", ["user_id"],
    )
    # One physical key can only be registered once per user (re-adding is a
    # no-op the API surfaces as a 409). Scoped to (user_id, fingerprint) so
    # two different users MAY register the same key for their own accounts.
    op.create_index(
        "ix_sftp_authorized_keys_user_fpr",
        "sftp_authorized_keys",
        ["user_id", "fingerprint"],
        unique=True,
    )

    # ---- optional per-user SFTP password (Argon2 hash) ----
    op.create_table(
        "sftp_credentials",
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Argon2 hash of the SFTP-specific password. NEVER the account
        # password. NULL is impossible (row only exists once a password is
        # set); clearing the password DELETEs the row.
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "last_used_at", sa.DateTime(timezone=True), nullable=True,
        ),
    )

    # ---- RLS: both tables fenced to the owner (FORCE + bypass clause) ----
    for tbl in ("sftp_authorized_keys", "sftp_credentials"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {tbl}_user_isolation ON {tbl} "
            f"FOR ALL USING ({_user_id_expr()}) WITH CHECK ({_user_id_expr()})"
        )


def downgrade() -> None:
    for tbl in ("sftp_authorized_keys", "sftp_credentials"):
        op.execute(f"DROP POLICY IF EXISTS {tbl}_user_isolation ON {tbl}")
        op.execute(f"ALTER TABLE {tbl} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_sftp_authorized_keys_user_fpr", table_name="sftp_authorized_keys",
    )
    op.drop_index(
        "ix_sftp_authorized_keys_user_id", table_name="sftp_authorized_keys",
    )
    op.drop_table("sftp_credentials")
    op.drop_table("sftp_authorized_keys")
