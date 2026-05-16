"""§C2 — cloud-sync metadata: skip_ai_training + source provenance.

Drive's Limited Use policy forbids using Drive content to train AI
models. Files we ingest from Drive (or any other cloud provider whose
ToS imposes a similar restriction) need to skip the CLIP / Florence-2
/ Qwen passes by default. The user can opt back in per-source via
the Settings → Cloud sync UI.

Two columns added to `images`:

- **`skip_ai_training`** (boolean, default false). When true:
  - Vision pass (`_maybe_run_vision`) is skipped at `store_upload`.
  - Summarization (`summarize_image_id`) is skipped.
  - Face scan (`run_face_scan_one`) is skipped.
- **`source_provider`** (varchar(16), nullable). Identifies the cloud
  provider this image came from (`google_drive`, `github`, etc.) so
  per-source opt-in can later flip the flag in bulk.

`cloud_files` also gains:

- **`remote_parent_path`** (text, nullable) — the slash-separated
  Drive folder path the file lived under at the time of ingest. The
  sync worker uses this to materialize a corresponding Folder tree
  in neuthek so the user's Drive structure isn't flattened.
- **`sha256`** (bytea(32), nullable) — content fingerprint at last
  sync; used to skip re-downloads when `remote_modified` reset but
  the bytes didn't change.

Revision ID: 0029_c2_cloud_sync_metadata
Revises: 0028_c16_tags_unification
Create Date: 2026-05-16 23:55:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_c2_cloud_sync_metadata"
down_revision: Union[str, None] = "0028_c16_tags_unification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "images",
        sa.Column(
            "skip_ai_training",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "images",
        sa.Column("source_provider", sa.String(16), nullable=True),
    )
    # Partial index so "show me everything that came from Drive" is
    # fast; the column is mostly NULL for user-uploaded files.
    op.create_index(
        "images_source_provider_idx",
        "images",
        ["source_provider"],
        postgresql_where=sa.text("source_provider IS NOT NULL"),
    )

    op.add_column(
        "cloud_files",
        sa.Column("remote_parent_path", sa.Text(), nullable=True),
    )
    # NB: cloud_files.sha256 already shipped in migration 0014_cloud_sync;
    # don't re-add. The §C2 work just starts populating it.


def downgrade() -> None:
    op.drop_column("cloud_files", "remote_parent_path")
    op.drop_index("images_source_provider_idx", table_name="images")
    op.drop_column("images", "source_provider")
    op.drop_column("images", "skip_ai_training")
