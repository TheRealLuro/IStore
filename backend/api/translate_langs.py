"""Translation language catalogue — the source of truth for the FE picker.

  GET /translate/languages
      → the FULL list of languages the Apache-2.0 engine
        (`backend/api/translate_engine.py`: MADLAD-400 + Opus-MT) can produce,
        as:
          [{"code": "<engine-accepted code>", "name": "<English name>"}, …]
        sorted by `name`, deduped by `name`.

  GET /translate/loader-phrases?lang=<code>
      → the document-translation LOADING phrases ("Scribing", "Decoding
        hieroglyphs", …) translated INTO `lang`, so the FE loader can narrate in
        the language the user is translating TO:
          {"lang": "<code>", "phrases": ["…", …]}
        The English base list is the canonical copy of `SCRIBE_PHRASES` from
        `frontend/neuthek/src/neuthek-loader.jsx`. Each language is translated
        ONCE and cached in-process (and best-effort to
        ./data/loader_phrases_cache.json) so a repeat selection is instant and
        never re-loads the model. `lang` empty / English → the English base list
        is returned verbatim with NO model call (the en-fast-path). On ANY
        failure (engine cold/busy/unavailable) it falls back to the English base
        list — it never errors and never hangs the loader.

WHY THIS EXISTS / THE GUARANTEE
-------------------------------
The FE language picker must only ever offer codes the engine actually maps.
`translate_engine.resolve_target` turns a caller code into either a MADLAD
`<2xx>` token or an Opus `>>xxx<<` route; an UNMAPPED code silently resolves to
English (`<2en>`), which would be an invisible "translated to English instead"
bug. To make that impossible, this catalogue is built FROM the engine's own
static maps and every candidate `code` is run through `resolve_target` at build
time — a code is only emitted if it resolves to a NON-default route (i.e. it is
NOT the English fallback unless the language genuinely *is* English). So every
`code` the FE receives is provably round-trippable through the engine.

The English display NAME for each code comes from a built-in code→name table
below (`_CODE_NAMES`) covering every code this module exposes; the Opus gap
languages reuse `translate_engine._OPUS_NAMES`.

IMPORT-SAFETY
-------------
NO heavy imports. This module only reads the STATIC dicts in
`translate_engine` (which itself imports nothing heavy at module load — torch /
transformers are lazy inside its functions). Importing this module — or calling
`build_languages()` — never loads a model, so it is safe at API boot under
`uvicorn --reload` and safe to call from a request handler synchronously.
"""
from __future__ import annotations

import json
import logging
import os
import threading

from fastapi import APIRouter, Query

from backend.api import translate_engine as _eng

logger = logging.getLogger(__name__)

# Same shape/feel as the other small routers; its own /translate prefix so the
# path is exactly GET /translate/languages. Auth is intentionally omitted — the
# language list is public, non-sensitive metadata.
router = APIRouter(prefix="/translate", tags=["translate"])


