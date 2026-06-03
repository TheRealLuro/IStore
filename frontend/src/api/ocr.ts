/* OCR + translate for the big image preview's "Text" tool bubble.
   Backend: backend/api/ocr.py (authed + owner-scoped). */
import { api, API_BASE_URL } from "./client";

export interface OcrLine {
  text: string;
  /** All normalized 0..1 against the SOURCE image's pixel size, so the
   *  FE can place boxes on the rendered <img> after accounting for
   *  object-fit/contain scaling. */
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface OcrResult {
  text: string;
  lines: OcrLine[];
  /** Source image pixel dimensions (the normalization basis). */
  width: number;
  height: number;
  /** "florence2-ocr-with-region" (boxes) or "florence2-ocr" (text only). */
  engine: string;
  /** True when per-line boxes are present. */
  boxes: boolean;
}

export interface TranslateResult {
  source_lang: string;
  target_lang: string;
  original: string;
  translation: string;
  engine: string;
  /** Plain-language note on the translation method + its limitation. */
  note: string;
}

export interface DocumentTextResult {
  /** Full extracted document text (capped server-side at 200k chars). */
  text: string;
  /** len(text) actually returned. */
  chars: number;
  /** Best-effort AI-ish topic for the document, or null. */
  topic: string | null;
  /** True when the source was longer than the cap and was clipped. */
  truncated: boolean;
}

/** One supported translation target language. The backend
 *  (GET /translate/languages, backend/api/translate_langs.py) returns the FULL
 *  set the engine can produce — `code` is an engine-accepted target code
 *  (a plain ISO-ish code like "es", "de", "zh-tw") that resolves cleanly, and
 *  `name` is the English display name. */
export interface TranslateLanguage {
  /** Engine-accepted target code — send this verbatim as `target`. */
  code: string;
  /** English display name, e.g. "Spanish". */
  name: string;
}

// One-time client cache for the language catalogue. It's static
// (engine-derived, name-sorted, deduped) so a single fetch per page load is
// plenty; subsequent callers reuse the same in-flight promise / resolved list.
let _languagesPromise: Promise<TranslateLanguage[]> | null = null;

/** Fetch the FULL list of supported translation target languages from
 *  GET /translate/languages as `[{code, name}]`, sorted by name. Public
 *  metadata (no auth needed), but goes through the shared api client so the
 *  session cookie rides along uniformly. Cached for the lifetime of the page:
 *  the first call fetches; every later call resolves to the same list (and
 *  concurrent callers share the one in-flight request). A failed fetch clears
 *  the cache so a later call can retry. */
export function fetchTranslateLanguages(): Promise<TranslateLanguage[]> {
  if (!_languagesPromise) {
    _languagesPromise = api
      .get<TranslateLanguage[]>(`/translate/languages`)
      .then((list) => (Array.isArray(list) ? list : []))
      .catch((e) => {
        // Drop the cached rejection so the next caller can try again.
        _languagesPromise = null;
        throw e;
      });
  }
  return _languagesPromise;
}

// ── Localized loader phrases ────────────────────────────────────────────────
// The translate loaders (DocTranslateLoader) cycle whimsical "scribe" phrases
// while a translation streams. GET /translate/loader-phrases?lang=<code> returns
// those phrases TRANSLATED into the target language, so the funny text shows in
// the language the user is translating TO. English is the base; the FE falls
// back to its built-in SCRIBE_PHRASES when the fetch fails or returns empty.

/** Response of GET /translate/loader-phrases — the loader phrases in `lang`. */
export interface LoaderPhrasesResult {
  /** The language code the phrases are in (echoes the request). */
  lang: string;
  /** The translated loader phrases (may be empty → caller falls back). */
  phrases: string[];
}

// Per-language client cache. The phrase set for a language is static, so a
// single fetch per language per page load is plenty; concurrent callers share
// the one in-flight promise. A failed fetch drops its cache entry so a later
// selection can retry. Keyed by the lowercased language code.
const _loaderPhrasesCache = new Map<string, Promise<string[]>>();

/** Fetch the loader phrases translated into `lang` from
 *  GET /translate/loader-phrases?lang=<code>, resolving to the `phrases` array
 *  (or `[]` on a malformed body). Cached per-language for the page lifetime;
 *  concurrent callers share the in-flight request, and a rejected fetch clears
 *  its cache entry so the next selection can retry. Goes through the shared api
 *  client so the session cookie rides along uniformly. Intended to be called on
 *  an ACTUAL language selection (not while typing-to-filter). */
export function fetchLoaderPhrases(lang: string): Promise<string[]> {
  const key = String(lang || "").toLowerCase().trim();
  if (!key) return Promise.resolve([]);
  let p = _loaderPhrasesCache.get(key);
  if (!p) {
    p = api
      .get<LoaderPhrasesResult>(
        `/translate/loader-phrases?lang=${encodeURIComponent(key)}`,
      )
      .then((r) => {
        const out =
          r && Array.isArray(r.phrases)
            ? r.phrases.filter((s) => typeof s === "string" && s.trim())
            : [];
        // If the backend returned the ENGLISH base verbatim — which happens when
        // the translation engine was cold/busy/evicted and it fell back to
        // English — do NOT keep it cached. Drop the entry so the next language
        // selection re-fetches and gets the REAL localized set once the engine
        // is warm. ("Studying" is the English base's first phrase; this code path
        // is only reached for non-English targets, so it's a safe sentinel.)
        if (out.length && out[0] === "Studying") {
          _loaderPhrasesCache.delete(key);
        }
        return out;
      })
      .catch((e) => {
        // Drop the cached rejection so the next selection can try again.
        _loaderPhrasesCache.delete(key);
        throw e;
      });
    _loaderPhrasesCache.set(key, p);
  }
  return p;
}

/** Run OCR over an owned image. Slow (Florence-2 inference) — callers
 *  should show a spinner; React Query caches the result per image id. */
export const ocrImage = (imageId: string): Promise<OcrResult> =>
  api.get<OcrResult>(`/images/${imageId}/ocr`);

/** Translate text (typically the OCR output) to `target` (ISO code or
 *  language name; defaults to English server-side). 503 when no
 *  translation model is available in the deployment. */
export const translateText = (
  imageId: string,
  text: string,
  target = "en",
): Promise<TranslateResult> =>
  api.post<TranslateResult>(`/images/${imageId}/translate`, { text, target });

/** Extract the FULL text of an owned DOCUMENT (PDF / docx / xlsx / pptx /
 *  plaintext / code …) so the Translate tool can translate the whole thing.
 *  Backend: backend/api/doctext.py (authed + owner-scoped). The text is
 *  capped at 200k chars server-side; `truncated` says when it was clipped. */
export const fetchDocumentText = (
  imageId: string,
): Promise<DocumentTextResult> =>
  api.get<DocumentTextResult>(`/images/${imageId}/text`);

/** One translated chunk pushed by the streaming endpoint. */
export interface TranslateStreamChunk {
  /** 0-based chunk index. */
  i: number;
  /** Total number of chunks the document was split into. */
  n: number;
  /** The translated text for this chunk. */
  text: string;
  /** Human name of the detected source language (same for every chunk). */
  source_lang: string;
  /** Human name of the target language (same for every chunk). */
  target_lang: string;
}

/** Callbacks + abort signal for the streaming translate reader. */
export interface TranslateStreamHandlers {
  /** Fired once per translated chunk, in order, as it arrives. */
  onChunk?: (chunk: TranslateStreamChunk) => void;
  /** Fired once when the stream signals completion. */
  onDone?: (info: { n: number }) => void;
  /** Fired on a server `{error}` line OR a thrown/network/abort failure. */
  onError?: (message: string) => void;
  /** Abort the in-flight fetch (close panel / re-translate). */
  signal?: AbortSignal;
}

// Legacy localStorage JWT keys — same transitional Bearer support the shared
// api client (./client) applies. Cookie-auth users have none of these; the
// `credentials: "include"` below ships the HttpOnly session cookie instead.
const LEGACY_TOKEN_KEYS = ["neuthek.jwt", "istore.jwt"];
function legacyBearer(): string | null {
  try {
    for (const k of LEGACY_TOKEN_KEYS) {
      const v = localStorage.getItem(k);
      if (v) return v;
    }
  } catch {
    /* private-browsing localStorage can throw */
  }
  return null;
}

/** Stream a WHOLE-document translation from POST
 *  /images/{id}/translate-stream (backend/api/translate_stream.py). The
 *  response is `application/x-ndjson`: one JSON object per line. We read the
 *  body with a streaming reader and dispatch each line:
 *    - a chunk line  → onChunk({i, n, text, source_lang, target_lang})
 *    - the done line  → onDone({n})
 *    - an error line  → onError(message)  (and we stop)
 *  Mirrors the shared api client's auth (credentials:"include" cookie +
 *  transitional legacy Bearer). Resolves when the stream ends (done, error,
 *  or abort); it never throws — failures go to onError. Pass `signal` from an
 *  AbortController to cancel an in-flight stream. */
export async function translateStream(
  imageId: string,
  text: string,
  target: string,
  { onChunk, onDone, onError, signal }: TranslateStreamHandlers = {},
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const bearer = legacyBearer();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/images/${imageId}/translate-stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({ text, target }),
      credentials: "include",
      signal,
    });
  } catch (e) {
    // Aborts surface here as an AbortError; swallow them (the caller asked
    // to stop) and report any real network failure.
    if ((e as { name?: string })?.name === "AbortError") return;
    onError?.((e as Error)?.message || "Could not reach the translator.");
    return;
  }

  if (!res.ok || !res.body) {
    let detail = res.statusText || "Translation failed.";
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 503) {
      detail = "Translation isn't available in this build.";
    }
    onError?.(detail);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  // Did we see a terminating line (done/error)? Did any chunk arrive? Used
  // after the loop to clear the caller's spinner even when the server closes
  // the stream WITHOUT a final {done}/{error} line (crash / proxy timeout /
  // truncation) — otherwise `translating` would hang true forever.
  let settled = false;
  let sawContent = false;

  // Parse one NDJSON line. Returns true when the stream should stop
  // (done/error line).
  const handleLine = (line: string): boolean => {
    const trimmed = line.trim();
    if (!trimmed) return false;
    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return false; // ignore a malformed/partial line defensively
    }
    if (typeof obj.error === "string") {
      onError?.(obj.error);
      settled = true;
      return true;
    }
    if (obj.done === true) {
      onDone?.({ n: typeof obj.n === "number" ? obj.n : 0 });
      settled = true;
      return true;
    }
    if (typeof obj.text === "string") {
      sawContent = true;
      onChunk?.(obj as unknown as TranslateStreamChunk);
    }
    return false;
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl = buf.indexOf("\n");
      while (nl !== -1) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (handleLine(line)) return; // done or error — stop reading
        nl = buf.indexOf("\n");
      }
    }
    // Flush any trailing partial line (no terminating newline).
    buf += decoder.decode();
    if (buf.trim() && handleLine(buf)) return;
    // Stream closed with no terminating line: settle so the caller's spinner
    // clears. Keep whatever streamed (onDone) when content arrived; otherwise
    // report the abnormal end.
    if (!settled) {
      if (sawContent) onDone?.({ n: 0 });
      else onError?.("The translation ended unexpectedly.");
    }
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") return;
    onError?.((e as Error)?.message || "Translation stream interrupted.");
  }
}

