"""Server-side text-to-speech with universal language coverage (NEW).

One route, authed:

  POST /tts/speak  → synthesize a single chunk of text to speech and return
                     the raw WAV bytes (audio/wav).
       Body: {
         "text":  "<up to 8000 chars>",
         "lang":  "<ISO-639-1 or BCP-47, e.g. 'es' or 'es-ES'>",
         "voice": "<optional explicit piper voice id, e.g. es_ES-davefx-medium>"
       }
       Response: `audio/wav` bytes for that chunk, plus an `X-TTS-Engine`
       response header naming the engine that served it
       ("piper" | "mms" | "espeak").

The frontend's "Listen" feature calls this PER CHUNK (a sentence / paragraph
at a time) and queues the returned clips for gap-free playback, so each call
must stay fast — we do NOT stream within a single call, we synthesize the
whole (short) chunk and hand back one finished WAV. The blocking synth runs
in a worker thread (`asyncio.to_thread`) so the event loop never stalls.

THREE-TIER ENGINE FLOW
----------------------
A given request language is resolved to ONE engine, best-quality first:

  1. PIPER  (neural, natural, commercial-safe, ~50 languages)
       Fast offline neural TTS (https://github.com/OHF-Voice/piper1-gpl,
       MIT). One good "medium"-quality voice per language; the voice model
       (`<id>.onnx` + `.onnx.json`) is lazily downloaded from HuggingFace
       `rhasspy/piper-voices` on first use and cached on disk + in memory.

  2. MMS-TTS  (neural, Meta Massively Multilingual Speech, ~1,100 languages)
       Meta's VITS-based `facebook/mms-tts-<iso639_3>` models (CC-BY-NC). When
       a language has NO Piper voice we map the request code to its ISO-639-3
       (e.g. 'sm'→'smo', 'fj'→'fij') and lazy-load that model via transformers
       `VitsModel` + `AutoTokenizer`, synthesizing on CPU. Each model is
       ~100-150 MB, cached on disk by HF + in memory per language. This is a
       NEURAL voice for the long tail Piper lacks (Samoan, Fijian, Swahili,
       Hausa, Yoruba, Cebuano, Quechua, …) — far better than the robotic
       eSpeak below. If MMS has no model for the language (the repo 404s — MMS
       does not cover EVERY ISO-639-3, e.g. Tongan/Tahitian/Zulu/Hawaiian are
       absent), we fall through to eSpeak.

  3. eSpeak-NG  (formant/robotic, ~100+ languages, GPL-3.0)
       Open-source universal fallback. When a language has NO Piper AND no MMS
       voice but eSpeak supports it, we shell out to
       `espeak-ng -v <code> -w <tmp.wav>` and return those bytes. Robotic but
       intelligible, and it covers some tail languages MMS lacks (Welsh, Zulu,
       Xhosa, Maori, …). eSpeak is a system package installed via the
       Dockerfile apt line.

  4. NONE → HTTP 501 `{"detail": "no_voice_for_language"}` so the frontend
       falls back to the browser's Web Speech API. Only the genuinely exotic
       tail with NO neural NOR formant voice (e.g. Tongan, Tahitian) lands here.

All engines run on CPU only — Piper via onnxruntime's CPU provider, MMS via
torch's CPU device (VITS is small; real-time on CPU for chunk-sized text),
eSpeak as a plain subprocess — so NO GPU memory is consumed (the GPU is
reserved for NLLB-200 + Florence-2 and is tight). There is no OOM risk here.

VOICE MANAGEMENT (Piper)
------------------------
`_piper_family_voice()` returns a `family → voice-id` map (e.g. "es" →
"es_ES-davefx-medium"). It is built ONCE, lazily, from the live
`rhasspy/piper-voices` `voices.json` catalog (downloaded via
`huggingface_hub`, cached on disk by HF), choosing the best-quality voice per
language family (medium > high > low > x_low) with a few well-known voices
pinned for recognizability. If the catalog can't be fetched (offline) we fall
back to a baked-in `_PIPER_FAMILY_VOICE_STATIC` map so the feature still works.
The incoming `lang` is normalized to its BASE subtag (before any '-' / '_'),
so 'es-ES', 'es_419', 'ES' all resolve to the same Spanish voice. A caller may
override the choice entirely with an explicit `voice` in the request body.

Each selected voice model is LAZILY downloaded from HuggingFace on first use
(via the bundled `piper.download_voices.download_voice`, idempotent — it skips
files already on disk) and CACHED under `./data/models/piper`
(= `/app/data/models/piper` in the container). Loaded `PiperVoice` objects are
cached per voice-id in a module global so we never reload weights per request.

IMPORT SAFETY
-------------
Mirrors `backend/api/ocr.py`: nothing heavy is imported at module top.
`piper` / `onnxruntime` / `wave` and `torch` / `transformers` (for MMS) are all
imported INSIDE the worker functions, and the `voices.json` fetch + the
`espeak-ng --voices` probe are both done LAZILY on first request (never at
import). So importing this module (which `app.py` does at boot, under
`uvicorn --reload`) stays light and can never crash the API even on a build
without the [ml] extras / without espeak-ng installed.

DEPENDENCIES (MMS tier)
-----------------------
The MMS tier needs `transformers` (already required for the translate/summarize
features), `torch` (already required), and `numpy` (already required) — NO new
third-party dependency is introduced: the WAV is assembled with the `wave`
stdlib module + numpy, deliberately avoiding a `scipy` requirement. The only
runtime cost is the first-use download of each `facebook/mms-tts-<iso3>` model
(~100-150 MB) into the HF cache (`$HF_HOME`, already a writable, persisted dir
in the image). Nothing extra needs baking into the image beyond what the
translate/summarize stack already installs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import threading
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.auth.users import current_active_user
from backend.models import User

logger = logging.getLogger(__name__)

# Own prefix/tag — wired separately in app.py (see the hand-off note at the
# bottom of this module for the exact two lines).
router = APIRouter(prefix="/tts", tags=["tts"])

# Upper bound on a single chunk. The FE splits long text and calls per chunk;
# 8000 chars is a generous paragraph ceiling that still synthesizes quickly.
# eSpeak gets a tighter cap (it's a subprocess per call, and robotic speech
# past a paragraph is rarely wanted) — see _ESPEAK_MAX_TEXT_CHARS.
_MAX_TEXT_CHARS = 8000
_ESPEAK_MAX_TEXT_CHARS = 4000


# ---------------------------------------------------------------------------
# TIER 1 — Piper: language-family → voice-id map.
#
# Static fallback used when the live `voices.json` catalog can't be fetched.
# Generated FROM that catalog (the values were each verified present): for
# every Piper language family we pick the best-quality voice (medium > high >
# low > x_low), with a handful of well-known, natural voices PINNED for
# recognizability (en→lessac, fr→siwis, de→thorsten, …). `_piper_family_voice`
# prefers a freshly-built map from the live catalog and only falls back here.
# ---------------------------------------------------------------------------
_PIPER_FAMILY_VOICE_STATIC: dict[str, str] = {
    "ar": "ar_JO-kareem-medium",
    "bg": "bg_BG-dimitar-medium",
    "ca": "ca_ES-upc_ona-medium",
    "cs": "cs_CZ-jirka-medium",
    "cy": "cy_GB-gwryw_gogleddol-medium",
    "da": "da_DK-talesyntese-medium",
    "de": "de_DE-thorsten-medium",
    "el": "el_GR-rapunzelina-medium",
    "en": "en_US-lessac-medium",
    "es": "es_ES-davefx-medium",
    "eu": "eu_ES-antton-medium",
    "fa": "fa_IR-amir-medium",
    "fi": "fi_FI-harri-medium",
    "fr": "fr_FR-siwis-medium",
    "hi": "hi_IN-pratham-medium",
    "hu": "hu_HU-anna-medium",
    "id": "id_ID-news_tts-medium",
    "is": "is_IS-bui-medium",
    "it": "it_IT-paola-medium",
    "ka": "ka_GE-natia-medium",
    "kk": "kk_KZ-issai-high",
    "ku": "ku_TR-berfin_renas-medium",
    "lb": "lb_LU-marylux-medium",
    "lv": "lv_LV-aivars-medium",
    "ml": "ml_IN-arjun-medium",
    "ne": "ne_NP-chitwan-medium",
    "nl": "nl_NL-mls-medium",
    "no": "no_NO-nvcc-medium",
    "pl": "pl_PL-darkman-medium",
    "pt": "pt_BR-faber-medium",
    "ro": "ro_RO-mihai-medium",
    "ru": "ru_RU-dmitri-medium",
    "sk": "sk_SK-lili-medium",
    "sl": "sl_SI-artur-medium",
    "sq": "sq_AL-edon-medium",
    "sr": "sr_RS-serbski_institut-medium",
    "sv": "sv_SE-alma-medium",
    "sw": "sw_CD-lanfrica-medium",
    "te": "te_IN-maya-medium",
    "tr": "tr_TR-dfki-medium",
    "uk": "uk_UA-ukrainian_tts-medium",
    "ur": "ur_PK-fasih-medium",
    "vi": "vi_VN-vais1000-medium",
    "zh": "zh_CN-huayan-medium",
}

# Well-known, natural-sounding voices PINNED as the default for their family
# when present in the live catalog (otherwise the best-quality auto-pick wins).
# These are the voices the app shipped originally + a couple of clearly nicer
# choices; pinning keeps the "one consistent voice per language" guarantee
# stable even if the catalog adds new voices for these families later.
_PIPER_PREFERRED: dict[str, str] = {
    "en": "en_US-lessac-medium",
    "es": "es_ES-davefx-medium",
    "fr": "fr_FR-siwis-medium",
    "de": "de_DE-thorsten-medium",
    "it": "it_IT-paola-medium",
    "pt": "pt_BR-faber-medium",
    "nl": "nl_NL-mls-medium",
    "pl": "pl_PL-darkman-medium",
    "ru": "ru_RU-dmitri-medium",
    "zh": "zh_CN-huayan-medium",
    "tr": "tr_TR-dfki-medium",
    "ar": "ar_JO-kareem-medium",
    "cy": "cy_GB-gwryw_gogleddol-medium",
}

# Quality ranking for auto-picking one voice per family (lower = better).
_PIPER_QUALITY_RANK = {"medium": 0, "high": 1, "low": 2, "x_low": 3}

# Built-once cache of the family→voice map (from the live catalog, or static).
_piper_map_cache: Optional[dict[str, str]] = None
_piper_map_lock = threading.Lock()


def _base_lang(lang: str) -> str:
    """Normalize a language tag to its lowercase base subtag.

    'es-ES' → 'es', 'pt_BR' → 'pt', 'ZH-Hans' → 'zh', '' → ''. We split on
    BOTH '-' and '_' so locale codes in either convention collapse to the
    same key the maps are indexed by.
    """
    if not lang:
        return ""
    return lang.strip().lower().replace("_", "-").split("-", 1)[0]


def _build_piper_map_from_catalog() -> Optional[dict[str, str]]:
    """Build a `family → best voice-id` map from the live rhasspy/piper-voices
    `voices.json` catalog, or return None if it can't be fetched/parsed.

    For each language family we keep the best-quality voice (medium > high >
    low > x_low; ties broken by voice-id for determinism). A family listed in
    `_PIPER_PREFERRED` uses that pinned voice when it actually exists in the
    catalog. Network/parse failures return None so the caller falls back to the
    baked-in static map — this function never raises into the request path.
    """
    try:
        import json

        from huggingface_hub import hf_hub_download

        path = hf_hub_download("rhasspy/piper-voices", "voices.json")
        with open(path, "r", encoding="utf-8") as fh:
            catalog = json.load(fh)
    except Exception:
        logger.info(
            "tts: could not fetch piper voices.json; using static voice map",
            exc_info=True,
        )
        return None

    keys = set(catalog.keys())
    best: dict[str, tuple[int, str]] = {}
    for key, meta in catalog.items():
        try:
            family = meta["language"]["family"]
        except (KeyError, TypeError):
            continue
        rank = _PIPER_QUALITY_RANK.get(meta.get("quality", "low"), 9)
        cur = best.get(family)
        if cur is None or rank < cur[0] or (rank == cur[0] and key < cur[1]):
            best[family] = (rank, key)

    out: dict[str, str] = {}
    for family, (_rank, key) in best.items():
        pinned = _PIPER_PREFERRED.get(family)
        out[family] = pinned if (pinned and pinned in keys) else key
    if not out:
        return None
    logger.info("tts: built piper voice map for %d languages from catalog",
                len(out))
    return out


def _piper_family_voice() -> dict[str, str]:
    """Return the (cached) `family → voice-id` map, building it once from the
    live catalog and falling back to the baked-in static map. Thread-safe."""
    global _piper_map_cache
    if _piper_map_cache is not None:
        return _piper_map_cache
    with _piper_map_lock:
        if _piper_map_cache is not None:
            return _piper_map_cache
        built = _build_piper_map_from_catalog()
        _piper_map_cache = built if built is not None else dict(
            _PIPER_FAMILY_VOICE_STATIC
        )
        return _piper_map_cache


def _piper_voice_id_for(lang: str, explicit: Optional[str]) -> Optional[str]:
    """Resolve the Piper voice id to use, or None if Piper has no voice for the
    language.

    An explicit `voice` (e.g. 'es_ES-davefx-medium') wins verbatim so a caller
    can pick any catalog voice. Otherwise we look up the base language family
    in the map.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    return _piper_family_voice().get(_base_lang(lang))


