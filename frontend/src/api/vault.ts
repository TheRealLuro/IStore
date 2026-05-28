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