// ── Structured document translation (preserves title/headings/bullets) ──────
// The newer document-translation path. Instead of a flat text stream, the
// server extracts the document's STRUCTURE itself and streams ONE typed block
// per line, in reading order. The FE renders each block by `type` so the
// translated page looks like a real formatted document (title, headings,
// bullets, paragraphs) rather than a wall of text. The FE does NOT send the
// text — only the target language; the server reads the document by id.

/** One typed, translated structural block pushed by translate-doc-stream. */
export interface TranslateDocBlock {
  /** 0-based block index (its position in reading order). */
  i: number;
  /** Total number of blocks the document was split into. */
  n: number;
  /** The block's structural role. */
  type: "title" | "h1" | "h2" | "p" | "li";
  /** The translated text for this block. */
  text: string;
  /** Human name of the detected source language (same for every block). */
  source_lang: string;
  /** Human name of the target language (same for every block). */
  target_lang: string;
}

/** Callbacks + abort signal for the structured-doc translate reader. */
export interface TranslateDocStreamHandlers {
  /** Fired once per typed block, in order, as it arrives. */
  onBlock?: (block: TranslateDocBlock) => void;
  /** Fired once when the stream signals completion. */
  onDone?: (info: { n: number }) => void;
  /** Fired on a server `{error}` line OR a thrown/network/abort failure. */
  onError?: (message: string) => void;
  /** Abort the in-flight fetch (close panel / re-translate). */
  signal?: AbortSignal;
}