# ===========================================================================
# Code → English display name.
#
# Keys are ENGINE codes — the exact strings we hand the FE as `code` and that
# `translate_engine.resolve_target` maps. For MADLAD-routed languages that is
# the MADLAD target code (the VALUE side of `_ISO_TO_MADLAD`, e.g. "es", "zh");
# for the two script-distinct Chinese variants it is the code that resolves to
# the right script ("zho_Hant" → Traditional, "yue" → Cantonese); for the Opus
# gap languages it is the 2-letter ISO that routes to Opus ("to","fj","sm","ty").
#
# This table is the single place a display name is defined; `build_languages`
# below only emits a language whose code appears here AND round-trips through
# the engine, so the two never drift into an unnamed or unmapped code.
# ===========================================================================
_CODE_NAMES: dict[str, str] = {
    # --- Western / Central Europe -----------------------------------------
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ca": "Catalan",
    "gl": "Galician", "eu": "Basque", "ga": "Irish", "cy": "Welsh",
    "is": "Icelandic", "lb": "Luxembourgish", "mt": "Maltese",
    # --- Nordic ------------------------------------------------------------
    "sv": "Swedish", "da": "Danish", "no": "Norwegian", "nn": "Norwegian Nynorsk",
    "fi": "Finnish",
    # --- Baltic / Slavic ---------------------------------------------------
    "pl": "Polish", "cs": "Czech", "sk": "Slovak", "sl": "Slovenian",
    "hr": "Croatian", "bs": "Bosnian", "sr": "Serbian", "mk": "Macedonian",
    "bg": "Bulgarian", "ru": "Russian", "uk": "Ukrainian", "be": "Belarusian",
    "lt": "Lithuanian", "lv": "Latvian", "et": "Estonian",
    # --- Greek / Romance tail / misc Europe -------------------------------
    "el": "Greek", "ro": "Romanian", "hu": "Hungarian", "sq": "Albanian",
    # --- Middle East / Semitic / Iranian ----------------------------------
    "ar": "Arabic", "he": "Hebrew", "fa": "Persian", "ur": "Urdu",
    "ps": "Pashto", "ku": "Kurdish", "ckb": "Central Kurdish (Sorani)",
    "az": "Azerbaijani", "hy": "Armenian", "ka": "Georgian",
    # --- Turkic / Central Asia --------------------------------------------
    "tr": "Turkish", "kk": "Kazakh", "uz": "Uzbek", "ky": "Kyrgyz",
    "tk": "Turkmen", "tt": "Tatar", "ba": "Bashkir", "mn": "Mongolian",
    # --- South Asia (Indic) -----------------------------------------------
    "hi": "Hindi", "bn": "Bengali", "pa": "Punjabi", "gu": "Gujarati",
    "or": "Odia (Oriya)", "ta": "Tamil", "te": "Telugu", "kn": "Kannada",
    "ml": "Malayalam", "mr": "Marathi", "ne": "Nepali", "si": "Sinhala",
    "sd": "Sindhi", "as": "Assamese", "sa": "Sanskrit", "mai": "Maithili",
    # --- East / Southeast Asia --------------------------------------------
    "zh": "Chinese (Simplified)", "zho_Hant": "Chinese (Traditional)",
    "yue": "Cantonese", "ja": "Japanese", "ko": "Korean", "th": "Thai",
    "lo": "Lao", "my": "Burmese", "km": "Khmer", "vi": "Vietnamese",
    "id": "Indonesian", "ms": "Malay", "tl": "Tagalog (Filipino)",
    "jv": "Javanese", "su": "Sundanese", "bo": "Tibetan",
    # --- Africa ------------------------------------------------------------
    "sw": "Swahili", "am": "Amharic", "ha": "Hausa", "yo": "Yoruba",
    "ig": "Igbo", "zu": "Zulu", "xh": "Xhosa", "af": "Afrikaans",
    "so": "Somali", "rw": "Kinyarwanda", "ny": "Chichewa (Nyanja)",
    "sn": "Shona", "st": "Southern Sotho", "tn": "Tswana", "lg": "Ganda",
    "wo": "Wolof", "ff": "Fula", "ti": "Tigrinya", "om": "Oromo",
    "ln": "Lingala",
    # --- Other / constructed ----------------------------------------------
    "eo": "Esperanto", "la": "Latin", "ht": "Haitian Creole",
    # --- Opus-MT gap languages (codes that route through opus-mt) ----------
    # Names also live in translate_engine._OPUS_NAMES; mirrored here so this
    # table is self-contained for every code we expose.
    "to": "Tongan", "fj": "Fijian", "sm": "Samoan", "ty": "Tahitian",

    # --- MADLAD-400 FULL coverage (the long tail / exotic languages) -------
    # English names for every additional MADLAD-400 `<2xx>` code now exposed
    # via translate_engine._MADLAD_FULL. Codes carrying a script/region suffix
    # name the script/region in parentheses (e.g. "bn_Latn" → "Bengali (Latin)",
    # "ms_Arab" → "Malay (Jawi)"), so the picker row is unambiguous. Names are
    # standard ISO-639-3 / Ethnologue English names. build_languages() only
    # emits a code whose name is HERE *and* that round-trips through
    # resolve_target, so this table stays the single source of truth and no raw
    # code can leak into the UI. (zh_Hant is intentionally NOT here — Traditional
    # Chinese is exposed via the FLORES code "zho_Hant" above.)
    "abt": "Ambulas", "ace": "Acehnese", "ace_Arab": "Acehnese (Jawi)",
    "acf": "Saint Lucian Creole", "ada": "Adangme", "adh": "Adhola",
    "ady": "Adyghe", "agr": "Aguaruna", "ahk": "Akha",
    "ak": "Akan", "akb": "Batak Angkola", "alt": "Southern Altai",
    "alz": "Alur", "amu": "Guerrero Amuzgo", "an": "Aragonese",
    "ang": "Old English", "ann": "Obolo", "ape": "Bukiyip",
    "arn": "Mapuche", "ary": "Moroccan Arabic", "arz": "Egyptian Arabic",
    "av": "Avar", "awa": "Awadhi", "ay": "Aymara",
    "az_RU": "Azerbaijani (Cyrillic)", "ban": "Balinese", "bar": "Bavarian",
    "bas": "Basaa", "bbc": "Batak Toba", "bci": "Baoulé",
    "ber": "Berber (Tamazight)", "ber_Latn": "Berber (Latin)", "bew": "Betawi",
    "bg_Latn": "Bulgarian (Latin)", "bgp": "Eastern Balochi", "bho": "Bhojpuri",
    "bi": "Bislama", "bik": "Bikol", "bim": "Bimoba",
    "bjn": "Banjar", "bjn_Arab": "Banjar (Jawi)", "bm": "Bambara",
    "bn_Latn": "Bengali (Latin)", "bqc": "Boko", "br": "Breton",
    "bru": "Eastern Bru", "brx": "Bodo", "bts": "Batak Simalungun",
    "btx": "Batak Karo", "bua": "Buryat", "bug": "Buginese",
    "bum": "Bulu", "bus": "Bokobaru", "bzj": "Belize Kriol",
    "cab": "Garifuna", "cac": "Chuj", "cak": "Kaqchikel",
    "cbk": "Chavacano", "cce": "Chopi", "ce": "Chechen",
    "ceb": "Cebuano", "cfm": "Falam Chin", "ch": "Chamorro",
    "chk": "Chuukese", "chm": "Mari (Meadow)", "chr": "Cherokee",
    "cnh": "Hakha Chin", "co": "Corsican", "cr_Latn": "Cree (Latin)",
    "crh": "Crimean Tatar", "crh_Latn": "Crimean Tatar (Latin)",
    "crs": "Seselwa Creole", "ctd_Latn": "Tedim Chin", "ctu": "Chol",
    "cuk": "San Blas Kuna", "cv": "Chuvash", "din": "Dinka",
    "dje": "Zarma", "djk": "Aukan", "dln": "Darlong",
    "doi": "Dogri", "dov": "Dombe", "dtp": "Kadazan Dusun",
    "dv": "Dhivehi", "dwr": "Dawro", "dyu": "Dyula",
    "dz": "Dzongkha", "ee": "Ewe", "el_Latn": "Greek (Latin)",
    "emp": "Northern Emberá", "enq": "Enga", "ff": "Fula",
    "ffm": "Maasina Fulfulde", "fil": "Filipino", "fip": "Fipa",
    "fj": "Fijian", "fo": "Faroese", "fon": "Fon",
    "fr_CA": "French (Canada)", "frp": "Arpitan", "fur": "Friulian",
    "fuv": "Nigerian Fulfulde", "fy": "Western Frisian", "gag": "Gagauz",
    "gbm": "Garhwali", "gd": "Scottish Gaelic", "gn": "Guarani",
    "gof": "Gofa", "gom": "Konkani (Goan)", "gom_Latn": "Konkani (Latin)",
    "gor": "Gorontalo", "grc": "Ancient Greek", "gsw": "Swiss German",
    "gu_Latn": "Gujarati (Latin)", "gub": "Guajajára", "guc": "Wayuu",
    "guh": "Guahibo", "gui": "Eastern Bolivian Guaraní", "gv": "Manx",
    "gvl": "Gulay", "gym": "Ngäbere", "haw": "Hawaiian",
    "hi_Latn": "Hindi (Latin)", "hif": "Fiji Hindi", "hil": "Hiligaynon",
    "hmn": "Hmong", "hne": "Chhattisgarhi", "ho": "Hiri Motu",
    "hui": "Huli", "hus": "Wastek", "hvn": "Sabu",
    "iba": "Iban", "ibb": "Ibibio", "ify": "Keley-i Kallahan",
    "ilo": "Ilocano", "inb": "Inga", "io": "Ido",
    "iso": "Isoko", "iu": "Inuktitut", "ium": "Iu Mien",
    "izz": "Izii", "jac": "Jakalteko", "jam": "Jamaican Patois",
    "jiv": "Shuar", "jvn": "Caribbean Javanese", "kaa": "Karakalpak",
    "kaa_Latn": "Karakalpak (Latin)", "kac": "Jingpho", "kbd": "Kabardian",
    "kbp": "Kabiye", "kek": "Qʼeqchiʼ", "kg": "Kongo",
    "kha": "Khasi", "kj": "Kuanyama", "kjg": "Khmu",
    "kjh": "Khakas", "kl": "Greenlandic (Kalaallisut)", "kmb": "Kimbundu",
    "kmz_Latn": "Khorasani Turkic (Latin)", "kn_Latn": "Kannada (Latin)",
    "knj": "Western Kanjobal", "koi": "Komi-Permyak", "kos": "Kosraean",
    "kr": "Kanuri", "kr_Arab": "Kanuri (Arabic)", "krc": "Karachay-Balkar",
    "kri": "Krio", "ks": "Kashmiri", "ks_Deva": "Kashmiri (Devanagari)",
    "ksd": "Kuanua", "ksw": "S'gaw Karen", "ktu": "Kituba",
    "kum": "Kumyk", "kv": "Komi", "kw": "Cornish",
    "kwi": "Awa-Cuaiquer", "laj": "Lango", "lhu": "Lahu",
    "li": "Limburgish", "lij": "Ligurian", "lmo": "Lombard",
    "lrc": "Northern Luri", "ltg": "Latgalian", "lu": "Luba-Katanga",
    "lus": "Mizo", "mad": "Madurese", "mag": "Magahi",
    "mak": "Makasar", "mam": "Mam", "mas": "Maasai",
    "mass": "Maasai (alt.)", "maz": "Central Mazahua", "mbt": "Matigsalug Manobo",
    "mdf": "Moksha", "meo": "Kedah Malay", "meu": "Motu",
    "mfe": "Mauritian Creole", "mg": "Malagasy", "mgh": "Makhuwa-Meetto",
    "mh": "Marshallese", "mi": "Maori", "min": "Minangkabau",
    "miq": "Miskito", "mkn": "Kupang Malay", "ml_Latn": "Malayalam (Latin)",
    "mni": "Meitei (Manipuri)", "mps": "Dadibi", "mqy": "Manggarai",
    "mrj": "Hill Mari", "mrw": "Maranao", "ms_Arab": "Malay (Jawi)",
    "ms_Arab_BN": "Brunei Malay (Jawi)", "msb": "Masbateño", "msi": "Sabah Malay",
    "msm": "Agusan Manobo", "mwl": "Mirandese", "myv": "Erzya",
    "nan_Latn_TW": "Hokkien (Taiwan, Latin)", "ndc_ZW": "Ndau", "nds": "Low German",
    "nds_NL": "Low Saxon (Netherlands)", "new": "Newari", "ngu": "Guerrero Nahuatl",
    "nhe": "Eastern Huasteca Nahuatl", "nia": "Nias", "nij": "Ngaju",
    "niq": "Nandi", "nnb": "Nande", "noa": "Woun Meu",
    "nog": "Nogai", "nr": "Southern Ndebele", "nso": "Northern Sotho (Sepedi)",
    "nus": "Nuer", "nut": "Nung", "nv": "Navajo",
    "nyu": "Nyungwe", "nzi": "Nzima", "oc": "Occitan",
    "oj": "Ojibwe", "os": "Ossetian", "otq": "Querétaro Otomi",
    "pag": "Pangasinan", "pap": "Papiamento", "pau": "Palauan",
    "pck": "Paite Chin", "pis": "Pijin", "pon": "Pohnpeian",
    "ppk": "Uma", "prs": "Dari", "qu": "Quechua",
    "qub": "Huallaga Quechua", "quc": "Kʼicheʼ", "quf": "Lambayeque Quechua",
    "quh": "South Bolivian Quechua", "qup": "Southern Pastaza Quechua",
    "quy": "Ayacucho Quechua", "qvc": "Cajamarca Quechua", "qvi": "Imbabura Quechua",
    "qvz": "Northern Pastaza Quichua", "qxr": "Cañar Highland Quichua",
    "raj": "Rajasthani", "rcf": "Réunion Creole", "rki": "Rakhine",
    "rm": "Romansh", "rmc": "Carpathian Romani", "rn": "Kirundi",
    "rom": "Romani", "ru_Latn": "Russian (Latin)", "rwo": "Rawa",
    "sah": "Yakut (Sakha)", "sat_Latn": "Santali (Latin)", "sc": "Sardinian",
    "scn": "Sicilian", "sda": "Toraja-Sa'dan", "se": "Northern Sami",
    "seh": "Sena", "sg": "Sango", "sh": "Serbo-Croatian",
    "shn": "Shan", "shp": "Shipibo-Conibo", "sja": "Epena",
    "skr": "Saraiki", "sm": "Samoan", "smt": "Simte",
    "spp": "Supyire", "srm": "Saramaccan", "srn": "Sranan Tongo",
    "ss": "Swati", "stq": "Saterland Frisian", "sus": "Susu",
    "suz": "Sunwar", "sxn": "Sangir", "syr": "Syriac",
    "szl": "Silesian", "ta_Latn": "Tamil (Latin)", "tab": "Tabasaran",
    "taj": "Eastern Tamang", "taq": "Tamasheq", "taq_Tfng": "Tamasheq (Tifinagh)",
    "tbz": "Ditammari", "tca": "Ticuna", "tcy": "Tulu",
    "tdx": "Tandroy-Mahafaly Malagasy", "te_Latn": "Telugu (Latin)", "teo": "Teso",
    "tet": "Tetum", "tg": "Tajik", "tiv": "Tiv",
    "tks": "Takestani", "tlh": "Klingon", "tll": "Tetela",
    "tly_IR": "Talysh", "toj": "Tojolabal", "trp": "Kokborok",
    "ts": "Tsonga", "tsc": "Tswa", "tsg": "Tausug",
    "tuc": "Mutu", "tvl": "Tuvaluan", "twu": "Termanu",
    "tyv": "Tuvan", "tyz": "Tày", "tzh": "Tzeltal",
    "tzj": "Tzʼutujil", "tzm": "Central Atlas Tamazight", "tzo": "Tzotzil",
    "ubu": "Umbu-Ungu", "udm": "Udmurt", "ug": "Uyghur",
    "ve": "Venda", "vec": "Venetian", "wa": "Walloon",
    "wal": "Wolaytta", "war": "Waray", "wuu": "Wu Chinese",
    "xal": "Kalmyk", "yap": "Yapese", "yi": "Yiddish",
    "yua": "Yucatec Maya", "zap": "Zapotec", "zh_Latn": "Chinese (Pinyin)",
    "zne": "Zande", "zza": "Zaza",
}


