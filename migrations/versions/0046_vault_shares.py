"""Vault sharing via key-wrapping (VLT-8 Phase 5).

Lets a vault owner share a single item (a file or a secure item) with
another neuthek user WITHOUT revealing anything to the server. The owner's
client seals the item's content key (for files) or the item plaintext (for
secure items) to the recipient's account PUBLIC key; only the recipient's
private key (unwrapped from their own master password) can open it. The
server stores an opaque sealed blob plus routing (who → whom) and never sees
a key or any plaintext.

Vault shares are intentionally a SEPARATE surface from Drive shares: they
have NO comments and no public links — a share is a direct, revocable grant
to a named neuthek account, viewable read-only by the recipient.

What this adds:
  - users.vault_public_key: a clear-text mirror of vault_meta's account
    public key. vault_meta is FORCE-RLS to its owner, so it can't be read
    cross-user to seal to a recipient. The public key is public by design,
    so mirroring it onto the (cross-readable) users row lets a sharer fetch
    a recipient's key. Populated by the client on vault setup / key
    provisioning.
  - vault_share_grants: one row per (item, recipient). Holds the sealed
    bundle, the item kind, and — for file items — a copy of the MinIO
    storage_key + size so the recipient can stream the ciphertext without
    the server reading the (owner-RLS-fenced) vault_items row. RLS lets
    BOTH the owner and the recipient see/delete a grant; only the owner can
    create one. Cascade-deletes with the item, the owner, or the recipient.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0046_vault_shares"
down_revision: Union[str, None] = "0045_vault_e2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UID = "current_setting('app.current_user_id', true)::uuid"


def upgrade() -> None:
    # --- 1. Clear-text mirror of the account public key on users ---
    op.add_column(
        "users",
        sa.Column("vault_public_key", sa.LargeBinary(), nullable=True),
    )

    # --- 2. vault_share_grants ---
    op.create_table(
        "vault_share_grants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vault_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Cleartext item kind (same exposure as vault_items.kind) so the
        # recipient's list can render the right icon without opening the seal.
        sa.Column("kind", sa.String(16), nullable=False),
        # File items: a copy of the object key + size so the recipient can
        # stream the ciphertext (their RLS can't read the owner's vault_items
        # row). The recipient still needs the sealed per-file key to decrypt.
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        # The bundle sealed to the recipient's account public key — opaque.
        sa.Column("sealed_payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "item_id", "recipient_user_id", name="uq_vault_share_item_recipient",
        ),
    )
    op.create_index(
        "ix_vault_share_grants_recipient", "vault_share_grants", ["recipient_user_id"],
    )
    op.create_index(
        "ix_vault_share_grants_item", "vault_share_grants", ["item_id"],
    )

    op.execute("ALTER TABLE vault_share_grants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vault_share_grants FORCE ROW LEVEL SECURITY")
    # Both parties can see (and remove) a grant; only the owner can create it.
    op.execute(
        "CREATE POLICY vault_share_grants_access ON vault_share_grants "
        "FOR ALL "
        f"USING (owner_user_id = {_UID} OR recipient_user_id = {_UID}) "
        f"WITH CHECK (owner_user_id = {_UID})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS vault_share_grants_access ON vault_share_grants")
    op.drop_index("ix_vault_share_grants_item", table_name="vault_share_grants")
    op.drop_index("ix_vault_share_grants_recipient", table_name="vault_share_grants")
    op.drop_table("vault_share_grants")
    op.drop_column("users", "vault_public_key")
