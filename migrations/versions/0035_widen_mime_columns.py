"""Widen images.mime_type_{original,served} VARCHAR(64) → VARCHAR(128).

The OOXML MIMEs are 66-74 characters and overflow the original
64-character column. PPTX is the worst offender at 74:
`application/vnd.openxmlformats-officedocument.presentationml.presentation`.
Before this migration, uploading a DOCX / XLSX / PPTX raised
`StringDataRightTruncationError` mid-INSERT and the upload failed.

128 leaves headroom for parameterized MIMEs (e.g. `; charset=utf-8`)
and for any IANA-registered type we might accept later — no further
widen is realistic to need.

Forward + back: ALTER COLUMN TYPE on Postgres is essentially free
for `varchar(N)` → `varchar(M)` when M >= N (no rewrite), and the
back path widens-then-narrows; we DON'T enforce the narrow-direction
rewrite because some existing rows may already have the longer
MIME persisted via the §C10 file-type batch.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0035_widen_mime_columns"
down_revision: Union[str, None] = "0034_comments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "images",
        "mime_type_original",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
    op.alter_column(
        "images",
        "mime_type_served",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Rows with MIMEs longer than 64 chars (every persisted OOXML
    # upload after this migration ran) would be truncated by a
    # straight type-change back to varchar(64). Guard against silent
    # data loss by failing loudly instead.
    bind = op.get_bind()
    overflow = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM images "
            "WHERE octet_length(mime_type_original) > 64 "
            "   OR octet_length(mime_type_served)   > 64"
        )
    ).scalar() or 0
    if overflow:
        raise RuntimeError(
            f"Cannot downgrade: {overflow} image row(s) have a MIME "
            "longer than 64 chars. Clear or migrate those rows first."
        )
    op.alter_column(
        "images",
        "mime_type_served",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
    op.alter_column(
        "images",
        "mime_type_original",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