/** Stream a STRUCTURED whole-document translation from POST
 *  /images/{id}/translate-doc-stream. The server extracts the document's
 *  structure and returns `application/x-ndjson`: one JSON object per line —
 *    - a block line → onBlock({i, n, type, text, source_lang, target_lang})
 *    - the done line → onDone({n})
 *    - an error line → onError(message)  (and we stop)
 *  Mirrors translateStream's auth EXACTLY (credentials:"include" session cookie
 *  + transitional legacy Bearer) and reuses the same line-by-line reader.
 *  Resolves when the stream ends (done, error, or abort); it never throws —
 *  failures go to onError. Pass `signal` from an AbortController to cancel. */
export async function translateDocStream(
  imageId: string,
  target: string,
  { onBlock, onDone, onError, signal }: TranslateDocStreamHandlers = {},
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const bearer = legacyBearer();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  let res: Response;
  try {
    res = await fetch(
      `${API_BASE_URL}/images/${imageId}/translate-doc-stream`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ target }),
        credentials: "include",
        signal,
      },
    );
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") return;
    onError?.((e as Error)?.message || "Could not reach the translator.");
    return;
  }

  if (!res.ok || !res.body) {
    let detail = res.statusText || "Translation failed.";
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 503) {
      detail = "Translation isn't available in this build.";
    }
    onError?.(detail);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  // See translateStream: settle the caller even if the server closes the
  // stream without a final {done}/{error} line so the spinner can't hang.
  let settled = false;
  let sawContent = false;

  // Parse one NDJSON line. Returns true when the stream should stop
  // (done/error line).
  const handleLine = (line: string): boolean => {
    const trimmed = line.trim();
    if (!trimmed) return false;
    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return false; // ignore a malformed/partial line defensively
    }
    if (typeof obj.error === "string") {
      onError?.(obj.error);
      settled = true;
      return true;
    }
    if (obj.done === true) {
      onDone?.({ n: typeof obj.n === "number" ? obj.n : 0 });
      settled = true;
      return true;
    }
    // A block line carries both a type and text.
    if (typeof obj.text === "string" && typeof obj.type === "string") {
      sawContent = true;
      onBlock?.(obj as unknown as TranslateDocBlock);
    }
    return false;
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl = buf.indexOf("\n");
      while (nl !== -1) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (handleLine(line)) return; // done or error — stop reading
        nl = buf.indexOf("\n");
      }
    }
    // Flush any trailing partial line (no terminating newline).
    buf += decoder.decode();
    if (buf.trim() && handleLine(buf)) return;
    // Stream closed with no terminating line: settle so the spinner clears.
    // Keep whatever blocks streamed (onDone) when content arrived; otherwise
    // report the abnormal end.
    if (!settled) {
      if (sawContent) onDone?.({ n: 0 });
      else onError?.("The translation ended unexpectedly.");
    }
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") return;
    onError?.((e as Error)?.message || "Translation stream interrupted.");
  }
}

