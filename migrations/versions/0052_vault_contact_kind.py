"""vault contact item kind (VLT-7 import-from-file)

Broadens the `vault_items.kind` CHECK constraint to admit a new
`'contact'` value, alongside the existing
`('password', 'note', 'seed', 'card', 'file')` set introduced by
migration 0045.

Why: VLT-7 ("import-from-file") lets a user drop a structured data file
INTO the vault and have it auto-import as the matching structured vault
item instead of an opaque encrypted blob. A `.vcf` / vCard becomes a
first-class **Contact** item (name, phones, emails, addresses, org, url,
note). All parsing + mapping happens CLIENT-SIDE in the browser; the
item is then AES-256-GCM-encrypted under the user's vault key exactly
like every other secure item. As with `password|note|seed|card`, the
`kind` string is the ONLY non-encrypted attribute — it just selects the
client viewer — so admitting `'contact'` leaks nothing the server didn't
already see for the other kinds. The zero-knowledge guarantee is
unchanged.

Purely additive + reversible: it only widens (and, on downgrade,
re-narrows) a CHECK constraint. No table, column, index, policy, or row
is otherwise touched. The model column is `String(16)`, which already
fits `'contact'` (7 chars), so no column change is needed.

Revision ID: 0052_vault_contact_kind
Revises: 0051_rejected_face_embeddings
Create Date: 2026-05-31 00:00:00
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0052_vault_contact_kind"
down_revision: Union[str, None] = "0051_rejected_face_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The full kind set after this migration. Kept in one place so the up/down
# directions can't drift. `contact` is the only addition vs. migration 0045.
_KINDS_WITH_CONTACT = "'password', 'note', 'seed', 'card', 'file', 'contact'"
_KINDS_WITHOUT_CONTACT = "'password', 'note', 'seed', 'card', 'file'"


def upgrade() -> None:
    # Replace the CHECK in place. Postgres validates the new constraint
    # against existing rows; every current row already holds one of the
    # prior kinds, so this is a non-breaking widening.
    op.drop_constraint("ck_vault_items_kind", "vault_items", type_="check")
    op.create_check_constraint(
        "ck_vault_items_kind",
        "vault_items",
        f"kind IN ({_KINDS_WITH_CONTACT})",
    )


def downgrade() -> None:
    # Narrow back to the 0045 set. NOTE: if any `contact` items exist they
    # must be migrated/removed before downgrading, or the new constraint
    # will fail validation — the same caveat 0045's own downgrade carries.
    op.drop_constraint("ck_vault_items_kind", "vault_items", type_="check")
    op.create_check_constraint(
        "ck_vault_items_kind",
        "vault_items",
        f"kind IN ({_KINDS_WITHOUT_CONTACT})",
    )
