// §C2/C4.6 — cloud sync client. The hosted deployment may not have
// OAuth configured (`*_oauth_client_id` empty in `.env`), in which
// case `getCloudProviders` reports those providers as `needs_setup`
// rather than `available`. Callers should gate the Connect button
// on status.

import { api } from "./client";

// §C4.6 — provider IDs from backend.cloud_sync.list_providers. The
// "available + needs_setup" set is real providers; placeholder slots
// with status "coming_soon" can't be passed to /cloud/links/{provider}.
// Google Drive + Dropbox use OAuth; iCloud uses Apple-ID + 2FA;
// Proton Drive + MEGA use email + password (driven by rclone under
// the hood).
export type CloudProvider =
  | "google_drive"
  | "dropbox"
  | "icloud"
  | "proton_drive"
  | "mega";

export interface CloudProviderInfo {
  id: CloudProvider;
  name: string;
  kind: "oauth2" | "app_password" | "credentials";
  status: "available" | "needs_setup" | "coming_soon";
  /** Drives the FE's connect affordance: "oauth" → browser redirect;
   *  "apple_id" → iCloud password+2FA modal; "password" → Proton /
   *  MEGA credentials modal. */
  auth_shape: "oauth" | "apple_id" | "password";
  blurb: string;
  docs: string | null;
}

export async function getCloudProviders(): Promise<CloudProviderInfo[]> {
  return api.get<CloudProviderInfo[]>("/cloud/providers");
}

export interface CloudLink {
  id: number;
  provider: CloudProvider;
  status: "active" | "conflicts" | "error";
  scopes: string | null;
  last_synced_at: string | null;
  created_at: string;
  ai_opted_in: boolean;
}

export interface ConnectResponse {
  auth_url: string;
  state: string;
}

export interface SyncResult {
  seen: number;
  pulled: number;
  skipped_unchanged: number;
  conflicts: number;
  conflict_remote_ids: string[];
  provider: CloudProvider;
}

export interface ConflictItem {
  remote_id: string | null;
  remote_path: string | null;
  reason: string | null;
  at: string | null;
}

export async function listCloudLinks(): Promise<CloudLink[]> {
  return api.get<CloudLink[]>("/cloud/links");
}

export async function connectCloud(provider: CloudProvider): Promise<ConnectResponse> {
  return api.post<ConnectResponse>(`/cloud/links/${provider}`);
}

export async function syncCloudLink(linkId: number): Promise<SyncResult> {
  return api.post<SyncResult>(`/cloud/links/${linkId}/sync`);
}

/** Poll the status of an in-flight sync. The /sync endpoint kicks
 *  off a background task and returns immediately; this endpoint
 *  reports the actual progress (state / counts / error). */
export interface SyncStatus {
  state: "idle" | "running" | "done" | "error";
  started_at: string | null;
  finished_at: string | null;
  counts: SyncResult | null;
  error: string | null;
}
export async function getSyncStatus(linkId: number): Promise<SyncStatus> {
  return api.get<SyncStatus>(`/cloud/links/${linkId}/sync-status`);
}

export async function disconnectCloud(linkId: number): Promise<void> {
  await api.delete(`/cloud/links/${linkId}`);
}

export async function setCloudAiOptIn(
  linkId: number,
  optedIn: boolean,
): Promise<{ affected: number; provider: CloudProvider; opted_in: boolean }> {
  return api.post(`/cloud/links/${linkId}/ai-opt-in`, { opted_in: optedIn });
}

export async function listCloudConflicts(linkId: number): Promise<{
  provider: CloudProvider;
  conflicts: ConflictItem[];
}> {
  return api.get(`/cloud/links/${linkId}/conflicts`);
}

/** Total file count + byte size on the provider's side. Walks the
 *  provider API live (~3s on a 50k-file Drive). Returns null when
 *  no link exists, the link is revoked, or the API call failed —
 *  the storage panel hides the totals row in that case. */
export interface ProviderFolderStats {
  provider: string;
  file_count: number;
  total_bytes: number;
}
export async function getProviderFolderStats(
  provider: CloudProvider,
): Promise<ProviderFolderStats | null> {
  return api.get<ProviderFolderStats | null>(`/cloud/folder-stats/${provider}`);
}

// §C4.6 — iCloud Drive sign-in. Apple doesn't have OAuth, so the
// flow is: user enters Apple ID + password into a neuthek form,
// backend constructs a pyicloud session, returns `requires_2fa=true`
// + a session_id, FE prompts for the 6-digit code, backend validates
// + persists the link. No browser redirect involved — the whole
// dance lives inside a modal.

export interface ICloudStartResponse {
  requires_2fa: boolean;
  /** Set when requires_2fa is true. Pass it back to /icloud/verify
   *  along with the user's 6-digit code. */
  session_id?: string;
  /** Set when requires_2fa is false (rare: trusted device or 2FA-
   *  disabled account) — the link was persisted immediately. */
  link_id?: number;
  message?: string;
}

export async function icloudStart(
  apple_id: string, password: string,
): Promise<ICloudStartResponse> {
  return api.post<ICloudStartResponse>(
    "/cloud/icloud/start", { apple_id, password },
  );
}

export async function icloudVerify(
  session_id: string, code: string,
): Promise<{ link_id: number }> {
  return api.post<{ link_id: number }>(
    "/cloud/icloud/verify", { session_id, code },
  );
}

// §C4.6 — Proton Drive sign-in. Proton has 2FA, so the shape mirrors
// iCloud's: /start returns `requires_2fa=true` + session_id if 2FA
// is enabled on the account, otherwise persists the link in one
// shot. /verify completes the dance with the 6-digit code.
export interface ProtonStartResponse {
  requires_2fa: boolean;
  /** Set when requires_2fa is true. Pass back to /proton-drive/verify
   *  with the 2FA code. */
  session_id?: string;
  /** Set when requires_2fa is false (2FA-disabled account) — the
   *  link was persisted immediately. */
  link_id?: number;
  message?: string;
}

export async function protonStart(
  email: string, password: string,
): Promise<ProtonStartResponse> {
  return api.post<ProtonStartResponse>(
    "/cloud/proton-drive/start", { email, password },
  );
}

export async function protonVerify(
  session_id: string, code: string,
): Promise<{ link_id: number }> {
  return api.post<{ link_id: number }>(
    "/cloud/proton-drive/verify", { session_id, code },
  );
}

// §C4.6 — MEGA sign-in. MEGA's rclone backend has no 2FA hook, so the
// flow is single-step: /start either persists the link (credentials
// were good) or 400s (credentials were bad). No /verify needed.
export async function megaStart(
  email: string, password: string,
): Promise<{ link_id: number }> {
  return api.post<{ link_id: number }>(
    "/cloud/mega/start", { email, password },
  );
}