# ---------------------------------------------------------------------------
# On-disk Piper voice cache.
#
# Voices live under ./data/models/piper (= /app/data/models/piper in the
# container). Settable via PIPER_VOICE_DIR for flexibility. We create the dir
# on first use. NOTE: in the dev compose this path is in the container's
# writable layer (not a bind mount / named volume), so the downloaded voices
# persist for the life of the CONTAINER but not across a `compose down` /
# image rebuild — they simply re-download on first use after that.
# ---------------------------------------------------------------------------
def _voice_dir() -> str:
    path = os.environ.get("PIPER_VOICE_DIR") or os.path.join(
        os.getcwd(), "data", "models", "piper"
    )
    os.makedirs(path, exist_ok=True)
    return path


# Loaded PiperVoice objects, keyed by voice id. A lock serializes the
# (download + load) of a given voice so two concurrent first-requests for the
# same language don't race on the half-written .onnx file or load the weights
# twice. Reads of an already-cached voice don't need the lock.
_VOICE_CACHE: dict[str, object] = {}
_VOICE_LOCK = threading.Lock()


def _voice_paths(voice_dir: str, voice_id: str) -> tuple[str, str]:
    """(onnx_path, onnx_json_path) for a voice id under voice_dir. Matches the
    file names `piper.download_voices.download_voice` writes."""
    onnx = os.path.join(voice_dir, f"{voice_id}.onnx")
    return onnx, onnx + ".json"


