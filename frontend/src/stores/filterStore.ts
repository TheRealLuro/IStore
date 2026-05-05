import { create } from "zustand";
import type { FileCategory } from "@/types/file";

export type CategoryFilter = FileCategory | "all";

/** A single segment of the folder breadcrumb. The store keeps the trail
 * (not just the leaf) so the breadcrumb component can render the full
 * "Root / Projects / Assignment 4" without an extra round-trip per
 * segment. Cleared whenever the user enters search or person view. */
export interface FolderCrumb {
  id: string;
  name: string;
}

export type ViewMode = "grid" | "map";

export type SortMode =
  | "uploaded_desc"
  | "uploaded_asc"
  | "name_asc"
  | "name_desc"
  | "size_desc"
  | "size_asc";

interface FilterState {
  category: CategoryFilter;
  query: string;
  person: string | null;
  /** Currently-open folder (null = root view). */
  folderId: string | null;
  /** Path from root to current folder, exclusive of root. */
  folderPath: FolderCrumb[];
  recentQueries: string[];
  /** "grid" (default file gallery) vs. "map" (C3 GPS view). */
  viewMode: ViewMode;
  /** Grid sort order. Folders are always first regardless. */
  sortMode: SortMode;
  setCategory: (c: CategoryFilter) => void;
  setQuery: (q: string) => void;
  setPerson: (name: string | null) => void;
  /** Enter `folder` and push it onto the breadcrumb trail. */
  enterFolder: (folder: FolderCrumb) => void;
  /** Jump to a specific position in the breadcrumb (or root with -1). */
  navigateToCrumb: (index: number) => void;
  pushRecent: (q: string) => void;
  setViewMode: (m: ViewMode) => void;
  setSortMode: (m: SortMode) => void;
  clearAll: () => void;
}

const RECENT_KEY = "istore.recent_searches";

const loadRecent = (): string[] => {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
};

const saveRecent = (xs: string[]): void => {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(xs));
  } catch {
    /* ignore */
  }
};

export const useFilterStore = create<FilterState>((set, get) => ({
  category: "all",
  query: "",
  person: null,
  folderId: null,
  folderPath: [],
  recentQueries: loadRecent(),
  viewMode: "grid",
  sortMode: "uploaded_desc",
  setCategory: (c) => set({ category: c }),
  setQuery: (q) => set({ query: q }),
  setPerson: (name) => set({ person: name }),
  setViewMode: (m) => set({ viewMode: m }),
  setSortMode: (m) => set({ sortMode: m }),
  enterFolder: (folder) =>
    set((s) => ({
      folderId: folder.id,
      folderPath: [...s.folderPath, folder],
      // Drop search/person scope when navigating into a folder so the
      // grid actually shows the folder contents, not a filtered view.
      query: "",
      person: null,
    })),
  navigateToCrumb: (index) => {
    if (index < 0) {
      set({ folderId: null, folderPath: [] });
      return;
    }
    const path = get().folderPath.slice(0, index + 1);
    const target = path[path.length - 1];
    set({ folderId: target?.id ?? null, folderPath: path });
  },
  pushRecent: (q) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    const cur = get().recentQueries.filter((x) => x !== trimmed);
    const next = [trimmed, ...cur].slice(0, 8);
    saveRecent(next);
    set({ recentQueries: next });
  },
  clearAll: () =>
    set({
      category: "all",
      query: "",
      person: null,
      folderId: null,
      folderPath: [],
    }),
}));
