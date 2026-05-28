// Zero-knowledge vault API client (VLT-5). Mirrors backend/api/vault.py.
//
// Everything that crosses this boundary is already CIPHERTEXT — the server
// stores and returns opaque base64 (nonce, ciphertext) blobs plus the public
// KDF parameters. The plaintext is encrypted/decrypted client-side in
// `@/vault/crypto`; this module never touches a key or a plaintext.

import { api, ApiError } from "./client";

export type VaultItemKind = "password" | "note";

export interface VaultMeta {
  kdf: string;
  kdf_iterations: number;
  kdf_salt: string; // base64
  verifier_nonce: string; // base64
  verifier_ct: string; // base64
  created_at?: string;
  updated_at?: string;
}

export interface VaultSetupBody {
  kdf: string;
  kdf_iterations: number;
  kdf_salt: string; // base64
  verifier_nonce: string; // base64
  verifier_ct: string; // base64
}

export interface VaultItem {
  id: string;
  kind: VaultItemKind;
  nonce: string; // base64
  ciphertext: string; // base64
  created_at: string;
  updated_at: string;
}

export interface VaultItemCreate {
  kind: VaultItemKind;
  nonce: string; // base64
  ciphertext: string; // base64
}

export interface VaultItemUpdate {
  nonce: string; // base64
  ciphertext: string; // base64
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
