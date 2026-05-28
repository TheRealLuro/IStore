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

// ---- account keypair (VLT-8: sealed-box equivalent for sharing) ----
//
// To SHARE a vault item, its per-file key is "sealed" to the recipient's
// account public key — only they can open it. WebCrypto has no X25519
// crypto_box_seal, so we build the equivalent on P-256 ECDH + HKDF +
// AES-GCM (no WASM dependency):
//   seal:  fresh ephemeral P-256 keypair → ECDH(ephemeral_priv,
//          recipient_pub) → HKDF-SHA256 → AES-GCM key → encrypt.
//          Output = ephemeralPubLen ‖ ephemeralPub ‖ nonce ‖ ciphertext.
//   open:  ECDH(my_priv, ephemeral_pub) → same HKDF → AES-GCM decrypt.
// The account private key never leaves the device unwrapped — it's stored
// AES-GCM-wrapped under the master key.

const ACCOUNT_CURVE = "P-256";
const SEAL_INFO = "neuthek.vault.seal.v1";
const ACCOUNT_KEY_AAD = "neuthek.vault.account-key.v1";

export interface AccountKeyPairExport {
  publicKey: string; // base64 raw (uncompressed EC point)
  enc_private_key: string; // base64 of nonce(12) ‖ AES-GCM(masterKey, pkcs8)
}

/** Generate a fresh account keypair: returns the public key (raw) and the
 *  PKCS8 private key wrapped under the master key, ready to persist on
 *  vault_meta. */
export async function createAccountKeyPair(
  masterKey: CryptoKey,
): Promise<AccountKeyPairExport> {
  const s = subtle();
  const kp = await s.generateKey(
    { name: "ECDH", namedCurve: ACCOUNT_CURVE },
    true,
    ["deriveBits"],
  );
  const pubRaw = new Uint8Array(await s.exportKey("raw", kp.publicKey));
  const pkcs8 = new Uint8Array(await s.exportKey("pkcs8", kp.privateKey));
  const nonce = randomBytes(NONCE_BYTES);
  const ct = new Uint8Array(
    await s.encrypt(
      { name: "AES-GCM", iv: nonce as BufferSource, additionalData: enc.encode(ACCOUNT_KEY_AAD) },
      masterKey,
      pkcs8 as BufferSource,
    ),
  );
  const blob = new Uint8Array(nonce.length + ct.length);
  blob.set(nonce, 0);
  blob.set(ct, nonce.length);
  return { publicKey: bytesToB64(pubRaw), enc_private_key: bytesToB64(blob) };
}

/** Unwrap the stored private key with the master key → an ECDH CryptoKey. */
export async function unwrapAccountPrivateKey(
  encPrivateKeyB64: string,
  masterKey: CryptoKey,
): Promise<CryptoKey> {
  const s = subtle();
  const blob = b64ToBytes(encPrivateKeyB64);
  const nonce = blob.subarray(0, NONCE_BYTES);
  const ct = blob.subarray(NONCE_BYTES);
  const pkcs8 = await s.decrypt(
    { name: "AES-GCM", iv: nonce as BufferSource, additionalData: enc.encode(ACCOUNT_KEY_AAD) },
    masterKey,
    ct as BufferSource,
  );
  return s.importKey(
    "pkcs8", pkcs8, { name: "ECDH", namedCurve: ACCOUNT_CURVE }, false, ["deriveBits"],
  );
}

export async function importAccountPublicKey(publicKeyB64: string): Promise<CryptoKey> {
  return subtle().importKey(
    "raw", b64ToBytes(publicKeyB64) as BufferSource,
    { name: "ECDH", namedCurve: ACCOUNT_CURVE }, false, [],
  );
}

async function deriveSealKey(
  privateKey: CryptoKey,
  publicKey: CryptoKey,
): Promise<CryptoKey> {
  const s = subtle();
  const shared = await s.deriveBits({ name: "ECDH", public: publicKey }, privateKey, 256);
  const hkdfBase = await s.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
  return s.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(0) as BufferSource, info: enc.encode(SEAL_INFO) },
    hkdfBase,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

/** Seal bytes (e.g. a per-file key) to a recipient's account public key.
 *  Output: base64 of ephLen(1) ‖ ephemeralPub ‖ nonce(12) ‖ ciphertext. */
