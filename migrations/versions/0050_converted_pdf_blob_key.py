"""Office-document → PDF conversion output key (server-side Office viewing).

Adds the nullable `images.converted_pdf_blob_key` column. When an Office
file (docx/xlsx/pptx/odt/ods/odp/rtf/doc/…) is uploaded or synced, the
ml-worker's `convert_office` job renders it to PDF with headless
LibreOffice and stores the resulting PDF in the served bucket under this
key. The existing PDF page-rasterization endpoints (`/images/{id}/pdf-meta`
and `/images/{id}/pdf-page/{n}`) then serve pages from this converted PDF
instead of the raw original, so Office files flow through the SAME
server-rasterized PDF viewer with no new frontend viewer.

Purely additive + nullable — existing rows get NULL (no Office conversion
has run for them), so this is a zero-downtime, no-backfill migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0050_converted_pdf_blob_key"
down_revision: Union[str, None] = "0049_folder_embedding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "converted_pdf_blob_key",
            sa.Text(),
            nullable=True,
            comment=(
                "Served-bucket key of the PDF rendered from an Office "
                "document by the ml-worker convert_office job (headless "
                "LibreOffice). The PDF page-rasterization endpoints serve "
                "pages from this blob so Office files reuse the existing "
                "PDF viewer. NULL = not an Office doc / conversion pending "
                "or failed."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("images", "converted_pdf_blob_key")