// ── Exact-copy PDF translation (text translated IN PLACE on the real PDF) ────
// The PDF analogue of the structured-doc translator. Instead of re-rendering
// the document from extracted blocks (a "clean re-render"), this returns the
// ORIGINAL PDF with ONLY its text translated in place — exact layout, images,
// and positions preserved. POST {target} to /images/{id}/translate-pdf; the
// response is `application/x-ndjson`:
//   - progress line: {"i":k,"n":total}
//   - final line:    {"done":true,"pdf_b64":"<base64 PDF>","source_lang":..,
//                     "target_lang":..}
//   - error line:    {"error":".."}
// HTTP 415 {"detail":"not_a_pdf"} when the row isn't a PDF.
//
// The stream ALSO emits PAGE-SNAPSHOT lines — the REAL rendered document pages
// with the translation applied so far (exact layout, progressively translating):
//   - page line: {"i":k,"n":n,"page":<0-based>,"pages":<total>,
//                 "page_png_b64":"<base64 PNG of the rendered page>"}
// These let the live view show the actual document (exact format) filling in,
// instead of an HTML reflow approximation. A page snapshot carries the same
// {i,n} as a progress line, so the parser checks for `page_png_b64` FIRST.

/** The structural role a translated PDF block plays — same vocabulary as the
 *  structured-doc stream, so the live render can style each block to match the
 *  real document (large title, headings, bullets, body paragraphs). */
export type PdfBlockKind = "title" | "h1" | "h2" | "p" | "li";

/** Callbacks + abort signal for the exact-copy PDF translate stream. */
export interface TranslatePdfHandlers {
  /** Fired once per progress line as blocks/pages are translated. Each line now
   *  also carries the translated block `text` (when present) plus its structural
   *  `kind` (title/h1/h2/p/li) so callers can stream the translation in BEHIND
   *  the loader — FORMATTED to match the document — while the PDF is rebuilt.
   *  `kind` is absent on older backends; callers should default to "p". */
  onProgress?: (p: {
    i: number;
    n: number;
    text?: string;
    kind?: PdfBlockKind;
    /** Human name of the detected SOURCE language, when the backend reports it
     *  early (on a progress/page line) rather than only on the final line. Absent
     *  on backends that only attach the language pair to the `done` line — callers
     *  must tolerate undefined and fall back to the `onDone` pair. Surfaced here so
     *  a same-language run (source === target) can be detected and aborted before
     *  the whole document is translated. */
    sourceLang?: string;
    /** Human name of the TARGET language, same early-availability caveat. */
    targetLang?: string;
  }) => void;
  /** Fired once per PAGE-SNAPSHOT line with the REAL rendered document page (the
   *  translation applied so far) as an object URL. `page` is the 0-based page
   *  index, `pages` the total page count, `url` an object URL for an image/png
   *  Blob decoded from `page_png_b64`. The caller OWNS the URL — it must revoke
   *  superseded/old URLs (e.g. when a newer snapshot for the same page arrives,
   *  on reset, and on unmount). Lets the live view render the actual document
   *  (exact layout) progressively translating instead of an HTML reflow. */
  onPageImage?: (p: { page: number; pages: number; url: string }) => void;
  /** Fired once on the final line with the translated PDF as a Blob. */
  onDone?: (result: {
    pdfBlob: Blob;
    sourceLang: string;
    targetLang: string;
  }) => void;
  /** Fired on a server `{error}` line, an HTTP 415 (not a PDF), or a
   *  thrown/network failure. */
  onError?: (message: string) => void;
  /** Abort the in-flight stream (re-run / close panel / file change). */
  signal?: AbortSignal;
}

