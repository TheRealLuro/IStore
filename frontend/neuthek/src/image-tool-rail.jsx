// Left-side TOOL-BUBBLE RAIL for the big image preview (lightbox).
//
// The user's pattern: "the big preview tools (comments, translator, etc)
// are all minimized into their bubbles on the left side of the file."
//
// This renders a quiet vertical column of small circular bubbles pinned to
// the LEFT edge of the lightbox. Each bubble is a tool; clicking one
// EXPANDS that tool's panel (a slide-out beside the file, reusing the
// comment-panel geometry) and marks the bubble active. Clicking the active
// bubble again — or the file / Esc, handled by the parent — MINIMIZES back
// to bubbles. Default state: everything minimized, the file is the focus.
// One tool open at a time (single `active` string in the parent).
//
// Tools retrofitted into the rail:
//   - comments : delegates to the EXISTING CommentPanel (the parent owns
//                that component; the rail just drives its expanded state so
//                comments are one-at-a-time with the others). The lone
//                floating comment bubble becomes the first rail bubble.
//   - info     : file details (name, AI topic, dimensions, size, location,
//                scene) — the same facts the right-hand DETAILS aside shows,
//                surfaced in the lightbox so you don't have to leave it.
//   - text     : NEW — OCR + translate (see TextToolPanel below).
//
// Hooks are unconditional (declared before any early return). The OCR
// overlay (highlight boxes on the rendered image) lives here too and is
// returned via the `overlay` render path the parent drops into the image
// stage.

import React, { useState, useEffect, useRef, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import {
  ocrImage,
  translateImageStream,
  translateDocStream,
  translatePdf,
  renderTranslatedPdf,
  fetchDocumentText,
  fetchTranslateLanguages,
  fetchLoaderPhrases,
  speak as ttsSpeak,
  NoNeuralVoiceError,
  neuralTtsSupportsLang,
} from "@/api/ocr";
import { DocTranslateLoader, SCRIBE_PHRASES } from "./neuthek-loader.jsx";

// ── Supported-language catalogue (full list from GET /translate/languages) ───
// The picker now offers the ENGINE'S FULL target set — ~100+ languages — rather
// than the short hardcoded list. We fetch [{code,name}] once (module-cached in
// the api client) and share it across every picker instance via this tiny hook,
// which keeps its own loaded/error state. `code` is sent verbatim as `target`
// (the engine's resolver accepts these plain codes). The static TARGET_LANGS
// below stays as the locale-default seed + the TTS code/name/BCP-47 lookups.
function useTranslateLanguages() {
  const [languages, setLanguages] = useState(null); // null = not loaded yet
  const [error, setError] = useState(null);
  useEffect(() => {
    let cancelled = false;
    fetchTranslateLanguages()
      .then((list) => { if (!cancelled) setLanguages(list); })
      .catch((e) => {
        if (!cancelled) setError(e?.detail || e?.message || "Could not load languages.");
      });
    return () => { cancelled = true; };
  }, []);
  return { languages, error };
}

// ── Localized loader phrases (per-target, cached client-side) ────────────────
// Fetches the whimsical loader phrases TRANSLATED into the current target code
// so the funny "Scribing…/Decoding hieroglyphs…" lines under the loader show in
// the language the user is translating TO. `fetchLoaderPhrases` caches per-lang
// in the api layer, so this only hits the network the first time a given
// language is selected (and never while typing-to-filter — the picker only
// reports a pick, and we key off the committed `target`). Falls back to the
// built-in English SCRIBE_PHRASES while loading, on error, or on an empty
// response. English ("en"/empty) skips the fetch and uses SCRIBE_PHRASES
// directly (English is the base list).
function useLoaderPhrases(targetCode) {
  const [phrases, setPhrases] = useState(SCRIBE_PHRASES);
  useEffect(() => {
    const code = String(targetCode || "").toLowerCase().trim();
    // English / empty → the base list verbatim; no round-trip.
    if (!code || code === "en" || code.split("-")[0] === "en") {
      setPhrases(SCRIBE_PHRASES);
      return undefined;
    }
    let cancelled = false;
    // Show the English list until the localized set lands, so the loader is
    // never empty mid-fetch.
    setPhrases(SCRIBE_PHRASES);
    fetchLoaderPhrases(code)
      .then((list) => {
        if (cancelled) return;
        setPhrases(Array.isArray(list) && list.length ? list : SCRIBE_PHRASES);
      })
      .catch(() => {
        if (!cancelled) setPhrases(SCRIBE_PHRASES);
      });
    return () => { cancelled = true; };
  }, [targetCode]);
  return phrases;
}

// localStorage key for the LAST-USED target code, so re-opening the translator
// (or another file) remembers the user's language. Persisted as the engine
// `code` (e.g. "es"); read back as the picker's default.
const LAST_TARGET_KEY = "neuthek.translate.lastTarget";
function readLastTarget() {
  try {
    const v = localStorage.getItem(LAST_TARGET_KEY);
    return v && v.trim() ? v.trim() : null;
  } catch {
    return null;
  }
}
function writeLastTarget(code) {
  try { if (code) localStorage.setItem(LAST_TARGET_KEY, code); } catch { /* private browsing */ }
}

// The default target: the last-used code if we have one, else the browser
// locale's base ISO when the engine plausibly supports it, else Spanish ("es")
// per the requested sensible default. Returns a PLAIN engine code (not FLORES),
// matching the codes GET /translate/languages emits.
function defaultTargetCode() {
  const last = readLastTarget();
  if (last) return last;
  try {
    const base = (navigator.language || "").toLowerCase().split("-")[0];
    // Only honor a locale that isn't English (English → fall through to the
    // Spanish default, since translating to one's own language is the odd case
    // and Spanish is the requested fallback).
    if (base && base !== "en") return base;
  } catch { /* navigator unavailable */ }
  return "es";
}

// ── Translate target languages ─────────────────────────────────────────────
// The backend translates with NLLB-200 (200 languages); each entry's `value`
// is the FLORES-200 code NLLB expects (e.g. "spa_Latn"). The list below is a
// comprehensive, searchable set spanning the major world languages plus a
// long tail — display name + (native name where it differs) + code. The
// backend also accepts plain ISO codes and maps them, but sending the FLORES
// code directly is unambiguous. Filtered client-side by the search box, so it
// can be long without cluttering the bubble panel.
//
// `iso` is the ISO-639-1 code used only to match the browser locale → the
// default target ("My language"); the value sent to the API is always FLORES.
const TARGET_LANGS = [
  { value: "eng_Latn", iso: "en", name: "English" },
  { value: "spa_Latn", iso: "es", name: "Spanish", native: "Español" },
  { value: "fra_Latn", iso: "fr", name: "French", native: "Français" },
  { value: "deu_Latn", iso: "de", name: "German", native: "Deutsch" },
  { value: "ita_Latn", iso: "it", name: "Italian", native: "Italiano" },
  { value: "por_Latn", iso: "pt", name: "Portuguese", native: "Português" },
  { value: "nld_Latn", iso: "nl", name: "Dutch", native: "Nederlands" },
  { value: "cat_Latn", iso: "ca", name: "Catalan", native: "Català" },
  { value: "glg_Latn", iso: "gl", name: "Galician", native: "Galego" },
  { value: "eus_Latn", iso: "eu", name: "Basque", native: "Euskara" },
  { value: "ron_Latn", iso: "ro", name: "Romanian", native: "Română" },
  { value: "pol_Latn", iso: "pl", name: "Polish", native: "Polski" },
  { value: "ces_Latn", iso: "cs", name: "Czech", native: "Čeština" },
  { value: "slk_Latn", iso: "sk", name: "Slovak", native: "Slovenčina" },
  { value: "slv_Latn", iso: "sl", name: "Slovenian", native: "Slovenščina" },
  { value: "hrv_Latn", iso: "hr", name: "Croatian", native: "Hrvatski" },
  { value: "srp_Cyrl", iso: "sr", name: "Serbian", native: "Српски" },
  { value: "bul_Cyrl", iso: "bg", name: "Bulgarian", native: "Български" },
  { value: "mkd_Cyrl", iso: "mk", name: "Macedonian", native: "Македонски" },
  { value: "rus_Cyrl", iso: "ru", name: "Russian", native: "Русский" },
  { value: "ukr_Cyrl", iso: "uk", name: "Ukrainian", native: "Українська" },
  { value: "bel_Cyrl", iso: "be", name: "Belarusian", native: "Беларуская" },
  { value: "lit_Latn", iso: "lt", name: "Lithuanian", native: "Lietuvių" },
  { value: "lvs_Latn", iso: "lv", name: "Latvian", native: "Latviešu" },
  { value: "est_Latn", iso: "et", name: "Estonian", native: "Eesti" },
  { value: "fin_Latn", iso: "fi", name: "Finnish", native: "Suomi" },
  { value: "swe_Latn", iso: "sv", name: "Swedish", native: "Svenska" },
  { value: "dan_Latn", iso: "da", name: "Danish", native: "Dansk" },
  { value: "nob_Latn", iso: "no", name: "Norwegian", native: "Norsk" },
  { value: "isl_Latn", iso: "is", name: "Icelandic", native: "Íslenska" },
  { value: "hun_Latn", iso: "hu", name: "Hungarian", native: "Magyar" },
  { value: "ell_Grek", iso: "el", name: "Greek", native: "Ελληνικά" },
  { value: "tur_Latn", iso: "tr", name: "Turkish", native: "Türkçe" },
  { value: "als_Latn", iso: "sq", name: "Albanian", native: "Shqip" },
  { value: "gle_Latn", iso: "ga", name: "Irish", native: "Gaeilge" },
  { value: "cym_Latn", iso: "cy", name: "Welsh", native: "Cymraeg" },
  { value: "mlt_Latn", iso: "mt", name: "Maltese", native: "Malti" },
  { value: "arb_Arab", iso: "ar", name: "Arabic", native: "العربية" },
  { value: "heb_Hebr", iso: "he", name: "Hebrew", native: "עברית" },
  { value: "pes_Arab", iso: "fa", name: "Persian", native: "فارسی" },
  { value: "urd_Arab", iso: "ur", name: "Urdu", native: "اردو" },
  { value: "pbt_Arab", iso: "ps", name: "Pashto", native: "پښتو" },
  { value: "ckb_Arab", iso: "ckb", name: "Kurdish (Sorani)", native: "کوردی" },
  { value: "azj_Latn", iso: "az", name: "Azerbaijani", native: "Azərbaycan" },
  { value: "hye_Armn", iso: "hy", name: "Armenian", native: "Հայերեն" },
  { value: "kat_Geor", iso: "ka", name: "Georgian", native: "ქართული" },
  { value: "kaz_Cyrl", iso: "kk", name: "Kazakh", native: "Қазақ" },
  { value: "uzn_Latn", iso: "uz", name: "Uzbek", native: "Oʻzbek" },
  { value: "kir_Cyrl", iso: "ky", name: "Kyrgyz", native: "Кыргызча" },
  { value: "tat_Cyrl", iso: "tt", name: "Tatar", native: "Татар" },
  { value: "khk_Cyrl", iso: "mn", name: "Mongolian", native: "Монгол" },
  { value: "hin_Deva", iso: "hi", name: "Hindi", native: "हिन्दी" },
  { value: "ben_Beng", iso: "bn", name: "Bengali", native: "বাংলা" },
  { value: "pan_Guru", iso: "pa", name: "Punjabi", native: "ਪੰਜਾਬੀ" },
  { value: "guj_Gujr", iso: "gu", name: "Gujarati", native: "ગુજરાતી" },
  { value: "ory_Orya", iso: "or", name: "Odia", native: "ଓଡ଼ିଆ" },
  { value: "tam_Taml", iso: "ta", name: "Tamil", native: "தமிழ்" },
  { value: "tel_Telu", iso: "te", name: "Telugu", native: "తెలుగు" },
  { value: "kan_Knda", iso: "kn", name: "Kannada", native: "ಕನ್ನಡ" },
  { value: "mal_Mlym", iso: "ml", name: "Malayalam", native: "മലയാളം" },
  { value: "mar_Deva", iso: "mr", name: "Marathi", native: "मराठी" },
  { value: "npi_Deva", iso: "ne", name: "Nepali", native: "नेपाली" },
  { value: "sin_Sinh", iso: "si", name: "Sinhala", native: "සිංහල" },
  { value: "snd_Arab", iso: "sd", name: "Sindhi", native: "سنڌي" },
  { value: "asm_Beng", iso: "as", name: "Assamese", native: "অসমীয়া" },
  { value: "zho_Hans", iso: "zh", name: "Chinese (Simplified)", native: "简体中文" },
  { value: "zho_Hant", iso: "zh-TW", name: "Chinese (Traditional)", native: "繁體中文" },
  { value: "yue_Hant", iso: "yue", name: "Cantonese", native: "粵語" },
  { value: "jpn_Jpan", iso: "ja", name: "Japanese", native: "日本語" },
  { value: "kor_Hang", iso: "ko", name: "Korean", native: "한국어" },
  { value: "tha_Thai", iso: "th", name: "Thai", native: "ไทย" },
  { value: "lao_Laoo", iso: "lo", name: "Lao", native: "ລາວ" },
  { value: "mya_Mymr", iso: "my", name: "Burmese", native: "မြန်မာ" },
  { value: "khm_Khmr", iso: "km", name: "Khmer", native: "ខ្មែរ" },
  { value: "vie_Latn", iso: "vi", name: "Vietnamese", native: "Tiếng Việt" },
  { value: "ind_Latn", iso: "id", name: "Indonesian", native: "Indonesia" },
  { value: "zsm_Latn", iso: "ms", name: "Malay", native: "Melayu" },
  { value: "tgl_Latn", iso: "tl", name: "Filipino", native: "Tagalog" },
  { value: "jav_Latn", iso: "jv", name: "Javanese", native: "Jawa" },
  { value: "sun_Latn", iso: "su", name: "Sundanese", native: "Sunda" },
  { value: "bod_Tibt", iso: "bo", name: "Tibetan", native: "བོད་སྐད་" },
  { value: "swh_Latn", iso: "sw", name: "Swahili", native: "Kiswahili" },
  { value: "amh_Ethi", iso: "am", name: "Amharic", native: "አማርኛ" },
  { value: "hau_Latn", iso: "ha", name: "Hausa", native: "Hausa" },
  { value: "yor_Latn", iso: "yo", name: "Yoruba", native: "Yorùbá" },
  { value: "ibo_Latn", iso: "ig", name: "Igbo", native: "Igbo" },
  { value: "zul_Latn", iso: "zu", name: "Zulu", native: "isiZulu" },
  { value: "xho_Latn", iso: "xh", name: "Xhosa", native: "isiXhosa" },
  { value: "afr_Latn", iso: "af", name: "Afrikaans", native: "Afrikaans" },
  { value: "som_Latn", iso: "so", name: "Somali", native: "Soomaali" },
  { value: "kin_Latn", iso: "rw", name: "Kinyarwanda", native: "Kinyarwanda" },
  { value: "nya_Latn", iso: "ny", name: "Chichewa", native: "Chichewa" },
  { value: "sna_Latn", iso: "sn", name: "Shona", native: "chiShona" },
  { value: "lug_Latn", iso: "lg", name: "Luganda", native: "Luganda" },
  { value: "lin_Latn", iso: "ln", name: "Lingala", native: "Lingála" },
  { value: "wol_Latn", iso: "wo", name: "Wolof", native: "Wolof" },
  { value: "tir_Ethi", iso: "ti", name: "Tigrinya", native: "ትግርኛ" },
  { value: "epo_Latn", iso: "eo", name: "Esperanto", native: "Esperanto" },
  { value: "hat_Latn", iso: "ht", name: "Haitian Creole", native: "Kreyòl" },
];

// Match the browser locale to one of the target languages so "My language"
// defaults sensibly. Returns the matching entry (or English as a fallback)
// plus the raw ISO so the label can read "My language (English)".
function browserLocaleTarget() {
  try {
    const full = (navigator.language || "en").toLowerCase(); // e.g. "pt-br"
    const base = full.split("-")[0];
    // Prefer an exact iso match (covers "zh-tw" → Traditional), then base.
    const exact = TARGET_LANGS.find((l) => l.iso.toLowerCase() === full);
    const byBase = TARGET_LANGS.find((l) => l.iso.toLowerCase() === base);
    const match = exact || byBase || TARGET_LANGS[0];
    let label = match.name;
    try {
      const dn = new Intl.DisplayNames([navigator.language || "en"], { type: "language" });
      label = dn.of(base) || match.name;
    } catch {
      /* Intl.DisplayNames unsupported — fall back to the static name. */
    }
    return { value: match.value, label };
  } catch {
    return { value: "eng_Latn", label: "English" };
  }
}

// Look up a target-language entry by a target code. The picker now sends plain
// ENGINE codes (e.g. "es", "zh-tw"), but legacy FLORES values ("spa_Latn") may
// still flow through, so we match on EITHER `value` (FLORES) OR `iso` (the
// plain code), case-insensitively. Returns the whole row (name / native / iso)
// or null. Used to turn the picked target into a BCP-47 tag + a TTS display
// name. Note: the row's `iso` is the canonical plain code we want for TTS.
function langByFlores(value) {
  if (!value) return null;
  const low = String(value).toLowerCase();
  return (
    TARGET_LANGS.find((l) => l.value === value) ||
    TARGET_LANGS.find((l) => (l.iso || "").toLowerCase() === low) ||
    null
  );
}

// Best-effort BCP-47 language tag from a target code. The TARGET_LANGS `iso`
// field is an ISO-639-1 (or ISO-639-3) primary subtag, itself a valid BCP-47
// tag for `utterance.lang` + voice matching; a couple of script variants carry
// a suffix already (e.g. "zh-TW"). When the code isn't in TARGET_LANGS (the
// long tail the full list adds), the engine code IS already a plain ISO-ish
// code, so we pass it through directly. Falls back to "en" when empty.
function bcp47FromFlores(value) {
  const row = langByFlores(value);
  if (row && row.iso) return row.iso;
  return value ? String(value) : "en";
}

// Human display name for a target code (English name), e.g. "Spanish". Prefers
// the TARGET_LANGS table; falls back to the raw code when unknown.
function langNameFromFlores(value) {
  const row = langByFlores(value);
  return (row && row.name) || value || "";
}

// Normalize a language NAME for loose equality (lowercase, trimmed, drop any
// parenthetical qualifier like "Chinese (Simplified)" → "chinese"). Used by the
// same-language guard, which compares the backend's detected SOURCE language
// name against the chosen target's name — these come from different sources
// (the translation engine vs. our catalogue), so an exact match is too strict.
function normalizeLangName(name) {
  return String(name || "")
    .toLowerCase()
    .replace(/\([^)]*\)/g, " ")
    .replace(/[^a-z]+/g, " ")
    .trim();
}

