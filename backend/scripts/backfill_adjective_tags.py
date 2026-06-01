"""Backfill: re-derive each image's tags from its ALREADY-STORED summary.

The file tags should be the descriptive ADJECTIVES that appear in the
file's AI summary, not the upload-time CLIP concept labels. New uploads
get this automatically (see `backend/summarize._mark_done`). This script
applies the same derivation to the EXISTING library WITHOUT re-running
any vision model — it only reads `images.summary` that's already in the
DB and rewrites the `tags` / `image_tags` rows.

For every non-deleted row that has a non-empty `summary`:
  * extract adjectives via `backend.summarize._extract_adjective_tags`
  * replace the row's machine tags (source IN ('clip','auto')) with
    those adjectives, leaving user-applied tags (source='user') intact

Idempotent: running it twice produces the same tag set. Rows whose
summary yields no adjectives are left untouched (their prior tags stay).

Run inside the ml-worker container (has NLTK + the data):

    docker exec neuthek-ml-worker \
        python -m backend.scripts.backfill_adjective_tags

Pass a user-id as the first arg to limit the backfill to one user.
"""

from __future__ import annotations

import sys

import psycopg2

from backend.config import settings
from backend.summarize import _extract_adjective_tags, _write_adjective_tags_sync


def main() -> int:
    only_user = sys.argv[1] if len(sys.argv) > 1 else None

    sync_url = settings.database_url_sync.replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    conn = psycopg2.connect(sync_url)
    conn.autocommit = False  # _write_adjective_tags_sync owns its own conn

    # Pull candidate rows (id, user_id, summary) up front. Read-only on
    # this connection; the per-row writes happen on the helper's own
    # connection, so we close this one before writing to avoid holding a
    # long transaction open across the whole backfill.
    where = "deleted_at IS NULL AND summary IS NOT NULL AND summary <> ''"
    params: tuple = ()
    if only_user:
        where += " AND user_id = %s"
        params = (only_user,)

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, user_id, summary FROM images WHERE {where} "
            f"ORDER BY summary_generated_at NULLS LAST",
            params,
        )
        rows = cur.fetchall()
    conn.close()

    total = len(rows)
    retagged = 0
    skipped_no_adj = 0
    failed = 0
    examples: list[tuple[str, list[str]]] = []

    print(f"backfill_adjective_tags: {total} summarized rows to process"
          + (f" (user {only_user})" if only_user else ""))

    for image_id, user_id, summary in rows:
        try:
            adjectives = _extract_adjective_tags(summary)
        except Exception as e:  # pragma: no cover - defensive
            failed += 1
            print(f"  ! extract failed for {image_id}: {e}")
            continue

        if not adjectives:
            skipped_no_adj += 1
            continue

        try:
            _write_adjective_tags_sync(image_id, user_id, adjectives)
            retagged += 1
            if len(examples) < 8:
                examples.append((str(image_id), adjectives))
            if retagged % 100 == 0:
                print(f"  … {retagged} re-tagged")
        except Exception as e:  # pragma: no cover - defensive
            failed += 1
            print(f"  ! write failed for {image_id}: {e}")

    print("---")
    print(f"re-tagged:        {retagged}")
    print(f"skipped (no adj): {skipped_no_adj}")
    print(f"failed:           {failed}")
    print(f"total candidates: {total}")
    if examples:
        print("examples (image_id -> tags):")
        for iid, adj in examples:
            print(f"  {iid}: {', '.join(adj)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
