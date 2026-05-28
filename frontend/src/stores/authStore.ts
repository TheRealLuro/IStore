import { create } from "zustand";
import type { User } from "@/types/file";
import { tokens } from "@/api/client";
import { logout as serverLogout, me } from "@/api/auth";
import { lockVault } from "@/vault/session";

interface AuthState {
  user: User | null;
  loading: boolean;
  bootstrap: () => Promise<void>;
  setUser: (u: User | null) => void;
  sessionExpired: () => void;
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
  sessionExpired: () => {
    // Server rejected our session mid-flight (cookie expired / revoked /
    // token_version bumped). Drop local auth state so the app re-renders the
    // sign-in screen instead of leaving the user in a zombie half-authed
    // state. We do NOT call serverLogout — the cookie is already dead.
    if (!useAuthStore.getState().user) return;
    try { lockVault(); } catch { /* noop */ }
    try { tokens.clear(); } catch { /* noop */ }
    set({ user: null, loading: false });
  },
  signOut: async () => {
    // Drop the in-memory vault key immediately — sign-out must not leave
    // an unlocked vault accessible to whoever signs in next.
    lockVault();
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

// Bridge the api-client's global 401 signal (api/client.ts dispatches
// "neuthek:session-expired" when a live session is rejected) into the auth
// store so the app drops to the sign-in screen instead of showing a confusing
// "not authorized" on whichever view fired the fresh request.
if (typeof window !== "undefined") {
  window.addEventListener("neuthek:session-expired", () => {
    useAuthStore.getState().sessionExpired();
  });
}
