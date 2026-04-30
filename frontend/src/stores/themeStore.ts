import { create } from "zustand";

export type Theme = "light" | "dark";

interface ThemeState {
  theme: Theme;
  toggle: () => void;
  set: (t: Theme) => void;
  bootstrap: () => void;
}

const KEY = "istore.theme";

const apply = (t: Theme) => {
  const root = document.documentElement;
  if (t === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
};

export const useThemeStore = create<ThemeState>((setState, get) => ({
  theme: "light",
  toggle: () => {
    const next: Theme = get().theme === "light" ? "dark" : "light";
    apply(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* ignore */
    }
    setState({ theme: next });
  },
  set: (t) => {
    apply(t);
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* ignore */
    }
    setState({ theme: t });
  },
  bootstrap: () => {
    let stored: Theme | null = null;
    try {
      stored = (localStorage.getItem(KEY) as Theme | null) ?? null;
    } catch {
      /* ignore */
    }
    const initial: Theme =
      stored ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    apply(initial);
    setState({ theme: initial });
  },
}));
