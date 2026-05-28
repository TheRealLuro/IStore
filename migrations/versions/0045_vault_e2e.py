"""Vault → E2E drive (VLT-8 Phase 1).

Generalizes the shipped zero-knowledge vault (passwords + notes) into a
full **end-to-end encrypted, drive-like** store: nested folders + any
file type + specialized secure items (seed phrases, cards) — all opaque
to the server, none ever AI-touched.

The model (E2E_DESIGN.md REVISION 2): E2E is a *destination*. Content in
the Vault is end-to-end encrypted; the normal Drive is unchanged
(server-readable, AI with consent). So this migration only adds to the
vault surface — it does NOT touch images/folders/the Drive, and there is
no server keypair, no `ai_mode`, no per-folder AI flag.

What this adds:
  - vault_meta: an X25519 ACCOUNT KEYPAIR (public stored in clear;
    private stored master-key-wrapped). The public key lets others seal
    a file-key to this user when a vault item is shared; the private key
    (unwrapped client-side with the master password) opens shares + file
    keys. Nullable so existing vaults keep working until the client
    populates them on next unlock.
  - vault_folders: nested folders for the vault drive. Folder NAMES are
    encrypted client-side (name_nonce + name_ct) — even the folder name
    is opaque to the server. RLS + FORCE, cascade-delete with the user
    and with the parent folder.
  - vault_items: folder placement (folder_id), broadened kind set
    (file | password | note | seed | card), and file-blob support
    (storage_key → MinIO object holding the ciphertext, size_bytes,
    wrapped_key → the per-file AES key sealed to the owner's account
    public key). Small items (password/note/seed/card) keep storing
    their ciphertext inline as before; only files offload to MinIO.

Everything stays under FORCE Row-Level Security on user_id.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0045_vault_e2e"
down_revision: Union[str, None] = "0044_vault"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. Account keypair on vault_meta (nullable; client populates) ---
    op.add_column(
        "vault_meta",
        sa.Column("account_public_key", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "vault_meta",
        sa.Column("enc_account_private_key", sa.LargeBinary(), nullable=True),
    )

    # --- 2. vault_folders (nested, encrypted names) ---
    op.create_table(
        "vault_folders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Self-referential parent; deleting a folder cascades to its
        # subfolders (and, via vault_items.folder_id below, its items).
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vault_folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # Folder name is E2E too — opaque (nonce, ciphertext) to the server.
        sa.Column("name_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("name_ct", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_vault_folders_user_parent", "vault_folders", ["user_id", "parent_id"],
    )
    op.execute("ALTER TABLE vault_folders ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vault_folders FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY vault_folders_user_isolation ON vault_folders "
        "FOR ALL "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid) "
        "WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid)"
    )

    # --- 3. vault_items: folders + file blobs + broader kinds ---
    op.add_column(
        "vault_items",
        sa.Column(
            "folder_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vault_folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    # File blobs live in MinIO; the row keeps the object key + size + the
    # per-file AES key sealed to the owner's account public key. Small
    # items (password/note/seed/card) leave these null and keep their
    # ciphertext inline as before.
    op.add_column(
        "vault_items",
        sa.Column("storage_key", sa.String(512), nullable=True),
    )
    op.add_column(
        "vault_items",
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "vault_items",
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=True),
    )
    op.create_index("ix_vault_items_folder_id", "vault_items", ["folder_id"])

    # Broaden the kind CHECK from (password, note) to include the new
    # secure-item types + files.
    op.drop_constraint("ck_vault_items_kind", "vault_items", type_="check")
    op.create_check_constraint(
        "ck_vault_items_kind",
        "vault_items",
        "kind IN ('password', 'note', 'seed', 'card', 'file')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_vault_items_kind", "vault_items", type_="check")
    op.create_check_constraint(
        "ck_vault_items_kind",
        "vault_items",
        "kind IN ('password', 'note')",
    )
    op.drop_index("ix_vault_items_folder_id", table_name="vault_items")
    op.drop_column("vault_items", "wrapped_key")
    op.drop_column("vault_items", "size_bytes")
    op.drop_column("vault_items", "storage_key")
    op.drop_column("vault_items", "folder_id")

    op.execute("DROP POLICY IF EXISTS vault_folders_user_isolation ON vault_folders")
    op.drop_index("ix_vault_folders_user_parent", table_name="vault_folders")
    op.drop_table("vault_folders")

    op.drop_column("vault_meta", "enc_account_private_key")
    op.drop_column("vault_meta", "account_public_key")