def _ensure_voice_files(voice_dir: str, voice_id: str) -> tuple[str, str]:
    """Make sure `<voice_id>.onnx` + `.onnx.json` exist under voice_dir,
    downloading them from HuggingFace (rhasspy/piper-voices) on first use.

    Uses the bundled `piper.download_voices.download_voice`, which is
    idempotent: it only fetches a file when it's missing/empty, so a cached
    voice incurs no network. Raises on a bad voice id / download failure;
    the caller maps that to the eSpeak fallback (treat as "no usable Piper
    voice").
    """
    onnx_path, json_path = _voice_paths(voice_dir, voice_id)
    if os.path.exists(onnx_path) and os.path.exists(json_path):
        return onnx_path, json_path

    from pathlib import Path

    from piper.download_voices import download_voice

    # download_voice writes <voice_id>.onnx and <voice_id>.onnx.json directly
    # into download_dir and skips any file already present.
    download_voice(voice_id, Path(voice_dir))

    if not (os.path.exists(onnx_path) and os.path.exists(json_path)):
        raise RuntimeError(
            f"voice files missing after download for '{voice_id}'"
        )
    return onnx_path, json_path


def _get_voice(voice_id: str):
    """Return a loaded, cached `PiperVoice` for `voice_id`, downloading the
    model on first use. Thread-safe; runs ON the worker thread (callers wrap
    the whole synth in asyncio.to_thread). CPU only (use_cuda=False).

    Raises on any failure (unknown id / download / load problem); the route
    converts that into the eSpeak fallback rather than a 500, because a
    missing/broken voice is functionally "no Piper voice for this language".
    """
    cached = _VOICE_CACHE.get(voice_id)
    if cached is not None:
        return cached

    with _VOICE_LOCK:
        # Re-check inside the lock — another thread may have just loaded it.
        cached = _VOICE_CACHE.get(voice_id)
        if cached is not None:
            return cached

        from piper import PiperVoice

        voice_dir = _voice_dir()
        onnx_path, json_path = _ensure_voice_files(voice_dir, voice_id)
        # use_cuda=False → onnxruntime CPU provider. Piper is real-time on CPU
        # for chunk-sized text and we keep the GPU free for NLLB/Florence.
        voice = PiperVoice.load(
            onnx_path, config_path=json_path, use_cuda=False
        )
        _VOICE_CACHE[voice_id] = voice
        return voice


