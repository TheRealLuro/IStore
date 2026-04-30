import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import Fuse from "fuse.js";
import { useAuthStore } from "./stores/authStore";
import { useFilterStore } from "./stores/filterStore";
import { useThemeStore } from "./stores/themeStore";
import { listFiles } from "./api/files";
import { LoginPage } from "./components/LoginPage";
import { StorageBar } from "./components/StorageBar";
import { FilterTabs } from "./components/FilterTabs";
import { SearchBar } from "./components/SearchBar";
import { UploadButton } from "./components/UploadButton";
import { FileGrid } from "./components/FileGrid";
import { PreviewPanel } from "./components/PreviewPanel";
import { BulkActionsBar } from "./components/BulkActionsBar";
import { ConfirmDeleteModal } from "./components/ConfirmDeleteModal";
import { ThemeToggle } from "./components/ThemeToggle";
import { UploadProgressPanel } from "./components/UploadProgressPanel";
import { useUIStore } from "./stores/uiStore";
import { LogOut } from "lucide-react";
import type { FileItem } from "./types/file";

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
  const setConfirmDelete = useUIStore((s) => s.setConfirmDelete);

  const { data: rawFiles, isLoading } = useQuery({
    queryKey: ["files"],
    queryFn: () => listFiles({}),
    staleTime: 10_000,
  });

  const filtered = useMemo<FileItem[]>(() => {
    let files = rawFiles ?? [];
    if (category !== "all") {
      files = files.filter((f) => f.category === category);
    }
    const q = query.trim();
    if (!q) return files;

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
        { name: "original_filename", weight: 0.55 },
        { name: "_extension", weight: 0.15 },
        { name: "scene_label", weight: 0.1 },
        { name: "content_type", weight: 0.1 },
        { name: "category", weight: 0.05 },
        { name: "indoor_outdoor", weight: 0.05 },
      ],
      threshold: 0.4,
      ignoreLocation: true,
      minMatchCharLength: 1,
    });
    return fuse.search(q).map((r) => r.item);
  }, [rawFiles, category, query]);

  return (
    <>
      <StorageBar />
      <header className="sticky top-0 z-10 glass border-b border-divider px-6 py-3 flex items-center justify-between gap-4">
        <h1 className="text-[18px] font-semibold tracking-tight whitespace-nowrap text-fg">
          My Files
        </h1>
        <SearchBar resultsCount={filtered.length} />
        <div className="flex items-center gap-2">
          <UploadButton />
          <ThemeToggle />
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

      <FilterTabs files={rawFiles ?? []} />
      <FileGrid files={filtered} query={query} loading={isLoading} />

      <BulkActionsBar files={rawFiles ?? []} />
      <PreviewPanel
        files={rawFiles ?? []}
        onRequestBulkDelete={() => setConfirmDelete(true)}
      />
      <UploadProgressPanel />
    </>
  );
}
