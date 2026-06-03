// File preview panel — slides in from the right when a file is selected.
import React, { useState as useStateP2, useEffect as useEffectP2, useRef as useRefP2 } from "react";
import { createPortal as createPortalP2 } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Icon } from "./icons.jsx";
import { AuthedThumb, AuthedImg, useAuthedBlobUrl } from "./auth-image.jsx";
import { getImagePeople, faceCropUrl, redetectFaces, reassignFace } from "@/api/people";
import { EditableName } from "./nameable-chip.jsx";
import { deleteFile, originalUrl, fetchAsBlobUrl, toggleStar } from "@/api/files";
import { attachImageTag, detachImageTag } from "@/api/tags";
import { CommentPanel } from "./comment-panel.jsx";
import { ImageToolRail, OcrOverlay, useImageOcr, DocToolRail, useDocumentText, useDocTranslation, DocTranslatedView, useImageTranslation, ImageTranslatedBar } from "./image-tool-rail.jsx";
import { DocTranslateLoader, SCRIBE_PHRASES } from "./neuthek-loader.jsx";
import { listShares } from "@/api/shares";
import { PdfPageStack } from "./pdf-stack.jsx";
import { ShareModal } from "./share-modal.jsx";
import { eraseImageCaches } from "./cache-eraser.js";
import { CodePreview, isCodeMime } from "./code-preview.jsx";
import { VideoPlayer } from "./video-player.jsx";
import { AudioPlayer } from "./audio-player.jsx";
import { CsvViewer } from "./csv-viewer.jsx";
import { SpreadsheetViewer } from "./spreadsheet-viewer.jsx";
import { IcsViewer } from "./ics-viewer.jsx";
import { VcfViewer } from "./vcf-viewer.jsx";
import { MarkdownViewer } from "./markdown-viewer.jsx";
import { HtmlViewer } from "./html-viewer.jsx";
import { DataTreeViewer } from "./data-tree-viewer.jsx";
import { NotebookViewer } from "./notebook-viewer.jsx";
import { Model3dViewer } from "./model3d-viewer.jsx";
import { FontViewer } from "./font-viewer.jsx";
import { EbookViewer } from "./ebook-viewer.jsx";
import { ArchiveViewer } from "./archive-viewer.jsx";
import { languageIcon, LanguageIcon } from "./language-icons.jsx";
import { fileTypeInfo } from "./file-types.js";

function fmtBytes(n) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

const TAG_SUGGESTIONS = [
  "Favorite", "To review", "Shared", "Archived", "Private", "WIP",
  "Work", "Personal", "Travel", "Family", "Reference", "Receipt",
];

