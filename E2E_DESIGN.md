# neuthek end-to-end encryption — architecture & build plan (VLT-8)

Status: **design, pre-build.**

> ## ⚑ REVISION 2 — FINAL MODEL (2026-05-28, supersedes §4–§12 below)
>
> Product owner clarified the model. It is now **simpler** than the
> per-folder design that follows. Build to THIS section; the sections
> below are kept for crypto reference (key hierarchy in §3 still holds)
> but the per-folder-AI / server-key / `ai_mode` / onboarding-choice
> machinery is **dropped**.
>
> ### The model: E2E is a destination, not a toggle
> - **Vault** = a full, drive-like, **end-to-end encrypted** store. Nested
>   folders + any file type (PDF, image, video, docs) + specialized secure
>   items (passwords, secure notes, crypto seed phrases, cards/IDs). The
>   server only ever holds ciphertext for vault content — it **never**
>   reads it and **no AI ever runs on it**. You upload *into* the Vault to
>   get E2E.
> - **Drive** = the normal library, **unchanged from today**: server-
>   readable, AI features available **with the user's existing per-feature
>   consent** (search / summaries / faces). No re-architecture here.
> - There is **no per-folder E2E toggle** and **no onboarding auto/manual
>   screen**. Where a file lives decides everything.
>
> ### What this removes vs. the design below
> - ❌ server keypair / `wrapped_fk_server` (server never reads vault; Drive
>   is already plaintext-readable, so nothing to wrap).
> - ❌ `users.ai_mode`, `folders.ai_enabled`, per-folder enable/disable
>   re-wrap + AI-artifact purge.
> - ❌ the one-screen onboarding AI choice.
> - ❌ encrypting the existing Drive / a "migrate Drive to E2E" tool.
>
> ### What stays / is added
> - ✅ Key hierarchy from §3: master password → MK → **X25519 account
>   keypair** (public stored; private MK-wrapped). Reuse the **existing
>   vault master password** as the account master password; store the
>   keypair on `vault_meta`.
> - ✅ Per-file AES-256-GCM key; for vault content the key is wrapped to
>   the **owner** only (+ to **recipients** when shared — see sharing).
> - ✅ **Vault is now a file store**: large file ciphertext goes to object
>   storage (MinIO), not an inline `bytea`. Folders nest; folder names are
>   encrypted client-side.
> - ✅ **Sharing IS in scope** (owner chose key-wrapping): grant one vault
>   item to a specific neuthek user by sealing its file-key to their
>   account public key, or to a non-user via a `#fragment` link-key. File-
>   scoped, revocable.
> - ✅ **Specialized secure-item viewers**, all clean + reveal-by-default:
>   passwords, secure notes, **seed phrases** (word grid, blur/reveal,
>   per-word copy, never in previews), **cards/IDs** (masked + copy).
> - ✅ Rule: **shareable item types do not carry a free-text "notes" field**
>   that would travel with a share (avoid leaking context on share). Notes
>   remain their own non-… item type; sensitive fields are reveal-gated.
>
> ### Revised phases (VLT-8)
> 1. **Keys + vault-as-drive schema** — migration `0046`: X25519 account
>    keypair on `vault_meta`; `vault_folders` (nested, encrypted names);
>    extend `vault_items` (folder_id, kinds file|password|note|seed|card,
>    object-storage ref for file blobs). Client account-keypair gen +
>    MK-wrap, derived from the existing master password.
> 2. **Encrypted upload + folders UI** — client encrypts (chunked for big
>    files/video), uploads ciphertext to MinIO via the vault API; vault
>    folder tree + destination = a vault folder.
> 3. **Encrypted download / preview** — client fetch → unwrap → decrypt →
>    render; PDF / image / video viewers, all client-side.
> 4. **Secure-item viewers** — seed phrase / password / note / card, clean,
>    reveal-gated.
> 5. **Sharing via key-wrapping** — per-recipient seal + `#fragment` links,
>    revoke; honor the "no notes on shareable items" rule.
> 6. **Polish + marketing copy** — describe the Vault as zero-knowledge
>    E2E (true); the Drive as encrypted-in-transit-+-at-rest with opt-in
>    AI. No whole-product E2E claim.

