"""Vault public links (VLT-8 — anyone-with-the-link sharing).

Adds a second sharing mode beside the direct, account-to-account grants
(0046): a PUBLIC LINK that anyone can open, optionally password-protected.

Still zero-knowledge. The decryption key never reaches the server — it lives
in the URL FRAGMENT (the part after #, which browsers never send). The server
stores only an opaque sealed blob plus a high-entropy token. When the owner
sets a password, the link key is derived from BOTH the fragment secret AND the
password (PBKDF2 → mixed via HKDF), so the sealed blob can't be opened with the
link alone — the server holds a public salt + iteration count but never the
password or the key.

vault_public_links has NO row-level security on purpose: a public link is
meant to be readable by an unauthenticated visitor who only knows the token,
so the read path runs with no user context. Owner management endpoints fence
explicitly on owner_user_id; the unguessable token gates public reads. The row
holds only ciphertext + routing — no plaintext, no keys. Cascade-deletes with
the item or the owner, so deleting either revokes every link.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0047_vault_public_links"
down_revision: Union[str, None] = "0046_vault_shares"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vault_public_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # High-entropy, URL-safe public identifier (secrets.token_urlsafe(32)).
        sa.Column("token", sa.String(64), nullable=False),
        # One link per item; deleting the item (or owner) revokes it.
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
        sa.Column("kind", sa.String(16), nullable=False),
        # File links copy the object key + size so the public viewer can stream
        # ciphertext without any user context (the row has no RLS).
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        # Bundle sealed under the link key (fragment secret [+ password]) — opaque.
        sa.Column("sealed_payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "password_required", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        # Public PBKDF2 params, present iff password_required.
        sa.Column("kdf_salt", sa.LargeBinary(), nullable=True),
        sa.Column("kdf_iterations", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("token", name="uq_vault_public_links_token"),
        sa.UniqueConstraint("item_id", name="uq_vault_public_links_item"),
    )
    op.create_index(
        "ix_vault_public_links_owner", "vault_public_links", ["owner_user_id"],
    )
    # NOTE: intentionally NO row-level security — public links are read by
    # unauthenticated visitors keyed on the token. Owner management is fenced
    # in the application layer; the row contains only ciphertext + routing.


def downgrade() -> None:
    op.drop_index("ix_vault_public_links_owner", table_name="vault_public_links")
    op.drop_table("vault_public_links")
