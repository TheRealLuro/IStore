"""Apache-2.0 translation engine (MADLAD-400 + Opus-MT) — the commercial-safe
replacement for the CC-BY-NC NLLB-200 translator.

WHY THIS EXISTS
---------------
The previous translator, `facebook/nllb-200-distilled-600M`, is licensed
**CC-BY-NC** (non-commercial) — a hard blocker for a commercial launch. This
module routes every translation through Apache-2.0 models instead, with NO
quality regression for the common languages and *wider* coverage of the long
tail:

  * PRIMARY — `google/madlad400-3b-mt` (Apache-2.0, 400+ languages). A T5
    seq2seq model. Translation is requested by PREFIXING the input text with a
    target-language token, e.g. ``"<2es> Hello world"`` → Spanish,
    ``"<2fr> ..."`` → French. The token is part of the *input* (it is NOT a
    forced-BOS id like NLLB used), which is exactly why this drops in cleanly
    on transformers <4.50 where the Florence forced-BOS quirk lives. Loaded in
    **8-bit** via bitsandbytes (~3 GB) so it fits the single 8 GB RTX 4060
    alongside the resident Florence-2 + CLIP; if 8-bit OOMs it can be switched
    to 4-bit nf4 (~1.65 GB) by flipping ``_MADLAD_LOAD_4BIT`` / the
    ``TRANSLATE_MADLAD_4BIT`` env var.

  * EXOTIC GAP-FILLER — `Helsinki-NLP/opus-mt-en-mul` (EN→many) and
    `Helsinki-NLP/opus-mt-mul-en` (many→EN), both Apache-2.0, ~0.3 GB each.
    Used for languages MADLAD lacks. opus-mt-en-mul wants a sentence-initial
    target token: ``">>ton<< Hello"``.

  * LLM TIER (low-resource) — for the handful of Pacific / rare languages
    MADLAD lacks AND Opus-MT mangles (most notably **Tongan**, which Opus
    returned as date-like noise), we route through the RESIDENT
    Qwen2.5-Instruct (the same model the summary/rewrite pipeline keeps hot —
    `backend.vision.runtime.get_summary_rewriter`) with a strict
    "Translate this English text to <Lang>. Reply with ONLY the translation:"
    prompt. ZERO extra VRAM (reuses the resident weights). Falls back to the
    Opus route if the LLM can't load, so nothing breaks. See `_LLM_ROUTE_NAMES`
    / `_llm_translate_text` / `_llm_translate_batch`.

PUBLIC SURFACE
--------------
  * ``get_translator()`` — lazily loads MADLAD (8-bit) + both Opus-MT models,
    caches them in module globals (thread-safe via a lock). Raises
    RuntimeError when the engine genuinely can't be brought up (no torch /
    transformers / weights) so callers can fall back to NLLB.
  * ``translate_text(text, target, source=None) -> str`` — the one call every
    translate path delegates to. Resolves the target to either a MADLAD
    ``<2xx>`` token or an Opus ``>>code<<`` route, chunks long input, decodes
    greedily (num_beams=1, no_repeat_ngram_size=3), and returns the joined
    translation. Best-effort: on any per-chunk failure it keeps going so the
    caller never gets an empty string for a partially-translatable input.

IMPORT-SAFETY
-------------
Mirrors ocr.py: **nothing heavy runs at module import**. `torch`,
`transformers`, and `bitsandbytes` are imported lazily INSIDE the functions, so
importing this module can never pull CUDA/torch at API boot (a broken import
there would crash `uvicorn --reload` and take the whole API down). The model
load happens on the first ``get_translator()`` / ``translate_text()`` call,
inside whatever threadpool the caller already uses.
"""
from __future__ import annotations

import logging
import os
import re
import threading

logger = logging.getLogger(__name__)


# ===========================================================================
# Configuration
# ===========================================================================
def _madlad_model_name() -> str:
    try:
        from backend.config import settings
        return getattr(
            settings, "translate_madlad_model_name", "google/madlad400-3b-mt"
        )
    except Exception:
        return "google/madlad400-3b-mt"


def _opus_en_mul_name() -> str:
    try:
        from backend.config import settings
        return getattr(
            settings, "translate_opus_en_mul_name", "Helsinki-NLP/opus-mt-en-mul"
        )
    except Exception:
        return "Helsinki-NLP/opus-mt-en-mul"


def _opus_mul_en_name() -> str:
    try:
        from backend.config import settings
        return getattr(
            settings, "translate_opus_mul_en_name", "Helsinki-NLP/opus-mt-mul-en"
        )
    except Exception:
        return "Helsinki-NLP/opus-mt-mul-en"


def _load_4bit() -> bool:
    """8-bit by default (~3 GB, best quality on 8 GB alongside Florence+CLIP).
    Flip to 4-bit nf4 (~1.65 GB) via TRANSLATE_MADLAD_4BIT=1 or the
    `translate_madlad_4bit` setting when 8-bit OOMs with Florence resident."""
    env = os.environ.get("TRANSLATE_MADLAD_4BIT")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on"}
    try:
        from backend.config import settings
        return bool(getattr(settings, "translate_madlad_4bit", False))
    except Exception:
        return False


# Chunking budget for one generate() call. MADLAD/T5 handle ~1000-1500 input
# chars comfortably; keep chunks well under the model's token window and prefer
# clean sentence/paragraph boundaries so a chunk never cuts mid-word.
_CHUNK_CHARS = 1_200
# Hard cap on input length per translate_text call (defence-in-depth; the
# callers already cap their own request sizes). 200k matches the API schemas.
_MAX_INPUT_CHARS = 200_000


# ===========================================================================
# Language-code mapping.
#
# Callers pass `target` as either a FLORES-200 code (spa_Latn, zho_Hans, …),
# an ISO-639-1 code (es, zh, …), or already a MADLAD token. We normalize to:
#   * a MADLAD `<2xx>` token for the PRIMARY path, OR
#   * an Opus `>>code<<` route for the languages MADLAD lacks.
#
# MADLAD's target tokens are BCP-47-ish (mostly the ISO-639-1 two-letter code,
# a few three-letter / script-suffixed). We map from BOTH the FLORES code and
# the bare ISO code so every existing caller keeps working unchanged.
# ===========================================================================

