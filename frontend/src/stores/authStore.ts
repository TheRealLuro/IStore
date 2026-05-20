import { create } from "zustand";
import type { User } from "@/types/file";
import { tokens } from "@/api/client";
import { logout as serverLogout, me } from "@/api/auth";

interface AuthState {
  user: User | null;
  loading: boolean;
  bootstrap: () => Promise<void>;
  setUser: (u: User | null) => void;
  signOut: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: true,
  bootstrap: async () => {
    // Cookie auth: we can't read the HttpOnly session cookie from JS,
    // so we always TRY `me()` and let the 401 path tell us the user
    // isn't authenticated. The localStorage breadcrumb is just an
    // optimization to avoid a wasted network round-trip when we know
    // there's definitely no session — the actual source of truth is
    // the server's response to a cheap /users/me call.
    try {
      const u = await me();
      set({ user: u, loading: false });
    } catch {
      // 401 / 403 / network error → unauthenticated. Clear local
      // breadcrumb so the next bootstrap exits early.
      tokens.clear();
      set({ user: null, loading: false });
    }
  },
  setUser: (u) => set({ user: u }),
  signOut: async () => {
    // Tell the server to clear the cookie before we drop local
    // state. `serverLogout` swallows network errors so a
    // disconnected user can still sign out locally.
    await serverLogout();
    // Sweep per-user localStorage so the next sign-in on a shared
    // device doesn't see the previous tenant's data. Search history
    // can contain names/addresses; playback/quality prefs are
    // device-local but the user reasonably expects sign-out to be
    // a clean break. We KEEP the autoplay/quality prefs (per-device,
    // not per-user) — only data that's clearly user-scoped is purged.
    try {
      const purge = [
        "neuthek.recentSearches",
        "neuthek.last_view",
        "neuthek.last_folder",
      ];
      for (const k of purge) localStorage.removeItem(k);
    } catch {
      // localStorage can throw in some private-browsing modes;
      // sign-out shouldn't fail because of it.
    }
    set({ user: null });
  },
}));
