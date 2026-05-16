"""Automatic query-expansion synonyms for the photo / document search domain.

CLIP already handles semantic similarity in embedding space — a query
of "vibrant" puts vivid + colorful + bright images near it in the
visual manifold, so CLIP-side ranking finds them without help. The
Postgres FTS pass that runs alongside CLIP, though, is purely lexical
— if the summary text says "colorful" and the user searched "vibrant",
FTS contributes zero to the blend even though the user obviously meant
both terms. This module expands the user's tokens with synonyms so FTS
catches those matches.

# Pipeline

For each query token we collect:

  1. **WordNet lemmas** — every Lemma name across every synset for the
     token, all parts of speech. This is the autonomous, linguistically
     rigorous baseline — `happy → glad, joyful, cheerful, ...`,
     `dog → domestic_dog, hound, ...`. The English WordNet is bundled
     with NLTK (3.0+); we load it once at first call, cache the
     `wordnet` module reference, and let NLTK handle the corpus
     download lazily (offline-safe if already on disk).

  2. **Visual-domain overlay** — a small hand-curated dict for terms
     where WordNet's linguistic sense doesn't match the visual sense
     a photographer uses. The canonical example: WordNet's `vibrant`
     leans on "vigorous / animated", not "colorful / vivid". The
     overlay supplies the visual-domain expansion in those cases. The
     overlay is kept tight (under ~30 entries) so it doesn't drift
     into a maintenance burden.

  3. **Library-vocabulary nearest neighbors** (optional) — when the
     CLIP text encoder is available AND the lazy vocabulary cache is
     built, we also project the token through CLIP and pick the top-N
     cosine-nearest words from the user's actual summary vocabulary.
     This is the "another AI model" pathway: synonyms inferred from
     the user's library rather than from a static dictionary. The
     vocabulary is built lazily from `images.summary` text on first
     use and refreshed in the background. Disabled silently when the
     [ml] extras aren't installed or the encoder isn't loaded.

# Output

`expand_query_to_tsquery(q)` returns a Postgres `to_tsquery`-friendly
string: per-token groups OR'd over `(token | syn1 | syn2 | ...)`,
AND-joined across tokens. Empty string when no usable tokens were
extracted; caller should fall back to `plainto_tsquery` on the raw
input in that case.

`SYNONYMS_INDEX[token] -> set[str]` mirrors the same expansion for
non-FTS callers (e.g. the keyword-overlap gate in the search route)
and is implemented as a thin wrapper around the expansion helpers.
"""
from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------- 2: visual-domain overlay ---------------------------------

# Kept intentionally short. Each entry covers a term where the visual /
# photographer sense isn't the WordNet primary sense. Resist adding
# entries WordNet already covers well; check first.
_VISUAL_OVERLAY: dict[str, list[str]] = {
    # Color saturation — WordNet's "vibrant" is vigorous/animated first.
    "vibrant":  ["colorful", "vivid", "saturated", "bold"],
    "vivid":    ["vibrant", "colorful", "intense"],
    "colorful": ["vibrant", "vivid", "multicolored", "rainbow"],
    "muted":    ["pastel", "subdued", "faded", "washed-out"],
    "moody":    ["dark", "shadowy", "atmospheric", "dim"],

    # Time of day — WordNet "dusk/dawn" pair fine; "golden hour" doesn't
    # tokenize as a single WordNet entry.
    "sunset":   ["dusk", "golden-hour", "twilight", "evening"],
    "sunrise":  ["dawn", "daybreak", "first-light", "morning"],

    # Common photographer terms WordNet doesn't link well.
    "selfie":   ["self-portrait", "front-camera", "me"],
    "screenshot":["screen-capture", "screen-grab", "capture"],
    "whiteboard":["board", "marker-board", "diagram"],
    "portrait": ["headshot", "selfie", "close-up", "face"],

    # Weather — WordNet's snow/rain lemmas are noun-only; we want
    # adjective + descriptive synonyms too.
    "snowy":    ["snow", "winter", "wintry", "frost"],
    "rainy":    ["rain", "drizzle", "stormy", "wet"],
    "foggy":    ["misty", "hazy", "fog"],

    # Visual composition.
    "minimal":  ["minimalist", "clean", "simple", "uncluttered"],
    "cozy":     ["warm", "intimate", "homey", "inviting"],
    "dramatic": ["striking", "intense", "epic", "bold"],
}


