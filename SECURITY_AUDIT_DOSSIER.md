# neuthek — Security Audit Dossier

> Prepared for an external security review. This document inventories the
> entire system: architecture, trust boundaries, every security-relevant
> subsystem and method (what it does, how it works, known issues), the full
> third-party dependency surface, the cryptography, and the residual risks we
> most want a reviewer to scrutinize. It deliberately names **where** secrets
> live but contains **no secret values**.
>
> Scale at time of writing: ~50 backend Python modules, **18 API routers /
> ~198 HTTP operations**, 47 Alembic migrations, a React SPA, and a separate
> Express marketing service. Last updated: 2026-05.

---

## 0. How to read this & suggested audit scope

Highest-value targets, in priority order:

1. **The zero-knowledge Vault crypto** (§7) — newest, most security-sensitive,
   and the only place we make a "we cannot read it" claim. Verify the claim end
   to end.
2. **Authentication & session** (§5) — fastapi-users, cookie/JWT, MFA, magic
   links, recovery codes, SSO account-linking.
3. **Multi-tenant isolation** (§6) — Postgres Row-Level Security + app-layer
   ownership checks. Confirm no cross-tenant read/write.
4. **Sharing surfaces** (§11) — three distinct share mechanisms, two of them
   unauthenticated by design.
5. **File-ingestion pipeline** (§9) — many native parsers (RAW, HEIC, PDF,
   video) + ffmpeg + ML pickle loads = a large memory-safety/RCE surface.
6. **Cloud-sync / OAuth / SSRF** (§10).
7. **Edge & infra** (§13).

A reproducible scanner + test harness is described in §16.

---

## 1. System overview

neuthek is an AI-aware personal cloud-storage product with three deployment
artifacts:

| Surface | Stack | Auth | Notes |
|---|---|---|---|
| **App API** | FastAPI (Python 3.12) behind Caddy | per-endpoint deps | The product. 18 routers. |
| **App SPA** | React 18 + Vite (TypeScript + JSX) | HttpOnly cookie | Served by a Vite/Nginx container. |
| **Marketing** | Express 4 + Vite SPA | none (public) + admin token | Separate repo dir, deployed on Render. Waitlist + newsletter. |

Supporting services (docker-compose): **PostgreSQL 16 + pgvector**, **Redis 7**
(rate-limit counters + job queue), **MinIO** (S3-compatible object store), an
**ML worker** (same image as the API, runs Florence-2 / CLIP / RetinaFace /
Whisper inference off a Redis queue).

Two product zones with **different security postures**:

- **Drive** — server holds the keys; AI features (semantic search, captions,
  face grouping) run on content *with the user's consent*. Encrypted in transit
  + at rest, NOT end-to-end.
- **Vault** — **end-to-end encrypted, zero-knowledge.** Keys derive from a
  master password on the device; the server stores only ciphertext and never
  runs AI on it. (§7.)

---

## 2. Trust boundaries & data flow

```
            ┌─────────────── Cloudflare (optional edge: WAF/CDN/DDoS) ───────────────┐
 Browser ──►│ Caddy (TLS term, HSTS, security headers, CF-Connecting-IP trust)       │
            └───────────────┬───────────────────────────────────┬────────────────────┘
                            │ /                                  │ /api,/vault,/auth,…
                       Vite/Nginx (SPA)                     FastAPI (uvicorn)
                                                                 │
            ┌────────────────────────────────────────────────────┼───────────────┐
            │ Postgres (RLS)   Redis (counters/queue)   MinIO (blobs)   ML worker  │
            └────────────────────────────────────────────────────────────────────┘
                            │ outbound (SSRF-relevant)
            Google OAuth · iCloud (pyicloud) · Proton/MEGA (rclone) · Nominatim · Stripe
```

**Boundaries an auditor should test:**
- Browser → API: cookie auth, CSRF/Origin checks, CORS.
- API → Postgres: RLS GUC (`app.current_user_id`) must be set per request and
  never spoofable by the client.
- API → MinIO: object keys are server-generated; clients never name buckets.
- API → external providers: SSRF and token-confidentiality boundary.
- Public link / public share endpoints: unauthenticated by design — token is
  the only gate (§11).
- The Vault crypto boundary: ciphertext crosses to the server; **plaintext and
  keys must never** (§7).

---

## 3. Sensitive-asset inventory

| Asset | Where | Protection |
|---|---|---|
| User passwords | `users` table | Argon2id (fastapi-users default) |
| Session JWT | HttpOnly `neuthek_session` cookie | `Secure`+`SameSite`; `token_version` revocation |
| TOTP secrets | `users.totp_secret_enc` | Fernet (secret_box) at rest |
| Recovery codes | `recovery_codes` | hashed |
| OAuth refresh tokens (Google/Dropbox) | `cloud_links` | Fernet at rest |
| iCloud session trust / rclone creds | `/var/neuthek/*` (0700) | filesystem, container-private |
| **Vault master password** | **never transmitted/stored** | derived client-side only |
| **Vault item plaintext / per-file keys** | **ciphertext only on server** | AES-256-GCM, client-held keys |
| Vault account private key | `vault_meta.enc_account_private_key` | wrapped under master key; server never unwraps |
| Stripe secret + webhook secret | env / `*_FILE` mounts | not in VCS |
| App symmetric master key (`secret_box`) | env / file | derives all server subkeys (HKDF) |
| Audit log | `audit_log` | PII-scrubbed, capped (see §14) |

---

## 4. Technology stack & dependency surface

### 4.1 Backend (Python 3.12) — `pyproject.toml`
Core: `fastapi==0.115.5`, `uvicorn[standard]==0.32.1`,
`sqlalchemy[asyncio]==2.0.36`, `asyncpg==0.30.0`, `psycopg2-binary==2.9.10`,
`alembic==1.13.3`, `pydantic==2.9.2`, `pydantic-settings==2.6.1`,
`fastapi-users[sqlalchemy]==15.0.5`, `argon2-cffi==23.1.0`,
`python-multipart==0.0.27`, `minio==7.2.10`, `redis>=5.2.0`, `httpx==0.27.2`,
`pgvector==0.3.6`, `stripe>=11.0`, `pyotp>=2.9.0`, `qrcode>=8.0`,
`psutil>=5.9.0`.