# FLORES-200 code → MADLAD language code (the bit inside `<2 … >`). Only the
# languages whose FLORES code differs from a trivial prefix, or that we want to
# pin explicitly, need listing — but we list the common set in full so the
# mapping is auditable and never guesses for a major language.
_FLORES_TO_MADLAD: dict[str, str] = {
    # Western / Central Europe
    "eng_Latn": "en", "spa_Latn": "es", "fra_Latn": "fr", "deu_Latn": "de",
    "ita_Latn": "it", "por_Latn": "pt", "nld_Latn": "nl", "cat_Latn": "ca",
    "glg_Latn": "gl", "eus_Latn": "eu", "gle_Latn": "ga", "cym_Latn": "cy",
    "isl_Latn": "is", "ltz_Latn": "lb", "mlt_Latn": "mt",
    # Nordic
    "swe_Latn": "sv", "dan_Latn": "da", "nob_Latn": "no", "nno_Latn": "nn",
    "fin_Latn": "fi",
    # Baltic / Slavic
    "pol_Latn": "pl", "ces_Latn": "cs", "slk_Latn": "sk", "slv_Latn": "sl",
    "hrv_Latn": "hr", "bos_Latn": "bs", "srp_Cyrl": "sr", "mkd_Cyrl": "mk",
    "bul_Cyrl": "bg", "rus_Cyrl": "ru", "ukr_Cyrl": "uk", "bel_Cyrl": "be",
    "lit_Latn": "lt", "lvs_Latn": "lv", "est_Latn": "et",
    # Greek / Romance tail / misc Europe
    "ell_Grek": "el", "ron_Latn": "ro", "hun_Latn": "hu", "als_Latn": "sq",
    # Middle East / Semitic / Iranian
    "arb_Arab": "ar", "heb_Hebr": "he", "pes_Arab": "fa", "urd_Arab": "ur",
    "pbt_Arab": "ps", "kmr_Latn": "ku", "ckb_Arab": "ckb", "azj_Latn": "az",
    "hye_Armn": "hy", "kat_Geor": "ka",
    # Turkic / Central Asia
    "tur_Latn": "tr", "kaz_Cyrl": "kk", "uzn_Latn": "uz", "kir_Cyrl": "ky",
    "tuk_Latn": "tk", "tat_Cyrl": "tt", "bak_Cyrl": "ba", "khk_Cyrl": "mn",
    # South Asia (Indic)
    "hin_Deva": "hi", "ben_Beng": "bn", "pan_Guru": "pa", "guj_Gujr": "gu",
    "ory_Orya": "or", "tam_Taml": "ta", "tel_Telu": "te", "kan_Knda": "kn",
    "mal_Mlym": "ml", "mar_Deva": "mr", "npi_Deva": "ne", "sin_Sinh": "si",
    "snd_Arab": "sd", "asm_Beng": "as", "san_Deva": "sa", "mai_Deva": "mai",
    # East / Southeast Asia
    "zho_Hans": "zh", "zho_Hant": "zh_Hant", "yue_Hant": "yue", "jpn_Jpan": "ja",
    "kor_Hang": "ko", "tha_Thai": "th", "lao_Laoo": "lo", "mya_Mymr": "my",
    "khm_Khmr": "km", "vie_Latn": "vi", "ind_Latn": "id", "zsm_Latn": "ms",
    "tgl_Latn": "tl", "jav_Latn": "jv", "sun_Latn": "su", "bod_Tibt": "bo",
    # Africa
    "swh_Latn": "sw", "amh_Ethi": "am", "hau_Latn": "ha", "yor_Latn": "yo",
    "ibo_Latn": "ig", "zul_Latn": "zu", "xho_Latn": "xh", "afr_Latn": "af",
    "som_Latn": "so", "kin_Latn": "rw", "nya_Latn": "ny", "sna_Latn": "sn",
    "sot_Latn": "st", "tsn_Latn": "tn", "lug_Latn": "lg", "wol_Latn": "wo",
    "fuv_Latn": "ff", "tir_Ethi": "ti", "gaz_Latn": "om", "lin_Latn": "ln",
    # Other
    "epo_Latn": "eo", "lat_Latn": "la", "hat_Latn": "ht",
}

# Bare ISO-639-1 → MADLAD code. Identity for most; a few normalize (no→no,
# zh→zh). Anything not listed and not a FLORES code falls back to "en".
_ISO_TO_MADLAD: dict[str, str] = {
    "en": "en", "es": "es", "fr": "fr", "de": "de", "it": "it", "pt": "pt",
    "nl": "nl", "ca": "ca", "gl": "gl", "eu": "eu", "ga": "ga", "cy": "cy",
    "is": "is", "lb": "lb", "mt": "mt", "sv": "sv", "da": "da", "nb": "no",
    "no": "no", "nn": "nn", "fi": "fi", "pl": "pl", "cs": "cs", "sk": "sk",
    "sl": "sl", "hr": "hr", "bs": "bs", "sr": "sr", "mk": "mk", "bg": "bg",
    "ru": "ru", "uk": "uk", "be": "be", "lt": "lt", "lv": "lv", "et": "et",
    "el": "el", "ro": "ro", "hu": "hu", "sq": "sq", "ar": "ar", "he": "he",
    "iw": "he", "fa": "fa", "ur": "ur", "ps": "ps", "ku": "ku", "ckb": "ckb",
    "az": "az", "hy": "hy", "ka": "ka", "tr": "tr", "kk": "kk", "uz": "uz",
    "ky": "ky", "tk": "tk", "tt": "tt", "ba": "ba", "mn": "mn", "hi": "hi",
    "bn": "bn", "pa": "pa", "gu": "gu", "or": "or", "ta": "ta", "te": "te",
    "kn": "kn", "ml": "ml", "mr": "mr", "ne": "ne", "si": "si", "sd": "sd",
    "as": "as", "sa": "sa", "mai": "mai", "zh": "zh", "zh-cn": "zh",
    "zh-tw": "zh_Hant", "yue": "yue", "ja": "ja", "ko": "ko", "th": "th",
    "lo": "lo", "my": "my", "km": "km", "vi": "vi", "id": "id", "ms": "ms",
    "tl": "tl", "fil": "tl", "jv": "jv", "su": "su", "bo": "bo", "sw": "sw",
    "am": "am", "ha": "ha", "yo": "yo", "ig": "ig", "zu": "zu", "xh": "xh",
    "af": "af", "so": "so", "rw": "rw", "ny": "ny", "sn": "sn", "st": "st",
    "tn": "tn", "lg": "lg", "wo": "wo", "ff": "ff", "ti": "ti", "om": "om",
    "ln": "ln", "eo": "eo", "la": "la", "ht": "ht",
}

