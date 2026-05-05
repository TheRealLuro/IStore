import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Fuse from "fuse.js";
import { useAuthStore } from "./stores/authStore";
import { useFilterStore } from "./stores/filterStore";
import { useThemeStore } from "./stores/themeStore";
import { listFiles } from "./api/files";
import { listFolders } from "./api/folders";
import { LoginPage } from "./components/LoginPage";
import { StorageBar } from "./components/StorageBar";
import { FilterTabs } from "./components/FilterTabs";
import { SearchBar } from "./components/SearchBar";
import { UploadButton } from "./components/UploadButton";
import { FileGrid } from "./components/FileGrid";
import { PreviewPanel } from "./components/PreviewPanel";
import { BulkActionsBar } from "./components/BulkActionsBar";
import { ConfirmDeleteModal } from "./components/ConfirmDeleteModal";
import { ConsentBanner } from "./components/ConsentBanner";
import { PeopleTray } from "./components/PeopleTray";
import { ThemeToggle } from "./components/ThemeToggle";
import { AccountModal } from "./components/AccountModal";
import { UploadProgressPanel } from "./components/UploadProgressPanel";
import { Breadcrumb } from "./components/Breadcrumb";
import { NewFolderModal } from "./components/NewFolderModal";
import { JumpToTop } from "./components/JumpToTop";
import { AdminPanel } from "./components/AdminPanel";
import { MapView } from "./components/MapView";
import { SortMenu } from "./components/SortMenu";
import { useUIStore } from "./stores/uiStore";
import { useSelectionStore } from "./stores/selectionStore";
import { FolderPlus, Grid3x3, LogOut, Map, Settings, ShieldAlert } from "lucide-react";
import type { FileItem, Folder } from "./types/file";
import type { SortMode } from "./stores/filterStore";

/** Returns a new array sorted by the chosen mode. Search results are
 * sorted by Fuse score (relevance), so this only runs on the no-query
 * branch. */
function applySort(files: FileItem[], mode: SortMode): FileItem[] {
  const out = [...files];
  switch (mode) {
    case "uploaded_asc":
      out.sort((a, b) => a.uploaded_at.localeCompare(b.uploaded_at));
      break;
    case "name_asc":
      out.sort((a, b) =>
        (a.original_filename || "").localeCompare(b.original_filename || ""),
      );
      break;
    case "name_desc":
      out.sort((a, b) =>
        (b.original_filename || "").localeCompare(a.original_filename || ""),
      );
      break;
    case "size_desc":
      out.sort((a, b) => (b.byte_size_served ?? 0) - (a.byte_size_served ?? 0));
      break;
    case "size_asc":
      out.sort((a, b) => (a.byte_size_served ?? 0) - (b.byte_size_served ?? 0));
      break;
    case "uploaded_desc":
    default:
      out.sort((a, b) => b.uploaded_at.localeCompare(a.uploaded_at));
      break;
  }
  return out;
}

export default function App() {
  const user = useAuthStore((s) => s.user);
  const loading = useAuthStore((s) => s.loading);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const signOut = useAuthStore((s) => s.signOut);
  const bootstrapTheme = useThemeStore((s) => s.bootstrap);

  useEffect(() => {
    bootstrapTheme();
    bootstrap();
  }, [bootstrap, bootstrapTheme]);

  // C2 — toast on cloud OAuth handoff return. The backend's
  // /cloud/callback/{provider} 302s to "/?cloud_connected=…" or
  // "/?cloud_error=…" so we read the query string here, fire a toast,
  // and clean the URL so a refresh doesn't re-toast.
  useEffect(() => {
    const url = new URL(window.location.href);
    const connected = url.searchParams.get("cloud_connected");
    const error = url.searchParams.get("cloud_error");
    if (connected) {
      import("react-hot-toast").then(({ default: toast }) =>
        toast.success(`${connected.replace("_", " ")} connected`),
      );
    } else if (error) {
      import("react-hot-toast").then(({ default: toast }) =>
        toast.error(`Cloud connect failed: ${error}`, { duration: 7000 }),
      );
    }
    if (connected || error) {
      url.searchParams.delete("cloud_connected");
      url.searchParams.delete("cloud_error");
      window.history.replaceState({}, "", url.toString());
    }
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-fg-secondary bg-page">
        Loading…
      </div>
    );
  }

  if (!user) {
    return <LoginPage />;
  }

  return (
    <div className="min-h-screen bg-page text-fg">
      <Shell signOut={signOut} />
      <ConfirmDeleteModal />
    </div>
  );
}