def _is_silent_english(code: str, engine: str, token: str) -> bool:
    """True when `resolve_target(code)` fell back to the MADLAD English token
    `<2en>` even though `code` is NOT English — the unmapped-code default. Such
    a code would silently mistranslate to English and must be dropped."""
    return (
        engine == "madlad"
        and token == "<2en>"
        and code not in {"en", "eng", "eng_latn"}
    )


def build_languages() -> list[dict[str, str]]:
    """Build the FULL supported-language list, sorted by name, deduped by name.

    PURE / NO MODEL LOAD. Reads only the static maps in `translate_engine` and
    validates every candidate code through `translate_engine.resolve_target`,
    so each returned `code`:
      * is a value the engine maps to a concrete `<2xx>` MADLAD token or a
        `>>xxx<<` Opus route (never the silent-English fallback), and
      * has a known English display name (`_CODE_NAMES`).

    Candidate codes are drawn from:
      * the MADLAD target codes (VALUES of `_ISO_TO_MADLAD` / `_FLORES_TO_MADLAD`)
        — these are exactly what MADLAD accepts and what `resolve_target` emits;
      * `zho_Hant` for Traditional Chinese (resolves to `<2zh_Hant>`; the bare
        value `zh_Hant` would collapse to `zh`/Simplified);
      * the Opus gap codes (`to`, `fj`, `sm`, `ty`) that route to opus-mt.

    A final dedupe by resolved engine token guarantees one language per route.
    Returns a list of {"code", "name"} dicts.
    """
    # 1) Collect candidate engine codes.
    candidates: set[str] = set()

    # MADLAD codes (the values the engine actually emits inside `<2…>`). Most
    # are also `_ISO_TO_MADLAD` keys, so they round-trip; the script-suffixed
    # ones (zh_Hant, yue) are handled explicitly below.
    for v in _eng._ISO_TO_MADLAD.values():
        candidates.add(v)
    for v in _eng._FLORES_TO_MADLAD.values():
        candidates.add(v)

    # Traditional Chinese: expose the FLORES code that resolves to the
    # Traditional script. The bare MADLAD value "zh_Hant" collapses to base
    # "zh" (Simplified) in resolve_target, so we offer "zho_Hant" — which is a
    # FLORES key mapping straight to "<2zh_Hant>".
    candidates.discard("zh_Hant")
    candidates.add("zho_Hant")

    # Opus gap languages — the 2-letter ISO codes that route to opus-mt.
    for k in ("to", "fj", "sm", "ty"):
        candidates.add(k)

    # 2) Validate each candidate through the real resolver and attach a name.
    #    Drop anything that (a) has no display name, or (b) resolves to the
    #    silent-English fallback while not actually being English. Then dedupe by
    #    the RESOLVED ENGINE TOKEN so two codes that the engine maps to the SAME
    #    token (e.g. the wrong-script "zh-tw" and "zh" both → "<2zh>") can never
    #    both appear — exactly one language is emitted per distinct engine route,
    #    which is what makes "the name always matches the translation" hold.
    by_token: dict[str, dict[str, str]] = {}
    skipped: list[str] = []
    for code in candidates:
        name = _CODE_NAMES.get(code)
        if not name:
            # No display name → we can't responsibly show it; skip (keeps the
            # table the single source of truth and avoids raw codes in the UI).
            skipped.append(code)
            continue
        try:
            engine, token = _eng.resolve_target(code)
        except Exception:
            logger.exception("translate_langs: resolve_target failed for %r", code)
            skipped.append(code)
            continue
        if _is_silent_english(code, engine, token):
            # Unmapped → would mistranslate to English. Never expose it.
            skipped.append(code)
            continue
        existing = by_token.get(token)
        if existing is None or len(code) < len(existing["code"]):
            by_token[token] = {"code": code, "name": name}

    if skipped:
        logger.debug("translate_langs: skipped unmapped/unnamed codes: %s",
                     sorted(skipped))

    # Final dedupe by NAME (defence-in-depth: two distinct engine tokens should
    # never share a display name, but if a future _CODE_NAMES edit did, the
    # shorter code wins so the picker has no duplicate rows).
    by_name: dict[str, dict[str, str]] = {}
    for entry in by_token.values():
        cur = by_name.get(entry["name"])
        if cur is None or len(entry["code"]) < len(cur["code"]):
            by_name[entry["name"]] = entry

    return sorted(by_name.values(), key=lambda d: d["name"].lower())


