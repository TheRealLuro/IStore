"""Cloud-sync exclusion tracking.

User-reported bug: "I deleted this folder 20 times and it keeps
coming back, most of the files I delete come back."

Root cause: the cloud sync's diff logic is keyed on
`(user_id, provider, remote_id)` for files and on folder name+parent
for folders. When the user soft-deletes (or hard-deletes) a synced
Image, the `cloud_files` row survives — `local_image_id` becomes
NULL (FK ON DELETE SET NULL) or still points at a soft-deleted row.
The NEXT sync sees the file on Drive, finds the cloud_files row,
attaches a fresh Image to it, and the file reappears in the
gallery. Same shape for folders: the sync's `_ensure_remote_folder_tree`
filters by `deleted_at IS NULL`, so a soft-deleted folder is invisible
to the lookup and a NEW folder with the same name gets created on
every sync.

This migration adds two pieces of tombstone state:

  1. `cloud_files.excluded_at` (timestamptz NULL): when the user
     deletes the Image attached to a cloud_files row, we stamp this
     column. Sync then skips any cloud_files entry where it's set.

  2. `folders.cloud_provider` + `folders.cloud_remote_path`: marks
     a folder as "synced from a cloud source." When the user deletes
     such a folder, the soft-delete is honored by sync — it WON'T
     recreate the folder under the same name+parent because the
     sync now looks up folders including soft-deleted ones and
     respects the deleted_at marker.

Backfill: for every cloud_files row whose local_image_id is NULL
(image already hard-deleted) we set excluded_at to now. Without
this, an existing user with a cleaned-up library would still see
all their previously-deleted files come back on the next sync.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0039_cloud_exclusion_tracking"
down_revision: Union[str, None] = "0038_original_retention_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # cloud_files.excluded_at — tombstone for "user said no, don't
    # re-import this file on the next sync." NULL means "in sync,
    # behave normally"; non-NULL means "user deleted the local copy,
    # skip the remote file."
    op.add_column(
        "cloud_files",
        sa.Column(
            "excluded_at", sa.TIMESTAMP(timezone=True), nullable=True,
        ),
    )
    op.create_index(
        "cloud_files_excluded_at_idx",
        "cloud_files",
        ["user_id", "provider", "excluded_at"],
    )

    # folders.cloud_provider + folders.cloud_remote_path — identifies
    # folders the sync worker created. NULL on every folder the user
    # made manually via the UI; set on the synthesized "Google Drive"
    # root + every subfolder the worker mirrored.
    op.add_column(
        "folders",
        sa.Column("cloud_provider", sa.String(16), nullable=True),
    )
    op.add_column(
        "folders",
        sa.Column("cloud_remote_path", sa.Text(), nullable=True),
    )
    op.create_index(
        "folders_cloud_path_idx",
        "folders",
        ["user_id", "cloud_provider", "cloud_remote_path"],
    )

    # Backfill: any cloud_files row whose Image is already gone is a
    # tombstone-by-implication — without this, the next sync resurrects
    # every file the user has deleted to date.
    op.execute("""
        UPDATE cloud_files
        SET excluded_at = NOW()
        WHERE local_image_id IS NULL
           OR local_image_id IN (
               SELECT id FROM images WHERE deleted_at IS NOT NULL
           )
    """)


def downgrade() -> None:
    op.drop_index("folders_cloud_path_idx", table_name="folders")
    op.drop_column("folders", "cloud_remote_path")
    op.drop_column("folders", "cloud_provider")
    op.drop_index("cloud_files_excluded_at_idx", table_name="cloud_files")
    op.drop_column("cloud_files", "excluded_at")