// Resolve the chosen target to its English display NAME, preferring the fetched
// engine catalogue (accurate for the long tail), then the static table, then the
// raw code. `languages` is the [{code,name}] list (possibly null while loading).
function targetDisplayName(target, languages) {
  const fromList = (languages || []).find((l) => l.code === target)?.name;
  return fromList || langNameFromFlores(target) || String(target || "");
}

// True when a detected SOURCE language name and a TARGET (by display name OR
// code) refer to the same language — the trigger for the same-language guard.
// Compares normalized names; also matches the source name against the target's
// resolved display name so "English" ⇄ "en"/"eng_Latn" all line up. Empty source
// (unknown) never matches, so a fresh document is never falsely blocked.
function isSameLanguage(sourceName, target, languages) {
  const src = normalizeLangName(sourceName);
  if (!src) return false;
  const tgtName = normalizeLangName(targetDisplayName(target, languages));
  return !!tgtName && src === tgtName;
}

// The MOST reliable same-language check for the in-stream path: the backend
// reports BOTH the source AND target language NAMES (from one engine), so
// comparing them directly avoids any code↔name resolution gap for the long-tail
// languages. Falls back to the code-based `isSameLanguage` when the stream didn't
// carry a target name. Empty source never matches.
function streamSameLanguage(sourceName, targetName, target, languages) {
  const src = normalizeLangName(sourceName);
  if (!src) return false;
  const tgt = normalizeLangName(targetName);
  if (tgt) return src === tgt;
  return isSameLanguage(sourceName, target, languages);
}

// ── Browser Text-to-Speech (Web Speech API) ─────────────────────────────────
// A tiny controller around window.speechSynthesis used by the document
// "Listen" reader. It:
//   - resolves the highest-quality available voice for a BCP-47 language
//     (prefers Natural / Online / Premium / Enhanced names, then any voice
//     whose `lang` matches the primary subtag, then the platform default),
//   - splits long text into sentence-ish chunks and QUEUES one utterance per
//     chunk so the engine doesn't truncate a long passage (browsers cap a
//     single utterance's length), and
//   - exposes play / pause / resume / stop with a tiny state callback so the
//     UI can reflect idle | playing | paused.
// Voices load asynchronously on some platforms; callers should subscribe to
// `onVoicesReady` (it wires speechSynthesis.onvoiceschanged) before picking.

// True when the browser exposes the Web Speech synthesis API.
function ttsSupported() {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

// Pick the best voice for a BCP-47 tag from the available voice list.
// Preference order:
//   1. exact-ish lang match AND a "premium" name (Natural/Online/Premium/Enhanced)
//   2. any "premium"-named voice whose lang shares the primary subtag
//   3. any voice whose lang shares the primary subtag (prefer a localService=false
//      "online" voice, which tends to be higher quality)
//   4. null → let the engine use its default for utterance.lang
function pickBestVoice(voices, bcp47) {
  if (!voices || voices.length === 0) return null;
  const want = (bcp47 || "en").toLowerCase();
  const base = want.split("-")[0];
  const isPremium = (v) => /natural|online|premium|enhanced|neural/i.test(v.name || "");
  const sameBase = (v) => (v.lang || "").toLowerCase().split("-")[0] === base;
  const exact = (v) => (v.lang || "").toLowerCase() === want;

  // 1 — premium + exact locale, then premium + same base.
  const premiumExact = voices.find((v) => exact(v) && isPremium(v));
  if (premiumExact) return premiumExact;
  const premiumBase = voices.find((v) => sameBase(v) && isPremium(v));
  if (premiumBase) return premiumBase;

  // 2 — same base; prefer an "online" (non-local) voice (usually nicer), then
  // an exact-locale local voice, then any same-base voice.
  const onlineBase = voices.find((v) => sameBase(v) && v.localService === false);
  if (onlineBase) return onlineBase;
  const exactAny = voices.find((v) => exact(v));
  if (exactAny) return exactAny;
  const anyBase = voices.find((v) => sameBase(v));
  if (anyBase) return anyBase;

  return null;
}

// Split text into speakable chunks (~sentence granularity, hard-capped length)
// so a long document is queued as many short utterances instead of one giant
// one the engine would truncate. Keeps sentence punctuation; falls back to a
// hard slice for pathological run-ons.
function chunkForSpeech(text, maxLen = 220) {
  const clean = (text || "").replace(/\s+\n/g, "\n").trim();
  if (!clean) return [];
  // Split on sentence enders (keeping them) and on hard line breaks.
  const rough = clean
    .split(/(?<=[.!?。！？…])\s+|\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean);
  const out = [];
  for (const piece of rough) {
    if (piece.length <= maxLen) {
      out.push(piece);
      continue;
    }
    // Long piece — break on commas/spaces under the cap, hard-slice if needed.
    let rest = piece;
    while (rest.length > maxLen) {
      let cut = rest.lastIndexOf(" ", maxLen);
      if (cut < maxLen * 0.5) cut = maxLen; // no good break — hard cut
      out.push(rest.slice(0, cut).trim());
      rest = rest.slice(cut).trim();
    }
    if (rest) out.push(rest);
  }
  return out;
}

// ── The rail itself ───────────────────────────────────────────────────────
// `active`     : null | "comments" | "info" | "text"  (parent-owned so it's
//                shared with the CommentPanel's expanded state).
// `onSelect(id)`: toggle a tool (parent maps "comments" to the comment
//                panel's expanded flag).
// `commentCount`: badge on the comments bubble.
// `onBestOf`    : fires the "Best of" bubble — an ACTION bubble (not a
//                slide-out panel). It hands off to the parent, which pulls
//                this photo's near-duplicate group and opens the shared
//                BestOfModal on it. Omitted (e.g. non-image surfaces) → the
//                bubble isn't rendered. It never sets `active`, so it can't
//                fight the one-panel-at-a-time info/text/comments state.
function ToolRail({ active, onSelect, commentCount, onBestOf, bubbles: bubblesProp }) {
  const bubbles = bubblesProp || [
    { id: "comments", icon: "message", label: "Comments", badge: commentCount },
    { id: "info", icon: "info", label: "File info" },
    { id: "text", icon: "type", label: "Text in image" },
  ];
  return (
    <div className="toolrail" role="toolbar" aria-label="Preview tools" onClick={(e) => e.stopPropagation()}>
      {bubbles.map((b) => (
        <button
          key={b.id}
          type="button"
          className={"toolrail__bubble" + (active === b.id ? " toolrail__bubble--active" : "")}
          aria-pressed={active === b.id}
          aria-label={active === b.id ? `Close ${b.label}` : `Open ${b.label}`}
          title={b.label}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(b.id);
          }}
        >
          <Icon name={b.icon} size={18} />
          {b.badge > 0 && <span className="toolrail__badge">{b.badge}</span>}
        </button>
      ))}
      {/* Best of — pull this photo's similar / burst group and pick the
          keeper. An action (opens the BestOfModal via the parent), so it
          carries no active state and is never part of the panel toggle. */}
      {onBestOf && (
        <button
          type="button"
          className="toolrail__bubble"
          aria-label="Pick best of similar shots"
          title="Best of similar shots"
          onClick={(e) => {
            e.stopPropagation();
            onBestOf();
          }}
        >
          <Icon name="wand" size={18} />
        </button>
      )}
    </div>
  );
}