@router.get("/languages")
def list_languages() -> list[dict[str, str]]:
    """Return every language the translation engine supports as
    `[{"code","name"}]`, sorted by name. Public (no auth) — it's just metadata
    for the picker. Pure + cheap: reads static maps only, never loads a model."""
    return build_languages()


# ===========================================================================
# Localized loader phrases.
#
# The document/image-translation loader (frontend/neuthek/src/neuthek-loader.jsx,
# `DocTranslateLoader`) cycles a set of whimsical scribe-themed status lines
# while a translation streams. By default those are English; this endpoint
# translates the whole set INTO the language the user is translating TO so the
# loader can narrate in that language ("Escribiendo…", "Traduciendo…").
#
# SCRIBE_PHRASES_EN below is the canonical copy of `SCRIBE_PHRASES` from the
# loader module (kept in sync by hand — ~60 short strings). Translating short UI
# phrases out of context isn't perfect, but it reads as friendly localized
# flavor and any failure simply falls back to English, so it can never break the
# loader.
# ===========================================================================

# Canonical English loader phrases — copied verbatim from
# frontend/neuthek/src/neuthek-loader.jsx's exported `SCRIBE_PHRASES`. Keep in
# sync if that list changes (this is the backend source of truth for the
# localized endpoint).
SCRIBE_PHRASES_EN: list[str] = [
    "Studying",
    "Scribing",
    "Scribbling",
    "Doodling",
    "Translating",
    "Uncovering old runes",
    "Talking with ancestors",
    "Consulting the elders",
    "Decoding hieroglyphs",
    "Whispering to the words",
    "Summoning a polyglot",
    "Polishing the vowels",
    "Sharpening the quill",
    "Dusting the dictionary",
    "Untangling the grammar",
    "Befriending a thesaurus",
    "Negotiating with idioms",
    "Bribing a polyglot",
    "Consulting the oracle",
    "Teaching the robots Spanish",
    "Dusting off the dictionary",
    "Arguing about commas",
    "Reading between the lines",
    "Waking the translator gnome",
    "Cross-checking with the stars",
    "Negotiating with verbs",
    "Translating the vibes",
    "Asking the words nicely",
    "Rolling dice on idioms",
    "Greasing the gears",
    "Conjugating in the dark",
    "Wrangling loose syllables",
    "Pestering the muses",
    "Looking up the hard words",
    "Checking the footnotes",
    "Matching the fonts",
    "Double-checking the idioms",
    "Asking a native speaker",
    "Flipping through the thesaurus",
    "Wrestling with the syntax",
    "Translating the sarcasm",
    "Preserving the puns",
    "Re-reading for tone",
    "Keeping the accents straight",
    "Politely declining false friends",
    "Untwisting the tongue-twisters",
    "Finding le mot juste",
    "Minding the gendered nouns",
    "Smoothing the phrasing",
    "Honoring the original",
    "Counting the syllables",
    "Proofreading it twice",
    "Borrowing a few loanwords",
    "Consulting the grammar police",
    "Picking the perfect synonym",
    "Aligning the paragraphs",
    "Whispering to the verbs",
]