---

## 1. Goals (from the product owner)

1. **Default-private.** Content is encrypted with keys the user holds.
2. **Vault = strict E2E, always.** Never AI, never server-readable, no
   toggles. For passwords, notes, seed phrases, very sensitive items.
3. **AI still works** on the rest of the library — that's the product. AI
   approval must be **clean, per-folder, non-redundant, non-intrusive**: you
   approve a folder once, files inherit it. Never a per-file nag.
4. **Auto vs manual, chosen at onboarding.** A one-screen first-run choice:
   - **Auto** — AI on across the library, with a plain "we never train on,
     sell, or share your data" statement.
   - **Manual / private-first** — AI off until you turn it on per folder.
5. **Sharing without compromise.** Sharing a file grants exactly that file to
   exactly those people — no broader exposure, even though it's encrypted.
6. **Upload picks a destination** (Vault vs a Library folder); AI follows the
   destination folder. No redundant prompts.

## 2. Threat model

- **In scope:** a full database + object-store compromise, a stolen backup,
  a malicious or compelled operator. None of these may yield plaintext of
  **vault or AI-off** content, nor any user's master password / private key.
- **Explicit trade-off (out of scope by design):** **AI-on files are
  readable by the server** — they must be, for CLIP/Florence/face models to
  run. We are honest about this: AI-on ≠ E2E. It is encrypted at rest and
  access-controlled, not zero-knowledge. The UI states this plainly the
  moment a folder is AI-on.
- Master password is never transmitted. Private keys leave the client only
  in master-key-wrapped form.

## 3. Key hierarchy

```
Master Password ──PBKDF2-SHA256(600k, per-user salt)──▶ Master Key (MK)   [client only]
                                                          │
                          ┌───────────────unwraps─────────┘
                          ▼
        Account Key Pair (X25519)
          • account_public_key      [stored plaintext, server]   ← others wrap to this
          • account_private_key     [stored MK-wrapped, server]  ← user unwraps with MK

        Per-file Key (FK = random AES-256-GCM key, one per file)
          • file bytes = AES-256-GCM(FK, nonce, plaintext)        [client encrypts pre-upload]
          • wrapped_fk_owner   = seal(FK → account_public_key)    [always]
          • wrapped_fk_server  = seal(FK → server_public_key)     [iff AI-on]
          • per-share grant    = seal(FK → recipient_public_key)  [per shared recipient]

        Server Key Pair (X25519)                                  [operator-held]
          • server_public_key       [plaintext]   ← AI-on files wrap FK to this
          • server_private_key      [Fernet-wrapped with CLOUD_ENCRYPTION_KEY, ML worker only]
```

- **One master password** protects everything E2E, including the vault. The
  vault stops being a separate keyring — it's the "always-E2E, never-AI"
  folder under the same account key. (Migration note in §10.)
- `seal(key → X25519 public)` = libsodium-style sealed box (ephemeral
  X25519 + AEAD). WebCrypto lacks X25519 sealed boxes natively; we use a
  small audited WASM (libsodium.js) or an ECDH-P256 + HKDF + AES-GCM
  equivalent built on WebCrypto. **Decision: libsodium.js sealed boxes**
  (crypto_box_seal) — boring, audited, tiny. Fallback documented if WASM is
  unavailable.

## 4. The two zones

| | Vault | Library (AI-off folder) | Library (AI-on folder) |
|---|---|---|---|
| Server can read | **never** | **never** | yes (needs to, for AI) |
| FK wrapped to | owner | owner | owner **+ server** |
| AI features | none | none | full |
| Thumbnails | client-made, encrypted | client-made, encrypted | server-made (as today) |
| Search | client-side, after unlock | client-side, after unlock | server semantic + FTS |
| Use for | secrets, seeds, sensitive | private files | everything you want findable |

