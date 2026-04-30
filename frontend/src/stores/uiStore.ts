import { create } from "zustand";

interface UIState {
  previewId: string | null;
  confirmDeleteOpen: boolean;
  deleting: boolean;
  undoBatch: { ids: string[]; deadline: number } | null;
  setPreview: (id: string | null) => void;
  setConfirmDelete: (open: boolean) => void;
  setDeleting: (b: boolean) => void;
  setUndoBatch: (b: { ids: string[]; deadline: number } | null) => void;
}

export const useUIStore = create<UIState>((set) => ({
  previewId: null,
  confirmDeleteOpen: false,
  deleting: false,
  undoBatch: null,
  setPreview: (id) => set({ previewId: id }),
  setConfirmDelete: (open) => set({ confirmDeleteOpen: open }),
  setDeleting: (b) => set({ deleting: b }),
  setUndoBatch: (b) => set({ undoBatch: b }),
}));