export async function sealToPublicKey(
  recipientPublicKeyB64: string,
  data: Uint8Array,
): Promise<string> {
  const s = subtle();
  const recipientPub = await importAccountPublicKey(recipientPublicKeyB64);
  const eph = await s.generateKey(
    { name: "ECDH", namedCurve: ACCOUNT_CURVE }, true, ["deriveBits"],
  );
  const ephPubRaw = new Uint8Array(await s.exportKey("raw", eph.publicKey));
  const key = await deriveSealKey(eph.privateKey, recipientPub);
  const nonce = randomBytes(NONCE_BYTES);
  const ct = new Uint8Array(
    await s.encrypt({ name: "AES-GCM", iv: nonce as BufferSource }, key, data as BufferSource),
  );
  const out = new Uint8Array(1 + ephPubRaw.length + nonce.length + ct.length);
  out[0] = ephPubRaw.length;
  out.set(ephPubRaw, 1);
  out.set(nonce, 1 + ephPubRaw.length);
  out.set(ct, 1 + ephPubRaw.length + nonce.length);
  return bytesToB64(out);
}

/** Open a sealed blob with my account private key (inverse of
 *  sealToPublicKey). Named distinctly from the internal AES-GCM `openSealed`
 *  helper above to avoid a duplicate-declaration collision. */
export async function unsealFromPrivateKey(
  myPrivateKey: CryptoKey,
  sealedB64: string,
): Promise<Uint8Array> {
  const s = subtle();
  const blob = b64ToBytes(sealedB64);
  const ephLen = blob[0];
  const ephPubRaw = blob.subarray(1, 1 + ephLen);
  const nonce = blob.subarray(1 + ephLen, 1 + ephLen + NONCE_BYTES);
  const ct = blob.subarray(1 + ephLen + NONCE_BYTES);
  const ephPub = await s.importKey(
    "raw", ephPubRaw as BufferSource,
    { name: "ECDH", namedCurve: ACCOUNT_CURVE }, false, [],
  );
  const key = await deriveSealKey(myPrivateKey, ephPub);
  return new Uint8Array(
    await s.decrypt({ name: "AES-GCM", iv: nonce as BufferSource }, key, ct as BufferSource),
  );
}

// ---- file chunk crypto (VLT-8: vault-as-drive) ----
//
// A vault FILE is encrypted under a random per-file AES-256 key, in fixed
// 1 MiB plaintext chunks, each its own AES-GCM ciphertext. Chunking lets us:
//   - encrypt arbitrarily large files (videos, archives) without holding the
//     whole plaintext (or ciphertext) in memory, and
//   - decrypt an arbitrary byte RANGE on download (P3) by fetching only the
//     chunks that cover it — the ciphertext layout is deterministic from the
//     metadata, so chunk boundaries are computable without a server index.
// Each chunk's nonce = 4-byte per-file random prefix ‖ 8-byte big-endian
// chunk index (unique per (key, chunk) — and the key is unique per file).
// Each chunk's AAD binds the chunk INDEX, so a tampered server can't reorder
// chunks; the decoder also re-checks the chunk count + total size to detect
// truncation. The per-file key is itself sealed to the owner's account
// public key (`sealToPublicKey`) and stored as the item's wrapped_key.

export const FILE_CHUNK_SIZE = 1024 * 1024; // 1 MiB plaintext per chunk
const FILE_AAD = "neuthek.vault.file.v1";
const FILE_KEY_BYTES = 32;
const FILE_NONCE_PREFIX_BYTES = 4;
const GCM_TAG_BYTES = 16;

export interface FileCryptoMeta {
  v: 1;
  size: number; // plaintext byte length
  chunkSize: number; // plaintext bytes per chunk
  chunks: number; // chunk count
  noncePrefix: string; // base64 of the 4-byte per-file random prefix
}

