"""§C1.2 — AI-suggested smart names.

Produces ≤ 3 short, filename-safe proposals for an image from the
already-computed summary signals (topic, summary, points, scene_label,
named people, capture date). We deliberately **reuse the existing
summary** rather than re-invoke Florence-2 + Qwen — running the heavy
captioner per rename keystroke would be wasteful and rate-limit
hostile.

When the image has no summary yet (`pending_summary=True`), we fall
back to deterministic suggestions derived from filename + scene label
+ capture date. The frontend shows them anyway so the user has
something to click; once the summarizer drains the queue, the next
"Regenerate" press picks up the richer signals.

Filename safety contract (mirrors `upload_validation.validate_image_filename`):

  - extension preserved verbatim (case-folded to lower)
  - no path separators, no control chars
  - max 60 characters in the base name (leaves room for ext + dedup
    suffix without busting the 255-byte filesystem limit)
  - words joined with `-`, lowercased
  - dedups identical proposals before returning

No row writes; pure read-only. The user picks one and the existing
`PATCH /images/{id}/name` flow validates + persists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Face, FaceDetection, Image, ImageGeo, Person


# Filename charset: ASCII letters, digits, `.`, `-`, `_`. Everything
# else (including spaces, punctuation, non-ASCII) collapses to `-`.
_SAFE_TOKEN_RX = re.compile(r"[^A-Za-z0-9._-]+")
_MULTI_DASH_RX = re.compile(r"-+")
_EDGE_DASH_RX = re.compile(r"^-+|-+$")

# Word noise to drop from the topic / summary before slugging — these
# words rarely add discriminative information to a filename.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for",
    "from", "had", "has", "have", "he", "her", "his", "i", "in", "is",
    "it", "its", "of", "on", "or", "she", "that", "the", "their",
    "them", "they", "this", "to", "was", "we", "were", "with",
    "you", "your", "image", "picture", "photo", "video", "document",
    "file", "untitled",
}

# Max base length (excluding `.ext`). 60 keeps us well clear of the
# 255-byte ext4/NTFS / 255-character Windows limit even when the user
# follows up with a manual dedup suffix.
_MAX_BASE = 60


@dataclass
class NameSuggestion:
    """One proposal — `name` is the full `{base}.{ext}` filename, ready
    to drop into the rename input. `why` is a 1-sentence rationale the
    UI can show below the chip."""
    name: str
    why: str


def _slug(text: str, *, max_words: int = 5) -> str:
    """Turn a free-text snippet into a filename-safe slug.

    - Lowercases.
    - Strips stopwords.
    - Joins the first `max_words` meaningful tokens with `-`.
    - Collapses repeated `-` and trims edge dashes.
    - Truncates to `_MAX_BASE` characters so callers don't need to
      re-check downstream.
    """
    if not text:
        return ""
    # Replace everything non-safe with a space, lowercase, split.
    cleaned = _SAFE_TOKEN_RX.sub(" ", text).strip().lower()
    if not cleaned:
        return ""
    tokens = [t for t in cleaned.split() if t and t not in _STOPWORDS]
    if not tokens:
        # Fall back to the raw tokens if the stopword filter ate them
        # all (e.g. summary was "the image" → empty).
        tokens = cleaned.split()
    joined = "-".join(tokens[:max_words])
    joined = _MULTI_DASH_RX.sub("-", joined)
    joined = _EDGE_DASH_RX.sub("", joined)
    return joined[:_MAX_BASE]


def _ext_from_filename(filename: str | None) -> str:
    """Return the canonical extension (lowercase, no dot). Empty when
    the filename has no extension at all."""
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _date_prefix(value: Optional[datetime]) -> str:
    """`2024-06-01` style prefix from a datetime, or empty string."""
    if value is None:
        return ""
    try:
        return value.date().isoformat()
    except Exception:
        return ""


def _stamp(base: str, ext: str, suffix: str) -> str:
    """Compose a final `{base}-{suffix}.{ext}` while keeping the total
    length under the filesystem ceiling. `suffix` is allowed to be
    empty (no dash inserted then)."""
    base = base.strip("-") or "untitled"
    if suffix:
        # Reserve space for `-{suffix}.{ext}` when truncating the base.
        budget = max(1, _MAX_BASE - (len(suffix) + 1))
        base = base[:budget].rstrip("-") or "untitled"
        composed = f"{base}-{suffix}"
    else:
        composed = base[:_MAX_BASE].rstrip("-") or "untitled"
    composed = _MULTI_DASH_RX.sub("-", composed)
    composed = _EDGE_DASH_RX.sub("", composed)
    if ext:
        return f"{composed}.{ext}"
    return composed


def _dedup(suggestions: Iterable[NameSuggestion]) -> list[NameSuggestion]:
    """Drop later proposals whose filename matches one already in the
    list (case-insensitive). Preserves the first-seen reason."""
    seen: set[str] = set()
    out: list[NameSuggestion] = []
    for s in suggestions:
        key = s.name.casefold()
        if key in seen or not s.name or s.name.startswith("."):
            continue
        seen.add(key)
        out.append(s)
    return out


async def suggest_names_for_image(
    session: AsyncSession,
    *,
    image: Image,
    user_id: UUID,
    limit: int = 3,
) -> list[NameSuggestion]:
    """Build up to `limit` filename proposals for `image`.

    Pure read-only — touches the image row, image_geo (when consent is
    granted), and face → person rows. Caller is responsible for
    owner-isolation (load the image via `_load_owned_image` first).
    """
    ext = _ext_from_filename(image.original_filename)
    # Source signals — each may be None / empty.
    topic_slug = _slug(image.summary_topic or "", max_words=4)
    summary_slug = _slug(image.summary or "", max_words=5)
    scene = _slug(image.scene_label or "", max_words=2)

    # Optional location — only when the user has retained GPS rows
    # (gps_retention consent). image_geo.place is the reverse-geocoded
    # locality when the worker has filled it in.
    geo = (
        await session.execute(
            select(ImageGeo).where(ImageGeo.image_id == image.id)
        )
    ).scalar_one_or_none()
    place_slug = _slug(getattr(geo, "place", None) or "", max_words=2) if geo else ""

    # Capture date prefix — falls back to upload date when EXIF is
    # absent so the date suggestion isn't blank for the modal pass.
    capture = geo.taken_at if geo and getattr(geo, "taken_at", None) else image.uploaded_at
    date = _date_prefix(capture)

    # People — surface the first named person's display_name. Cheap
    # JOIN; we only fetch the first hit so the cost is bounded.
    person_slug = ""
    person_row = (
        await session.execute(
            select(Person.display_name)
            .join(Face, Face.person_id == Person.id)
            .join(FaceDetection, FaceDetection.face_id == Face.id)
            .where(
                FaceDetection.image_id == image.id,
                Face.user_id == user_id,
                Person.user_id == user_id,
                Person.display_name.is_not(None),
            )
            .limit(1)
        )
    ).first()
    if person_row and person_row[0]:
        person_slug = _slug(person_row[0], max_words=2)

    suggestions: list[NameSuggestion] = []

    # 1. Topic + place — the "search-friendly" combination. Falls
    # back to scene_label when no topic exists yet.
    primary_subject = topic_slug or summary_slug or scene
    if primary_subject and place_slug:
        suggestions.append(NameSuggestion(
            name=_stamp(primary_subject, ext, place_slug),
            why="Topic from AI summary plus the captured location.",
        ))
    elif primary_subject:
        suggestions.append(NameSuggestion(
            name=_stamp(primary_subject, ext, ""),
            why="Topic distilled from the AI summary.",
        ))

    # 2. Date-prefixed for chronological sorting.
    if primary_subject and date:
        suggestions.append(NameSuggestion(
            name=_stamp(date, ext, primary_subject),
            why="Date-prefixed for chronological sorting.",
        ))
    elif date and (scene or place_slug):
        # No topic yet — fall back to date + scene.
        suggestions.append(NameSuggestion(
            name=_stamp(date, ext, scene or place_slug),
            why="Date-prefixed; topic will sharpen once the summary lands.",
        ))

    # 3. People-aware variant — surfaces a named person when present.
    if person_slug and primary_subject:
        suggestions.append(NameSuggestion(
            name=_stamp(person_slug, ext, primary_subject),
            why="Named person from face recognition plus the topic.",
        ))
    elif person_slug and date:
        suggestions.append(NameSuggestion(
            name=_stamp(person_slug, ext, date),
            why="Named person plus capture date.",
        ))

    # 4. Catch-all when we had absolutely nothing. This is the
    # "pending summary" path the FE used to fake locally.
    if not suggestions:
        fallback_base = scene or place_slug or "untitled"
        if date:
            suggestions.append(NameSuggestion(
                name=_stamp(date, ext, fallback_base),
                why="Capture date; rename once the summary finishes.",
            ))
        else:
            suggestions.append(NameSuggestion(
                name=_stamp(fallback_base, ext, ""),
                why="Scene-only — re-run after the summary completes.",
            ))

    return _dedup(suggestions)[:limit]