# ===========================================================================
# MADLAD-400 FULL language coverage (the long tail).
#
# MADLAD-400 (`google/madlad400-3b-mt`) is trained on 400+ languages, each
# addressed by a `<2xx>` target token that lives in its SentencePiece vocab.
# The block above pins the common set with friendly ISO aliases; this table
# extends coverage to MADLAD's COMPLETE token list (extracted verbatim from the
# model's `spiece.model`, then filtered to drop the non-language control tokens
# `<2translate>`, `<2transliterate>`, `<2back_translated>`, `<2en_xx_simple>`,
# `<2zxx_xx_dtynoise>`, the script-only `<2Latn>`/`<2Arab>`/… and region-only
# `<2CA>`/`<2IR>`/… tokens). So `GET /translate/languages` can offer EVERY
# language MADLAD actually supports, including the exotic / low-resource ones.
#
# Entries are IDENTITY mappings (`"code": "code"`) — the MADLAD token IS the
# lookup code — so each is BOTH a candidate value read by
# `translate_langs.build_languages` AND a resolvable key, i.e. `resolve_target`
# returns a CONCRETE `<2code>` token for it (never the silent-English
# fallback). Codes carrying a script/region suffix (e.g. `bn_Latn` romanized
# Bengali, `ks_Deva` Devanagari Kashmiri, `ms_Arab_BN` Brunei Jawi Malay) are
# DISTINCT tokens from their default form and are listed verbatim so the picker
# can target the exact script. Adding a code here is a pure static-table change:
# NO model reload — MADLAD already knows the token.
#
# Generated from the model card's language table (= the spiece vocab). Merged
# into `_ISO_TO_MADLAD` at import (a few keys already present above are simply
# overwritten with the identical identity value — no behaviour change).
# ---------------------------------------------------------------------------
_MADLAD_FULL: dict[str, str] = {
    "abt": "abt", "ace": "ace", "ace_Arab": "ace_Arab", "acf": "acf",
    "ada": "ada", "adh": "adh", "ady": "ady", "agr": "agr", "ahk": "ahk",
    "ak": "ak", "akb": "akb", "alt": "alt", "alz": "alz", "amu": "amu",
    "an": "an", "ang": "ang", "ann": "ann", "ape": "ape", "arn": "arn",
    "ary": "ary", "arz": "arz", "av": "av", "awa": "awa", "ay": "ay",
    "az_RU": "az_RU", "ban": "ban", "bar": "bar", "bas": "bas", "bbc": "bbc",
    "bci": "bci", "ber": "ber", "ber_Latn": "ber_Latn", "bew": "bew",
    "bg_Latn": "bg_Latn", "bgp": "bgp", "bho": "bho", "bi": "bi",
    "bik": "bik", "bim": "bim", "bjn": "bjn", "bjn_Arab": "bjn_Arab",
    "bm": "bm", "bn_Latn": "bn_Latn", "bqc": "bqc", "br": "br", "bru": "bru",
    "brx": "brx", "bts": "bts", "btx": "btx", "bua": "bua", "bug": "bug",
    "bum": "bum", "bus": "bus", "bzj": "bzj", "cab": "cab", "cac": "cac",
    "cak": "cak", "cbk": "cbk", "cce": "cce", "ce": "ce", "ceb": "ceb",
    "cfm": "cfm", "ch": "ch", "chk": "chk", "chm": "chm", "chr": "chr",
    "cnh": "cnh", "co": "co", "cr_Latn": "cr_Latn", "crh": "crh",
    "crh_Latn": "crh_Latn", "crs": "crs", "ctd_Latn": "ctd_Latn",
    "ctu": "ctu", "cuk": "cuk", "cv": "cv", "din": "din", "dje": "dje",
    "djk": "djk", "dln": "dln", "doi": "doi", "dov": "dov", "dtp": "dtp",
    "dv": "dv", "dwr": "dwr", "dyu": "dyu", "dz": "dz", "ee": "ee",
    "el_Latn": "el_Latn", "emp": "emp", "enq": "enq", "ff": "ff",
    "ffm": "ffm", "fil": "fil", "fip": "fip", "fj": "fj", "fo": "fo",
    "fon": "fon", "fr_CA": "fr_CA", "frp": "frp", "fur": "fur", "fuv": "fuv",
    "fy": "fy", "gag": "gag", "gbm": "gbm", "gd": "gd", "gn": "gn",
    "gof": "gof", "gom": "gom", "gom_Latn": "gom_Latn", "gor": "gor",
    "grc": "grc", "gsw": "gsw", "gu_Latn": "gu_Latn", "gub": "gub",
    "guc": "guc", "guh": "guh", "gui": "gui", "gv": "gv", "gvl": "gvl",
    "gym": "gym", "haw": "haw", "hi_Latn": "hi_Latn", "hif": "hif",
    "hil": "hil", "hmn": "hmn", "hne": "hne", "ho": "ho", "hui": "hui",
    "hus": "hus", "hvn": "hvn", "iba": "iba", "ibb": "ibb", "ify": "ify",
    "ilo": "ilo", "inb": "inb", "io": "io", "iso": "iso", "iu": "iu",
    "ium": "ium", "izz": "izz", "jac": "jac", "jam": "jam", "jiv": "jiv",
    "jvn": "jvn", "kaa": "kaa", "kaa_Latn": "kaa_Latn", "kac": "kac",
    "kbd": "kbd", "kbp": "kbp", "kek": "kek", "kg": "kg", "kha": "kha",
    "kj": "kj", "kjg": "kjg", "kjh": "kjh", "kl": "kl", "kmb": "kmb",
    "kmz_Latn": "kmz_Latn", "kn_Latn": "kn_Latn", "knj": "knj", "koi": "koi",
    "kos": "kos", "kr": "kr", "kr_Arab": "kr_Arab", "krc": "krc",
    "kri": "kri", "ks": "ks", "ks_Deva": "ks_Deva", "ksd": "ksd",
    "ksw": "ksw", "ktu": "ktu", "kum": "kum", "kv": "kv", "kw": "kw",
    "kwi": "kwi", "laj": "laj", "lhu": "lhu", "li": "li", "lij": "lij",
    "lmo": "lmo", "lrc": "lrc", "ltg": "ltg", "lu": "lu", "lus": "lus",
    "mad": "mad", "mag": "mag", "mak": "mak", "mam": "mam", "mas": "mas",
    "mass": "mass", "maz": "maz", "mbt": "mbt", "mdf": "mdf", "meo": "meo",
    "meu": "meu", "mfe": "mfe", "mg": "mg", "mgh": "mgh", "mh": "mh",
    "mi": "mi", "min": "min", "miq": "miq", "mkn": "mkn", "ml_Latn": "ml_Latn",
    "mni": "mni", "mps": "mps", "mqy": "mqy", "mrj": "mrj", "mrw": "mrw",
    "ms_Arab": "ms_Arab", "ms_Arab_BN": "ms_Arab_BN", "msb": "msb",
    "msi": "msi", "msm": "msm", "mwl": "mwl", "myv": "myv",
    "nan_Latn_TW": "nan_Latn_TW", "ndc_ZW": "ndc_ZW", "nds": "nds",
    "nds_NL": "nds_NL", "new": "new", "ngu": "ngu", "nhe": "nhe",
    "nia": "nia", "nij": "nij", "niq": "niq", "nnb": "nnb", "noa": "noa",
    "nog": "nog", "nr": "nr", "nso": "nso", "nus": "nus", "nut": "nut",
    "nv": "nv", "nyu": "nyu", "nzi": "nzi", "oc": "oc", "oj": "oj",
    "os": "os", "otq": "otq", "pag": "pag", "pap": "pap", "pau": "pau",
    "pck": "pck", "pis": "pis", "pon": "pon", "ppk": "ppk", "prs": "prs",
    "qu": "qu", "qub": "qub", "quc": "quc", "quf": "quf", "quh": "quh",
    "qup": "qup", "quy": "quy", "qvc": "qvc", "qvi": "qvi", "qvz": "qvz",
    "qxr": "qxr", "raj": "raj", "rcf": "rcf", "rki": "rki", "rm": "rm",
    "rmc": "rmc", "rn": "rn", "rom": "rom", "ru_Latn": "ru_Latn",
    "rwo": "rwo", "sah": "sah", "sat_Latn": "sat_Latn", "sc": "sc",
    "scn": "scn", "sda": "sda", "se": "se", "seh": "seh", "sg": "sg",
    "sh": "sh", "shn": "shn", "shp": "shp", "sja": "sja", "skr": "skr",
    "sm": "sm", "smt": "smt", "spp": "spp", "srm": "srm", "srn": "srn",
    "ss": "ss", "stq": "stq", "sus": "sus", "suz": "suz", "sxn": "sxn",
    "syr": "syr", "szl": "szl", "ta_Latn": "ta_Latn", "tab": "tab",
    "taj": "taj", "taq": "taq", "taq_Tfng": "taq_Tfng", "tbz": "tbz",
    "tca": "tca", "tcy": "tcy", "tdx": "tdx", "te_Latn": "te_Latn",
    "teo": "teo", "tet": "tet", "tg": "tg", "tiv": "tiv", "tks": "tks",
    "tlh": "tlh", "tll": "tll", "tly_IR": "tly_IR", "toj": "toj",
    "trp": "trp", "ts": "ts", "tsc": "tsc", "tsg": "tsg", "tuc": "tuc",
    "tvl": "tvl", "twu": "twu", "tyv": "tyv", "tyz": "tyz", "tzh": "tzh",
    "tzj": "tzj", "tzm": "tzm", "tzo": "tzo", "ubu": "ubu", "udm": "udm",
    "ug": "ug", "ve": "ve", "vec": "vec", "wa": "wa", "wal": "wal",
    "war": "war", "wuu": "wuu", "xal": "xal", "yap": "yap", "yi": "yi",
    "yua": "yua", "zap": "zap", "zh_Latn": "zh_Latn", "zne": "zne",
    "zza": "zza",
}