# ---------- 1: WordNet lemmas ----------------------------------------

_wn = None
_wn_lock = threading.Lock()


def _load_wordnet():
    """Lazy-load NLTK's WordNet corpus.

    Returns the `wordnet` module on success, None when nltk isn't
    installed or the corpus isn't downloaded. The first failed load
    is cached so we don't pay the import / NoSuchCorpusError cost on
    every query.
    """
    global _wn
    if _wn is not None:
        return _wn if _wn is not False else None
    with _wn_lock:
        if _wn is not None:
            return _wn if _wn is not False else None
        try:
            from nltk.corpus import wordnet as wn
            # Force load — `wordnet.synsets("test")` triggers the corpus
            # download lookup; if it fails we'd rather fail loud now
            # than at query time.
            wn.synsets("test")
            _wn = wn
            return wn
        except Exception as e:
            logger.info("WordNet unavailable, falling back to overlay-only: %s", e)
            _wn = False  # sentinel: tried and failed
            return None


@lru_cache(maxsize=2048)
def _wordnet_lemmas(token: str) -> tuple[str, ...]:
    """Return all WordNet lemma names for `token` across every synset
    and every part of speech. Underscored multi-word lemmas
    (`golden_age`) become hyphenated (`golden-age`) so they match the
    same `[a-z0-9-]` lexeme alphabet `expand_query_to_tsquery` accepts.

    Lemmas are deduped and lowercased; the token itself is filtered
    out so the overlay set doesn't double-count it. Capped via
    `MAX_LEMMAS_PER_TOKEN` further down to keep tsquery size in check
    on tokens with very large synset families (e.g. `run` has 50+
    lemmas in WordNet).
    """
    wn = _load_wordnet()
    if wn is None:
        return ()
    try:
        out: set[str] = set()
        for syn in wn.synsets(token):
            for lemma in syn.lemmas():
                name = lemma.name().lower().replace("_", "-")
                if name and name != token:
                    out.add(name)
        return tuple(sorted(out))
    except Exception:
        logger.exception("wordnet lookup failed for %r", token)
        return ()


# ---------- 3: optional CLIP-vocabulary nearest neighbors ------------
#
# Disabled by default (returns empty). When the user wants smarter
# domain-relevant synonyms, we can wire this to the existing CLIP text
# encoder + a lazy vocabulary cache built from `images.summary` text.
# Implementation lives in `synonyms_vocab.py` (TODO) so this module
# stays cheap to import.

def _clip_vocab_neighbors(token: str) -> tuple[str, ...]:
    # Hook for future enhancement — see module docstring (3).
    return ()


# ---------- combined expansion ---------------------------------------

MAX_LEMMAS_PER_TOKEN = 10


@lru_cache(maxsize=4096)
def expand_token(token: str) -> tuple[str, ...]:
    """All known synonyms for `token`, deduped and capped.

    Combines WordNet lemmas + visual overlay + (optionally) library
    nearest-neighbors. The visual overlay always wins on uniqueness —
    `vibrant`'s overlay (colorful, vivid, ...) and WordNet's lemmas
    (vivacious, animated, ...) are unioned. Token itself is excluded
    from the result.
    """
    token = token.lower().strip()
    if not token:
        return ()
    pool: set[str] = set()
    pool.update(_wordnet_lemmas(token))
    pool.update(_VISUAL_OVERLAY.get(token, ()))
    pool.update(_clip_vocab_neighbors(token))
    pool.discard(token)
    # Sort for stable cache + tsquery shape, then cap.
    return tuple(sorted(pool))[:MAX_LEMMAS_PER_TOKEN]


# ---------- public API ------------------------------------------------