/** Decode a base64 string into an ArrayBuffer (the translated PDF bytes).
 *  atob yields a binary string; we widen each char code to a byte. Returning
 *  the backing ArrayBuffer (rather than the Uint8Array view) keeps the Blob
 *  constructor's BlobPart type happy under lib.dom's ArrayBufferLike split. */
function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const len = bin.length;
  const buf = new ArrayBuffer(len);
  const view = new Uint8Array(buf);
  for (let i = 0; i < len; i += 1) view[i] = bin.charCodeAt(i);
  return buf;
}

/** Stream an EXACT-COPY PDF translation from POST /images/{id}/translate-pdf.
 *  Reads the `application/x-ndjson` body line-by-line (same reader as
 *  translateDocStream), dispatching:
 *    - a page-snapshot line → onPageImage({page, pages, url})  (the REAL rendered
 *      document page translated so far — base64 `page_png_b64` decoded into an
 *      image/png Blob object URL the caller owns + must revoke). Checked FIRST
 *      because a snapshot line carries the same {i,n} as a progress line.
 *    - a progress line → onProgress({i, n, text?, kind?})  (text + structural
 *      kind let the caller stream a FORMATTED live render behind the loader)
 *    - the final line  → onDone({pdfBlob, sourceLang, targetLang})  (the
 *      base64 `pdf_b64` decoded into an application/pdf Blob)
 *    - an error line OR HTTP 415 → onError(message)
 *  Mirrors translateDocStream's auth EXACTLY (credentials:"include" session
 *  cookie + transitional legacy Bearer). Resolves when the stream ends (done,
 *  error, or abort); it never throws — failures go to onError. Pass `signal`
 *  from an AbortController to cancel. */
