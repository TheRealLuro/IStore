"""Recall@K measurement for the /search endpoint.

A "held-out eval set" is a small JSON of (image_id, expected_queries[])
pairs hand-curated from the operator's own library. For each pair,
each expected query is fired against /search; the image_id is
considered "found" if it lands in the top-K hits.

The measurement reports:
  - Recall@K — fraction of (query, image) pairs where the image
    landed in the top K.
  - MRR@K — mean reciprocal rank for found hits (0 for misses).
  - Per-query breakdown for failures so the operator can see WHICH
    queries are missing and tune the prompt / vocab / blend weights.

Run after material changes to: the Qwen prompt, the concept vocab,
the CLIP/FTS blend weights, the embedding model, or the face-
detection threshold. Diff the recall against the previous run to
decide if the change was actually an improvement.

Usage (operator, against the live dev server):

    .venv/Scripts/python -m backend.eval.recall_at_5 \\
        --base-url http://localhost:8000 \\
        --auth-token <jwt> \\
        --eval-set backend/eval/eval_set.json \\
        [--k 5]

Eval-set JSON shape:

    [
      {
        "image_id": "9c0f...uuid...",
        "label": "snowy hike with Sasha",   # optional human label
        "queries": [
          "snowy mountain trail",
          "winter hike",
          "Sasha in the snow"
        ]
      },
      ...
    ]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Dataclasses (pure stdlib so the script can run without the backend's
# pydantic models loaded).
# ----------------------------------------------------------------------


@dataclass
class EvalCase:
    image_id: str
    queries: list[str]
    label: str | None = None


@dataclass
class QueryResult:
    case_image_id: str
    query: str
    expected_label: str | None
    found: bool
    rank: int | None  # 1-based rank within top-K; None if not in top-K
    top_k_ids: list[str] = field(default_factory=list)


@dataclass
class RecallResult:
    k: int
    total_pairs: int
    found_pairs: int
    recall_at_k: float
    mrr_at_k: float
    per_query: list[QueryResult]
    misses: list[QueryResult]

    def to_json(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "total_pairs": self.total_pairs,
            "found_pairs": self.found_pairs,
            "recall_at_k": self.recall_at_k,
            "mrr_at_k": self.mrr_at_k,
            "misses": [asdict(m) for m in self.misses],
        }


# ----------------------------------------------------------------------
# Core measurement
# ----------------------------------------------------------------------


async def measure_recall(
    search_fn,
    eval_set: Iterable[EvalCase],
    k: int = 5,
) -> RecallResult:
    """Compute recall@K + MRR@K against the provided search function.

    `search_fn` is an async callable `(query: str, limit: int) -> list[str]`
    returning the image_ids of the top-K hits (in rank order). Indirection
    via callable so the unit tests can pass a stub instead of hitting an
    HTTP endpoint.
    """
    per_query: list[QueryResult] = []
    misses: list[QueryResult] = []
    rr_sum = 0.0
    found = 0
    total = 0

    for case in eval_set:
        for q in case.queries:
            total += 1
            try:
                hits = await search_fn(q, k)
            except Exception:
                logger.exception("search_fn raised for %r", q)
                hits = []
            rank: int | None = None
            for idx, hit_id in enumerate(hits[:k], start=1):
                if hit_id == case.image_id:
                    rank = idx
                    break
            qr = QueryResult(
                case_image_id=case.image_id,
                query=q,
                expected_label=case.label,
                found=rank is not None,
                rank=rank,
                top_k_ids=list(hits[:k]),
            )
            per_query.append(qr)
            if rank is not None:
                found += 1
                rr_sum += 1.0 / rank
            else:
                misses.append(qr)

    return RecallResult(
        k=k,
        total_pairs=total,
        found_pairs=found,
        recall_at_k=(found / total) if total else 0.0,
        mrr_at_k=(rr_sum / total) if total else 0.0,
        per_query=per_query,
        misses=misses,
    )


# ----------------------------------------------------------------------
# CLI — runs against a live server
# ----------------------------------------------------------------------


def _load_eval_set(path: Path) -> list[EvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[EvalCase] = []
    for row in raw:
        out.append(
            EvalCase(
                image_id=row["image_id"],
                queries=list(row["queries"]),
                label=row.get("label"),
            )
        )
    return out


async def _http_search(base_url: str, auth_token: str):
    """Build an async search_fn that hits the running backend."""
    import httpx  # type: ignore

    client = httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {auth_token}"},
        timeout=30.0,
    )

    async def _search(query: str, limit: int) -> list[str]:
        r = await client.get(
            "/search/", params={"q": query, "limit": limit}
        )
        r.raise_for_status()
        return [hit["id"] for hit in r.json()]

    return client, _search


async def _amain(args: argparse.Namespace) -> int:
    eval_set = _load_eval_set(Path(args.eval_set))
    if not eval_set:
        print("eval set is empty — nothing to measure", file=sys.stderr)
        return 2

    client, search = await _http_search(args.base_url, args.auth_token)
    try:
        result = await measure_recall(search, eval_set, k=args.k)
    finally:
        await client.aclose()

    print(
        f"\nRecall@{result.k}: "
        f"{result.found_pairs}/{result.total_pairs} = "
        f"{result.recall_at_k:.3f}"
    )
    print(f"MRR@{result.k}:    {result.mrr_at_k:.3f}\n")

    if result.misses:
        print(f"--- {len(result.misses)} miss(es) ---")
        for m in result.misses:
            label = f" [{m.expected_label}]" if m.expected_label else ""
            print(f"  {m.case_image_id}{label}  query={m.query!r}")
            if m.top_k_ids:
                print(f"    actual top-{result.k}: {m.top_k_ids}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result.to_json(), indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote machine-readable result to {args.json_out}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="backend.eval.recall_at_5",
        description="Measure recall@K for the /search endpoint.",
    )
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--auth-token", required=True,
        help="JWT for an authenticated user — paste from the app's "
             "DevTools after logging in, or mint via /auth/login.",
    )
    parser.add_argument(
        "--eval-set", default="backend/eval/eval_set.json",
        help="Path to the held-out eval set JSON.",
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument(
        "--json-out", default=None,
        help="Write machine-readable result JSON here for diffing.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
