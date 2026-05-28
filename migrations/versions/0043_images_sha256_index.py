"""Index on (user_id, sha256) for content-hash dedup.

SYNC-1 — the cloud-sync loop now dedups by content hash: before
creating an Image for a freshly-downloaded blob, it looks up an
existing non-deleted Image with the same (user_id, sha256). This
stops the same physical file syncing twice (e.g. a file the user
keeps in BOTH MEGA and Proton Drive, or a re-pull that lost its
CloudFile row) from creating duplicate gallery entries.

That lookup runs once per synced file, so it needs an index — a
sequential scan of a 50k-image library per file would make a large
first sync crawl. Partial index on the non-deleted rows keeps it
small (soft-deleted rows aren't dedup targets).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0043_images_sha256_index"
down_revision: Union[str, None] = "0042_search_telemetry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Partial index — only live rows are dedup candidates, so we don't
    # index tombstoned rows. `sha256` is a bytea; btree handles it fine
    # for equality lookups.
    op.execute(
        "CREATE INDEX images_user_sha256_idx ON images (user_id, sha256) "
        "WHERE deleted_at IS NULL AND sha256 IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS images_user_sha256_idx")