export async function translatePdf(
  imageId: string,
  target: string,
  { onProgress, onPageImage, onDone, onError, signal }: TranslatePdfHandlers = {},
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const bearer = legacyBearer();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/images/${imageId}/translate-pdf`, {
      method: "POST",
      headers,
      body: JSON.stringify({ target }),
      credentials: "include",
      signal,
    });
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") return;
    onError?.((e as Error)?.message || "Could not reach the translator.");
    return;
  }

  if (!res.ok || !res.body) {
    let detail = res.statusText || "Translation failed.";
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 415) {
      detail = "This file isn’t a PDF.";
    } else if (res.status === 503) {
      detail = "Translation isn’t available in this build.";
    }
    onError?.(detail);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  // Did we see the terminating line (the final pdf_b64, or an error)? Used
  // after the loop to fail cleanly if the server closes the stream WITHOUT
  // the final PDF (crash / proxy timeout / truncation) — otherwise the
  // caller's `translating` flag would hang true forever with no PDF to show.
  let settled = false;

  // Read a non-empty string field off a parsed line (or undefined). Used to
  // surface an EARLY source/target language pair when the backend attaches it to
  // a progress/page line (so a same-language run can be caught before the whole
  // PDF is translated); both are commonly absent until the final line.
  const str = (v: unknown): string | undefined =>
    typeof v === "string" && v.trim() ? v : undefined;

  // Parse one NDJSON line. Returns true when the stream should stop
  // (done/error line).
  const handleLine = (line: string): boolean => {
    const trimmed = line.trim();
    if (!trimmed) return false;
    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return false; // ignore a malformed/partial line defensively
    }
    if (typeof obj.error === "string") {
      onError?.(obj.error);
      settled = true;
      return true;
    }
    if (obj.done === true && typeof obj.pdf_b64 === "string") {
      try {
        const pdfBuf = base64ToArrayBuffer(obj.pdf_b64);
        const pdfBlob = new Blob([pdfBuf], { type: "application/pdf" });
        onDone?.({
          pdfBlob,
          sourceLang:
            typeof obj.source_lang === "string" ? obj.source_lang : "",
          targetLang:
            typeof obj.target_lang === "string" ? obj.target_lang : "",
        });
      } catch {
        onError?.("Could not decode the translated PDF.");
      }
      settled = true;
      return true;
    }
    // A PAGE-SNAPSHOT line carries the REAL rendered page (translated so far) as
    // base64 PNG. It ALSO carries {i,n} (same as a progress line), so it MUST be
    // handled BEFORE the generic progress branch below, or it'd be mistaken for
    // a plain progress tick and the image dropped. Decode to an image/png Blob
    // object URL and hand it off; the caller owns + revokes the URL. A decode
    // failure is swallowed (the stream keeps going — a missed snapshot is not
    // fatal, the final PDF still arrives). It does NOT stop the stream.
    if (typeof obj.page_png_b64 === "string") {
      try {
        const pageBuf = base64ToArrayBuffer(obj.page_png_b64);
        const pageBlob = new Blob([pageBuf], { type: "image/png" });
        const url = URL.createObjectURL(pageBlob);
        // `page` is the 0-based page index; fall back to the progress index `i`
        // when absent. `pages` is the total; fall back to `n`, then 0.
        const page =
          typeof obj.page === "number"
            ? obj.page
            : typeof obj.i === "number"
              ? obj.i
              : 0;
        const pages =
          typeof obj.pages === "number"
            ? obj.pages
            : typeof obj.n === "number"
              ? obj.n
              : 0;
        onPageImage?.({ page, pages, url });
        // A snapshot line MAY also carry the language pair; surface it via
        // onProgress (with the snapshot's i/n) so the caller learns the source
        // language early — letting a same-language run abort before the whole
        // PDF finishes. Both fields are usually absent until the final line.
        const snapSrc = str(obj.source_lang);
        const snapTgt = str(obj.target_lang);
        if (snapSrc || snapTgt) {
          onProgress?.({
            i: typeof obj.i === "number" ? obj.i : page,
            n: typeof obj.n === "number" ? obj.n : pages,
            sourceLang: snapSrc,
            targetLang: snapTgt,
          });
        }
      } catch {
        /* a snapshot that won't decode is skipped — non-fatal */
      }
      return false;
    }
    if (typeof obj.i === "number" && typeof obj.n === "number") {
      // The progress line now optionally carries the translated block text AND
      // its structural kind ({"i":k,"n":n,"text":"…","kind":"title|h1|h2|p|li"})
      // — forward both so the UI can stream the translation in behind the loader
      // FORMATTED to match the document. Both are absent on older backends; an
      // unrecognized kind is dropped so the consumer falls back to a paragraph.
      // The progress line MAY also carry the language pair (source/target name);
      // forward it so a same-language run can be detected and aborted early.
      const KINDS = ["title", "h1", "h2", "p", "li"] as const;
      const rawKind = typeof obj.kind === "string" ? obj.kind : undefined;
      const kind = (KINDS as readonly string[]).includes(rawKind as string)
        ? (rawKind as PdfBlockKind)
        : undefined;
      onProgress?.({
        i: obj.i,
        n: obj.n,
        text: typeof obj.text === "string" ? obj.text : undefined,
        kind,
        sourceLang: str(obj.source_lang),
        targetLang: str(obj.target_lang),
      });
    }
    return false;
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl = buf.indexOf("\n");
      while (nl !== -1) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (handleLine(line)) return; // done or error — stop reading
        nl = buf.indexOf("\n");
      }
    }
    // Flush any trailing partial line (no terminating newline). The final
    // pdf_b64 line is large, so this path commonly carries it.
    buf += decoder.decode();
    if (buf.trim() && handleLine(buf)) return;
    // Stream closed with no final PDF line: surface a failure so the spinner
    // clears (there's no translated PDF to render).
    if (!settled) onError?.("The PDF translation ended unexpectedly.");
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") return;
    onError?.((e as Error)?.message || "Translation stream interrupted.");
  }
}

/** A structural block sent to the translated-PDF renderer. */
export interface RenderPdfBlock {
  type: "title" | "h1" | "h2" | "p" | "li";
  text: string;
}

/** Render a styled, structured PDF of an already-translated document via POST
 *  /render-translated-pdf and resolve to the application/pdf Blob. The server
 *  lays out the typed blocks (title/headings/bullets/paragraphs) with accents
 *  intact. Mirrors translateStream's auth (credentials:"include" cookie +
 *  transitional legacy Bearer). Throws on a non-OK response; pass `signal` to
 *  cancel an in-flight render. */
export async function renderTranslatedPdf(
  filename: string,
  blocks: RenderPdfBlock[],
  sourceLang: string,
  targetLang: string,
  { signal }: { signal?: AbortSignal } = {},
): Promise<Blob> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const bearer = legacyBearer();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  const res = await fetch(`${API_BASE_URL}/render-translated-pdf`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      filename,
      blocks,
      source_lang: sourceLang,
      target_lang: targetLang,
    }),
    credentials: "include",
    signal,
  });

  if (!res.ok) {
    let detail = res.statusText || "Could not render the PDF.";
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.blob();
}

// ── In-image translation (text translated IN PLACE on the raster) ───────────
// The image analogue of the document translator. POST the target language to
// /images/{id}/translate-image and the server returns the SAME image as PNG
// bytes with its text erased + the translation rendered where it was. The
// detected source + target language names ride back on response headers
// (X-Source-Lang / X-Target-Lang, human names like "English"). When no text is
// found the server returns the original image with X-Source-Lang = "".

/** Result of an in-image translation: the rendered PNG blob + the language
 *  pair. `sourceLang === ""` means no text was detected (blob is the original
 *  image unchanged). */
export interface TranslateImageResult {
  /** image/png bytes — the image with its text translated in place. */
  blob: Blob;
  /** Human name of the detected source language ("" when no text found). */
  sourceLang: string;
  /** Human name of the target language. */
  targetLang: string;
}

/** Translate the text INSIDE an owned image to `target` (a FLORES-200 code
 *  like "spa_Latn" or a plain ISO code like "es"). POSTs `{target}` to
 *  /images/{id}/translate-image and resolves to the rendered PNG blob plus the
 *  X-Source-Lang / X-Target-Lang header values. Mirrors the shared api client's
 *  auth (credentials:"include" session cookie + transitional legacy Bearer).
 *  Throws on a non-OK response; pass `signal` to cancel an in-flight request. */
export async function translateImage(
  imageId: string,
  target: string,
  { signal }: { signal?: AbortSignal } = {},
): Promise<TranslateImageResult> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const bearer = legacyBearer();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  const res = await fetch(`${API_BASE_URL}/images/${imageId}/translate-image`, {
    method: "POST",
    headers,
    body: JSON.stringify({ target }),
    credentials: "include",
    signal,
  });

  if (!res.ok) {
    let detail = res.statusText || "Could not translate the image.";
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 503) {
      detail = "Translation isn't available in this build.";
    }
    throw new Error(detail);
  }

  const blob = await res.blob();
  return {
    blob,
    sourceLang: res.headers.get("X-Source-Lang") || "",
    targetLang: res.headers.get("X-Target-Lang") || "",
  };
}

// ── Streaming in-image translation (alive loader + live text behind) ─────────
// The streaming analogue of translateImage, mirroring translatePdf's UX: POST
// {target} to /images/{id}/translate-image-stream; the response is
// `application/x-ndjson` (one JSON object per line):
//   - reading line:  {"stage":"reading"}                  (OCR started)
//   - region line:   {"i":k,"n":n,"text":"<translated region text>"}
//   - final line:    {"done":true,"image_b64":"<base64 PNG>",
//                     "source_lang":"<name>","target_lang":"<name>"}
//                    (source_lang === "" → no readable text found; the image is
//                     the original unchanged)
//   - error line:    {"error":".."}
// We read the body line-by-line (same reader as translatePdf) so the caller can
// show the branded loader, stream each region's translation in BEHIND it, and
// swap to the rendered PNG on the final line.

/** Callbacks + abort signal for the streaming in-image translate reader. */
export interface TranslateImageStreamHandlers {
  /** Fired on a {"stage":"…"} line (currently just "reading" when OCR starts).
   *  Lets the caller flip the loader's caption before any region arrives. */
  onStage?: (stage: string) => void;
  /** Fired once per translated region as it arrives, in order. `i` is the
   *  0-based region index, `n` the total region count, `text` the region's
   *  translated text — accumulate it BEHIND the loader. */
  onRegion?: (region: { i: number; n: number; text: string }) => void;
  /** Fired once on the final line with the rendered PNG as a Blob. `sourceLang`
   *  is "" when no readable text was found (blob is the original image). */
  onDone?: (result: {
    blob: Blob;
    sourceLang: string;
    targetLang: string;
  }) => void;
  /** Fired on a server `{error}` line, a non-OK response, or a thrown/network
   *  failure. */
  onError?: (message: string) => void;
  /** Abort the in-flight stream (re-run / close panel / file change). */
  signal?: AbortSignal;
}

/** Stream an IN-IMAGE translation from POST
 *  /images/{id}/translate-image-stream. Reads the `application/x-ndjson` body
 *  line-by-line (same reader as translatePdf), dispatching:
 *    - a stage line  → onStage(stage)
 *    - a region line → onRegion({i, n, text})
 *    - the final line → onDone({blob, sourceLang, targetLang})  (the base64
 *      `image_b64` decoded into an image/png Blob)
 *    - an error line OR non-OK → onError(message)
 *  Mirrors translatePdf's auth EXACTLY (credentials:"include" session cookie +
 *  transitional legacy Bearer). Resolves when the stream ends (done, error, or
 *  abort); it never throws — failures go to onError. Pass `signal` from an
 *  AbortController to cancel. */
export async function translateImageStream(
  imageId: string,
  target: string,
  { onStage, onRegion, onDone, onError, signal }: TranslateImageStreamHandlers = {},
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const bearer = legacyBearer();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  let res: Response;
  try {
    res = await fetch(
      `${API_BASE_URL}/images/${imageId}/translate-image-stream`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ target }),
        credentials: "include",
        signal,
      },
    );
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") return;
    onError?.((e as Error)?.message || "Could not reach the translator.");
    return;
  }

  if (!res.ok || !res.body) {
    let detail = res.statusText || "Could not translate the image.";
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 503) {
      detail = "Translation isn't available in this build.";
    }
    onError?.(detail);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  // Did we see the terminating line (the final image_b64, or an error)? Used
  // after the loop to fail cleanly if the server closes the stream WITHOUT the
  // final image (crash / proxy timeout / truncation) — otherwise the caller's
  // `translating` flag would hang true forever with no image to show.
  let settled = false;

  // Parse one NDJSON line. Returns true when the stream should stop
  // (done/error line).
  const handleLine = (line: string): boolean => {
    const trimmed = line.trim();
    if (!trimmed) return false;
    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return false; // ignore a malformed/partial line defensively
    }
    if (typeof obj.error === "string") {
      onError?.(obj.error);
      settled = true;
      return true;
    }
    if (obj.done === true && typeof obj.image_b64 === "string") {
      try {
        const imgBuf = base64ToArrayBuffer(obj.image_b64);
        const blob = new Blob([imgBuf], { type: "image/png" });
        onDone?.({
          blob,
          sourceLang:
            typeof obj.source_lang === "string" ? obj.source_lang : "",
          targetLang:
            typeof obj.target_lang === "string" ? obj.target_lang : "",
        });
      } catch {
        onError?.("Could not decode the translated image.");
      }
      settled = true;
      return true;
    }
    // A region line carries i/n + the translated text for that region.
    if (typeof obj.i === "number" && typeof obj.text === "string") {
      onRegion?.({
        i: obj.i,
        n: typeof obj.n === "number" ? obj.n : 0,
        text: obj.text,
      });
      return false;
    }
    // A stage line ({"stage":"reading"}) — OCR has started.
    if (typeof obj.stage === "string") {
      onStage?.(obj.stage);
    }
    return false;
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl = buf.indexOf("\n");
      while (nl !== -1) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (handleLine(line)) return; // done or error — stop reading
        nl = buf.indexOf("\n");
      }
    }
    // Flush any trailing partial line (no terminating newline). The final
    // image_b64 line is large, so this path commonly carries it.
    buf += decoder.decode();
    if (buf.trim() && handleLine(buf)) return;
    // Stream closed with no final image line: surface a failure so the spinner
    // clears (there's no translated image to render).
    if (!settled) onError?.("The image translation ended unexpectedly.");
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") return;
    onError?.((e as Error)?.message || "Translation stream interrupted.");
  }
}

// ── Neural TTS (Piper) ──────────────────────────────────────────────────────
// Server-side neural voice. POST /tts/speak {text, lang} → audio/wav bytes.
// Only a fixed set of base languages have a neural voice; anything else (or an
// unsupported base subtag) comes back as HTTP 501 so the caller can fall back
// to the browser's window.speechSynthesis.

/** Base ISO-639-1 languages the neural endpoint can synthesize. Used to skip
 *  a guaranteed-501 round-trip for unsupported languages (we still treat a
 *  server 501 as authoritative — this is just an early-out hint). */
export const NEURAL_TTS_LANGS = [
  "en", "es", "fr", "de", "it", "pt", "nl", "pl", "ru", "zh", "tr", "ar",
] as const;

/** True when `lang`'s base subtag has a neural voice (per NEURAL_TTS_LANGS).
 *  Accepts a BCP-47 tag or bare ISO code; case-insensitive. */
export function neuralTtsSupportsLang(lang: string | null | undefined): boolean {
  const base = String(lang || "").toLowerCase().split("-")[0];
  return (NEURAL_TTS_LANGS as readonly string[]).includes(base);
}

/** Thrown by `speak()` when the server returns 501 (no neural voice for the
 *  requested language). Callers catch this specifically to switch to the
 *  browser speechSynthesis fallback. */
export class NoNeuralVoiceError extends Error {
  constructor(public lang: string) {
    super(`No neural voice for language "${lang}".`);
    this.name = "NoNeuralVoiceError";
  }
}

/** Synthesize one chunk of `text` in `lang` (base ISO-639-1, e.g. "es") via
 *  the neural endpoint and resolve to the audio/wav Blob. Mirrors the shared
 *  api client's auth (credentials:"include" session cookie + transitional
 *  legacy Bearer). Pass `signal` from an AbortController to cancel an in-flight
 *  request.
 *    - HTTP 501  → throws NoNeuralVoiceError(lang)  (caller falls back)
 *    - other !ok → throws a generic Error
 *    - aborted   → the underlying fetch rejects with an AbortError (re-thrown;
 *                  the caller's AbortController owns that signal)
 *  The FIRST call for a brand-new language downloads its model server-side
 *  (~20s) before responding; warm calls are ~0.15s. */
export async function speak(
  text: string,
  lang: string,
  { signal, voice }: { signal?: AbortSignal; voice?: string } = {},
): Promise<Blob> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const bearer = legacyBearer();
  if (bearer) headers.Authorization = `Bearer ${bearer}`;

  const body: { text: string; lang: string; voice?: string } = { text, lang };
  if (voice) body.voice = voice;

  const res = await fetch(`${API_BASE_URL}/tts/speak`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    credentials: "include",
    signal,
  });

  if (res.status === 501) {
    // Drain the body so the connection can be reused; detail is informational.
    try { await res.text(); } catch { /* ignore */ }
    throw new NoNeuralVoiceError(lang);
  }
  if (!res.ok) {
    let detail = res.statusText || "Speech synthesis failed.";
    try {
      const j = (await res.json()) as { detail?: string };
      if (j?.detail) detail = j.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.blob();
}
