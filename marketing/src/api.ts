/* Tiny API client for the marketing site. Only the public waitlist
   surface lives here today — the rest of the marketing site is
   pure static content. */

const RAW = (import.meta.env.VITE_API_BASE_URL || "").trim();

// Default to the local backend during dev so `npm run dev` "just works"
// without anyone setting an env var. Static-host builds (Render) without
// VITE_API_BASE_URL set will simply fall through to localStorage.
export const API_BASE_URL: string =
  RAW || (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

export type WaitlistUseCase =
  | "personal"
  | "family"
  | "creative"
  | "research"
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
  if (!API_BASE_URL) {
    throw new Error("offline");
  }
  const res = await fetch(`${API_BASE_URL}/waitlist/signup`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if (res.status === 429) {
      throw new Error("rate-limited");
    }
    if (res.status === 422) {
      throw new Error("invalid-email");
    }
    throw new Error(`server-error-${res.status}`);
  }
  return (await res.json()) as WaitlistSignupResult;
}