function Shell({ signOut }: { signOut: () => void }) {
  const category = useFilterStore((s) => s.category);
  const query = useFilterStore((s) => s.query);
  const person = useFilterStore((s) => s.person);
  const setPerson = useFilterStore((s) => s.setPerson);
  const folderId = useFilterStore((s) => s.folderId);
  const viewMode = useFilterStore((s) => s.viewMode);
  const setViewMode = useFilterStore((s) => s.setViewMode);
  const sortMode = useFilterStore((s) => s.sortMode);
  const setConfirmDelete = useUIStore((s) => s.setConfirmDelete);
  const isSuperuser = useAuthStore((s) => s.user?.is_superuser ?? false);
  // Preview is "open" for both single-file inspection and multi-select
  // bulk panel. Drives the gallery layout shift + backdrop blur below so
  // the preview reads as the focal element.
  const previewId = useUIStore((s) => s.previewId);
  const selectedSize = useSelectionStore((s) => s.selected.size);
  const previewOpen = previewId !== null || selectedSize > 1;
  const [accountOpen, setAccountOpen] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);

  // Person view + global search ignore folder scope (search needs to find
  // a file no matter where it lives). Otherwise the list is folder-scoped.
  const trimmedQuery = query.trim();
  const useGlobalScope = !!person || trimmedQuery.length > 0;
  const filesScope = useGlobalScope ? "ALL" : folderId;

  const { data: rawFiles, isLoading } = useQuery({
    queryKey: ["files", { person, folderId: filesScope }],
    queryFn: () =>
      listFiles({
        person: person ?? undefined,
        folderId: filesScope as string | null | "ALL",
      }),
    staleTime: 10_000,
  });

  // Folders are only relevant when we're browsing a specific scope —
  // global search hides them; person view hides them. We invalidate
  // ["folders", parentId] after create/delete so the grid stays in sync.
  const { data: folders } = useQuery({
    queryKey: ["folders", folderId],
    queryFn: () => listFolders(folderId),
    staleTime: 10_000,
    enabled: !useGlobalScope,
  });

  const filtered = useMemo<FileItem[]>(() => {
    let files = rawFiles ?? [];
    if (category !== "all") {
      files = files.filter((f) => f.category === category);
    }
    const q = query.trim();
    if (!q) return applySort(files, sortMode);

    // Direct extension match (e.g. ".pdf", "pdf", "PDF").
    const stripped = q.replace(/^\./, "").toLowerCase();
    if (/^[a-z0-9]{1,5}$/i.test(stripped)) {
      const extMatch = files.filter((f) =>
        (f.original_filename || "").toLowerCase().endsWith("." + stripped),
      );
      if (extMatch.length > 0) return extMatch;
    }

    // Synthesize an `extension` field per file so it's part of the fuzzy search.
    const enriched = files.map((f) => ({
      ...f,
      _extension: (f.original_filename || "").split(".").pop()?.toLowerCase() ?? "",
    }));

    const fuse = new Fuse(enriched, {
      keys: [
        { name: "original_filename", weight: 0.45 },
        { name: "summary", weight: 0.2 },
        { name: "summary_topic", weight: 0.1 },
        { name: "_extension", weight: 0.1 },
        { name: "scene_label", weight: 0.05 },
        { name: "content_type", weight: 0.05 },
        { name: "category", weight: 0.03 },
        { name: "indoor_outdoor", weight: 0.02 },
      ],
      threshold: 0.4,
      ignoreLocation: true,
      minMatchCharLength: 1,
    });
    return fuse.search(q).map((r) => r.item);
  }, [rawFiles, category, query, sortMode]);

  // Folders also filter by name when search is active (cheap substring,
  // matches feel snappier without Fuse for short folder names).
  const filteredFolders = useMemo<Folder[]>(() => {
    const list = folders ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((f) => f.name.toLowerCase().includes(q));
  }, [folders, query]);

  return (
    <>
      {/* Gallery + chrome — shifts right and dims when the preview panel
          is open so the gallery actually fits the visible viewport instead
          of disappearing under the panel. The transition is on padding +
          opacity only; filter stays on the backdrop layer below to avoid
          creating a stacking context that would clip fixed modals. */}
      <div
        className={`transition-[padding] duration-[520ms] ${
          previewOpen ? "md:pr-[488px]" : "pr-0"
        }`}
        style={{
          // Same iOS easing curve as the panel's slide-in keyframe so the
          // two motions read as a single coordinated transition rather
          // than two independent animations finishing at different times.
          transitionTimingFunction: "cubic-bezier(0.32, 0.72, 0, 1)",
        }}
      >
        <ConsentBanner />
        <StorageBar />
        <header className="sticky top-0 z-10 glass border-b border-divider px-6 py-3 flex items-center justify-between gap-4">
          <h1 className="text-[18px] font-semibold tracking-tight whitespace-nowrap text-fg">
            My Files
          </h1>
          <SearchBar resultsCount={filtered.length} />
          <div className="flex items-center gap-2">
            <button
              onClick={() => setNewFolderOpen(true)}
              className="btn-icon"
              aria-label="New folder"
              title="New folder"
            >
              <FolderPlus className="h-4 w-4" />
            </button>
            <UploadButton />
            <SortMenu />
            <button
              onClick={() => setViewMode(viewMode === "grid" ? "map" : "grid")}
              className="btn-icon"
              aria-label={viewMode === "grid" ? "Map view" : "Grid view"}
              title={viewMode === "grid" ? "Map view" : "Grid view"}
            >
              {viewMode === "grid" ? (
                <Map className="h-4 w-4" />
              ) : (
                <Grid3x3 className="h-4 w-4" />
              )}
            </button>
            <ThemeToggle />
            {isSuperuser && (
              <button
                onClick={() => setAdminOpen(true)}
                className="btn-icon"
                aria-label="Admin"
                title="Admin"
              >
                <ShieldAlert className="h-4 w-4" />
              </button>
            )}
            <button
              onClick={() => setAccountOpen(true)}
              className="btn-icon"
              aria-label="Account settings"
              title="Account"
            >
              <Settings className="h-4 w-4" />
            </button>
            <button
              onClick={signOut}
              className="btn-icon"
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </header>
        <AccountModal open={accountOpen} onClose={() => setAccountOpen(false)} />
        <NewFolderModal open={newFolderOpen} onClose={() => setNewFolderOpen(false)} />
        {isSuperuser && (
          <AdminPanel open={adminOpen} onClose={() => setAdminOpen(false)} />
        )}

        <FilterTabs files={rawFiles ?? []} />
        <PeopleTray />
        <Breadcrumb />
        {person && (
          <div className="px-6 pt-1 pb-2">
            <button
              onClick={() => setPerson(null)}
              className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft text-accent px-3 py-1 text-[12px] font-medium hover:opacity-90"
            >
              Showing photos of {person}
              <span className="text-accent/70">· clear</span>
            </button>
          </div>
        )}
        {viewMode === "map" ? (
          <MapView />
        ) : (
          <FileGrid
            files={filtered}
            folders={useGlobalScope ? [] : filteredFolders}
            query={query}
            loading={isLoading}
          />
        )}
      </div>

      {/* Backdrop blur sits between gallery (z<15) and the preview panel
          (z=20). pointer-events-none so clicks fall through to the
          gallery — user can still pick another card. Modals (z>=30) and
          UploadProgressPanel (z=30) render on top, sharp. */}
      {previewOpen && (
        <div
          aria-hidden
          className="fixed inset-0 z-[15] pointer-events-none backdrop-blur-[2px] bg-page/30 animate-fade-in"
        />
      )}

      <BulkActionsBar files={rawFiles ?? []} />
      <PreviewPanel
        files={rawFiles ?? []}
        onRequestBulkDelete={() => setConfirmDelete(true)}
      />
      <UploadProgressPanel />
      <JumpToTop />
    </>
  );
}