function fileChunkNonce(prefix: Uint8Array, index: number): Uint8Array {
  const nonce = new Uint8Array(NONCE_BYTES);
  nonce.set(prefix, 0);
  const dv = new DataView(nonce.buffer, nonce.byteOffset, nonce.byteLength);
  // 64-bit big-endian chunk index across bytes 4..11.
  dv.setUint32(FILE_NONCE_PREFIX_BYTES, Math.floor(index / 2 ** 32));
  dv.setUint32(FILE_NONCE_PREFIX_BYTES + 4, index >>> 0);
  return nonce;
}

function fileChunkAad(index: number): Uint8Array {
  const base = enc.encode(FILE_AAD);
  const out = new Uint8Array(base.length + 8);
  out.set(base, 0);
  const dv = new DataView(out.buffer, out.byteOffset, out.byteLength);
  dv.setUint32(base.length, Math.floor(index / 2 ** 32));
  dv.setUint32(base.length + 4, index >>> 0);
  return out;
}

/** Fresh random per-file key (raw bytes). Sealed to the owner with
 *  sealToPublicKey for storage; imported per-operation for AES-GCM. */
export function randomFileKey(): Uint8Array {
  return randomBytes(FILE_KEY_BYTES);
}

async function importFileKey(
  raw: Uint8Array,
  usages: KeyUsage[],
): Promise<CryptoKey> {
  return subtle().importKey("raw", raw as BufferSource, "AES-GCM", false, usages);
}

/** Encrypt a File/Blob into one ciphertext Blob (concatenated AES-GCM
 *  chunks) plus the metadata needed to decrypt it. Streams chunk-by-chunk;
 *  never materializes the whole plaintext at once. */
export async function encryptFile(
  file: Blob,
  fileKeyRaw: Uint8Array,
): Promise<{ blob: Blob; meta: FileCryptoMeta }> {
  const s = subtle();
  const key = await importFileKey(fileKeyRaw, ["encrypt"]);
  const prefix = randomBytes(FILE_NONCE_PREFIX_BYTES);
  const size = file.size;
  const chunkSize = FILE_CHUNK_SIZE;
  const chunks = size === 0 ? 0 : Math.ceil(size / chunkSize);
  const parts: BlobPart[] = [];
  for (let i = 0; i < chunks; i++) {
    const start = i * chunkSize;
    const end = Math.min(start + chunkSize, size);
    const pt = new Uint8Array(await file.slice(start, end).arrayBuffer());
    const ct = await s.encrypt(
      {
        name: "AES-GCM",
        iv: fileChunkNonce(prefix, i) as BufferSource,
        additionalData: fileChunkAad(i) as BufferSource,
      },
      key,
      pt as BufferSource,
    );
    parts.push(ct);
  }
  return {
    blob: new Blob(parts, { type: "application/octet-stream" }),
    meta: { v: 1, size, chunkSize, chunks, noncePrefix: bytesToB64(prefix) },
  };
}

/** Decrypt a full ciphertext blob back into the original bytes. Verifies the
 *  recovered length equals the declared size (truncation detection); each
 *  chunk's AES-GCM tag + AAD detect any tampering/reordering. */
export async function decryptFile(
  ciphertext: ArrayBuffer | Uint8Array,
  fileKeyRaw: Uint8Array,
  meta: FileCryptoMeta,
): Promise<Uint8Array> {
  const s = subtle();
  const key = await importFileKey(fileKeyRaw, ["decrypt"]);
  const prefix = b64ToBytes(meta.noncePrefix);
  const ct =
    ciphertext instanceof Uint8Array ? ciphertext : new Uint8Array(ciphertext);
  const out = new Uint8Array(meta.size);
  let inPos = 0;
  let outPos = 0;
  for (let i = 0; i < meta.chunks; i++) {
    const ptLen = Math.min(meta.chunkSize, meta.size - outPos);
    const ctLen = ptLen + GCM_TAG_BYTES;
    const slice = ct.subarray(inPos, inPos + ctLen);
    const pt = new Uint8Array(
      await s.decrypt(
        {
          name: "AES-GCM",
          iv: fileChunkNonce(prefix, i) as BufferSource,
          additionalData: fileChunkAad(i) as BufferSource,
        },
        key,
        slice as BufferSource,
      ),
    );
    out.set(pt, outPos);
    inPos += ctLen;
    outPos += pt.length;
  }
  if (outPos !== meta.size) {
    throw new Error("Decrypted size does not match metadata (truncated?).");
  }
  return out;
}

