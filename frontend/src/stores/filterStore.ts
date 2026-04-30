import { create } from "zustand";
import type { FileCategory } from "@/types/file";

export type CategoryFilter = FileCategory | "all";

interface FilterState {
  category: CategoryFilter;
  query: string;
  recentQueries: string[];
  setCategory: (c: CategoryFilter) => void;
  setQuery: (q: string) => void;
  pushRecent: (q: string) => void;
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
  recentQueries: loadRecent(),
  setCategory: (c) => set({ category: c }),
  setQuery: (q) => set({ query: q }),
  pushRecent: (q) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    const cur = get().recentQueries.filter((x) => x !== trimmed);
    const next = [trimmed, ...cur].slice(0, 8);
    saveRecent(next);
    set({ recentQueries: next });
  },
  clearAll: () => set({ category: "all", query: "" }),
}));