# Codes that mean "already English" → return the base list with no model call.
_ENGLISH_CODES = {"", "en", "eng", "eng_latn", "en_us", "en_gb"}

# In-process cache: normalized lang code → translated phrase list. Each language
# is translated at most once per process; guarded by a lock so two concurrent
# first-requests for the same language don't both load/translate.
_PHRASES_CACHE: dict[str, list[str]] = {}
_PHRASES_LOCK = threading.Lock()

# Optional on-disk persistence so the (one-time) translation survives a reload
# and a cold container doesn't have to re-run the model for a language a user
# already picked. Best-effort: any IO error is ignored.
_PHRASES_CACHE_PATH = os.path.join("data", "loader_phrases_cache.json")


def _norm_lang(lang: str) -> str:
    """Normalize a requested lang code for cache keying (lower, trim, '-'→'_')."""
    return (lang or "").strip().lower().replace("-", "_")


def _load_disk_cache() -> None:
    """Populate `_PHRASES_CACHE` from the on-disk JSON once, best-effort. Only
    keeps entries whose list length matches the current English base list, so a
    stale cache from an older/shorter phrase set is ignored rather than served
    truncated. Caller holds `_PHRASES_LOCK`."""
    try:
        if not os.path.isfile(_PHRASES_CACHE_PATH):
            return
        with open(_PHRASES_CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        logger.debug("loader-phrases: disk cache unreadable; ignoring", exc_info=True)
        return
    if not isinstance(data, dict):
        return
    n = len(SCRIBE_PHRASES_EN)
    for k, v in data.items():
        if (
            isinstance(k, str)
            and isinstance(v, list)
            and len(v) == n
            and all(isinstance(s, str) for s in v)
        ):
            _PHRASES_CACHE.setdefault(_norm_lang(k), v)


def _save_disk_cache() -> None:
    """Persist the current cache to disk best-effort (atomic-ish via tmp file).
    Caller holds `_PHRASES_LOCK`. Any failure (read-only FS, missing dir) is
    swallowed — the in-process cache is the real one; disk is a nice-to-have."""
    try:
        os.makedirs(os.path.dirname(_PHRASES_CACHE_PATH) or ".", exist_ok=True)
        tmp = _PHRASES_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_PHRASES_CACHE, fh, ensure_ascii=False)
        os.replace(tmp, _PHRASES_CACHE_PATH)
    except Exception:
        logger.debug("loader-phrases: could not persist disk cache", exc_info=True)