def _synthesize_piper_wav(text: str, voice_id: str) -> bytes:
    """Blocking synth: text → WAV bytes for a Piper `voice_id`. Runs in a
    worker thread. Writes through piper's `synthesize_wav`, which emits a
    proper RIFF/WAVE container (header + PCM) via the stdlib `wave` module, so
    the returned bytes are a complete, playable .wav. Raises on any failure."""
    import io
    import wave

    voice = _get_voice(voice_id)

    buf = io.BytesIO()
    # `wave.open` on a BytesIO gives a Wave_write; synthesize_wav sets the
    # format (rate / width / channels) from the voice and writes all frames,
    # so we don't pre-set the params ourselves (set_wav_format defaults True).
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# TIER 2 — MMS-TTS: Meta Massively Multilingual Speech (neural, ~1,100 langs).
#
# Meta ships one VITS model per language at `facebook/mms-tts-<iso639_3>`
# (CC-BY-NC). For a request whose language has NO Piper voice, we map the
# request code (2-letter ISO-639-1 / BCP-47 base, or an already-3-letter code
# from the catalog) to the ISO-639-3 MMS uses, then lazy-load + cache that
# model and synthesize on CPU. MMS does NOT cover every ISO-639-3 (e.g. Tongan
# 'ton', Tahitian 'tah', Zulu 'zul', Hawaiian 'haw' have no MMS model), so a
# 404/unknown-repo is treated as "no MMS voice" and the caller falls through to
# eSpeak — never a 500.
#
# Why CPU: the GPU is tight (~6.4/8 GB, reserved for NLLB-200 + Florence-2).
# VITS is small and real-time on CPU for chunk-sized text, so MMS adds ZERO GPU
# memory. We force the model onto CPU and run inference under `torch.no_grad()`.
# The WAV is assembled with the `wave` stdlib + numpy (no scipy dependency).
# ---------------------------------------------------------------------------

