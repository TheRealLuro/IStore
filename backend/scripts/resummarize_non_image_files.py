"""#184 — one-shot re-summarize of NON-IMAGE files that previously got a
thin / filename-only summary.

Before #184 the summarizer produced rich text only for images, videos,
audio, and prose documents (pdf/docx/xlsx/txt/md). Two categories fell
through to a near-empty stub:

  * CODE files (`.py`/`.js`/`.go`/`.java`/…) — stored as category
    ``document`` but NOT routed through text extraction, so they got
    "Document: <stem>." with no language, purpose, or library tags.
  * ARCHIVES (`.zip`/`.tar.gz`/…) — category ``other`` — got
    "Archive (.zip) — <stem>." with no view of their contents.

The summarizer now produces useful, searchable summaries for both (see
``backend/summarize.py``: ``_summarize_code`` / ``_summarize_other``).
This script backfills the EXISTING library so those rows get the new
summaries (and their CLIP summary embeddings, so semantic search finds
them).

What it does, per matching row:
  * flips ``pending_summary = True`` (so the FE shows the "Generating…"
    skeleton and ``/images/summarize-progress`` counts it), and
  * enqueues a ``summarize`` job onto the SAME Redis queue the
    ``neuthek-ml-worker`` consumes (``backend.jobs.enqueue_summarize``).
The actual Florence/Qwen/heuristic work runs in the ml-worker, exactly
like a fresh upload — this script only selects + enqueues, so it never
loads a model itself and is safe to run from the backend container.

SCOPE (default): only the two categories that were thin —
``document`` + ``other``. Existing image/video/audio summaries are NOT
touched. Override with ``--category`` to narrow further, or
``--all-categories`` to include image/video/audio too (e.g. after an
unrelated summarizer change). ``--only-thin`` (default) restricts to
rows whose current summary still looks like the old filename stub so a
re-run doesn't redo rows that already have a good summary; pass
``--force`` to re-summarize every matching row regardless.

SAFETY:
  * DRY-RUN by default — prints the rows it WOULD re-summarize and
    enqueues nothing. Pass ``--apply`` to flip pending_summary + enqueue.
  * Per-user fair-queue + the worker's own dedupe key mean enqueuing the
    same row twice is harmless (the second enqueue is dropped).
  * Soft-deleted rows are skipped. ``--limit`` caps the batch so a huge
    library doesn't flood Redis in one run.

Run:
    # preview (no changes), default scope (document + other):
    docker exec neuthek-backend python -m backend.scripts.resummarize_non_image_files

    # apply — flip pending + enqueue summarize jobs:
    docker exec neuthek-backend python -m backend.scripts.resummarize_non_image_files --apply

    # only archives, force every row, cap 1000:
    docker exec neuthek-backend python -m backend.scripts.resummarize_non_image_files \
        --apply --category other --force --limit 1000
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.jobs import enqueue_summarize
from backend.models import Image

logger = logging.getLogger("resummarize_non_image_files")

# Categories that were thin before #184. Code lives under "document";
# archives under "other".
DEFAULT_CATEGORIES = ("document", "other")

# A summary "looks like the old thin stub" when it matches one of these
# legacy fallback shapes. Used by --only-thin so a re-run skips rows that
# already carry a good (LLM/heuristic) summary.
#   _summarize_other (old): "Archive (.zip) — name." / "ZIP. " / "File."
#   _doc_summary_fallback : "Document: <stem>." / "Document about <x>."
#                           / "Document content could not be summarized."
_THIN_PATTERNS = (
    re.compile(r"^Archive \(\.[a-z0-9]+\)", re.IGNORECASE),
    re.compile(r"^Document:\s", re.IGNORECASE),
    re.compile(r"^Document about\s", re.IGNORECASE),
    re.compile(r"^Document content could not be summarized", re.IGNORECASE),
    re.compile(r"^[A-Z0-9]+\.?$"),  # bare "ZIP." / "BIN." stub
    re.compile(r"^[A-Z0-9]+ file\b", re.IGNORECASE),  # "BIN file — x."
    re.compile(r"^File\.?$", re.IGNORECASE),
)


def _looks_thin(summary: str | None) -> bool:
    """True when `summary` is empty or matches a legacy thin-stub shape."""
    if not summary or not summary.strip():
        return True
    s = summary.strip()
    return any(p.match(s) for p in _THIN_PATTERNS)


async def _select_rows(
    session, categories: tuple[str, ...], force: bool, limit: int,
) -> list[tuple[UUID, str | None, str | None, str | None]]:
    """Return (id, category, original_filename, summary) for candidate rows.

    `force=False` filters to rows that have no summary yet OR are already
    flagged pending — the thin-shape check is applied in Python afterward
    (regex on `summary` is awkward in SQL across dialects). `force=True`
    returns every non-deleted row in `categories`.
    """
    stmt = (
        select(
            Image.id, Image.category, Image.original_filename, Image.summary,
        )
        .where(
            Image.deleted_at.is_(None),
            Image.category.in_(categories),
        )
        .order_by(Image.uploaded_at.desc())
        .limit(limit)
    )
    if not force:
        # Candidates worth looking at: missing summary, pending, or
        # present-but-possibly-thin (filtered in Python by the caller).
        stmt = stmt.where(
            or_(
                Image.summary.is_(None),
                Image.pending_summary.is_(True),
                Image.summary.isnot(None),  # keep all; Python filters thin
            )
        )
    return list((await session.execute(stmt)).all())


async def run(
    apply: bool,
    categories: tuple[str, ...],
    force: bool,
    only_thin: bool,
    limit: int,
) -> int:
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    mode = "APPLY" if apply else "DRY-RUN"
    scope = ", ".join(categories)
    sel = "every matching row" if force else (
        "thin / missing summaries only" if only_thin
        else "missing or pending summaries"
    )
    print(f"=== re-summarize non-image files ({mode}) ===")
    print(f"categories: {scope}   selecting: {sel}   limit: {limit}\n")

    chosen: list[tuple[UUID, str | None, str | None]] = []
    per_cat: dict[str, int] = {}
    try:
        async with Session() as session:
            rows = await _select_rows(session, categories, force, limit)

        for img_id, cat, fname, summary in rows:
            if not force and only_thin and not _looks_thin(summary):
                continue
            chosen.append((img_id, cat, fname))
            per_cat[cat or "?"] = per_cat.get(cat or "?", 0) + 1

        # Print what we'd do.
        for img_id, cat, fname in chosen[:200]:
            print(f"  {cat:9} {img_id}  {fname or '(no filename)'}")
        if len(chosen) > 200:
            print(f"  … and {len(chosen) - 200} more")

        if apply and chosen:
            ids = [c[0] for c in chosen]
            # Flag pending in bulk so the FE shows the regen skeleton and
            # /summarize-progress counts these. We do NOT clear `summary`
            # — leaving the old text in place keeps search working until
            # the new summary lands (the worker's _mark_done overwrites it).
            async with Session() as session:
                await session.execute(
                    sa_update(Image)
                    .where(Image.id.in_(ids))
                    .values(pending_summary=True)
                )
                await session.commit()

            # Enqueue summarize jobs onto the ml-worker queue. The worker's
            # dedupe key makes a duplicate enqueue a no-op, so this is safe
            # even if the script is re-run.
            enqueued = 0
            for img_id in ids:
                try:
                    ok = await enqueue_summarize(img_id)
                    if ok:
                        enqueued += 1
                except Exception:
                    logger.exception("enqueue failed for %s", img_id)
            print(f"\nflagged pending: {len(ids)}   enqueued: {enqueued}")
    finally:
        await engine.dispose()

    print("\n=== summary ===")
    for cat, n in sorted(per_cat.items()):
        print(f"  {cat:9}: {n}")
    print(f"  {'total':9}: {len(chosen)}")
    if not apply:
        print("\nDRY-RUN — nothing changed. Re-run with --apply to enqueue.")
    else:
        print("\nAPPLIED — rows flagged pending + summarize jobs enqueued.")
        print("The neuthek-ml-worker will process them; watch its logs or "
              "GET /images/summarize-progress.")
    return 0


def main() -> None:
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description=(
            "Re-summarize non-image files (code + archives) that got a "
            "thin summary before #184. DRY-RUN by default."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Flip pending_summary + enqueue summarize jobs. Without this "
             "flag the script only prints what it would do.",
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=["image", "video", "document", "audio", "other"],
        help="Restrict to this category (repeatable). Default: document + "
             "other (the categories that were thin).",
    )
    parser.add_argument(
        "--all-categories",
        action="store_true",
        help="Include image/video/audio too (e.g. after an unrelated "
             "summarizer change). Overrides --category.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-summarize EVERY matching row, not just thin/missing ones.",
    )
    parser.add_argument(
        "--no-only-thin",
        dest="only_thin",
        action="store_false",
        help="Without --force, include rows whose summary isn't obviously "
             "thin (default: skip rows that already have a good summary).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Max rows to process in one run (default 2000).",
    )
    parser.set_defaults(only_thin=True)
    args = parser.parse_args()

    if args.all_categories:
        categories = ("image", "video", "document", "audio", "other")
    elif args.category:
        categories = tuple(dict.fromkeys(args.category))
    else:
        categories = DEFAULT_CATEGORIES

    raise SystemExit(asyncio.run(
        run(
            apply=args.apply,
            categories=categories,
            force=args.force,
            only_thin=args.only_thin,
            limit=max(1, args.limit),
        )
    ))


if __name__ == "__main__":
    main()