# Merge MADLAD's full set into the primary lookup. Existing keys keep their
# (identical) value; new codes become identity-resolvable. One pass, at import,
# no model load.
_ISO_TO_MADLAD.update(_MADLAD_FULL)

# Case-folded index of _ISO_TO_MADLAD: lower-cased key → ORIGINAL-cased MADLAD
# value. Built ONCE at import. Needed because many MADLAD codes carry a
# CamelCase script / UPPER region suffix that IS part of the token
# (`bn_Latn`, `ms_Arab_BN`, `nan_Latn_TW`, `az_RU`, `ndc_ZW`, …): a caller may
# send any case (`bn_latn`, `BN_LATN`), and `resolve_target` normalizes to
# lower-case before lookup, so a plain `low in _ISO_TO_MADLAD` would MISS the
# mixed-case key and wrongly fall through to the base code (`<2bn>`) — or, for
# codes whose base isn't mapped, all the way to silent English (`<2en>`). This
# index lets the resolver recover the EXACT script/region token regardless of
# input case. When two keys fold to the same lower-case form the first wins;
# the table has no such genuine clashes (script suffixes keep them distinct).
_ISO_TO_MADLAD_CI: dict[str, str] = {}
for _k, _v in _ISO_TO_MADLAD.items():
    _ISO_TO_MADLAD_CI.setdefault(_k.lower(), _v)

# Languages MADLAD does NOT cover that we route to Opus-MT instead. The KEY is
# the normalized lookup code (FLORES or ISO, lower-cased); the VALUE is the
# Opus `>>code<<` token (ISO-639-2/3). Tongan is the headline gap the task
# calls out. Add more here as MADLAD gaps are found — each addition is a pure
# routing change, no model reload.
#
# Opus-MT en-mul / mul-en use 3-letter ISO codes in their `>>xxx<<` token set.
_OPUS_ROUTE: dict[str, str] = {
    # Tongan — MADLAD lacks it; opus-mt-en-mul covers it as ">>ton<<".
    "ton": "ton", "to": "ton", "ton_latn": "ton",
    # Fijian — another Pacific gap opus-mt-mul covers.
    "fij": "fij", "fj": "fij", "fij_latn": "fij",
    # Samoan.
    "smo": "smo", "sm": "smo", "smo_latn": "smo",
    # Tahitian.
    "tah": "tah", "ty": "tah",
}

# Human-readable names for the Opus-routed codes (so callers that want a
# display name still get one; the NLLB path owns FLORES display names).
_OPUS_NAMES: dict[str, str] = {
    "ton": "Tongan", "fij": "Fijian", "smo": "Samoan", "tah": "Tahitian",
}

# ===========================================================================
# LLM TIER for LOW-RESOURCE languages (Tongan, …).
#
# MADLAD lacks these and Opus-MT en-mul produces garbage for them (the user
# reported Tongan coming back as "22 May 30 1999"-style noise). For this small
# set we instead route the translation through the RESIDENT instruction LLM
# — Qwen2.5-1.5B-Instruct, already loaded on the GPU for the summary/rewrite
# pipeline (`backend.vision.runtime.get_summary_rewriter`) — with a strict
# "reply with ONLY the translation" prompt. Reusing the resident model adds
# ZERO extra VRAM (it's the same weights the summarizer keeps hot).
#
# The KEY is the normalized lookup code (FLORES or ISO, lower-cased); the VALUE
# is the ENGLISH LANGUAGE NAME we put into the prompt ("Translate this English
# text to <Name>."). These are a SUBSET of `_OPUS_ROUTE` above: when the LLM is
# available they pre-empt the Opus route; when it isn't, the existing Opus path
# still runs (so nothing breaks). Add a code here to move it onto the LLM tier.
_LLM_ROUTE_NAMES: dict[str, str] = {
    "ton": "Tongan", "to": "Tongan", "ton_latn": "Tongan",
    "fij": "Fijian", "fj": "Fijian", "fij_latn": "Fijian",
    "smo": "Samoan", "sm": "Samoan", "smo_latn": "Samoan",
    "tah": "Tahitian", "ty": "Tahitian",
}


def _llm_target_name(target: str) -> str | None:
    """Return the English language NAME for the LLM prompt when `target` is on
    the low-resource LLM tier (Tongan, …), else None. Accepts a FLORES code, an
    ISO code, or a bare subtag — matched case-insensitively, base-subtag too."""
    low = _norm_lookup(target)
    if low in _LLM_ROUTE_NAMES:
        return _LLM_ROUTE_NAMES[low]
    base = low.split("_", 1)[0]
    return _LLM_ROUTE_NAMES.get(base)


def _norm_lookup(code: str) -> str:
    """Lower-case, trim, normalize separators for table lookups. Keeps the
    script suffix (e.g. 'zho_hant') so we can distinguish Traditional Chinese,
    but the FLORES tables are keyed with the canonical CamelCase script, so we
    look up against the ORIGINAL too in the resolver."""
    return (code or "").strip().lower().replace("-", "_")


def _source_is_exotic(source: str | None) -> bool:
    """True when `source` is one of the exotic Opus gap-languages (Tongan…),
    so an x→English translation should route through opus-mt-mul-en rather than
    MADLAD (which lacks the source language)."""
    if not source:
        return False
    low = _norm_lookup(source)
    return low in _OPUS_ROUTE or low.split("_", 1)[0] in _OPUS_ROUTE


def resolve_target(target: str) -> tuple[str, str]:
    """Resolve a caller's `target` to a (engine, code) routing decision.

    Returns one of:
      * ("madlad", "<2xx>")     — translate with MADLAD, token already built;
      * ("opus",   ">>xxx<<")   — translate with Opus-MT en-mul, token built.

    Accepts a FLORES-200 code (spa_Latn), an ISO-639-1 code (es), an explicit
    MADLAD token ("<2es>"), or an Opus token (">>ton<<"). Unknown/empty → English
    via MADLAD ("<2en>"). Opus gap-languages (Tongan, …) route to Opus."""
    raw = (target or "").strip()
    if not raw:
        return "madlad", "<2en>"

    # Already a fully-formed token — trust it.
    if raw.startswith("<2") and raw.endswith(">"):
        return "madlad", raw
    if raw.startswith(">>") and raw.endswith("<<"):
        return "opus", raw

    low = _norm_lookup(raw)

    # 1) Opus gap-languages first (Tongan etc.) — checked before MADLAD so a
    #    language MADLAD lacks is never silently mistranslated.
    if low in _OPUS_ROUTE:
        return "opus", f">>{_OPUS_ROUTE[low]}<<"

    # 2) FLORES code → MADLAD. Look up against the ORIGINAL (CamelCase script)
    #    and a normalized variant so 'spa_Latn' and 'spa_latn' both resolve.
    if raw in _FLORES_TO_MADLAD:
        return "madlad", f"<2{_FLORES_TO_MADLAD[raw]}>"
    for k, v in _FLORES_TO_MADLAD.items():
        if k.lower() == low:
            return "madlad", f"<2{v}>"

    # 3) ISO-639-1 / script- / region-suffixed → MADLAD. Match the FULL code
    #    case-insensitively FIRST (via the folded index), so a script/region
    #    token like 'bn_Latn', 'ms_Arab_BN', 'nan_Latn_TW' or 'az_RU' resolves
    #    to its DISTINCT token (`<2bn_Latn>`, …) instead of collapsing to the
    #    base ('<2bn>') or, worse, to silent English. The exact-case key is a
    #    subset of the folded index, so the single CI lookup covers both.
    if low in _ISO_TO_MADLAD_CI:
        return "madlad", f"<2{_ISO_TO_MADLAD_CI[low]}>"
    # Only AFTER the full code fails do we fall back to the base subtag, so a
    # known-language-with-unknown-script still translates (in the base script).
    base = low.split("_", 1)[0]
    if base in _OPUS_ROUTE:
        return "opus", f">>{_OPUS_ROUTE[base]}<<"
    if base in _ISO_TO_MADLAD_CI:
        return "madlad", f"<2{_ISO_TO_MADLAD_CI[base]}>"

    # 4) Unknown — default to English (safest for Latin text), MADLAD path.
    logger.info("translate_engine: unknown target %r → defaulting to English", target)
    return "madlad", "<2en>"