# A tighter text cap for MMS than Piper: VITS allocates activations over the
# whole token sequence in one forward pass, so an over-long chunk would spike
# CPU RAM + latency. A paragraph ceiling keeps a single synth fast and bounded.
_MMS_MAX_TEXT_CHARS = 2000

# Request base-subtag (ISO-639-1 / BCP-47 base) → ISO-639-3 code MMS publishes.
# Most of the app's exotic catalog codes are ALREADY ISO-639-3 (e.g. 'ace',
# 'ceb', 'sah', 'quy') and pass straight through `_mms_iso3_for` when present in
# the live MMS catalog; this table bridges the 2-letter ISO codes (and the few
# macro/variant mismatches) to the exact code MMS uses. Entries whose model
# happens not to exist are harmless — the loader 404s and we fall to eSpeak.
_MMS_ISO3: dict[str, str] = {
    # --- ISO-639-1 → ISO-639-3 (the common 2-letter requests) --------------
    "af": "afr", "ak": "aka", "am": "amh", "ar": "ara", "as": "asm",
    "av": "ava", "ay": "ayr", "az": "azb", "ba": "bak", "be": "bel",
    "bg": "bul", "bi": "bis", "bm": "bam", "bn": "ben", "bo": "bod",
    "bs": "bos", "ca": "cat", "ce": "che", "cv": "chv",
    "cs": "ces", "cy": "cym", "da": "dan", "de": "deu", "dz": "dzo",
    "ee": "ewe", "el": "ell", "en": "eng", "eo": "epo", "es": "spa",
    "et": "est", "eu": "eus", "fa": "fas", "ff": "ful", "fi": "fin",
    "fj": "fij", "fo": "fao", "fr": "fra", "ga": "gle", "gd": "gla",
    "gl": "glg", "gn": "grn", "gu": "guj", "ha": "hau", "he": "heb",
    "hi": "hin", "hr": "hrv", "ht": "hat", "hu": "hun", "hy": "hyw",
    "id": "ind",
    "ig": "ibo", "is": "isl", "it": "ita", "ja": "jpn", "jv": "jav",
    "ka": "kat", "kk": "kaz", "km": "khm", "kn": "kan", "ko": "kor",
    "ku": "kmr", "kv": "kpv", "kw": "cor", "ky": "kir", "la": "lat",
    "lb": "ltz",
    "lg": "lug", "li": "lim", "ln": "lin", "lo": "lao", "lt": "lit",
    "lu": "lub", "lv": "lav", "mg": "mlg", "mh": "mah", "mi": "mri",
    "mk": "mkd", "ml": "mal", "mn": "mon", "mr": "mar", "ms": "zlm",
    "mt": "mlt", "my": "mya", "ne": "npi", "nl": "nld", "no": "nob",
    "nr": "nbl", "ny": "nya", "oc": "oci", "oj": "oji", "om": "orm",
    "or": "ory", "os": "oss", "pa": "pan", "pl": "pol", "ps": "pbt",
    "pt": "por", "qu": "quy", "rm": "roh", "rn": "run", "ro": "ron",
    "ru": "rus", "rw": "kin", "sa": "san", "sc": "srd", "sd": "snd",
    "se": "sme", "sg": "sag", "si": "sin", "sk": "slk", "sl": "slv",
    "sm": "smo", "sn": "sna", "so": "som", "sq": "sqi", "sr": "srp",
    "ss": "ssw", "st": "sot", "su": "sun", "sv": "swe", "sw": "swh",
    "ta": "tam", "te": "tel", "tg": "tgk", "th": "tha", "ti": "tir",
    "tk": "tuk", "tl": "tgl", "tn": "tsn", "to": "ton", "tr": "tur",
    "ts": "tso", "tt": "tat", "ty": "tah", "ug": "uig", "uk": "ukr",
    "ur": "urd", "uz": "uzb", "ve": "ven", "vi": "vie", "wa": "wln",
    "wo": "wol", "xh": "xho", "yi": "yid", "yo": "yor", "zu": "zul",
    # --- catalog codes whose base differs from the MMS ISO-639-3 -----------
    # (most 3-letter catalog codes pass straight through; these are the few
    # the catalog spells differently from MMS's chosen code.)
    "chm": "mhr",   # Mari (Meadow) — MMS uses the Meadow-Mari code 'mhr'
    "din": "dik",   # Dinka → Southwestern Dinka
    "doi": "dgo",   # Dogri → MMS 'dgo'
    "dv": "div",    # Dhivehi
    "gom": "gom",   # Goan Konkani
    "ckb": "ckb",   # Central Kurdish (Sorani)
}

