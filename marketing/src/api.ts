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
  // Newsletter opt-in. The waitlist still only sends two emails by
  // default (early-access + GA); checking this expands the consent to
  // include the weekly newsletter that goes out with each release
  // notes drop. We persist the flag server-side so a future export to
  // a real ESP knows who actually wants the recurring sends.
  newsletter_opt_in?: boolean;
}

export interface WaitlistSignupResult {
  ok: boolean;
  already_signed_up: boolean;
  // True if the email was already verified from a previous signup, so the
  // UI can skip the "check your inbox" prompt.
  already_verified?: boolean;
  // True when the server successfully handed the verification email to
  // the configured ESP (Resend). False if RESEND_API_KEY isn't set or the
  // send call failed — in which case the server logged the link.
  verification_email_sent?: boolean;
  // Dev-only: when the email send couldn't go through (e.g. no API key
  // in local dev), the server returns the verify URL here so the page
  // can show a "click here" link directly. Never included in prod.
  verify_url?: string;
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

export interface VerifyResult {
  ok: boolean;
  email: string;
  verified_at: string;
}

export async function verifyWaitlistEmail(token: string): Promise<VerifyResult> {
  const res = await fetch(`${API_PREFIX}/waitlist/verify?token=${encodeURIComponent(token)}`);
  if (!res.ok) {
    if (res.status === 400) throw new Error("invalid-token");
    throw new Error(`server-error-${res.status}`);
  }
  return (await res.json()) as VerifyResult;
}

export async function resendWaitlistVerify(email: string): Promise<void> {
  const res = await fetch(`${API_PREFIX}/waitlist/resend`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    if (res.status === 429) throw new Error("rate-limited");
    if (res.status === 422) throw new Error("invalid-email");
    throw new Error(`server-error-${res.status}`);
  }
}

export interface UnsubscribeResult {
  ok: boolean;
  email: string;
  unsubscribed_at: string;
}

export async function unsubscribeNewsletter(token: string): Promise<UnsubscribeResult> {
  const res = await fetch(`${API_PREFIX}/waitlist/unsubscribe?token=${encodeURIComponent(token)}`);
  if (!res.ok) {
    if (res.status === 400) throw new Error("invalid-token");
    throw new Error(`server-error-${res.status}`);
  }
  return (await res.json()) as UnsubscribeResult;
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
  newsletter_opt_in: boolean;
  newsletter_consent_at: string | null;
  unsubscribed_at: string | null;
  verified: boolean;
  verified_at: string | null;
  verify_sent_at: string | null;
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

export interface AdminResendResult {
  ok: boolean;
  sent: boolean;
  already_verified?: boolean;
  verify_url?: string;
}

export async function adminResendVerify(id: number): Promise<AdminResendResult> {
  const res = await fetch(`${API_PREFIX}/admin/waitlist/${id}/resend-verify`, {
    method: "POST",
    headers: adminHeaders(),
  });
  if (res.status === 401) throw new Error("unauthorized");
  if (!res.ok) throw new Error(`server-error-${res.status}`);
  return (await res.json()) as AdminResendResult;
}

// --------------------------------------------------------------------- //
// Newsletter admin API
// --------------------------------------------------------------------- //

export interface NewsletterRecipientsInfo {
  slug: string;
  title: string;
  eligible: number;        // verified + opted-in + not yet sent
  already_sent: number;    // dedupe count
  previous_failures: number;
  resend_configured: boolean; // is RESEND_API_KEY set on this deploy
}

export async function adminNewsletterRecipients(slug: string): Promise<NewsletterRecipientsInfo> {
  const res = await fetch(
    `${API_PREFIX}/admin/newsletter/recipients?slug=${encodeURIComponent(slug)}`,
    { headers: adminHeaders() },
  );
  if (res.status === 401) throw new Error("unauthorized");
  if (!res.ok) throw new Error(`server-error-${res.status}`);
  return (await res.json()) as NewsletterRecipientsInfo;
}

export interface NewsletterSendResult {
  ok: boolean;
  slug: string;
  total: number;
  sent: number;
  failed: number;
  failures: { email: string; reason?: string }[];
  resend_configured: boolean;
}

export async function adminNewsletterSend(slug: string): Promise<NewsletterSendResult> {
  const res = await fetch(`${API_PREFIX}/admin/newsletter/send`, {
    method: "POST",
    headers: {
      ...adminHeaders(),
      "content-type": "application/json",
    },
    body: JSON.stringify({ slug }),
  });
  if (res.status === 401) throw new Error("unauthorized");
  if (!res.ok) throw new Error(`server-error-${res.status}`);
  return (await res.json()) as NewsletterSendResult;
}

export function adminNewsletterPreviewUrl(slug: string): string {
  return `${API_PREFIX}/admin/newsletter/preview?slug=${encodeURIComponent(slug)}`;
}

export interface NewsletterTestResult {
  ok: boolean;
  to: string;
  reason?: string | null;
  resend_configured: boolean;
}

/** Send a one-off TEST copy of a newsletter to a single address,
 *  bypassing eligibility — lets the operator verify the pipeline. */
export async function adminNewsletterTest(
  slug: string,
  to: string,
): Promise<NewsletterTestResult> {
  const res = await fetch(`${API_PREFIX}/admin/newsletter/test`, {
    method: "POST",
    headers: { ...adminHeaders(), "content-type": "application/json" },
    body: JSON.stringify({ slug, to }),
  });
  if (res.status === 401) throw new Error("unauthorized");
  if (!res.ok) {
    let detail = `server-error-${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return (await res.json()) as NewsletterTestResult;
}
