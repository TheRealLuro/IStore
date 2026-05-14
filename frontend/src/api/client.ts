const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const TOKEN_KEY = "neuthek.jwt";
const LEGACY_TOKEN_KEY = "istore.jwt";

export const tokens = {
  // Read the new key first, fall back to the legacy one so users who
  // signed in before the IStore → neuthek rename don't get bounced to
  // the auth screen on the next deploy. Any read that finds the legacy
  // token migrates it forward and removes the old entry.
  get: () => {
    const v = localStorage.getItem(TOKEN_KEY);
    if (v) return v;
    const legacy = localStorage.getItem(LEGACY_TOKEN_KEY);
    if (legacy) {
      try { localStorage.setItem(TOKEN_KEY, legacy); localStorage.removeItem(LEGACY_TOKEN_KEY); } catch {}
      return legacy;
    }
    return null;
  },
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(LEGACY_TOKEN_KEY);
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
  const token = tokens.get();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (
    init.body &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
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
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export const API_BASE_URL = API_BASE;
