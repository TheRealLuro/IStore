import { api, tokens } from "./client";
import type { User } from "@/types/file";

export async function login(email: string, password: string): Promise<User> {
  const res = await api.postForm<{ access_token: string; token_type: string }>(
    "/auth/jwt/login",
    { username: email, password },
  );
  tokens.set(res.access_token);
  return await me();
}

export async function register(
  email: string,
  password: string,
  ageConfirmed = true,
): Promise<User> {
  return api.post<User>("/auth/register", {
    email,
    password,
    age_confirmed: ageConfirmed,
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
