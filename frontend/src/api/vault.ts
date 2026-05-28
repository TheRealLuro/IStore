// Zero-knowledge vault API client (VLT-5). Mirrors backend/api/vault.py.
//
// Everything that crosses this boundary is already CIPHERTEXT — the server
// stores and returns opaque base64 (nonce, ciphertext) blobs plus the public
// KDF parameters. The plaintext is encrypted/decrypted client-side in
// `@/vault/crypto`; this module never touches a key or a plaintext.

import { api, ApiError, API_BASE_URL } from "./client";

// password/note/seed/card are small items encrypted wholesale; "file" items
// are object-storage-backed (their bytes live in MinIO as ciphertext).
export type VaultItemKind = "password" | "note" | "seed" | "card" | "file";

export interface VaultMeta {
  kdf: string;
  kdf_iterations: number;
  kdf_salt: string; // base64
  verifier_nonce: string; // base64
  verifier_ct: string; // base64
  // VLT-8 — present once the account keypair has been provisioned. The
  // private key is master-key-wrapped; the server never sees it unwrapped.
  account_public_key?: string | null; // base64 raw P-256 point
  enc_account_private_key?: string | null; // base64 nonce‖AES-GCM(pkcs8)
  created_at?: string;
  updated_at?: string;
}

export interface VaultSetupBody {
  kdf: string;
  kdf_iterations: number;
  kdf_salt: string; // base64
  verifier_nonce: string; // base64
  verifier_ct: string; // base64
  account_public_key?: string | null; // base64
  enc_account_private_key?: string | null; // base64
}

export interface VaultAccountKeyBody {
  account_public_key: string; // base64
  enc_account_private_key: string; // base64
}

export interface VaultItem {
  id: string;
  kind: VaultItemKind;
  nonce: string; // base64
  ciphertext: string; // base64 — for file items this is the encrypted metadata
  folder_id?: string | null;
  // File items only:
  wrapped_key?: string | null; // base64 — per-file key sealed to the owner
  size_bytes?: number | null; // ciphertext byte size
  has_file?: boolean; // true → fetch /vault/files/{id} to get the bytes
  created_at: string;
  updated_at: string;
}

export interface VaultItemCreate {
  kind: Exclude<VaultItemKind, "file">;
  nonce: string; // base64
  ciphertext: string; // base64
  folder_id?: string | null;
}

export interface VaultItemUpdate {
  nonce: string; // base64
  ciphertext: string; // base64
}

// ---- folders ----

export interface VaultFolder {
  id: string;
  parent_id: string | null;
  name_nonce: string; // base64
  name_ct: string; // base64
  created_at: string;
  updated_at: string;
}

export interface VaultFolderCreate {
  parent_id?: string | null;
  name_nonce: string; // base64
  name_ct: string; // base64
}

export interface VaultFolderUpdate {
  parent_id?: string | null;
  name_nonce: string; // base64
  name_ct: string; // base64
}