// ── Center translated-PDF view (over the PDF page stack) ─────────────────────
// For PDFs the FINAL translation is the REAL PDF returned by the backend with
// only its text translated IN PLACE (exact layout/images/positions) — NOT a
// clean re-render. We do NOT show that PDF in the browser's native PDF viewer
// anymore (an <iframe src="blob:…"> rendered Chrome's PDF chrome — toolbar,
// thumbnail rail, a tiny default zoom — which looked off-brand). Instead, both
// while translating AND once done, we render the STREAMED EXACT PAGE IMAGES — the
// real rendered translated pages — as a clean scrollable STACK of white page
// "sheets" centered on the app's dark backdrop, matching the rest of the modal
// (the same `.nk-xlate__pages` look the live render uses). The full-fidelity PDF
// is still one click away via Download PDF (the real `pdf_b64` blob).
//
// FLOW:
//   • While TRANSLATING — the translate-pdf stream emits page-snapshot lines
//     ({i,n,page,pages,page_png_b64}); the hook decodes each to an object URL and
//     keeps the LATEST snapshot per page in `pageImages`, which we hand to
//     DocTranslateLoader. The loader shows its centered warming card (logo +
//     localized scribe phrases + progress) UNTIL progress ≥25% (the hook gates
//     early snapshots), then swaps to the pages with the branded loader compacted
//     into a slim progress STRIP on top. The page does NOT auto-scroll.
//     (`streamBlocks`/`streamText` remain a fallback for an older backend that
//     emits block text but no snapshots.)
//   • When DONE — the SAME page images (now the fully-translated pages, latest
//     snapshot per page) render as the final stack in the app's own style, with a
//     thin top bar carrying the language pair, a Download PDF button (real blob),
//     and the Show original ⇄ Show translation toggle.
//
// Defensive: `t` may be a partially-initialized hook value, so every field is
// read with a fallback (the stream can emit a line with a missing field, and a
// future refactor must not be able to blank-screen this overlay). `streamBlocks`
// / `streamText` may be undefined on an older/stale hook shape — coalesce both.
function TranslatedPdfView({ t }) {
  const tt = t || {};
  const translating = !!tt.translating;
  const done = !!tt.done;
  const progress = tt.progress || { done: 0, total: 0 };
  const langs = tt.langs || null;
  const showTranslation = !!tt.showTranslation;
  const toggleView = tt.toggleView;
  const translatedPdfBlob = tt.translatedPdfBlob || null;
  const loaderPhrases = tt.loaderPhrases;
  // The STREAMED EXACT PAGE IMAGES — object URLs of the real rendered document
  // pages translated so far (exact layout), indexed by 0-based page. This drives
  // BOTH the live render (while translating) AND the final stack (once done): the
  // latest snapshot per page is the fully-translated render of that page. May be
  // absent on a partially-built hook — default to empty.
  const pageImages = Array.isArray(tt.pageImages) ? tt.pageImages : [];
  // The live formatted blocks ([{text, kind}]) + flat-text fallback. Kept ONLY
  // as a fallback for an older backend that streams block text but no page
  // snapshots; the page images above are PREFERRED by the loader. Either may be
  // absent on a partially-built hook — default to empty so the loader simply
  // shows its warming card instead of throwing.
  const streamBlocks = Array.isArray(tt.streamBlocks) ? tt.streamBlocks : [];
  const streamText = typeof tt.streamText === "string" ? tt.streamText : "";

  // The final page sheets — drop holes (pages that never streamed a snapshot),
  // keeping each page's index for a stable key. When some pages lack an image we
  // simply show what's available; the Download still gives the full PDF.
  const finalPages = pageImages
    .map((url, page) => ({ url, page }))
    .filter((p) => typeof p.url === "string" && p.url);

  const pair =
    langs && (langs.source || langs.target)
      ? `${langs.source || "Detected"} → ${langs.target || ""}`.trim()
      : null;

  // 25% PREVIEW GATE (CHANGE 2): while translating, show ONLY the branded loader
  // (logo + phrases + progress) until the stream is ~25% done, THEN start showing
  // the streamed pages. We gate purely at the DISPLAY layer — below 25% we hand
  // the loader EMPTY page/block/text props so it renders its centered warming
  // card; at/after 25% we pass the real streamed content through. (The hook keeps
  // accumulating snapshots regardless, so the FINISHED stack still has every page
  // even when a short doc's only snapshot arrives before the 25% mark.)
  const pTotal = progress?.total || 0;
  const pDone = progress?.done || 0;
  const pct = pTotal > 0 ? (pDone / pTotal) * 100 : 0;
  const pastGate = pct >= 25;
  const gatedPageImages = pastGate ? pageImages : [];
  const gatedBlocks = pastGate ? streamBlocks : [];
  const gatedText = pastGate ? streamText : "";

  // Download the REAL translated PDF (the `pdf_b64` blob — full fidelity, exact
  // layout/text-in-place), named after nothing in particular here (the left
  // panel's download names it by file + language; this is the quick grab from the
  // result view). Creates a temp object URL + <a download>, revoked shortly after.
  const downloadPdf = () => {
    if (!translatedPdfBlob) {
      toast.error("The translated PDF isn’t ready yet.");
      return;
    }
    try {
      const url = URL.createObjectURL(translatedPdfBlob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "translated.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch {
      toast.error("Could not download the translated PDF.");
    }
  };

  // WHILE TRANSLATING: the live render (`.nk-xlate--live`, opaque) fully covers
  // the still-mounted original PdfPageStack. Once DONE the final page-image stack
  // fills the body below the toggle bar — app-styled, no browser PDF chrome.
  return (
    <div
      className="doc-translated doc-translated--pdf"
      aria-label="Translated PDF"
    >
      {/* The top status bar is only meaningful once the translation is done.
          WHILE translating, the live render's own strip carries progress, so we
          drop this bar. */}
      {!translating && done && (
        <div className="doc-translated__bar">
          <div className="doc-translated__bar-left">
            {pair && <span className="doc-translated__pair">{pair}</span>}
            <span className="doc-translated__progress">Translated</span>
          </div>
          <div className="doc-translated__bar-actions">
            {translatedPdfBlob && (
              <button
                type="button"
                className="chip-toggle"
                onClick={downloadPdf}
                title="Download the translated PDF — exact copy, text in place"
              >
                <Icon name="download" size={12} />
                Download PDF
              </button>
            )}
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
        </div>
      )}

      {/* WHILE TRANSLATING: the STREAMED EXACT PAGE IMAGES — the real rendered
          document pages (exact layout) translated so far — fill a scrollable
          area, progressively translating, with the branded loader compacted into
          the slim top strip. The display gate above feeds the loader empty props
          below 25%, so it shows its centered warming card until progress ≥25%,
          then swaps to the pages. `behindBlocks`/`behindText` are only a fallback
          for an older backend that emits block text but no page snapshots. The
          page does NOT auto-scroll. */}
      {translating && (
        <DocTranslateLoader
          className="nk-xlate--overlay"
          progress={progress}
          pair={pair}
          phrases={loaderPhrases}
          pageImages={gatedPageImages}
          behindBlocks={gatedBlocks}
          behindText={gatedText}
        />
      )}

      {/* DONE: the FINAL translated-page stack — clean white sheets centered on
          the app's dark backdrop (same look as the original PdfPageStack / the
          live render), NO browser PDF chrome. When some pages lack a streamed
          image we show what's available; the Download above still gives the full
          PDF. If NONE streamed (a backend that emitted only the final blob), we
          point the user at Download. */}
      {!translating && done && (
        <div className="doc-translated__scroll">
          {finalPages.length > 0 ? (
            <div className="nk-xlate__pages">
              {finalPages.map((p) => (
                <img
                  key={p.page}
                  src={p.url}
                  className="nk-xlate__page-img"
                  alt={`Translated page ${p.page + 1}`}
                  draggable="false"
                />
              ))}
            </div>
          ) : (
            <div className="doc-translated__placeholder">
              {translatedPdfBlob
                ? "Your translated PDF is ready — use Download PDF above to open it."
                : "No translated pages to show."}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Per-face correction chip for the preview's "People in this photo"
// section. Displays the existing avatar + inline-rename name, and adds
// a "fix" affordance (kebab) that opens a small menu:
//   - "Not this person"  → reassignFace(face_id, null)  (detaches the
//      face so a bad cluster match stops mislabeling this photo)
//   - "Move to <name>"   → reassignFace(face_id, personId)  (one entry
//      per other known person, so the user can re-home a face that
//      landed under the wrong person)
//   - "Delete <person>"  → bubbles up to the parent's onDeletePerson so
//      the whole person can be removed straight from the chip.
// The menu is portaled + position:fixed (same pattern as the gallery's
// FolderCard menu) so the horizontally-scrolling `.preview__faces`
// container doesn't clip it.
function FaceChip({ face, file, people, onDeletePerson }) {
  const qc = useQueryClient();
  // Auto-focus target for the "Move to" search input — focused when the
  // modal opens so the user can start filtering names immediately.
  const searchRef = useRefP2(null);
  // Was a popover (position:fixed, anchored to the ⋯ button). Per user
  // feedback ("cant scroll in the mini pop up", "make it its own big
  // modal card") this is now a centered MODAL — so it only needs an
  // open/closed boolean, no anchor geometry.
  const [open, setOpen] = useStateP2(false);
  const [busy, setBusy] = useStateP2(false);
  // Live filter for the "Move to <name>" list. Reset every time the
  // modal opens so a stale query never hides the full list.
  const [moveQuery, setMoveQuery] = useStateP2("");
  const labelled = !!face.person_display_name;

  // Esc closes the modal. Capture phase + stopPropagation so it fires
  // even while the search input has focus, and so it doesn't fall through
  // to the PreviewPanel's own Esc handler (which would close the whole
  // preview). Mirrors the per-modal Esc handlers elsewhere in this file.
  useEffectP2(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open]);

  // Auto-focus the search box when the modal opens. A 0ms defer lets the
  // portaled node mount before we reach for it.
  useEffectP2(() => {
    if (!open) return undefined;
    const t = setTimeout(() => searchRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  const openModal = (e) => {
    e.stopPropagation();
    setMoveQuery("");
    setOpen(true);
  };

  // reassignFace(face_id, personId|null). null detaches; a number moves
  // the face to that person. We invalidate this image's people AND the
  // global people list so both the chip here and the People tab update.
  const runReassign = async (personId, label) => {
    if (busy || face.face_id == null) return;
    setBusy(true);
    setOpen(false);
    try {
      await reassignFace(face.face_id, personId);
      toast.success(
        personId == null
          ? "Removed this face from the person"
          : `Moved this face to ${label}`,
      );
      qc.invalidateQueries({ queryKey: ["image-people", file.id] });
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["files"] });
      qc.invalidateQueries({ queryKey: ["facets"] });
    } catch (err) {
      toast.error(err?.detail || "Could not update this face");
    } finally {
      setBusy(false);
    }
  };

  // "Move to…" candidates — every named person OTHER than the one this
  // face is currently under. Sourced from the people prop the parent
  // passes from the ["people"] query. The full set drives the search
  // box; the live query filters by name (case-insensitive). We don't
  // pre-slice — with a search field the user can reach any name, and the
  // list scrolls within the modal's max-height.
  const allTargets = (people?.persons || [])
    .filter((p) => p.display_name && p.id !== face.person_id);
  const q = moveQuery.trim().toLowerCase();
  const filteredTargets = q
    ? allTargets.filter((p) => p.display_name.toLowerCase().includes(q))
    : allTargets;

  return (
    <div className="face-chip" style={busy ? { opacity: 0.6 } : undefined}>
      <AuthedThumb
        url={faceCropUrl(face.face_id)}
        className="face-chip__avatar"
        placeholder={{ background: "var(--surface-2)" }}
      />
      <EditableName
        name={face.person_display_name}
        // Per-detection relabel so correcting one misdetection doesn't
        // drag every photo of the originally-grouped person along.
        detectionId={face.detection_id}
        clusterId={face.face_id ? null : face.cluster_id}
        className={"face-chip__name" + (labelled ? "" : " face-chip__name--unnamed")}
        invalidate={[["image-people", file.id], ["people"]]}
      />
      {/* Correction affordance — only meaningful once there's a face
          row to retarget. Unnamed faces still get it so the user can
          push a stray detection out of an auto-cluster. */}
      {face.face_id != null && (
        <button
          type="button"
          className="face-chip__fix"
          aria-label="Fix this face"
          title="Wrong person? Fix this face"
          onClick={openModal}
          disabled={busy}
        >
          <Icon name="moreH" size={13}/>
        </button>
      )}
      {open && createPortalP2(
        // Centered MODAL (overlay + card), modeled on ShareModal so it
        // feels native: dim backdrop, click-backdrop-to-close, Esc-to-
        // close, comfortable width with a scrollable people list. All
        // styling lives in styles-face-modal.css under `.facefix-*`.
        <div
          className="facefix-modal__overlay"
          onClick={() => setOpen(false)}
          data-no-marquee="true"
        >
          <div
            className="facefix-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="facefix-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <header className="facefix-modal__head">
              <div className="facefix-modal__head-text">
                <div className="facefix-modal__kicker">FIX THIS FACE</div>
                <div id="facefix-modal-title" className="facefix-modal__title">
                  {labelled
                    ? `Move this face from ${face.person_display_name}`
                    : "Move this face to…"}
                </div>
              </div>
              <button
                className="btn-icon"
                onClick={() => setOpen(false)}
                aria-label="Close"
              >
                <Icon name="x" size={15}/>
              </button>
            </header>

            {/* Per-face detach — "Not <person>". Pushes this single
                detection out of the (possibly wrong) cluster. */}
            <button
              type="button"
              className="facefix-modal__not"
              onClick={() => runReassign(null, null)}
            >
              <span className="facefix-modal__not-icon"><Icon name="x" size={14}/></span>
              <span className="facefix-modal__not-label">
                Not {labelled ? face.person_display_name : "this person"}
              </span>
            </button>

            {allTargets.length > 0 ? (
              <>
                <div className="facefix-modal__kicker facefix-modal__kicker--move">MOVE TO</div>
                {/* Live name filter — auto-focused on open (searchRef).
                    Single clean focus ring (see styles-face-modal.css).
                    Esc is handled by the capture listener above; stop
                    other keys from bubbling to the preview. */}
                <div className="facefix-modal__search">
                  <Icon name="search" size={14}/>
                  <input
                    ref={searchRef}
                    type="text"
                    className="facefix-modal__search-input"
                    placeholder="Search people…"
                    value={moveQuery}
                    onChange={(e) => setMoveQuery(e.target.value)}
                    onKeyDown={(e) => { if (e.key !== "Escape") e.stopPropagation(); }}
                    aria-label="Search people to move this face to"
                  />
                </div>
                <div className="facefix-modal__list">
                  {filteredTargets.length > 0 ? (
                    filteredTargets.map((p) => {
                      // Per-person face avatar — same source the People
                      // tab uses: the person's representative crop via
                      // faceCropUrl(sample_face_id). Falls back to the
                      // user glyph when a person has no sample face id.
                      const avatarUrl = p.sample_face_id ? faceCropUrl(p.sample_face_id) : null;
                      return (
                        <button
                          key={p.id}
                          type="button"
                          className="facefix-modal__row"
                          onClick={() => runReassign(p.id, p.display_name)}
                        >
                          {avatarUrl ? (
                            <AuthedThumb
                              url={avatarUrl}
                              className="facefix-modal__avatar"
                              placeholder={{ background: "var(--surface-2)" }}
                            />
                          ) : (
                            <span className="facefix-modal__avatar facefix-modal__avatar--fallback">
                              <Icon name="user" size={15}/>
                            </span>
                          )}
                          <span className="facefix-modal__row-name">{p.display_name}</span>
                        </button>
                      );
                    })
                  ) : (
                    <div className="facefix-modal__empty">No matching people</div>
                  )}
                </div>
              </>
            ) : (
              <div className="facefix-modal__empty facefix-modal__empty--none">
                No other people yet. Name a face elsewhere first, then you can
                move this one to them.
              </div>
            )}
            {/* Intentionally NO whole-person delete here. This is a quick
                per-FACE fix (relabel / "Not this person" / move one
                detection); a "Delete {person}" — which wipes that person
                across EVERY photo — would sit one misclick from "Not this
                person" and be too destructive. User: "in quick preview we
                dont want the full delete that will be bad." Whole-person
                deletion lives in the People tab + person drill-in header. */}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

export function PreviewPanel({ file, onClose, onOpenAccount, onRename, user, activePerson, people, onDeletePerson, onBestOf }) {
  const qc = useQueryClient();
  const [tags, setTags] = useStateP2([]);
  const [draft, setDraft] = useStateP2("");
  const [showSuggest, setShowSuggest] = useStateP2(false);
  const [lightbox, setLightbox] = useStateP2(false);
  const [starred, setStarred] = useStateP2(false);
  const [shareModalOpen, setShareModalOpen] = useStateP2(false);
  // Tags disclosure — collapsible per user request ("tags should be a
  // colapsable"). Remembers the last open/closed choice across files and
  // sessions in localStorage; defaults to open so tags are visible the
  // first time. Hook stays unconditional (declared before the early
  // `if (!file)` return below).
  const TAGS_OPEN_KEY = "neuthek.preview.tagsOpen";
  const [tagsOpen, setTagsOpen] = useStateP2(() => {
    try { return localStorage.getItem(TAGS_OPEN_KEY) !== "0"; } catch { return true; }
  });
  const toggleTagsOpen = () => {
    setTagsOpen((v) => {
      const next = !v;
      try { localStorage.setItem(TAGS_OPEN_KEY, next ? "1" : "0"); } catch { /* private browsing */ }
      return next;
    });
  };

  // Left tool-bubble rail state (image lightbox), declared up here because
  // the Esc-handler effect just below depends on them (referencing them
  // later in the file caused a temporal-dead-zone crash). One tool open at
  // a time: comments uses commentsExpanded; activeTool drives info/text.
  const [commentsExpanded, setCommentsExpanded] = useStateP2(true);
  const [activeTool, setActiveTool] = useStateP2(null); // null | "info" | "text"

  // Esc order in the image lightbox: minimize an open tool panel first
  // (rail pattern — Esc tucks the tool back into its bubble), THEN close
  // the lightbox, THEN close the preview. Outside the lightbox it just
  // closes the preview as before.
  useEffectP2(() => {
    if (!file) return;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (lightbox) {
        if (activeTool || commentsExpanded) {
          // Tuck whichever tool is open back into its bubble.
          setActiveTool(null);
          setCommentsExpanded(false);
        } else {
          setLightbox(false);
        }
      } else {
        onClose && onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [file, lightbox, onClose, activeTool, commentsExpanded]);

  // Re-seed tags + star state when the file changes. We depend on the
  // STRINGIFIED tag list (not the array ref) so when the React-Query
  // cache refetches after a tag attach/detach, the new tags overwrite
  // the local mirror — previously this effect's deps were `[file?.id,
  // file?.is_starred]`, so adding a tag while the preview was open
  // updated the cache but the local state stayed at the old snapshot,
  // and on close + reopen the tag appeared briefly then "disappeared"
  // because addTag's purely-local push had never been persisted to
  // the backend (now it is, via addTag below).
  const tagKey = Array.isArray(file?.tags) ? file.tags.join("␟") : "";
  useEffectP2(() => {
    if (!file) return;
    setTags(Array.isArray(file.tags) ? file.tags : []);
    setStarred(!!file.is_starred);
  }, [file?.id, file?.is_starred, tagKey]);

  // Real people for this image (or video — face_scan runs over video
  // keyframes too). Gated on the user's face_recognition consent
  // server-side — when consent is off, the endpoint returns []. We
  // render nothing in that case.
  const isFaceCapable = file?.type === "image" || file?.type === "video";
  const { data: imagePeople = [] } = useQuery({
    queryKey: ["image-people", file?.id],
    queryFn: () => getImagePeople(file.id),
    enabled: !!file && isFaceCapable,
    staleTime: 30_000,
  });

  // Live share grants on this file (todo §1.1 / G1). Only the owner
  // can list them — backend filters by user_id at the route level, so
  // this returns 4xx for non-owners; we just show nothing in that case.
  const { data: shareGrants = [] } = useQuery({
    queryKey: ["image-shares", file?.id],
    queryFn: () => listShares(file.id),
    enabled: !!file?.id,
    staleTime: 15_000,
  });

  // D8 — user-signal re-detect cascade. Tracks busy/result so the UI
  // can swap between "Mark as containing a person" → "Looking…" →
  // "Found N" → "Couldn't find anyone, add manually?" copy.
  const [redetectState, setRedetectState] = useStateP2("idle"); // idle | running | empty
  const handleRedetect = async () => {
    if (!file || redetectState === "running") return;
    setRedetectState("running");
    try {
      const r = await redetectFaces(file.id);
      if (r.persisted > 0) {
        toast.success(`Found ${r.persisted} face${r.persisted === 1 ? "" : "s"} (${r.stage}).`);
        qc.invalidateQueries({ queryKey: ["image-people", file.id] });
        qc.invalidateQueries({ queryKey: ["people"] });
        setRedetectState("idle");
      } else {
        setRedetectState("empty");
      }
    } catch (e) {
      toast.error(e?.detail || "Could not run re-detect.");
      setRedetectState("idle");
    }
  };

  const handleToggleStar = async () => {
    if (!file) return;
    // Optimistic: flip the local flag immediately, hit the backend, roll
    // back on failure. Always invalidate the files cache on success so
    // the sidebar / gallery pick up the new state.
    const next = !starred;
    setStarred(next);
    try {
      await toggleStar(file.id);
      qc.invalidateQueries({ queryKey: ["files"] });
    } catch (e) {
      setStarred(!next);
      toast.error(e?.detail || "Could not save star");
    }
  };

  const handleDownload = async () => {
    if (!file) return;
    try {
      const blob = await fetchAsBlobUrl(originalUrl(file.id));
      const a = document.createElement("a");
      a.href = blob;
      a.download = file.name || "download";
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoke a moment later so the browser has time to start the download.
      setTimeout(() => URL.revokeObjectURL(blob), 4000);
    } catch (e) {
      toast.error(e?.detail || "Could not download file");
    }
  };

  const handleDelete = async () => {
    if (!file) return;
    if (!window.confirm(`Move "${file.name}" to trash?`)) return;
    try {
      await deleteFile(file.id);
      toast.success(`Deleted "${file.name}"`);
      // §A5 — full FE cache purge for the deleted id.
      await eraseImageCaches(qc, [file.id]);
      onClose && onClose();
    } catch (e) {
      toast.error(e?.detail || "Could not delete file");
    }
  };

  const isImage = file?.type === "image";
  const isVideo = file?.type === "video";
  // "doc" covers PDFs/Office; "code" is the app.jsx type for code/text
  // files (.py, .txt, .json, …) — both are the `document` category
  // upstream. isCode/isPdf gate on isDoc, so code files MUST be included
  // here or the code viewer never opens (the file falls to the generic
  // hero with the plain `< >` icon and a no-op click).
  const isDoc = file?.type === "doc" || file?.type === "code";
  // Hook call must stay unconditional — pass null when the file isn't
  // a PDF so the underlying fetch is skipped. PDF preview UX:
  //   - hero pane shows a non-scrolling page-1 render (the raster
  //     stored as `file.thumb` when our PyMuPDF rasterizer ran);
  //   - clicking the hero opens a full-page modal with the real PDF
  //     in an iframe, where the browser handles scroll/zoom/text-select.
  // The blob URL is only needed once the modal opens, so we gate the
  // fetch behind `pdfModal`.
  // Real PDFs AND converted Office docs both flow through the existing
  // PDF modal / PdfPageStack. The backend sets `pdf_viewable` →
  // `file.pdfViewable` true for native PDFs and for Office files whose
  // LibreOffice→PDF conversion has landed, so checking that flag routes
  // docx/xlsx/pptx/odt/… through the rasterized page viewer with no new
  // viewer. We still keep the mime/ext check as a belt-and-suspenders
  // path for native PDFs.
  const isPdf = !!(file && (file.pdfViewable || (isDoc && (
    (file.mime_type_original || "").toLowerCase() === "application/pdf"
    || (file.ext || "").toLowerCase() === "pdf"
  ))));
  // Themed full-display viewers. The kind tag comes from the catalog in
  // file-types.js; each kind gets its own modal state so Esc-handlers can
  // target the right one, mirroring the pdfModal / codeModal pattern.
  // NOTE: these MUST be computed before `isCode` below — `isCode` gates
  // itself off the rich kinds (markdown / datatree / notebook) so a
  // `.md` / `.json` / `.ipynb` opens its dedicated viewer instead of the
  // raw CodePreview.
  const ftKind = fileTypeInfo(file?.ext).kind;
  const isVideoFile = !!file && ftKind === "video";
  const isAudioFile = !!file && ftKind === "audio";
  const isCsvFile   = !!file && ftKind === "csv";
  const isIcsFile   = !!file && ftKind === "ics";
  const isVcfFile   = !!file && ftKind === "vcf";
  const isMarkdownFile = !!file && ftKind === "markdown";
  const isDataTreeFile = !!file && ftKind === "datatree";
  const isNotebookFile = !!file && ftKind === "notebook";
  const isModel3dFile  = !!file && ftKind === "model3d";
  const isSpreadsheetFile = !!file && ftKind === "spreadsheet";
  const isFontFile     = !!file && ftKind === "font";
  const isEbookFile    = !!file && ftKind === "ebook";
  const isArchiveFile  = !!file && ftKind === "archive";
  // HTML/HTM — rendered as a STATIC, scripts-disabled sandboxed iframe by
  // HtmlViewer (NOT executed). Its own kind so it routes here instead of
  // the raw CodePreview; `isCode` below excludes it for the same reason.
  const isHtmlFile     = !!file && ftKind === "html";
  // SVG — categorized `image` upstream, so it already flows through the
  // image hero/lightbox; but a served raster thumb may not exist for it.
  // We render SVG from the AUTHED ORIGINAL bytes as an <img> (browsers
  // never execute scripts in an img-loaded SVG, so this is inherently
  // safe — the backend also sanitizes on upload). Detected by extension
  // since `svg` is unambiguous.
  const isSvgFile = !!file && (file.ext || "").toLowerCase() === "svg";
  // Code / text files (`.py`, `.js`, `Dockerfile`, …). The backend
  // returns `text/x-<lang>` for these; CodePreview picks the right
  // Prism grammar from that mime. Falls back to plain monospace if
  // the language isn't in the loader table. Excludes the rich kinds so
  // markdown/JSON/notebook flow to their dedicated viewers.
  // `isModel3dFile`/`isSpreadsheetFile` are excluded so an `.stl` (mime
  // `text/x-stl` — starts with `text/`, so isCodeMime is true) reaches the
  // 3D viewer instead of being captured by the code viewer, and so `.xlsx`
  // reaches the spreadsheet viewer.
  const isCode = !!(file && isDoc && !isPdf && !isMarkdownFile && !isDataTreeFile && !isNotebookFile && !isHtmlFile && !isModel3dFile && !isSpreadsheetFile && !isCsvFile && !isIcsFile && !isVcfFile && isCodeMime(file.mime_type_original));
  const [pdfModal, setPdfModal] = useStateP2(false);
  const [codeModal, setCodeModal] = useStateP2(false);
  const [videoModal, setVideoModal] = useStateP2(false);
  const [audioModal, setAudioModal] = useStateP2(false);
  const [csvModal, setCsvModal] = useStateP2(false);
  const [icsModal, setIcsModal] = useStateP2(false);
  const [vcfModal, setVcfModal] = useStateP2(false);
  const [markdownModal, setMarkdownModal] = useStateP2(false);
  const [dataTreeModal, setDataTreeModal] = useStateP2(false);
  const [notebookModal, setNotebookModal] = useStateP2(false);
  const [model3dModal, setModel3dModal] = useStateP2(false);
  const [spreadsheetModal, setSpreadsheetModal] = useStateP2(false);
  const [fontModal, setFontModal] = useStateP2(false);
  const [ebookModal, setEbookModal] = useStateP2(false);
  const [archiveModal, setArchiveModal] = useStateP2(false);
  const [htmlModal, setHtmlModal] = useStateP2(false);
  // Comments panel state — expanded by default whenever a
  // full-display surface opens. Auto-collapses to the bubble during
  // video playback (see videoFocusMode below); the user can click
  // the bubble to expand again while the video keeps playing.
  // LEFT TOOL-BUBBLE RAIL (image lightbox). commentsExpanded + activeTool
  // are declared earlier (the Esc-handler effect above depends on them); the
  // rail keeps the open tools mutually exclusive — one panel at a time.
  // showOcrBoxes toggles the on-image highlight overlay; ocrLineIdx links a
  // hovered text line to its box (and back). All declared unconditionally
  // before the early `if (!file)` return below.
  const [showOcrBoxes, setShowOcrBoxes] = useStateP2(true);
  const [ocrLineIdx, setOcrLineIdx] = useStateP2(null);
  // Video focus mode — true while a video is actively playing.
  // Drives:
  //   - .lightbox--focus (pure-black backdrop, hides everything but
  //     the player so the eye lands on the picture)
  //   - auto-collapse of the comment panel to its bubble
  // Cleared on pause / end / modal close.
  const [videoFocusMode, setVideoFocusMode] = useStateP2(false);
  // Close any open full-display modal when the panel is closed
  // externally or the user navigates to a different file.
  useEffectP2(() => {
    setPdfModal(false); setCodeModal(false);
    setVideoModal(false); setAudioModal(false);
    setCsvModal(false); setIcsModal(false); setVcfModal(false);
    setMarkdownModal(false); setDataTreeModal(false); setNotebookModal(false);
    setModel3dModal(false); setFontModal(false); setEbookModal(false); setArchiveModal(false);
    setHtmlModal(false); setSpreadsheetModal(false);
    setCommentsExpanded(true);
    setVideoFocusMode(false);
    // Reset the rail to "all minimized" for the new file so info/text
    // panels and the OCR overlay don't leak across files.
    setActiveTool(null);
    setShowOcrBoxes(true);
    setOcrLineIdx(null);
    // Reset the "mark as containing a person" control to idle on every
    // file change. Without this, an "empty" result (a manual detect that
    // found no faces) from one file leaked onto the NEXT file, so the
    // prompt appeared even on files that already have a detected face.
    setRedetectState("idle");
  }, [file?.id]);
  // Image lightbox opens with EVERYTHING minimized to bubbles (the rail's
  // default state — "the file is the focus"). The shared `commentsExpanded`
  // defaults open for the OTHER surfaces (PDF/video/etc., which keep their
  // auto-expanded comment panel), so we explicitly collapse it + clear the
  // info/text tool the moment the image lightbox opens. Only runs on the
  // false→true edge so the user can freely open a tool afterward.
  useEffectP2(() => {
    if (lightbox) {
      setCommentsExpanded(false);
      setActiveTool(null);
    }
  }, [lightbox]);
  // Document surfaces (PDF / code / markdown) open with the rail fully
  // minimized too — same "the file is the focus" default as the image
  // lightbox. Only runs on the false→true edge so the user can freely open
  // a tool afterward.
  useEffectP2(() => {
    if (pdfModal || codeModal || markdownModal) {
      setCommentsExpanded(false);
      setActiveTool(null);
    }
  }, [pdfModal, codeModal, markdownModal]);
  // When the video modal closes (Esc, click-outside, X), drop focus
  // mode so re-opening another surface starts at "normal lightbox."
  useEffectP2(() => {
    if (!videoModal) setVideoFocusMode(false);
  }, [videoModal]);
  // Bridge from VideoPlayer's onPlayingChange. When playback starts
  // we set focus mode AND auto-collapse the comments bubble. When
  // it stops we drop focus mode but DON'T re-expand comments — the
  // user may have explicitly collapsed during a pause, and we should
  // respect that until they click the bubble themselves.
  const handleVideoPlayingChange = (playing) => {
    setVideoFocusMode(!!playing);
    if (playing) setCommentsExpanded(false);
  };
  // Esc closes whatever modal is open before falling through to the
  // panel-close handler. We already have an Esc listener for the
  // image lightbox + preview; each modal needs its own capture-phase
  // listener so it can swallow the event first.
  // Doc-surface Esc order mirrors the image lightbox: if a rail tool
  // (info / text) or the comments panel is open, Esc tucks it back into
  // its bubble FIRST; only a second Esc closes the modal.
  useEffectP2(() => {
    if (!pdfModal) return;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (activeTool || commentsExpanded) { setActiveTool(null); setCommentsExpanded(false); }
      else setPdfModal(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [pdfModal, activeTool, commentsExpanded]);
  useEffectP2(() => {
    if (!codeModal) return;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (activeTool || commentsExpanded) { setActiveTool(null); setCommentsExpanded(false); }
      else setCodeModal(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [codeModal, activeTool, commentsExpanded]);
  useEffectP2(() => {
    if (!videoModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setVideoModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [videoModal]);
  useEffectP2(() => {
    if (!audioModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setAudioModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [audioModal]);
  useEffectP2(() => {
    if (!csvModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setCsvModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [csvModal]);
  useEffectP2(() => {
    if (!icsModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setIcsModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [icsModal]);
  useEffectP2(() => {
    if (!vcfModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setVcfModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [vcfModal]);
  useEffectP2(() => {
    if (!markdownModal) return;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (activeTool || commentsExpanded) { setActiveTool(null); setCommentsExpanded(false); }
      else setMarkdownModal(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [markdownModal, activeTool, commentsExpanded]);
  useEffectP2(() => {
    if (!dataTreeModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setDataTreeModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [dataTreeModal]);
  useEffectP2(() => {
    if (!notebookModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setNotebookModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [notebookModal]);
  useEffectP2(() => {
    if (!model3dModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setModel3dModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [model3dModal]);
  useEffectP2(() => {
    if (!spreadsheetModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setSpreadsheetModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [spreadsheetModal]);
  useEffectP2(() => {
    if (!fontModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setFontModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [fontModal]);
  useEffectP2(() => {
    if (!ebookModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setEbookModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [ebookModal]);
  useEffectP2(() => {
    if (!archiveModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setArchiveModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [archiveModal]);
  useEffectP2(() => {
    if (!htmlModal) return;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); setHtmlModal(false); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [htmlModal]);

  // OCR for the Text tool. Unconditional hook; the underlying query only
  // fires when the image lightbox is open AND the Text bubble is active,
  // so opening any other surface never triggers Florence-2. Cached per
  // image id in TanStack Query, so re-opening the Text tool is instant.
  // Exclude SVG: it's categorized `image` but is a vector the OCR
  // raster path (PIL + Florence) can't open, so don't fire a doomed call.
  const ocrQuery = useImageOcr(
    file?.id,
    isImage && !isSvgFile,
    lightbox && activeTool === "text",
  );

  // Whether a DOCUMENT surface (PDF / Office-as-PDF / code / markdown) is
  // open. These get the same left tool-rail as the image lightbox, with a
  // "Translate document" tool instead of OCR. Declared before the early
  // return so the doc-text hook below stays unconditional.
  const docSurfaceOpen = pdfModal || codeModal || markdownModal;

  // Full document text for the Translate tool. Unconditional hook; the
  // underlying GET /images/{id}/text only fires when a doc surface is open
  // AND the Translate ("text") bubble is active — so opening a document
  // never extracts text until the user asks. Cached per file id.
  const docTextQuery = useDocumentText(
    file?.id,
    docSurfaceOpen && activeTool === "text",
  );

  // Shared document-translation state. Instantiated ONCE here (unconditional
  // hook — declared before the early return) so the LEFT Translate panel and
  // the CENTER translated-document view read the SAME translation. The left
  // panel (DocToolRail → DocTextToolPanel) drives it; the center shows the
  // result. The 3rd arg routes PDFs to the EXACT-COPY path: the backend returns
  // the ORIGINAL PDF with only its text translated IN PLACE, and we render THAT
  // real PDF in the same viewer (NOT the structured clean re-render that
  // DocTranslatedView builds — that path stays for txt/docx/etc.).
  const docTranslation = useDocTranslation(file?.id, docTextQuery?.data?.text || "", isPdf);

  // Shared IN-IMAGE translation state. Instantiated ONCE here (unconditional
  // hook — declared before the early return) so the LEFT Text panel and the
  // MAIN image view read the SAME translation: the panel drives Translate +
  // Download + the toggle; the main view renders the translated PNG in place of
  // the original <img>. Resets/aborts itself when the file id changes.
  const imageTranslation = useImageTranslation(file?.id);

  if (!file) return null;

  // Tag changes here persist to the backend via /images/{id}/tags so
  // closing + reopening the preview reflects the actual state. We
  // optimistically update local state for snappy UI, then invalidate
  // the React-Query caches that back the gallery, the preview's own
  // re-seed effect, and the filter dropdown's chip group.
  const invalidateAfterTagChange = () => {
    qc.invalidateQueries({ queryKey: ["files"] });
    qc.invalidateQueries({ queryKey: ["facets"] });
    qc.invalidateQueries({ queryKey: ["tags"] });
  };
  const addTag = async (t) => {
    const label = (t || "").trim();
    if (!label || !file?.id) return;
    if (tags.includes(label)) {
      setDraft("");
      return;
    }
    // Optimistic — instant UI; rolled back on error.
    setTags((prev) => [...prev, label]);
    setDraft("");
    try {
      await attachImageTag(file.id, { label });
      invalidateAfterTagChange();
    } catch (e) {
      setTags((prev) => prev.filter((x) => x !== label));
      toast.error(e?.detail || e?.message || "Could not save tag");
    }
  };
  const removeTag = async (t) => {
    if (!file?.id) return;
    // Look up the tag id from the rich tagRows mirror (mapper in
    // app.jsx kept the {id, label, color} shape alongside the
    // flattened string list). Falls back to a no-op when we don't
    // have an id — e.g. a tag the user just typed and the optimistic
    // attach hasn't returned yet.
    const rich = (file.tagRows || []).find((r) => r.label === t);
    setTags((prev) => prev.filter((x) => x !== t));
    if (!rich) return;
    try {
      await detachImageTag(file.id, rich.id);
      invalidateAfterTagChange();
    } catch (e) {
      setTags((prev) => (prev.includes(t) ? prev : [...prev, t]));
      toast.error(e?.detail || e?.message || "Could not remove tag");
    }
  };

  // Whether ANY left-edge panel is expanded (so the lightbox content
  // shifts right to clear it). Comments works on every surface; the
  // info/text tools only render in the image lightbox, where activeTool
  // tracks them. On non-image surfaces activeTool is always null, so this
  // reduces to the old `commentsExpanded`-only behavior — unchanged.
  const leftPanelOpen = commentsExpanded || activeTool != null;

  // Compose the lightbox className once — the modifiers depend on whether
  // a left panel is expanded (drops the 360px left padding when only
  // bubbles show) and on videoFocusMode (paints the backdrop pure black so
  // the eye lands on the video). Used on every modal surface.
  const lightboxClass = [
    "lightbox",
    leftPanelOpen ? "lightbox--comments" : "lightbox--bubble",
    videoFocusMode ? "lightbox--focus" : "",
  ].filter(Boolean).join(" ");

  // Rail bubble toggle — enforces one-tool-at-a-time. Comments uses its
  // own expanded flag (the existing CommentPanel); info/text use
  // activeTool. Selecting one closes the others.
  const handleToolSelect = (id) => {
    if (id === "comments") {
      setCommentsExpanded((open) => {
        const next = !open;
        if (next) setActiveTool(null); // close info/text when comments opens
        return next;
      });
      return;
    }
    // info / text
    setActiveTool((cur) => {
      const next = cur === id ? null : id;
      if (next) setCommentsExpanded(false); // close comments when these open
      return next;
    });
  };

  // The rail belongs to the IMAGE lightbox specifically (the OCR overlay
  // needs the rendered image). Other surfaces keep the plain comment
  // bubble/panel exactly as before.
  const railActive = activeTool || (commentsExpanded ? "comments" : null);
  const showImageRail = lightbox && (isImage || isSvgFile);
  // Whether the MAIN image view should currently show the TRANSLATED render
  // (text translated in place) instead of the original photo. True only when a
  // translated image exists AND the user hasn't toggled back to the original.
  const showImageTranslation =
    !!imageTranslation.translatedUrl && imageTranslation.showTranslation;
  // Document surfaces (PDF / Office-as-PDF / code / markdown) get the same
  // left tool-rail, with a "Translate document" tool in place of OCR. The
  // rail + its panels are position:fixed, so it's rendered once at the top
  // level (below) gated on this flag.
  const showDocRail = docSurfaceOpen;

  // Whether ANY full-display surface (lightbox or a viewer modal) is open. Used
  // to neutralize the dim backdrop's click-to-close while a surface — and its
  // translate panel / in-progress translation — is up, so an accidental click
  // on the chrome can't tear it all down. Each surface keeps its own X + Esc.
  const anySurfaceOpen =
    lightbox || pdfModal || codeModal || videoModal || audioModal || csvModal ||
    icsModal || vcfModal || markdownModal || dataTreeModal || notebookModal ||
    model3dModal || fontModal || ebookModal || archiveModal || htmlModal ||
    spreadsheetModal;

  return (
    <React.Fragment>
    {/* Dim backdrop behind the right-hand DETAILS aside. It only closes the
        preview when NO full-display surface is open — once a lightbox / viewer
        modal (and its translate panel / running translation) is up, a stray
        click on the chrome must NOT tear it down. Each surface still has its
        own X button + Esc. */}
    <div
      className="preview-backdrop"
      onClick={anySurfaceOpen ? undefined : onClose}
    />
    {/* §G2 — comment panel only renders while a full-display
        surface is open. Shared across image lightbox, PDF modal,
        and code modal so every shareable file type gets the same
        thread UX. Position: fixed left edge — the lightbox content
        sits to its right (CSS gives the lightbox .lightbox--comments
        class a left padding so nothing's covered). The panel closes
        implicitly when the parent surface closes; the thread itself
        is server-side, so re-opening the file resumes the
        conversation. */}
    <CommentPanel
      fileId={file.id}
      currentUserId={user?.id}
      ownerUserId={file.user_id || user?.id}
      open={lightbox || pdfModal || codeModal || videoModal || audioModal || csvModal || icsModal || vcfModal || markdownModal || dataTreeModal || notebookModal || model3dModal || fontModal || ebookModal || archiveModal || htmlModal || spreadsheetModal}
      expanded={commentsExpanded}
      onToggleExpanded={(next) => {
        // Keep the rail mutually exclusive: opening comments closes the
        // info/text tools. Closing just collapses. (No-op for non-image
        // surfaces where activeTool stays null.)
        setCommentsExpanded(next);
        if (next) setActiveTool(null);
      }}
      // In the image lightbox AND on document surfaces the rail owns the
      // comments bubble, so hide the CommentPanel's own floating bubble to
      // avoid a duplicate. On every other surface it keeps its self-
      // contained bubble.
      hideBubble={showImageRail || showDocRail}
    />
    {lightbox && (file.thumb || isSvgFile) && (
      // Click-outside no longer closes the lightbox — ONLY the X button (below)
      // or Esc do, so an incidental click on the dim area (or switching tabs)
      // can't dismiss the image / its translate panel mid-work.
      <div className={lightboxClass}>
        <button className="lightbox__close" aria-label="Close" onClick={(e) => { e.stopPropagation(); setLightbox(false); }}>
          <Icon name="x" size={18}/>
        </button>
        {/* LEFT TOOL-BUBBLE RAIL — comments / info / text, one at a time.
            Comments is driven by commentsExpanded (the CommentPanel above
            renders the expanded panel); info/text render their own panels
            from inside the rail component. */}
        <ImageToolRail
          file={file}
          active={railActive}
          onSelect={handleToolSelect}
          commentCount={undefined}
          // "Best of" bubble — only when the parent wired a handler AND the
          // open file is a raster image (SVG has no CLIP embedding / burst
          // group). Hands the file up so the parent fetches its similar
          // group and opens the shared BestOfModal.
          onBestOf={onBestOf && isImage ? () => onBestOf(file) : undefined}
          ocrQuery={ocrQuery}
          showBoxes={showOcrBoxes}
          onToggleBoxes={() => setShowOcrBoxes((v) => !v)}
          activeLineIdx={ocrLineIdx}
          onHoverLine={setOcrLineIdx}
          translation={imageTranslation}
        />
        {/* Image STAGE — a wrapper that hugs the rendered <img> so the OCR
            overlay (normalized 0..1 boxes) can be placed as percentages and
            stays aligned at any zoom/letterbox. SVG loads the original
            vector (inert <img>); raster images use the full-res variant.
            Clicking the file tucks any open tool back into its bubble (the
            rail's minimize-on-file-click behavior) without closing the
            lightbox; the backdrop click still closes it. */}
        <div
          className="lb-stage"
          onClick={(e) => {
            e.stopPropagation();
            if (activeTool || commentsExpanded) {
              setActiveTool(null);
              setCommentsExpanded(false);
            }
          }}
        >
          <AuthedImg
            url={isSvgFile ? originalUrl(file.id) : (file.thumbFull || file.thumb)}
            className="lightbox__img"
            alt={file.name}
            // Branded loader while the full-res blob loads, so opening — or
            // switching to — a file shows a clean "loading" beat instead of a
            // blank flash. Delayed in CSS, so an instant cache hit never shows
            // it.
            loader
            style={isSvgFile ? { background: "#fff", display: "block" } : { display: "block" }}
          />
          {/* TRANSLATED IMAGE — overlays the original (which stays mounted, so
              the toggle flips back instantly with no reload). The stage is
              sized to the original <img>, so an absolutely-positioned image
              filling the stage exactly covers it (same dimensions). Only shown
              while the user is viewing the translation. */}
          {showImageTranslation && (
            <img
              src={imageTranslation.translatedUrl}
              className="lightbox__img img-translated__img"
              alt={`${file.name} — translated`}
            />
          )}
          {/* OCR highlight boxes — hidden while the translated image is shown
              (the boxes map the ORIGINAL text positions, which no longer
              apply to the re-rendered translation). */}
          {activeTool === "text" && !showImageTranslation && showOcrBoxes && ocrQuery?.data?.boxes && (
            <OcrOverlay
              lines={ocrQuery.data.lines}
              activeIdx={ocrLineIdx}
              onHoverBox={setOcrLineIdx}
            />
          )}
          {/* While the image is translating: the SAME branded loader the
              document/PDF flow now uses — the neuthek logo + cycling localized
              scribe phrases + the real `i / n` progress bar (driven by each OCR
              region's {i,n}), centered over a softly-dimmed original photo
              (nk-xlate--overlay). No reflowed text page is rendered — the
              original photo stays visible underneath until, on done, the
              rendered translated PNG swaps in place. */}
          {activeTool === "text" && imageTranslation.translating && (
            <DocTranslateLoader
              className="nk-xlate--overlay"
              progress={imageTranslation.progress}
              phrases={(imageTranslation.loaderPhrases && imageTranslation.loaderPhrases.length) ? imageTranslation.loaderPhrases : SCRIBE_PHRASES}
              pair={
                imageTranslation.langs &&
                (imageTranslation.langs.source || imageTranslation.langs.target)
                  ? `${imageTranslation.langs.source || "Detected"} → ${imageTranslation.langs.target || ""}`.trim()
                  : null
              }
            />
          )}
          {/* Status bar — language pair + Translating indicator + the
              Show original ⇄ Show translation toggle. Only while the Text tool
              is open AND a translation exists or is in flight. */}
          {activeTool === "text" && (imageTranslation.translatedUrl || imageTranslation.translating) && (
            <ImageTranslatedBar t={imageTranslation} />
          )}
        </div>
      </div>
    )}
    {/* LEFT TOOL-BUBBLE RAIL for DOCUMENT surfaces (PDF / Office-as-PDF /
        code / markdown). Same one-panel-at-a-time UX as the image lightbox,
        with a "Translate document" tool in place of OCR. The rail + its
        panels are position:fixed, so it's rendered once here and overlays
        whichever doc modal is open. Comments is driven by commentsExpanded
        (the CommentPanel above renders the expanded panel); info/text render
        their own panels from inside the rail. */}
    {showDocRail && (
      <DocToolRail
        file={file}
        active={railActive}
        onSelect={handleToolSelect}
        commentCount={undefined}
        docQuery={docTextQuery}
        translation={docTranslation}
      />
    )}
    {pdfModal && isPdf && (
      // Click-outside no longer closes the modal — only the X button / Esc do,
      // so a stray click on the dim area (or a tab switch) can't dismiss the
      // document and its translate panel / running translation.
      <div className={lightboxClass}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon">
              <Icon name="document" size={14}/>
            </span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button
              type="button"
              className="btn-icon"
              onClick={handleDownload}
              aria-label="Download"
              title="Download"
            >
              <Icon name="download" size={14}/>
            </button>
            <button
              type="button"
              className="btn-icon"
              onClick={() => setPdfModal(false)}
              aria-label="Close document"
              title="Close"
            >
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body pdf-modal__body--translatable">
            {/* Server-rasterized page stack (PyMuPDF → JPEG per page,
                lazy-loaded into a themed scroll container). Replaces the
                old iframe-into-PDFium approach so the scrollbar belongs
                to us and every page is visible in one continuous scroll
                instead of paginated under PDFium's `view=Fit` mode.
                Kept MOUNTED under the translated overlay so flipping back
                to the original preserves scroll position. */}
            <PdfPageStack fileId={file.id}/>
            {/* CENTER translated PDF — the EXACT-COPY translated PDF (real
                layout, text in place), rendered in the browser's native PDF
                viewer (iframe) IN PLACE of the original. Mounted while
                translating (progress overlay over the still-visible original)
                and once done (the translated PDF). The toggle flips between the
                original PdfPageStack and this translated PDF. NOT the clean
                re-render (DocTranslatedView) — that path is only for non-PDFs. */}
            {docTranslation.showTranslation &&
              (docTranslation.translating || docTranslation.done) && (
              <TranslatedPdfView t={docTranslation}/>
            )}
          </div>
        </div>
      </div>
    )}
    {codeModal && isCode && (
      // Click-outside no longer closes the modal — only the X button / Esc do
      // (keeps the translate panel + any running translation alive).
      <div className={lightboxClass}>
        <div className="pdf-modal pdf-modal--code" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon">
              <Icon name="code" size={14}/>
            </span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button
              type="button"
              className="btn-icon"
              onClick={handleDownload}
              aria-label="Download"
              title="Download"
            >
              <Icon name="download" size={14}/>
            </button>
            <button
              type="button"
              className="btn-icon"
              onClick={() => setCodeModal(false)}
              aria-label="Close code viewer"
              title="Close"
            >
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body pdf-modal__body--translatable" style={{ background: "var(--surface)" }}>
            <CodePreview
              fileId={file.id}
              mime={file.mime_type_original}
              byteSize={file.byteSize}
              filename={file.name}
            />
            {docTranslation.showTranslation && (
              <DocTranslatedView t={docTranslation}/>
            )}
          </div>
        </div>
      </div>
    )}
    {videoModal && isVideoFile && (
      <div className={lightboxClass}>
        <VideoPlayer
          fileId={file.id}
          fileName={file.name}
          onClose={() => setVideoModal(false)}
          onPlayingChange={handleVideoPlayingChange}
        />
      </div>
    )}
    {audioModal && isAudioFile && (
      <div className={lightboxClass}>
        <AudioPlayer
          fileId={file.id}
          fileName={file.name}
          fileExt={file.ext}
          onClose={() => setAudioModal(false)}
        />
      </div>
    )}
    {csvModal && isCsvFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="spreadsheet" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setCsvModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <CsvViewer fileId={file.id} fileName={file.name}/>
          </div>
        </div>
      </div>
    )}
    {icsModal && isIcsFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="calendar" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setIcsModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <IcsViewer fileId={file.id} fileName={file.name}/>
          </div>
        </div>
      </div>
    )}
    {vcfModal && isVcfFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="contact" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setVcfModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <VcfViewer fileId={file.id} fileName={file.name}/>
          </div>
        </div>
      </div>
    )}
    {markdownModal && isMarkdownFile && (
      // Click-outside disabled — only the X button / Esc close (keeps the
      // translate panel + any running translation alive).
      <div className={lightboxClass}>
        <div className="pdf-modal pdf-modal--fill" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="document" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setMarkdownModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body pdf-modal__body--translatable" style={{ background: "var(--surface)" }}>
            <MarkdownViewer fileId={file.id} fileName={file.name}/>
            {docTranslation.showTranslation && (
              <DocTranslatedView t={docTranslation}/>
            )}
          </div>
        </div>
      </div>
    )}
    {dataTreeModal && isDataTreeFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="code" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setDataTreeModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <DataTreeViewer fileId={file.id} fileName={file.name} ext={file.ext}/>
          </div>
        </div>
      </div>
    )}
    {notebookModal && isNotebookFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="code" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setNotebookModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <NotebookViewer fileId={file.id} fileName={file.name}/>
          </div>
        </div>
      </div>
    )}
    {model3dModal && isModel3dFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal pdf-modal--fill" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="cube" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setModel3dModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <Model3dViewer fileId={file.id} fileName={file.name} fileExt={file.ext}/>
          </div>
        </div>
      </div>
    )}
    {spreadsheetModal && isSpreadsheetFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="spreadsheet" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setSpreadsheetModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <SpreadsheetViewer fileId={file.id} fileName={file.name}/>
          </div>
        </div>
      </div>
    )}
    {fontModal && isFontFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="type" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setFontModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <FontViewer fileId={file.id} fileName={file.name} fileExt={file.ext} byteSize={file.byteSize}/>
          </div>
        </div>
      </div>
    )}
    {ebookModal && isEbookFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal pdf-modal--fill" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="book" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setEbookModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <EbookViewer fileId={file.id} fileName={file.name}/>
          </div>
        </div>
      </div>
    )}
    {archiveModal && isArchiveFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal pdf-modal--fill" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="archive" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setArchiveModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <ArchiveViewer fileId={file.id} fileName={file.name} onClose={() => setArchiveModal(false)}/>
          </div>
        </div>
      </div>
    )}
    {htmlModal && isHtmlFile && (
      <div className={lightboxClass}>
        <div className="pdf-modal pdf-modal--fill" onClick={(e) => e.stopPropagation()}>
          <div className="pdf-modal__head">
            <span className="pdf-modal__icon"><Icon name="code" size={14}/></span>
            <div className="pdf-modal__name">{file.name}</div>
            <span className="pdf-modal__size">{file.size}</span>
            <button type="button" className="btn-icon" onClick={handleDownload} aria-label="Download" title="Download">
              <Icon name="download" size={14}/>
            </button>
            <button type="button" className="btn-icon" onClick={() => setHtmlModal(false)} aria-label="Close" title="Close">
              <Icon name="x" size={14}/>
            </button>
          </div>
          <div className="pdf-modal__body" style={{ background: "var(--surface)" }}>
            <HtmlViewer fileId={file.id} fileName={file.name}/>
          </div>
        </div>
      </div>
    )}
    <aside className="preview" aria-label="File details" onClick={(e) => e.stopPropagation()}>
      <div className="preview__head">
        <div className="preview__head-title mono">DETAILS</div>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            className="btn-icon"
            aria-label={starred ? "Unstar" : "Star"}
            aria-pressed={starred}
            onClick={handleToggleStar}
            style={starred ? { color: "var(--accent, #f5a623)" } : undefined}
            title={starred ? "Starred" : "Star"}
          >
            <Icon name="star" size={15} strokeWidth={starred ? 0 : 1.6}
                  style={starred ? { fill: "currentColor" } : undefined}/>
          </button>
          <button className="btn-icon" onClick={onClose} aria-label="Close">
            <Icon name="x" size={15}/>
          </button>
        </div>
      </div>

      {isSpreadsheetFile ? (
        <button
          type="button"
          onClick={() => setSpreadsheetModal(true)}
          className="preview__hero"
          aria-label="Open spreadsheet"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="spreadsheet" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isPdf ? (
        // Hero shows the non-scrolling first-page raster (the same
        // image used on the gallery card thumb). Clicking opens a full
        // scrollable browser-native PDF modal. When the raster isn't
        // available (PyMuPDF wasn't installed when the file uploaded —
        // run /admin → Generate PDF thumbnails to backfill), we fall
        // back to the icon and still let the click open the modal.
        file.thumb ? (
          // Fill the hero area with the page-1 raster (cover, anchored
          // to the top so the page header is what the user sees). The
          // previous `contain` left grey letterbox bars that made the
          // page look like a small floating PDF in the corner of the
          // pane. Cover with top alignment matches Drive's doc preview
          // style and reads as "page". The pdf-page modifier swaps in
          // a white background so the bottom edge of a short page
          // doesn't show the app's surface color underneath.
          <AuthedThumb
            url={file.thumb}
            className="preview__hero preview__hero--pdf-page"
            onClick={() => setPdfModal(true)}
            role="button"
            aria-label="Open document"
            title="Click to open document"
            style={{
              cursor: "pointer",
              backgroundSize: "cover",
              backgroundRepeat: "no-repeat",
              backgroundPosition: "center top",
              backgroundColor: "#fff",
            }}
          />
        ) : (
          <button
            type="button"
            onClick={() => setPdfModal(true)}
            className="preview__hero"
            aria-label="Open document"
            title="Click to open document"
            style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
          >
            <div className="thumb-icon">
              <Icon name="document" size={42} strokeWidth={1.3}/>
              <span className="mono">PDF</span>
            </div>
          </button>
        )
      ) : isCode ? (
        // Code: hero shows the language icon (same as the gallery card).
        // Clicking opens a full modal with syntax-highlighted lines —
        // matches the PDF UX where the hero is a static preview and
        // the modal is the scrollable reader.
        <button
          type="button"
          onClick={() => setCodeModal(true)}
          className="preview__hero"
          aria-label="Open code viewer"
          title="Click to open code viewer"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <LanguageIcon name={languageIcon(file.ext)} size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isVideoFile ? (
        // Video hero: server-rasterized poster frame (when available)
        // with a centered play overlay. Click anywhere on the hero
        // opens the themed video player full-screen. Falls back to a
        // play-button + extension chip when no thumb has been
        // generated yet.
        <button
          type="button"
          onClick={() => setVideoModal(true)}
          className="preview__hero preview__hero--video"
          aria-label="Play video"
          title="Click to play"
          style={{ position: "relative", padding: 0, border: 0, cursor: "pointer", background: "var(--surface-2)" }}
        >
          {file.thumb ? (
            <AuthedThumb url={file.thumb} className="preview__hero" style={{ width: "100%", height: "100%" }}/>
          ) : (
            <div style={{ display: "grid", placeItems: "center", width: "100%", height: "100%", color: "var(--ink-3)" }}>
              <div className="thumb-icon">
                <Icon name="video" size={42} strokeWidth={1.3}/>
                <span className="mono">{file.ext}</span>
              </div>
            </div>
          )}
          <span className="preview__play-overlay" aria-hidden="true">
            <Icon name="play" size={28}/>
          </span>
        </button>
      ) : isAudioFile ? (
        <button
          type="button"
          onClick={() => setAudioModal(true)}
          className="preview__hero preview__hero--audio"
          aria-label="Play audio"
          title="Click to play"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="music" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
          <span className="preview__play-overlay" aria-hidden="true">
            <Icon name="play" size={28}/>
          </span>
        </button>
      ) : isCsvFile ? (
        <button
          type="button"
          onClick={() => setCsvModal(true)}
          className="preview__hero"
          aria-label="Open spreadsheet"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="spreadsheet" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isIcsFile ? (
        <button
          type="button"
          onClick={() => setIcsModal(true)}
          className="preview__hero"
          aria-label="Open calendar"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="calendar" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isVcfFile ? (
        <button
          type="button"
          onClick={() => setVcfModal(true)}
          className="preview__hero"
          aria-label="Open contact card"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="contact" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isMarkdownFile ? (
        <button
          type="button"
          onClick={() => setMarkdownModal(true)}
          className="preview__hero"
          aria-label="Open document"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="document" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isDataTreeFile ? (
        <button
          type="button"
          onClick={() => setDataTreeModal(true)}
          className="preview__hero"
          aria-label="Open data viewer"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <LanguageIcon name={languageIcon(file.ext)} size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isNotebookFile ? (
        <button
          type="button"
          onClick={() => setNotebookModal(true)}
          className="preview__hero"
          aria-label="Open notebook"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <LanguageIcon name={languageIcon(file.ext)} size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isModel3dFile ? (
        <button
          type="button"
          onClick={() => setModel3dModal(true)}
          className="preview__hero"
          aria-label="Open 3D model"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="cube" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isFontFile ? (
        <button
          type="button"
          onClick={() => setFontModal(true)}
          className="preview__hero"
          aria-label="Open font specimen"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="type" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isEbookFile ? (
        <button
          type="button"
          onClick={() => setEbookModal(true)}
          className="preview__hero"
          aria-label="Open book"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="book" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isArchiveFile ? (
        <button
          type="button"
          onClick={() => setArchiveModal(true)}
          className="preview__hero"
          aria-label="Open archive"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="archive" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isHtmlFile ? (
        // HTML hero: a code glyph that opens the sandboxed static preview.
        // The page itself renders inside HtmlViewer's locked-down iframe
        // (no scripts) — never in the hero.
        <button
          type="button"
          onClick={() => setHtmlModal(true)}
          className="preview__hero"
          aria-label="Open HTML preview"
          title="Click to open"
          style={{ display: "grid", placeItems: "center", color: "var(--ink-3)", background: "var(--surface-2)", border: 0, cursor: "pointer" }}
        >
          <div className="thumb-icon">
            <Icon name="code" size={42} strokeWidth={1.3}/>
            <span className="mono">{file.ext}</span>
          </div>
        </button>
      ) : isSvgFile ? (
        // SVG hero — render the authed ORIGINAL bytes as an <img> (inert:
        // browsers don't run scripts in img-loaded SVG). Click opens the
        // same lightbox, which also loads the original for SVG. Works even
        // when no served raster thumb exists. White matte so a
        // transparent SVG stays legible against the dark theme.
        <AuthedImg
          url={originalUrl(file.id)}
          className="preview__hero"
          alt={file.name}
          onClick={() => setLightbox(true)}
          role="button"
          aria-label="View full size"
          style={{ cursor: "pointer", width: "100%", height: "100%", objectFit: "contain", background: "#fff" }}
        />
      ) : file.thumb ? (
        <AuthedThumb
          url={file.thumb}
          className="preview__hero"
          onClick={isImage ? () => setLightbox(true) : undefined}
          role={isImage ? "button" : undefined}
          aria-label={isImage ? "View full size" : undefined}
        />
      ) : (
        // Generic fallback — uses the file-type catalog so every known
        // extension gets a clean themed glyph instead of the bare
        // "document" icon. Unknown extensions fall through to "file".
        <div className="preview__hero" style={{ display: "grid", placeItems: "center", color: "var(--ink-3)" }}>
          <div className="thumb-icon">
            <Icon name={fileTypeInfo(file.ext).icon} size={42} strokeWidth={1.3}/>
            <span className="mono">{fileTypeInfo(file.ext).label}</span>
          </div>
        </div>
      )}

      <div className="preview__body">
        <div className="preview__title">{file.name}</div>
        {/* Pending wins over `file.topic`: during a re-summarize the
            topic may still be the OLD value, so show the shimmer instead
            of stale AI text. */}
        {file.pendingSummary ? (
          <div className="preview__topic">
            <span className="kicker" style={{ marginRight: 8 }}><Icon name="sparkles" size={10} style={{ verticalAlign: "-1px" }}/> AI</span>
            <span className="skel skel--text" style={{ width: "60%", display: "inline-block" }} aria-label="Generating summary" title="Generating summary…"/>
          </div>
        ) : file.topic ? (
          <div className="preview__topic">
            <span className="kicker" style={{ marginRight: 8 }}><Icon name="sparkles" size={10} style={{ verticalAlign: "-1px" }}/> AI</span>
            {file.topic}
          </div>
        ) : null}
        <div className="preview__meta">
          <span>{file.when}</span>
          <span style={{ color: "var(--ink-4)" }}>·</span>
          <span className="mono">{file.size}</span>
          <span style={{ color: "var(--ink-4)" }}>·</span>
          <span className="mono">{file.ext}</span>
        </div>

        {/* Rich AI description block — the long-form `summary` text the
            C2 multi-model pipeline produces. This is what makes the
            file searchable, so we surface it prominently. Concept-tag
            chips below give the user immediate scannable keywords.
            When the backend is still summarizing (fresh upload, force
            backfill in flight), render a shimmer placeholder instead
            of an empty slot — the user can see the pipeline is still
            working rather than wondering why nothing's there. */}
        {/* Pending wins over `file.aiContent`: while the summarizer is
            (re)running, `aiContent` may still carry the OLD long-form
            text. Show the "Generating summary…" skeleton instead so we
            never present stale AI output as current — it swaps to the
            real description live once the worker commits (driven by the
            gated summarize-progress poll in app.jsx). */}
        {file.pendingSummary ? (
          <div className="preview__section">
            <div className="preview__section-label">
              Description
              <span className="kicker" style={{ marginLeft: 8, color: "var(--ink-3)" }}>Generating summary…</span>
            </div>
            <div style={{ padding: "0 12px" }} aria-label="Generating summary">
              <div className="skel skel--text" style={{ width: "92%" }}/>
              <div className="skel skel--text" style={{ width: "84%", marginTop: 6 }}/>
              <div className="skel skel--text" style={{ width: "67%", marginTop: 6 }}/>
            </div>
          </div>
        ) : file.aiContent ? (
          <div className="preview__section">
            <div className="preview__section-label">Description</div>
            <div style={{
              fontSize: 13, lineHeight: 1.55, color: "var(--ink-2)",
              padding: "0 12px",
            }}>
              {file.aiContent}
            </div>
            {Array.isArray(file.signals?.concepts) && file.signals.concepts.length > 0 && (
              <div className="ptags" style={{ marginTop: 10, padding: "0 12px" }}>
                {file.signals.concepts.slice(0, 8).map((c) => (
                  <span key={c} className="ptag" data-tone="muted">{c}</span>
                ))}
              </div>
            )}
          </div>
        ) : null}

        {isFaceCapable && imagePeople.length > 0 && (
          <div className="preview__section">
            <div className="preview__section-label" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span>{isVideo ? "People in this video" : "People in this photo"}</span>
              {/* Delete-this-person affordance — surfaced when the
                  gallery is drilled into a specific person (a person-
                  filtered set), satisfying "delete a person from the
                  mini preview". Removes the whole Person via the
                  parent's confirm + cache invalidation. */}
              {activePerson?.personId && onDeletePerson && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  style={{ padding: "2px 8px", fontSize: 11, color: "var(--danger)" }}
                  onClick={() => onDeletePerson({ personId: activePerson.personId, name: activePerson.name })}
                  title={`Delete ${activePerson.name || "this person"}`}
                >
                  <Icon name="trash" size={11}/> Delete person
                </button>
              )}
            </div>
            <div className="preview__faces">
              {imagePeople.map((p) => (
                <FaceChip
                  key={p.face_id ?? `c${p.cluster_id}`}
                  face={p}
                  file={file}
                  people={people}
                  onDeletePerson={onDeletePerson}
                />
              ))}
            </div>
            {/* One-line hint so the user knows the chips are correctable
                — addresses "opening a person shows images that don't
                contain them" by pointing at the fix affordance. */}
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6, lineHeight: 1.45 }}>
              Wrong match? Use the ⋯ on a face to remove it or move it to the right person.
            </div>
          </div>
        )}

        {/* D8 — when the default pipeline found no faces, let the user
            insist there's a person here. Click runs the cascade
            (RetinaFace 0.3 → 0.15 → mediapipe). On the still-empty
            terminal state, surface a "manual add" hint — the actual
            user-drawn-box flow is a follow-up. */}
        {isFaceCapable && imagePeople.length === 0 && (
          <div className="preview__section">
            <div className="preview__section-label">{isVideo ? "People in this video" : "People in this photo"}</div>
            {redetectState === "empty" ? (
              <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
                No faces found, even after re-running detection at a lower
                threshold and the mediapipe fallback.{" "}
                <button
                  type="button"
                  onClick={() => toast("Manual face-add is coming soon — draw a box around the face you want to label.")}
                  style={{ background: "none", border: 0, padding: 0, color: "var(--ink)", textDecoration: "underline", cursor: "pointer", font: "inherit" }}
                >
                  Need to add manually?
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={handleRedetect}
                disabled={redetectState === "running"}
                className="btn btn--ghost btn--sm"
                style={{ alignSelf: "flex-start" }}
              >
                <Icon name={redetectState === "running" ? "refresh" : "users"} size={12}/>
                {redetectState === "running" ? "Looking…" : "Mark as containing a person"}
              </button>
            )}
          </div>
        )}

        <div className="preview__section">
          {/* Collapsible Tags disclosure. The header is a real <button>
              (aria-expanded) with a chevron that rotates open/closed; the
              chip list + add-field live in the animated region below.
              Only the tag CHIPS collapse — the "Shared with" block stays
              visible underneath. */}
          <button
            type="button"
            className="preview__disclosure"
            aria-expanded={tagsOpen}
            aria-controls="preview-tags-region"
            onClick={toggleTagsOpen}
          >
            <Icon
              name="chevronRight"
              size={13}
              className="preview__disclosure-chev"
              data-open={tagsOpen ? "true" : "false"}
            />
            <span className="preview__disclosure-label">Tags</span>
            {tags.length > 0 && (
              <span className="preview__disclosure-count">{tags.length}</span>
            )}
          </button>
          <div
            id="preview-tags-region"
            className="preview-collapse"
            data-open={tagsOpen ? "true" : "false"}
          >
           <div className="preview-collapse__inner">
            <div className="ptags">
              {tags.map(t => {
                const tone =
                  /favorite|fav/i.test(t) ? "ink" :
                  /review/i.test(t) ? "warn" :
                  /shared/i.test(t) ? "info" :
                  /private|archived/i.test(t) ? "muted" :
                  /wip|work/i.test(t) ? "ok" : undefined;
                return (
                  <span key={t} className="ptag" data-tone={tone}>
                    {t}
                    <button className="ptag__x" onClick={() => removeTag(t)} aria-label={`Remove ${t}`}>
                      <Icon name="x" size={9} strokeWidth={2.4}/>
                    </button>
                  </span>
                );
              })}
              <div className="ptag-add">
                <input
                  value={draft}
                  placeholder={tags.length ? "Add tag" : "Add a tag…"}
                  onFocus={() => setShowSuggest(true)}
                  onBlur={() => setTimeout(() => setShowSuggest(false), 160)}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && draft.trim()) addTag(draft);
                    if (e.key === "Backspace" && !draft && tags.length) removeTag(tags[tags.length - 1]);
                  }}
                />
                {showSuggest && (
                  <div className="ptag-pop" onMouseDown={(e) => e.preventDefault()}>
                    {TAG_SUGGESTIONS.filter(s => !tags.includes(s) && s.toLowerCase().includes(draft.toLowerCase())).slice(0, 8).map(s => (
                      <button key={s} className="ptag-pop__item" onClick={() => addTag(s)}>
                        <Icon name="plus" size={10}/> {s}
                      </button>
                    ))}
                    {draft.trim() && !TAG_SUGGESTIONS.includes(draft.trim()) && (
                      <button className="ptag-pop__item ptag-pop__item--new" onClick={() => addTag(draft)}>
                        <Icon name="sparkles" size={10}/> Create "{draft.trim()}"
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
           </div>
          </div>

          {/* Shared with — live data from /images/{id}/shares (todo §1.1 / G1).
              Only renders for files the caller owns; non-owners get a 4xx
              from the backend and `shareGrants` stays []. The "+ Share"
              button is always shown so the owner has a single affordance
              for "share this with someone". */}
          <div className="sharedwith-block" style={{ marginTop: 10 }}>
            <div className="sharedwith-block__head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>Shared with</span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setShareModalOpen(true)}
                style={{ padding: "2px 8px", fontSize: 11 }}
              >
                <Icon name="plus" size={10}/> Share
              </button>
            </div>
            {shareGrants.length === 0 ? (
              <div style={{ fontSize: 11.5, color: "var(--ink-3)", padding: "6px 0" }}>
                Not shared with anyone.
              </div>
            ) : shareGrants.map((g) => {
              const label = g.recipient_display_name || g.recipient_email;
              const initials = label.split(/[\s@]+/).map(s => s[0]).filter(Boolean).slice(0,2).join("").toUpperCase();
              const when = g.recipient_user_id
                ? (g.expires_at ? `expires ${new Date(g.expires_at).toLocaleDateString()}` : "")
                : "pending signup";
              return (
                <div key={g.id} className="sharedwith-block__row">
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="sharedwith__avatar" style={{ width: 22, height: 22, fontSize: 10 }}>
                      {initials || "?"}
                    </span>
                    <strong>{label}</strong>
                  </div>
                  <span>{when}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="preview__section">
          <div className="kv">
            <div className="kv__row"><span className="kv__k">Type</span><span className="kv__v mono">{file.ext}</span></div>
            <div className="kv__row"><span className="kv__k">Size</span><span className="kv__v mono">{file.size}</span></div>
            <div className="kv__row"><span className="kv__k">Uploaded</span><span className="kv__v">{file.when}</span></div>
            {isImage && file.width && file.height && (
              <div className="kv__row"><span className="kv__k">Dimensions</span><span className="kv__v mono">{file.width} × {file.height}</span></div>
            )}
            {file.gps && (file.gps.place || file.gps.lat != null) && (
              <div className="kv__row">
                <span className="kv__k">Location</span>
                {/* User-facing location: show the reverse-geocoded
                    place name (e.g. "Salt Lake City, Utah") so the
                    preview reads like a human description. Coords are
                    kept on the data (file.gps.lat/lng) for map pin
                    rendering but deliberately not surfaced here —
                    nobody recognizes a file by its decimal degrees. */}
                <span className="kv__v" style={{ textAlign: "right" }}>
                  {file.gps.place || "Looking up location…"}
                </span>
              </div>
            )}
            {file.scene_label && (
              <div className="kv__row"><span className="kv__k">Scene</span><span className="kv__v">{file.scene_label}</span></div>
            )}
            {file.indoor_outdoor && (
              <div className="kv__row"><span className="kv__k">Setting</span><span className="kv__v">{file.indoor_outdoor}</span></div>
            )}
          </div>
        </div>

        <div className="preview__section">
          <div className="preview__section-label">Storage</div>
          <div className="kv">
            {file.byte_size_original != null && (
              <div className="kv__row">
                <span className="kv__k">Original size</span>
                <span className="kv__v mono">{fmtBytes(file.byte_size_original)}</span>
              </div>
            )}
            {file.byte_size_served != null && (
              <div className="kv__row">
                <span className="kv__k">Served size</span>
                <span className="kv__v mono">
                  {fmtBytes(file.byte_size_served)}
                  {file.byte_size_original ? (
                    <span style={{ color: "var(--ink-3)" }}>
                      {" "}
                      ({Math.round((1 - file.byte_size_served / file.byte_size_original) * 100)}% smaller)
                    </span>
                  ) : null}
                </span>
              </div>
            )}
            {file.codec && (
              <div className="kv__row">
                <span className="kv__k">Encoding</span>
                <span className="kv__v mono">
                  {file.codec.toUpperCase()}{file.quality ? ` · q ${file.quality}` : ""}
                </span>
              </div>
            )}
            {file.original_expires_at && (
              <div className="kv__row">
                <span className="kv__k">Original retained until</span>
                <span className="kv__v">
                  {(() => {
                    try {
                      const d = new Date(file.original_expires_at);
                      const dateStr = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
                      const daysLeft = Math.max(0, Math.round((d.getTime() - Date.now()) / 86_400_000));
                      return `${dateStr} · ${daysLeft} ${daysLeft === 1 ? "day" : "days"} left`;
                    } catch { return file.original_expires_at; }
                  })()}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="preview__foot">
        <button className="btn btn--secondary" style={{ flex: 1 }} onClick={handleDownload}>
          <Icon name="download" size={14}/> Download
        </button>
        <button
          className="btn btn--secondary"
          style={{ flex: 1 }}
          onClick={() => setShareModalOpen(true)}
          title="Share this file"
        >
          <Icon name="share" size={14}/> Share
        </button>
        <button
          className="btn-icon"
          aria-label="Move to trash"
          title="Delete"
          onClick={handleDelete}
        >
          <Icon name="trash" size={15}/>
        </button>
      </div>
    </aside>
    {shareModalOpen && file && (
      <ShareModal
        imageId={file.id}
        imageName={file.name || file.original_filename}
        onClose={() => setShareModalOpen(false)}
      />
    )}
    </React.Fragment>
  );
}

// Named export above; legacy `window.PreviewPanel` removed.