# Loaded (tokenizer, model) tuples keyed by ISO-639-3, plus a per-language
# "known-missing" marker so a 404 is remembered and we don't re-hit HF each
# request for a language MMS simply doesn't have. A lock serializes the
# download+load of a given language so concurrent first-requests don't race.
_MMS_CACHE: dict[str, object] = {}
_MMS_MISSING: set[str] = set()
_MMS_LOCK = threading.Lock()


def _mms_iso3_for(lang: str) -> Optional[str]:
    """Resolve a request language to the ISO-639-3 code MMS publishes, or None.

    Order: explicit map entry for the base subtag → the base subtag itself when
    it's already a 3-letter code (the catalog's exotic codes are ISO-639-3). We
    do NOT verify the model exists here (that needs a network call); a code that
    has no MMS model simply 404s at load time and falls through to eSpeak.
    """
    base = _base_lang(lang)
    if not base:
        return None
    mapped = _MMS_ISO3.get(base)
    if mapped:
        return mapped
    # Catalog exotic codes (e.g. 'ace', 'ceb', 'sah') are already ISO-639-3.
    if len(base) == 3 and base.isalpha():
        return base
    return None


def _get_mms_model(iso3: str):
    """Return a cached `(tokenizer, model)` for the MMS language `iso3`,
    downloading + loading `facebook/mms-tts-<iso3>` on CPU on first use.

    Thread-safe; runs ON the worker thread (callers wrap synth in
    asyncio.to_thread). Raises if the model can't be fetched/loaded (unknown
    language → HF 404, or a load error); the route maps that to the eSpeak
    fallback rather than a 500, because a missing MMS model is functionally
    "no MMS voice for this language".
    """
    cached = _MMS_CACHE.get(iso3)
    if cached is not None:
        return cached

    with _MMS_LOCK:
        cached = _MMS_CACHE.get(iso3)
        if cached is not None:
            return cached

        import torch
        from transformers import AutoTokenizer, VitsModel

        repo = f"facebook/mms-tts-{iso3}"
        # token=False → never send stale creds; these repos are public. A
        # nonexistent language raises here (caught upstream → eSpeak).
        tokenizer = AutoTokenizer.from_pretrained(repo, token=False)
        model = VitsModel.from_pretrained(repo, token=False)
        model = model.to("cpu")
        model.eval()
        # torch can default to many intra-op threads; cap to keep one synth
        # from starving the event-loop's executor / other CPU work.
        try:
            torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        except Exception:
            pass
        loaded = (tokenizer, model)
        _MMS_CACHE[iso3] = loaded
        return loaded


def _synthesize_mms_wav(text: str, iso3: str) -> bytes:
    """Blocking synth: text → WAV bytes via MMS `facebook/mms-tts-<iso3>` on
    CPU. Runs in a worker thread. Builds a mono 16-bit PCM WAV at the model's
    `config.sampling_rate` using the `wave` stdlib + numpy (no scipy). Raises on
    any failure (unknown language / load / inference) → caller falls to eSpeak.
    """
    import io
    import wave

    import numpy as np
    import torch

    tokenizer, model = _get_mms_model(iso3)

    clipped = text[:_MMS_MAX_TEXT_CHARS]
    inputs = tokenizer(clipped, return_tensors="pt")
    with torch.no_grad():
        waveform = model(**inputs).waveform  # (1, num_samples), float32 [-1,1]

    audio = waveform.squeeze().detach().cpu().numpy().astype(np.float32)
    # Guard against NaNs/Infs, clamp to [-1, 1], scale to signed 16-bit PCM.
    audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype("<i2")

    sample_rate = int(getattr(model.config, "sampling_rate", 16000))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)          # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm16.tobytes())
    data = buf.getvalue()
    if not data:
        raise RuntimeError("MMS produced an empty WAV")
    return data


# ---------------------------------------------------------------------------
# TIER 3 — eSpeak-NG universal fallback (formant/robotic, ~100+ languages).
#
# `espeak-ng` is a system binary installed via the Dockerfile apt line. We
# probe `espeak-ng --voices` ONCE (lazily) to learn the exact set of voice
# codes this build supports, then map a request language to a supported voice
# code and shell out to synthesize a temp WAV. A few aliases bridge a base
# subtag that eSpeak names differently (Mandarin is "cmn", Norwegian is "nb").
# ---------------------------------------------------------------------------

# Map a request base-subtag to the eSpeak voice code when they differ. Only
# 1:1, linguistically-sound bridges — we do NOT alias e.g. Tagalog→English
# (that would mispronounce); such languages stay browser-only instead.
_ESPEAK_ALIAS: dict[str, str] = {
    "zh": "cmn",   # Chinese (Mandarin) — eSpeak calls it 'cmn'
    "no": "nb",    # Norwegian → Norwegian Bokmål
    "nn": "nb",    # Norwegian Nynorsk → Bokmål voice (closest eSpeak has)
    "sh": "hr",    # Serbo-Croatian → Croatian
}

