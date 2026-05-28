// Zero-knowledge vault crypto (VLT-5).
//
// Every byte of key derivation and AES-GCM here runs in the browser via
// WebCrypto (`crypto.subtle`). The master password and the derived vault
// key NEVER leave the device and are NEVER sent to the server. The server
// only ever stores opaque (nonce, ciphertext) blobs plus the *public* KDF
// parameters. Even a full database + backup compromise yields no plaintext.
//
// Crypto contract — must stay in lock-step with backend/api/vault.py and
// migration 0044_vault.py:
//
//   KDF        PBKDF2-SHA256 over a per-user random salt.
//   key        256-bit AES-GCM, a NON-EXTRACTABLE CryptoKey (its raw bytes
//              are unreadable from JS, so even an XSS can't exfiltrate it).
//   nonce      96-bit (12-byte) random value, fresh for every encryption.
//   ciphertext AES-256-GCM output — includes the 128-bit auth tag.
//   verifier   a fixed constant encrypted under the vault key. On unlock the
//              client decrypts it; a wrong password makes AES-GCM
//              authentication FAIL, which is the "wrong password" signal.
//              The server never learns the password or the key.
//
// The plaintext of a vault item is a JSON blob (incl. its title), so even
// titles are encrypted; search happens client-side after unlock.

export const KDF_NAME = "PBKDF2-SHA256";

// OWASP 2023 floor for PBKDF2-HMAC-SHA256 is 600,000 iterations. The backend
// rejects anything below 310,000 or above 5,000,000; we sit at 600,000 —
// strong, and roughly 0.3–0.6 s in a modern browser on unlock.
export const KDF_ITERATIONS = 600_000;

// 128-bit salt. The backend accepts 16–64 bytes; the salt is public by
// design (it only ensures two users with the same master password derive
// different keys), so 16 bytes is plenty.
export const SALT_BYTES = 16;

// 96-bit GCM nonce. The backend requires EXACTLY 12 bytes.
export const NONCE_BYTES = 12;

// Domain-separation tags, bound into each ciphertext as AES-GCM "additional
// authenticated data". A verifier blob can't be swapped for an item blob
// (or vice-versa) by anyone tampering with the database — the auth tag won't
// validate against the wrong context. These strings are STABLE: changing one
// would orphan every ciphertext sealed under it.
const AAD_VERIFIER = "neuthek.vault.verifier.v1";
const AAD_ITEM = "neuthek.vault.item.v1";

// The constant the verifier encrypts. Its exact bytes don't matter — only
// that the client knows them and can confirm a successful, *authenticated*
// decryption. Versioned so the scheme can rotate later.
const VERIFIER_PLAINTEXT = "neuthek-vault-verifier-v1";

const enc = new TextEncoder();
const dec = new TextDecoder();

function subtle(): SubtleCrypto {
  const c = globalThis.crypto;
  if (!c || !c.subtle) {
    throw new Error(
      "WebCrypto is unavailable. The vault requires a secure context " +
        "(HTTPS, or localhost during development).",
    );
  }
  return c.subtle;
}

// ---- base64 (standard alphabet, matches the backend's strict gate) -------

export function bytesToB64(bytes: Uint8Array): string {
  let bin = "";
  // Chunk to avoid blowing the argument limit on String.fromCharCode for
  // large ciphertexts (notes can be tens of KB).
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(
      null,
      bytes.subarray(i, i + CHUNK) as unknown as number[],
    );
  }
  return btoa(bin);
}

export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function randomBytes(n: number): Uint8Array {
  const b = new Uint8Array(n);
  globalThis.crypto.getRandomValues(b);
  return b;
}

// ---- key derivation ------------------------------------------------------