# ===========================================================================
# Lazy model loading — cached in module globals behind a lock. Import-safe:
# nothing here runs at import; the first call triggers the download/load.
# ===========================================================================
_CACHE: dict | None = None
_UNAVAILABLE = False  # latched True once we know the engine can't load.
_LOCK = threading.Lock()

# Serializes ALL tokenizer+generate work. The HuggingFace FAST tokenizers are
# NOT re-entrant: `padding`/`truncation` flip shared Rust-side state via
# `enable_truncation`/`enable_padding`, so two threads tokenizing on the SAME
# tokenizer instance race and raise `RuntimeError: Already borrowed`. FastAPI
# runs our sync translate endpoints in a threadpool, so two overlapping
# translations (e.g. a document translation and a loader-phrases call, or two
# users) would collide. The actual GPU generate() already serializes on the
# single card, so guarding tokenize+generate with one lock costs ~nothing and
# makes every translate path concurrency-safe. Held only for the (fast) encode
# + generate + decode of one chunk/sub-batch, never across a whole request.
_GEN_LOCK = threading.Lock()


def _build_madlad():
    """Load MADLAD-400 (8-bit, or 4-bit when configured). Returns
    (model, tokenizer). Raises on failure (caller decides fallback)."""
    import torch
    from transformers import BitsAndBytesConfig
    from transformers.models.auto.modeling_auto import AutoModelForSeq2SeqLM
    from transformers.models.auto.tokenization_auto import AutoTokenizer

    from backend.vision.runtime import _report_loaded, get_device

    device = get_device()
    name = _madlad_model_name()
    tokenizer = AutoTokenizer.from_pretrained(name)

    if device == "cuda":
        # Quantize so the 3B model fits the GPU alongside Florence+CLIP.
        from backend.vision import vram_manager as _vram
        _need = 1.8 if _load_4bit() else 3.5
        _vram.register("translator_madlad", est_gb=_need, evictable=False)
        _vram.ensure_room(_need)
        if _load_4bit():
            quant = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )
            label = "madlad400-3b (4bit-nf4)"
        else:
            quant = BitsAndBytesConfig(load_in_8bit=True)
            label = "madlad400-3b (8bit)"
        # device_map={"": 0} pins the quantized weights to GPU 0. bitsandbytes
        # materializes its own int8/nf4 buffers, so we must NOT pass
        # low_cpu_mem_usage=False here (that fights the quantizer) and must NOT
        # call .to(device) after (bnb models reject a post-hoc move).
        model = AutoModelForSeq2SeqLM.from_pretrained(
            name,
            quantization_config=quant,
            device_map={"": 0},
            torch_dtype=torch.float16,
        )
    else:
        # CPU fallback (no GPU): full-precision, slow but correct. Materialize
        # fully so there's no meta tensor left behind.
        model = AutoModelForSeq2SeqLM.from_pretrained(
            name, torch_dtype=torch.float32, low_cpu_mem_usage=False
        )
        model = model.to("cpu")
        label = "madlad400-3b (cpu-fp32)"

    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    try:
        _report_loaded(label, device)
    except Exception:
        pass
    logger.info("translate_engine: loaded %s on %s", label, device)
    try:
        from backend.vision import vram_manager as _vram
        if device == "cuda":
            _vram.mark_resident("translator_madlad"); _vram.touch("translator_madlad")
    except Exception:
        pass
    return model, tokenizer


def _build_opus(name: str, device: str):
    """Load one Opus-MT model (en-mul or mul-en). Small (~0.3 GB) so we run it
    in fp16 on GPU / fp32 on CPU without quantization. Returns
    (model, tokenizer). Raises on failure."""
    import torch
    from transformers.models.auto.modeling_auto import AutoModelForSeq2SeqLM
    from transformers.models.auto.tokenization_auto import AutoTokenizer

    from backend.vision.runtime import _materialize_to

    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForSeq2SeqLM.from_pretrained(
        name, torch_dtype=dtype, low_cpu_mem_usage=False
    )
    model = _materialize_to(model, device).eval()
    tokenizer = AutoTokenizer.from_pretrained(name)
    for p in model.parameters():
        p.requires_grad_(False)
    return model, tokenizer


def get_translator() -> dict:
    """Return the cached translator bundle, loading on first use.

    The bundle dict:
      {
        "device": "cuda"|"cpu",
        "madlad_model", "madlad_tokenizer",
        "opus_en_mul_model", "opus_en_mul_tokenizer",   # may be None if it
        "opus_mul_en_model", "opus_mul_en_tokenizer",   # failed to load
      }

    MADLAD is REQUIRED — if it can't load we latch unavailable and raise
    RuntimeError (caller falls back to NLLB). The Opus models are OPTIONAL: a
    failure to load them only disables the exotic gap-languages (Tongan, …);
    MADLAD still serves everything else. Thread-safe (double-checked lock)."""
    global _CACHE, _UNAVAILABLE
    if _CACHE is not None:
        return _CACHE
    if _UNAVAILABLE:
        raise RuntimeError("translate_engine previously failed to load")

    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        if _UNAVAILABLE:
            raise RuntimeError("translate_engine previously failed to load")
        try:
            from backend.vision.runtime import get_device

            device = get_device()

            # PRIMARY (required). A failure here latches unavailable.
            madlad_model, madlad_tok = _build_madlad()

            # GAP-FILLER (optional). Wrap each so a download/load failure for
            # the exotic models doesn't kill the whole engine — MADLAD alone
            # still covers 400+ languages.
            opus_en_mul_model = opus_en_mul_tok = None
            opus_mul_en_model = opus_mul_en_tok = None
            try:
                opus_en_mul_model, opus_en_mul_tok = _build_opus(
                    _opus_en_mul_name(), device
                )
                logger.info("translate_engine: loaded opus-mt-en-mul on %s", device)
            except Exception:
                logger.exception(
                    "translate_engine: opus-mt-en-mul load failed; exotic "
                    "EN→x languages (Tongan, …) will be unavailable"
                )
            try:
                opus_mul_en_model, opus_mul_en_tok = _build_opus(
                    _opus_mul_en_name(), device
                )
                logger.info("translate_engine: loaded opus-mt-mul-en on %s", device)
            except Exception:
                logger.exception(
                    "translate_engine: opus-mt-mul-en load failed; exotic "
                    "x→EN languages will be unavailable"
                )

            _CACHE = {
                "device": device,
                "madlad_model": madlad_model,
                "madlad_tokenizer": madlad_tok,
                "opus_en_mul_model": opus_en_mul_model,
                "opus_en_mul_tokenizer": opus_en_mul_tok,
                "opus_mul_en_model": opus_mul_en_model,
                "opus_mul_en_tokenizer": opus_mul_en_tok,
            }
            return _CACHE
        except Exception as exc:
            _UNAVAILABLE = True
            logger.exception(
                "translate_engine: MADLAD load failed; engine unavailable "
                "(callers fall back to NLLB)"
            )
            raise RuntimeError("translate_engine unavailable") from exc