Media/parsers (large native attack surface): `pillow==12.2.0`,
`pillow-heif==0.18.0`, `imagecodecs==2024.9.22`, `rawpy==0.24.0` (LibRaw),
`numpy==2.1.3`, `PyMuPDF>=1.24.0`, `pypdf`, `pdfminer.six`, `python-docx`,
`openpyxl`.

ML extras (`[ml]`): `torch>=2.5.1`, `torchvision`, `open-clip-torch==2.28.0`,
`transformers>=4.45,<4.50`, `tokenizers`, `timm`, `accelerate`,
`insightface==0.7.3`, `onnxruntime==1.20.1`, `mediapipe>=0.10`,
`faster-whisper>=1.0.3`, `sumy`, `nltk`.

System packages (Dockerfile): **ffmpeg**, **exiftool**, **rclone v1.69.0**
(pinned), libpq, OpenCV runtime libs.

### 4.2 Frontend SPA — `frontend/package.json`
`react@18`, `@tanstack/react-query@5`, `zustand@5`, `hls.js`, `leaflet`,
`supercluster`, `prismjs`, `date-fns`, `react-hot-toast`, `@stripe/*`. Build:
Vite 8 + `@vitejs/plugin-react`.

### 4.3 Marketing — `marketing/package.json`
`express@4.21`, `express-rate-limit@7.5`, `pg@8.13`, `better-sqlite3@11`,
`react@18`, `react-router-dom@6`.

### 4.4 Dependency-risk notes for the auditor
- **Native image/RAW/HEIC/PDF parsers** (`pillow`, `pillow-heif`,
  `imagecodecs`, `rawpy`/LibRaw, `PyMuPDF`) historically carry memory-safety
  CVEs. Uploaded bytes reach them. Check versions against advisories at audit
  time.
- **ffmpeg** processes attacker-controlled media; we constrain it (§9) but it
  is the single largest RCE surface.
- **ML model loading** (`transformers`, `insightface`, `torch`): model files
  are pickle/safetensors. We ship known weights; confirm no untrusted model
  path is loadable (supply-chain).
- **`xlsx`/`node-tar`** were Dependabot-flagged historically and remediated
  (see §15 prior findings); confirm none reintroduced.
- Dependabot + CodeQL run in CI (backend + frontend + marketing).

---

## 5. Authentication & session management

Built on **fastapi-users 15** (`backend/auth/users.py`). Identity: email +
Argon2id password. Surfaces (router `/auth`, `/users`, plus
`backend/api/email_link.py`, `backend/api/two_factor.py`):

### 5.1 Methods / flows
- **Register** (`/auth/register`) — requires `display_name` + `age_confirmed`
  (C4.1). Sends a verification email (`backend/email_send.py`). Issues no
  session until verified-flow completes per config.
- **Password login** (`/auth/jwt/login`, `/auth/cookie/login`) — Argon2id
  verify. On success sets the **HttpOnly `neuthek_session` cookie** (the SPA
  never sees the JWT; closes the localStorage-XSS exfiltration vector).
- **Login lockout** (`backend/security.py`) — failed-attempt counter in Redis.
  **Known/keyed-by:** lockout keys on **email** (A5) AND the cookie path is
  rate-limited (CR-2). Auditor: confirm lockout can't be used to DoS a victim
  (mitigated by also keying on IP and short windows) and can't be bypassed by
  case/whitespace email variants.
- **JWT revocation** (A8) — every JWT embeds a `tv` (token_version) claim
  compared to `users.token_version`; bumping the column logs out every session.
  Bumped on password reset and 2FA disable.
- **MFA / TOTP** (`backend/api/two_factor.py`) — `pyotp` RFC-6238. Secret is
  Fernet-encrypted (`totp_secret_enc`); `enabled` flips only after a verify.
  Regenerate flow + QR rendered locally (secret never leaves host).
- **Recovery codes** (`recovery_codes` table) — single-use, hashed; consume
  flow sets the cookie.
- **Magic link / passwordless** (`backend/api/email_link.py`) — `/signin`
  landing consumes a short-lived JWT; paired with a 6-digit code. **Known
  issue history:** Gmail link-prefetch consumed single-use tokens early →
  remediated (separate code + link, prefetch-tolerant).
- **Forgot/reset password** (`/auth`) — emailed reset JWT; `/reset` landing.
  Bumps `token_version` on success.
- **Email change re-verify** (C4.3) — changing email re-enters unverified state.
- **Google SSO** (`backend/auth/google_sso.py`) — OAuth code flow; `state` is
  HMAC-signed (`oauth_sso_state_key`, §8). **Known/remediated (CR-5):** SSO
  email-match account-takeover — linking now requires matching `google_sub`,
  not just email equality. Auditor: re-verify the account-linking decision.

### 5.2 Session middleware (`backend/security.py`)
- `SecurityControlsMiddleware` — central rate-limiting / upload-limit / abuse
  enforcement; tiered limits per user (`_tier_limits_for`).
- `CsrfOriginMiddleware` — Origin/Referer checks on state-changing requests
  (cookie auth ⇒ CSRF-relevant).
- `SecurityHeadersMiddleware` — sets/strips security headers at the app layer
  (Caddy also sets them; defense in depth).
- `client_ip()` — derives the real client IP, trusting `CF-Connecting-IP` only
  when behind Cloudflare (and `X-Forwarded-For` via Caddy `trusted_proxies`).
  **Auditor focus:** IP spoofing → rate-limit bypass if the proxy chain is
  misconfigured. Correctness depends on origin being locked to CF (§13).

**Known issues / things to probe:** session fixation on cookie login; cookie
flags in all deploy modes; whether `validate_production_settings()` (below)
actually refuses to boot with dev secrets in prod.

---

## 6. Authorization & multi-tenancy (RLS)

Two layered defenses:

1. **Postgres Row-Level Security (FORCE)** on tenant tables — policies fence
   rows to `current_setting('app.current_user_id')::uuid`. The app sets that
   GUC per request from the authenticated user. FORCE means even the table
   owner is subject to it. Applied to: `vault_meta`, `vault_items`,
   `vault_folders`, `vault_share_grants`, and the image/consent/etc. tenant
   tables.
2. **App-layer ownership checks** — endpoints additionally filter on
   `user_id == current_user` and return 404 (not 403) on wrong-owner IDs to
   avoid existence oracles.

**Deliberate exceptions (must read):**
- `vault_public_links` has **no RLS** — it is read by *unauthenticated*
  visitors who only know the high-entropy token. Owner management is fenced in
  the app layer; the row holds only ciphertext + routing. (§11.3)