The vault is just a reserved, undeletable, always-`ai_enabled=false` folder
with a stricter UX (no AI toggle shown).

## 5. AI approval model (clean, per-folder, never per-file)

- `users.ai_mode` ∈ {`auto`, `manual`} — set once at onboarding (§9).
- `folders.ai_enabled` (bool). New folders default to `ai_mode=='auto'`.
- A file **inherits its folder's `ai_enabled` at upload** — that's the only
  decision point. No per-file prompt, ever.
- Folder chip in the UI: ✦ "AI on" (sparkle) or 🔒 "Private" (lock). One
  click toggles, with a one-line confirmation of the trade-off.
- **Enable AI on a folder** (off→on): client (unlocked) unwraps each FK with
  MK and adds `wrapped_fk_server`; server then queues AI for those files.
- **Disable AI on a folder** (on→off): server **deletes** `wrapped_fk_server`
  + all AI-derived artifacts (embeddings, summaries, server thumbnails,
  face data) for those files, and the client re-uploads encrypted
  thumbnails. The server's read access is revoked.

## 6. Upload flow

1. Destination picker (one control): **Vault** · **a Library folder** (last
   destination remembered; no second prompt).
2. Client generates `FK`, encrypts bytes, computes `wrapped_fk_owner`, and —
   iff the destination folder is AI-on — `wrapped_fk_server`.
3. For Vault / AI-off destinations the client also generates + encrypts a
   thumbnail and uploads it alongside.
4. `POST /files` with: ciphertext, nonce, kind, folder_id, wrapped_fk_owner,
   wrapped_fk_server?, enc_thumb?. Server stores opaque bytes; for AI-on it
   enqueues the pipeline (worker unwraps via server key).

## 7. Download / preview / thumbnails

- Browser fetches ciphertext + `wrapped_fk_owner`, unwraps FK with the
  in-memory account private key (available after master-password unlock),
  decrypts, renders. Same in-memory-key / auto-lock model as today's vault.
- **AI-off / vault** thumbnails: client-encrypted, decrypted client-side.
- **AI-on** thumbnails + served variants: server-side as today (it has FK).
- Large files / video: chunked AES-GCM (per-chunk nonce derived from a base
  nonce + counter) so we stream rather than buffer whole files.

## 8. Sharing without compromise

- **To a neuthek user (by email):** look up their `account_public_key`,
  `seal(FK → recipient_public_key)`, store a `file_share_grant`
  (file_id, recipient_user_id, wrapped_fk, expires_at, revoked_at). They list
  shares, unwrap with their private key, decrypt that one file. Revoke =
  set `revoked_at`; their next fetch 404s and the wrap is dropped.
- **To a non-user (public link):** generate a random link-key; `seal(FK →
  link-key)`; the link carries the key in the URL **# fragment**
  (`/s/<id>#<key>`) which browsers never send to the server. Recipient's
  browser reads the fragment, decrypts client-side. Server only ever holds
  ciphertext + the fragment-less grant. TTL + revoke as today.
- Scope is always a single file. No grant ever exposes the folder or account
  keys.

## 9. Onboarding (the one-screen choice)