# ===========================================================================
# LLM tier — resident Qwen2.5-Instruct for the low-resource gap-languages.
#
# We DON'T load a new model: `get_summary_rewriter()` already keeps a
# Qwen2.5-1.5B-Instruct resident on the GPU for the summary/rewrite pipeline,
# so we borrow that exact (model, tokenizer, device) and drive it with a strict
# translation prompt. Availability is latched the first time we ask: if the
# rewriter can't be brought up (no [ml] extras / weights uncached) we fall back
# to the Opus route so nothing breaks.
# ===========================================================================
_LLM_UNAVAILABLE = False  # latched True once we know the rewriter can't load.


def _get_llm():
    """Return the resident (model, tokenizer, device) summary-rewriter LLM
    (Qwen2.5-Instruct), or None if it can't be loaded. Latches unavailability so
    we only probe once. Import-safe: the heavy import lives in the runtime
    helper, not here."""
    global _LLM_UNAVAILABLE
    if _LLM_UNAVAILABLE:
        return None
    try:
        from backend.vision.runtime import get_summary_rewriter

        model, tokenizer, device = get_summary_rewriter()
        return model, tokenizer, device
    except Exception:
        _LLM_UNAVAILABLE = True
        logger.exception(
            "translate_engine: summary-rewriter LLM unavailable; low-resource "
            "languages (Tongan, …) fall back to the Opus route"
        )
        return None


def _llm_translate_one(model, tokenizer, device, lang_name: str, text: str) -> str:
    """Translate ONE block of English `text` into `lang_name` with the resident
    Qwen LLM, using the strict translation prompt. Greedy decode (matches the
    summarizer's policy). Returns the stripped translation, or "" on failure.

    Reuses the SAME chat-template + generate shape the summary rewriter uses
    (`apply_chat_template` → tokenize → greedy `generate` → decode only the new
    tokens). Guarded by `_GEN_LOCK` because the fast tokenizer is shared with
    the summary pipeline and is NOT re-entrant across threads."""
    import torch

    body = (text or "").strip()
    if not body:
        return ""
    # The exact prompt the task specifies. A short system line keeps Qwen from
    # adding "Sure, here is the translation:" preamble; the user turn is the
    # literal instruction so the reply is ONLY the translated string.
    messages = [
        {
            "role": "system",
            "content": (
                "You are a professional translator. You output ONLY the "
                "translation of the user's text, with no preamble, no notes, "
                "no quotation marks, and no explanation."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Translate this English text to {lang_name}. "
                f"Reply with ONLY the translation:\n\n{body}"
            ),
        },
    ]
    with _GEN_LOCK:
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(device)
        prompt_len = int(inputs.input_ids.shape[1])
        # Budget the output relative to the input length (translations are a
        # similar order of magnitude), with a sane floor/ceiling so one odd
        # block can't run away. ~1.8× the prompt's *content* tokens + slack.
        approx_in = max(8, prompt_len)
        max_new = max(48, min(512, int(approx_in * 1.8) + 24))
        with torch.no_grad():
            out_ids = model.generate(
                **inputs,
                max_new_tokens=max_new,
                do_sample=False,
                num_beams=1,
                pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        new_ids = out_ids[0][prompt_len:]
        reply = tokenizer.decode(new_ids, skip_special_tokens=True)
    # Tidy: collapse whitespace and strip stray wrapping quotes the model may
    # add despite the instruction.
    reply = re.sub(r"\s+", " ", reply or "").strip().strip('"').strip("'").strip()
    return reply


def _llm_translate_text(lang_name: str, text: str) -> str | None:
    """Translate `text` into `lang_name` via the resident LLM, chunking long
    input on natural boundaries (reusing `_chunk`). Returns the joined
    translation, or None when the LLM is unavailable so the caller can fall
    back to the Opus route. Best-effort per chunk (a failing chunk is skipped),
    matching `translate_text`'s contract."""
    loaded = _get_llm()
    if loaded is None:
        return None
    model, tokenizer, device = loaded
    chunks = [c for c in _chunk(text) if c.strip()] or [text]
    parts: list[str] = []
    for ch in chunks:
        try:
            parts.append(_llm_translate_one(model, tokenizer, device, lang_name, ch))
        except Exception:
            logger.exception("translate_engine: LLM chunk failed; skipping")
            parts.append("")
    return _join(parts)


def _llm_translate_batch(lang_name: str, texts: list[str]) -> list[str] | None:
    """Translate a LIST of blocks into `lang_name` via the resident LLM,
    returning a list 1:1 with `texts` (empty input → "" at its index without a
    model call). Returns None when the LLM is unavailable so the caller can fall
    back to the Opus route.

    The LLM is autoregressive (no cheap padded batch like the seq2seq engines),
    so we translate one block PER generate() — matching the task's "Batch per
    block" instruction. Best-effort per item (a failing/blank block keeps "").
    """
    loaded = _get_llm()
    if loaded is None:
        return None
    model, tokenizer, device = loaded
    out: list[str] = []
    for t in texts:
        body = (t or "").strip()
        if not body:
            out.append("")
            continue
        try:
            out.append(_llm_translate_one(model, tokenizer, device, lang_name, body))
        except Exception:
            logger.exception("translate_engine: LLM block failed; blank for this item")
            out.append("")
    return out


# ===========================================================================
# Chunking + generation
# ===========================================================================
def _chunk(text: str, chunk_chars: int = _CHUNK_CHARS) -> list[str]:
    """Split text into <= chunk_chars pieces on paragraph/sentence/word
    boundaries so a chunk never cuts mid-word. Mirrors ocr.py's splitter."""
    text = text.strip()
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        end = min(i + chunk_chars, n)
        if end < n:
            window = text[i:end]
            for sep in ("\n\n", "\n", ". ", "! ", "? ", "; ", "。", " "):
                cut = window.rfind(sep)
                if cut > chunk_chars * 0.5:
                    end = i + cut + len(sep)
                    break
        out.append(text[i:end])
        i = end
    return out


def _greedy_kwargs() -> dict:
    """Greedy decode + no-repeat guard — identical decoding policy to the NLLB
    path so output style matches and degenerate loops are suppressed."""
    return dict(do_sample=False, num_beams=1, no_repeat_ngram_size=3)


def _generate_madlad(model, tokenizer, device, token: str, chunk: str) -> str:
    """Translate ONE chunk with MADLAD. Prefixes the input with the `<2xx>`
    target token (MADLAD's translation trigger) and decodes greedily.
    Tokenize+generate+decode run under `_GEN_LOCK` (fast-tokenizer re-entrancy)."""
    import torch

    prompt = f"{token} {chunk.strip()}"
    with _GEN_LOCK:
        enc = tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=1024
        )
        enc = {k: v.to(device) for k, v in enc.items()}
        in_len = int(enc["input_ids"].shape[1])
        max_new = max(32, min(1024, int(in_len * 1.6) + 24))
        with torch.no_grad():
            out_ids = model.generate(**enc, max_new_tokens=max_new, **_greedy_kwargs())
        decoded = tokenizer.batch_decode(out_ids, skip_special_tokens=True)
    return (decoded[0] if decoded else "").strip()