# Built-once set of supported eSpeak voice codes (lowercased), and the set of
# their base subtags, learned from `espeak-ng --voices`.
_espeak_full: Optional[set[str]] = None   # full codes, e.g. {"en-gb","cmn"}
_espeak_base: Optional[set[str]] = None    # base subtags, e.g. {"en","cmn"}
_espeak_lock = threading.Lock()


def _espeak_bin() -> Optional[str]:
    """Absolute path to the `espeak-ng` binary, or None if not installed."""
    return shutil.which("espeak-ng")


def _load_espeak_voices() -> tuple[set[str], set[str]]:
    """Parse `espeak-ng --voices` once into (full-codes, base-subtags).

    The output is a fixed-width table whose 2nd column is the voice code
    (e.g. `en-gb`, `cmn`, `ca-va`) and whose 5th column is `<group>/<file>`
    (e.g. `gmw/en`, `sit/cmn`); we collect codes from BOTH so a request can
    match either the full code or the file's bare language code. Returns two
    empty sets if espeak-ng is missing or the probe fails (→ no eSpeak
    coverage; languages fall through to browser TTS).
    """
    binpath = _espeak_bin()
    if not binpath:
        return set(), set()
    try:
        proc = subprocess.run(
            [binpath, "--voices"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        logger.info("tts: `espeak-ng --voices` failed", exc_info=True)
        return set(), set()

    full: set[str] = set()
    base: set[str] = set()
    for line in proc.stdout.splitlines()[1:]:  # skip the header row
        parts = line.split()
        if len(parts) < 5:
            continue
        code = parts[1].lower()              # e.g. "en-gb", "cmn"
        file_code = parts[4].split("/")[-1].lower()  # e.g. "en", "cmn"
        full.add(code)
        full.add(file_code)
        base.add(code.split("-", 1)[0])
        base.add(file_code.split("-", 1)[0])
    return full, base


def _espeak_voices() -> tuple[set[str], set[str]]:
    """(full-codes, base-subtags) eSpeak supports, built once and cached."""
    global _espeak_full, _espeak_base
    if _espeak_full is not None and _espeak_base is not None:
        return _espeak_full, _espeak_base
    with _espeak_lock:
        if _espeak_full is None or _espeak_base is None:
            _espeak_full, _espeak_base = _load_espeak_voices()
            logger.info("tts: eSpeak-NG supports %d voice codes",
                        len(_espeak_full))
        return _espeak_full, _espeak_base


def _espeak_code_for(lang: str) -> Optional[str]:
    """Resolve a request language to an eSpeak voice code, or None if eSpeak
    has no voice for it. Tries: explicit alias, then the base subtag verbatim
    (which eSpeak accepts for the vast majority of its languages)."""
    base = _base_lang(lang)
    if not base:
        return None
    full, bases = _espeak_voices()
    if not full:
        return None
    alias = _ESPEAK_ALIAS.get(base)
    if alias and (alias in full or alias in bases):
        return alias
    if base in full or base in bases:
        return base
    return None


def _synthesize_espeak_wav(text: str, espeak_code: str) -> bytes:
    """Blocking synth: text → WAV bytes via `espeak-ng -v <code> -w <tmp.wav>`.
    Runs in a worker thread. Writes to a private temp file, reads the bytes
    back, and always removes the temp file. Robotic but intelligible; covers
    the long-tail languages Piper lacks. Raises on any failure (caller maps to
    501 → browser TTS)."""
    import tempfile

    binpath = _espeak_bin()
    if not binpath:
        raise RuntimeError("espeak-ng not installed")

    clipped = text[:_ESPEAK_MAX_TEXT_CHARS]

    fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="tts_espeak_")
    os.close(fd)  # espeak writes the file itself; we only need the path.
    try:
        proc = subprocess.run(
            [binpath, "-v", espeak_code, "-w", tmp_path, clipped],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"espeak-ng exited {proc.returncode}: {proc.stderr.strip()}"
            )
        with open(tmp_path, "rb") as fh:
            data = fh.read()
        if not data:
            raise RuntimeError("espeak-ng produced an empty WAV")
        return data
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Engine resolution + blocking synth dispatch.
# ---------------------------------------------------------------------------
def _resolve_engine(
    lang: str, explicit_voice: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """Pick the engine + voice for a request: Piper → MMS → eSpeak.

    Returns ("piper", voice_id) | ("mms", iso3) | ("espeak", espeak_code) |
    (None, None). An explicit `voice` always forces Piper with that voice id.

    NOTE on MMS: we can only know a language is in the request map here, not
    that Meta actually publishes a model for it (that needs a network call). So
    "mms" may be chosen for a language MMS lacks; the route catches the load
    404 and falls through to eSpeak. A language already known-missing (cached
    after a prior 404) is skipped straight to eSpeak so we don't re-hit HF.
    """
    piper_id = _piper_voice_id_for(lang, explicit_voice)
    if piper_id:
        return "piper", piper_id
    iso3 = _mms_iso3_for(lang)
    if iso3 and iso3 not in _MMS_MISSING:
        return "mms", iso3
    espeak_code = _espeak_code_for(lang)
    if espeak_code:
        return "espeak", espeak_code
    return None, None


def _synthesize(engine: str, voice: str, text: str) -> bytes:
    """Dispatch to the chosen engine's blocking synth (runs in a worker
    thread)."""
    if engine == "piper":
        return _synthesize_piper_wav(text, voice)
    if engine == "mms":
        return _synthesize_mms_wav(text, voice)
    if engine == "espeak":
        return _synthesize_espeak_wav(text, voice)
    raise RuntimeError(f"unknown TTS engine '{engine}'")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)
    # ISO-639-1 or BCP-47. Normalized to its base subtag server-side.
    lang: str = Field(default="en", max_length=35)
    # Optional explicit Piper voice id (e.g. "es_ES-davefx-medium"). Overrides
    # the language map when set (forces the Piper engine).
    voice: Optional[str] = Field(default=None, max_length=80)


@router.post("/speak")
async def tts_speak(
    body: SpeakRequest,
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    """Synthesize one chunk of text to speech and return audio/wav.

    Engine flow: Piper (neural) → MMS (neural) → eSpeak-NG (robotic) → 501.
    Picks a Piper voice from `voice` (explicit) or the language map; if Piper
    has no voice for the language, tries MMS (`facebook/mms-tts-<iso3>`); if MMS
    has no model either, tries eSpeak-NG; if none covers it, returns 501
    `{"detail":"no_voice_for_language"}` so the FE falls back to browser TTS.
    The chosen engine is reported in the `X-TTS-Engine` response header
    ("piper" | "mms" | "espeak").

    Robustness: each tier that's CHOSEN but fails at synth time (a Piper voice
    that won't download, an MMS model that 404s, …) transparently falls through
    to the NEXT tier in the chain, so a broken/absent higher tier never denies
    the user a working lower one. Heavy synth runs in a thread so the event loop
    stays free; the first call for a given voice/model lazily downloads + caches
    it (subsequent calls reuse the in-memory object).
    """
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to speak.")

    # Build the ordered tier chain for this request: the resolved primary,
    # then every still-viable lower tier as a fallback. Trying them in order
    # means an absent/broken higher tier (e.g. MMS 404 for Tongan) degrades to
    # the next working one rather than erroring.
    engine, voice = _resolve_engine(body.lang, body.voice)
    if not engine or not voice:
        # No Piper AND no MMS AND no eSpeak voice → let the FE use the browser.
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "no_voice_for_language"
        )

    chain: list[tuple[str, str]] = [(engine, voice)]
    # Append the lower tiers not already chosen, in quality order, so a failure
    # at the chosen tier can fall through. We only add a tier that ISN'T already
    # the primary (so an eSpeak-primary request doesn't list eSpeak twice).
    # (Piper is only ever the primary — there's no "fall back UP to Piper".)
    if engine != "mms":
        iso3 = _mms_iso3_for(body.lang)
        if iso3 and iso3 not in _MMS_MISSING:
            chain.append(("mms", iso3))
    if engine != "espeak":
        espeak_code = _espeak_code_for(body.lang)
        if espeak_code:
            chain.append(("espeak", espeak_code))

    wav_bytes: Optional[bytes] = None
    used_engine: Optional[str] = None
    for idx, (eng, vc) in enumerate(chain):
        try:
            wav_bytes = await asyncio.to_thread(_synthesize, eng, vc, text)
            used_engine = eng
            if idx > 0:
                logger.info(
                    "tts: %s failed; served %s (%s) for lang=%s",
                    chain[0][0], eng, vc, body.lang,
                )
            break
        except Exception:
            # Remember a missing MMS model so future requests skip straight to
            # eSpeak instead of re-hitting HuggingFace for a 404 every time.
            if eng == "mms":
                _MMS_MISSING.add(vc)
            logger.info(
                "tts: synth failed for engine=%s voice=%s lang=%s (tier %d/%d)",
                eng, vc, body.lang, idx + 1, len(chain), exc_info=True,
            )
            continue

    if not wav_bytes or not used_engine:
        # Every tier failed → 501 so the FE falls back to browser TTS instead of
        # erroring out. (An explicit, mistyped `voice` lands here too, which is
        # the right behaviour: fall back rather than 500.)
        logger.warning(
            "tts: all engines failed for lang=%s; returning 501", body.lang,
        )
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "no_voice_for_language"
        )

    engine = used_engine

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"X-TTS-Engine": engine},
    )
