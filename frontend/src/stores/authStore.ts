import { create } from "zustand";
import type { User } from "@/types/file";
import { tokens } from "@/api/client";
import { me } from "@/api/auth";

interface AuthState {
  user: User | null;
  loading: boolean;
  bootstrap: () => Promise<void>;
  setUser: (u: User | null) => void;
  signOut: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: true,
  bootstrap: async () => {
    if (!tokens.get()) {
      set({ loading: false });
      return;
    }
    try {
      const u = await me();
      set({ user: u, loading: false });
    } catch {
      tokens.clear();
      set({ user: null, loading: false });
    }
  },
  setUser: (u) => set({ user: u }),
  signOut: () => {
    tokens.clear();
    set({ user: null });
  },
}));
