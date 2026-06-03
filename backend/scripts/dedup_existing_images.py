"""#174 — one-shot visual dedup of already-stored near-identical images.

Backfill counterpart to the worker hook
(`backend/worker/main.py::_dedup_image_against_oldest`). It applies the
SAME rule to the EXISTING library: within each user, group images whose
CLIP embeddings are near-identical (cosine similarity >= THRESHOLD), keep
the OLDEST of each group, repoint the others' CloudFiles onto the keeper,
and soft-delete the others.

Motivating case: iCloud holds ~33 distinct assets all named
"lp_image.heic" — the same logo saved many times. Each has different bytes
(so content-hash dedup misses them) but near-identical embeddings.

Cosine reuse: scores come from `Image.clip_embedding.cosine_distance(...)`
— the exact column + pgvector operator the semantic search uses
(`backend/api/search.py::_clip_search`). The vectors are L2-normalised
(`backend/vision/pipeline.py`), so similarity == 1 - cosine_distance.

SAFETY:
  * DRY-RUN by default — prints every group it WOULD merge and changes
    nothing. Pass `--apply` to actually repoint CloudFiles + soft-delete.
  * Repoint happens BEFORE soft-delete (same ordering as the hook and the
    cloud_sync hash-dedup block) so a survivor is always live + linked
    before its duplicate disappears.
  * The survivor's blob is never read or mutated.
  * Same-user only; images with NULL embeddings are skipped and counted.

Run:
    # preview (no changes):
    docker exec neuthek-backend python -m backend.scripts.dedup_existing_images
    # apply:
    docker exec neuthek-backend python -m backend.scripts.dedup_existing_images --apply
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.models import CloudFile, Image

logger = logging.getLogger("dedup_existing_images")

# Identical threshold + semantics to the worker hook. Kept as a literal
# here (rather than importing backend.worker.main) so running this script
# does NOT import the worker module / trigger its model-warm side effects.
# Two-tier, FILENAME-AWARE: EXACT merges regardless of filename; the looser
# SAME-NAME tier only merges re-saves of the same base filename — so
# distinct burst shots (IMG_9852 vs IMG_9853, ~0.98 cosine) are never merged.
DEDUP_COSINE_THRESHOLD = 0.98     # same-base-filename re-saves
DEDUP_EXACT_THRESHOLD = 0.995     # near-exact dup, any filename


async def _user_ids(session) -> list[UUID]:
    rows = (
        await session.execute(
            select(Image.user_id)
            .where(Image.deleted_at.is_(None))
            .group_by(Image.user_id)
        )
    ).all()
    return [r[0] for r in rows]


async def _null_embedding_count(session, user_id: UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(Image)
                .where(
                    Image.user_id == user_id,
                    Image.deleted_at.is_(None),
                    Image.clip_embedding.is_(None),
                )
            )
        ).scalar_one()
    )


async def _find_groups(session, user_id: UUID) -> list[dict]:
    """Greedy oldest-seeds-the-group clustering for one user.

    Walk the user's non-deleted, embedded images oldest-first. The first
    not-yet-consumed image becomes a keeper; every strictly-newer image
    within the cosine threshold of it is pulled into that keeper's group
    (and consumed so it can't seed or join another group). This mirrors
    the hook's "keep the oldest, fold newer near-identicals into it" rule
    exactly, and is deterministic via the (uploaded_at, id) ordering.

    Returns a list of groups; each group is a dict:
        {"keeper": (id, uploaded_at, filename),
         "members": [(id, uploaded_at, filename, cosine_similarity), ...]}
    Only groups with >= 1 member (i.e. an actual duplicate found) are
    returned.
    """
    rows = (
        await session.execute(
            select(
                Image.id, Image.uploaded_at, Image.original_filename,
                Image.clip_embedding,
            )
            .where(
                Image.user_id == user_id,
                Image.deleted_at.is_(None),
                Image.clip_embedding.is_not(None),
            )
            .order_by(Image.uploaded_at.asc(), Image.id.asc())
        )
    ).all()

    consumed: set[UUID] = set()
    samename_max = 1.0 - DEDUP_COSINE_THRESHOLD   # 0.02 distance
    exact_max = 1.0 - DEDUP_EXACT_THRESHOLD       # 0.005 distance
    groups: list[dict] = []

    for keeper_id, keeper_uploaded, keeper_name, keeper_emb in rows:
        if keeper_id in consumed:
            continue
        consumed.add(keeper_id)

        # Candidate duplicates: same user, not deleted, embedded, NOT this
        # row, and within the cosine threshold of the keeper. Computed with
        # the same pgvector cosine operator as search. We then filter to
        # strictly-newer + not-already-consumed in Python so a row can only
        # belong to one group (the oldest keeper that claims it).
        dist = Image.clip_embedding.cosine_distance(keeper_emb)
        keeper_base = re.sub(r"\.[^.]+$", "", keeper_name or "").lower()
        cand_base = func.lower(
            func.regexp_replace(Image.original_filename, r"\.[^.]+$", "")
        )
        cands = (
            await session.execute(
                select(Image.id, Image.uploaded_at, Image.original_filename,
                       dist.label("distance"))
                .where(
                    Image.user_id == user_id,
                    Image.id != keeper_id,
                    Image.deleted_at.is_(None),
                    Image.clip_embedding.is_not(None),
                    or_(
                        # near-exact dup, any filename
                        dist <= exact_max,
                        # looser, but only for a same-base-filename re-save
                        and_(dist <= samename_max, cand_base == keeper_base),
                    ),
                )
                .order_by(Image.uploaded_at.asc(), Image.id.asc())
            )
        ).all()

        members: list[tuple] = []
        keeper_key = (keeper_uploaded, keeper_id)
        for cid, c_uploaded, c_name, c_dist in cands:
            if cid in consumed:
                continue
            # Only fold STRICTLY-NEWER rows into the keeper — never delete
            # something older than the keeper (it would itself be a keeper).
            if (c_uploaded, cid) <= keeper_key:
                continue
            consumed.add(cid)
            members.append(
                (cid, c_uploaded, c_name, 1.0 - float(c_dist))
            )

        if members:
            groups.append(
                {
                    "keeper": (keeper_id, keeper_uploaded, keeper_name),
                    "members": members,
                }
            )

    return groups


async def _apply_group(session, keeper_id: UUID, member_ids: list[UUID]) -> None:
    """Repoint every member's CloudFiles onto the keeper, then soft-delete
    the members. Repoint-before-delete, identical to the worker hook."""
    now = datetime.now(timezone.utc)
    await session.execute(
        sa_update(CloudFile)
        .where(CloudFile.local_image_id.in_(member_ids))
        .values(local_image_id=keeper_id)
    )
    await session.execute(
        sa_update(Image)
        .where(Image.id.in_(member_ids))
        .values(deleted_at=now)
    )


def _fmt_name(name: str | None) -> str:
    return name if name else "(no filename)"


async def run(apply: bool) -> int:
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    total_users = 0
    total_groups = 0
    total_merged = 0
    total_null = 0

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== visual dedup ({mode}) — threshold cosine >= "
          f"{DEDUP_COSINE_THRESHOLD} ===")

    try:
        async with Session() as session:
            user_ids = await _user_ids(session)

        for user_id in user_ids:
            async with Session() as session:
                null_count = await _null_embedding_count(session, user_id)
                groups = await _find_groups(session, user_id)

            if null_count:
                total_null += null_count

            if not groups:
                if null_count:
                    print(f"\nuser {user_id}: no duplicate groups "
                          f"({null_count} image(s) skipped — NULL embedding)")
                continue

            total_users += 1
            print(f"\nuser {user_id}: {len(groups)} duplicate group(s)"
                  + (f", {null_count} image(s) skipped (NULL embedding)"
                     if null_count else ""))

            for g in groups:
                keeper_id, keeper_uploaded, keeper_name = g["keeper"]
                members = g["members"]
                total_groups += 1
                total_merged += len(members)
                print(f"  KEEP  {keeper_id}  {_fmt_name(keeper_name)}  "
                      f"(uploaded {keeper_uploaded})")
                for cid, c_uploaded, c_name, sim in members:
                    print(f"    merge {cid}  {_fmt_name(c_name)}  "
                          f"cosine={sim:.4f}")

            if apply:
                async with Session() as session:
                    for g in groups:
                        keeper_id = g["keeper"][0]
                        member_ids = [m[0] for m in g["members"]]
                        await _apply_group(session, keeper_id, member_ids)
                    await session.commit()
    finally:
        await engine.dispose()

    print("\n=== summary ===")
    print(f"users with duplicates : {total_users}")
    print(f"duplicate groups      : {total_groups}")
    print(f"images merged away    : {total_merged}")
    print(f"images skipped (NULL embedding): {total_null}")
    if not apply:
        print("\nDRY-RUN — nothing changed. Re-run with --apply to merge.")
    else:
        print("\nAPPLIED — CloudFiles repointed and duplicates soft-deleted.")
    return 0


def main() -> None:
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description=(
            "Visually dedup near-identical stored images per user "
            "(cosine >= 0.98). DRY-RUN by default."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually repoint CloudFiles + soft-delete duplicates. "
             "Without this flag the script only prints what it would do.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.apply)))


if __name__ == "__main__":
    main()