def _generate_opus(model, tokenizer, device, token: str, chunk: str) -> str:
    """Translate ONE chunk with Opus-MT. opus-mt-en-mul needs a sentence-
    initial `>>code<<` target token; opus-mt-mul-en (→EN) needs none, so
    `token` is "" for that direction. Guarded by `_GEN_LOCK`."""
    import torch

    prompt = f"{token} {chunk.strip()}".strip()
    with _GEN_LOCK:
        enc = tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=512
        )
        enc = {k: v.to(device) for k, v in enc.items()}
        in_len = int(enc["input_ids"].shape[1])
        max_new = max(32, min(512, int(in_len * 1.8) + 24))
        with torch.no_grad():
            out_ids = model.generate(**enc, max_new_tokens=max_new, **_greedy_kwargs())
        decoded = tokenizer.batch_decode(out_ids, skip_special_tokens=True)
    return (decoded[0] if decoded else "").strip()


def _join(parts: list[str]) -> str:
    """Rejoin translated chunks with a single space. Chunks were split on
    natural sentence/paragraph boundaries already (and most inputs are a single
    chunk at this size), so a plain space between them reads correctly and
    never doubles separators. Empty pieces are dropped."""
    return " ".join(p for p in parts if p).strip()


def _resolve_route(target: str, source: str | None, bundle: dict) -> dict:
    """Resolve the concrete (model, tokenizer, tokens) for a `(target, source)`.

    Returns a dict: {"use", "model", "tokenizer", "gen_token", "madlad_token"}.
      * "use" ∈ {"madlad", "opus-mt-en-mul", "opus-mt-mul-en"};
      * for a MADLAD route, "madlad_token" is the `<2xx>` prefix and
        model/tokenizer point at the MADLAD bundle;
      * for an Opus route, model/tokenizer point at the Opus bundle and
        "gen_token" is the sentence-initial `>>code<<` (or "" for →EN).
    Falls back to MADLAD English when a needed Opus model didn't load. Shared by
    `translate_text` and `translate_batch` so both route identically."""
    engine, code = resolve_target(target)
    target_is_english = _norm_lookup(target) in {"en", "eng", "eng_latn"}

    model = tokenizer = None
    gen_token = ""
    use = "madlad"

    if engine == "opus":
        # Route A — into an exotic language via en-mul.
        model = bundle["opus_en_mul_model"]
        tokenizer = bundle["opus_en_mul_tokenizer"]
        gen_token = code  # ">>ton<<"
        use = "opus-mt-en-mul"
    elif target_is_english and _source_is_exotic(source):
        # Route B — out of an exotic language into English via mul-en.
        model = bundle["opus_mul_en_model"]
        tokenizer = bundle["opus_mul_en_tokenizer"]
        gen_token = ""  # →EN takes no target token
        use = "opus-mt-mul-en"

    if use.startswith("opus") and (model is None or tokenizer is None):
        logger.warning(
            "translate_engine: %s unavailable for target=%r source=%r; "
            "falling back to MADLAD", use, target, source,
        )
        use = "madlad"

    if use == "madlad":
        model = bundle["madlad_model"]
        tokenizer = bundle["madlad_tokenizer"]

    return {
        "use": use,
        "model": model,
        "tokenizer": tokenizer,
        "gen_token": gen_token,
        # When we degraded an Opus route to MADLAD the engine token isn't a
        # MADLAD token, so translate via English (safest for Latin text).
        "madlad_token": code if (use == "madlad" and engine == "madlad") else "<2en>",
    }


def translate_text(text: str, target: str, source: str | None = None) -> str:
    """Translate `text` into `target`, returning the translated string.

    ROUTING: `target` is resolved by `resolve_target` to either a MADLAD
    `<2xx>` token (the 400-language primary) or an Opus `>>code<<` route (the
    exotic gap-fillers like Tongan). Long input is chunked on natural
    boundaries; each chunk is decoded greedily (num_beams=1,
    no_repeat_ngram_size=3) and the pieces are rejoined.

    `source` is accepted for signature compatibility with the callers. MADLAD
    is target-token driven and auto-detects the source, so `source` is only
    consulted to pick the Opus DIRECTION: when the target is an Opus
    gap-language we use en-mul (assumes EN-ish source, the common case for
    "translate this UI/sign into Tongan"); when the SOURCE is an Opus
    gap-language and the target is English we use mul-en.

    Raises RuntimeError when the engine can't be loaded (caller falls back to
    NLLB). Empty input → "". Best-effort per chunk: a single failing chunk is
    skipped rather than aborting the whole translation."""
    text = (text or "").strip()
    if not text:
        return ""
    text = text[:_MAX_INPUT_CHARS]

    # LLM TIER (low-resource gap-languages: Tongan, …). MADLAD lacks these and
    # Opus mangles them, so for this small set we translate with the resident
    # Qwen LLM via a strict prompt. Tried BEFORE loading the seq2seq engine so a
    # Tongan request never pays the MADLAD/Opus load; if the LLM is unavailable
    # `_llm_translate_text` returns None and we fall through to the Opus route
    # below (existing behaviour, nothing breaks).
    llm_name = _llm_target_name(target)
    if llm_name is not None:
        llm_out = _llm_translate_text(llm_name, text)
        if llm_out is not None:
            return llm_out

    bundle = get_translator()  # RuntimeError → caller falls back to NLLB
    device = bundle["device"]

    route = _resolve_route(target, source, bundle)
    use = route["use"]
    model = route["model"]
    tokenizer = route["tokenizer"]
    gen_token = route["gen_token"]

    chunks = [c for c in _chunk(text) if c.strip()] or [text]
    parts: list[str] = []

    if use.startswith("opus"):
        for ch in chunks:
            try:
                parts.append(_generate_opus(model, tokenizer, device, gen_token, ch))
            except Exception:
                logger.exception("translate_engine: opus chunk failed; skipping")
                parts.append("")
    else:
        madlad_token = route["madlad_token"]
        for ch in chunks:
            try:
                parts.append(
                    _generate_madlad(model, tokenizer, device, madlad_token, ch)
                )
            except Exception:
                logger.exception("translate_engine: madlad chunk failed; skipping")
                parts.append("")

    return _join(parts)


# ===========================================================================
# Batched translation — translate a LIST of short strings in ONE padded
# generate() per sub-batch. This exists because the per-CALL cost of the
# 8-bit-quantized MADLAD generate() is large and roughly fixed (~seconds),
# independent of how short the input is. Translating N short UI strings with N
# separate `translate_text` calls therefore costs ~N × that fixed latency
# (~250 s for the 57 loader phrases). Running them as a single PADDED BATCH
# collapses that to ~one generate() per `_BATCH_SIZE` items — the model
# processes the whole batch in parallel on the GPU — while keeping a strict 1:1
# input→output alignment (no newline-join line-drift). Used by the localized
# loader-phrases endpoint; safe for any short-string list.
# ===========================================================================
# Sub-batch size: how many strings go into one padded generate(). Each phrase is
# only a few tokens, so a sub-batch of 32 is a tiny tensor on the GPU; we sub-
# batch (rather than one giant batch) only to bound peak activation memory on
# the shared 8 GB card. Larger ⇒ fewer generate() calls ⇒ faster, up to memory.
_BATCH_SIZE = 32
# Per-ITEM token budget in a batch — short UI strings only. Bounds both input
# truncation and the per-item new-token cap so one odd item can't blow up the
# whole batch's generation length.
_BATCH_ITEM_MAX_TOKENS = 64


