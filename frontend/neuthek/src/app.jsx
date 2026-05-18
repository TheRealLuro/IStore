// Top-level App: stitches everything together. Manages routing, modals, selection.
//
// Wired to the real backend via React Query — `useFiles()` pulls from /images/
// and maps the FileItem → neuthek's mock-shape so the existing card / preview
// components keep working without rewrites. Auth uses src/stores/authStore for
// session bootstrap; sign-out hits the real token clearer.
import React, {
  useState as useStateApp,
  useEffect as useEffectApp,
  useRef as useRefApp,
  useMemo as useMemoApp,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Icon } from "./icons.jsx";
import {
  TermsModal,
  PrivacyModal,
  FaceConsentModal,
  CookieBanner,
  DeleteAccountModal,
  ExportModal,
} from "./policies.jsx";
import { Sidebar, GalleryView, EmptyGallery } from "./gallery.jsx";
import { UploadModal } from "./upload.jsx";
import { PreviewPanel } from "./preview.jsx";
import { AccountModal } from "./account.jsx";
import { AuthScreen } from "./auth.jsx";
import { RenameModal } from "./rename.jsx";
import { BestOfModal } from "./bestof.jsx";
// MapView (Leaflet, ~100 KB) and AdminOverlay (admin-only) are heavy chunks
// that most users never need on first paint. Lazy-load them so the initial
// bundle drops below the 500 KB warning. React.Suspense fallbacks render a
// thin "Loading…" while the chunk arrives.
const MapView = React.lazy(() =>
  import("./map.jsx").then((m) => ({ default: m.MapView })),
);
const AdminOverlay = React.lazy(() =>
  import("./admin.jsx").then((m) => ({ default: m.AdminOverlay })),
);
import { NewFolderModal } from "./folder-modals.jsx";
import { SharedGalleryView } from "./shared.jsx";
import { useAuthStore } from "@/stores/authStore";
import {
  listFiles, getImageGeo, servedUrl, renameImage,
  getSummarizeProgress, searchSemantic, getFacets,
  bulkDelete, bulkRestore, bulkMove, createFolderWithImages,
  clearSearchHistory,
} from "@/api/files";
import { createShare, buildShareUrlWithEmail, listIncomingShares } from "@/api/shares";
import { eraseImageCaches } from "./cache-eraser.js";
import { listFolders } from "@/api/folders";
import { listPeople, faceCropUrl, detectAndLabel } from "@/api/people";
import { AuthedThumb } from "./auth-image.jsx";
import { EditableName } from "./nameable-chip.jsx";
import { getConsentScopes, grantConsent } from "@/api/consent";
import { getAccountTrash } from "@/api/auth";
import toast from "react-hot-toast";
import { formatDistanceToNowStrict } from "date-fns";
// Recent searches are persisted per-browser to localStorage. The mock
// `RECENT_SEARCHES` seed list is intentionally NOT imported — a fresh
// account should start empty and grow as the user actually searches.
const SEARCH_HISTORY_KEY = "neuthek.recentSearches";
function loadRecentSearches() {
  try {
    const raw = localStorage.getItem(SEARCH_HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((s) => typeof s === "string") : [];
  } catch { return []; }
}
function saveRecentSearches(list) {
  try { localStorage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(list.slice(0, 8))); } catch {}
}

// Top-of-app sticky banner that shows summary-regen progress while the
// worker is draining the queue. Reuses the `["summarize-progress"]` cache
// key so it deduplicates against the Account modal's poll. `onDismiss`
// flips a session flag in the parent so the banner stays hidden for the
// rest of this tab session even while pending > 0.
function SummarizingBanner({ signedIn, dismissed, onDismiss }) {
  const { data: progress } = useQuery({
    queryKey: ["summarize-progress"],
    queryFn: getSummarizeProgress,
    enabled: signedIn && !dismissed,
    refetchInterval: (q) =>
      q.state.data && q.state.data.pending > 0 ? 2000 : false,
    staleTime: 1000,
  });
  if (!progress || dismissed) return null;
  if (progress.pending <= 0) return null;
  if (!progress.has_any_summary) return null; // brand-new account, skip
  const pct = progress.total ? Math.round((progress.completed / progress.total) * 100) : 0;
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "relative",
        height: 32,
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "0 16px",
        background: "var(--surface)",
        borderBottom: "1px solid var(--line)",
        fontSize: 12.5,
        color: "var(--ink-2)",
        overflow: "hidden",
      }}
    >
      <div style={{
        position: "absolute",
        left: 0, top: 0, bottom: 0,
        width: `${pct}%`,
        background: "var(--surface-2)",
        transition: "width 250ms ease-out",
        zIndex: 0,
      }}/>
      <span style={{ position: "relative", zIndex: 1, display: "inline-flex", alignItems: "center", gap: 6 }}>
        <Icon name="sparkles" size={12}/>
        Summarizing {progress.completed} of {progress.total} · {pct}%
      </span>
      <span style={{ flex: 1 }}/>
      <button
        onClick={onDismiss}
        title="Dismiss for this session"
        aria-label="Dismiss"
        style={{ position: "relative", zIndex: 1, background: "none", border: 0, padding: 4, color: "var(--ink-3)", cursor: "pointer" }}
      >
        <Icon name="x" size={12}/>
      </button>
    </div>
  );
}

// Map a real FileItem (backend shape) to neuthek's mock-shape so the existing
// gallery / preview / cards keep working without rewrites. Anything we can't
// derive from the FileItem (mock topic / aiContent / gps / tags / folder) is
// either left unset or filled in with safe defaults.
// Code/text MIMEs the FE recognizes for the dedicated code-icon
// rendering path. Keep this in sync with backend/upload_validation.py
// `_CODE_EXTS` values (the right-hand side mime, not the keys).
const CODE_MIME_PREFIXES = ["text/x-", "text/javascript", "text/css", "text/html", "text/xml", "text/markdown", "application/json", "application/x-ipynb"];

function isCodeMime(mime) {
  if (!mime) return false;
  return CODE_MIME_PREFIXES.some((p) => mime.startsWith(p));
}