First library open (after account creation), one screen:
1. **Set your master password** (with the existing "this is the only key,
   write it down, no recovery" warning — already built for the vault).
2. **Choose your default:**
   - **Auto — AI across my library** *(recommended)*. "AI runs only to power
     your search and organization. We never train on, sell, or share your
     data." → `ai_mode = auto`.
   - **Private by default.** "Files stay end-to-end encrypted; turn AI on per
     folder when you want search and summaries there." → `ai_mode = manual`.
3. Generate the account key pair client-side; store public + MK-wrapped
   private. Vault is created as the reserved always-private folder.

Changeable later in Settings → Privacy. The vault is unaffected by this
choice — always strict E2E.

## 10. Migration (existing plaintext libraries)

Existing users already have server-readable plaintext files (the server
processed them). We do **not** silently claim those are E2E.

- On rollout, existing files are marked **AI-on** in their current folders
  (truthful: the server already has them) and keep working unchanged.
- New uploads follow this design.
- A **"Encrypt my existing library"** tool (later phase) lets a user
  download → client-encrypt → re-upload existing files into E2E, and purges
  the old plaintext + server AI artifacts for the ones they make AI-off.
- The marketing site keeps describing the product accurately: encrypted in
  transit + at rest today; **opt-in E2E + zero-knowledge vault** as the new
  capability. No blanket "everything is E2E" claim while legacy plaintext
  exists (FTC/Zoom caution).

## 11. Schema (additive migration `0045_e2e`)

- `users`: `+account_public_key bytea`, `+enc_account_private_key bytea`,
  `+account_kdf_salt bytea`, `+account_kdf_iterations int`,
  `+ai_mode text default 'auto'` (CHECK in auto|manual).
- `server_keys`: `id`, `public_key bytea`, `enc_private_key bytea` (Fernet),
  `created_at`, `active bool` — supports rotation.
- `folders`: `+ai_enabled bool` (default per ai_mode at create).
- `images`/files: `+is_e2e bool`, `+enc_algo text`, `+content_nonce bytea`,
  `+wrapped_fk_owner bytea`, `+wrapped_fk_server bytea null`,
  `+enc_thumb_key bytea null`. The stored object becomes ciphertext for
  E2E/AI-off; AI-on objects may stay server-encrypted-at-rest as today.
- `file_share_grants`: `id`, `file_id`, `recipient_user_id null`,
  `link_key_hint null`, `wrapped_fk bytea`, `expires_at`, `revoked_at`,
  audit columns. RLS so a user sees only grants they own or received.
- All new tables/columns under FORCE RLS where user-scoped; cascade-delete
  with the account (deletion-completeness test extended).

## 12. Phased build

- **Phase 1 — Keys & onboarding.** `0045_e2e` (keys + ai_mode + folder flag),
  client account-keypair generation, server keypair, onboarding screen,
  Settings → Privacy default switch. Unifies the vault master password as the
  account master password. *No file encryption yet — foundation only.*
- **Phase 2 — Encrypted upload.** Client FK encryption, destination picker,
  `wrapped_fk_*`, ciphertext upload, encrypted client thumbnails for
  AI-off/vault.
- **Phase 3 — Encrypted download/preview.** Client fetch→unwrap→decrypt
  render; chunked streaming for large/video.
- **Phase 4 — AI gating + folder toggle.** Worker reads only AI-on files via
  server key; folder enable/disable re-wraps / purges.
- **Phase 5 — Sharing with key-wrapping.** Per-user grants + `#fragment`
  links + revoke; migrate existing share flow.
- **Phase 6 — Migration tool + polish.** "Encrypt existing library", QA,
  perf, docs, marketing copy update.

Each phase ships behind a flag, with tests (round-trip, wrong-key, RLS,
share-scope, AI-gating, deletion-completeness) before the next begins.

## 13. Open risks / notes

- WebCrypto has no X25519 sealed-box → depend on libsodium.js (WASM). Vet
  size + SRI. P-256 ECDH fallback path documented if WASM blocked.
- AI-on files are server-readable by definition — keep the UI honest so a
  user never believes an AI-on file is zero-knowledge.
- Search over E2E/AI-off content is client-side only (no server index) —
  scope expectations in the UI.
- Performance: chunked crypto for video; encrypted-thumbnail generation
  client-side adds upload cost for AI-off content.
- Backwards compatibility: legacy plaintext stays AI-on; never relabeled E2E.