- `users` is not per-user RLS-fenced (auth/login must read across rows).

**Known/remediated:** D2 `image_persons` IDOR write (now ownership-checked);
D1 unbounded pagination (now capped). **Auditor focus:** confirm the GUC is set
on *every* authenticated path (including background/worker DB sessions, which
operate as the owner), and that any cross-user read (e.g. recipient public-key
lookup, share grants) is intentional and minimal.

---

## 7. The zero-knowledge Vault (primary review target)

Goal: the server **cannot** read Vault contents even with full DB + object-store
compromise. Implemented client-side in `frontend/src/vault/crypto.ts`
(WebCrypto, no WASM) and ciphertext-only on the server (`backend/api/vault.py`).

### 7.1 Key hierarchy
- **Master key** = `PBKDF2-SHA256(masterPassword, salt, 600_000)` →
  non-extractable AES-256-GCM CryptoKey. Salt is public (per-user, 16 B). The
  password and key never leave the device. Forgetting the password = data is
  unrecoverable by design.
- **Verifier** — a known constant encrypted under the master key; unlock
  succeeds iff AES-GCM auth passes. The server stores the verifier blob and
  learns nothing.
- **Account keypair** (P-256 ECDH) — public key stored in clear (and mirrored
  to `users.vault_public_key` so others can seal to it); private key stored
  **AES-GCM-wrapped under the master key** (`enc_account_private_key`).
- **Per-file key** — random AES-256 per file. File bytes are encrypted in
  **1 MiB chunks** (each chunk: 12-B nonce = 4-B random prefix ‖ 8-B BE counter;
  per-chunk AAD binds the chunk index to prevent reordering/truncation).
- **Wrapping/sharing** — a "sealed box" built on P-256 ECDH + HKDF-SHA256 +
  AES-GCM (WebCrypto has no X25519 `crypto_box_seal`). `sealToPublicKey`
  output = `ephLen ‖ ephPub ‖ nonce ‖ ct`.

### 7.2 Security-critical methods (`crypto.ts`)
| Method | What it does | Notes / invariants |
|---|---|---|
| `deriveVaultKey` | PBKDF2 → non-extractable AES-GCM key | iters bounded server-side 310k–5M; client uses 600k |
| `makeVerifier` / `checkVerifier` | wrong-password signal via GCM auth | AAD domain-separated |
| `encryptItem` / `decryptItem` | seal a small JSON item (incl. title) | titles encrypted; search is client-side |
| `createAccountKeyPair` / `unwrapAccountPrivateKey` | gen + master-key-wrap the ECDH key | private key never leaves device unwrapped |
| `sealToPublicKey` / `unsealFromPrivateKey` | seal/open per-file key to a recipient | ephemeral ECDH per seal |
| `encryptFile` / `decryptFile` | chunked AES-GCM; verifies length+chunk count | truncation/tamper/wrong-key all fail |
| `derivePublicLinkKey` | HKDF(linkSecret [+ PBKDF2(password)]) | password link unopenable by link alone |
| `sealPublicLink` / `openPublicLink` | seal a bundle to a public-link key | key lives in URL fragment only |

### 7.3 Server side (`backend/api/vault.py`)
The router is deliberately "dumb": validates that every binary field is
well-formed base64 within strict byte bounds, stores/returns opaque bytes,
**never decrypts, never logs ciphertext/keys/passwords**. File ciphertext is
streamed to a dedicated MinIO bucket (`neuthek-vault`); HTTP Range supported for
encrypted-media scrubbing. Quota shares one pool with the Drive.

### 7.4 Validation performed (not a substitute for audit)
Node round-trips: sealed-box (10/10), chunked file crypto incl. tamper/
truncation/wrong-key (30/30), share-bundle cross-account (5/5), public-link incl.
"password link not openable by link alone" (8/8). Backend in-process E2E vs live
infra: vault CRUD 33/33, direct shares 29/29, public links 31/31.

### 7.5 Known issues / residual risk (Vault)
- **Password public links are offline-brute-forceable**: an attacker who
  fetches the sealed blob once can brute-force the password locally. PBKDF2 600k
  slows it; strong passwords are the user's responsibility. (Inherent to all
  client-encrypted public links; MEGA/Proton-class.)
- **No forward secrecy / key rotation** for the account keypair: rotating it
  would orphan existing shares (we refuse to overwrite). Master-password change
  = full client re-encrypt (not yet a one-click flow).
- **Metadata leakage**: the server learns item *kind* (password/note/seed/card/
  file), file *sizes* (ciphertext length), counts, folder structure (encrypted
  names but visible graph), and timestamps. This is by design but should be in
  the threat model.
- **Trust-on-first-use** for recipient public keys: a malicious server could
  serve an attacker's public key during a share lookup. Out of scope for the
  honest-but-curious model; note for a malicious-server model.
- **XSS in the app would defeat E2E** while unlocked (the master key lives in
  memory). The non-extractable CryptoKey limits exfiltration but not misuse.
  Auditor: scrutinize the SPA's XSS surface and CSP.

---

## 8. Cryptography inventory (server-side)

- **`backend/key_derivation.py`** — single app master key → purpose-bound
  subkeys via **HKDF** (CR-3 key separation). Subkeys: signed-download,
  signed-share, stream, OAuth-SSO-state, OAuth-cloud-sync-state. Cached;
  `derive_subkey(purpose)`.
- **`backend/secret_box.py`** — **Fernet** (AES-128-CBC + HMAC) for at-rest
  encryption of OAuth refresh tokens + TOTP secrets. `_bootstrap_key()` resolves
  the key from env/file; `encrypt`/`decrypt`.
- **`backend/signed_urls.py`** — **HMAC-signed, TTL-capped** URLs for media
  download / share / stream. `verify_download`/`verify_share_download` bind
  image_id + user_id + variant + expiry; recipient binding added (U3/S4).
  TTLs capped (`_capped_ttl`). Auditor: confirm constant-time comparison and no
  signature-stripping/`alg=none`-style bypass.
