import { create } from "zustand";

interface SelectionState {
  selected: Set<string>;
  multiSelectMode: boolean;
  toggle: (id: string) => void;
  add: (id: string) => void;
  remove: (id: string) => void;
  setMany: (ids: string[]) => void;
  clear: () => void;
  setMultiSelectMode: (on: boolean) => void;
  isSelected: (id: string) => boolean;
}

export const useSelectionStore = create<SelectionState>((set, get) => ({
  selected: new Set<string>(),
  multiSelectMode: false,
  toggle: (id) =>
    set((s) => {
      const next = new Set(s.selected);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { selected: next };
    }),
  add: (id) =>
    set((s) => {
      const next = new Set(s.selected);
      next.add(id);
      return { selected: next };
    }),
  remove: (id) =>
    set((s) => {
      const next = new Set(s.selected);
      next.delete(id);
      return { selected: next };
    }),
  setMany: (ids) => set({ selected: new Set(ids) }),
  clear: () => set({ selected: new Set(), multiSelectMode: false }),
  setMultiSelectMode: (on) => set({ multiSelectMode: on }),
  isSelected: (id) => get().selected.has(id),
}));
