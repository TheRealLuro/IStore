"""Query-expansion synonyms for the photo / document search domain.

CLIP already handles semantic similarity in embedding space — a query
of "vibrant" puts vivid + colorful + bright images near it in the
visual manifold, so CLIP-side ranking finds them without help. The
Postgres FTS pass that runs alongside CLIP, though, is purely lexical
— if the summary text says "colorful" and the user searched "vibrant",
FTS contributes zero to the blend even though the user obviously meant
both terms. This module expands the user's tokens with hand-curated
synonyms for the high-value visual-search vocabulary so FTS catches
those matches.

Scope is deliberately small (~80 entries). It covers categories that
come up constantly in photo libraries: colors / brightness, moods /
atmospheres, time-of-day, weather, common objects, people-grouping,
and broad scene types. Each entry is a (canonical, expansions) pair
where the expansions are the words that should ALSO match. The
expansion is one-way for clarity but applied bidirectionally by
`SYNONYMS_INDEX` below — searching "vivid" finds "vibrant" and vice
versa.

Adding a term: append to `SYNONYM_GROUPS`. Keep the canonical
short (one word) and pick expansions that a reader would think of as
substitutable in a photo caption. Avoid antonyms; avoid words with
multiple meanings unless they're all relevant (e.g. "bright" is fine
for both color and intelligence).

For Postgres FTS, expanded tokens are OR'd inside a `to_tsquery`
expression: `vibrant | colorful | bright | vivid`. The lexeme
normalization in `english` config handles plurals + tenses, so
"colorful" already covers "colorfully" etc.
"""
from __future__ import annotations

import re

# Each tuple: (canonical_token, [expansions]). The expansions are
# treated as synonyms in BOTH directions — see SYNONYMS_INDEX below.
SYNONYM_GROUPS: list[tuple[str, list[str]]] = [
    # ---- color saturation / brightness ----
    ("vibrant",   ["colorful", "vivid", "bright", "saturated", "bold"]),
    ("colorful",  ["vibrant", "vivid", "bright", "rainbow", "multicolored"]),
    ("muted",     ["dull", "faded", "pastel", "subdued", "washed"]),
    ("dark",      ["dim", "shadowy", "low-light", "moody"]),
    ("bright",    ["sunny", "lit", "luminous", "vivid"]),
    ("blurry",    ["blurred", "soft", "out-of-focus", "fuzzy"]),
    ("sharp",     ["crisp", "clear", "detailed", "in-focus"]),

    # ---- mood / atmosphere ----
    ("cozy",      ["warm", "intimate", "inviting", "homey"]),
    ("chaotic",   ["messy", "cluttered", "busy", "disorganized"]),
    ("peaceful",  ["calm", "serene", "tranquil", "quiet"]),
    ("energetic", ["dynamic", "lively", "active", "bustling"]),
    ("dramatic",  ["striking", "intense", "bold", "epic"]),
    ("minimal",   ["minimalist", "clean", "simple", "uncluttered", "bare"]),

    # ---- time of day ----
    ("sunset",    ["dusk", "evening", "golden-hour", "twilight"]),
    ("sunrise",   ["dawn", "morning", "daybreak", "first-light"]),
    ("night",     ["nighttime", "evening", "dark", "late"]),
    ("day",       ["daytime", "afternoon", "midday"]),

    # ---- weather / environment ----
    ("snow",      ["snowy", "winter", "frost", "frozen", "icy"]),
    ("snowy",     ["snow", "winter", "wintry", "frost"]),
    ("rain",      ["rainy", "wet", "drizzle", "stormy"]),
    ("rainy",     ["rain", "wet", "drizzle", "stormy"]),
    ("sunny",     ["bright", "clear", "fair", "blue-sky"]),
    ("cloudy",    ["overcast", "gray", "grey", "muted-sky"]),
    ("foggy",     ["misty", "hazy", "fog", "mist"]),
    ("storm",     ["stormy", "thunderstorm", "lightning", "tempest"]),

    # ---- people & groupings ----
    ("person",    ["individual", "human", "people", "someone"]),
    ("people",    ["persons", "individuals", "humans", "crowd", "group"]),
    ("group",     ["crowd", "gathering", "team", "party", "ensemble"]),
    ("crowd",     ["group", "audience", "throng", "many-people"]),
    ("portrait",  ["headshot", "selfie", "face", "close-up"]),
    ("selfie",    ["self-portrait", "front-camera", "me"]),
    ("family",    ["relatives", "household", "kin"]),
    ("kid",       ["kids", "child", "children", "toddler", "baby"]),
    ("child",     ["kid", "children", "youngster", "toddler"]),

    # ---- common scenes ----
    ("beach",     ["shore", "coast", "seaside", "sand", "ocean-edge"]),
    ("mountain",  ["mountains", "peak", "summit", "alpine", "hill"]),
    ("forest",    ["woods", "woodland", "trees", "jungle"]),
    ("city",      ["urban", "downtown", "metropolis", "skyline"]),
    ("street",    ["road", "alley", "avenue", "sidewalk"]),
    ("indoor",    ["inside", "interior", "indoors", "room"]),
    ("outdoor",   ["outside", "exterior", "outdoors", "nature"]),
    ("water",     ["ocean", "sea", "lake", "river", "pond"]),
    ("ocean",     ["sea", "water", "waves", "tide"]),
    ("sky",       ["clouds", "horizon", "atmosphere", "heavens"]),

    # ---- documents / screenshots ----
    ("document",  ["doc", "paper", "file", "pdf", "page"]),
    ("screenshot",["screen-capture", "screen-grab", "capture", "shot"]),
    ("receipt",   ["bill", "invoice", "tab", "purchase"]),
    ("invoice",   ["bill", "receipt", "statement"]),
    ("note",      ["notes", "memo", "annotation", "writing"]),
    ("handwriting", ["handwritten", "written", "scrawl", "notes"]),
    ("whiteboard",["board", "marker-board", "diagram", "sketch"]),
    ("diagram",   ["chart", "schematic", "figure", "illustration"]),
    ("chart",     ["graph", "diagram", "plot", "figure"]),

    # ---- common objects ----
    ("car",       ["vehicle", "automobile", "auto", "sedan"]),
    ("dog",       ["puppy", "pup", "canine"]),
    ("cat",       ["kitten", "kitty", "feline"]),
    ("food",      ["meal", "dish", "cuisine", "plate"]),
    ("plant",     ["flower", "leaves", "greenery", "foliage"]),
    ("tree",      ["trees", "branches", "trunk"]),
    ("building",  ["architecture", "structure", "house", "facade"]),
    ("house",     ["home", "residence", "building", "dwelling"]),
    ("phone",     ["mobile", "cell", "smartphone", "handset"]),
    ("computer",  ["laptop", "desktop", "pc", "machine"]),

    # ---- pet/photo-style adjectives ----
    ("cute",      ["adorable", "sweet", "lovely", "charming"]),
    ("funny",     ["humorous", "amusing", "silly", "comic"]),
    ("old",       ["vintage", "antique", "retro", "historic"]),
    ("new",       ["modern", "fresh", "recent", "contemporary"]),
    ("small",     ["tiny", "little", "miniature", "compact"]),
    ("big",       ["large", "huge", "giant", "massive"]),
]