- **Vault crypto** — §7 (client-side; PBKDF2/AES-GCM/P-256-ECDH/HKDF).
- **TLS** — terminated by Caddy (Let's Encrypt); HSTS preload; TLS 1.3 + h2/h3.

**Auditor focus:** all server subkeys derive from one master key — its
compromise is catastrophic for at-rest tokens + signed URLs (but NOT the Vault,
which is client-keyed). Confirm rotation story (`_clear_all_caches`,
secret-manager modes).

---

## 9. File-ingestion & media pipeline (large attack surface)

Path: upload → validation → store original (MinIO) → background transcode/
re-encode → derived blobs → optional AI.

Key modules: `backend/api/images.py`, `upload_validation.py`,
`archive_upload.py`, `image.py`, `transcode.py`, `ffmpeg_args.py`, `codecs.py`,
`hls.py`, `video.py`, `document_compress.py`, `summarize.py`, `transcribe.py`.

**Controls in place (and prior findings remediated):**
- **Type/size gates** (`upload_validation.py`): per-file cap (200 MB default),
  per-hour count, per-day byte budget, max image pixels (decompression-bomb
  guard).
- **Zip-bomb defenses** (U2): per-entry uncompressed cap, total uncompressed
  cap, max entries, max depth, max ratio for archive uploads.
- **EXIF stripping** (B1) on re-encode; **U5/U6** hardened EXIF-strip failure
  handling + video metadata scrub.
- **ffmpeg hardening** (CR-6): `-protocol_whitelist` restricts ffmpeg to safe
  protocols (blocks `file:`/`http:` SSRF via crafted playlists);
  `ffmpeg_args.py` centralizes arg construction.
- **Quarantine bucket** for suspect uploads; SSE modes available.
- Non-root container (CR-7) limits blast radius of a parser RCE.

**Known issues / probe targets:**
- RAW/HEIC/PDF/codec parsers run on attacker bytes — version-pin against CVEs.
- ML inference loads model weights (pickle/safetensors) — confirm no
  user-controlled model path.
- HLS/transcode temp-file handling and path construction (traversal).
- Decompression-bomb coverage for PDF rasterization (PyMuPDF) and RAW.

---

## 10. Cloud sync & OAuth (SSRF + token surface)

Modules: `backend/cloud_sync.py`, `cloud_sync_worker.py`, `cloud_sync_lock.py`,
`cloud_sync_retry.py`, `rclone_wrapper.py`, `backend/api/cloud.py`,
`backend/auth/google_sso.py`. Providers: **Google Drive** (OAuth),
**iCloud** (pyicloud — Apple ID + HSA-2/2SA), **Proton Drive + MEGA** (via
pinned **rclone**). (Dropbox/OneDrive/Box/pCloud were trialed and removed.)

**Controls / remediated findings:**
- OAuth `state` HMAC-signed (CR-3 subkeys) to prevent CSRF/login-CSRF.
- Refresh tokens **Fernet-encrypted at rest** (`cloud_links`).
- **CR-4 quota bypass** fixed — cloud-sync ingest enforces the account quota
  (now via the shared combined-pool helper).
- **CS9/CS10** per-link sync lock (no concurrent double-sync) + exponential
  backoff retry.
- **CS3** Google token revoked on link delete.
- iCloud session-trust + rclone creds stored in `/var/neuthek/*` (0700,
  container-private).

**Known issues / probe targets:**
- **SSRF**: any provider fetch that follows provider-supplied URLs/redirects.
  Confirm allow-listing + no internal-network reachability.
- rclone is a powerful external binary — confirm config is generated by us
  (not user-injected) and invoked without shell interpolation
  (`rclone_wrapper.py`).
- Token scope minimization (Drive is `drive.readonly`).
- iCloud 2FA flows touch several Apple endpoints; verify no credential logging.

---

## 11. Sharing surfaces (three distinct mechanisms)

### 11.1 Drive shares (`backend/api/shares.py`, `comments.py`)
Server-readable assets shared to a recipient or via signed link. Has
**comments** (`comments` table). Signed URLs bind recipient + audit at fetch
(U3/S4). Pagination capped (D1).

### 11.2 Vault direct shares — key-wrapped (`vault_share_grants`)
Owner seals the per-file key (or item plaintext) to a recipient's account
public key on-device → grant row holds only the sealed blob + routing. FORCE
RLS lets both parties see/delete; only the owner creates. **No comments, no
public links.** Recipient streams ciphertext via `/vault/shares/{id}/file`.

### 11.3 Vault public links — anyone-with-link (`vault_public_links`)
Zero-knowledge public link. The decryption key rides in the **URL fragment**
(never sent to the server). Optional password is mixed into key derivation
(PBKDF2→HKDF) so the blob can't be opened with the link alone. Optional expiry.
**Unauthenticated read endpoints** (`GET /vault/public/{token}` and
`/public/{token}/file`), gated only by a high-entropy `secrets.token_urlsafe(32)`
token, rate-limited per token. Recreating rotates the token (old link dies).
Deleting the item cascades the link. **No comments.**

**Auditor focus on §11:** token entropy + enumeration resistance; that the
fragment is genuinely never logged/forwarded; that unauthenticated endpoints
leak nothing beyond the sealed blob + public KDF params; rate-limit efficacy
against scraping; and that the three mechanisms don't cross-contaminate
(e.g. a Drive share can't expose a Vault blob).

---

## 12. Background jobs, workers & ML

`backend/jobs.py` (Redis queue), `backend/worker/main.py` (BLPOP loop),
`faces_pipeline.py`, `trainer.py`, `bandit.py`, `best_of.py`, `name_suggest.py`,
`summarize.py`, `transcribe.py`, `heartbeats.py`, `system_probes.py`.

- ML inference runs in a separate container (own GIL); jobs are per-user and
  consent-gated. CS5 fixed "AI-when-disabled" (no inference without consent).
- `system_probes.py` powers the admin System/Hardware tabs (psutil) — confirm
  it can't be coerced into arbitrary command/file reads.
- **Model supply chain**: weights are pickle/safetensors; review load paths.
- Per-user queue draining + worker heartbeats; confirm a malicious job payload
  can't escape the per-user scope.

---

## 13. Network edge & infrastructure

- **Caddy** (`Caddyfile`, `deploy/Caddyfile`) — TLS, HSTS preload,
  `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, strips
  `Server`. Cloudflare `trusted_proxies` + `client_ip_headers Cf-Connecting-Ip`
  so rate-limits/audit key on the real visitor. **Refresh CF CIDR list
  periodically.**
- **Cloudflare** (`CLOUDFLARE_SETUP.md`) — operator must: Full(strict) TLS,
  **lock the origin to CF ranges** (else `CF-Connecting-IP` is spoofable and the
  WAF is bypassable), WAF + OWASP CRS, Bot Fight, rate-limit rules on
  `/auth/*` + admin.
- **Docker** (`Dockerfile`, CR-7/F17): multi-stage, **non-root `neuthek` user
  (uid 1000)**, credential dirs 0700, `--no-cache-dir` venv.
- **docker-compose** (CR-8/F14): MinIO/Postgres ports **loopback-bound**
  (`127.0.0.1`), Vite dev server loopback-only (it serves the source tree
  unauthenticated). Secrets via env or `*_FILE` mounts.
- **`validate_production_settings()`** (`backend/security.py`, CR-10) — refuses
  to boot in production with dev defaults (jwt_secret, MinIO creds, etc.) and
  requires Redis. **Auditor: verify it actually blocks every weak default.**

**Probe targets:** the hosted edge `deploy/Caddyfile` does **not** currently
route `/vault/*` to the API (self-host root `Caddyfile` proxies everything) — if
the managed deploy uses same-origin API, vault routes would 404 there. Confirm
the hosted routing topology. MinIO console exposure; Redis auth; Postgres
at-rest encryption (OS/volume layer, operator-owned).

---

## 14. Logging, audit & PII

- `backend/audit.py` + `audit_log` table. **D5/F12:** audit details are capped
  in size and **PII-scrubbed**; ciphertext/keys/passwords are never logged
  (esp. Vault). Auth events recorded (`_audit_auth_event`).
- Stack traces are not leaked to clients (backend CodeQL follow-up).
- **Auditor focus:** confirm no secret/credential/Vault material appears in app
  logs, error responses, or the audit table under any code path.

---

## 15. Prior security work (already remediated — shows scope of past review)

A previous internal audit produced a triaged backlog; these are **fixed** and
serve as a map of historically-sensitive areas:

- **CR-2** cookie-login lockout · **CR-3** HKDF key separation · **CR-4**
  cloud-sync quota bypass · **CR-5** SSO email-match takeover · **CR-6** ffmpeg
  `-protocol_whitelist` · **CR-7/F17** Dockerfile non-root + multi-stage ·
  **CR-8/F14** compose loopback binding · **CR-10** production-settings
  validation.
- **A5** lockout-DoS keyed by email · **A8** JWT revocation via token_version.
- **U2** zip-bomb caps · **U3/S4** share URL recipient binding + audit at fetch ·
  **U5/U6** EXIF/video-metadata scrub.
- **D1** pagination caps · **D2** image_persons IDOR · **D5/F12** audit cap +
  PII scrub.
- **CS3/CS9/CS10** cloud-sync token-revoke + lock + backoff.
- CodeQL: ReDoS, open-redirect, stack-trace leak, iframe/client-redirect
  (frontend). Dependabot: `xlsx`, `node-tar`.

A reviewer should treat these as *regression-test targets* (the repo has tests
under `tests/` for most).

---

## 16. How to reproduce our checks

- **Backend tests:** `pytest` against a disposable Postgres (the
  `tests/conftest.py` `_test_db` fixture drops/creates/migrates a test DB and
  stubs MinIO). Vault-specific: `tests/test_vault*.py`,
  `tests/test_storage_vault_usage.py`. Security regressions:
  `tests/test_cookie_login_lockout.py`, `test_cross_user_leak.py`,
  `test_encryption_posture.py`, `test_dockerfile_hardening.py`,
  `test_compose_defaults_hardening.py`, `test_a7_repo_hygiene.py`, etc.
- **SAST:** CodeQL workflows (backend Python, frontend, marketing) +
  Dependabot. Re-run on the audit branch.
- **Vault crypto:** the algorithms live in `frontend/src/vault/crypto.ts` and
  can be exercised standalone under Node (esbuild bundle) — recommended for an
  independent crypto review.

---

## 17. Top residual risks (where to spend the audit budget)

1. **Vault threat model under a *malicious* (not just honest-but-curious)
   server** — TOFU on recipient keys; can a compromised server trick a user
   into sealing to an attacker key, or serve swapped ciphertext?
2. **SPA XSS / CSP** — any XSS defeats E2E while unlocked; review CSP, the PDF/
   HTML preview sandboxing (we refuse to inline-render HTML/SVG from decrypted
   blobs), and `dangerouslySetInnerHTML` usage.
3. **Native media parser RCE** (RAW/HEIC/PDF/ffmpeg) on attacker uploads.
4. **RLS GUC integrity** — is `app.current_user_id` ever set from
   client-controllable input, or unset on any authed path?
5. **Public/unauthenticated endpoints** (vault public links, Drive signed
   links, marketing) — enumeration, rate-limit bypass, info leak.
6. **OAuth/SSRF** in cloud-sync; rclone invocation safety.
7. **Single app master key** blast radius (at-rest tokens, signed URLs).
8. **Edge dependency** — `CF-Connecting-IP` trust is only safe with the origin
   locked to Cloudflare; verify the operator checklist is enforced.

---

## 18. Secrets the audit team will need (request out-of-band, never in VCS)

To exercise authenticated/admin/billing/cloud paths in a staging environment:
- A staging `.env` (or `*_FILE` mounts) with: `jwt_secret`, `secret_box` master
  key, MinIO creds, Postgres creds, Redis URL.
- Optional: Google OAuth client id/secret (test project), Stripe **test-mode**
  keys + webhook secret, a throwaway iCloud/Proton/MEGA account for sync.
- An admin user (`role=admin`/superuser) for the `/admin` surface.

None of these are committed; production values live only in the deploy
environment.

---
---

# PART II — Offensive testing playbook (pen-test / red-team)

This part is written for the testing team. It enumerates concrete attack
vectors, the exact defensive limits to expect (so you know what "normal" looks
like and can design bypasses), the highest-value targets, the non-obvious
behaviors ("tricks") worth abusing, and tooling. **Authorized scope only**:
this is the product owner's own application; test against a **staging** deploy,
never production user data.

## A. Rules of engagement & target setup

- **Stand up staging:** `docker compose up -d` brings the full stack
  (Postgres/Redis/MinIO/API/worker/SPA). Set a *real* `.env` (see §18) and
  **`SECURITY_RATE_LIMITS_ENABLED=true`** + `APP_ENV=production`-equivalent so
  rate-limits/lockouts are live (they **silently no-op in dev/test** — testing
  with them off will give false-negative results on brute-force findings).
- **Provision accounts:** at least 3 users (attacker A, victim B, bystander C),
  one **admin/superuser**, and (optionally) Stripe **test-mode** keys + a
  throwaway Google/iCloud/Proton/MEGA account for the cloud-sync surface.
- **For Vault tests:** set up a vault on A and B (master passwords you control),
  upload files + create secure items + create direct shares and public links so
  there is live ciphertext + grants + tokens to attack.
- **Capture everything through Burp/ZAP.** The SPA talks to the API with a
  **HttpOnly cookie** (`neuthek_session`) — there is no bearer token in JS, so
  drive the API directly with the cookie jar.
- **In scope:** API (`:8000`/edge), SPA, Vault crypto, the marketing Express
  service, the Caddy edge config, the docker/compose posture.
- **Out of scope unless asked:** third-party providers themselves
  (Google/Apple/Stripe/MEGA), Cloudflare's own infra.

## B. Full attack-surface map (per router)

198 operations across 18 routers. Auth model: **per-endpoint dependency**
(`current_active_user` / `current_admin_user` / `current_superuser`), a handful
of **deliberately unauthenticated** endpoints, and middleware
(CSRF-origin, rate-limit, security-headers) wrapping all of it.

| Router | ~ops | Auth | Prime test targets |
|---|---|---|---|
| `/images` | 43 | user | upload validation, IDOR on image_id, signed-URL forgery, EXIF/metadata leakage, transcode/HLS path handling, pagination caps |
| `/vault` | 25 | user + **2 public** | crypto contract, base64-bound gate, IDOR on item/folder/grant/link ids, **`GET /vault/public/{token}` + `/public/{token}/file` are UNAUTH**, share/recipient-key enumeration |
| `/admin` | 23 | **admin/superuser** | privilege-escalation to reach it, IDOR/role checks on every op, the `system_probes` (psutil) surface, user-impersonation/quota/delete |
| `/auth` + `/users` | 22 | mixed | brute-force, lockout bypass, token replay (reset/verify/magic-link), SSO state CSRF + account-linking takeover, session fixation, user-enumeration via timing/response diffs |
| `/account` | 17 | user | export (data-exfil scope), schedule-delete/cancel window, email-change re-verify, consent toggles |
| `/cloud` | 16 | user | OAuth state, SSRF on provider fetch, token confidentiality, quota bypass, rclone arg injection |
| `/folders` | 9 | user | IDOR, cycle/ტree abuse, tag joins |
| `/consent` | 8 | user | bypass → does AI run without consent? (CS5 regression) |
| `/people`,`/faces` | 10 | user | BIPA/biometric data IDOR (D2 regression: image_persons write) |
| `/shares` | 5 | user + **public** | signed share links, recipient binding (U3/S4), claim flow, comments |
| `/storage` | 5 | user | quota math, free-originals/variants actions, combined-pool accounting |
| `/billing` | 5 | user + **webhook** | **Stripe webhook signature forgery**, price/tier tampering, IDOR on subscription |
| `/tags`,`/search`,`/comments`,`/feedback`,`/health` | ~10 | mixed | search injection/ReDoS, comment XSS stored, health info-leak |

Pull the live, exact list from `GET /openapi.json` on the running API.

## C. Authentication & brute-force playbook (exact limits)

Defenses live in `backend/security.py` + `backend/config.py`. **Known values
to design around:**

- **Per-IP auth burst:** `auth_rate_limit_per_minute = 5` (60-second window) on
  `SecurityControlsMiddleware`-matched auth endpoints (login, TOTP login,
  password-reset, magic-link, etc.).
- **Failure lockout:** after `auth_lockout_failures = 5` failures in a **24h
  window**, exponential backoff `auth_lockout_base_seconds(60) * 2^min(n-5,4)`
  capped at `auth_lockout_max_seconds(900s)` → effective lock steps **60s →
  120s → 240s → 480s → 900s**.
- **Lockout key = identity + IP** (A5 fix) — designed so attacking one victim
  doesn't lock everyone, and so failed attempts from one IP across many
  accounts still throttle. **Test:** can you bypass by rotating
  `X-Forwarded-For` / `CF-Connecting-IP`? (Should fail unless the origin is NOT
  locked to Cloudflare — see §13; this is the #1 lockout-bypass to verify.)
- **TOTP / 6-digit codes count toward lockout** (a previously-fixed loophole) —
  re-verify failed 2FA + magic-code attempts increment the counter.
- **JWT:** lifetime capped at 7 days; `tv` (token_version) claim revokes all
  sessions when bumped. **Test:** does an old JWT survive a password reset / 2FA
  disable? (Should not.) Can you forge/strip the `tv` claim or downgrade the
  signing alg? Inspect the cookie JWT for `alg`, `exp`, `tv`.
- **Brute-force targets to enumerate:** login password, TOTP 6-digit
  (10^6 space — confirm lockout makes it infeasible), recovery codes,
  magic-link 6-digit code, password-reset token, email-verify token,
  **vault public-link passwords** (offline, see §F).
- **User enumeration:** compare timing + response bodies for
  existing-vs-nonexistent emails on login / forgot-password / register /
  `/vault/recipient-key`. (`/vault/recipient-key` returns 404 for unknown email
  — confirm it's rate-limited at **60/hr/user** and doesn't leak existence via
  timing.)
- **Session:** test fixation (does the cookie rotate on login?), `Secure` +
  `SameSite` + `HttpOnly` flags in every deploy mode, logout invalidation.

## D. Injection & web-vuln test matrix

| Class | Where to aim | Existing defense | What to try |
|---|---|---|---|
| **SQLi** | All query params, search, filters, admin lookups | SQLAlchemy 2.0 parameterized everywhere; **raw SQL is limited to the per-request `SET app.current_user_id` GUC** (RLS) and migrations | Fuzz search/filter/sort params; specifically probe whether `app.current_user_id` can be influenced by any client value (a true RLS break) |
| **Command injection** | ffmpeg (`transcode.py`/`ffmpeg_args.py`), rclone (`rclone_wrapper.py`), exiftool | args built as lists (no shell); ffmpeg `-protocol_whitelist` set | Craft filenames/playlists/provider paths with shell metacharacters, `file:`/`http:` URLs inside media containers, rclone remote-name injection |
| **SSRF** | cloud-sync provider fetches, Nominatim reverse-geocode (`name_suggest.py`/geocoder), ffmpeg playlists, any URL the server fetches | ffmpeg protocol allow-list; review needed elsewhere | Point provider/redirect/geocode inputs at `169.254.169.254`, internal hosts, `minio:9000`, `redis:6379`, `postgres:5432`; test redirect-follow |
| **Path traversal** | object storage keys, transcode/HLS temp files, any filename echoed to disk | storage keys are **server-generated** (`{user_id}/{uuid}`), clients never name them | Try `../`, null bytes, absolute paths in upload filenames + folder names; confirm temp-file dirs are isolated |
| **Stored/Reflected XSS** | comments, display_name, tags, file/folder names rendered in SPA; marketing waitlist fields; **decrypted Vault content rendered in the viewer** | React escapes by default; the public viewer **refuses to inline-render HTML/SVG** from decrypted blobs (download-only); PDFs sandboxed iframe | Inject `<img onerror>`/`<svg>`/`javascript:` into every text field; test the file-preview path (a malicious HTML file in the vault) and the `/v/{token}` viewer; check CSP presence + `dangerouslySetInnerHTML` |
| **CSRF** | all cookie-authenticated state changes | `CsrfOriginMiddleware` (Origin/Referer) + `SameSite` cookie | Forge cross-origin POST/PUT/DELETE with/without Origin; test `SameSite` edge cases, `null` origin, sub-domain |
| **Open redirect** | reset/verify/magic-link landing `?next=`, SSO callback redirect | CodeQL open-redirect finding remediated | Re-test `next`/redirect params for `//evil.com`, `\/\/`, `%2f%2f`, data:/javascript: |
| **Header injection / response splitting** | any reflected header (filename in Content-Disposition, redirects) | — | CRLF in filenames, redirect targets |
| **ReDoS** | search/synonym/regex paths (`synonyms.py`, search) | one ReDoS finding remediated | Send pathological inputs to search + any regex-backed validator |
| **Deserialization** | ML model loading (`transformers`/`insightface` pickle), any pickle/`eval`/`yaml.load` | ships known weights only | Grep for `pickle.load`/`torch.load`/`yaml.load`/`eval`; confirm no user-controlled model path |
| **XXE / SSTI** | XML/SVG parsers, any templating | not expected | Probe `openpyxl`/`docx`/SVG ingestion for external entities; confirm no server-side template renders user input |
| **Decompression/zip bomb** | archive upload, PDF rasterize, RAW/HEIC | per-entry + total uncompressed caps, max entries/depth/ratio, max pixels (U2) | Nested zips, high-ratio entries, pixel-flood images, multi-GB PDFs |
| **Mass assignment** | any model-bound create/update (Pydantic) | explicit schemas | Send extra fields (`is_superuser`, `role`, `quota_bytes`, `user_id`, `token_version`) on register/profile/update |

## E. IDOR / broken-access-control plan

Every endpoint that takes an `{id}` (image, folder, vault item, vault folder,
share grant, public link, person, face, comment, subscription) is an IDOR
candidate. **Method:** as user A, enumerate UUIDs from your own objects, then
replay every read/update/delete against B's object IDs.

- Expect **404 (not 403)** on wrong-owner IDs — by design, to avoid existence
  oracles. A `200`/`204`/`500` on another user's object is a finding.
- Two-layer defense: app-layer `user_id == current_user` check **and** Postgres
  FORCE RLS. **Try to defeat both**: e.g., does the worker/background DB session
  set the GUC? Does any admin endpoint skip the role check? Does
  `image_persons` write re-check ownership (D2 regression)?
- Vault-specific: as a non-recipient, can you fetch `/vault/shares/{grant}/file`
  or `/vault/files/{id}`? As a non-owner, can you create/get/delete another
  user's `/vault/items/{id}/public-link`? (All should 404.)

## F. Vault crypto attack playbook (top priority)

Threat model split: **honest-but-curious server** (the design target) vs
**malicious server** (note residual risk). Attacks to attempt:

1. **Read plaintext from the server side.** Confirm the API never returns/logs
   plaintext, master password, per-file keys, or unwrapped account private key.
   Inspect DB rows + MinIO objects directly — everything should be opaque
   (nonce‖ciphertext / sealed bundles).
2. **Nonce reuse / counter overflow** in chunked file encryption (per-file key
   is random; nonce = 4-B random prefix ‖ 8-B BE counter). Try to force two
   chunks to share a nonce, or a >2^32-chunk file edge case.
3. **AAD / domain-separation confusion.** Each blob is bound to a context tag
   (`neuthek.vault.verifier.v1`, `.item.v1`, `.file.v1`, `.account-key.v1`,
   `.seal.v1`, `.publiclink.v1`). Try swapping a verifier blob for an item blob,
   a file chunk across files/indices, or replaying a sealed bundle to the wrong
   endpoint — GCM auth + AAD should reject all.
4. **Truncation / reordering** of file chunks — drop/duplicate/reorder
   ciphertext chunks; the decoder re-checks chunk count + total size and each
   chunk's index-bound AAD. Confirm it fails closed.
5. **Public-link password — offline brute-force.** Fetch the sealed blob once
   (it's served unauthenticated by token), then brute-force the password
   **offline** (PBKDF2-SHA256, **600k iters** + HKDF). Quantify the real cost
   for weak passwords; this is an inherent, documented risk — measure it.
6. **URL-fragment leakage.** The link key lives in `#…`. Verify it is **never**
   sent to the server, never appears in `Referer` to third-party assets, never
   logged, and isn't leaked by the SPA to analytics/error reporters. A fragment
   leak fully breaks a no-password link.
7. **Token entropy / enumeration.** `secrets.token_urlsafe(32)` = 256-bit.
   Confirm tokens aren't guessable/sequential and that `/vault/public/{token}`
   is rate-limited (**240/hr/token**) against scraping. Test expired/rotated
   tokens 404.
8. **Recipient-key substitution (malicious-server / MITM).** During a share,
   the sharer fetches the recipient's public key from the server
   (`users.vault_public_key`). A malicious server could serve an attacker key.
   This is **TOFU** — note as residual risk; test whether the client pins or
   verifies anything.
9. **Downgrade / parameter tampering.** Can the server force a weaker KDF iter
   count? Server bounds vault iters to **310k–5M**; public-link salt/iters are
   client-set but server-stored — try returning absurd KDF params on a public
   view and see if the client honors them.
10. **XSS-assisted key theft.** While unlocked, the master key + account private
    key live in JS memory (non-extractable CryptoKey limits export but not
    *use*). Any XSS ⇒ attacker can call decrypt/seal in-page. This makes §D XSS
    findings **critical**, not medium.

## G. Rate-limit / DoS / resource-exhaustion (exact caps)

- **Auth:** §C (5/min burst, lockout 5-fail/24h).
- **Uploads:** per-file **200 MB**, **300 uploads/hour**, **10 GB/day** (free
  floor; higher tiers via `plans`). Vault per-file cap **5 GB**.
- **Vault writes:** `vault:write` **300/hr**; setup **5/hr**; account-key
  **5/hr**; share **100/hr**; recipient-key lookup **60/hr**; public view/file
  **240/hr per token**; wipe **5/hr**.
- **Expensive work:** ML inference jobs (Florence-2/Whisper) are queued
  per-user — test queue-flooding, oversized media to exhaust the worker, and
  whether one user can starve others. Decompression bombs (§D). Pixel-flood
  images vs `upload_max_image_pixels`.
- **Redis dependency:** rate-limits + the job queue + lockout counters all live
  in Redis. **`require_redis_when_production()`** refuses to boot without it —
  but test the failure mode if Redis dies *at runtime* (do limits fail open or
  closed?).
- **Algorithmic:** ReDoS (§D), unbounded pagination (capped, D1 — re-test),
  large `limit`/`offset`, deeply nested folder trees / share graphs.

## H. Business-logic abuse

- **Quota bypass:** the Drive + Vault now share **one** quota pool
  (`compute_used_bytes_fast`). Try to exceed it by racing concurrent uploads,
  mixing Drive + Vault + cloud-sync ingest, or exploiting the variants/originals
  accounting (CR-4 was a real bypass here).
- **Sharing abuse:** can a recipient re-share? Can a revoked grant/link still
  fetch ciphertext (revocation must be immediate)? Can you create a public link
  on an item you don't own? Does deleting an item truly cascade-kill its grants
  + links + MinIO blobs?
- **Billing:** **forge Stripe webhooks** (signature verification — `billing.py`
  imports Stripe lazily; confirm `stripe_webhook_secret` is enforced and events
  are idempotent via `stripe_events`). Tamper price/tier IDs to upgrade for
  free; IDOR on subscription/customer.
- **Consent:** disable AI consent, then trigger an action that would normally
  run AI — confirm nothing runs (CS5). Toggle consent races.
- **Account deletion:** the schedule-delete grace window + the nightly sweep —
  test cancel-after-delete, and that a deleted account's blobs/rows/grants are
  fully removed (A5 full-deletion test exists).

## I. Tooling & commands

- **Proxy/fuzz:** Burp Suite / OWASP ZAP (cookie-jar the `neuthek_session`).
- **API enumeration:** `GET /openapi.json` → import into Burp/Postman; drive
  every operation.
- **SQLi:** `sqlmap` against search/filter/sort params (cookie-authenticated).
- **Content/endpoint discovery:** `ffuf`/`feroxbuster` (note the
  unauthenticated `/vault/public/*`, `/v/*`, `/share/*`, `/health`, marketing).
- **Vuln templates:** `nuclei` (CVEs in the native deps — pin-check Pillow,
  PyMuPDF, ffmpeg, rawpy/LibRaw, torch at audit time).
- **SAST:** `semgrep --config p/python p/react p/owasp-top-ten`; the repo also
  ships CodeQL workflows + Dependabot — re-run on the audit branch.
- **Container/IaC:** `trivy image neuthek-backend:latest`,
  `trivy fs .`, `hadolint Dockerfile`, `dockle`; review `docker-compose*.yml`
  for exposed ports + secrets.
- **Crypto:** bundle `frontend/src/vault/crypto.ts` with esbuild and exercise
  the primitives under Node for an independent review; inspect raw DB rows +
  MinIO objects to confirm opacity.
- **TLS/headers:** `testssl.sh`, `nikto`, check HSTS/CSP/`X-Frame-Options`/
  `Permissions-Policy` from Caddy + `SecurityHeadersMiddleware`.

## J. Non-obvious behaviors & "tricks" worth abusing

- **404-not-403 everywhere** for wrong-owner/missing objects → no existence
  oracle, but also means a successful cross-tenant op would be silent; assert on
  body/side-effects, not just status.
- **Vault base64 gate:** the server validates strict base64 + **exact byte
  bounds** on every binary field (nonce = 12B, pubkey = 65B, etc.). Fuzz with
  off-by-one lengths, non-canonical base64, oversized blobs (256 KB item /
  metadata cap, 5 GB file cap, 512 B wrapped-key cap) to find a validation gap.
- **Public-link one-time reveal:** the full link URL is shown **once** at
  creation because the server never stores the fragment secret — confirm the
  secret truly isn't recoverable server-side later (a "regenerate that returns
  the same URL" would be a finding).
- **Rate-limits no-op in dev/test:** if `SECURITY_RATE_LIMITS_ENABLED=false` or
  Redis is absent in a misconfigured prod, **all brute-force protection
  silently disappears** — verify the prod config actually enforces it
  (`validate_production_settings`).
- **Edge IP trust:** `CF-Connecting-IP` is trusted only behind locked-down
  Cloudflare. If the origin is reachable directly (not firewalled to CF
  ranges), an attacker can **spoof the client IP header** and bypass per-IP
  limits + poison audit logs. Test direct-to-origin reachability.
- **Edge routing:** the hosted `deploy/Caddyfile` enumerates API prefixes
  explicitly; `/billing` is intentionally not blanket-proxied (page collision).
  Probe for **path-confusion** (does any API path get served by the SPA, or any
  SPA route reach the API?).
- **`/admin` is both a page and an API prefix** (`/admin` SPA page vs
  `/admin/*` API) — test the boundary for path confusion / auth gaps.
- **Worker DB sessions** operate as the data owner — confirm a crafted job
  payload can't read/write across users.

---

*End of dossier. Part I is the architecture/asset/threat-model reference; Part
II is the offensive testing playbook. Pair both with read access to the
repository and a staging deploy with rate-limits ENABLED. Every claim above is
traceable to the module paths and config keys cited; the testing team should
treat the "known issues / residual risks" (§7.5, §17, Part II) as starting
hypotheses to confirm or refute, not as an exhaustive list.*
