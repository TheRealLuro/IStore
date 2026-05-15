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
import { useAuthStore } from "@/stores/authStore";
import { listFiles, getImageGeo, servedUrl, renameImage, getSummarizeProgress, searchSemantic, getFacets } from "@/api/files";
import { listPeople, faceCropUrl } from "@/api/people";
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
function fileItemToNeuthek(f) {
  const cat = f.category;
  const type = cat === "document" ? "doc" : cat === "image" ? "image" : cat === "video" ? "video" : "doc";
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
    thumb: hasServedThumb ? servedUrl(f.id) : null,
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
  anyFilterActive,
  clearAllFilters,
  activeCount,
}) {
  const indoorCount = facets.indoor_outdoor.find(f => f.value === "indoor")?.count;
  const outdoorCount = facets.indoor_outdoor.find(f => f.value === "outdoor")?.count;
  const groups = [
    {
      label: "Scene",
      chips: [
        indoorCount > 0 && { val: filterIndoorOutdoor === "indoor", label: "Indoor", count: indoorCount,
          onClick: () => setFilterIndoorOutdoor(filterIndoorOutdoor === "indoor" ? null : "indoor") },
        outdoorCount > 0 && { val: filterIndoorOutdoor === "outdoor", label: "Outdoor", count: outdoorCount,
          onClick: () => setFilterIndoorOutdoor(filterIndoorOutdoor === "outdoor" ? null : "outdoor") },
        ...(facets.scenes || []).slice(0, 12).map(s => ({
          val: filterScene === s.value,
          label: s.value.replace(/_/g, " "),
          count: s.count,
          onClick: () => setFilterScene(filterScene === s.value ? null : s.value),
        })),
      ].filter(Boolean),
    },
    {
      label: "Content",
      chips: [
        facets.with_faces > 0 && { val: filterHasFaces === true, label: "Has people", count: facets.with_faces,
          onClick: () => setFilterHasFaces(filterHasFaces === true ? null : true) },
        facets.with_gps > 0 && { val: filterHasGps === true, label: "Has location", count: facets.with_gps,
          onClick: () => setFilterHasGps(filterHasGps === true ? null : true) },
        ...(facets.content_types || []).filter(c => c.value && c.value !== "photo").slice(0, 8).map(c => ({
          val: filterContentType === c.value,
          label: c.value,
          count: c.count,
          onClick: () => setFilterContentType(filterContentType === c.value ? null : c.value),
        })),
      ].filter(Boolean),
    },
  ].filter(g => g.chips.length > 0);

  return (
    <details className="filters-dd">
      <summary className="btn btn--ghost btn--sm filters-dd__btn" data-active={anyFilterActive}>
        <Icon name="sort" size={12}/> Filters
        {activeCount > 0 && (
          <span className="filters-dd__badge">{activeCount}</span>
        )}
      </summary>
      <div className="filters-dd__panel">
        {groups.map(g => (
          <div key={g.label} className="filters-dd__group">
            <div className="filters-dd__group-label">{g.label}</div>
            <div className="filters-dd__chips">
              {g.chips.map(c => (
                <button
                  key={c.label}
                  type="button"
                  onClick={c.onClick}
                  className="chip"
                  data-active={c.val}
                >
                  {c.label}
                  {c.count != null && (
                    <span style={{ marginLeft: 6, color: "var(--ink-4)" }}>{c.count}</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        ))}
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
  const user = realUser
    ? {
        name: realUser.display_name || realUser.email.split("@")[0],
        email: realUser.email,
        is_superuser: realUser.is_superuser,
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
  const [view, setView] = useStateApp("gallery");
  const [empty, setEmpty] = useStateApp(false);
  const [query, setQuery] = useStateApp("");
  const [sort, setSort] = useStateApp("recent");
  // Richer filter axes — driven by the chip row below the type pills.
  // Each is independent and AND-composed on the server. `null` =
  // "don't filter on this axis." Reset together via the "Clear" chip.
  const [filterScene, setFilterScene] = useStateApp(null);
  const [filterContentType, setFilterContentType] = useStateApp(null);
  const [filterIndoorOutdoor, setFilterIndoorOutdoor] = useStateApp(null);
  const [filterHasFaces, setFilterHasFaces] = useStateApp(null);
  const [filterHasGps, setFilterHasGps] = useStateApp(null);
  const clearAllFilters = () => {
    setFilterScene(null);
    setFilterContentType(null);
    setFilterIndoorOutdoor(null);
    setFilterHasFaces(null);
    setFilterHasGps(null);
  };
  const anyFilterActive =
    filterScene != null || filterContentType != null ||
    filterIndoorOutdoor != null || filterHasFaces != null ||
    filterHasGps != null;
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
  const useGlobalScope = trimmedQuery.length > 0;
  const filesScope = useGlobalScope ? "ALL" : folderId;
  // Fold the filter chip state into the listFiles query so server-side
  // filtering does the heavy lifting (the gallery only paints what
  // matches). Cache key includes every axis so toggling a chip shows
  // a separate cached page rather than mutating the current one in place.
  const filesQueryFilters = useMemoApp(() => ({
    folderId: filesScope,
    scene: filterScene,
    contentType: filterContentType,
    indoorOutdoor: filterIndoorOutdoor,
    hasFaces: filterHasFaces,
    hasGps: filterHasGps,
  }), [filesScope, filterScene, filterContentType, filterIndoorOutdoor, filterHasFaces, filterHasGps]);
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
    queryKey: ["search", trimmedQuery],
    queryFn: () => searchSemantic(trimmedQuery, 60),
    enabled: signedIn && trimmedQuery.length > 0,
    staleTime: 30_000,
    keepPreviousData: true,
  });

  // Real GPS points for the Map view, keyed by image id so we can splice
  // them onto each neuthek-shaped row.
  const { data: geoResp } = useQuery({
    queryKey: ["geo"],
    queryFn: getImageGeo,
    enabled: signedIn,
    staleTime: 60_000,
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
    return Object.values(states).filter((s) => s === "NONE").length;
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

  // Sidebar counts — derived from the real file list. The "geo" count is
  // sourced from the global geo response (not the current folder scope) so
  // the Map count in the sidebar matches what the map will actually show.
  // `starred` is library-wide once we've fetched the starred view; before
  // that we fall back to baseFiles for an approximation. Shared has no
  // backend yet (todo.md G1) so it stays at 0 instead of a mock value.
  // Trash is similarly 0 until the trash-view endpoint surfaces a count.
  const sideCounts = useMemoApp(() => {
    const c = { all: baseFiles.length, image: 0, video: 0, document: 0, geo: 0, starred: 0, people: 0, shared: 0, trash: 0 };
    for (const f of baseFiles) {
      if (f.category === "image") c.image += 1;
      else if (f.category === "video") c.video += 1;
      else if (f.category === "document") c.document += 1;
    }
    c.geo = (geoResp?.points || []).length;
    c.starred = isStarredView
      ? starredRaw.length
      : baseFiles.filter((f) => f.is_starred).length;
    c.people =
      (peopleResp?.persons || []).length
      + (peopleResp?.unlabeled_clusters || []).length;
    c.trash = trashSummary?.count ?? 0;
    return c;
  }, [baseFiles, geoResp, isStarredView, starredRaw, peopleResp, trashSummary]);

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
  const showEmpty = empty || trashIsEmpty || (files.length === 0 && !query && view !== "people");
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
              onBlur={() => setTimeout(() => setShowHistory(false), 160)}
              onKeyDown={(e) => { if (e.key === "Enter") submitSearch(); if (e.key === "Escape") { setQuery(""); setShowHistory(false); } }}
            />
            {query
              ? <button className="btn-icon" style={{ width: 24, height: 24 }} onClick={() => setQuery("")}><Icon name="x" size={12}/></button>
              : <span className="search__hint">⌘ K</span>}

            {showHistory && (history.length > 0 || query) && (
              <div className="search-history" onMouseDown={(e) => e.preventDefault()}>
                <div className="search-history__semantic">
                  <Icon name="sparkles" size={11}/>
                  Semantic search reads file names, AI summaries, and contents — across every folder.
                </div>
                {history.length > 0 && (
                  <>
                    <div className="search-history__head">
                      <span className="search-history__title">Recent searches</span>
                      <button className="search-history__clear" onClick={() => setHistory([])}>Clear all</button>
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
                  </>
                )}
              </div>
            )}
          </div>
          <button className="btn-icon" onClick={() => setTheme(theme === "light" ? "dark" : "light")}
                  aria-label={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
                  title={`Switch to ${theme === "light" ? "dark" : "light"} mode`}>
            <Icon name={theme === "light" ? "moon" : "sun"} size={15}/>
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
              <Icon name="shield" size={15}/>
            </button>
          )}
          {t.showLogout && (
            <button className="logout-btn" onClick={realSignOut} title="Sign out">
              <Icon name="log_out" size={13}/> Sign out
            </button>
          )}
        </div>

        {!isMap && (
          <div className="subbar">
            <div className="tabs" role="tablist">
              {[
                { id: "recent", label: "Recent" },
                { id: "name",   label: "Name" },
                { id: "size",   label: "Size" },
              ].map(t => (
                <button
                  key={t.id}
                  className="tab"
                  data-active={sort === t.id}
                  onClick={() => {
                    if (sort === t.id) {
                      // Re-clicking the active tab flips direction.
                      setSortDir(d => d === "asc" ? "desc" : "asc");
                    } else {
                      setSort(t.id);
                      // Recent defaults to newest-first; alphabetical /
                      // numerical sorts default to A→Z / smallest-first.
                      setSortDir(t.id === "recent" ? "desc" : "asc");
                    }
                  }}
                  title={sort === t.id
                    ? `Currently ${sortDir === "asc" ? "ascending" : "descending"} — click to flip`
                    : `Sort by ${t.label}`}
                >
                  {t.label}
                  {sort === t.id && (
                    <Icon
                      name={sortDir === "asc" ? "chevronUp" : "chevronDown"}
                      size={11}
                      style={{ marginLeft: 4, verticalAlign: "-1px" }}
                    />
                  )}
                </button>
              ))}
            </div>
            <div className="topbar__spacer"/>
            {multiSelected.size > 0 && (
              <span style={{ fontSize: 12, color: "var(--ink-2)", marginRight: 8 }}>
                {multiSelected.size} selected
                <button
                  className="btn btn--ghost btn--sm"
                  style={{ marginLeft: 8 }}
                  onClick={clearMultiSelected}
                  title="Clear selection"
                >
                  Clear
                </button>
              </span>
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
                anyFilterActive={anyFilterActive}
                clearAllFilters={clearAllFilters}
                activeCount={[
                  filterScene, filterContentType, filterIndoorOutdoor,
                  filterHasFaces === true ? true : null,
                  filterHasGps === true ? true : null,
                ].filter(v => v != null).length}
              />
            )}
            <button className="btn btn--ghost btn--sm" onClick={() => setShowNewFolder(true)}>
              <Icon name="folderPlus" size={12}/> New folder
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => setShowBestOf(true)}>
              <Icon name="wand" size={12}/> Pick best of burst
            </button>
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
              <Icon name="chevronLeft" size={14}/>
            </button>
            <button onClick={() => navigateToCrumb(-1)}
                    style={{ background: "none", border: 0, padding: 0, color: "var(--ink-2)", cursor: "pointer" }}>
              <Icon name="home" size={12}/> Home
            </button>
            {folderPath.map((c, i) => (
              <React.Fragment key={c.id}>
                <Icon name="chevronRight" size={11} style={{ color: "var(--ink-4)" }}/>
                <button onClick={() => navigateToCrumb(i)}
                        style={{ background: "none", border: 0, padding: 0, color: i === folderPath.length - 1 ? "var(--ink)" : "var(--ink-2)", fontWeight: i === folderPath.length - 1 ? 600 : 400, cursor: "pointer" }}>
                  {c.name}
                </button>
              </React.Fragment>
            ))}
          </div>
        )}

        {isMap
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
      <BestOfModal open={showBestOf} onClose={() => setShowBestOf(false)}/>
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