def _translate_phrases(lang: str) -> tuple[list[str], int]:
    """Translate the English phrase list into `lang` via the engine's BATCHED
    path (`translate_engine.translate_batch`), which routes exactly like the
    document translator (`translate_text`) — MADLAD `<2xx>` primary, Opus
    `>>code<<` for the exotic gap-languages (Tongan, Samoan, Fijian, Tahitian).

    Returns `(phrases, translated_count)` where `phrases` has length
    `len(SCRIBE_PHRASES_EN)` (each entry the localized phrase, or its English
    source when that one phrase couldn't be translated) and `translated_count`
    is how many entries came back DIFFERENT from their English source (i.e.
    genuinely translated). The caller uses that count to decide whether to cache
    the result (a real translation) or retry later (a total miss → engine
    cold/busy and every phrase echoed English).

    WHY ONE BATCHED CALL (not a newline-joined block, not N single calls)
    --------------------------------------------------------------------
    The old endpoint took ~250–760 s for a first-time language — during which
    the FE loader shows the ENGLISH base list the whole time the fetch is in
    flight, so the localized phrases never appear before the translation itself
    finishes. That is the user-visible "exotic returns English" bug (reproduced
    here for Tongan AND Swahili). Two earlier shapes both failed:
      * ONE 57-line newline-joined `translate_text` call — MADLAD/Opus
        greedy-decode the ~1.1k-char block out to hundreds of tokens
        (~300 s for a single generate), and the line count can drift so phrases
        don't map 1:1 to the loader slots.
      * N single-phrase calls — correct and 1:1, but the 8-bit MADLAD
        generate() has a large ~fixed per-CALL latency, so ~57 calls ≈ 250 s
        (small newline batches were even worse once drift forced per-phrase
        fallbacks: ~760 s).
    `translate_batch` runs the phrases as PADDED BATCHES — one `generate()` per
    sub-batch (32) — so the model processes them in parallel on the GPU: a few
    generate() calls total (≈ a handful of seconds) with STRICT 1:1 alignment.
    The result is cached (in-process + disk) so every later selection of that
    language is instant.

    ROBUSTNESS. `translate_batch` raises RuntimeError only when the engine can't
    be loaded at all (cold/busy) — the caller catches it to serve English
    WITHOUT caching, so a later warm call retries. Otherwise each phrase that
    comes back empty, or as an English echo, falls back to its English source
    for that single loader line; only genuinely-translated lines bump
    `translated_count`, so a language that resolved to the English fallback
    (every phrase echoed) is reported as untranslated and not cached."""
    n = len(SCRIBE_PHRASES_EN)
    # ONE batched round-trip for the whole set (internally sub-batched). Raises
    # RuntimeError if the engine is unavailable → caller serves English uncached.
    results = _eng.translate_batch(list(SCRIBE_PHRASES_EN), target=lang, source="en")

    final: list[str] = []
    translated = 0
    for i, src in enumerate(SCRIBE_PHRASES_EN):
        cand = (results[i] if i < len(results) else "").strip()
        # A genuine translation differs from the English source (case-folded —
        # an echo of the English word is NOT a translation and stays English so
        # the loader reads cleanly).
        if cand and cand.casefold() != src.casefold():
            final.append(cand)
            translated += 1
        else:
            final.append(src)
    return final, translated