# Backwards-compat name. Older callers (the keyword-overlap gate in
# api/search.py) treat SYNONYMS_INDEX as a dict lookup; provide one
# shaped the same way but backed by the lazy WordNet expansion. We
# can't be a regular dict (we'd need every token pre-populated), so
# wrap as a class with __getitem__ + .get(token, default).

class _SynonymsIndex:
    """Dict-shaped lazy lookup over expand_token()."""

    def __getitem__(self, key: str) -> set[str]:
        return set(expand_token(key))

    def get(self, key: str, default=None):  # noqa: D401 — dict-shape
        result = expand_token(key)
        if not result:
            return default if default is not None else set()
        return set(result)


SYNONYMS_INDEX = _SynonymsIndex()


_SAFE_LEX = re.compile(r"^[a-z0-9-]{2,40}$")


def _ts_quote(token: str) -> str | None:
    """Sanitize a token for inclusion in a Postgres `to_tsquery` literal.

    `to_tsquery` accepts lexeme strings + operators (`&`, `|`, `!`, `<->`).
    Anything else (quotes, parens, colons) is unsafe to inline. We only
    keep tokens that match `[a-z0-9-]{2,40}` — the vocabulary the
    Postgres `english` config tokenizes for our haystacks.

    Hyphenated multi-word lemmas (`golden-age`) get split into an
    AND'd pair so the expansion still matches when both words appear
    in the haystack, regardless of order.
    """
    token = token.lower().strip()
    if not _SAFE_LEX.match(token):
        return None
    if "-" in token:
        parts = [p for p in token.split("-") if p]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        return "(" + " & ".join(parts) + ")"
    return token


def expand_query_to_tsquery(q: str) -> str:
    """Build a `to_tsquery`-friendly expanded query string from the
    user's raw input.

    Strategy:
      - Tokenize the raw query into [a-z0-9-]+ words.
      - For each token, call `expand_token(token)` (WordNet + overlay
        + library-vocab, deduped).
      - Build `(token | syn1 | syn2 | ...)` per token.
      - AND the per-token groups together.

    Example: `vibrant sunset` becomes (roughly):
      `(vibrant | colorful | vivid | saturated | ...) &
       (sunset | dusk | (golden & hour) | twilight | ...)`

    Stopwords are NOT dropped here — Postgres' `english` config does
    that inside `to_tsvector`. The caller should still guard against
    an all-stopword raw query, which `to_tsquery` would reject.

    Returns "" when no usable tokens were produced; callers should
    fall back to `plainto_tsquery(q)` on the raw input.
    """
    raw_tokens = re.findall(r"[A-Za-z0-9-]{2,40}", q.lower())
    if not raw_tokens:
        return ""

    groups: list[str] = []
    seen_tokens: set[str] = set()
    for tok in raw_tokens:
        if tok in seen_tokens:
            continue
        seen_tokens.add(tok)
        tok_lex = _ts_quote(tok)
        if not tok_lex:
            continue
        syn_lexs: list[str] = []
        for syn in expand_token(tok):
            lex = _ts_quote(syn)
            if lex and lex != tok_lex and lex not in syn_lexs:
                syn_lexs.append(lex)
        if syn_lexs:
            group = "(" + " | ".join([tok_lex, *syn_lexs]) + ")"
        else:
            group = tok_lex
        groups.append(group)

    if not groups:
        return ""
    return " & ".join(groups)


# ---------- tiny self-check ------------------------------------------
#
# Run `python -m backend.synonyms` to sanity-check the expansion for a
# few representative tokens — useful when adjusting the overlay or
# debugging a WordNet corpus issue.

def _selfcheck():  # pragma: no cover
    samples = ["vibrant", "happy", "sunset", "dog", "selfie", "running"]
    for s in samples:
        syns = list(expand_token(s))[:8]
        print(f"  {s:>10s}  ->  {syns}")
    print()
    print(expand_query_to_tsquery("vibrant sunset over the ocean"))


if __name__ == "__main__":  # pragma: no cover
    _selfcheck()