// GET /vault/meta returns 404 when the user has no vault yet. We translate
// that single case to `null` so callers can branch setup-vs-unlock cleanly;
// every other error propagates.
export async function getVaultMeta(): Promise<VaultMeta | null> {
  try {
    return await api.get<VaultMeta>("/vault/meta");
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export async function setupVault(body: VaultSetupBody): Promise<VaultMeta> {
  return api.post<VaultMeta>("/vault/setup", body);
}

// Provision the account keypair for a vault that predates VLT-8 (or whose
// keypair wasn't set at setup). 409 if one already exists.
export async function setAccountKey(
  body: VaultAccountKeyBody,
): Promise<VaultMeta> {
  return api.post<VaultMeta>("/vault/account-key", body);
}

export async function listVaultItems(): Promise<VaultItem[]> {
  return api.get<VaultItem[]>("/vault/items");
}

export async function createVaultItem(
  body: VaultItemCreate,
): Promise<VaultItem> {
  return api.post<VaultItem>("/vault/items", body);
}

export async function updateVaultItem(
  id: string,
  body: VaultItemUpdate,
): Promise<VaultItem> {
  return api.put<VaultItem>(`/vault/items/${id}`, body);
}

export async function deleteVaultItem(id: string): Promise<void> {
  await api.delete(`/vault/items/${id}`);
}

// Wipe the entire vault (meta + all items). Irreversible — used by the
// "reset vault" / forgot-master-password path.
export async function wipeVault(): Promise<void> {
  await api.delete("/vault");
}

// ---- folders (VLT-8) ----

export async function listVaultFolders(): Promise<VaultFolder[]> {
  return api.get<VaultFolder[]>("/vault/folders");
}

export async function createVaultFolder(
  body: VaultFolderCreate,
): Promise<VaultFolder> {
  return api.post<VaultFolder>("/vault/folders", body);
}

export async function updateVaultFolder(
  id: string,
  body: VaultFolderUpdate,
): Promise<VaultFolder> {
  return api.put<VaultFolder>(`/vault/folders/${id}`, body);
}

export async function deleteVaultFolder(id: string): Promise<void> {
  await api.delete(`/vault/folders/${id}`);
}

// ---- files (VLT-8) ----

export interface VaultFileUpload {
  nonce: string; // base64 — metadata nonce
  ciphertext: string; // base64 — encrypted metadata JSON
  wrapped_key: string; // base64 — per-file key sealed to the owner
  folderId?: string | null;
  blob: Blob; // the ciphertext (chunked AES-GCM)
}

// Upload an E2E-encrypted file. The Blob is the ciphertext; the server
// streams it to MinIO and never sees the key or plaintext.
export async function uploadVaultFile(
  upload: VaultFileUpload,
): Promise<VaultItem> {
  const fd = new FormData();
  fd.append("nonce", upload.nonce);
  fd.append("ciphertext", upload.ciphertext);
  fd.append("wrapped_key", upload.wrapped_key);
  if (upload.folderId) fd.append("folder_id", upload.folderId);
  fd.append("blob", upload.blob, "blob");
  return api.post<VaultItem>("/vault/files", fd);
}

// Fetch a file item's ciphertext as a Blob (whole object). The caller
// decrypts it with the per-file key it unsealed from `wrapped_key`.
export async function downloadVaultFile(id: string): Promise<Blob> {
  return api.get<Blob>(`/vault/files/${id}`, "blob");
}

// Direct URL to a file's ciphertext (for Range fetches / media elements in
// P3). Carries the session cookie via credentials on fetch.
export function vaultFileUrl(id: string): string {
  return `${API_BASE_URL}/vault/files/${id}`;
}

// ---- sharing (VLT-8 P5) ----

export interface VaultRecipientKey {
  email: string;
  display_name?: string | null;
  account_public_key: string; // base64
}

export interface VaultShareRecipient {
  id: string; // grant id
  recipient_email: string;
  recipient_display_name?: string | null;
  created_at: string;
}

export interface VaultIncomingShare {
  id: string; // grant id
  kind: VaultItemKind;
  size_bytes?: number | null;
  has_file: boolean;
  sealed_payload: string; // base64 — unseal with the account private key
  owner_email?: string | null;
  owner_display_name?: string | null;
  created_at: string;
}

// Look up a recipient's account public key by email. Throws ApiError 404 when
// the address has no neuthek vault.
export async function getRecipientKey(email: string): Promise<VaultRecipientKey> {
  return api.get<VaultRecipientKey>(
    `/vault/recipient-key?email=${encodeURIComponent(email)}`,
  );
}

export async function shareVaultItem(
  itemId: string,
  body: { recipient_email: string; sealed_payload: string },
): Promise<VaultShareRecipient> {
  return api.post<VaultShareRecipient>(`/vault/items/${itemId}/share`, body);
}

export async function listItemShares(itemId: string): Promise<VaultShareRecipient[]> {
  return api.get<VaultShareRecipient[]>(`/vault/items/${itemId}/shares`);
}

export async function listIncomingShares(): Promise<VaultIncomingShare[]> {
  return api.get<VaultIncomingShare[]>("/vault/shares");
}

export async function deleteShare(grantId: string): Promise<void> {
  await api.delete(`/vault/shares/${grantId}`);
}

// Fetch a shared file's ciphertext (recipient). Decrypt with the per-file key
// recovered from the unsealed bundle.
export async function downloadSharedFile(grantId: string): Promise<Blob> {
  return api.get<Blob>(`/vault/shares/${grantId}/file`, "blob");
}

// ---- public links (VLT-8 P7) ----

export interface VaultPublicLink {
  token: string;
  password_required: boolean;
  expires_at?: string | null;
  created_at: string;
}

export interface VaultPublicLinkCreate {
  sealed_payload: string; // base64 — bundle sealed under the link key
  password_required: boolean;
  kdf_salt?: string | null; // base64, iff password_required
  kdf_iterations?: number | null; // iff password_required
  expires_in_days?: number | null;
}

// Unauthenticated viewer payload (the public /v/{token} page consumes this).
export interface VaultPublicLinkView {
  kind: VaultItemKind;
  has_file: boolean;
  size_bytes?: number | null;
  sealed_payload: string; // base64
  password_required: boolean;
  kdf_salt?: string | null; // base64
  kdf_iterations?: number | null;
}

export async function createPublicLink(
  itemId: string,
  body: VaultPublicLinkCreate,
): Promise<VaultPublicLink> {
  return api.post<VaultPublicLink>(`/vault/items/${itemId}/public-link`, body);
}

// Current link for an item, or null when none exists (404).
export async function getPublicLink(itemId: string): Promise<VaultPublicLink | null> {
  try {
    return await api.get<VaultPublicLink>(`/vault/items/${itemId}/public-link`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export async function deletePublicLink(itemId: string): Promise<void> {
  await api.delete(`/vault/items/${itemId}/public-link`);
}

// Unauthenticated: fetch a public link's sealed bundle + KDF params by token.
export async function viewPublicLink(token: string): Promise<VaultPublicLinkView> {
  return api.get<VaultPublicLinkView>(`/vault/public/${encodeURIComponent(token)}`);
}

// Unauthenticated: fetch a public file link's ciphertext.
export async function downloadPublicFile(token: string): Promise<Blob> {
  return api.get<Blob>(`/vault/public/${encodeURIComponent(token)}/file`, "blob");
}
