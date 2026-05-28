// In-memory vault session (VLT-5).
//
// Holds the derived AES-GCM CryptoKey for the duration of an UNLOCKED
// session only. The key is:
//   - never written to localStorage / sessionStorage / IndexedDB / cookies
//   - a non-extractable CryptoKey (its raw bytes are unreadable from JS)
//   - dropped on explicit lock, on sign-out, and after an idle timeout
//
// Because it lives only in a module-scoped variable, a full page reload also
// clears it (the user must re-enter the master password). A tiny pub/sub lets
// React components re-render the moment the vault locks or unlocks.

import { useSyncExternalStore } from "react";

let _key: CryptoKey | null = null;
// VLT-8 — the unwrapped account private key (non-extractable ECDH key) and
// the matching public key (base64). Held only while unlocked, alongside the
// master key, so the vault can unseal per-file keys and seal new ones for
// sharing. Cleared on lock exactly like the master key.
let _accountPrivateKey: CryptoKey | null = null;
let _accountPublicKey: string | null = null;
let _idleTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

// Auto-lock after this much inactivity. Re-armed on unlock and whenever
// `touchVault()` is called from meaningful activity in the vault UI.
export const IDLE_LOCK_MS = 5 * 60 * 1000;

function emit(): void {
  for (const l of listeners) l();
}

function armIdleTimer(): void {
  if (_idleTimer) clearTimeout(_idleTimer);
  _idleTimer = setTimeout(lockVault, IDLE_LOCK_MS);
}

export function unlockVault(key: CryptoKey): void {
  _key = key;
  armIdleTimer();
  emit();
}

// Attach the account keypair to the live session. Called right after
// unlockVault once the private key has been unwrapped with the master key.
// Separate from unlockVault so legacy callers (and the setup path before the
// keypair exists) keep working unchanged.
export function setAccountKeys(
  privateKey: CryptoKey,
  publicKey: string,
): void {
  _accountPrivateKey = privateKey;
  _accountPublicKey = publicKey;
}

export function lockVault(): void {
  if (_idleTimer) {
    clearTimeout(_idleTimer);
    _idleTimer = null;
  }
  if (_key === null) return; // already locked — don't spam listeners
  _key = null;
  _accountPrivateKey = null;
  _accountPublicKey = null;
  emit();
}

export function getVaultKey(): CryptoKey | null {
  return _key;
}

// The unwrapped account private key (for unsealing per-file keys). Null when
// locked or when the vault predates the keypair and hasn't provisioned one.
export function getAccountPrivateKey(): CryptoKey | null {
  return _accountPrivateKey;
}

// The account public key (base64), for sealing per-file keys to oneself on
// upload and to recipients when sharing.
export function getAccountPublicKey(): string | null {
  return _accountPublicKey;
}

export function isVaultUnlocked(): boolean {
  return _key !== null;
}

// Reset the idle timer. Call on meaningful user activity inside the vault
// (revealing a password, editing a note) so an actively-used vault doesn't
// lock out from under the user.
export function touchVault(): void {
  if (_key) armIdleTimer();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// React hook: re-renders the caller whenever the vault locks/unlocks.
export function useVaultUnlocked(): boolean {
  return useSyncExternalStore(
    subscribe,
    isVaultUnlocked,
    () => false, // server snapshot (SSR safety; this app is client-only)
  );
}
