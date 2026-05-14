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
import { listFiles, getImageGeo, servedUrl } from "@/api/files";
import { getConsentScopes, grantConsent } from "@/api/consent";
import toast from "react-hot-toast";
import { formatDistanceToNowStrict } from "date-fns";
import { RECENT_SEARCHES } from "./data.jsx";

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
  return {
    id: f.id,
    type,
    category: cat,
    name: f.original_filename || `untitled.${ext.toLowerCase()}`,
    size,
    when,
    thumb: type === "image" ? servedUrl(f.id) : null,
    ext,
    topic: f.summary_topic,
    aiContent: f.summary,
    gps: null, // wired separately from /images/geo
    tags: f.status ? [f.status] : [],
    folder: f.folder_id,
    width: f.width,
    height: f.height,
    scene_label: f.scene_label,
    indoor_outdoor: f.indoor_outdoor,
    _real: true,
  };
}

const VIEW_LABELS = {
  gallery: "All files",
  photos: "Photos",
  videos: "Videos",
  docs: "Documents",
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
  showPeopleStrip: true,
  showFolders: true,
  density: "regular",
  brandPitch: "",
  showAdmin: false,
  showLogout: true,
  showFab: true,
};

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
  const [typeFilter, setTypeFilter] = useStateApp("all");
  const [selectedFile, setSelectedFile] = useStateApp(null);
  // Folder navigation. `null` = root, otherwise the folder UUID.
  // `folderPath` is the breadcrumb stack ([{id,name}, ...]).
  const [folderId, setFolderId] = useStateApp(null);
  const [folderPath, setFolderPath] = useStateApp([]);
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

  // search history (clearable)
  const [history, setHistory] = useStateApp(RECENT_SEARCHES);
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

  // file rename overrides (so renamed files visibly update)
  const [nameOverrides, setNameOverrides] = useStateApp({});

  // Real file list from /images/. Search-mode (non-empty query) bypasses
  // the folder scope by passing "ALL"; otherwise we fetch the current folder
  // (null = root). We only fetch when signed-in to avoid firing a 401 on
  // the auth screen.
  const trimmedQuery = query.trim();
  const useGlobalScope = trimmedQuery.length > 0;
  const filesScope = useGlobalScope ? "ALL" : folderId;
  const { data: rawFiles = [] } = useQuery({
    queryKey: ["files", filesScope],
    queryFn: () => listFiles({ folderId: filesScope }),
    enabled: signedIn,
    staleTime: 10_000,
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

  const baseFiles = useMemoApp(() => {
    const geoMap = new Map((geoResp?.points || []).map((p) => [p.id, p]));
    return rawFiles.map((f) => {
      const n = fileItemToNeuthek(f);
      const g = geoMap.get(f.id);
      if (g) n.gps = { lat: g.lat, lng: g.lng, place: null };
      if (nameOverrides[n.id]) n.name = nameOverrides[n.id];
      return n;
    });
  }, [rawFiles, geoResp, nameOverrides]);

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
      if (g) n.gps = { lat: g.lat, lng: g.lng, place: null };
      return n;
    });
  }, [allFilesForMap, geoResp, isMapView]);

  // Sidebar counts — derived from the real file list. The "geo" count is
  // sourced from the global geo response (not the current folder scope) so
  // the Map count in the sidebar matches what the map will actually show.
  const sideCounts = useMemoApp(() => {
    const c = { all: baseFiles.length, image: 0, video: 0, document: 0, geo: 0 };
    for (const f of baseFiles) {
      if (f.category === "image") c.image += 1;
      else if (f.category === "video") c.video += 1;
      else if (f.category === "document") c.document += 1;
    }
    c.geo = (geoResp?.points || []).length;
    return c;
  }, [baseFiles, geoResp]);

  const filesByView = useMemoApp(() => ({
    gallery: baseFiles,
    photos: baseFiles.filter(f => f.type === "image"),
    videos: baseFiles.filter(f => f.type === "video"),
    docs: baseFiles.filter(f => f.type === "doc"),
    people: baseFiles.filter(f => f.type === "image"),
    places: baseFiles.filter(f => f.gps),
    shared: [],
    trash: [],
  }), [baseFiles]);

  const files = filesByView[view] || [];

  const openSub = (key) => {
    if (key === "face") setShowFace(true);
    if (key === "privacy") setShowPrivacy(true);
    if (key === "terms") setShowTerms(true);
    if (key === "delete") setShowDelete(true);
    if (key === "export") setShowExport(true);
  };

  const handleRename = (f) => { setRenameFile(f); setShowRename(true); };
  const saveRename = (newName) => {
    if (renameFile) setNameOverrides(o => ({ ...o, [renameFile.id]: newName }));
  };

  const submitSearch = (q) => {
    const v = (q ?? query).trim();
    if (!v) return;
    setHistory(h => [v, ...h.filter(x => x.toLowerCase() !== v.toLowerCase())].slice(0, 8));
    setShowHistory(false);
  };

  if (authLoading) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "100vh",
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

  const showEmpty = empty || (view === "trash") || (files.length === 0 && !query);
  const densityGap = t.density === "compact" ? 10 : t.density === "comfy" ? 22 : 16;
  const isMap = view === "places";

  return (
    <div className="app" data-preview={selectedFile ? "open" : "closed"}
         style={{ "--dc-grid-gap": densityGap + "px" }}>
      <style>{`.gallery__grid { gap: ${densityGap}px; }`}</style>
      <Sidebar
        view={view}
        onView={(v) => { setView(v); setQuery(""); setSelectedFile(null); }}
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
        <div className="topbar">
          <div className="topbar__title">
            <h1>{VIEW_LABELS[view]}</h1>
            <span className="topbar__title-meta">
              {query
                ? `${files.filter(f => f.name.toLowerCase().includes(query.toLowerCase()) || (f.topic || "").toLowerCase().includes(query.toLowerCase()) || (f.aiContent || "").toLowerCase().includes(query.toLowerCase())).length} results`
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
              <button className="tab" data-active={sort === "recent"} onClick={() => setSort("recent")}>Recent</button>
              <button className="tab" data-active={sort === "name"} onClick={() => setSort("name")}>Name</button>
              <button className="tab" data-active={sort === "size"} onClick={() => setSort("size")}>Size</button>
            </div>
            <div className="topbar__spacer"/>
            <button className="btn btn--ghost btn--sm" onClick={() => setShowNewFolder(true)}>
              <Icon name="folderPlus" size={12}/> New folder
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => setShowBestOf(true)}>
              <Icon name="wand" size={12}/> Pick best of burst
            </button>
            <button className="btn-icon" aria-label="Sort"><Icon name="sort" size={15}/></button>
            <button className="btn-icon" aria-label="View"><Icon name="grid" size={15}/></button>
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
                selected={selectedFile?.id}
                showPeopleStrip={t.showPeopleStrip}
                showFolders={t.showFolders}
                typeFilter={typeFilter}
                onTypeFilter={setTypeFilter}
                onSelect={(f) => setSelectedFile(prev => prev?.id === f.id ? null : f)}
                onRename={handleRename}
                folderId={folderId}
                onEnterFolder={enterFolder}
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