// ── Info panel ─────────────────────────────────────────────────────────────
// Mirrors the right-hand DETAILS facts inside the lightbox. Read-only.
function InfoToolPanel({ file, onClose }) {
  const rows = [];
  if (file.ext) rows.push(["Type", file.ext]);
  if (file.size) rows.push(["Size", file.size]);
  if (file.when) rows.push(["Uploaded", file.when]);
  if (file.width && file.height) rows.push(["Dimensions", `${file.width} × ${file.height}`]);
  if (file.gps && (file.gps.place || file.gps.lat != null)) {
    rows.push(["Location", file.gps.place || "Looking up location…"]);
  }
  if (file.scene_label) rows.push(["Scene", file.scene_label]);
  if (file.indoor_outdoor) rows.push(["Setting", file.indoor_outdoor]);

  return (
    <aside className="toolpanel" onClick={(e) => e.stopPropagation()} aria-label="File info">
      <header className="toolpanel__head">
        <Icon name="info" size={14} />
        <span className="toolpanel__title">File info</span>
        <button
          type="button"
          className="btn-icon toolpanel__collapse"
          onClick={onClose}
          aria-label="Collapse to bubble"
          title="Collapse to bubble"
        >
          <Icon name="chevronLeft" size={13} />
        </button>
      </header>
      <div className="toolpanel__body">
        <div className="toolpanel__filename">{file.name}</div>
        {file.topic ? (
          <div className="toolpanel__topic">
            <span className="kicker" style={{ marginRight: 6 }}>
              <Icon name="sparkles" size={10} style={{ verticalAlign: "-1px" }} /> AI
            </span>
            {file.topic}
          </div>
        ) : null}
        <div className="toolpanel__kv">
          {rows.map(([k, v]) => (
            <div key={k} className="toolpanel__kv-row">
              <span className="toolpanel__kv-k">{k}</span>
              <span className="toolpanel__kv-v">{v}</span>
            </div>
          ))}
        </div>
        {file.aiContent ? (
          <div className="toolpanel__section">
            <div className="toolpanel__section-label">Description</div>
            <div className="toolpanel__desc">{file.aiContent}</div>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

// ── Language picker (searchable, full engine list) ──────────────────────────
// A compact combobox over the ENGINE'S FULL target set (fetched from
// GET /translate/languages — ~100+ languages). Closed, it shows the current
// language as a small pill-button; open, it drops a search box + a scrollable,
// type-to-filter list. `value` is an engine `code` (e.g. "es"); `onChange(code)`
// reports the pick (and the parent persists it as the last-used target). The
// filter matches the English name AND the code so users can type "german" or
// "de". `languages` is the fetched [{code,name}] list (null while loading);
// `loading`/`error` drive the placeholder/empty states. Its own hooks live here
// (standalone component) so the parent panel's hooks stay unconditional.
function LanguagePicker({ value, onChange, languages, loading, error }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null);
  const inputRef = useRef(null);

  const list = languages || [];
  // Resolve the chosen code to a display name. Falls back to the engine code
  // itself (uppercased) before the list has loaded so the button always reads
  // something sensible rather than blank.
  const selected = list.find((l) => l.code === value);
  const buttonLabel = selected
    ? selected.name
    : (value ? value.toUpperCase() : "Select language");

  // Close on outside click + focus the search box when opening.
  useEffect(() => {
    if (!open) return undefined;
    const onDocClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      window.clearTimeout(id);
    };
  }, [open]);

  const q = query.trim().toLowerCase();
  const filtered = !q
    ? list
    : list.filter((l) => `${l.name} ${l.code}`.toLowerCase().includes(q));

  const pick = (code) => {
    onChange(code);
    setOpen(false);
    setQuery("");
  };

  return (
    <div className="langpick" ref={rootRef}>
      <button
        type="button"
        className="langpick__button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Target language: ${buttonLabel}. Change.`}
        title={selected ? `${selected.name} (${selected.code})` : buttonLabel}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="langpick__current">{buttonLabel}</span>
        <Icon name="chevronDown" size={12} />
      </button>
      {open && (
        <div className="langpick__pop" role="listbox" aria-label="Languages">
          <div className="langpick__search">
            <Icon name="search" size={13} />
            <input
              ref={inputRef}
              type="text"
              className="langpick__input"
              placeholder={
                list.length > 0 ? `Search ${list.length} languages…` : "Search languages…"
              }
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setOpen(false);
                } else if (e.key === "Enter" && filtered.length > 0) {
                  e.preventDefault();
                  pick(filtered[0].code);
                }
              }}
            />
          </div>
          <div className="langpick__list">
            {loading && list.length === 0 ? (
              <div className="langpick__empty">Loading languages…</div>
            ) : error && list.length === 0 ? (
              <div className="langpick__empty">{error}</div>
            ) : filtered.length === 0 ? (
              <div className="langpick__empty">No match</div>
            ) : (
              filtered.map((l) => (
                <button
                  key={l.code}
                  type="button"
                  role="option"
                  aria-selected={l.code === value}
                  className={"langpick__opt" + (l.code === value ? " langpick__opt--sel" : "")}
                  onClick={() => pick(l.code)}
                  title={l.code}
                >
                  <span className="langpick__opt-name">{l.name}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Text (OCR + translate) panel ───────────────────────────────────────────
// On open it OCRs the image (React Query, cached per image id). Lists the
// extracted lines, offers a show/hide toggle for the on-image highlight
// boxes (rendered by the parent over the rendered <img>), and a Translate
// control with a comprehensive, searchable target-language picker (NLLB-200
// languages). Translation runs server-side (NLLB-200, Qwen fallback); we
// surface the engine + note inline.
function TextToolPanel({
  file,
  onClose,
  ocr,
  ocrLoading,
  ocrError,
  showBoxes,
  onToggleBoxes,
  activeLineIdx,
  onHoverLine,
  t,
}) {
  const text = ocr?.text || "";
  const lines = ocr?.lines || [];
  const hasText = !!text.trim();

  const copyText = useCallback(() => {
    try {
      navigator.clipboard.writeText(text);
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Could not copy");
    }
  }, [text]);

  // Download the TRANSLATED IMAGE (PNG) once a translation exists. Named after
  // the original file's base + the target language, e.g. "poster (Spanish).png".
  // Uses a temp object URL + <a download>, revoked shortly after the click.
  const downloadTranslatedImage = useCallback(() => {
    if (!t?.translatedBlob) {
      toast.error("Translate the image first.");
      return;
    }
    try {
      const base = (file.name || "image").replace(/\.[^./\\]+$/, "");
      const lang = t.targetLang || langNameFromFlores(t.target) || "translation";
      const url = URL.createObjectURL(t.translatedBlob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${base} (${lang}).png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch {
      toast.error("Could not download the translated image.");
    }
  }, [t?.translatedBlob, t?.targetLang, t?.target, file.name]);

  return (
    <aside className="toolpanel toolpanel--text" onClick={(e) => e.stopPropagation()} aria-label="Text in image">
      <header className="toolpanel__head">
        <Icon name="type" size={14} />
        <span className="toolpanel__title">Text in image</span>
        {lines.length > 0 && <span className="toolpanel__count">{lines.length}</span>}
        <button
          type="button"
          className="btn-icon toolpanel__collapse"
          onClick={onClose}
          aria-label="Collapse to bubble"
          title="Collapse to bubble"
        >
          <Icon name="chevronLeft" size={13} />
        </button>
      </header>

      <div className="toolpanel__body">
        {/* PRIMARY action — translate the text INSIDE the image. The result
            replaces the photo in the MAIN view (the parent swaps the <img> to
            the translated render), and while it runs the branded
            DocTranslateLoader (logo + localized phrases + i/n progress) shows
            OVER the image in the center. Rendered ALWAYS — independent of the
            client-side OCR readout below — because the server runs its own text
            detection. Shown FIRST so it's the headline of the panel. */}
        <ImageTranslateControls t={t} onDownload={downloadTranslatedImage} />

        {/* SECONDARY — the extracted-text readout (copy + on-image box toggle).
            This is the client-side OCR used for the on-image highlight boxes; it
            is SEPARATE from the in-image translation above. We no longer show a
            big "Reading text from the image…" skeleton here — the branded loader
            over the image now owns the "working" signal; this section just
            quietly resolves into the extracted lines (or a small note). */}
        {ocrLoading ? (
          <div className="toolpanel__hint" style={{ marginTop: 16 }}>
            <span className="doc-translated__spinner" aria-hidden="true" style={{ verticalAlign: "-1px", marginRight: 6 }} />
            Reading the image’s text for the highlight boxes…
          </div>
        ) : ocrError ? (
          <div className="toolpanel__note" style={{ marginTop: 16 }} title={ocrError}>
            <Icon name="info" size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />
            Couldn’t read the image’s text for highlighting.
          </div>
        ) : hasText ? (
          <>
            <div className="toolpanel__controls" style={{ marginTop: 14 }}>
              {ocr?.boxes && lines.length > 0 && (
                <button
                  type="button"
                  className={"chip-toggle" + (showBoxes ? " chip-toggle--on" : "")}
                  onClick={onToggleBoxes}
                  aria-pressed={showBoxes}
                  title={showBoxes ? "Hide highlight boxes" : "Show highlight boxes on the image"}
                >
                  <Icon name={showBoxes ? "eye" : "eyeOff"} size={13} />
                  {showBoxes ? "Boxes on" : "Boxes off"}
                </button>
              )}
              <button type="button" className="chip-toggle" onClick={copyText} title="Copy text">
                <Icon name="copy" size={13} /> Copy
              </button>
            </div>

            {/* Extracted lines. Hovering a line highlights its box on the
                image (and vice-versa via activeLineIdx). When boxes aren't
                available we just render the text. */}
            <div className="toolpanel__section-label">Extracted text</div>
            <div className="ocr-lines">
              {lines.length > 0 ? (
                lines.map((ln, i) => (
                  <div
                    key={i}
                    className={"ocr-line" + (activeLineIdx === i ? " ocr-line--active" : "")}
                    onMouseEnter={() => onHoverLine(i)}
                    onMouseLeave={() => onHoverLine(null)}
                  >
                    {ln.text}
                  </div>
                ))
              ) : (
                <pre className="ocr-text">{text}</pre>
              )}
            </div>
          </>
        ) : (
          <div className="toolpanel__hint" style={{ marginTop: 14 }}>
            We didn’t detect readable text in this image — try Translate
            anyway, it runs its own text detection.
          </div>
        )}
      </div>
    </aside>
  );
}

// ── In-image Translate controls (left Text panel) ───────────────────────────
// The control surface for the IN-IMAGE translation. The translated IMAGE itself
// renders in the MAIN center view (the parent swaps the <img>); this is just:
//   - a searchable target-language picker (NLLB-200) + a Translate button that
//     POSTs to /images/{id}/translate-image (via the shared `useImageTranslation`
//     state the parent threads in as `t`),
//   - once a translation exists: the language pair, a Show original ⇄ Show
//     translation toggle (mirrors the center bar), and a Download button that
//     saves the translated PNG,
//   - a "Translating image…" indicator while running, and the "no text found"
//     / error message when applicable.
function ImageTranslateControls({ t, onDownload }) {
  const langPair =
    t.langs && (t.langs.source || t.langs.target)
      ? `${t.langs.source || "Detected"} → ${t.langs.target || ""}`.trim()
      : null;

  return (
    <div className="toolpanel__section toolpanel__translate">
      <div className="toolpanel__section-label">Translate text in image</div>
      <div className="toolpanel__translate-row">
        <LanguagePicker
          value={t.target}
          onChange={t.setTarget}
          languages={t.languages}
          loading={t.languages == null && !t.languagesError}
          error={t.languagesError}
        />
        <button
          type="button"
          className="btn btn--primary btn--sm"
          onClick={t.run}
          disabled={t.translating}
        >
          {t.translating ? "Translating…" : t.done ? "Re-translate" : "Translate"}
        </button>
      </div>

      {t.translating && (
        <div className="toolpanel__hint" style={{ marginTop: 10 }}>
          <span className="doc-translated__spinner" aria-hidden="true" style={{ verticalAlign: "-1px", marginRight: 6 }} />
          Translating the image — erasing the original text and rendering the
          translation in its place.
        </div>
      )}

      {/* No-text / error message (e.g. the server found nothing to translate). */}
      {!t.translating && t.error && (
        <div className="toolpanel__note" style={{ marginTop: 10 }} title={t.error}>
          <Icon name="info" size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />
          {t.error}
        </div>
      )}

      {/* Once a translated image exists: language pair + toggle (flips the
          MAIN view) + Download the rendered PNG. The image itself lives in
          the center, not here. */}
      {t.done && t.translatedUrl && (
        <div className="toolpanel__translation">
          <div className="toolpanel__translation-head">
            {langPair ? (
              <span className="toolpanel__lang-pill">{langPair}</span>
            ) : (
              <span />
            )}
            <button
              type="button"
              className="chip-toggle"
              onClick={t.toggleView}
              aria-pressed={t.showTranslation}
              title="Toggle original / translation in the main view"
            >
              <Icon name="refresh" size={12} />
              {t.showTranslation ? "Show original" : "Show translation"}
            </button>
          </div>
          <div className="toolpanel__controls" style={{ marginTop: 8, marginBottom: 0 }}>
            <button
              type="button"
              className="chip-toggle"
              onClick={onDownload}
              title="Download the translated image (PNG)"
            >
              <Icon name="download" size={13} /> Download image
            </button>
          </div>
          <div
            className="toolpanel__note"
            title="The text in the image was detected, erased, and re-rendered in the target language with a neural machine-translation model."
          >
            <Icon name="info" size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />
            Neural translation · in place
          </div>
        </div>
      )}
    </div>
  );
}

// ── In-image translation state (MAIN image view ⇄ LEFT Text panel) ──────────
// The image analogue of useDocTranslation. The redesigned image Translate UX
// renders the TRANSLATED image (text erased + translation rendered in place) in
// the MAIN CENTER view — replacing the original <img> — while the LEFT Text
// panel drives the controls (language picker + Translate + Download). Both
// surfaces read the SAME translation, so this hook owns all of that state once;
// the parent (PreviewPanel) instantiates it and threads it to the center image
// view and the left panel.
//
// The translation comes back from POST /images/{id}/translate-image as a single
// image/png blob (NOT a stream): the same image with its text translated in
// place. The detected source + target language NAMES ride on response headers.
//
// State:
//   target          FLORES-200 code the user picked (defaults to locale).
//   translating     true while the request is in flight.
//   done            true once a translated image has been produced.
//   error           a user-facing message (incl. the "no text found" case).
//   translatedBlob  the returned image/png Blob (for Download).
//   translatedUrl   object URL of that blob (rendered by the center view +
//                   revoked on file change / unmount / re-run).
//   sourceLang/targetLang  human language names from the response headers.
//   showTranslation center toggle: true → translated image, false → original.
//
// Resets fully when `imageId` changes (revoking the object URL + re-seeding the
// locale target). Aborts any in-flight request on unmount / re-run.
export function useImageTranslation(imageId) {
  const localeDefault = browserLocaleTarget();
  // Full engine language catalogue for the picker + the default target code
  // (last-used → locale → Spanish), persisted across files/sessions.
  const { languages, error: languagesError } = useTranslateLanguages();
  const [target, setTarget] = useState(defaultTargetCode);
  // Loader phrases localized to the chosen target (falls back to English).
  const loaderPhrases = useLoaderPhrases(target);
  const [translating, setTranslating] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState(null);
  const [translatedBlob, setTranslatedBlob] = useState(null);
  const [translatedUrl, setTranslatedUrl] = useState(null);
  const [sourceLang, setSourceLang] = useState("");
  const [targetLang, setTargetLang] = useState("");
  // Live translated TEXT accumulated from the stream (one region per line),
  // shown FAINTLY BEHIND the branded loader so the translation visibly fills in
  // while it works — mirrors useDocTranslation.streamText for PDFs/docs. Cleared
  // on reset/run; superseded by the rendered PNG when done.
  const [streamText, setStreamText] = useState("");
  // Region counter for the loader's "3 / 8" label + determinate bar. Driven by
  // the stream's {i,n}; stays {0,0} (indeterminate "warming up") until OCR
  // finishes and the first region lands.
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  // Default to ORIGINAL so opening an image shows the real photo first; the
  // user flips to the translation (it auto-flips when they hit Translate).
  const [showTranslation, setShowTranslation] = useState(false);

  const abortRef = useRef(null);
  // Hold the live object URL in a ref too so cleanup (unmount / file change)
  // can revoke it without depending on the state value in the effect deps.
  const urlRef = useRef(null);

  const cancelRequest = useCallback(() => {
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch { /* already aborted */ }
      abortRef.current = null;
    }
  }, []);

  // Revoke + forget the current translated object URL.
  const revokeUrl = useCallback(() => {
    if (urlRef.current) {
      try { URL.revokeObjectURL(urlRef.current); } catch { /* no-op */ }
      urlRef.current = null;
    }
  }, []);

  // Clear everything (used by file change + a fresh run + language switch).
  const reset = useCallback(() => {
    cancelRequest();
    revokeUrl();
    setTranslating(false);
    setDone(false);
    setError(null);
    setTranslatedBlob(null);
    setTranslatedUrl(null);
    setSourceLang("");
    setTargetLang("");
    setStreamText("");
    setProgress({ done: 0, total: 0 });
  }, [cancelRequest, revokeUrl]);

  // Full reset + drop back to the original view when the image changes. The
  // chosen TARGET is intentionally KEPT (it's the user's last-used language,
  // persisted) so switching files doesn't silently reset their language.
  useEffect(() => {
    reset();
    setShowTranslation(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageId]);

  // Abort + revoke on unmount.
  useEffect(() => () => { cancelRequest(); revokeUrl(); }, [cancelRequest, revokeUrl]);

  const run = useCallback(() => {
    if (!imageId || translating) return;
    // Drop any prior translation/URL/request before starting a fresh one.
    cancelRequest();
    revokeUrl();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);
    setDone(false);
    setTranslatedBlob(null);
    setTranslatedUrl(null);
    setSourceLang("");
    setTargetLang("");
    setStreamText("");
    setProgress({ done: 0, total: 0 });
    // Flip the center to the translated view so the user lands on the result —
    // the branded loader (with the translation streaming in behind it) shows
    // there until the rendered PNG lands.
    setShowTranslation(true);
    setTranslating(true);

    // STREAMING in-image translation: the server emits a {stage:"reading"} line
    // (OCR started), then one {i,n,text} line per translated region, then a
    // final {done,image_b64,source_lang,target_lang} line. We accumulate each
    // region's text into `streamText` (shown faintly behind the loader) and
    // track {i,n} as `progress`, then object-URL the decoded PNG on done.
    translateImageStream(imageId, target, {
      signal: controller.signal,
      onStage: () => {
        // OCR started but no region has a count yet — keep the bar in its
        // indeterminate "warming up" state (progress stays {0,0}).
      },
      onRegion: ({ i, n, text }) => {
        if (controller.signal.aborted) return;
        // Regions arrive in order; show "k / n" (k = i+1 done) and stream the
        // translated text in behind the loader card.
        setProgress({ done: Math.min(i + 1, n || i + 1), total: n });
        if (text && text.trim()) {
          setStreamText((prev) => (prev ? prev + "\n\n" + text : text));
        }
      },
      onDone: ({ blob, sourceLang: src, targetLang: tgt }) => {
        if (controller.signal.aborted) return;
        abortRef.current = null;
        setTargetLang(tgt || "");
        // No text detected: the server returned the original image unchanged
        // and an empty source language. Surface a message + flip back to the
        // original (there's nothing translated to show).
        if (!src) {
          setSourceLang("");
          setShowTranslation(false);
          setError("No readable text was found in this image.");
          setTranslating(false);
          return;
        }
        setSourceLang(src);
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        setTranslatedBlob(blob);
        setTranslatedUrl(url);
        // Snap the bar to complete so the hand-off to the rendered image reads
        // as "finished" rather than freezing mid-count.
        setProgress((p) => (p.total ? { done: p.total, total: p.total } : p));
        setDone(true);
        setTranslating(false);
      },
      onError: (msg) => {
        if (controller.signal.aborted) return;
        abortRef.current = null;
        const text =
          typeof msg === "string" && msg
            ? msg
            : "Could not translate the image.";
        setError(text);
        // Nothing translated to show — drop back to the original photo.
        setShowTranslation(false);
        setTranslating(false);
        toast.error(text);
      },
    });
  }, [imageId, translating, target, cancelRequest, revokeUrl]);

  // Switching language cancels an in-flight request + clears the prior
  // translation so the next Translate starts clean (and drops back to the
  // original until it runs again). Persists the pick as the last-used target.
  const changeTarget = useCallback((v) => {
    reset();
    setShowTranslation(false);
    setTarget(v);
    writeLastTarget(v);
  }, [reset]);

  const toggleView = useCallback(() => setShowTranslation((v) => !v), []);

  // Back-compat language-pair object (mirrors useDocTranslation's `langs`).
  const langs = sourceLang || targetLang ? { source: sourceLang, target: targetLang } : null;

  return {
    localeDefault,
    target,
    setTarget: changeTarget,
    // Full engine language list (+ load error) for the searchable picker.
    languages,
    languagesError,
    // Localized loader phrases for the branded loader (English fallback).
    loaderPhrases,
    translating,
    done,
    error,
    translatedBlob,
    translatedUrl,
    sourceLang,
    targetLang,
    langs,
    // Live translated text streamed in behind the loader card (per region) +
    // the region counter for the loader's progress bar/label.
    streamText,
    progress,
    showTranslation,
    setShowTranslation,
    toggleView,
    run,
    reset,
  };
}

// ── Shared document-translation state (CENTER view ⇄ LEFT panel) ────────────
// The redesigned document Translate UX renders the translated document in the
// MAIN CENTER view (streaming, in place) while the LEFT tool panel drives the
// controls (language picker + Translate + Download) and a TTS reader. Both
// surfaces must read the SAME translation, so this hook owns all of that state
// once; the parent (PreviewPanel) instantiates it and threads it to the center
// view and the left panel. Keeping it in a single hook (rather than two
// independent components) is what lets the center fill in while the left
// panel's Listen button reads whichever language is on screen.
//
// The TRANSLATION now comes from the STRUCTURED stream (translateDocStream):
// the server extracts the document structure itself and pushes one TYPED block
// per line — we do NOT send it the text. The `text` arg is still received, but
// used ONLY for original-language TTS (reading the source aloud).
//
// State:
//   target            FLORES-200 code the user picked (defaults to locale).
//   blocks            typed structural blocks [{type,text}] in reading order,
//                     each placed at its stream index `i` (grows while
//                     streaming). The CENTER view renders these by type.
//   translatedText    derived: blocks' text "\n\n"-joined (for the TTS reader
//                     + the PDF download fallback).
//   translating/done  stream lifecycle flags.
//   progress {done,total}  block counter for the "3 / 18" label.
//   srcLang/tgtLang   human language names from the blocks (the pair).
//   langs {source,target}  the same pair as an object (back-compat).
//   showTranslation   center toggle: true → translated view, false → original.
//
// Resets fully when `fileId` changes (and re-seeds the locale target); also
// drops a stale translation if the source `text` changes (doc finished
// loading after mount). Aborts any in-flight stream on unmount.
export function useDocTranslation(fileId, text, isPdf = false) {
  const localeDefault = browserLocaleTarget();
  // Full engine language catalogue for the picker + the default target code
  // (last-used → locale → Spanish), persisted across files/sessions.
  const { languages, error: languagesError } = useTranslateLanguages();
  const [target, setTarget] = useState(defaultTargetCode);
  // Loader phrases localized to the chosen target (falls back to English).
  const loaderPhrases = useLoaderPhrases(target);
  // Typed blocks accumulated in reading order; each lands at its index `i`.
  // (NON-PDF / structured path only.)
  const [blocks, setBlocks] = useState([]); // [{ type, text }]
  // Live translated TEXT accumulated from the stream, shown FAINTLY BEHIND the
  // loader so the translation visibly fills in while it works. Fed by BOTH
  // paths: the PDF stream's progress `text` and the structured stream's blocks.
  // Cleared on reset/run; superseded by the real rendered result when done.
  // (Kept for the TTS fallback + any flat consumer; the FORMATTED live render
  // uses `streamBlocks` below.)
  const [streamText, setStreamText] = useState("");
  // Live translated BLOCKS accumulated from the stream, each as {text, kind}
  // in reading order. This is what the PDF live render styles by `kind`
  // (title/h1/h2/p/li) so the streaming page matches the real document FORMAT
  // instead of a flat wall of paragraphs. Appended per progress/block line;
  // cleared on reset/run.
  const [streamBlocks, setStreamBlocks] = useState([]); // [{ text, kind }]
  const [translating, setTranslating] = useState(false);
  const [done, setDone] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [srcLang, setSrcLang] = useState("");
  const [tgtLang, setTgtLang] = useState("");
  // Default to ORIGINAL so opening a document shows the real page first; the
  // user flips to the translation (or it auto-flips when they hit Translate).
  const [showTranslation, setShowTranslation] = useState(false);
  // ── PDF path: the EXACT-COPY translated PDF (real layout, text-in-place).
  // For PDFs we do NOT use the structured `blocks` re-render; instead the
  // backend returns the ORIGINAL PDF translated in place, which we render in
  // the same viewer. `translatedPdfBlob` is the application/pdf Blob; its
  // object URL is held in `translatedPdfUrl` (revoked on reset/unmount). The
  // joined translated TEXT (for TTS) is extracted lazily + cached in a ref.
  const [translatedPdfBlob, setTranslatedPdfBlob] = useState(null);
  const [translatedPdfUrl, setTranslatedPdfUrl] = useState(null);
  const pdfUrlRef = useRef(null);
  // ── PDF path: STREAMED EXACT PAGE IMAGES (the real rendered pages). The
  // translate-pdf stream also emits page-snapshot lines — the actual document
  // pages with the translation applied so far (exact layout, progressively
  // translating). We keep them as an ARRAY indexed by 0-based page; the LATEST
  // snapshot for a page WINS (a later, more-translated render of page k replaces
  // the earlier one, revoking the superseded object URL). The live PDF view
  // renders these instead of the HTML reflow approximation. Each entry is an
  // object URL string (or undefined for a page that hasn't streamed yet). A ref
  // mirrors the same map so cleanup (reset / unmount) can revoke every URL
  // without the effect depending on the state value.
  const [pageImages, setPageImages] = useState([]); // (string | undefined)[]
  const pageImagesRef = useRef([]);
  // Cache of the translated PDF's extracted text for the TTS reader, so it
  // isn't re-extracted on every Play. Keyed nowhere — invalidated by reset().
  const pdfTextCacheRef = useRef(null);
  // SAME-LANGUAGE GUARD message. Set (and a toast shown) when the chosen target
  // language equals the document's detected SOURCE language — there's nothing to
  // translate, so we never start (or we abort) the stream and surface this inline
  // prompt: "This document is already in <Language>." Cleared on reset / a fresh
  // run / a language switch. Never coexists with a translation in flight.
  const [sameLangMessage, setSameLangMessage] = useState(null);

  const hasText = !!(text || "").trim();
  const abortRef = useRef(null);

  const cancelStream = useCallback(() => {
    if (abortRef.current) {
      try { abortRef.current.abort(); } catch { /* already aborted */ }
      abortRef.current = null;
    }
  }, []);

  // Revoke + forget the current translated-PDF object URL.
  const revokePdfUrl = useCallback(() => {
    if (pdfUrlRef.current) {
      try { URL.revokeObjectURL(pdfUrlRef.current); } catch { /* no-op */ }
      pdfUrlRef.current = null;
    }
  }, []);

  // Revoke EVERY streamed page-image object URL + forget the map. Called on
  // reset / a fresh run / unmount so no page snapshot leaks. Clears both the ref
  // (the source of truth for revocation) and the state (so the view empties).
  const revokeAllPageImages = useCallback(() => {
    const arr = pageImagesRef.current || [];
    for (const url of arr) {
      if (url) { try { URL.revokeObjectURL(url); } catch { /* no-op */ } }
    }
    pageImagesRef.current = [];
    setPageImages([]);
  }, []);

  // Record one streamed page snapshot — the REAL rendered page translated so
  // far. The LATEST snapshot for a given page WINS: if a URL already exists at
  // that index, revoke the superseded one before storing the new URL so the
  // earlier (less-translated) render is freed. Defensive against a missing/odd
  // page index (skip + immediately revoke the URL so it can't leak).
  //
  // NOTE on the 25% reveal gate (CHANGE 2): we intentionally KEEP every snapshot
  // here so `pageImages` always holds the latest render of each page for the
  // FINAL stack (dropping early ones would lose the last page of a short doc when
  // its only snapshot arrives before the progress line that crosses 25%). The
  // "don't reveal pages until progress ≥25%" behavior is enforced purely at the
  // DISPLAY layer (TranslatedPdfView gates what it hands the loader), so nothing
  // is shown early WITHOUT discarding the data needed for the finished view.
  const setPageImage = useCallback((page, url) => {
    if (!url) return;
    const idx = Number.isInteger(page) ? page : -1;
    if (idx < 0) { try { URL.revokeObjectURL(url); } catch { /* no-op */ } return; }
    const arr = pageImagesRef.current.slice();
    const prev = arr[idx];
    if (prev && prev !== url) { try { URL.revokeObjectURL(prev); } catch { /* no-op */ } }
    arr[idx] = url;
    pageImagesRef.current = arr;
    setPageImages(arr);
  }, []);

  // Clear everything (used by file change + a fresh run + language switch).
  const reset = useCallback(() => {
    cancelStream();
    revokePdfUrl();
    revokeAllPageImages();
    setBlocks([]);
    setStreamText("");
    setStreamBlocks([]);
    setDone(false);
    setTranslating(false);
    setProgress({ done: 0, total: 0 });
    setSrcLang("");
    setTgtLang("");
    setTranslatedPdfBlob(null);
    setTranslatedPdfUrl(null);
    pdfTextCacheRef.current = null;
    setSameLangMessage(null);
  }, [cancelStream, revokePdfUrl, revokeAllPageImages]);

  // Full reset + drop back to the original view when the file changes. The
  // chosen TARGET is intentionally KEPT (it's the user's last-used language,
  // persisted) so switching files doesn't silently reset their language.
  useEffect(() => {
    reset();
    setShowTranslation(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId]);

  // Source text changed (doc finished loading) — drop a stale translation but
  // keep the chosen target + view preference. NON-PDF only: for PDFs `text` is
  // used solely for original-view TTS (the translation reads the PDF itself by
  // id), and the `/text` extraction can resolve AFTER the user has already
  // started a PDF translation — resetting here would abort that in-flight run.
  useEffect(() => {
    if (isPdf) return;
    reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  // Abort + revoke on unmount.
  useEffect(() => () => { cancelStream(); revokePdfUrl(); revokeAllPageImages(); }, [cancelStream, revokePdfUrl, revokeAllPageImages]);

  // SAME-LANGUAGE GUARD. The document's detected source language equals the
  // chosen target — there's nothing to translate. Tears down any in-flight
  // stream, drops every half-streamed page, flips back to the ORIGINAL view, and
  // surfaces the inline prompt + a toast: "This document is already in <Lang>."
  // `srcName` is the detected source language NAME (preferred for the message);
  // we fall back to the target's display name when the source name is empty.
  // Memoized so the stream callbacks can call it without a stale closure.
  const guardSameLanguage = useCallback((srcName) => {
    cancelStream();
    revokePdfUrl();
    revokeAllPageImages();
    const langLabel =
      (srcName && srcName.trim()) || targetDisplayName(target, languages) || "this language";
    setStreamText("");
    setStreamBlocks([]);
    setBlocks([]);
    setTranslatedPdfBlob(null);
    setTranslatedPdfUrl(null);
    setProgress({ done: 0, total: 0 });
    setDone(false);
    setTranslating(false);
    setShowTranslation(false);
    const msg = `This document is already in ${langLabel}.`;
    setSameLangMessage(msg);
    toast(msg, { icon: "🌐" });
  }, [cancelStream, revokePdfUrl, revokeAllPageImages, target, languages]);

  const run = useCallback(() => {
    if (translating) return;
    // PDFs need a file id (text isn't required — the backend reads the PDF
    // itself); non-PDFs need extracted text to translate.
    if (!fileId) return;
    if (!isPdf && !hasText) return;
    // PRE-FLIGHT same-language guard: if a PRIOR run already detected this
    // document's source language and it matches the chosen target, don't start
    // the stream at all — show the inline prompt instead. (`srcLang` survives
    // until the file changes; it's reset below only AFTER this check.) A fresh
    // document with no detected source yet falls through and is caught in-stream.
    if (isSameLanguage(srcLang, target, languages)) {
      guardSameLanguage(srcLang);
      return;
    }
    setSameLangMessage(null);
    cancelStream();
    revokePdfUrl();
    revokeAllPageImages();
    const controller = new AbortController();
    abortRef.current = controller;
    setBlocks([]);
    setStreamText("");
    setStreamBlocks([]);
    setDone(false);
    setProgress({ done: 0, total: 0 });
    setSrcLang("");
    setTgtLang("");
    setTranslatedPdfBlob(null);
    setTranslatedPdfUrl(null);
    pdfTextCacheRef.current = null;
    // Flip the center to the translated view so the user watches it fill in.
    setShowTranslation(true);
    setTranslating(true);

    if (isPdf) {
      // EXACT-COPY PDF path: the backend returns the ORIGINAL PDF with only its
      // text translated IN PLACE (layout/images/positions preserved). We render
      // THAT returned PDF in the same viewer — NOT a structured re-render. The
      // stream emits {i,n,text?,kind?} progress lines, then a final
      // {done,pdf_b64,…} line. We accumulate each line's `text` + structural
      // `kind` into `streamBlocks` so the live render BEHIND the loader matches
      // the document FORMAT (title/headings/bullets/paragraphs) while the PDF is
      // rebuilt; `streamText` keeps the flat join for the TTS fallback.
      translatePdf(fileId, target, {
        signal: controller.signal,
        onProgress: ({ i, n, text: blockText, kind, sourceLang, targetLang }) => {
          if (controller.signal.aborted) return;
          // EARLY same-language guard: if the backend reports the language pair
          // on a progress/page line and it's source === target, abort the stream
          // now (before the whole PDF is translated) and show the prompt.
          if (sourceLang && streamSameLanguage(sourceLang, targetLang, target, languages)) {
            guardSameLanguage(sourceLang);
            return;
          }
          // Remember the detected source as soon as it's known (also powers the
          // language pair caption / a future pre-flight guard). A target name
          // from the stream supersedes our resolved one.
          if (sourceLang) setSrcLang(sourceLang);
          if (targetLang) setTgtLang(targetLang);
          // Progress is 0-based page index k of n; show "k / n" (k+1 done).
          setProgress({ done: Math.min(i + 1, n || i + 1), total: n });
          if (blockText && blockText.trim()) {
            const body = blockText;
            // Default an absent/unknown kind to a body paragraph so older
            // backends (no `kind`) still render as readable prose.
            const blockKind = kind || "p";
            setStreamBlocks((prev) => [...prev, { text: body, kind: blockKind }]);
            setStreamText((prev) => (prev ? prev + "\n\n" + body : body));
          }
        },
        // STREAMED EXACT PAGE IMAGES — the real rendered document pages with the
        // translation applied so far (exact layout, progressively translating).
        // Store the latest snapshot per page (superseded URLs revoked inside
        // setPageImage); the live view renders these instead of the HTML reflow.
        // Ignore late arrivals after an abort so a stale snapshot can't leak.
        onPageImage: ({ page, url }) => {
          if (controller.signal.aborted) {
            try { URL.revokeObjectURL(url); } catch { /* no-op */ }
            return;
          }
          setPageImage(page, url);
        },
        onDone: ({ pdfBlob, sourceLang, targetLang }) => {
          if (controller.signal.aborted) return;
          // FINAL same-language safety net: a backend that only reports the pair
          // on the done line still triggers the guard here (rather than showing a
          // pointless "English → English" result). Remember the source first so
          // the message reads the real language name.
          if (sourceLang && streamSameLanguage(sourceLang, targetLang, target, languages)) {
            setSrcLang(sourceLang);
            guardSameLanguage(sourceLang);
            return;
          }
          if (sourceLang) setSrcLang(sourceLang);
          if (targetLang) setTgtLang(targetLang);
          const url = URL.createObjectURL(pdfBlob);
          pdfUrlRef.current = url;
          setTranslatedPdfBlob(pdfBlob);
          setTranslatedPdfUrl(url);
          setProgress((p) => (p.total ? { done: p.total, total: p.total } : p));
          setDone(true);
          setTranslating(false);
          abortRef.current = null;
        },
        onError: (msg) => {
          toast.error(typeof msg === "string" && msg ? msg : "Could not translate the PDF.");
          // Nothing translated to show — drop back to the original PDF.
          setShowTranslation(false);
          setTranslating(false);
          abortRef.current = null;
        },
      });
      return;
    }

    // STRUCTURED stream: the server extracts the document's structure and
    // streams one typed block per line. We do NOT send it the text — only the
    // target. Each block is placed at its index `i` so out-of-order arrival
    // (it shouldn't, but defensively) still renders in reading order.
    translateDocStream(fileId, target, {
      signal: controller.signal,
      onBlock: (b) => {
        if (controller.signal.aborted) return;
        // EARLY same-language guard: the structured stream carries the language
        // pair on every block (incl. the first), so if source === target we abort
        // on the first line and show the prompt instead of "English → English".
        if (b.source_lang && streamSameLanguage(b.source_lang, b.target_lang, target, languages)) {
          guardSameLanguage(b.source_lang);
          return;
        }
        setBlocks((prev) => {
          const next = prev.slice();
          // Grow sparsely to index i, then fill the gaps so .map stays dense.
          while (next.length <= b.i) next.push(null);
          next[b.i] = { type: b.type, text: b.text };
          return next;
        });
        // Feed the behind-loader faint panel too, so non-PDF docs also show the
        // translation streaming in under the loader card.
        if (b.text && b.text.trim()) {
          setStreamText((prev) => (prev ? prev + "\n\n" + b.text : b.text));
        }
        setProgress({ done: b.i + 1, total: b.n });
        if (b.source_lang) setSrcLang(b.source_lang);
        if (b.target_lang) setTgtLang(b.target_lang);
      },
      onDone: ({ n }) => {
        setProgress((p) => ({ done: n || p.done, total: n || p.total }));
        setDone(true);
        setTranslating(false);
        abortRef.current = null;
      },
      onError: (msg) => {
        toast.error(typeof msg === "string" && msg ? msg : "Could not translate.");
        setTranslating(false);
        abortRef.current = null;
      },
    });
  }, [hasText, translating, target, fileId, isPdf, srcLang, languages, guardSameLanguage, cancelStream, revokePdfUrl, revokeAllPageImages, setPageImage]);

  // Switching language cancels an in-flight stream + clears the half-done
  // translation so the next Translate starts clean. Persists the pick as the
  // last-used target so it sticks across files + sessions.
  const changeTarget = useCallback((v) => {
    reset();
    setTarget(v);
    writeLastTarget(v);
  }, [reset]);

  const toggleView = useCallback(() => setShowTranslation((v) => !v), []);

  // Dense list of arrived blocks (drop the streaming holes) for rendering,
  // joining, and the PDF payload.
  const filledBlocks = blocks.filter(Boolean);
  // Joined translated text — for the TTS reader (reads the translation) and as
  // a convenience for callers. "\n\n" mirrors the prior flat-stream join.
  // For PDFs we don't accumulate blocks (the render is the real PDF), so this
  // is empty on the PDF path; PDF TTS text is fetched lazily via
  // `getTranslatedText` below and cached.
  const translatedText = filledBlocks.map((b) => b.text).join("\n\n");
  // Back-compat language-pair object (some callers read `langs.source/target`).
  // NEVER expose a same-language pair (e.g. "English → English"): when the
  // detected source and target name to the same language we return null so no
  // caller renders a nonsensical pair — the same-language guard owns that case.
  const langs =
    (srcLang || tgtLang) &&
    !(srcLang && tgtLang && normalizeLangName(srcLang) === normalizeLangName(tgtLang))
      ? { source: srcLang, target: tgtLang }
      : null;

  // Lazily resolve the TRANSLATED text for the TTS reader.
  //  - NON-PDF: it's already the joined structured blocks (`translatedText`).
  //  - PDF: pdf.js isn't available to pull text out of the rendered PDF blob,
  //    so we fetch the translated text on demand from the structured stream
  //    (same target language) and cache it in a ref so repeated Play presses
  //    don't re-extract. Returns "" if nothing is available yet.
  const getTranslatedText = useCallback(async () => {
    if (!isPdf) return translatedText;
    if (pdfTextCacheRef.current != null) return pdfTextCacheRef.current;
    if (!fileId) return "";
    const parts = [];
    await translateDocStream(fileId, target, {
      onBlock: (b) => {
        if (b && (b.text || "").trim()) parts.push(b.text);
      },
      // onDone/onError: nothing extra to do — we resolve on stream end.
    });
    const joined = parts.join("\n\n");
    pdfTextCacheRef.current = joined;
    return joined;
  }, [isPdf, translatedText, fileId, target]);

  return {
    localeDefault,
    target,
    setTarget: changeTarget,
    // Full engine language list (+ load error) for the searchable picker.
    languages,
    languagesError,
    // Localized loader phrases for the branded loader (English fallback).
    loaderPhrases,
    blocks,
    translatedText,
    // Live translated text streamed in behind the loader card (both paths).
    streamText,
    // Live translated BLOCKS [{text, kind}] for the FORMATTED PDF live render
    // (styled by kind so the streaming page matches the document structure).
    streamBlocks,
    // `translation` kept as an alias of the joined text so any incidental
    // reader (e.g. the TTS source guard) keeps working.
    translation: translatedText,
    translating,
    done,
    progress,
    srcLang,
    tgtLang,
    langs,
    showTranslation,
    setShowTranslation,
    toggleView,
    run,
    reset,
    hasText,
    // PDF exact-copy path extras.
    isPdf,
    translatedPdfBlob,
    translatedPdfUrl,
    // STREAMED EXACT PAGE IMAGES (PDF live view): array indexed by 0-based page,
    // each an object URL of the real rendered page translated so far (latest
    // wins). The live PDF view renders these instead of the HTML reflow; empty
    // until the first snapshot arrives (then the view swaps from the warming
    // card to the pages). URLs are revoked on reset / re-run / unmount.
    pageImages,
    getTranslatedText,
    // SAME-LANGUAGE GUARD: a non-null message ("This document is already in
    // <Lang>.") when the chosen target equals the detected source — the panel
    // shows it inline and `clearSameLangMessage` dismisses it (also cleared on a
    // fresh run / language switch / file change).
    sameLangMessage,
    clearSameLangMessage: () => setSameLangMessage(null),
  };
}

// ── Document TTS "Listen" reader (left panel) ───────────────────────────────
// Reads the document's CURRENT-language text aloud. PRIMARY engine is the
// server-side NEURAL voice (POST /tts/speak → audio/wav, Piper): clear, human.
// We split the text into sentence chunks (chunkForSpeech), then play them
// gaplessly through ONE reusable <Audio> element — fetching chunk i's WAV,
// playing it, and PREFETCHING chunk i+1 while it plays. Each blob URL is
// revoked after it finishes so nothing leaks. The language sent to the endpoint
// is the BASE subtag of `bcp47` ("es-ES" → "es").
//
// FALLBACK: if the FIRST chunk fails because the language has no neural voice
// (HTTP 501 → NoNeuralVoiceError) OR a network error, we switch the WHOLE read
// to the browser's window.speechSynthesis (the previous implementation, kept
// intact below as `playSystem`/`speakSystemFrom`). A tiny label shows which
// engine is live: "Neural voice" vs "System voice".
//
// `text`  : the text to read (already chosen by the caller for the current view)
// `bcp47` : BCP-47 tag → base subtag for the neural `lang`; also voice matching
//           + utterance.lang on the fallback path
// `langName` : display name ("Spanish") for the control's label
// `resetKey` : when this changes (file / view / language), any in-flight speech
//              is cancelled so we never read stale text.
function DocTtsReader({ text, bcp47, langName, resetKey }) {
  // status: "idle" | "preparing" | "playing" | "paused". Declared first so the
  // hook order is stable regardless of engine support.
  const [status, setStatus] = useState("idle");
  // engine: which path is driving the CURRENT read. "neural" by default; flips
  // to "system" if neural is unavailable / the first chunk fails.
  const [engine, setEngine] = useState("neural");
  const [voicesReady, setVoicesReady] = useState(false);

  const synthSupported = ttsSupported();
  // The neural endpoint may have a voice for this language even when the
  // browser has none (and vice-versa); the control is useful if EITHER works.
  const baseLang = (bcp47 || "en").toLowerCase().split("-")[0];
  const neuralLikely = neuralTtsSupportsLang(baseLang);

  // ── Neural-path refs ──
  // One reusable Audio element for the whole read (gapless via src swap).
  const audioRef = useRef(null);
  // AbortController for in-flight /tts/speak fetches (Stop aborts them).
  const abortRef = useRef(null);
  // chunks[] for the current read + the index now playing.
  const chunksRef = useRef([]);
  const neuralIdxRef = useRef(0);
  // Prefetched next blob: { idx, blob } so chunk i+1 is ready when i ends.
  const prefetchRef = useRef(null);
  // The object URL currently set on the Audio element (revoked on advance/stop).
  const curUrlRef = useRef(null);
  // Set true by stop()/reset so async fetch handlers know the read is dead.
  const neuralStoppedRef = useRef(false);

  // ── System (fallback) path refs (unchanged behavior) ──
  // Guards so the chained `onend` queue knows when the user asked to stop.
  const stoppedRef = useRef(false);
  const queueRef = useRef([]);
  const idxRef = useRef(0);

  // Voices load asynchronously on Chrome/Edge — subscribe to onvoiceschanged
  // and also poll the initial list (some browsers populate synchronously).
  // Only relevant to the system fallback, but harmless to always wire.
  useEffect(() => {
    if (!synthSupported) return undefined;
    const synth = window.speechSynthesis;
    const sync = () => {
      const v = synth.getVoices();
      if (v && v.length > 0) setVoicesReady(true);
    };
    sync();
    synth.addEventListener?.("voiceschanged", sync);
    // Fallback for browsers exposing only the onX property.
    const prev = synth.onvoiceschanged;
    synth.onvoiceschanged = sync;
    return () => {
      synth.removeEventListener?.("voiceschanged", sync);
      synth.onvoiceschanged = prev || null;
    };
  }, [synthSupported]);

  // Lazily create (once) the shared Audio element.
  const getAudio = useCallback(() => {
    if (!audioRef.current && typeof Audio !== "undefined") {
      audioRef.current = new Audio();
    }
    return audioRef.current;
  }, []);

  // Revoke + forget the object URL currently on the Audio element.
  const revokeCurUrl = useCallback(() => {
    if (curUrlRef.current) {
      try { URL.revokeObjectURL(curUrlRef.current); } catch { /* no-op */ }
      curUrlRef.current = null;
    }
  }, []);

  // ── System (fallback) path: the prior implementation, intact ──
  // Speak the queued chunk at idxRef, chaining the next on `onend`. Each
  // utterance carries the language + best voice so multi-sentence reads stay
  // consistent.
  const speakSystemFrom = useCallback((voice) => {
    const synth = window.speechSynthesis;
    const queue = queueRef.current;
    if (idxRef.current >= queue.length) {
      setStatus("idle");
      return;
    }
    const u = new SpeechSynthesisUtterance(queue[idxRef.current]);
    u.lang = bcp47 || "en";
    if (voice) u.voice = voice;
    u.rate = 1;
    u.pitch = 1;
    u.onend = () => {
      if (stoppedRef.current) return;
      idxRef.current += 1;
      if (idxRef.current < queue.length) {
        speakSystemFrom(voice);
      } else {
        setStatus("idle");
      }
    };
    u.onerror = () => {
      // Swallow benign "interrupted"/"canceled" errors from our own cancel().
      if (stoppedRef.current) return;
      setStatus("idle");
    };
    synth.speak(u);
  }, [bcp47]);

  // Start (or restart) a SYSTEM read from the current `text`. Used as the
  // fallback when neural is unavailable. Sets engine="system" so the UI label
  // reflects the active voice.
  const playSystem = useCallback(() => {
    if (!synthSupported) {
      // No neural AND no browser speech — nothing we can do.
      setStatus("idle");
      return;
    }
    const synth = window.speechSynthesis;
    const chunks = chunkForSpeech(text);
    if (chunks.length === 0) {
      setStatus("idle");
      return;
    }
    stoppedRef.current = false;
    try { synth.cancel(); } catch { /* no-op */ }
    queueRef.current = chunks;
    idxRef.current = 0;
    const voice = pickBestVoice(synth.getVoices(), bcp47);
    setEngine("system");
    setStatus("playing");
    speakSystemFrom(voice);
  }, [synthSupported, text, bcp47, speakSystemFrom]);

  // ── Neural path ──
  // Fetch one chunk's WAV blob. Returns null if the read was stopped or the
  // chunk index is out of range. Throws on fetch failure (handled by caller).
  const fetchChunk = useCallback((idx) => {
    const chunks = chunksRef.current;
    if (idx < 0 || idx >= chunks.length) return Promise.resolve(null);
    const ctrl = abortRef.current;
    return ttsSpeak(chunks[idx], baseLang, { signal: ctrl?.signal });
  }, [baseLang]);

  // Play the chunk at neuralIdxRef using the given blob, wiring `ended` to
  // advance (consuming the prefetched next blob) and kicking off the prefetch
  // of the chunk after that. Declared as a ref-held function so the recursive
  // advance doesn't need the callback in its own dep array.
  const playNeuralRef = useRef(null);
  playNeuralRef.current = (blob) => {
    if (neuralStoppedRef.current) return;
    const audio = getAudio();
    if (!audio) { playSystem(); return; }

    revokeCurUrl();
    const url = URL.createObjectURL(blob);
    curUrlRef.current = url;
    audio.src = url;

    // Advance to the next chunk when this one finishes.
    audio.onended = () => {
      if (neuralStoppedRef.current) return;
      revokeCurUrl();
      const nextIdx = neuralIdxRef.current + 1;
      neuralIdxRef.current = nextIdx;
      if (nextIdx >= chunksRef.current.length) {
        setStatus("idle");
        return;
      }
      // Use the prefetched blob if it matches; otherwise fetch on demand.
      const pre = prefetchRef.current;
      prefetchRef.current = null;
      const nextBlobP =
        pre && pre.idx === nextIdx ? Promise.resolve(pre.blob) : fetchChunk(nextIdx);
      nextBlobP
        .then((b) => {
          if (neuralStoppedRef.current || !b) return;
          playNeuralRef.current(b);
        })
        .catch((err) => {
          if (neuralStoppedRef.current) return;
          if (err?.name === "AbortError") return;
          // Mid-read failure: stop cleanly (first-chunk failures fall back; a
          // failure partway through just ends the read).
          setStatus("idle");
        });
      // Prefetch the chunk AFTER next, for gapless continuation.
      const afterIdx = nextIdx + 1;
      if (afterIdx < chunksRef.current.length) {
        fetchChunk(afterIdx)
          .then((b) => {
            if (neuralStoppedRef.current || !b) return;
            prefetchRef.current = { idx: afterIdx, blob: b };
          })
          .catch(() => { /* prefetch miss → fetched on demand at advance */ });
      }
    };

    audio.onerror = () => {
      if (neuralStoppedRef.current) return;
      // A decode/playback error mid-read just ends it.
      setStatus("idle");
    };

    setStatus("playing");
    audio.play().catch((err) => {
      if (neuralStoppedRef.current) return;
      if (err?.name === "AbortError") return;
      setStatus("idle");
    });
  };

  // Start a fresh NEURAL read from `text`. Fetches chunk 0 (showing
  // "Preparing voice…"); on success plays it and prefetches chunk 1. If chunk 0
  // fails with NoNeuralVoiceError (501) or any network error, falls back to the
  // system voice for the whole read.
  const playNeural = useCallback(() => {
    const chunks = chunkForSpeech(text);
    if (chunks.length === 0) {
      setStatus("idle");
      return;
    }
    neuralStoppedRef.current = false;
    chunksRef.current = chunks;
    neuralIdxRef.current = 0;
    prefetchRef.current = null;
    abortRef.current = new AbortController();
    setEngine("neural");
    setStatus("preparing");

    fetchChunk(0)
      .then((blob) => {
        if (neuralStoppedRef.current || !blob) return;
        playNeuralRef.current(blob);
        // Prefetch chunk 1 while chunk 0 plays.
        if (chunks.length > 1) {
          fetchChunk(1)
            .then((b) => {
              if (neuralStoppedRef.current || !b) return;
              prefetchRef.current = { idx: 1, blob: b };
            })
            .catch(() => { /* prefetch miss is fine */ });
        }
      })
      .catch((err) => {
        if (neuralStoppedRef.current) return;
        if (err?.name === "AbortError") return;
        // 501 (no neural voice) OR network error → browser speech for the read.
        if (err instanceof NoNeuralVoiceError || err?.name === "NoNeuralVoiceError") {
          playSystem();
          return;
        }
        // Any other failure (server error, offline): try the system voice if we
        // have it; otherwise surface idle.
        if (synthSupported) {
          playSystem();
        } else {
          setStatus("idle");
        }
      });
  }, [text, fetchChunk, playSystem, synthSupported]);

  // ── Shared controls ──
  // Hard stop — tears down BOTH engines + resets UI. Memoized; used by Stop, by
  // the reset-on-change effect, and on unmount.
  const stop = useCallback(() => {
    // Neural teardown: flag, abort in-flight fetches, pause + clear audio,
    // revoke URLs, drop the queue/prefetch.
    neuralStoppedRef.current = true;
    try { abortRef.current?.abort(); } catch { /* no-op */ }
    abortRef.current = null;
    const audio = audioRef.current;
    if (audio) {
      try { audio.pause(); } catch { /* no-op */ }
      audio.onended = null;
      audio.onerror = null;
      try { audio.removeAttribute("src"); audio.load(); } catch { /* no-op */ }
    }
    revokeCurUrl();
    chunksRef.current = [];
    neuralIdxRef.current = 0;
    prefetchRef.current = null;

    // System teardown: cancel the utterance queue.
    if (synthSupported) {
      stoppedRef.current = true;
      queueRef.current = [];
      idxRef.current = 0;
      try { window.speechSynthesis.cancel(); } catch { /* no-op */ }
    }
    setStatus("idle");
  }, [synthSupported, revokeCurUrl]);

  // Play / Resume. Resumes a paused read (either engine) in place; otherwise
  // starts a fresh read on the PRIMARY (neural) engine — which self-falls-back
  // to system if neural is unavailable.
  const play = useCallback(() => {
    if (status === "paused") {
      if (engine === "neural") {
        const audio = audioRef.current;
        setStatus("playing");
        try { audio?.play(); } catch { /* no-op */ }
      } else if (synthSupported) {
        stoppedRef.current = false;
        try { window.speechSynthesis.resume(); } catch { /* no-op */ }
        setStatus("playing");
      }
      return;
    }
    // Fresh read. Prefer neural; playNeural falls back to system on failure.
    // If the browser has no speech AND neural is unsupported for this language,
    // there's nothing to do.
    if (!neuralLikely && !synthSupported) {
      setStatus("idle");
      return;
    }
    if (neuralLikely) {
      playNeural();
    } else {
      playSystem();
    }
  }, [status, engine, synthSupported, neuralLikely, playNeural, playSystem]);

  const pause = useCallback(() => {
    if (engine === "neural") {
      try { audioRef.current?.pause(); } catch { /* no-op */ }
    } else if (synthSupported) {
      try { window.speechSynthesis.pause(); } catch { /* no-op */ }
    }
    setStatus("paused");
  }, [engine, synthSupported]);

  // Cancel speech whenever the file / view / language changes (resetKey) so we
  // never keep reading text that's no longer on screen.
  useEffect(() => {
    stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  // Stop on unmount (panel collapsed / modal closed).
  useEffect(() => stop, [stop]);

  // The control is useful when EITHER engine can speak this language.
  const canSpeak = neuralLikely || synthSupported;
  if (!canSpeak) return null;

  const hasText = !!(text || "").trim();
  const preparing = status === "preparing";
  const playing = status === "playing";

  return (
    <div className="toolpanel__section toolpanel__tts">
      <div className="toolpanel__section-label">
        Listen{langName ? ` · ${langName}` : ""}
      </div>
      <div className="toolpanel__tts-row">
        {playing ? (
          <button
            type="button"
            className="btn btn--secondary btn--sm"
            onClick={pause}
            title="Pause reading"
          >
            <Icon name="pause" size={13} /> Pause
          </button>
        ) : (
          <button
            type="button"
            className="btn btn--secondary btn--sm"
            onClick={play}
            disabled={!hasText || preparing}
            title={
              preparing
                ? "Preparing the neural voice…"
                : status === "paused"
                  ? "Resume reading"
                  : "Read this document aloud"
            }
          >
            {preparing ? (
              <>
                <span className="doc-translated__spinner" aria-hidden="true" /> Preparing voice…
              </>
            ) : (
              <>
                <Icon name="play" size={13} /> {status === "paused" ? "Resume" : "Listen"}
              </>
            )}
          </button>
        )}
        <button
          type="button"
          className="chip-toggle"
          onClick={stop}
          disabled={status === "idle"}
          title="Stop reading"
        >
          <Icon name="square" size={12} /> Stop
        </button>
        {/* Active-engine label — only while something is loading/playing. */}
        {status !== "idle" && (
          <span
            className="toolpanel__tts-engine"
            title={
              engine === "neural"
                ? "Clear, human neural voice (server-side)."
                : "Your browser's built-in speech voice."
            }
          >
            <Icon name={engine === "neural" ? "sparkles" : "volume"} size={11} style={{ verticalAlign: "-1px", marginRight: 3 }} />
            {engine === "neural" ? "Neural voice" : "System voice"}
          </span>
        )}
      </div>
      <div className="toolpanel__hint" style={{ marginTop: 8 }}>
        {preparing
          ? "Preparing the voice — the first time a language is used it loads once, then it's instant."
          : "Reads the text currently shown in the document."}
      </div>
    </div>
  );
}

// Render one TYPED translated block as the right document element. `li` items
// get a bullet glyph + hanging indent; `title`/`h1`/`h2` are styled headings;
// `p` is a readable body paragraph. Unknown types fall back to a paragraph.
function DocBlock({ type, text }) {
  const body = (text || "").trim();
  if (!body) return null;
  switch (type) {
    case "title":
      return <h1 className="doc-translated__title">{body}</h1>;
    case "h1":
      return <h2 className="doc-translated__h1">{body}</h2>;
    case "h2":
      return <h3 className="doc-translated__h2">{body}</h3>;
    case "li":
      return (
        <div className="doc-translated__li">
          <span className="doc-translated__bullet" aria-hidden="true">•</span>
          <span className="doc-translated__li-text">{body}</span>
        </div>
      );
    case "p":
    default:
      return <p className="doc-translated__p">{body}</p>;
  }
}

// ── CENTER translated-image bar (over the main image stage) ─────────────────
// A thin status bar pinned to the top of the image stage while an in-image
// translation is showing (or running). Mirrors the document translator's bar:
// the language pair + a live "Translating image…" indicator on the left, and
// the Show original ⇄ Show translation toggle on the right. The TRANSLATED
// IMAGE itself is rendered by the parent (it swaps the <img> src to the
// translated object URL) — this component is just the controls that ride on
// top. The parent mounts it only when a translation exists or is in flight.
export function ImageTranslatedBar({ t }) {
  const { translating, done, langs, showTranslation, toggleView, translatedUrl } = t;

  const pair =
    langs && (langs.source || langs.target)
      ? `${langs.source || "Detected"} → ${langs.target || ""}`.trim()
      : null;
  const statusLabel = translating
    ? "Translating image…"
    : done
      ? "Translated"
      : null;
  // The toggle only makes sense once there's a translated image to flip TO.
  const canToggle = !!translatedUrl;

  return (
    <div
      className="img-translated__bar"
      // Inert affordance over the photo; never let a click bubble to the stage
      // (which would tuck the open tool away) or the lightbox (which closes).
      onClick={(e) => e.stopPropagation()}
    >
      <div className="img-translated__bar-left">
        {pair && <span className="doc-translated__pair">{pair}</span>}
        {statusLabel && (
          <span
            className={
              "doc-translated__progress" +
              (translating ? " doc-translated__progress--live" : "")
            }
          >
            {translating && <span className="doc-translated__spinner" aria-hidden="true" />}
            {statusLabel}
          </span>
        )}
      </div>
      {canToggle && (
        <button
          type="button"
          className="chip-toggle"
          onClick={toggleView}
          aria-pressed={showTranslation}
          title="Toggle original / translation"
        >
          <Icon name="refresh" size={12} />
          {showTranslation ? "Show original" : "Show translation"}
        </button>
      )}
    </div>
  );
}

// ── CENTER translated-document view ─────────────────────────────────────────
// Rendered in the MAIN CENTER document area (inside each doc modal body). When
// the shared translation state says "show translation", this replaces the
// original PDF/text render with a CLEAN, readable document page in the target
// language. The translation arrives as a STRUCTURED stream of TYPED blocks
// (title / h1 / h2 / li / p) which we render by type — large bold title,
// section headings, bulleted list items, and body paragraphs — so the page
// looks like a real formatted document, NOT a flat text blob. Blocks appear
// top-to-bottom as they stream in. A small header carries the language pair,
// live progress, and the Show original ⇄ Show translation toggle. The parent
// only mounts this when a translation exists or is in flight; the toggle itself
// lives here so it sits with the content it flips.
export function DocTranslatedView({ t }) {
  const {
    blocks, translating, done, progress, langs, showTranslation, toggleView,
    loaderPhrases,
  } = t;

  // Drop the streaming holes + empties; render what's arrived in order.
  const visibleBlocks = (blocks || []).filter((b) => b && (b.text || "").trim());
  // Once the first typed block has arrived, the STRUCTURED PAGE itself is the
  // readable live render (it fills in top-to-bottom). Before that — the warming
  // beat — we show the centered branded card. So the full-cover loader only
  // shows while there's nothing to read yet; once blocks land we swap to a
  // compact, non-covering branded strip so the translation stays readable as it
  // streams (no more card sitting over the text).
  const hasBlocks = visibleBlocks.length > 0;

  const pair =
    langs && (langs.source || langs.target)
      ? `${langs.source || "Detected"} → ${langs.target || ""}`.trim()
      : null;

  // Auto-scroll the page to the newest block as it streams, so the latest
  // translated text is always in view (watching the document become the new
  // language). Keyed on the block count so it fires per arrival.
  const scrollRef = useRef(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && translating) el.scrollTop = el.scrollHeight;
  }, [visibleBlocks.length, translating]);

  return (
    <div className="doc-translated" aria-label="Translated document">
      {/* Top bar: language pair + the Show original ⇄ Show translation toggle.
          WHILE translating, the compact branded strip below carries progress
          (logo + localized phrases + i/n bar), so we keep this bar minimal. */}
      <div className="doc-translated__bar">
        <div className="doc-translated__bar-left">
          {pair && <span className="doc-translated__pair">{pair}</span>}
          {!translating && done && (
            <span className="doc-translated__progress">Translated</span>
          )}
        </div>
        <button
          type="button"
          className="chip-toggle"
          onClick={toggleView}
          aria-pressed={showTranslation}
          title="Toggle original / translation"
        >
          <Icon name="refresh" size={12} />
          {showTranslation ? "Show original" : "Show translation"}
        </button>
      </div>

      {/* Compact branded progress STRIP — only once blocks are streaming in, so
          it sits ABOVE the readable page without covering it (the warming card
          handles the pre-first-block beat below). The `strip` variant of the
          loader renders just the slim branded bar (no live page / card). */}
      {translating && hasBlocks && (
        <DocTranslateLoader
          variant="strip"
          progress={progress}
          pair={pair}
          phrases={loaderPhrases}
        />
      )}

      <div className="doc-translated__scroll" ref={scrollRef}>
        <article className="doc-translated__page doc-translated__page--structured">
          {hasBlocks ? (
            visibleBlocks.map((b, i) => (
              <DocBlock key={i} type={b.type} text={b.text} />
            ))
          ) : (
            !translating && (
              <div className="doc-translated__placeholder">
                Pick a language and press Translate.
              </div>
            )
          )}
        </article>
      </div>

      {/* Warming beat: before the first block lands, the centered branded card
          (logo + cycling localized phrases + bar) signals "working" over this
          body. It disappears the moment blocks start streaming, handing off to
          the readable structured page + the compact strip above. */}
      {translating && !hasBlocks && (
        <DocTranslateLoader
          className="nk-xlate--overlay"
          progress={progress}
          pair={pair}
          phrases={loaderPhrases}
        />
      )}
    </div>
  );
}

// ── Document Translate + Listen panel (LEFT rail) ───────────────────────────
// The DOCUMENT tool panel, redesigned. The translated document itself now
// renders in the MAIN CENTER view (DocTranslatedView); this LEFT panel is the
// control surface:
//   - a searchable target-language picker (NLLB-200) + a Translate button that
//     STREAMS the whole document into the center (via the shared
//     `useDocTranslation` state the parent threads in as `t`),
//   - a Download button that saves the translated document as a STRUCTURED,
//     styled PDF (rendered server-side from the typed blocks) named after the
//     file + target language; enabled only once translation finishes,
//   - a "Listen" TTS reader (DocTtsReader) that reads the document's CURRENT
//     language aloud — the ORIGINAL text while the center shows the original,
//     the TRANSLATION (the joined typed blocks) while it shows the translation.
// There is NO translation TEXT block here anymore — the translation lives in
// the center. `t` is the shared translation hook from the parent; `doc` is the
// extracted-text query payload.
function DocTextToolPanel({ file, onClose, doc, docLoading, docError, t }) {
  const text = doc?.text || "";
  const hasText = !!text.trim();
  const truncated = !!doc?.truncated;
  const isPdf = !!t.isPdf;

  // For PDFs, the translation render is the REAL translated PDF (no structured
  // blocks), so there's no joined block text to read. We lazily extract the
  // translated TEXT for the TTS reader via the hook's `getTranslatedText`
  // (cached) and hold it in local state so the reader gets a plain string.
  const [pdfTtsText, setPdfTtsText] = useState("");
  useEffect(() => {
    if (!isPdf) return;
    // Only fetch once the user is actually viewing a finished translation.
    if (!(t.showTranslation && t.done)) return;
    let cancelled = false;
    t.getTranslatedText?.()
      .then((txt) => { if (!cancelled) setPdfTtsText(txt || ""); })
      .catch(() => { if (!cancelled) setPdfTtsText(""); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPdf, t.showTranslation, t.done, t.target, file.id]);

  // What the TTS reader should speak + the language it's in: the TRANSLATION
  // (the joined typed blocks for docs / the extracted PDF text for PDFs, in the
  // target language) when the center shows the translation AND we have some
  // translated text; otherwise the ORIGINAL document text. The original's
  // language is unknown up-front, so we fall back to the browser locale's tag
  // for it. The translation's language is exact.
  const translatedTtsSource = isPdf ? pdfTtsText : t.translatedText;
  const showingTranslation = t.showTranslation && !!(translatedTtsSource || "").trim();
  const ttsText = showingTranslation ? translatedTtsSource : text;
  const ttsBcp47 = showingTranslation
    ? bcp47FromFlores(t.target)
    : (t.localeDefault?.value ? bcp47FromFlores(t.localeDefault.value) : "en");
  // Display name for the target language: prefer the backend's reported name,
  // then the fetched language catalogue (accurate for the long tail), then the
  // static table fallback.
  const targetNameFromList = (t.languages || []).find((l) => l.code === t.target)?.name;
  const ttsLangName = showingTranslation
    ? (t.tgtLang || targetNameFromList || langNameFromFlores(t.target))
    : "Original";
  // Cancel speech whenever the file, the view (orig⇄translation), or the
  // language changes so we never keep reading text that's left the screen.
  const ttsResetKey = `${file.id}|${showingTranslation ? "t" : "o"}|${t.target}`;

  const [preparingPdf, setPreparingPdf] = useState(false);
  const downloadTranslation = useCallback(async () => {
    const base = (file.name || "document").replace(/\.[^./\\]+$/, "");
    const lang = t.tgtLang || langNameFromFlores(t.target) || "translation";

    // PDF path — the translated PDF IS the result (exact copy, text in place);
    // download the blob directly, NO render-from-blocks call.
    if (isPdf) {
      if (!t.translatedPdfBlob) {
        toast.error("Translate the PDF first.");
        return;
      }
      const url = URL.createObjectURL(t.translatedPdfBlob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${base} (${lang}).pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      return;
    }

    // NON-PDF path — render a STRUCTURED, styled PDF from the typed blocks
    // (title/headings/bullets/paragraphs) server-side. Shows a brief
    // "Preparing PDF…" state while the server renders.
    const blocks = (t.blocks || [])
      .filter((b) => b && (b.text || "").trim())
      .map((b) => ({ type: b.type, text: b.text }));
    if (!blocks.length) {
      toast.error("Translate the document first.");
      return;
    }
    setPreparingPdf(true);
    try {
      const srcLang = t.srcLang || "";
      const pdf = await renderTranslatedPdf(file.name || "document", blocks, srcLang, lang);
      const url = URL.createObjectURL(pdf);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${base} (${lang}).pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (e) {
      toast.error(
        (e && e.message) ? e.message : "Could not prepare the translated PDF.",
      );
    } finally {
      setPreparingPdf(false);
    }
  }, [isPdf, t.translatedPdfBlob, t.blocks, t.target, t.srcLang, t.tgtLang, file.name]);

  const progressLabel =
    t.progress.total > 0
      ? `Translating… ${t.progress.done} / ${t.progress.total}`
      : "Translating…";
  const hasTranslation = isPdf
    ? (t.done && !!t.translatedPdfUrl)
    : !!(t.translation || "").trim();
  // Download is only meaningful once a translation exists: the translated PDF
  // blob (PDF path) or a finished structured stream (non-PDF path).
  const canDownload = isPdf
    ? (t.done && !!t.translatedPdfBlob)
    : (t.done && (t.blocks || []).some((b) => b && (b.text || "").trim()));

  return (
    <aside className="toolpanel toolpanel--text toolpanel--doc" onClick={(e) => e.stopPropagation()} aria-label="Translate document">
      <header className="toolpanel__head">
        <Icon name="type" size={14} />
        <span className="toolpanel__title">Translate document</span>
        <button
          type="button"
          className="btn-icon toolpanel__collapse"
          onClick={onClose}
          aria-label="Collapse to bubble"
          title="Collapse to bubble"
        >
          <Icon name="chevronLeft" size={13} />
        </button>
      </header>

      <div className="toolpanel__body">
        {docLoading && (
          <div className="toolpanel__loading">
            <span className="skel skel--text" style={{ width: "92%" }} />
            <span className="skel skel--text" style={{ width: "85%", marginTop: 6 }} />
            <span className="skel skel--text" style={{ width: "70%", marginTop: 6 }} />
            <div className="toolpanel__hint" style={{ marginTop: 10 }}>Reading the document…</div>
          </div>
        )}

        {!docLoading && docError && (
          <div className="toolpanel__empty">
            <div className="toolpanel__empty-title">Couldn’t read the document</div>
            <div className="toolpanel__empty-body">{docError}</div>
          </div>
        )}

        {!docLoading && !docError && !hasText && (
          <div className="toolpanel__empty">
            <div className="toolpanel__empty-icon">
              <Icon name="type" size={22} strokeWidth={1.4} />
            </div>
            <div className="toolpanel__empty-title">No text found</div>
            <div className="toolpanel__empty-body">
              We couldn’t extract any readable text from this document.
            </div>
          </div>
        )}

        {!docLoading && !docError && hasText && (
          <>
            {truncated && (
              <div className="toolpanel__note" style={{ marginTop: 0, marginBottom: 12 }}>
                <Icon name="info" size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />
                This document is long — showing & translating the first part only.
              </div>
            )}

            {/* Translate the WHOLE document — STREAMS into the CENTER view. */}
            <div className="toolpanel__section toolpanel__translate">
              <div className="toolpanel__section-label">Translate to</div>
              <div className="toolpanel__translate-row">
                <LanguagePicker
                  value={t.target}
                  onChange={t.setTarget}
                  languages={t.languages}
                  loading={t.languages == null && !t.languagesError}
                  error={t.languagesError}
                />
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  onClick={t.run}
                  disabled={t.translating}
                >
                  {t.translating ? "Translating…" : t.done ? "Re-translate" : "Translate"}
                </button>
              </div>

              {/* SAME-LANGUAGE GUARD prompt — the document is already in the
                  chosen target, so we didn't translate. A small inline note (the
                  guard also fired a toast); picking another language clears it
                  via the hook's reset. */}
              {t.sameLangMessage && (
                <div className="toolpanel__note toolpanel__note--samelang" style={{ marginTop: 10 }}>
                  <Icon name="info" size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />
                  {t.sameLangMessage}
                </div>
              )}

              {t.translating && (
                <div className="toolpanel__hint" style={{ marginTop: 10 }}>
                  {progressLabel}
                </div>
              )}

              {/* Once a translation exists: show the language pair, a toggle
                  that flips the CENTER view, and a Download (.txt) button. The
                  translation TEXT itself is intentionally NOT here — it lives
                  in the center document view. */}
              {(hasTranslation || t.done) && (
                <div className="toolpanel__translation">
                  <div className="toolpanel__translation-head">
                    {t.langs && (t.langs.source || t.langs.target) ? (
                      <span className="toolpanel__lang-pill">
                        {t.langs.source} → {t.langs.target}
                      </span>
                    ) : (
                      <span />
                    )}
                    <button
                      type="button"
                      className="chip-toggle"
                      onClick={t.toggleView}
                      aria-pressed={t.showTranslation}
                      title="Toggle original / translation in the document"
                    >
                      <Icon name="refresh" size={12} />
                      {t.showTranslation ? "Show original" : "Show translation"}
                    </button>
                  </div>
                  <div className="toolpanel__controls" style={{ marginTop: 8, marginBottom: 0 }}>
                    <button
                      type="button"
                      className="chip-toggle"
                      onClick={downloadTranslation}
                      disabled={!canDownload || preparingPdf}
                      title={
                        canDownload
                          ? (isPdf
                              ? "Download the translated PDF — exact copy, text in place"
                              : "Download the translated document as a formatted PDF")
                          : "Finish translating to download the PDF"
                      }
                    >
                      {preparingPdf ? (
                        <>
                          <span className="doc-translated__spinner" aria-hidden="true" /> Preparing PDF…
                        </>
                      ) : (
                        <>
                          <Icon name="download" size={13} /> Download PDF
                        </>
                      )}
                    </button>
                  </div>
                  <div
                    className="toolpanel__note"
                    title={
                      isPdf
                        ? "Neural machine translation across 456 languages. The original PDF is returned with only its text translated in place — exact layout, images, and positions preserved."
                        : "Neural machine translation across 456 languages, streamed block-by-block with the document's structure preserved."
                    }
                  >
                    <Icon name="info" size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />
                    Neural translation · 456 languages
                  </div>
                </div>
              )}
            </div>

            {/* Listen — reads the CURRENT-language text aloud (browser TTS). */}
            <DocTtsReader
              text={ttsText}
              bcp47={ttsBcp47}
              langName={ttsLangName}
              resetKey={ttsResetKey}
            />
          </>
        )}
      </div>
    </aside>
  );
}

// ── OCR overlay (boxes over the rendered image) ─────────────────────────────
// Rendered by the parent INSIDE the image stage (a position:relative wrapper
// that hugs the <img>). Boxes are normalized 0..1, so we just express them
// as percentages — they track the rendered image at any scale because the
// stage is sized to the image. `activeIdx` highlights the hovered line's box.
export function OcrOverlay({ lines, activeIdx, onHoverBox }) {
  if (!lines || lines.length === 0) return null;
  return (
    <div
      className="ocr-overlay"
      aria-hidden="true"
      // A click on a box is inert (the boxes are hover affordances); stop
      // it bubbling to the stage, which would tuck the Text panel away.
      onClick={(e) => e.stopPropagation()}
    >
      {lines.map((ln, i) => (
        <div
          key={i}
          className={"ocr-box" + (activeIdx === i ? " ocr-box--active" : "")}
          style={{
            left: `${ln.x * 100}%`,
            top: `${ln.y * 100}%`,
            width: `${ln.w * 100}%`,
            height: `${ln.h * 100}%`,
          }}
          onMouseEnter={() => onHoverBox && onHoverBox(i)}
          onMouseLeave={() => onHoverBox && onHoverBox(null)}
          title={ln.text}
        />
      ))}
    </div>
  );
}

// ── Public hook: OCR state for an image, gated on the Text tool being open ──
// Kept as a hook so the parent can render the overlay (which lives in the
// image stage) AND the panel (which lives in the rail) from one source of
// truth. Unconditional; the query is `enabled` only when needed.
export function useImageOcr(fileId, isImage, enabled) {
  const q = useQuery({
    queryKey: ["ocr", fileId],
    queryFn: () => ocrImage(fileId),
    enabled: !!fileId && !!isImage && !!enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
  return q;
}

// ── Public hook: full document text, gated on the Translate tool being open ─
// The document analogue of useImageOcr. Fires GET /images/{id}/text only when
// the doc preview is open AND the Translate bubble is active, so opening a doc
// never extracts text until the user asks. Cached per file id in TanStack
// Query, so re-opening the tool is instant.
export function useDocumentText(fileId, enabled) {
  const q = useQuery({
    queryKey: ["doctext", fileId],
    queryFn: () => fetchDocumentText(fileId),
    enabled: !!fileId && !!enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
  return q;
}

// ── The composed rail surface the parent renders ───────────────────────────
// Renders the rail bubbles + the active tool's panel (info/text). Comments
// is handled by the parent's existing CommentPanel (driven by the same
// `active` state) so it stays one-at-a-time with these. The OCR overlay is
// rendered separately by the parent via <OcrOverlay> + useImageOcr above.
export function ImageToolRail({
  file,
  active,
  onSelect,
  commentCount,
  onBestOf,
  ocrQuery,
  showBoxes,
  onToggleBoxes,
  activeLineIdx,
  onHoverLine,
  translation,
}) {
  const ocrErrorMsg =
    ocrQuery?.error?.detail || ocrQuery?.error?.message || (ocrQuery?.isError ? "OCR failed." : null);
  return (
    <>
      <ToolRail active={active} onSelect={onSelect} commentCount={commentCount} onBestOf={onBestOf} />
      {active === "info" && <InfoToolPanel file={file} onClose={() => onSelect("info")} />}
      {active === "text" && (
        <TextToolPanel
          file={file}
          onClose={() => onSelect("text")}
          ocr={ocrQuery?.data}
          ocrLoading={!!ocrQuery?.isLoading && !!ocrQuery?.isFetching}
          ocrError={ocrErrorMsg}
          showBoxes={showBoxes}
          onToggleBoxes={onToggleBoxes}
          activeLineIdx={activeLineIdx}
          onHoverLine={onHoverLine}
          t={translation}
        />
      )}
    </>
  );
}

// ── The composed rail surface for DOCUMENT previews ─────────────────────────
// Same bubble-rail + one-panel-at-a-time UX as ImageToolRail, but tailored for
// documents: the bubbles are Comments / File info / Translate (no "Best of",
// no OCR boxes — documents have no raster to overlay). The Translate bubble
// (id "text", so it reuses the parent's existing rail-toggle state machine)
// opens DocTextToolPanel, which drives the shared translation state (`t`,
// instantiated once by the parent so the CENTER view + this panel share it) and
// the TTS reader. Comments is still owned by the parent's CommentPanel (driven
// by the shared `active` state) so it stays mutually exclusive with these.
export function DocToolRail({
  file,
  active,
  onSelect,
  commentCount,
  docQuery,
  translation,
}) {
  const docErrorMsg =
    docQuery?.error?.detail || docQuery?.error?.message ||
    (docQuery?.isError ? "Could not read this document." : null);
  const bubbles = [
    { id: "comments", icon: "message", label: "Comments", badge: commentCount },
    { id: "info", icon: "info", label: "File info" },
    { id: "text", icon: "type", label: "Translate document" },
  ];
  return (
    <>
      <ToolRail active={active} onSelect={onSelect} commentCount={commentCount} bubbles={bubbles} />
      {active === "info" && <InfoToolPanel file={file} onClose={() => onSelect("info")} />}
      {active === "text" && (
        <DocTextToolPanel
          file={file}
          onClose={() => onSelect("text")}
          doc={docQuery?.data}
          docLoading={!!docQuery?.isLoading && !!docQuery?.isFetching}
          docError={docErrorMsg}
          t={translation}
        />
      )}
    </>
  );
}
