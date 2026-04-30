import { create } from "zustand";

export type UploadState = "uploading" | "done" | "error" | "cancelled";

export interface UploadItem {
  id: string;
  name: string;
  size: number;
  uploaded: number;
  startedAt: number;
  state: UploadState;
  error?: string;
  /** XHR reference so we can abort. */
  xhr?: XMLHttpRequest;
}

interface UploadStoreState {
  items: UploadItem[];
  collapsed: boolean;
  start: (name: string, size: number, xhr?: XMLHttpRequest) => string;
  setProgress: (id: string, uploaded: number) => void;
  finish: (id: string, state: "done" | "error" | "cancelled", error?: string) => void;
  cancel: (id: string) => void;
  clearDone: () => void;
  setCollapsed: (b: boolean) => void;
}

export const useUploadStore = create<UploadStoreState>((set, get) => ({
  items: [],
  collapsed: false,
  start: (name, size, xhr) => {
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `u-${Math.random().toString(36).slice(2)}-${Date.now()}`;
    set((s) => ({
      items: [
        ...s.items,
        {
          id,
          name,
          size,
          uploaded: 0,
          startedAt: Date.now(),
          state: "uploading",
          xhr,
        },
      ],
      collapsed: false,
    }));
    return id;
  },
  setProgress: (id, uploaded) =>
    set((s) => ({
      items: s.items.map((i) => (i.id === id ? { ...i, uploaded } : i)),
    })),
  finish: (id, state, error) =>
    set((s) => ({
      items: s.items.map((i) =>
        i.id === id
          ? {
              ...i,
              state,
              error,
              uploaded: state === "done" ? i.size : i.uploaded,
              xhr: undefined,
            }
          : i,
      ),
    })),
  cancel: (id) => {
    const item = get().items.find((i) => i.id === id);
    item?.xhr?.abort();
    set((s) => ({
      items: s.items.map((i) =>
        i.id === id ? { ...i, state: "cancelled", xhr: undefined } : i,
      ),
    }));
  },
  clearDone: () =>
    set((s) => ({
      items: s.items.filter((i) => i.state === "uploading"),
    })),
  setCollapsed: (b) => set({ collapsed: b }),
}));