/** Byte offset (in the ciphertext blob) where chunk `index` begins. Each
 *  encrypted chunk is plaintextChunkSize + 16 (GCM tag) bytes, so the
 *  position is a pure function of the metadata — used by P3 Range download. */
export function fileChunkCiphertextOffset(meta: FileCryptoMeta, index: number): number {
  return index * (meta.chunkSize + GCM_TAG_BYTES);
}

// ---- public links (VLT-8 P7: anyone-with-the-link sharing) ----
//
// A public link is zero-knowledge: the AES key is derived from a random secret
// carried in the URL FRAGMENT (never sent to the server). If the owner sets a
// password, the key is derived from BOTH the fragment secret AND the password
// (PBKDF2 → mixed into HKDF), so the sealed blob can't be opened with the link
// alone. The server stores only the sealed blob + (for password links) a
// public salt + iteration count.

export const PUBLINK_KDF_ITERATIONS = 600_000;
export const PUBLINK_SALT_BYTES = 16;
const PUBLINK_INFO = "neuthek.vault.publiclink.v1";

/** Fresh 32-byte link secret (goes in the URL fragment, base64url). */
export function randomLinkSecret(): Uint8Array {
  return randomBytes(32);
}

// base64url (URL-fragment friendly: no +, /, or = padding).
export function bytesToB64Url(bytes: Uint8Array): string {
  return bytesToB64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
export function b64UrlToBytes(s: string): Uint8Array {
  const norm = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = norm.length % 4 === 0 ? "" : "=".repeat(4 - (norm.length % 4));
  return b64ToBytes(norm + pad);
}

/** Derive the public-link AES-GCM key from the fragment secret and, when the
 *  link is password-protected, the password (PBKDF2 → HKDF combine). */
export async function derivePublicLinkKey(
  linkSecret: Uint8Array,
  password: string | null,
  salt: Uint8Array | null,
  iterations: number | null,
): Promise<CryptoKey> {
  const s = subtle();
  let ikm: Uint8Array;
  if (password && salt && iterations) {
    const baseKey = await s.importKey(
      "raw", enc.encode(password), { name: "PBKDF2" }, false, ["deriveBits"],
    );
    const pwBits = new Uint8Array(
      await s.deriveBits(
        { name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" },
        baseKey, 256,
      ),
    );
    ikm = new Uint8Array(linkSecret.length + pwBits.length);
    ikm.set(linkSecret, 0);
    ikm.set(pwBits, linkSecret.length);
  } else {
    ikm = linkSecret;
  }
  const hkdfBase = await s.importKey("raw", ikm as BufferSource, "HKDF", false, ["deriveKey"]);
  return s.deriveKey(
    {
      name: "HKDF", hash: "SHA-256",
      salt: new Uint8Array(0) as BufferSource, info: enc.encode(PUBLINK_INFO),
    },
    hkdfBase,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

/** Seal a bundle under the link key. Output: base64 of nonce(12) ‖ ct. */
export async function sealPublicLink(linkKey: CryptoKey, obj: unknown): Promise<string> {
  const s = subtle();
  const nonce = randomBytes(NONCE_BYTES);
  const ct = new Uint8Array(
    await s.encrypt(
      { name: "AES-GCM", iv: nonce as BufferSource },
      linkKey,
      enc.encode(JSON.stringify(obj)) as BufferSource,
    ),
  );
  const out = new Uint8Array(nonce.length + ct.length);
  out.set(nonce, 0);
  out.set(ct, nonce.length);
  return bytesToB64(out);
}

/** Inverse of sealPublicLink. Throws on wrong key / wrong password / tamper. */
export async function openPublicLink<T = unknown>(
  linkKey: CryptoKey,
  sealedB64: string,
): Promise<T> {
  const s = subtle();
  const blob = b64ToBytes(sealedB64);
  const nonce = blob.subarray(0, NONCE_BYTES);
  const ct = blob.subarray(NONCE_BYTES);
  const pt = await s.decrypt(
    { name: "AES-GCM", iv: nonce as BufferSource }, linkKey, ct as BufferSource,
  );
  return JSON.parse(dec.decode(new Uint8Array(pt))) as T;
}
