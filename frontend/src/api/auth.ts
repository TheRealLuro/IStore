import { ApiError, api, tokens } from "./client";
import type { User } from "@/types/file";

/** Sentinel thrown when /auth/jwt/login refuses because the user has
 * TOTP enabled. The caller catches this, prompts for the 6-digit code,
 * and re-submits via `loginWithTotp`. */
export class TotpRequiredError extends Error {
  constructor() { super("totp_required"); }
}

export async function login(email: string, password: string): Promise<User> {
  // Cookie auth (2026-05+): the backend's /auth/cookie/login sets a
  // HttpOnly Secure SameSite=Lax `neuthek_session` cookie and returns
  // 204 No Content. We flip the localStorage breadcrumb so the
  // bootstrap() check has something cheap to read on next mount; the
  // real auth credential lives in the cookie, which JS can't see.
  try {
    await api.postForm<void>(
      "/auth/cookie/login",
      { username: email, password },
    );
    tokens.set();
    return await me();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401 && /totp_required/i.test(e.detail || "")) {
      throw new TotpRequiredError();
    }
    throw e;
  }
}

export async function loginWithTotp(
  email: string, password: string, totpCode: string,
): Promise<User> {
  // TOTP login: the backend's /auth/jwt/login-totp returns the JWT in
  // the response body (Bearer transport). We then exchange it for a
  // cookie session by hitting /auth/cookie/login with the same
  // credentials — but the simpler path is to just mint the cookie on
  // the same TOTP endpoint. Until the backend ships that, we fall
  // through to bearer mode for the totp path; the next non-totp login
  // moves the user to cookie.
  // TODO: add /auth/cookie/login-totp variant.
  const res = await api.postForm<{ access_token: string; token_type: string }>(
    "/auth/jwt/login-totp",
    { username: email, password, totp_code: totpCode },
  );
  // Legacy bearer mode — set the JWT into localStorage so subsequent
  // requests authenticate via the Authorization header path that
  // client.ts still supports as a fallback. Users with 2FA will move
  // to cookie auth automatically once we add the TOTP cookie variant.
  try { localStorage.setItem("neuthek.jwt", res.access_token); } catch {}
  tokens.set();
  return await me();
}

export interface RegisterConsent {
  kind: string;
  state: "GRANTED" | "WITHDRAWN";
}

export async function register(
  email: string,
  password: string,
  ageConfirmed = true,
  consents?: RegisterConsent[],
  consentSignature?: string,
): Promise<User> {
  return api.post<User>("/auth/register", {
    email,
    password,
    age_confirmed: ageConfirmed,
    // §B2 — collect the consent ledger BEFORE the user row is
    // externally visible. The backend's UserManager.create() override
    // writes the ConsentRecord rows in the same transaction as the
    // user. Legacy callers pass nothing here; the post-signup
    // consents modal still works as a fallback.
    ...(consents && consents.length ? { consents } : {}),
    ...(consentSignature ? { consent_signature: consentSignature } : {}),
  });
}

export async function me(): Promise<User> {
  return api.get<User>("/users/me");
}

/** Update the current user's profile. fastapi-users' `/users/me` accepts
 * any subset of (email, password, display_name); the server hashes the
 * password if provided. Pass `currentPassword` only as a UX hint — the
 * server doesn't verify it (fastapi-users' default contract), so we
 * verify locally by re-auth before submitting. */
export async function updateMe(body: {
  email?: string;
  password?: string;
  display_name?: string | null;
}): Promise<User> {
  return api.patch<User>("/users/me", body);
}

export async function logout(): Promise<void> {
  // Hit the cookie-logout route so the backend clears the
  // `neuthek_session` cookie. JS-set cookie deletion is unreliable
  // across browsers + the cookie is HttpOnly, so we rely on the
  // server's Set-Cookie with Max-Age=0. The local breadcrumb +
  // any leftover Bearer JWT is cleared by tokens.clear() AFTER the
  // server confirms (or fails) so a transient network error doesn't
  // strand the FE in a "looks signed in but isn't" state.
  try {
    await api.post<void>("/auth/cookie/logout");
  } catch {
    // Swallow — even if the server is unreachable we still want to
    // clear the local state so the user UI signs them out.
  }
  tokens.clear();
}

// ---- Phase 13 (C6) account recovery ----

/** Trigger a password-reset email. fastapi-users returns 202 even when
 * the email isn't on file — that's intentional, prevents enumeration. */
export async function forgotPassword(email: string): Promise<void> {
  await api.post<void>("/auth/forgot-password", { email });
}

