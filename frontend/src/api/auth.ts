import { ApiError, api, tokens } from "./client";
import type { User } from "@/types/file";

/** Sentinel thrown when /auth/jwt/login refuses because the user has
 * TOTP enabled. The caller catches this, prompts for the 6-digit code,
 * and re-submits via `loginWithTotp`. */
export class TotpRequiredError extends Error {
  constructor() { super("totp_required"); }
}

export async function login(email: string, password: string): Promise<User> {
  try {
    const res = await api.postForm<{ access_token: string; token_type: string }>(
      "/auth/jwt/login",
      { username: email, password },
    );
    tokens.set(res.access_token);
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
  const res = await api.postForm<{ access_token: string; token_type: string }>(
    "/auth/jwt/login-totp",
    { username: email, password, totp_code: totpCode },
  );
  tokens.set(res.access_token);
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

export function logout(): void {
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

/** Trade a recovery code for a JWT. Drops the JWT into local storage
 * directly so the existing me() bootstrap picks the user up. */
export async function recoveryLogin(
  email: string,
  code: string,
): Promise<User> {
  const res = await api.post<{ access_token: string; token_type: string }>(
    "/account/recovery-codes/login",
    { email, code },
  );
  tokens.set(res.access_token);
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
