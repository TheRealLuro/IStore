"""vault ssh_key + id item kinds (VLT secrets expansion)

Broadens the `vault_items.kind` CHECK constraint to admit two new
values — `'ssh_key'` and `'id'` — alongside the existing
`('password', 'note', 'seed', 'card', 'file', 'contact')` set introduced
by migration 0052.

Why:
  * `ssh_key` — a first-class **SSH key** item: a label/host, the private
    key (a multiline secret), the public key, an optional passphrase, and
    a key type (ed25519/rsa/ecdsa). Lets users keep SSH credentials in the
    vault instead of an opaque encrypted blob or a plaintext `~/.ssh` file.
  * `id` — a first-class **identity document** item (Passport / Driver's
    license / National ID / Residence permit / other): document type, full
    name, document number, country/issuer, issue + expiry dates, date of
    birth, note. Splits identity documents out of the payment-`card` kind so
    each gets deliberate, type-specific fields.

As with every other kind, ALL parsing/formatting happens CLIENT-SIDE in the
browser; the item is then AES-256-GCM-encrypted under the user's vault key
exactly like every other secure item. The `kind` string is the ONLY
non-encrypted attribute — it just selects the client viewer — so admitting
`'ssh_key'` / `'id'` leaks nothing the server didn't already see for the
other kinds. The zero-knowledge guarantee is unchanged.

Purely additive + reversible: it only widens (and, on downgrade,
re-narrows) a CHECK constraint. No table, column, index, policy, or row is
otherwise touched. The model column is `String(16)`, which already fits
`'ssh_key'` (7 chars) and `'id'` (2 chars), so no column change is needed.

Revision ID: 0053_vault_ssh_id_kinds
Revises: 0052_vault_contact_kind
Create Date: 2026-05-31 00:00:00
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0053_vault_ssh_id_kinds"
down_revision: Union[str, None] = "0052_vault_contact_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The full kind set on each side of this migration. Kept in one place so the
# up/down directions can't drift. `ssh_key` + `id` are the only additions vs.
# migration 0052.
_KINDS_NEW = "'password', 'note', 'seed', 'card', 'file', 'contact', 'ssh_key', 'id'"
_KINDS_OLD = "'password', 'note', 'seed', 'card', 'file', 'contact'"


def upgrade() -> None:
    # Replace the CHECK in place. Postgres validates the new constraint
    # against existing rows; every current row already holds one of the
    # prior kinds, so this is a non-breaking widening.
    op.drop_constraint("ck_vault_items_kind", "vault_items", type_="check")
    op.create_check_constraint(
        "ck_vault_items_kind",
        "vault_items",
        f"kind IN ({_KINDS_NEW})",
    )


def downgrade() -> None:
    # Narrow back to the 0052 set. NOTE: if any `ssh_key` / `id` items exist
    # they must be migrated/removed before downgrading, or the new constraint
    # will fail validation — the same caveat 0045/0052's own downgrades carry.
    op.drop_constraint("ck_vault_items_kind", "vault_items", type_="check")
    op.create_check_constraint(
        "ck_vault_items_kind",
        "vault_items",
        f"kind IN ({_KINDS_OLD})",
    )