def _phrases_for_lang(lang: str) -> list[str]:
    """Return the loader phrases for `lang`, using the in-process + disk cache
    and translating at most once. English / empty short-circuits to the base
    list with NO model call. Any failure → the English base list (never raises).

    The model call is done UNDER the lock so a language translates exactly once,
    but the English fast-path and an existing-cache hit never take the model
    path (so picking a language you've used before is instant)."""
    key = _norm_lang(lang)
    if key in _ENGLISH_CODES:
        return list(SCRIBE_PHRASES_EN)

    cached = _PHRASES_CACHE.get(key)
    if cached is not None:
        return cached

    with _PHRASES_LOCK:
        # Re-check under the lock; also lazily hydrate from disk on first miss.
        cached = _PHRASES_CACHE.get(key)
        if cached is not None:
            return cached
        if not _PHRASES_CACHE:
            _load_disk_cache()
            cached = _PHRASES_CACHE.get(key)
            if cached is not None:
                return cached

        try:
            phrases, translated = _translate_phrases(key)
        except Exception:
            # Engine cold/busy/unavailable, or any other error → English base
            # list. We do NOT cache the failure so a later (warm) call can still
            # produce the localized set.
            logger.info(
                "loader-phrases: translation failed for %r; serving English",
                lang, exc_info=True,
            )
            return list(SCRIBE_PHRASES_EN)

        # Only cache a SUCCESSFUL, genuinely-localized result. If not a single
        # phrase came back different from English (every phrase echoed, or the
        # target resolved to the English fallback), treat it as a transient miss
        # — serve English now but DON'T persist it, so a later call can retry and
        # a real translation can still land in the cache. A real translation is
        # cached (in-process + disk) so the next selection is instant.
        if translated <= 0:
            logger.info(
                "loader-phrases: no phrase translated for %r (engine echoed "
                "English); serving English, not caching", lang,
            )
            return list(SCRIBE_PHRASES_EN)

        logger.info(
            "loader-phrases: cached %d/%d localized phrases for %r",
            translated, len(SCRIBE_PHRASES_EN), key,
        )
        _PHRASES_CACHE[key] = phrases
        _save_disk_cache()
        return phrases


@router.get("/loader-phrases")
def loader_phrases(
    lang: str = Query("en", max_length=40, description="Target language code"),
) -> dict:
    """Return the document-translation loader phrases translated into `lang`.

    Response: `{"lang": "<code>", "phrases": [...]}`. The phrase list is the
    localized form of the loader's English `SCRIBE_PHRASES`. Public (no auth):
    it's harmless UI flavor metadata, same as `/translate/languages`.

    Behaviour:
      * `lang` empty or English (`en`, `eng`, `eng_Latn`, …) → the English base
        list verbatim, with NO model load (the en-fast-path);
      * otherwise → the set translated into `lang` via the Apache-2.0 engine,
        CACHED so each language translates only once (in-process + best-effort
        ./data/loader_phrases_cache.json);
      * ANY failure (engine cold/busy/unavailable) → the English base list. This
        endpoint NEVER errors and never hangs the loader.

    It's meant to be called once when the user SELECTS a target language (before
    a doc translation starts), so it usually won't contend with a running doc
    translation; the cache makes repeat selections free.
    """
    return {"lang": lang, "phrases": _phrases_for_lang(lang)}
