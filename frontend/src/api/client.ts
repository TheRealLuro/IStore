const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

// HttpOnly-cookie auth (2026-05+). The JWT lives in a `neuthek_session`
// cookie that JS cannot read; the browser ships it automatically on
// every same-origin or CORS-credentialed fetch via `credentials: "include"`.
// The previous build stored the JWT in localStorage where any XSS in the
// origin could read it; the cookie path closes that exfiltration vector.
//
// `tokens` is kept as a thin facade so existing callers (`tokens.get()`,
// `tokens.clear()`) don't break. There's no token to RETURN anymore —
// `get()` reports whether a session likely exists (cookie present is
// not directly observable from JS; we use a non-secret `neuthek_signed_in`
// breadcrumb cookie the backend sets in tandem, OR fall back to a
// localStorage flag the auth flow toggles on login success). `set` /
// `clear` only flip that breadcrumb; the real cookie is owned by the
// server and cleared by the /auth/cookie/logout endpoint.

const SIGNED_IN_KEY = "neuthek.signedIn";
// Legacy localStorage keys we used to hold the JWT in. Any user
// returning from a pre-cookie build still has these; the new flow
// works fine without them, but we proactively purge so an XSS surface
// can't read a leftover JWT after the user upgrades the FE.
const LEGACY_TOKEN_KEYS = ["neuthek.jwt", "istore.jwt"];

function purgeLegacyTokens(): void {
  for (const k of LEGACY_TOKEN_KEYS) {
    try { localStorage.removeItem(k); } catch { /* private browsing */ }
  }
}
// Run once on module load — if a user upgrades the frontend mid-session
// the old localStorage JWT is gone before the next render.
purgeLegacyTokens();

export const tokens = {
  // Returns a truthy string when the caller has any kind of session
  // (legacy localStorage JWT OR cookie breadcrumb). Callers that need
  // a real Bearer token should check whether the returned value is
  // literal `"cookie"` (no Bearer available; rely on `credentials:
  // "include"` to ship the session cookie) vs. an actual JWT.
  get: (): string | null => {
    for (const k of LEGACY_TOKEN_KEYS) {
      try {
        const v = localStorage.getItem(k);
        // NOTE: we don't `removeItem` here — that races with the
        // request() path in this same file which reads the same key
        // for the transitional Bearer header. The signOut path is the
        // one that purges (see authStore.ts).
        if (v) return v;
      } catch { /* fall through */ }
    }
    // Cookie session breadcrumb. Returns a sentinel so boolean
    // checks (`!!tokens.get()`) work uniformly across both modes
    // without exposing the real session credential to JS.
    try {
      return localStorage.getItem(SIGNED_IN_KEY) ? "cookie" : null;
    } catch {
      return null;
    }
  },
  // No-op shim. Cookie mode: the backend sets the session cookie on
  // /auth/cookie/login; the FE just flips a breadcrumb so the
  // `bootstrap()` check has something to read.
  set: (_token?: string) => {
    try { localStorage.setItem(SIGNED_IN_KEY, "1"); } catch {}
  },
  clear: () => {
    try {
      localStorage.removeItem(SIGNED_IN_KEY);
      purgeLegacyTokens();
    } catch {}
  },
};

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  parse: "json" | "blob" | "text" | "raw" = "json",
): Promise<T> {
  const headers = new Headers(init.headers);
  // Transitional Bearer support — if a returning user still has a
  // localStorage JWT (pre-cookie build), let them keep working until
  // they sign out and back in. `tokens.get()` will eat the legacy key
  // on first read above.
  const legacy = (() => {
    try {
      for (const k of LEGACY_TOKEN_KEYS) {
        const v = localStorage.getItem(k);
        if (v) return v;
      }
    } catch { /* private mode */ }
    return null;
  })();
  if (legacy) headers.set("Authorization", `Bearer ${legacy}`);
  if (
    init.body &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    // Send the session cookie on every API call. Required for cookie
    // auth to work across the dev cross-origin setup
    // (5173 → 8000) and harmless on same-origin prod.
    credentials: "include",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = (j as { detail?: string }).detail || detail;
    } catch {
      /* fall through */
    }
    throw new ApiError(res.status, detail);
  }
  if (parse === "blob") return (await res.blob()) as unknown as T;
  if (parse === "text") return (await res.text()) as unknown as T;
  if (parse === "raw") return res as unknown as T;
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, parse?: "json" | "blob" | "text" | "raw") =>
    request<T>(path, { method: "GET" }, parse),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    }),
  postForm: <T>(path: string, params: Record<string, string>) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(params).toString(),
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: body ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export const API_BASE_URL = API_BASE;