def _generate_batch_madlad(model, tokenizer, device, token: str,
                           texts: list[str],
                           item_max_tokens: int = _BATCH_ITEM_MAX_TOKENS) -> list[str]:
    """Translate a list of strings with MADLAD in ONE padded generate().
    Each input is prefixed with the `<2xx>` target token. Returns a list of the
    same length (decoded, stripped), 1:1 with `texts`. `item_max_tokens` bounds
    both input truncation and the per-item new-token cap — short UI strings keep
    the default 64; the PDF-block path raises it so a longer line isn't cut."""
    import torch

    prompts = [f"{token} {t.strip()}" for t in texts]
    with _GEN_LOCK:
        enc = tokenizer(
            prompts, return_tensors="pt", padding=True, truncation=True,
            max_length=item_max_tokens,
        )
        enc = {k: v.to(device) for k, v in enc.items()}
        in_len = int(enc["input_ids"].shape[1])
        max_new = max(16, min(item_max_tokens, int(in_len * 1.6) + 16))
        with torch.no_grad():
            out_ids = model.generate(**enc, max_new_tokens=max_new, **_greedy_kwargs())
        decoded = tokenizer.batch_decode(out_ids, skip_special_tokens=True)
    return [(d or "").strip() for d in decoded]


def _generate_batch_opus(model, tokenizer, device, token: str,
                         texts: list[str],
                         item_max_tokens: int = _BATCH_ITEM_MAX_TOKENS) -> list[str]:
    """Translate a list of strings with Opus-MT in ONE padded generate().
    opus-mt-en-mul needs a sentence-initial `>>code<<` token on each item;
    →EN (mul-en) takes none (`token` == ""). Returns a list 1:1 with `texts`.
    `item_max_tokens` bounds input truncation + per-item new-token cap (default
    64 for short strings; the PDF-block path raises it)."""
    import torch

    prompts = [f"{token} {t.strip()}".strip() for t in texts]
    with _GEN_LOCK:
        enc = tokenizer(
            prompts, return_tensors="pt", padding=True, truncation=True,
            max_length=item_max_tokens,
        )
        enc = {k: v.to(device) for k, v in enc.items()}
        in_len = int(enc["input_ids"].shape[1])
        max_new = max(16, min(item_max_tokens, int(in_len * 1.8) + 16))
        with torch.no_grad():
            out_ids = model.generate(**enc, max_new_tokens=max_new, **_greedy_kwargs())
        decoded = tokenizer.batch_decode(out_ids, skip_special_tokens=True)
    return [(d or "").strip() for d in decoded]


# Per-item token budget for the PDF / document BLOCK batch path. Line-level PDF
# units (and merged paragraphs) routinely run longer than a few-word UI phrase,
# so the loader-phrases default (64) would TRUNCATE them. 256 covers a long
# wrapped paragraph line while keeping the padded batch tensor modest on the
# shared 8 GB card.
_BLOCK_ITEM_MAX_TOKENS = 256


def translate_batch(
    texts: list[str],
    target: str,
    source: str | None = None,
    *,
    batch_size: int = _BATCH_SIZE,
    item_max_tokens: int = _BATCH_ITEM_MAX_TOKENS,
) -> list[str]:
    """Translate a LIST of strings into `target`, returning a list of the SAME
    LENGTH with each translation at the same index.

    Routes exactly like `translate_text` (MADLAD `<2xx>` primary, Opus
    `>>code<<` for the exotic gap-languages — the SAME `_resolve_route`, so
    batched Tongan/Fijian/Samoan/Tahitian go through opus-mt-en-mul just like a
    single `translate_text` call), but runs the inputs as PADDED BATCHES — one
    `generate()` per `batch_size` items — so N strings cost ~N/batch_size
    generate() calls instead of N. Strict 1:1 alignment (unlike newline-joining a
    block, which the models may merge/split).

    `batch_size` / `item_max_tokens` default to the short-UI-string tuning (32 /
    64); the PDF-block path passes a smaller batch_size with a larger
    item_max_tokens (`_BLOCK_ITEM_MAX_TOKENS`) so a longer line isn't truncated.

    Inputs are stripped; an empty input yields "" at its index without a model
    call. Raises RuntimeError when the engine can't be loaded (same contract as
    `translate_text`). Best-effort per sub-batch: if one padded generate() fails
    the items in THAT sub-batch come back "" (the caller can fall back per item)
    rather than aborting the whole list."""
    if not texts:
        return []

    # LLM TIER (low-resource gap-languages: Tongan, …) — same pre-emption as
    # `translate_text`. For this small set the resident Qwen LLM translates each
    # block (one generate() per block); if the LLM is unavailable
    # `_llm_translate_batch` returns None and we fall through to the seq2seq
    # engine route below (existing Opus behaviour). Tried BEFORE the engine load
    # so a Tongan document never pays the MADLAD/Opus startup.
    llm_name = _llm_target_name(target)
    if llm_name is not None:
        llm_out = _llm_translate_batch(llm_name, texts)
        if llm_out is not None:
            return llm_out

    bundle = get_translator()  # RuntimeError → caller handles fallback
    device = bundle["device"]
    route = _resolve_route(target, source, bundle)
    use = route["use"]
    model = route["model"]
    tokenizer = route["tokenizer"]

    # Map non-empty inputs to their indices so empties cost nothing and the
    # output stays perfectly aligned.
    idx = [i for i, t in enumerate(texts) if (t or "").strip()]
    out: list[str] = ["" for _ in texts]

    if use.startswith("opus"):
        gen_token = route["gen_token"]
        gen = lambda sub: _generate_batch_opus(
            model, tokenizer, device, gen_token, sub, item_max_tokens
        )
    else:
        madlad_token = route["madlad_token"]
        gen = lambda sub: _generate_batch_madlad(
            model, tokenizer, device, madlad_token, sub, item_max_tokens
        )

    step = max(1, int(batch_size))
    for s in range(0, len(idx), step):
        chunk_idx = idx[s:s + step]
        sub = [texts[i].strip() for i in chunk_idx]
        try:
            res = gen(sub)
        except Exception:
            logger.exception("translate_engine: batch generate failed; sub-batch blank")
            res = ["" for _ in sub]
        for i, r in zip(chunk_idx, res):
            out[i] = r
    return out


# ===========================================================================
# Introspection helper (used by the standalone test + diagnostics).
# ===========================================================================
def route_for(target: str, source: str | None = None) -> str:
    """Human label of which sub-model would handle `(target, source)` — for
    tests / diagnostics only. Mirrors translate_text's routing (LLM tier for the
    low-resource gap-languages; Route A → Opus en-mul into an exotic target;
    Route B → Opus mul-en out of an exotic source into English; else MADLAD).
    Does not load anything (the LLM label reflects the static routing decision,
    not whether the LLM is currently loadable)."""
    llm_name = _llm_target_name(target)
    if llm_name is not None:
        return f"qwen-llm ({llm_name})"
    engine, code = resolve_target(target)
    if engine == "opus":
        return f"opus-mt-en-mul ({code})"
    if _norm_lookup(target) in {"en", "eng", "eng_latn"} and _source_is_exotic(source):
        return "opus-mt-mul-en (→en)"
    return f"madlad400-3b ({code})"