function fileItemToNeuthek(f) {
  const cat = f.category;
  let type = cat === "document" ? "doc" : cat === "image" ? "image" : cat === "video" ? "video" : "doc";
  // Special-case code / structured-text files: they're served as
  // `document` upstream but the gallery card + preview hero should
  // show a code icon, not the generic page-of-paper icon.
  if (cat === "document" && isCodeMime(f.mime_type_original)) {
    type = "code";
  }
  const ext = (f.original_filename || "").split(".").pop()?.toUpperCase() || (f.mime_type_original?.split("/")[1] || "FILE").toUpperCase();
  const sizeBytes = f.byte_size_served ?? f.byte_size_original ?? 0;
  const size = sizeBytes < 1024 * 1024
    ? Math.round(sizeBytes / 1024) + " KB"
    : sizeBytes < 1024 * 1024 * 1024
      ? (sizeBytes / 1024 / 1024).toFixed(1) + " MB"
      : (sizeBytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
  let when = "—";
  try {
    when = formatDistanceToNowStrict(new Date(f.uploaded_at), { addSuffix: true });
  } catch { /* keep default */ }
  // Docs (PDF page-1 rasters land in the same served bucket as image
  // thumbs once the backend rasterize-at-upload path is wired). Until
  // then, FE only emits a thumb for images; the preview hero falls
  // back to the icon otherwise.
  const hasServedThumb = type === "image" || (cat === "document" && f.mime_type_served && f.mime_type_served.startsWith("image/"));

  return {
    id: f.id,
    type,
    category: cat,
    name: f.original_filename || `untitled.${ext.toLowerCase()}`,
    size,
    when,
    // Card thumb caps at 600 px on the longest side (room for retina
    // grids @ 300 CSS px). Backend resizes + caches the variant; the
    // network payload drops from MB-scale to ~20 KB per card so a
    // 60-card scroll doesn't decode 50+ MB of WebP.
    thumb: hasServedThumb ? servedUrl(f.id, { maxDim: 600 }) : null,
    // Full-resolution path for the preview-hero lightbox + sharing.
    thumbFull: hasServedThumb ? servedUrl(f.id) : null,
    ext,
    topic: f.summary_topic,
    aiContent: f.summary,
    signals: f.summary_signals || null,
    // Surfaced to the preview panel + gallery card so they can render a
    // shimmer placeholder in the topic/description slot while the
    // background summarizer is still working. Without this, fresh
    // uploads sit with empty AI fields and look like they failed.
    pendingSummary: !!f.pending_summary,
    // Raw timestamp + bytes used by the gallery sort. `when` (above) is
    // a humanized "12 days ago" string for display only.
    uploaded_at: f.uploaded_at,
    gps: null, // wired separately from /images/geo
    tags: f.status ? [f.status] : [],
    folder: f.folder_id,
    width: f.width,
    height: f.height,
    byte_size_original: f.byte_size_original,
    byte_size_served: f.byte_size_served,
    codec: f.codec,
    quality: f.quality,
    mime_type_original: f.mime_type_original,
    mime_type_served: f.mime_type_served,
    original_expires_at: f.original_expires_at,
    scene_label: f.scene_label,
    indoor_outdoor: f.indoor_outdoor,
    is_starred: !!f.is_starred,
    starred_at: f.starred_at,
    _real: true,
  };
}

const VIEW_LABELS = {
  gallery: "All files",
  photos: "Photos",
  videos: "Videos",
  docs: "Documents",
  starred: "Starred",
  people: "People",
  places: "Map",
  shared: "Shared",
  trash: "Trash",
};

// Tweaks panel was a dev-only prototype tool — replaced with a frozen
// production config object. Future work can re-introduce a real settings
// surface that drives these values from user preferences. `showAttention`
// is now derived from real consent state below — not statically true.
const t = {
  requireFace: false,
  allowEarlyAI: true,
  showCookieBanner: true,
  // People strip removed from the All Files page — the People sidebar
  // tab is the canonical place to find people, and the strip ate
  // ~80px of vertical space at the top of every gallery view.
  showPeopleStrip: false,
  showFolders: true,
  density: "regular",
  brandPitch: "",
  showAdmin: false,
  showLogout: true,
  showFab: true,
};

// Bulk-action toolbar surfaced in the subbar whenever multi-select
// has at least one entry. Wraps the four actions the user actually
// wants on a selection — Move to folder · New folder w/ selection ·
// Pick best of (images-only) · Delete — plus a Clear/exit button.
// Built local to <App/> via lazy-imported helpers so the toolbar
// owns the queryClient invalidation contract for each action.
function DetectPersonModal({ open, imageCount, name, setName, progress, running, onSubmit, onClose }) {
  // Close on Escape unless a scan is in flight.
  useEffectApp(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape" && !running) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, running, onClose]);
  if (!open) return null;
  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;
  return (
    <div
      className="lightbox"
      onClick={() => { if (!running) onClose(); }}
      style={{ background: "rgba(0,0,0,0.45)" }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--surface)",
          color: "var(--ink)",
          borderRadius: 14,
          width: "min(440px, calc(100vw - 32px))",
          padding: 22,
          boxShadow: "0 20px 60px rgba(0,0,0,0.3)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
          <Icon name="users" size={18}/>
          <strong style={{ fontSize: 15 }}>Detect person</strong>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginBottom: 16 }}>
          Runs face detection on {imageCount} selected image{imageCount === 1 ? "" : "s"}
          and labels every face it finds. The label also trains the
          clustering centroid so future uploads of the same person
          auto-group to them.
        </div>

        {!running && progress.total === 0 && (
          <form onSubmit={onSubmit}>
            <label style={{ display: "block", fontSize: 12, color: "var(--ink-3)", marginBottom: 4 }}>
              Name
            </label>
            <input
              autoFocus
              className="input input--lg"
              placeholder="e.g. Alice Johnson"
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={{ width: "100%" }}
            />
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
              <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
              <button type="submit" className="btn btn--primary" disabled={!name.trim()}>
                Start scan
              </button>
            </div>
          </form>
        )}

        {(running || progress.total > 0) && (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--ink-3)", marginBottom: 6 }}>
              <span>Scanning for "{name}"…</span>
              <span className="mono">{progress.done} / {progress.total}</span>
            </div>
            <div
              style={{
                height: 8,
                borderRadius: 999,
                background: "var(--surface-2)",
                overflow: "hidden",
              }}
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: "100%",
                  background: "var(--accent, #5b8def)",
                  transition: "width 240ms ease",
                }}
              />
            </div>
            <div style={{ marginTop: 12, fontSize: 12, color: "var(--ink-3)" }}>
              Found {progress.found} face{progress.found === 1 ? "" : "s"} so far.
            </div>
            {!running && progress.done === progress.total && (
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
                <button type="button" className="btn btn--primary" onClick={onClose}>Done</button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function BulkActionBar({ selectedIds, selectedAreAllImages, onClear, onPickBestOf, inTrashView = false }) {
  const qc = useQueryClient();
  const moveDdRef = useRefApp(null);
  const newFolderDdRef = useRefApp(null);
  const shareDdRef = useRefApp(null);
  // Outside-click + Escape dismissal for the three bulk-bar popovers.
  // `<details>` doesn't ship that behavior natively; without these
  // hooks the user has to click the same summary chevron twice to
  // close, which felt wrong (caught in the 2026-05-16 audit pass).
  useDetailsAutoClose(moveDdRef);
  useDetailsAutoClose(newFolderDdRef);
  useDetailsAutoClose(shareDdRef);
  const [newFolderName, setNewFolderName] = useStateApp("");
  const [busy, setBusy] = useStateApp(null); // "move" | "new" | "delete" | "share"
  const [shareEmail, setShareEmail] = useStateApp("");
  const [shareDuration, setShareDuration] = useStateApp(86400);
  const [issuedLinks, setIssuedLinks] = useStateApp([]); // [{filename?, url}]

  const { data: folders = [] } = useQuery({
    queryKey: ["folders", null],
    queryFn: () => listFolders(null),
    staleTime: 30_000,
  });

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ["files"] });
    qc.invalidateQueries({ queryKey: ["folders"] });
    qc.invalidateQueries({ queryKey: ["facets"] });
  };

  const doMoveTo = async (folderId) => {
    if (busy) return;
    setBusy("move");
    try {
      const r = await bulkMove(selectedIds, folderId);
      const where = folderId
        ? `"${folders.find(f => f.id === folderId)?.name || "folder"}"`
        : "root";
      const skipNote = r.skipped > 0 ? ` (${r.skipped} skipped)` : "";
      toast.success(`Moved ${r.moved} to ${where}${skipNote}`);
      invalidateAll();
      onClear();
    } catch (e) {
      toast.error(e?.detail || "Move failed");
    } finally {
      setBusy(null);
      if (moveDdRef.current) moveDdRef.current.open = false;
    }
  };

  const doCreateFolderWithSelection = async (e) => {
    e?.preventDefault();
    const name = newFolderName.trim();
    if (!name || busy) return;
    setBusy("new");
    try {
      const f = await createFolderWithImages(name, selectedIds, null);
      toast.success(`Created "${f.name}" with ${f.item_count} files`);
      invalidateAll();
      onClear();
      setNewFolderName("");
      if (newFolderDdRef.current) newFolderDdRef.current.open = false;
    } catch (err) {
      toast.error(err?.detail || "Create failed");
    } finally {
      setBusy(null);
    }
  };

  const doDelete = async () => {
    if (busy) return;
    // In the trash view, "Delete" means permanent purge (the items
    // are already trashed). Elsewhere it's soft-delete → moves to
    // Trash with deleted_at set so the user can still restore.
    const verb = inTrashView ? "Permanently delete" : "Move";
    const tail = inTrashView ? " — this can't be undone." : " to trash?";
    if (!window.confirm(`${verb} ${selectedIds.length} item${selectedIds.length === 1 ? "" : "s"}${tail}`)) return;
    setBusy("delete");
    try {
      const r = await bulkDelete(selectedIds, { purge: inTrashView });
      const skipNote = r.skipped_count > 0 ? ` (${r.skipped_count} skipped)` : "";
      toast.success(
        inTrashView
          ? `Permanently deleted ${r.count}${skipNote}`
          : `Moved ${r.count} to trash${skipNote}`,
      );
      if (inTrashView) {
        // Only erase per-id caches when we've truly purged the row;
        // soft-deletes keep the row alive in the trash slice and
        // restoring needs the cached thumbnail.
        await eraseImageCaches(qc, selectedIds);
      }
      invalidateAll();
      qc.invalidateQueries({ queryKey: ["files", "TRASHED"] });
      onClear();
    } catch (e) {
      toast.error(e?.detail || "Delete failed");
    } finally {
      setBusy(null);
    }
  };

  const doRestore = async () => {
    if (busy) return;
    setBusy("restore");
    try {
      const r = await bulkRestore(selectedIds);
      toast.success(`Restored ${r.count}`);
      invalidateAll();
      qc.invalidateQueries({ queryKey: ["files", "TRASHED"] });
      onClear();
    } catch (e) {
      toast.error(e?.detail || "Restore failed");
    } finally {
      setBusy(null);
    }
  };

  // "Detect person" modal state. The modal shows the name input,
  // then once submitted swaps to a per-batch progress bar so the user
  // can see scanning isn't frozen. Batches keep the request small so
  // a 50-image bulk doesn't burn 60+ s on one HTTP call (the face
  // pipeline is ~1 s per image on CPU).
  const [detectModalOpen, setDetectModalOpen] = useStateApp(false);
  const [detectName, setDetectName] = useStateApp("");
  const [detectProgress, setDetectProgress] = useStateApp({ done: 0, total: 0, found: 0 });
  const doDetectAndLabel = async (e) => {
    e?.preventDefault?.();
    const name = detectName.trim();
    if (!name || busy) return;
    setBusy("detect");
    const BATCH = 5;
    const total = selectedIds.length;
    let found = 0;
    let scanned = 0;
    setDetectProgress({ done: 0, total, found: 0 });
    try {
      for (let i = 0; i < selectedIds.length; i += BATCH) {
        const slice = selectedIds.slice(i, i + BATCH);
        const r = await detectAndLabel(slice, name);
        scanned += r.images_scanned;
        found += r.new_faces;
        setDetectProgress({ done: Math.min(i + slice.length, total), total, found });
        // Refresh queries between batches so the gallery + people sidebar
        // tick up while the long scan is running.
        qc.invalidateQueries({ queryKey: ["people"] });
        qc.invalidateQueries({ queryKey: ["files"] });
      }
      toast.success(
        found > 0
          ? `Labeled ${found} face${found === 1 ? "" : "s"} across ${scanned} image${scanned === 1 ? "" : "s"} as "${name}". Future detections will cluster to them automatically.`
          : `Scanned ${scanned} image${scanned === 1 ? "" : "s"} but didn't find any new faces.`,
      );
      qc.invalidateQueries({ queryKey: ["image-people"] });
      setDetectModalOpen(false);
      setDetectName("");
      onClear();
    } catch (err) {
      toast.error(err?.detail || "Detect failed");
    } finally {
      setBusy(null);
    }
  };

  // Bulk share — V1 ships per-image grants (todo §1.1 / G1 picked
  // single-image granularity), so a multi-select share fires N
  // independent createShare calls and returns N URLs the sharer can
  // copy individually. Multi-image-single-link is a future feature.
  const doBulkShare = async (e) => {
    e?.preventDefault?.();
    const trimmed = shareEmail.trim();
    if (!trimmed || busy) return;
    setBusy("share");
    setIssuedLinks([]);
    const links = [];
    try {
      for (const id of selectedIds) {
        const grant = await createShare(id, {
          recipientEmail: trimmed,
          durationSeconds: shareDuration,
        });
        if (grant.share_url) {
          links.push({
            id,
            url: buildShareUrlWithEmail(grant.share_url, trimmed),
          });
        }
      }
      setIssuedLinks(links);
      qc.invalidateQueries({ queryKey: ["image-shares"] });
      qc.invalidateQueries({ queryKey: ["incoming-shares"] });
      toast.success(`Created ${links.length} share link${links.length === 1 ? "" : "s"} for ${trimmed}`);
    } catch (err) {
      toast.error(err?.detail || "Share failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="bulk-bar" role="toolbar" aria-label="Bulk actions">
      <span className="bulk-bar__count">
        {selectedIds.length} selected
      </span>

      {/* Move to existing folder */}
      <details className="sort-dd" ref={moveDdRef}>
        <summary className="btn btn--ghost btn--sm sort-dd__btn" title="Move selection to folder">
          <Icon name="folder" size={12}/> Move to
          <Icon name="chevronDown" size={11} style={{ marginLeft: 2, opacity: 0.7 }}/>
        </summary>
        <div className="sort-dd__panel" style={{ minWidth: 200, maxHeight: 280, overflowY: "auto" }}>
          <button
            type="button"
            className="sort-dd__opt"
            onClick={() => doMoveTo(null)}
            disabled={busy === "move"}
          >
            <span><Icon name="home" size={11} style={{ marginRight: 6 }}/>Root (no folder)</span>
          </button>
          {folders.length === 0 && (
            <div style={{ padding: 10, fontSize: 12, color: "var(--ink-3)" }}>
              No folders yet — use "New folder" instead.
            </div>
          )}
          {folders.map(f => (
            <button
              key={f.id}
              type="button"
              className="sort-dd__opt"
              onClick={() => doMoveTo(f.id)}
              disabled={busy === "move"}
            >
              <span><Icon name="folder" size={11} style={{ marginRight: 6 }}/>{f.name}</span>
              <span style={{ color: "var(--ink-4)", fontSize: 11 }}>{f.item_count}</span>
            </button>
          ))}
        </div>
      </details>

      {/* New folder with selection — single round trip */}
      <details className="sort-dd" ref={newFolderDdRef}>
        <summary className="btn btn--ghost btn--sm sort-dd__btn" title="Create folder with selection">
          <Icon name="folderPlus" size={12}/> New folder
          <Icon name="chevronDown" size={11} style={{ marginLeft: 2, opacity: 0.7 }}/>
        </summary>
        <div className="sort-dd__panel" style={{ minWidth: 240, padding: 10 }}>
          <form onSubmit={doCreateFolderWithSelection} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <label style={{ fontSize: 11, color: "var(--ink-3)", letterSpacing: "0.1em", textTransform: "uppercase" }}>
              New folder name
            </label>
            <input
              autoFocus
              type="text"
              placeholder="e.g. Trip 2026"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              style={{
                padding: "6px 10px",
                background: "var(--surface-2)",
                border: "1px solid var(--line)",
                borderRadius: 7,
                color: "var(--ink)",
                fontSize: 13,
                outline: "none",
              }}
            />
            <button
              type="submit"
              className="btn btn--primary btn--sm"
              disabled={!newFolderName.trim() || busy === "new"}
            >
              {busy === "new"
                ? "Creating…"
                : `Create + add ${selectedIds.length} file${selectedIds.length === 1 ? "" : "s"}`}
            </button>
          </form>
        </div>
      </details>

      {/* Pick best of — only meaningful when the selection is all photos */}
      {selectedAreAllImages && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onPickBestOf}
          title="Pick the best photo from this selection"
        >
          <Icon name="wand" size={12}/> Pick best of
        </button>
      )}

      {/* Bulk share — creates one grant per selected file and returns
          a copy-able link for each. New-recipient 1-day cap is enforced
          server-side per grant (see backend/api/shares.py). */}
      <details className="sort-dd" ref={shareDdRef}>
        <summary className="btn btn--ghost btn--sm sort-dd__btn" title="Share selection">
          <Icon name="share" size={12}/> Share
          <Icon name="chevronDown" size={11} style={{ marginLeft: 2, opacity: 0.7 }}/>
        </summary>
        <div className="sort-dd__panel" style={{ minWidth: 300, padding: 12 }}>
          <form onSubmit={doBulkShare} style={{ display: "grid", gap: 8 }}>
            <label style={{ fontSize: 11, color: "var(--ink-3)", letterSpacing: "0.1em", textTransform: "uppercase" }}>
              Share {selectedIds.length} file{selectedIds.length === 1 ? "" : "s"} with
            </label>
            <input
              type="email"
              required
              autoFocus
              value={shareEmail}
              onChange={(e) => setShareEmail(e.target.value)}
              placeholder="them@example.com"
              style={{
                padding: "6px 10px",
                background: "var(--surface-2)",
                border: "1px solid var(--line)",
                borderRadius: 7,
                color: "var(--ink)",
                fontSize: 13,
                outline: "none",
              }}
            />
            <select
              value={shareDuration}
              onChange={(e) => setShareDuration(Number(e.target.value))}
              style={{
                padding: "6px 10px",
                background: "var(--surface-2)",
                border: "1px solid var(--line)",
                borderRadius: 7,
                color: "var(--ink)",
                fontSize: 13,
                outline: "none",
              }}
            >
              <option value={3600}>1 hour</option>
              <option value={86400}>1 day</option>
              <option value={7 * 86400}>7 days</option>
              <option value={30 * 86400}>30 days</option>
            </select>
            <button
              type="submit"
              className="btn btn--primary btn--sm"
              disabled={!shareEmail.trim() || busy === "share"}
            >
              {busy === "share" ? "Creating links…" : "Create links"}
            </button>
          </form>
          {issuedLinks.length > 0 && (
            <div style={{ marginTop: 10, display: "grid", gap: 6, maxHeight: 200, overflowY: "auto" }}>
              <div style={{ fontSize: 11, color: "var(--ink-3)" }}>
                Copy each link and send it to the recipient.
              </div>
              {issuedLinks.map((link) => (
                <div key={link.id} style={{ display: "flex", gap: 6 }}>
                  <input
                    readOnly
                    value={link.url}
                    onFocus={(e) => e.currentTarget.select()}
                    style={{
                      flex: 1, padding: "5px 8px", borderRadius: 6,
                      border: "1px solid var(--line)", background: "var(--surface)",
                      color: "var(--ink)", fontSize: 11, fontFamily: "monospace",
                    }}
                  />
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      navigator.clipboard.writeText(link.url).then(
                        () => toast.success("Copied"),
                        () => toast.error("Copy failed — select manually"),
                      );
                    }}
                  >
                    Copy
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </details>

      {inTrashView && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={doRestore}
          disabled={busy === "restore"}
          title="Restore selection from trash"
        >
          <Icon name="refresh" size={12}/> {busy === "restore" ? "Restoring…" : "Restore"}
        </button>
      )}

      {!inTrashView && selectedAreAllImages && (
        <button
          type="button"
          className="btn btn--secondary btn--sm"
          onClick={() => setDetectModalOpen(true)}
          disabled={busy === "detect"}
          title="Detect faces in the selected images and label them"
        >
          <Icon name="users" size={12}/> Detect person
        </button>
      )}

      {detectModalOpen && (
        <DetectPersonModal
          open={detectModalOpen}
          imageCount={selectedIds.length}
          name={detectName}
          setName={setDetectName}
          progress={detectProgress}
          running={busy === "detect"}
          onSubmit={doDetectAndLabel}
          onClose={() => { if (busy !== "detect") setDetectModalOpen(false); }}
        />
      )}

      <button
        type="button"
        className="btn btn--ghost btn--sm bulk-bar__danger"
        onClick={doDelete}
        disabled={busy === "delete"}
        title={inTrashView ? "Permanently delete selection — can't be undone" : "Move selection to trash"}
      >
        <Icon name="trash" size={12}/> {busy === "delete" ? "Deleting…" : (inTrashView ? "Delete forever" : "Delete")}
      </button>

      <button
        type="button"
        className="btn-icon"
        onClick={onClear}
        title="Clear selection"
        aria-label="Clear selection"
      >
        <Icon name="x" size={12}/>
      </button>
    </div>
  );
}

// Shared "click outside to close" hook for `<details>`-based popovers.
// Native `<details>` doesn't dismiss on outside click — that was a
// real UX bug in earlier builds (operator saw the Sort + Filters
// menus stay open while clicking thumbnails). Pass the details
// element's ref; this binds a one-time mousedown listener while the
// popover is open and closes it on any click outside the element.
// Escape is also wired so keyboard users get the same behavior.
function useDetailsAutoClose(detailsRef) {
  useEffectApp(() => {
    const el = detailsRef.current;
    if (!el) return undefined;
    const onMouseDown = (e) => {
      if (!el.open) return;
      if (!el.contains(e.target)) {
        el.open = false;
      }
    };
    const onKey = (e) => {
      if (e.key === "Escape" && el.open) el.open = false;
    };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [detailsRef]);
}

// SortDropdown — collapses the three sort tabs (Recent / Name / Size)
// into one compact subbar control. The inline direction arrow lets
// the user flip asc/desc without leaving the popover. Built on the
// same `<details>` primitive as `FiltersDropdown` + the shared
// `useDetailsAutoClose` hook for outside-click dismissal.
const SORT_OPTIONS = [
  { id: "recent", label: "Recent",  defaultDir: "desc" },
  { id: "name",   label: "Name",    defaultDir: "asc"  },
  { id: "size",   label: "Size",    defaultDir: "asc"  },
];
function SortDropdown({ sort, sortDir, setSort, setSortDir }) {
  const detailsRef = useRefApp(null);
  useDetailsAutoClose(detailsRef);
  const active = SORT_OPTIONS.find(o => o.id === sort) || SORT_OPTIONS[0];
  const onPick = (id) => {
    if (id === sort) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSort(id);
      const def = SORT_OPTIONS.find(o => o.id === id)?.defaultDir || "desc";
      setSortDir(def);
    }
    if (detailsRef.current) detailsRef.current.open = false;
  };
  return (
    <details className="sort-dd" ref={detailsRef}>
      <summary className="btn btn--ghost btn--sm sort-dd__btn">
        <Icon name="sort" size={12}/>
        {active.label}
        <Icon
          name={sortDir === "asc" ? "chevronUp" : "chevronDown"}
          size={11}
          style={{ marginLeft: 2, opacity: 0.7 }}
        />
      </summary>
      <div className="sort-dd__panel">
        {SORT_OPTIONS.map(o => (
          <button
            key={o.id}
            type="button"
            className="sort-dd__opt"
            data-active={sort === o.id}
            onClick={() => onPick(o.id)}
          >
            <span>{o.label}</span>
            {sort === o.id && (
              <Icon
                name={sortDir === "asc" ? "chevronUp" : "chevronDown"}
                size={11}
              />
            )}
          </button>
        ))}
      </div>
    </details>
  );
}

// FiltersDropdown — single subbar control that collapses every facet
// chip (scene / content type / indoor-outdoor / has-faces / has-gps)
// into one button + a popover. The popover is a `<details>` so we
// don't need our own outside-click handler — clicking elsewhere on
// the page just leaves it open until Escape or another click on the
// summary; that's the standard `<details>` UX. Active filters get a
// numeric badge on the button so the count stays glanceable when
// the dropdown is closed.
function FiltersDropdown({
  facets,
  filterScene, setFilterScene,
  filterContentType, setFilterContentType,
  filterIndoorOutdoor, setFilterIndoorOutdoor,
  filterHasFaces, setFilterHasFaces,
  filterHasGps, setFilterHasGps,
  // §C9 — added in this pass. People + Tags surface from facets.persons /
  // facets.tags; Date range is a free-form pair of <input type="date">.
  // All three remain optional so the dropdown still works on backends
  // that haven't redeployed yet (defensive ?. everywhere below).
  filterPersonId, setFilterPersonId,
  filterTag, setFilterTag,
  filterDateRange, setFilterDateRange,
  anyFilterActive,
  clearAllFilters,
  activeCount,
}) {
  const [search, setSearch] = useStateApp("");
  const detailsRef = useRefApp(null);
  const inputRef = useRefApp(null);
  useDetailsAutoClose(detailsRef);

  // Auto-focus the search input when the dropdown opens, and clear it
  // when it closes — the Cmd-K-style "type immediately" pattern is the
  // fastest way for the user to home in on a specific filter without
  // scanning the whole list.
  useEffectApp(() => {
    const el = detailsRef.current;
    if (!el) return;
    const onToggle = () => {
      if (el.open) {
        // Defer focus so it lands after the panel finishes rendering.
        setTimeout(() => inputRef.current?.focus(), 0);
      } else {
        setSearch("");
      }
    };
    el.addEventListener("toggle", onToggle);
    return () => el.removeEventListener("toggle", onToggle);
  }, []);

  const indoorCount = facets.indoor_outdoor.find(f => f.value === "indoor")?.count;
  const outdoorCount = facets.indoor_outdoor.find(f => f.value === "outdoor")?.count;
  const groups = [
    {
      label: "Scene",
      chips: [
        indoorCount > 0 && { val: filterIndoorOutdoor === "indoor", label: "Indoor", count: indoorCount, hint: "indoor",
          onClick: () => setFilterIndoorOutdoor(filterIndoorOutdoor === "indoor" ? null : "indoor") },
        outdoorCount > 0 && { val: filterIndoorOutdoor === "outdoor", label: "Outdoor", count: outdoorCount, hint: "outdoor",
          onClick: () => setFilterIndoorOutdoor(filterIndoorOutdoor === "outdoor" ? null : "outdoor") },
        ...(facets.scenes || []).map(s => ({
          val: filterScene === s.value,
          label: s.value.replace(/_/g, " "),
          hint: s.value,
          count: s.count,
          onClick: () => setFilterScene(filterScene === s.value ? null : s.value),
        })),
      ].filter(Boolean),
    },
    {
      label: "Content",
      chips: [
        facets.with_faces > 0 && { val: filterHasFaces === true, label: "Has people", count: facets.with_faces, hint: "people faces",
          onClick: () => setFilterHasFaces(filterHasFaces === true ? null : true) },
        facets.with_gps > 0 && { val: filterHasGps === true, label: "Has location", count: facets.with_gps, hint: "location gps map",
          onClick: () => setFilterHasGps(filterHasGps === true ? null : true) },
        ...(facets.content_types || []).filter(c => c.value && c.value !== "photo").map(c => ({
          val: filterContentType === c.value,
          label: c.value,
          hint: c.value,
          count: c.count,
          onClick: () => setFilterContentType(filterContentType === c.value ? null : c.value),
        })),
      ].filter(Boolean),
    },
    // §C9 — People chips. Empty when face_recognition consent is off
    // (backend returns persons=[] in that case). When present, one chip
    // per named person; clicking sets filterPersonId and the gallery
    // re-queries the cross-folder list.
    {
      label: "People",
      chips: (facets.persons || []).map(p => ({
        val: filterPersonId === p.id,
        label: p.display_name,
        hint: `person ${p.display_name}`,
        count: p.count,
        onClick: () => setFilterPersonId?.(filterPersonId === p.id ? null : p.id),
      })),
    },
    // §C9 — Tags chips. Tag picker uses the color stored on the tag
    // row so the chip matches what the gallery card shows. Single-tag
    // selection for now (clicking a different tag swaps); multi-tag
    // AND/OR is a future extension once we've seen real usage.
    {
      label: "Tags",
      chips: (facets.tags || []).map(t => ({
        val: filterTag === t.label,
        label: t.label,
        hint: `tag ${t.label}`,
        count: t.count,
        tagColor: t.color,
        onClick: () => setFilterTag?.(filterTag === t.label ? null : t.label),
      })),
    },
  ];

  // Substring filter — searches both the visible label AND a hint
  // string so e.g. "gps" finds "Has location" even though the visible
  // label doesn't contain "gps". Matching is case-insensitive.
  const q = search.trim().toLowerCase();
  const filteredGroups = q
    ? groups
        .map(g => ({
          ...g,
          chips: g.chips.filter(c =>
            c.label.toLowerCase().includes(q) || (c.hint || "").toLowerCase().includes(q)
          ),
        }))
        .filter(g => g.chips.length > 0)
    : groups.filter(g => g.chips.length > 0);

  // Top section: just the chips that are currently active. Lets the
  // user see what they've selected without scrolling — and tap them
  // off without remembering which group they came from.
  const activeChips = groups.flatMap(g => g.chips.filter(c => c.val));
  const totalChipCount = groups.reduce((n, g) => n + g.chips.length, 0);
  const filteredCount = filteredGroups.reduce((n, g) => n + g.chips.length, 0);

  return (
    <details className="filters-dd" ref={detailsRef}>
      <summary className="btn btn--ghost btn--sm filters-dd__btn" data-active={anyFilterActive}>
        <Icon name="sort" size={12}/> Filters
        {activeCount > 0 && (
          <span className="filters-dd__badge">{activeCount}</span>
        )}
      </summary>
      <div className="filters-dd__panel">
        <div className="filters-dd__search">
          <Icon name="search" size={12}/>
          <input
            ref={inputRef}
            type="text"
            placeholder={`Search ${totalChipCount} filters…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              // Esc clears the input first, then closes the dropdown
              // on a second press — standard Apple-style Cmd-K UX.
              if (e.key === "Escape") {
                if (search) { setSearch(""); e.stopPropagation(); }
                else if (detailsRef.current) { detailsRef.current.open = false; }
              }
              // Enter applies the first matching filter — the user
              // can type "outdoor" + Enter to one-tap apply.
              if (e.key === "Enter") {
                const first = filteredGroups[0]?.chips[0];
                if (first) {
                  first.onClick();
                  setSearch("");
                  if (detailsRef.current) detailsRef.current.open = false;
                }
              }
            }}
          />
          {search && (
            <button
              type="button"
              className="filters-dd__search-clear"
              onClick={() => { setSearch(""); inputRef.current?.focus(); }}
              aria-label="Clear search"
            >
              <Icon name="x" size={10}/>
            </button>
          )}
        </div>

        {activeChips.length > 0 && !q && (
          <div className="filters-dd__group filters-dd__group--active">
            <div className="filters-dd__group-label">Active</div>
            <div className="filters-dd__chips">
              {activeChips.map(c => (
                <button
                  key={"active-" + c.label}
                  type="button"
                  onClick={c.onClick}
                  className="chip"
                  data-active="true"
                  title="Click to remove"
                >
                  {c.label}
                  <Icon name="x" size={10} style={{ marginLeft: 4 }}/>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* §C9 — Date range picker. Two <input type="date"> fields side
            by side. Either can be empty for an open-ended range. We
            hide it entirely when the substring search is active so
            the chip filter still feels keyboard-first; otherwise the
            picker stays visible above the chip groups. */}
        {!q && setFilterDateRange && (
          <div className="filters-dd__daterange">
            <div className="filters-dd__group-label">Date range</div>
            <div className="filters-dd__daterange-row">
              <input
                type="date"
                className="filters-dd__date"
                value={filterDateRange?.start || ""}
                min={facets.date_range?.earliest?.slice(0, 10) || undefined}
                max={facets.date_range?.latest?.slice(0, 10) || undefined}
                onChange={(e) => setFilterDateRange({
                  ...filterDateRange,
                  start: e.target.value || null,
                })}
                aria-label="From date"
              />
              <span style={{ color: "var(--ink-4)", fontSize: 12 }}>to</span>
              <input
                type="date"
                className="filters-dd__date"
                value={filterDateRange?.end || ""}
                min={facets.date_range?.earliest?.slice(0, 10) || undefined}
                max={facets.date_range?.latest?.slice(0, 10) || undefined}
                onChange={(e) => setFilterDateRange({
                  ...filterDateRange,
                  end: e.target.value || null,
                })}
                aria-label="To date"
              />
              {(filterDateRange?.start || filterDateRange?.end) && (
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => setFilterDateRange({ start: null, end: null })}
                  title="Clear date range"
                  aria-label="Clear date range"
                >
                  <Icon name="x" size={11}/>
                </button>
              )}
            </div>
          </div>
        )}

        {filteredCount === 0 ? (
          <div className="filters-dd__empty">
            No filters match "{search}".
          </div>
        ) : (
          // Each group becomes a native <details> so the dropdown stays
          // scannable as the catalog grows (50+ tags, dozens of scenes,
          // many persons). When the user types in the search box we
          // force every matching group open so Cmd-K-style filtering
          // still works without scrolling through collapsed sections.
          // The header shows "Group (n shown · k active)" so the user
          // can see what's selected without expanding everything.
          filteredGroups.map(g => {
            const activeInGroup = g.chips.filter(c => c.val).length;
            const forceOpen = !!q || activeInGroup > 0;
            return (
              <details
                key={g.label}
                className="filters-dd__group filters-dd__group--collapsible"
                open={forceOpen}
              >
                <summary className="filters-dd__group-summary">
                  <span className="filters-dd__group-label">{g.label}</span>
                  <span className="filters-dd__group-meta">
                    {g.chips.length}
                    {activeInGroup > 0 && (
                      <span className="filters-dd__group-active"> · {activeInGroup} active</span>
                    )}
                  </span>
                  <span className="filters-dd__group-chev" aria-hidden="true" />
                </summary>
                <div className="filters-dd__chips">
                  {g.chips.map(c => (
                    <button
                      key={c.label}
                      type="button"
                      onClick={c.onClick}
                      className="chip"
                      data-active={c.val}
                      style={c.tagColor ? { "--chip-tag-color": c.tagColor } : undefined}
                    >
                      {c.tagColor && (
                        <span
                          aria-hidden="true"
                          style={{
                            display: "inline-block",
                            width: 8,
                            height: 8,
                            borderRadius: 999,
                            background: c.tagColor,
                            marginRight: 6,
                          }}
                        />
                      )}
                      {c.label}
                      {c.count != null && (
                        <span style={{ marginLeft: 6, color: "var(--ink-4)" }}>{c.count}</span>
                      )}
                    </button>
                  ))}
                </div>
              </details>
            );
          })
        )}

        {anyFilterActive && (
          <div className="filters-dd__foot">
            <button type="button" onClick={clearAllFilters} className="btn btn--ghost btn--sm">
              <Icon name="x" size={11}/> Clear all
            </button>
          </div>
        )}
      </div>
    </details>
  );
}

// People view picker — grid of every person + unlabeled cluster, sized
// for the page (not a strip). Clicking a card drills into that person's
// photos via the parent's `peopleFilter` state. Unlabeled clusters get
// an inline rename affordance so the user can name people without
// having to open an image first.
function PeoplePicker({ people, onPick }) {
  const named = (people?.persons || []).map((p) => ({
    id: "p" + p.id,
    personId: p.id,
    clusterId: null,
    name: p.display_name,
    img: p.sample_face_id ? faceCropUrl(p.sample_face_id) : null,
    count: p.face_count,
  }));
  const unnamed = (people?.unlabeled_clusters || []).map((c) => ({
    id: "c" + c.cluster_id,
    personId: null,
    clusterId: c.cluster_id,
    name: null,
    img: faceCropUrl(c.sample_face_id),
    count: c.face_count,
  }));
  const everyone = [...named, ...unnamed];

  if (everyone.length === 0) {
    return (
      <div className="empty">
        <div className="empty__icon"><Icon name="users" size={26} strokeWidth={1.4}/></div>
        <div className="empty__title">No people yet</div>
        <div className="empty__body">Upload photos with faces (and grant face-recognition consent in Settings → Privacy). Detected faces will be grouped here.</div>
      </div>
    );
  }

  return (
    <div style={{ padding: "0 28px 28px" }}>
      <div className="kicker" style={{ marginBottom: 14 }}>
        {named.length} named · {unnamed.length} unnamed
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
        gap: 18,
      }}>
        {everyone.map((p) => (
          <div
            key={p.id}
            style={{
              display: "flex", flexDirection: "column",
              borderRadius: 14,
              background: "var(--surface)",
              border: "1px solid var(--line)",
              overflow: "hidden",
              cursor: p.personId ? "pointer" : "default",
              transition: "border-color 120ms ease, transform 120ms ease",
            }}
            onMouseEnter={(e) => { if (p.personId) e.currentTarget.style.borderColor = "var(--ink-3)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--line)"; }}
            onClick={() => {
              if (!p.personId) return; // unnamed clusters need a name first
              onPick?.({ personId: p.personId, name: p.name });
            }}
            title={p.personId ? `Show photos of ${p.name}` : "Name this person to filter their photos"}
          >
            {/* Square face tile — same approach as the gallery's
                small `.person__avatar` (cover + center), just sized
                up. Backend now picks the highest-confidence detection
                per person (people.py), which means the source crop is
                already tight on the face; with `cover + center` it
                fills the box and the eyes land near the middle. */}
            <div style={{
              width: "100%",
              aspectRatio: "1 / 1",
              background: "var(--surface-3)",
              position: "relative",
              borderBottom: "1px solid var(--line)",
              overflow: "hidden",
            }}>
              {p.img ? (
                <AuthedThumb
                  url={p.img}
                  style={{
                    position: "absolute",
                    inset: 0,
                    backgroundSize: "cover",
                    backgroundPosition: "center",
                  }}
                  placeholder={{ background: "var(--surface-3)" }}
                />
              ) : null}
            </div>
            <div style={{
              padding: "12px 14px",
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 8,
            }}>
              <EditableName
                name={p.name}
                personId={p.personId}
                clusterId={p.clusterId}
                className={"person__name" + (p.name ? "" : " person__name--unnamed")}
                unnamedPlaceholder="Name this person"
                invalidate={[["people"]]}
              />
              <span style={{ fontSize: 11.5, color: "var(--ink-3)", flexShrink: 0 }}>
                {p.count} {p.count === 1 ? "photo" : "photos"}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function App() {
  // theme
  const [theme, setThemeState] = useStateApp(() => {
    try {
      const saved = localStorage.getItem("neuthek.theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch (e) {}
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  useEffectApp(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("neuthek.theme", theme); } catch (e) {}
  }, [theme]);
  const setTheme = setThemeState;

  // Real auth — bootstrap on mount and pull the signed-in user from the
  // shared zustand store. Sign-out clears the JWT and bounces back to the
  // AuthScreen.
  const realUser = useAuthStore((s) => s.user);
  const authLoading = useAuthStore((s) => s.loading);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const realSignOut = useAuthStore((s) => s.signOut);
  useEffectApp(() => { bootstrap(); }, [bootstrap]);

  const signedIn = !!realUser;
  // Pass the *full* user record through to children — the previous
  // shape stripped away `google_linked`, `is_verified`, `totp_enabled`,
  // `role`, `age_confirmed`, which left the Settings rows reading
  // stale-undefined values. `name` is kept as the friendly display so
  // consumers that already read it don't have to fall back per-site.
  const user = realUser
    ? {
        ...realUser,
        name: realUser.display_name || realUser.email.split("@")[0],
      }
    : { name: "—", email: "" };
  const setUser = () => {/* no-op: source of truth is authStore */};
  // AuthScreen calls this once login/register succeed; no extra work needed
  // since useAuthStore already holds the user.
  const handleSignedIn = () => {};

  // Shared QueryClient — used to invalidate the consent-scopes cache after
  // the BIPA-grade FaceConsentModal signs (so the Privacy tab + sidebar
  // attention pill both refresh) and after sign-out clears identity.
  const qcApp = useQueryClient();
  const handleFaceSign = async (signature) => {
    try {
      await grantConsent({
        signature_text: signature,
        consent_collection: true,
        consent_retention: true,
      });
      qcApp.invalidateQueries({ queryKey: ["consent-scopes"] });
      qcApp.invalidateQueries({ queryKey: ["people"] });
      toast.success("Face recognition enabled.");
    } catch (e) {
      toast.error(e?.detail || "Could not record consent");
    }
  };
  // The Shared tab is reachable two ways:
  //   - Sidebar click (sets view="shared" directly)
  //   - /share/{token} redirect after claim → /?view=shared&share=<id>
  // Read the query string ONCE at mount so refreshing while on the
  // Shared tab keeps the user there. Once consumed, the `share` param
  // is stripped (see `consumeInitialShareId` below) so a follow-up
  // refresh doesn't keep popping the preview.
  const _initialViewParams = useMemoApp(() => {
    if (typeof window === "undefined") return { view: "gallery", shareId: null };
    const params = new URLSearchParams(window.location.search);
    const v = params.get("view");
    const sid = params.get("share");
    return {
      view: v === "shared" ? "shared" : "gallery",
      shareId: sid || null,
    };
  }, []);
  const [view, setView] = useStateApp(_initialViewParams.view);
  const [initialShareId, setInitialShareId] = useStateApp(_initialViewParams.shareId);
  // Strip the `share` query param once the Shared view consumes it.
  const consumeInitialShareId = useRefApp(null);
  consumeInitialShareId.current = () => {
    setInitialShareId(null);
    try {
      const u = new URL(window.location.href);
      if (u.searchParams.has("share")) {
        u.searchParams.delete("share");
        window.history.replaceState({}, "", u.toString());
      }
    } catch {}
  };
  const [empty, setEmpty] = useStateApp(false);
  const [query, setQuery] = useStateApp("");
  const [sort, setSort] = useStateApp("recent");
  // Richer filter axes — driven by the chip row below the type pills.
  // Each is independent and AND-composed on the server. `null` =
  // "don't filter on this axis." Reset together via the "Clear" chip.
  // §C9 — seed filter state from URL so reloading or sharing a link
  // keeps the chips applied. Same pattern as the `view` / `share`
  // bootstrapping above. Read once on mount; the effect below writes
  // back whenever state changes.
  const _initialFilters = useMemoApp(() => {
    if (typeof window === "undefined") {
      return { scene: null, contentType: null, indoorOutdoor: null,
        hasFaces: null, hasGps: null, personId: null, tag: null,
        dateRange: { start: null, end: null } };
    }
    const p = new URLSearchParams(window.location.search);
    const num = (s) => { const n = Number(s); return Number.isFinite(n) ? n : null; };
    return {
      scene: p.get("scene") || null,
      contentType: p.get("content_type") || null,
      indoorOutdoor: p.get("indoor") || null,
      hasFaces: p.get("has_faces") === "true" ? true : null,
      hasGps: p.get("has_gps") === "true" ? true : null,
      personId: p.get("person_id") ? num(p.get("person_id")) : null,
      tag: p.get("tag") || null,
      dateRange: {
        start: p.get("date_from") || null,
        end: p.get("date_to") || null,
      },
    };
  }, []);
  const [filterScene, setFilterScene] = useStateApp(_initialFilters.scene);
  const [filterContentType, setFilterContentType] = useStateApp(_initialFilters.contentType);
  const [filterIndoorOutdoor, setFilterIndoorOutdoor] = useStateApp(_initialFilters.indoorOutdoor);
  const [filterHasFaces, setFilterHasFaces] = useStateApp(_initialFilters.hasFaces);
  const [filterHasGps, setFilterHasGps] = useStateApp(_initialFilters.hasGps);
  // §C9 multi-axis additions. People/Tag/Date are AND-composed with
  // every chip above, gated by the same `useGlobalScope` so applying
  // one always pulls cross-folder results.
  const [filterPersonId, setFilterPersonId] = useStateApp(_initialFilters.personId);
  const [filterTag, setFilterTag] = useStateApp(_initialFilters.tag);
  // Free-form date range as { start: "YYYY-MM-DD"|null, end: "YYYY-MM-DD"|null }.
  // We keep it as an object in React-state (easier UI) and serialize to
  // "start,end" only when handing it to listFiles below.
  const [filterDateRange, setFilterDateRange] = useStateApp(_initialFilters.dateRange);
  const clearAllFilters = () => {
    setFilterScene(null);
    setFilterContentType(null);
    setFilterIndoorOutdoor(null);
    setFilterHasFaces(null);
    setFilterHasGps(null);
    setFilterPersonId(null);
    setFilterTag(null);
    setFilterDateRange({ start: null, end: null });
  };
  const anyFilterActive =
    filterScene != null || filterContentType != null ||
    filterIndoorOutdoor != null || filterHasFaces != null ||
    filterHasGps != null ||
    filterPersonId != null || filterTag != null ||
    filterDateRange.start != null || filterDateRange.end != null;
  // §C9 — write filter state back to the URL so reload + back/forward
  // + link-sharing all preserve the active chips. We never push() —
  // every change replaces the current history entry so the back
  // button still works as expected (it pops to the previous *page*,
  // not the previous filter combination).
  useEffectApp(() => {
    if (typeof window === "undefined") return;
    try {
      const u = new URL(window.location.href);
      const set = (k, v) => {
        if (v == null || v === "" || v === false) u.searchParams.delete(k);
        else u.searchParams.set(k, String(v));
      };
      set("scene", filterScene);
      set("content_type", filterContentType);
      set("indoor", filterIndoorOutdoor);
      set("has_faces", filterHasFaces === true ? "true" : null);
      set("has_gps", filterHasGps === true ? "true" : null);
      set("person_id", filterPersonId);
      set("tag", filterTag);
      set("date_from", filterDateRange.start);
      set("date_to", filterDateRange.end);
      const next = u.pathname + (u.search ? u.search : "") + u.hash;
      // Only replace when the URL actually changed — avoids hammering
      // the history stack on every render of an effect with stable deps.
      if (next !== window.location.pathname + window.location.search + window.location.hash) {
        window.history.replaceState({}, "", next);
      }
    } catch { /* defensive — URL API differences across browsers */ }
  }, [
    filterScene, filterContentType, filterIndoorOutdoor,
    filterHasFaces, filterHasGps,
    filterPersonId, filterTag,
    filterDateRange.start, filterDateRange.end,
  ]);
  // Toggleable per-tab sort direction. Recent defaults to desc (newest
  // first), Name and Size to asc — matches what users expect from
  // every other file browser. Stored as a single value for the
  // currently-active sort key.
  const [sortDir, setSortDir] = useStateApp("desc");
  // "grid" = thumbnail tiles; "list" = compact bar rows.
  const [layoutMode, setLayoutMode] = useStateApp("grid");
  // Multi-select: a Set of file ids the user has checked. Cleared when
  // the active view or folder changes so a stale selection doesn't
  // bleed into a different scope.
  const [multiSelected, setMultiSelected] = useStateApp(() => new Set());
  const toggleMultiSelected = (id) => {
    setMultiSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const clearMultiSelected = () => setMultiSelected(new Set());
  const [typeFilter, setTypeFilter] = useStateApp("all");
  const [selectedFile, setSelectedFile] = useStateApp(null);
  // Folder navigation. `null` = root, otherwise the folder UUID.
  // `folderPath` is the breadcrumb stack ([{id,name}, ...]).
  const [folderId, setFolderId] = useStateApp(null);
  const [folderPath, setFolderPath] = useStateApp([]);
  // People view drill-in: when a user clicks a person chip on the People
  // view, we filter the gallery to that person's photos. `null` = show
  // the person picker (no filter applied); set = show photos of person.
  const [peopleFilter, setPeopleFilter] = useStateApp(null);
  const enterFolder = (folder) => {
    setFolderId(folder.id);
    setFolderPath((p) => [...p, { id: folder.id, name: folder.name }]);
    setSelectedFile(null);
  };
  const navigateToCrumb = (idx) => {
    if (idx < 0) {
      setFolderId(null);
      setFolderPath([]);
    } else {
      const target = folderPath[idx];
      setFolderId(target.id);
      setFolderPath((p) => p.slice(0, idx + 1));
    }
    setSelectedFile(null);
  };

  // search history (clearable) — sourced from localStorage so the list
  // survives reloads and starts empty for new accounts.
  const [history, setHistoryState] = useStateApp(() => loadRecentSearches());
  const setHistory = (updater) => {
    setHistoryState((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      saveRecentSearches(next);
      return next;
    });
  };
  const [showHistory, setShowHistory] = useStateApp(false);
  // Held by the search input's onBlur so we can cancel the pending
  // hide-history setTimeout on unmount — otherwise it fires a setState
  // on an unmounted component when the user navigates away mid-blur.
  const searchBlurTimer = useRefApp(null);
  useEffectApp(() => () => {
    if (searchBlurTimer.current) clearTimeout(searchBlurTimer.current);
  }, []);

  // FAB scroll-to-top — the actual scroll container is .gallery, not .main
  const mainRef = useRefApp(null);
  const [fabShow, setFabShow] = useStateApp(false);
  useEffectApp(() => {
    if (!signedIn) return;
    let scrollEl = null;
    const findScroller = () => mainRef.current?.querySelector(".gallery") || mainRef.current;
    const onScroll = () => setFabShow((scrollEl?.scrollTop || 0) > 240);
    const tryAttach = () => {
      scrollEl = findScroller();
      if (scrollEl) scrollEl.addEventListener("scroll", onScroll);
    };
    const t = setTimeout(tryAttach, 60);
    return () => {
      clearTimeout(t);
      if (scrollEl) scrollEl.removeEventListener("scroll", onScroll);
    };
  }, [signedIn, view, query]);

  // modal stack
  const [showUpload, setShowUpload] = useStateApp(false);
  const [showAccount, setShowAccount] = useStateApp(false);
  // Which AccountModal tab to focus when it opens. Set by callers like the
  // sidebar attention pill ("Privacy") or future deep links.
  const [accountTab, setAccountTab] = useStateApp("profile");
  const openAccount = (tab = "profile") => {
    setAccountTab(tab);
    setShowAccount(true);
  };

  // §C2 — post-OAuth landing handling. The backend's
  // `/cloud/callback/{provider}` redirects back to
  // `/?cloud_connected=<provider>` on success or `/?cloud_error=<reason>`
  // on failure. We surface a toast + auto-open Account → Cloud sync
  // so the user can hit "Sync now" without hunting for it. Runs once
  // per sign-in (the cleanup strips the param so a refresh doesn't
  // re-fire it).
  useEffectApp(() => {
    if (!signedIn || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("cloud_connected");
    const failed = params.get("cloud_error");
    if (!connected && !failed) return;
    if (connected) {
      const label = connected === "google_drive"
        ? "Google Drive"
        : connected === "github" ? "GitHub" : connected;
      toast.success(`Connected to ${label}. Open Cloud sync to pull your files.`);
      setAccountTab("cloud");
      setShowAccount(true);
    } else if (failed) {
      const msg = failed === "not_configured"
        ? "Cloud sync isn't configured on this deployment yet. The operator needs to set the OAuth credentials in .env."
        : failed === "bad_state"
          ? "OAuth handshake failed (state signature mismatch). Try connecting again."
          : `Could not finish connecting (${failed}). Try again.`;
      toast.error(msg);
    }
    // Strip the params so a refresh doesn't replay the toast / modal.
    const url = new URL(window.location.href);
    url.searchParams.delete("cloud_connected");
    url.searchParams.delete("cloud_error");
    window.history.replaceState({}, "", url.pathname + (url.search || ""));
  }, [signedIn]);

  // Settings → Account → "Link Google" landing handler. The callback
  // redirects back to `/#sso_linked=1&email=...` (the same lightbox
  // pattern as cloud-sync), or `#sso_error=<reason>` on failure.
  // Runs once per sign-in, then strips the fragment so a refresh
  // doesn't replay the toast.
  useEffectApp(() => {
    if (!signedIn || typeof window === "undefined") return;
    const hash = window.location.hash || "";
    if (!hash.includes("sso_linked=") && !hash.includes("sso_error=")) return;
    const frag = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
    const linked = frag.get("sso_linked");
    const linkErr = frag.get("sso_error");
    // Clear immediately so a reload doesn't replay.
    try {
      window.history.replaceState({}, "", window.location.pathname + window.location.search);
    } catch {}
    if (linked === "1") {
      toast.success("Google account linked. Sign in with Google now lands here.");
      qcApp.invalidateQueries({ queryKey: ["me"] });
      // Re-bootstrap the auth store so the user object gains
      // `google_linked: true` without a hard refresh.
      try { useAuthStore.getState().bootstrap(); } catch {}
      setAccountTab("profile");
      setShowAccount(true);
    } else if (linkErr) {
      const msg = linkErr === "google_already_linked"
        ? "That Google account is already linked to a different neuthek user."
        : linkErr === "link_user_missing"
          ? "Could not find this user to link. Sign out and back in, then try again."
          : `Could not link Google (${linkErr}).`;
      toast.error(msg);
    }
  }, [signedIn]);

  const [showTerms, setShowTerms] = useStateApp(false);
  const [showPrivacy, setShowPrivacy] = useStateApp(false);
  const [showFace, setShowFace] = useStateApp(false);
  const [showDelete, setShowDelete] = useStateApp(false);
  const [showExport, setShowExport] = useStateApp(false);
  // Cookie banner: persist the user's choice. We only store the fact that
  // they made one (essential vs all); the actual scope decisions live in
  // the per-scope consent toggles. localStorage is fine for this since
  // it's a UX gate, not a legal record.
  // Session flag — the top summary-progress banner stays hidden for the
  // rest of this tab session once the user clicks dismiss. Resets on tab
  // reload (intentional — banner re-appears so a long backfill stays
  // visible after a refresh).
  const [summaryBannerDismissed, setSummaryBannerDismissed] = useStateApp(false);
  const [showCookie, setShowCookie] = useStateApp(() => {
    try { return localStorage.getItem("neuthek.cookie") == null; } catch { return true; }
  });
  const dismissCookie = (mode) => {
    try { localStorage.setItem("neuthek.cookie", mode); } catch {}
    setShowCookie(false);
  };
  const [showRename, setShowRename] = useStateApp(false);
  const [renameFile, setRenameFile] = useStateApp(null);
  const [showBestOf, setShowBestOf] = useStateApp(false);
  const [showAdmin, setShowAdmin] = useStateApp(false);
  const [showNewFolder, setShowNewFolder] = useStateApp(false);

  // Real file list from /images/. Search-mode (non-empty query) bypasses
  // the folder scope by passing "ALL"; otherwise we fetch the current folder
  // (null = root). We only fetch when signed-in to avoid firing a 401 on
  // the auth screen.
  const trimmedQuery = query.trim();
  // Debounced query — used as the cache key + enabled gate for the
  // /search request. Lags `query` by 280 ms so each keystroke doesn't
  // fire a network round-trip. The visible input still updates
  // immediately; only the network call waits. Pressing Enter
  // short-circuits via `submitSearch` which sets debouncedQuery
  // synchronously so the user gets results instantly when they want
  // them now instead of in a fifth of a second.
  const [debouncedQuery, setDebouncedQuery] = useStateApp("");
  useEffectApp(() => {
    const t = setTimeout(() => setDebouncedQuery(trimmedQuery), 280);
    return () => clearTimeout(t);
  }, [trimmedQuery]);
  // Top-level "type" views (Photos, Videos, Documents) ignore the
  // current folder and surface the user's whole library by media kind.
  // Without this, those tabs would silently filter to `folder_id IS
  // NULL` (the root) and miss everything inside Google Drive / synced
  // folders — which surprised users with "where did my files go?"
  const isTypeView = view === "photos" || view === "videos" || view === "docs";
  // Cross-folder scope when:
  //   - The user is searching (results need to span everything)
  //   - The user is in a type view (Photos / Videos / Documents are
  //     conceptually "all of this kind anywhere")
  //   - The user has any filter chip active (scene / content type /
  //     indoor-outdoor / has-people / has-location). Without this,
  //     clicking "Indoor" while sitting at root applied
  //     indoor_outdoor=indoor AND folder_id IS NULL, which returned
  //     near-zero on libraries where most content lives inside Google
  //     Drive subfolders. The facet COUNTS were already cross-folder,
  //     so the chips invited clicks that produced empty galleries.
  const useGlobalScope = trimmedQuery.length > 0 || isTypeView || anyFilterActive;
  const filesScope = useGlobalScope ? "ALL" : folderId;
  // Fold the filter chip state into the listFiles query so server-side
  // filtering does the heavy lifting (the gallery only paints what
  // matches). Cache key includes every axis so toggling a chip shows
  // a separate cached page rather than mutating the current one in place.
  const filesQueryFilters = useMemoApp(() => {
    // §C9 — serialize the date range object to the wire format the
    // backend expects ("start,end" with either side empty for open-
    // ended). Only emit when one side is set; empty,empty would just
    // produce no-op WHERE clauses but pollutes the cache key.
    const tb = filterDateRange.start || filterDateRange.end
      ? `${filterDateRange.start || ""},${filterDateRange.end || ""}`
      : null;
    return {
      folderId: filesScope,
      scene: filterScene,
      contentType: filterContentType,
      indoorOutdoor: filterIndoorOutdoor,
      hasFaces: filterHasFaces,
      hasGps: filterHasGps,
      personId: filterPersonId,
      tag: filterTag,
      takenBetween: tb,
    };
  }, [
    filesScope, filterScene, filterContentType, filterIndoorOutdoor,
    filterHasFaces, filterHasGps,
    filterPersonId, filterTag,
    filterDateRange.start, filterDateRange.end,
  ]);
  // Folder count for the current scope — used by the empty-state guard
  // below so a library that contains synced cloud folders (Google
  // Drive, etc.) doesn't show "library is empty, upload something" at
  // the root just because no un-foldered files exist. The folder
  // listing is already fetched inside GalleryView; this is a separate
  // shared cache-key query so the App can read it directly.
  const { data: rootFoldersForEmpty = [] } = useQuery({
    queryKey: ["folders", folderId ?? null, null],
    queryFn: () => listFolders(folderId ?? null, null),
    enabled: signedIn && view === "gallery",
    staleTime: 30_000,
  });
  const { data: rawFiles = [] } = useQuery({
    queryKey: ["files", filesQueryFilters],
    queryFn: () => listFiles(filesQueryFilters),
    enabled: signedIn,
    staleTime: 10_000,
  });

  // Facets — drives the chip choices below the type pills. Refetches
  // infrequently because the set of available scenes/content types
  // only changes when files are added/deleted.
  const { data: facets } = useQuery({
    queryKey: ["facets"],
    queryFn: getFacets,
    enabled: signedIn,
    staleTime: 60_000,
  });

  // Semantic search via /search?q=... — CLIP cosine + FTS hybrid scored
  // server-side. We only fire when the user actually types something so
  // an empty query doesn't waste a round-trip. The order from the
  // backend is the ranked order (highest score first); we keep it for
  // display so the most relevant hits stay on top. Local refinement
  // (sort / type-filter) still applies on top of these results.
  const { data: searchHits } = useQuery({
    queryKey: ["search", debouncedQuery],
    queryFn: () => searchSemantic(debouncedQuery, 60),
    enabled: signedIn && debouncedQuery.length > 0,
    staleTime: 30_000,
    keepPreviousData: true,
  });

  // Real GPS points for the Map view, keyed by image id so we can splice
  // them onto each neuthek-shaped row.
  //
  // refetchInterval kicks in when ANY geo row has coordinates but no
  // place name yet — the post-upload reverse-geocode worker runs as a
  // background task (~1-2s for Nominatim) and writes image_geo.place
  // after the upload's HTTP response has already flushed. Without
  // polling, the preview panel showed "Looking up location…" until
  // the user reloaded. We poll every 3s while there's at least one
  // pending row; once every row has a place, the interval auto-stops
  // (returns false) so we're not hitting the endpoint forever.
  const { data: geoResp } = useQuery({
    queryKey: ["geo"],
    queryFn: getImageGeo,
    enabled: signedIn,
    staleTime: 60_000,
    refetchInterval: (data) => {
      if (!data || !data.points) return false;
      const anyPending = data.points.some((p) => p.lat != null && !p.place);
      return anyPending ? 3000 : false;
    },
  });

  // Map view needs *every* file with GPS, not just files in the current
  // folder scope. Without this, opening a folder hides any pins for files
  // inside it (and vice versa). We fire this query unconditionally — once —
  // and let React Query cache it; cost is the same as one extra page load.
  const isMapView = view === "places";
  const { data: allFilesForMap = [] } = useQuery({
    queryKey: ["files", "ALL"],
    queryFn: () => listFiles({ folderId: "ALL" }),
    enabled: signedIn && isMapView,
    staleTime: 30_000,
  });

  // Starred view — cross-folder list of `is_starred` rows. Backend orders
  // by `starred_at DESC NULLS LAST` so newest stars come first.
  const isStarredView = view === "starred";
  const { data: starredRaw = [] } = useQuery({
    queryKey: ["files", "STARRED"],
    queryFn: () => listFiles({ starred: true }),
    enabled: signedIn && isStarredView,
    staleTime: 30_000,
  });

  // Trash view — cross-folder list of soft-deleted rows. The backend
  // sorts by deleted_at desc so the most recently trashed entry comes
  // first. Shares no cache key with the live file list, so a delete
  // from the gallery and a restore from the trash view each invalidate
  // their own slice.
  const isTrashView = view === "trash";
  const { data: trashedRaw = [] } = useQuery({
    queryKey: ["files", "TRASHED"],
    queryFn: () => listFiles({ trashed: true }),
    enabled: signedIn && isTrashView,
    staleTime: 30_000,
  });

  // People drill-in: when peopleFilter is set, fetch only that
  // person's photos via the backend's `?person_id=` param. Backend
  // joins images -> face_detections -> faces -> persons so this is
  // a single query, not a client-side filter over `baseFiles`.
  const isPeopleView = view === "people";
  const { data: personFilesRaw = [] } = useQuery({
    queryKey: ["files", "PERSON", peopleFilter?.personId ?? null],
    queryFn: () => listFiles({ personId: peopleFilter.personId, folderId: "ALL" }),
    enabled: signedIn && isPeopleView && !!peopleFilter?.personId,
    staleTime: 30_000,
  });

  // When a search is active, the ranked /search hits become the source
  // of truth for the gallery. The score ordering is preserved here —
  // the gallery's local sort runs only when no query is present (handled
  // inside GalleryView). Local fallback filtering still narrows further
  // by type pill etc.
  const sourceFiles = trimmedQuery.length > 0 && searchHits ? searchHits : rawFiles;
  const baseFiles = useMemoApp(() => {
    const geoMap = new Map((geoResp?.points || []).map((p) => [p.id, p]));
    return sourceFiles.map((f) => {
      const n = fileItemToNeuthek(f);
      const g = geoMap.get(f.id);
      if (g) n.gps = { lat: g.lat, lng: g.lng, place: g.place || null };
      return n;
    });
  }, [sourceFiles, geoResp]);

  // Real consent state — drives the sidebar "needs your attention" pill.
  // We count scopes still in NONE state (user hasn't decided yet); GRANTED
  // and WITHDRAWN both count as "decided" so the pill goes away once the
  // user has been through the toggles. Backend returns the same shape
  // used by AccountModal Privacy, so we share the cache key.
  const { data: consentData } = useQuery({
    queryKey: ["consent-scopes"],
    queryFn: getConsentScopes,
    enabled: signedIn,
    staleTime: 30_000,
  });
  const pendingConsents = useMemoApp(() => {
    const states = consentData?.states || {};
    // Only count scopes the user can actually toggle from the Privacy
    // panel. Without this, the backend's full SUPPORTED_SCOPES list
    // includes things the panel doesn't surface yet, and the pill
    // would stay at "needs your attention" forever.
    const toggleable = [
      "ai_summary",
      "semantic_search",
      "face_recognition",
      "gps_retention",
      "bandit_compression_telemetry",
      "exif_retention",
    ];
    return toggleable.filter((k) => states[k] === "NONE").length;
  }, [consentData]);

  // Cross-folder file list, used only by the Map view. Splices the same
  // geoResp coords onto whichever files have GPS rows.
  const allFilesMapped = useMemoApp(() => {
    if (!isMapView) return [];
    const geoMap = new Map((geoResp?.points || []).map((p) => [p.id, p]));
    return allFilesForMap.map((f) => {
      const n = fileItemToNeuthek(f);
      const g = geoMap.get(f.id);
      if (g) n.gps = { lat: g.lat, lng: g.lng, place: g.place || null };
      return n;
    });
  }, [allFilesForMap, geoResp, isMapView]);

  // Starred files mapped to neuthek shape (cross-folder).
  const starredFiles = useMemoApp(() => {
    if (!isStarredView) return [];
    const geoMap = new Map((geoResp?.points || []).map((p) => [p.id, p]));
    return starredRaw.map((f) => {
      const n = fileItemToNeuthek(f);
      const g = geoMap.get(f.id);
      if (g) n.gps = { lat: g.lat, lng: g.lng, place: g.place || null };
      return n;
    });
  }, [starredRaw, geoResp, isStarredView]);

  // Trashed files (cross-folder) mapped to neuthek shape.
  const trashedFiles = useMemoApp(() => {
    if (!isTrashView) return [];
    return trashedRaw.map((f) => fileItemToNeuthek(f));
  }, [trashedRaw, isTrashView]);

  // Person-filtered files mapped to neuthek shape (cross-folder).
  const personFiles = useMemoApp(() => {
    if (!isPeopleView || !peopleFilter?.personId) return [];
    const geoMap = new Map((geoResp?.points || []).map((p) => [p.id, p]));
    return personFilesRaw.map((f) => {
      const n = fileItemToNeuthek(f);
      const g = geoMap.get(f.id);
      if (g) n.gps = { lat: g.lat, lng: g.lng, place: g.place || null };
      return n;
    });
  }, [personFilesRaw, geoResp, isPeopleView, peopleFilter]);

  // Real people total for the sidebar — named persons + unlabeled
  // clusters. The same query already drives the People strip on the
  // gallery, so React Query dedupes it.
  const { data: peopleResp } = useQuery({
    queryKey: ["people"],
    queryFn: listPeople,
    enabled: signedIn,
    staleTime: 60_000,
  });
  // Trash count for the sidebar — populated from the same endpoint the
  // Account → Trash page uses. We share the cache key with that page so
  // emptying the trash there immediately updates the badge here.
  const { data: trashSummary } = useQuery({
    queryKey: ["account-trash"],
    queryFn: getAccountTrash,
    enabled: signedIn,
    staleTime: 60_000,
  });

  // Incoming shares — files other users have shared with me (todo §1.1 /
  // G1). Drives the "Shared" sidebar badge so a recipient sees a count
  // light up without having to navigate. Same cache key the future
  // Shared view will read, so a single fetch covers both.
  const { data: incomingShares = [] } = useQuery({
    queryKey: ["incoming-shares"],
    queryFn: listIncomingShares,
    enabled: signedIn,
    staleTime: 30_000,
  });

  // Sidebar counts — derived from the real file list. The "geo" count is
  // sourced from the global geo response (not the current folder scope) so
  // the Map count in the sidebar matches what the map will actually show.
  // `starred` is library-wide once we've fetched the starred view; before
  // that we fall back to baseFiles for an approximation. Shared comes
  // from /shares/incoming (todo §1.1 / G1). Trash is 0 until the
  // trash-view endpoint surfaces a count.
  const sideCounts = useMemoApp(() => {
    // Sidebar pills are *library-wide* counts, not folder-scoped. Pull
    // them from /images/facets (which we already fetch) rather than
    // deriving from `baseFiles` (which IS folder-scoped) — otherwise
    // Photos / Videos / Documents undercount whenever the user is
    // inside a folder, and Trash reads 0 outside the trash view.
    const byCat = facets?.by_category || {};
    const c = {
      all: facets?.total ?? baseFiles.length,
      image: byCat.image ?? 0,
      video: byCat.video ?? 0,
      document: byCat.document ?? 0,
      geo: 0,
      starred: 0,
      people: 0,
      shared: 0,
      trash: facets?.trash_count ?? 0,
    };
    c.geo = (geoResp?.points || []).length;
    c.starred = isStarredView
      ? starredRaw.length
      : baseFiles.filter((f) => f.is_starred).length;
    c.people =
      (peopleResp?.persons || []).length
      + (peopleResp?.unlabeled_clusters || []).length;
    c.shared = incomingShares.length;
    return c;
  }, [facets, baseFiles, geoResp, isStarredView, starredRaw, peopleResp, incomingShares]);

  const filesByView = useMemoApp(() => ({
    gallery: baseFiles,
    photos: baseFiles.filter(f => f.type === "image"),
    videos: baseFiles.filter(f => f.type === "video"),
    docs: baseFiles.filter(f => f.type === "doc"),
    starred: starredFiles,
    // People view: when no person is selected, the "files" array is
    // empty (we render the person picker instead). When a person is
    // selected we render the cross-folder photos returned by the
    // person-filtered fetch.
    people: peopleFilter?.personId ? personFiles : [],
    places: baseFiles.filter(f => f.gps),
    shared: [],
    trash: trashedFiles,
  }), [baseFiles, starredFiles, trashedFiles, personFiles, peopleFilter]);

  const files = filesByView[view] || [];

  const openSub = (key) => {
    if (key === "face") setShowFace(true);
    if (key === "privacy") setShowPrivacy(true);
    if (key === "terms") setShowTerms(true);
    if (key === "delete") setShowDelete(true);
    if (key === "export") setShowExport(true);
  };

  const handleRename = (f) => { setRenameFile(f); setShowRename(true); };
  const saveRename = async (newName) => {
    if (!renameFile) return;
    try {
      await renameImage(renameFile.id, newName);
      qcApp.invalidateQueries({ queryKey: ["files"] });
      toast.success("Renamed");
    } catch (e) {
      // Bubble the server's validator message ("CON is a reserved system
      // name on Windows.", "Filename can't contain /.", etc.) so the user
      // knows why the rename failed instead of a generic toast.
      toast.error(e?.detail || "Could not rename");
      throw e;
    }
  };

  const submitSearch = (q) => {
    const v = (q ?? query).trim();
    if (!v) return;
    // Fire the search immediately on Enter — bypass the 280ms debounce
    // so the user gets results without waiting once they've committed
    // to the query.
    setDebouncedQuery(v);
    setHistory(h => [v, ...h.filter(x => x.toLowerCase() !== v.toLowerCase())].slice(0, 8));
    setShowHistory(false);
  };

  if (authLoading) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh",
                    color: "var(--ink-3)", fontFamily: "Geist, system-ui" }}>
        Loading…
      </div>
    );
  }
  if (!signedIn) {
    return (
      <AuthScreen
        onSignedIn={handleSignedIn}
        tweaks={t}
        theme={theme}
        setTheme={setTheme}
      />
    );
  }

  // People-picker mode: when the user is on the People view and hasn't
  // drilled into a specific person yet, we render the full-page picker
  // instead of the gallery (so the EmptyGallery hint doesn't appear).
  const isPeoplePicker = view === "people" && !peopleFilter?.personId;
  // Trash view should render the gallery whenever there ARE trashed
  // files; only fall through to the "trash is empty" message when the
  // list is genuinely empty.
  const trashIsEmpty = view === "trash" && trashedFiles.length === 0;
  // Don't fall through to the upload-CTA when the user has *folders*
  // visible (synced Google Drive folder, etc.) — render the gallery so
  // those folders are clickable. Previously this fired EmptyGallery
  // any time `files.length === 0`, hiding every synced subtree.
  const hasFoldersInScope = view === "gallery" && (rootFoldersForEmpty?.length || 0) > 0;
  const showEmpty = empty || trashIsEmpty || (
    files.length === 0 && !query && view !== "people" && !hasFoldersInScope
  );
  const densityGap = t.density === "compact" ? 10 : t.density === "comfy" ? 22 : 16;
  const isMap = view === "places";

  return (
    <div className="app" data-preview={selectedFile ? "open" : "closed"}
         style={{ "--dc-grid-gap": densityGap + "px" }}>
      <style>{`.gallery__grid { gap: ${densityGap}px; }`}</style>
      <Sidebar
        view={view}
        onView={(v) => { setView(v); setQuery(""); setSelectedFile(null); setPeopleFilter(null); }}
        onUpload={() => setShowUpload(true)}
        onAccount={() => openAccount("profile")}
        // Pill is shown only when there are undecided consent scopes.
        // Clicking it deep-links into Settings → Privacy.
        attentionCount={pendingConsents}
        onAttention={() => openAccount("privacy")}
        user={user}
        counts={sideCounts}
      />

      <main className="main" ref={mainRef}>
        <SummarizingBanner
          signedIn={signedIn}
          dismissed={summaryBannerDismissed}
          onDismiss={() => setSummaryBannerDismissed(true)}
        />
        <div className="topbar">
          <div className="topbar__title">
            <h1>{VIEW_LABELS[view]}</h1>
            <span className="topbar__title-meta">
              {query
                ? `${files.length} results`
                : `${files.length} items`}
            </span>
          </div>
          <div className="topbar__spacer"/>
          <div className="search" style={{ position: "relative" }}>
            <Icon name="search" size={14} style={{ color: "var(--ink-3)" }}/>
            <input
              placeholder='Search across all your files — "sunset", "lease", "Stardew"…'
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => setShowHistory(true)}
              onBlur={() => { searchBlurTimer.current = setTimeout(() => setShowHistory(false), 160); }}
              onKeyDown={(e) => { if (e.key === "Enter") submitSearch(); if (e.key === "Escape") { setQuery(""); setShowHistory(false); } }}
            />
            {query
              ? <button
                  className="btn-icon"
                  style={{ width: 24, height: 24 }}
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                  title="Clear search"
                >
                  <Icon name="x" size={12}/>
                </button>
              : <span className="search__hint">⌘ K</span>}

            {/* History dropdown is only useful BEFORE the user starts
                typing. Once `query` is non-empty, the gallery below
                shows the live search results — keeping the dropdown
                open at that point covered the results and forced a
                blur-then-refocus to actually see them. Now: dropdown
                shows on focus with empty query (history + tip) and
                hides as soon as the user types. */}
            {showHistory && !query && history.length > 0 && (
              <div className="search-history" onMouseDown={(e) => e.preventDefault()}>
                <div className="search-history__semantic">
                  <Icon name="sparkles" size={11}/>
                  Semantic search reads file names, AI summaries, and contents — across every folder.
                </div>
                {history.length > 0 && (
                  <>
                    <div className="search-history__head">
                      <span className="search-history__title">Recent searches</span>
                    </div>
                    {history.map((h, i) => (
                      <button key={i} className="search-history__item"
                              onClick={() => { setQuery(h); submitSearch(h); }}>
                        <Icon name="history" size={13} style={{ color: "var(--ink-3)" }}/>
                        <span>{h}</span>
                        <span className="search-history__item-x"
                              onClick={(e) => { e.stopPropagation(); setHistory(hist => hist.filter((_, j) => j !== i)); }}>
                          <Icon name="x" size={11}/>
                        </span>
                      </button>
                    ))}
                    {/* §C1.4 — Clear history affordance at the dropdown
                        bottom. Hits `DELETE /search/history` for the
                        audit trail, then wipes localStorage. The button
                        used to live in the header but the spec calls
                        for the bottom position. */}
                    <button
                      className="search-history__clear search-history__clear--bottom"
                      onClick={async () => {
                        try { await clearSearchHistory(); } catch { /* offline ok */ }
                        setHistory([]);
                        setShowHistory(false);
                      }}
                      style={{
                        width: "100%",
                        marginTop: 4,
                        padding: "8px 10px",
                        textAlign: "left",
                        fontSize: 12,
                        color: "var(--ink-3)",
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        borderRadius: 6,
                      }}
                    >
                      <Icon name="trash" size={12}/> Clear history
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
          <button className="btn-icon" onClick={() => setTheme(theme === "light" ? "dark" : "light")}
                  aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
                  title={`Switch to ${theme === "light" ? "dark" : "light"} mode`}>
            <Icon name={theme === "light" ? "moon" : "sun"} size={14}/>
          </button>
          <button className="btn btn--secondary" onClick={() => setShowUpload(true)}>
            <Icon name="upload" size={14}/> Upload
          </button>
          {user.is_superuser && (
            <button
              className="btn-icon"
              onClick={() => setShowAdmin(true)}
              title="Admin console"
              aria-label="Admin console"
            >
              <Icon name="shield" size={14}/>
            </button>
          )}
          {t.showLogout && (
            <button className="logout-btn" onClick={realSignOut} title="Sign out">
              <Icon name="log_out" size={14}/> Sign out
            </button>
          )}
        </div>

        {!isMap && (
          <div className="subbar">
            {/* Sort dropdown — single button instead of three side-by-side
                tabs. Closed it shows just the active key + direction
                arrow ("Recent ↓"); open it reveals all three options
                with the asc/desc toggle inline so a re-click flips
                direction without leaving the dropdown. */}
            <SortDropdown sort={sort} sortDir={sortDir} setSort={setSort} setSortDir={setSortDir}/>
            <div className="topbar__spacer"/>
            {/* Bulk-action toolbar — replaces the bare "N selected · Clear"
                so multi-select actually has reach. Move / new-folder /
                pick-best-of / delete all operate on the selection set;
                the bar collapses entirely when nothing's selected. */}
            {multiSelected.size > 0 && (
              <BulkActionBar
                selectedIds={Array.from(multiSelected)}
                selectedAreAllImages={Array.from(multiSelected).every(id => {
                  const f = baseFiles.find(b => b.id === id);
                  return f && f.type === "image";
                })}
                onClear={clearMultiSelected}
                onPickBestOf={() => setShowBestOf(true)}
                inTrashView={view === "trash"}
              />
            )}
            {/* Filters — single dropdown button collapsing the scene /
                place / face / location chips into one compact control.
                Previously these all wrapped across the topbar as a third
                row that ate vertical real estate. The badge shows the
                active count so it stays glanceable at a distance. */}
            {!isMap && view !== "trash" && facets && (
              <FiltersDropdown
                facets={facets}
                filterScene={filterScene} setFilterScene={setFilterScene}
                filterContentType={filterContentType} setFilterContentType={setFilterContentType}
                filterIndoorOutdoor={filterIndoorOutdoor} setFilterIndoorOutdoor={setFilterIndoorOutdoor}
                filterHasFaces={filterHasFaces} setFilterHasFaces={setFilterHasFaces}
                filterHasGps={filterHasGps} setFilterHasGps={setFilterHasGps}
                filterPersonId={filterPersonId} setFilterPersonId={setFilterPersonId}
                filterTag={filterTag} setFilterTag={setFilterTag}
                filterDateRange={filterDateRange} setFilterDateRange={setFilterDateRange}
                anyFilterActive={anyFilterActive}
                clearAllFilters={clearAllFilters}
                activeCount={[
                  filterScene, filterContentType, filterIndoorOutdoor,
                  filterHasFaces === true ? true : null,
                  filterHasGps === true ? true : null,
                  filterPersonId, filterTag,
                  filterDateRange.start || filterDateRange.end ? true : null,
                ].filter(v => v != null).length}
              />
            )}
            <button className="btn btn--ghost btn--sm" onClick={() => setShowNewFolder(true)}>
              <Icon name="folderPlus" size={12}/> New folder
            </button>
            {/* "Pick best of burst" used to live here too but it opened the
                modal with no selection — which dropped users on the
                upload/sample mock instead of scoring their actual photos.
                The action now only appears inside BulkActionBar (visible
                only when 2+ photos are selected), which is the only place
                the flow makes sense. */}
            <button
              className="btn-icon"
              aria-label={layoutMode === "grid" ? "Switch to list view" : "Switch to grid view"}
              aria-pressed={layoutMode === "list"}
              onClick={() => setLayoutMode(m => m === "grid" ? "list" : "grid")}
              title={layoutMode === "grid" ? "Switch to list view" : "Switch to grid view"}
            >
              <Icon name={layoutMode === "grid" ? "list" : "grid"} size={15}/>
            </button>
          </div>
        )}

        {/* Breadcrumb — only shown when we're inside a folder. Each crumb
            is clickable; "Home" jumps back to root. */}
        {!isMap && (folderPath.length > 0) && (
          <div style={{
            padding: "8px 28px 4px",
            display: "flex", alignItems: "center", gap: 6,
            fontSize: 12.5, color: "var(--ink-3)",
          }}>
            <button onClick={() => navigateToCrumb(folderPath.length - 2)}
                    title="Back one level"
                    aria-label="Back one level"
                    style={{ background: "none", border: 0, padding: "2px 4px",
                             color: "var(--ink-2)", cursor: "pointer",
                             display: "inline-flex", alignItems: "center" }}>
              <Icon name="chevronLeft" size={12}/>
            </button>
            <button onClick={() => navigateToCrumb(-1)}
                    style={{ background: "none", border: 0, padding: 0, color: "var(--ink-2)", cursor: "pointer" }}>
              <Icon name="home" size={12}/> Home
            </button>
            {folderPath.map((c, i) => (
              <React.Fragment key={c.id}>
                <Icon name="chevronRight" size={12} style={{ color: "var(--ink-3)" }}/>
                <button onClick={() => navigateToCrumb(i)}
                        style={{ background: "none", border: 0, padding: 0, color: i === folderPath.length - 1 ? "var(--ink)" : "var(--ink-2)", fontWeight: i === folderPath.length - 1 ? 600 : 400, cursor: "pointer" }}>
                  {c.name}
                </button>
              </React.Fragment>
            ))}
          </div>
        )}

        {view === "shared"
          ? <SharedGalleryView
              initialShareId={initialShareId}
              onClearShareParam={() => consumeInitialShareId.current && consumeInitialShareId.current()}
            />
          : isMap
          ? <React.Suspense fallback={<div style={{ padding: 40, color: "var(--ink-3)" }}>Loading map…</div>}>
              <MapView items={allFilesMapped} onPick={(f) => setSelectedFile(f)}/>
            </React.Suspense>
          : isPeoplePicker
            ? <PeoplePicker
                people={peopleResp}
                onPick={(p) => setPeopleFilter(p)}
              />
            : showEmpty
              ? (view === "trash"
                  ? <div className="empty">
                      <div className="empty__icon"><Icon name="trash" size={28} strokeWidth={1.4}/></div>
                      <div className="empty__title">Your trash is empty</div>
                      <div className="empty__body">Files you delete appear here for 30 days before they're removed for good. Nothing to clean up right now.</div>
                    </div>
                  : <EmptyGallery onUpload={() => setShowUpload(true)}/>)
              : <GalleryView
                  files={files}
                  query={query}
                  sort={sort}
                  sortDir={sortDir}
                  view={view}
                  layoutMode={layoutMode}
                  selected={selectedFile?.id}
                  multiSelected={multiSelected}
                  onMultiSelectToggle={toggleMultiSelected}
                  // People drill-in already filters server-side; don't
                  // render the redundant strip below the topbar.
                  showPeopleStrip={t.showPeopleStrip && view !== "people"}
                  showFolders={t.showFolders}
                  typeFilter={typeFilter}
                  onTypeFilter={setTypeFilter}
                  onSelect={(f) => setSelectedFile(prev => prev?.id === f.id ? null : f)}
                  onRename={handleRename}
                  folderId={folderId}
                  onEnterFolder={enterFolder}
                  peopleFilter={peopleFilter}
                  onClearPeopleFilter={() => setPeopleFilter(null)}
                />}
      </main>

      <PreviewPanel file={selectedFile} onClose={() => setSelectedFile(null)} onRename={handleRename} user={user}/>

      {/* FAB jump-to-top */}
      {t.showFab && (
        <button className="fab-top" data-show={fabShow}
                onClick={() => mainRef.current?.querySelector(".gallery")?.scrollTo({ top: 0, behavior: "smooth" })}
                aria-label="Jump to top">
          <Icon name="arrowUp" size={18}/>
        </button>
      )}

      {/* modals */}
      <UploadModal open={showUpload} onClose={() => setShowUpload(false)}/>
      <AccountModal
        open={showAccount}
        onClose={() => setShowAccount(false)}
        onOpenSubmodal={openSub}
        user={user}
        onUserChange={setUser}
        initialTab={accountTab}
        onSignOut={realSignOut}
      />
      <TermsModal open={showTerms} onClose={() => setShowTerms(false)} mode="view"/>
      <PrivacyModal open={showPrivacy} onClose={() => setShowPrivacy(false)}/>
      <FaceConsentModal open={showFace} onClose={() => setShowFace(false)} onSign={handleFaceSign}/>
      <DeleteAccountModal open={showDelete} onClose={() => setShowDelete(false)} email={user.email}/>
      <ExportModal open={showExport} onClose={() => setShowExport(false)}/>
      <RenameModal open={showRename} file={renameFile} onClose={() => setShowRename(false)} onSave={saveRename}/>
      <BestOfModal
        open={showBestOf}
        onClose={() => setShowBestOf(false)}
        // When the user opens this from the multi-select bar, hand
        // the modal the actual selection so it can call the real
        // /images/best-of endpoint instead of the upload/sample demo.
        imageIds={Array.from(multiSelected)}
        // After a successful keep, the losers are already in Trash —
        // clear the gallery's multi-select so the bar reflects reality.
        onAfterKeep={() => clearMultiSelected()}
      />
      <React.Suspense fallback={null}>
        {showAdmin && <AdminOverlay open={showAdmin} onClose={() => setShowAdmin(false)}/>}
      </React.Suspense>
      <NewFolderModal
        open={showNewFolder}
        onClose={() => setShowNewFolder(false)}
        parentFolderId={folderId}
      />

      <CookieBanner
        open={showCookie && t.showCookieBanner}
        onAcceptAll={() => dismissCookie("all")}
        onEssentialOnly={() => dismissCookie("essential")}
        onCustomize={() => { dismissCookie("customize"); setShowPrivacy(true); }}
      />
    </div>
  );
}
