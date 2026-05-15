/* Marketing-site API client.
 *
 * The signup endpoint and (admin) viewer endpoints live in the same
 * Render Web Service as this SPA — see ../server.mjs. In dev, Vite
 * proxies /api → http://127.0.0.1:5181 (the Express server). In prod,
 * Express serves both the SPA and the API off the same origin, so
 * relative URLs "just work". */

const API_PREFIX = "/api";

/* Use-case keys are the closed enum the server accepts. Anything else
 * maps to "other" server-side. Keep these IDs short and stable — the
 * admin viewer groups by them and we don't want to renumber if we
 * tweak the user-facing labels later. */
export type WaitlistUseCase =
  | "personal"
  | "family"
  | "creative"
  | "developer"
  | "student"
  | "research"
  | "educator"
  | "professional"
  | "other";

export interface WaitlistSignupBody {
  email: string;
  use_case: WaitlistUseCase;
}

export interface WaitlistSignupResult {
  ok: boolean;
  already_signed_up: boolean;
}

export async function postWaitlistSignup(
  body: WaitlistSignupBody
): Promise<WaitlistSignupResult> {
  const res = await fetch(`${API_PREFIX}/waitlist/signup`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if (res.status === 429) throw new Error("rate-limited");
    if (res.status === 422) throw new Error("invalid-email");
    throw new Error(`server-error-${res.status}`);
  }
  return (await res.json()) as WaitlistSignupResult;
}

// --------------------------------------------------------------------- //
// Admin API — guarded by HTTP Basic Auth on the server. The client stores
// the credentials in sessionStorage so a reload doesn't blow the session
// but a closed tab does.
// --------------------------------------------------------------------- //

const ADMIN_AUTH_KEY = "neuthek.admin.auth";

export function setAdminAuth(user: string, pass: string) {
  const token = btoa(`${user}:${pass}`);
  sessionStorage.setItem(ADMIN_AUTH_KEY, token);
}

export function clearAdminAuth() {
  sessionStorage.removeItem(ADMIN_AUTH_KEY);
}

export function hasAdminAuth(): boolean {
  return !!sessionStorage.getItem(ADMIN_AUTH_KEY);
}

function adminHeaders(): HeadersInit {
  const token = sessionStorage.getItem(ADMIN_AUTH_KEY);
  return token ? { Authorization: `Basic ${token}` } : {};
}

export interface WaitlistEntry {
  id: number;
  email: string;
  use_case: string;
  source: string;
  ip: string | null;
  user_agent: string | null;
  notified: boolean;
  notified_at: string | null;
  created_at: string;
}

export async function listWaitlist(limit = 500): Promise<WaitlistEntry[]> {
  const res = await fetch(`${API_PREFIX}/admin/waitlist?limit=${limit}`, {
    headers: adminHeaders(),
  });
  if (res.status === 401) throw new Error("unauthorized");
  if (!res.ok) throw new Error(`server-error-${res.status}`);
  return (await res.json()) as WaitlistEntry[];
}

export async function markWaitlistNotified(id: number): Promise<WaitlistEntry> {
  const res = await fetch(`${API_PREFIX}/admin/waitlist/${id}/notified`, {
    method: "PATCH",
    headers: adminHeaders(),
  });
  if (res.status === 401) throw new Error("unauthorized");
  if (!res.ok) throw new Error(`server-error-${res.status}`);
  return (await res.json()) as WaitlistEntry;
}