/** Consume a reset-password JWT (from the email link). */
export async function resetPassword(
  token: string,
  password: string,
): Promise<void> {
  await api.post<void>("/auth/reset-password", { token, password });
}

/** Send a fresh verification email. */
export async function requestVerify(email: string): Promise<void> {
  await api.post<void>("/auth/request-verify-token", { email });
}

/** Consume a verify JWT (from the email link). Returns the user. */
export async function verifyEmail(token: string): Promise<User> {
  return api.post<User>("/auth/verify", { token });
}

export interface RecoveryCodesStatus {
  has_codes: boolean;
  remaining: number;
  generated_at: string | null;
}

export async function getRecoveryCodesStatus(): Promise<RecoveryCodesStatus> {
  return api.get<RecoveryCodesStatus>("/account/recovery-codes");
}

/** Issue 8 fresh codes — also sent by email. The plaintext is returned
 * exactly once; the server only stores argon2 hashes. */
export async function regenerateRecoveryCodes(): Promise<{ codes: string[] }> {
  return api.post<{ codes: string[] }>(
    "/account/recovery-codes/regenerate",
  );
}

export interface AccountActivityEntry {
  id: number;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

/** User-visible activity log. Returns the caller's most recent audit
 *  events — sign-ins, consent changes, renames, deletes, etc. */
export async function getAccountActivity(limit = 50): Promise<AccountActivityEntry[]> {
  return api.get<AccountActivityEntry[]>(`/account/activity?limit=${limit}`);
}

export interface TrashSummary {
  count: number;
  total_bytes: number;
}

/** Soft-deleted images for the caller. Just the count + total bytes —
 *  per-row listing comes later if we add a recoverable-items grid. */
export async function getAccountTrash(): Promise<TrashSummary> {
  return api.get<TrashSummary>("/account/trash");
}

/** Permanently delete every soft-deleted image. Cannot be undone. */
export async function emptyAccountTrash(): Promise<{ deleted: number }> {
  return api.post<{ deleted: number }>("/account/trash/empty");
}

/** Trade a recovery code for a session. Same caveat as
 *  `loginWithTotp` — the recovery-codes endpoint currently returns
 *  Bearer JWT only; we stash it in legacy localStorage so requests
 *  authenticate while the user re-establishes a normal session.
 *  When the backend adds a cookie variant we'll flip this over. */
export async function recoveryLogin(
  email: string,
  code: string,
): Promise<User> {
  const res = await api.post<{ access_token: string; token_type: string }>(
    "/account/recovery-codes/login",
    { email, code },
  );
  try { localStorage.setItem("neuthek.jwt", res.access_token); } catch {}
  tokens.set();
  return await me();
}

// ---- §1.2.2 TOTP 2FA ----

export interface TwoFactorStatus {
  enabled: boolean;
  verified_at: string | null;
}

export interface TwoFactorSetupBundle {
  secret: string;
  otpauth_uri: string;
  qr_png_base64: string;
  issuer: string;
}

export const getTwoFactorStatus = () =>
  api.get<TwoFactorStatus>("/account/2fa/status");

export const setupTwoFactor = () =>
  api.post<TwoFactorSetupBundle>("/account/2fa/setup");

export const verifyTwoFactor = (code: string) =>
  api.post<TwoFactorStatus>("/account/2fa/verify", { code });

export const disableTwoFactor = (
  body: { code?: string; password?: string },
) => api.post<TwoFactorStatus>("/account/2fa/disable", body);

// ---- §1.2.3 notification preferences ----

export interface NotificationPrefRow {
  kind: string;
  channel: string;
  enabled: boolean;
}

export interface NotificationPrefsResponse {
  kinds: string[];
  channels: string[];
  prefs: NotificationPrefRow[];
}

export const getNotificationPrefs = () =>
  api.get<NotificationPrefsResponse>("/account/notifications");

export const updateNotificationPrefs = (prefs: NotificationPrefRow[]) =>
  api.patch<NotificationPrefsResponse>("/account/notifications", { prefs });

// Sign-in-with-Google link/unlink for the Settings → Account row.
// `linkGoogleAccount` returns a URL the FE should hard-navigate to —
// the user consents on Google, the callback stamps google_sub onto
// this user, and Google redirects back to the FE with #sso_linked=1.
export async function linkGoogleAccount(): Promise<{ auth_url: string }> {
  return api.post<{ auth_url: string }>("/auth/google/link");
}

export async function unlinkGoogleAccount(): Promise<{ linked: boolean; changed: boolean }> {
  return api.delete<{ linked: boolean; changed: boolean }>("/auth/google/link");
}