// Derive the AES-GCM vault key from the master password + public salt.
// The returned CryptoKey is NON-EXTRACTABLE: its raw bytes can never be
// read back out of memory by JavaScript.
export async function deriveVaultKey(
  masterPassword: string,
  salt: Uint8Array,
  iterations: number,
): Promise<CryptoKey> {
  const s = subtle();
  const baseKey = await s.importKey(
    "raw",
    enc.encode(masterPassword),
    { name: "PBKDF2" },
    false,
    ["deriveKey"],
  );
  return s.deriveKey(
    { name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" },
    baseKey,
    { name: "AES-GCM", length: 256 },
    false, // extractable: false — cannot be exported
    ["encrypt", "decrypt"],
  );
}

// ---- generic seal / open -------------------------------------------------

interface Sealed {
  nonce: Uint8Array;
  ciphertext: Uint8Array;
}

async function seal(
  key: CryptoKey,
  plaintext: Uint8Array,
  aad: string,
): Promise<Sealed> {
  const s = subtle();
  const nonce = randomBytes(NONCE_BYTES);
  const ct = await s.encrypt(
    { name: "AES-GCM", iv: nonce as BufferSource, additionalData: enc.encode(aad) },
    key,
    plaintext as BufferSource,
  );
  return { nonce, ciphertext: new Uint8Array(ct) };
}

async function openSealed(
  key: CryptoKey,
  nonce: Uint8Array,
  ciphertext: Uint8Array,
  aad: string,
): Promise<Uint8Array> {
  const s = subtle();
  const pt = await s.decrypt(
    { name: "AES-GCM", iv: nonce as BufferSource, additionalData: enc.encode(aad) },
    key,
    ciphertext as BufferSource,
  );
  return new Uint8Array(pt);
}

// ---- verifier ------------------------------------------------------------

export interface VerifierBlob {
  verifier_nonce: string; // base64
  verifier_ct: string; // base64
}

export async function makeVerifier(key: CryptoKey): Promise<VerifierBlob> {
  const { nonce, ciphertext } = await seal(
    key,
    enc.encode(VERIFIER_PLAINTEXT),
    AAD_VERIFIER,
  );
  return {
    verifier_nonce: bytesToB64(nonce),
    verifier_ct: bytesToB64(ciphertext),
  };
}

// True iff `key` correctly decrypts the verifier (the master password was
// right). Any authentication failure → false (wrong password / tampered blob).
export async function checkVerifier(
  key: CryptoKey,
  blob: VerifierBlob,
): Promise<boolean> {
  try {
    const pt = await openSealed(
      key,
      b64ToBytes(blob.verifier_nonce),
      b64ToBytes(blob.verifier_ct),
      AAD_VERIFIER,
    );
    return dec.decode(pt) === VERIFIER_PLAINTEXT;
  } catch {
    return false;
  }
}

// ---- items ---------------------------------------------------------------

export interface SealedItem {
  nonce: string; // base64
  ciphertext: string; // base64
}

export async function encryptItem(
  key: CryptoKey,
  plaintext: unknown,
): Promise<SealedItem> {
  const json = enc.encode(JSON.stringify(plaintext));
  const { nonce, ciphertext } = await seal(key, json, AAD_ITEM);
  return { nonce: bytesToB64(nonce), ciphertext: bytesToB64(ciphertext) };
}

export async function decryptItem<T = unknown>(
  key: CryptoKey,
  sealed: SealedItem,
): Promise<T> {
  const pt = await openSealed(
    key,
    b64ToBytes(sealed.nonce),
    b64ToBytes(sealed.ciphertext),
    AAD_ITEM,
  );
  return JSON.parse(dec.decode(pt)) as T;
}

// ---- high-level setup ----------------------------------------------------

export interface SetupPayload {
  kdf: string;
  kdf_iterations: number;
  kdf_salt: string; // base64
  verifier_nonce: string; // base64
  verifier_ct: string; // base64
}

// First-time setup: generate a fresh salt, derive the key, build the verifier,
// and return both the live key (to unlock the session immediately) and the
// meta payload to POST to /vault/setup.
export async function createVaultSetup(
  masterPassword: string,
): Promise<{ key: CryptoKey; payload: SetupPayload }> {
  const salt = randomBytes(SALT_BYTES);
  const key = await deriveVaultKey(masterPassword, salt, KDF_ITERATIONS);
  const verifier = await makeVerifier(key);
  return {
    key,
    payload: {
      kdf: KDF_NAME,
      kdf_iterations: KDF_ITERATIONS,
      kdf_salt: bytesToB64(salt),
      verifier_nonce: verifier.verifier_nonce,
      verifier_ct: verifier.verifier_ct,
    },
  };
}
