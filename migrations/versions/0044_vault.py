"""Zero-knowledge encrypted vault — passwords + secure notes (VLT-3).

This is the schema for a vault the SERVER CANNOT READ. The threat
model: even a full database + backup compromise, or a malicious/
compelled operator, must not yield plaintext passwords or notes.

How that holds (the crypto contract every layer agrees on):

  1. The user sets a MASTER PASSWORD in the browser. It is never sent
     to the server — not at setup, not at unlock, never.
  2. The browser derives a 256-bit VAULT KEY from the master password
     with PBKDF2-SHA256 over a per-user random salt (>= 600k
     iterations, OWASP 2023 floor). Argon2id-WASM is the documented
     upgrade; the `kdf` column records the algorithm so we can
     migrate without guessing.
  3. Each vault item is encrypted client-side with AES-256-GCM using
     the vault key + a fresh random 96-bit nonce. The server stores
     ONLY (nonce, ciphertext+tag). The plaintext is a JSON blob
     ({title, username, password, url, notes, …}) — even the TITLE
     is inside the ciphertext, so the server can't see it. Search
     happens client-side after unlock.
  4. A VERIFIER lets the client confirm "right master password?" on
     unlock WITHOUT the server knowing the password: at setup the
     client encrypts a random constant with the vault key and stores
     the (nonce, ciphertext). On unlock the client decrypts it; the
     AES-GCM auth tag only validates with the correct key. The server
     just holds opaque bytes.

The server's entire job here is dumb ciphertext storage + RLS. It
NEVER decrypts, NEVER sees the master password or the vault key, and
the API (VLT-4) validates payloads as base64 of bounded length but
treats them as opaque.

Tables:
  vault_meta  — one row per user: KDF params + salt + verifier.
  vault_items — one row per password/note: kind + nonce + ciphertext.

Both RLS + FORCE on user_id; both cascade-delete with the account so
"delete my account" wipes the vault with everything else.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0044_vault"
down_revision: Union[str, None] = "0043_images_sha256_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vault_meta",
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # KDF descriptor — algorithm + cost so we can migrate later.
        sa.Column("kdf", sa.String(32), nullable=False, server_default="PBKDF2-SHA256"),
        sa.Column("kdf_iterations", sa.Integer(), nullable=False),
        # Salt is NOT secret (it's public by design) — random per user
        # so two users with the same master password derive different
        # keys. Generated client-side at setup.
        sa.Column("kdf_salt", sa.LargeBinary(), nullable=False),
        # Verifier — (nonce, ciphertext) of a random constant encrypted
        # under the vault key. Lets the client check the password on
        # unlock; opaque to the server.
        sa.Column("verifier_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("verifier_ct", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "vault_items",
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
        # "password" | "note". The kind is the ONLY non-encrypted
        # attribute — it drives which client-side viewer renders the
        # decrypted blob. No title/username/url stored in clear.
        sa.Column("kind", sa.String(16), nullable=False),
        # AES-256-GCM: 96-bit nonce + ciphertext-with-tag. Opaque bytes.
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('password', 'note')",
            name="ck_vault_items_kind",
        ),
    )
    op.create_index("ix_vault_items_user_id", "vault_items", ["user_id"])
    op.create_index(
        "ix_vault_items_user_kind", "vault_items", ["user_id", "kind"],
    )

    # RLS — both tables fenced to the owner. FORCE so even the table
    # owner role is subject to the policy (defence in depth; the app
    # connects as a non-superuser but FORCE removes any doubt).
    for tbl in ("vault_meta", "vault_items"):
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {tbl}_user_isolation ON {tbl} "
            f"FOR ALL "
            f"USING (user_id = current_setting('app.current_user_id', true)::uuid) "
            f"WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid)"
        )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS vault_items_user_isolation ON vault_items")
    op.execute("DROP POLICY IF EXISTS vault_meta_user_isolation ON vault_meta")
    op.drop_index("ix_vault_items_user_kind", table_name="vault_items")
    op.drop_index("ix_vault_items_user_id", table_name="vault_items")
    op.drop_table("vault_items")
    op.drop_table("vault_meta")