def _normalize(token: str) -> str:
    return token.lower().strip()


# Bidirectional index: every word that appears in any group maps to
# the deduplicated full set for that group (canonical + expansions),
# minus the word itself. So `vibrant → {colorful, vivid, bright, ...}`
# and `colorful → {vibrant, vivid, bright, ...}`.
SYNONYMS_INDEX: dict[str, set[str]] = {}
for _canonical, _expansions in SYNONYM_GROUPS:
    _all = {_normalize(_canonical), *(_normalize(x) for x in _expansions)}
    for _word in _all:
        bucket = SYNONYMS_INDEX.setdefault(_word, set())
        bucket.update(_all - {_word})


_SAFE_LEX = re.compile(r"^[a-z0-9-]{2,40}$")


def _ts_quote(token: str) -> str | None:
    """Sanitize a token for inclusion in a Postgres `to_tsquery` literal.

    `to_tsquery` accepts lexeme strings + operators (`&`, `|`, `!`, `<->`).
    Anything else (quotes, parens, colons) is unsafe to inline. We only
    keep tokens that match `[a-z0-9-]{2,40}` — the vocabulary the
    Postgres `english` config tokenizes for our haystacks. Returns
    None for tokens that don't pass the gate so the caller can skip them.

    Hyphens in lexemes (e.g. `golden-hour`) are split by `english`
    into two words; we replace them with a phrase operator so the
    expanded clause still matches as a unit when present, and as
    either word otherwise. For simplicity here we use a `&` join
    (both words must appear) — that's a small precision hit vs. the
    phrase operator `<->` but avoids the order constraint.
    """
    token = _normalize(token)
    if not _SAFE_LEX.match(token):
        return None
    if "-" in token:
        parts = [p for p in token.split("-") if p]
        return "(" + " & ".join(parts) + ")"
    return token


def expand_query_to_tsquery(q: str) -> str:
    """Build a `to_tsquery`-friendly expanded query string from the
    user's raw input.

    Strategy:
      - Tokenize the raw query into [a-z0-9-]+ words.
      - For each token, look up synonyms in SYNONYMS_INDEX.
      - For each token, build `(token | syn1 | syn2 | ...)`.
      - AND the per-token groups together.

    Example: `vibrant sunset` becomes:
      `(vibrant | colorful | vivid | bright | saturated | bold) &
       (sunset | dusk | evening | (golden & hour) | twilight)`

    Stopwords are NOT dropped — Postgres' `english` config does that
    in `to_tsvector`, and `to_tsquery` raises a `text-search query
    contains only stop words` error when EVERY lexeme is a stopword.
    The caller should guard against an all-stopword query.

    Returns an empty string when no usable tokens were found — the
    caller should fall back to plainto_tsquery on the raw input.
    """
    raw_tokens = re.findall(r"[A-Za-z0-9-]{2,40}", q.lower())
    if not raw_tokens:
        return ""

    groups: list[str] = []
    for tok in raw_tokens:
        tok_lex = _ts_quote(tok)
        if not tok_lex:
            continue
        syns = SYNONYMS_INDEX.get(tok, set())
        # Cap synonyms per token so a runaway dictionary entry can't
        # blow up the tsquery into something that takes 200ms to plan.
        # 8 is generous — the largest current group has 5 entries.
        syn_lexs = []
        for s in sorted(syns):
            lex = _ts_quote(s)
            if lex and lex != tok_lex:
                syn_lexs.append(lex)
            if len(syn_lexs) >= 8:
                break
        if syn_lexs:
            group = "(" + " | ".join([tok_lex, *syn_lexs]) + ")"
        else:
            group = tok_lex
        groups.append(group)

    if not groups:
        return ""
    return " & ".join(groups)
