# neuthek

neuthek is an **AI-aware personal cloud** built around three ideas:
find things by what you remember (semantic search via CLIP cosine on
embeddings), pay attention to privacy from the start (per-user
Postgres Row-Level Security, opt-in AI, consent-gated biometrics,
hard account deletion), and run on hardware you control (Docker
Compose self-host today; hosted launch later).

The product isn't released publicly yet. The hosted version
launches first; an open-source self-host build follows with no
committed date — the codebase is being cleaned up for public
release. Until then this repository is the working development
tree.

<table>
  <tr>
    <td><strong>Core Stack</strong><br><br>
      <kbd>FastAPI · Python 3.12</kbd>
      <kbd>PostgreSQL 16 + pgvector</kbd>
      <kbd>Redis 7</kbd>
      <kbd>MinIO (S3-API)</kbd>
      <kbd>Caddy (TLS)</kbd>
      <kbd>Alembic</kbd>
      <kbd>Docker Compose</kbd>
      <kbd>React 18 + Vite</kbd>
      <kbd>TanStack Query v5</kbd>
      <kbd>Zustand</kbd>
      <kbd>Leaflet + supercluster</kbd>
      <kbd>Prism</kbd>
      <kbd>Pytest</kbd>
    </td>
  </tr>
  <tr>
    <td><strong>AI / ML Models</strong><br><br>
      <kbd>OpenCLIP ViT-L-14 (semantic search)</kbd>
      <kbd>Florence-2-large (captions + OCR)</kbd>
      <kbd>Qwen2.5-Instruct (summaries)</kbd>
      <kbd>RetinaFace + ArcFace via insightface (opt-in faces)</kbd>
      <kbd>rawpy / LibRaw (camera RAW decode)</kbd>
      <kbd>LinUCB contextual bandit (compression policy)</kbd>
      <kbd>WordNet (search synonyms)</kbd>
    </td>
  </tr>
  <tr>
    <td><strong>Shipped In Engine</strong><br><br>
      <kbd>JWT auth + TOTP 2FA + recovery codes</kbd>
      <kbd>Google SSO</kbd>
      <kbd>Encryption at rest + TLS in transit</kbd>
      <kbd>FORCE Row-Level Security per user</kbd>
      <kbd>Hard account deletion (every byte, tested)</kbd>
      <kbd>One-click portable ZIP export</kbd>
      <kbd>30-day Trash + 30-day account-delete grace</kbd>
      <kbd>Append-only audit log</kbd>
      <kbd>Consent before signup</kbd>
      <kbd>EXIF / GPS stripped by default</kbd>
      <kbd>Per-user fair scheduling + rate limits</kbd>
      <kbd>Encrypted backups (age sidecar)</kbd>
      <kbd>Hybrid CLIP + FTS search</kbd>
      <kbd>Multi-axis gallery filtering</kbd>
      <kbd>Best Of picker (sharpness + exposure + face + CLIP)</kbd>
      <kbd>Inline preview surface (PDF / code / RAW / GPS pin)</kbd>
      <kbd>Real folders + per-user tags</kbd>
      <kbd>Opt-in face clustering + recognition (BIPA-grade)</kbd>
      <kbd>Google Drive + GitHub cloud sync</kbd>
      <kbd>Sharing primitive (signed, expiring links)</kbd>
      <kbd>Stripe billing (Free / Pro / Business)</kbd>
      <kbd>Email verification on waitlist</kbd>
      <kbd>Newsletter broadcast (RFC 8058 unsubscribe)</kbd>
      <kbd>Admin overlay + Queue + Developer tabs</kbd>
      <kbd>Cross-vendor accelerator dispatch (CUDA / XPU / MPS)</kbd>
    </td>
  </tr>
</table>

## Contents

- [What neuthek does today](#what-neuthek-does-today)
- [Repository layout](#repository-layout)
- [The stack and why](#the-stack-and-why)
- [The AI / ML models and why](#the-ai--ml-models-and-why)
- [Data and storage model](#data-and-storage-model)
- [Privacy and security posture](#privacy-and-security-posture)
- [Compliance status](#compliance-status)
- [Setup](#setup)
- [Common operations](#common-operations)
- [API surface](#api-surface)
- [Marketing site](#marketing-site)
- [Tests](#tests)
- [What's still being built](#whats-still-being-built)
- [Contributing](#contributing)
- [Security disclosure](#security-disclosure)
- [License](#license)

## What neuthek does today

| Surface | Behavior |
| --- | --- |
| **Accounts** | Registration, email verification, JWT login, TOTP 2FA, recovery codes, Sign in with Google (with link-to-existing flow). FastAPI Users underneath. |
| **Uploads** | Per-user authenticated POST. MIME + magic-byte + re-decode validation. Polyglot trailer strip. EXIF GPS + camera metadata stripped at upload by default; per-scope consent to retain. Drag-folder + archive (zip / tar / optional 7z + RAR) upload paths. |
| **Compression** | Content-aware. LinUCB contextual bandit picks codec (WebP / MozJPEG / AVIF / JXL) and quality (55-92) from a 32-dim feature vector. Detected screenshots fall to lossless WebP. Animated GIF passthrough. Camera RAW (NEF/CR2/ARW/DNG/RAF/ORF/RW2/PEF) decoded via rawpy + LibRaw and re-encoded at JPEG q95 for thumbnails. |
| **Storage** | MinIO S3-API: originals / served / faces / quarantine buckets, SSE-S3 or SSE-KMS at rest (per-bucket KMS key IDs for content vs. biometric). |
| **Database** | PostgreSQL 16 + pgvector. 33 Alembic migrations. FORCE Row-Level Security on every per-user table (biometrics, geo, consents, share grants, recovery codes, bandit state, image_persons). |
| **Search** | Hybrid CLIP + Postgres FTS, CLIP-led (0.65 CLIP / 0.35 FTS). WordNet + visual-domain synonym expansion. Live-as-you-type (280 ms debounce). |
| **Multi-axis filtering** | Gallery filters compose scene / indoor-outdoor / content type / tag / person / date range / location radius / starred. Filter state persists in URL. |
| **Best Of picker** | Multi-select 2–30 photos. Backend scores each on sharpness (OpenCV Laplacian) + exposure + face quality + optional CLIP cosine to a use-case prompt (25 presets + free text). Three modes: overall, burst (CLIP-clustered), use-case. "Keep this one" moves the rest to soft Trash. |
| **Inline preview** | Image lightbox (zoom + arrow-key nav), multi-page PDF stack with lazy page rasterization, syntax-highlighted code preview for ~40 languages (Prism), GPS pin with reverse-geocoded place name, tag / star / share from the same panel. |
| **Faces (opt-in)** | RetinaFace detection + ArcFace 512-dim embeddings + per-user clustering. BIPA-grade: signed-consent ledger, three-year auto-expiry of unrelated templates, immediate deletion on revoke. The Person row labelled "Me" auto-binds to your account display name. |
| **Folders + tags** | First-class folders with drag-drop multi-select moves. AI-suggested smart filenames. Per-user tag system (18 named chip colours, status-as-tag unified). |
| **Cloud sync** | Google Drive (read-only scope, PKCE OAuth, Fernet-encrypted refresh token, hourly background sweep, conflict banner, AI fenced out per Google Limited Use). GitHub (own repos, image files, secret-pattern skip list). |
| **Sharing** | Per-image grants with email pinning, hashed share tokens, server-enforced 1-day cap for unverified recipients, signed expiring URLs, full audit trail, one-click revoke. |
| **Map view** | Leaflet on CartoDB Voyager / DarkMatter tiles. supercluster spatial index (with O(N×K) pixel-space fallback). Click-cluster-to-zoom via `getClusterExpansionZoom`. Subtle grid backdrop during tile transitions. |
| **Trash + delete** | 30-day soft-delete recovery. Bulk Delete-forever path purges originals + every derived row via `backend/deletion.py`. Account delete schedules 30-day grace; sweeper hard-deletes after. |
| **Export** | One-click ZIP of the full library (files + summaries + embeddings + persons + faces + consents + audit). Rate-limited to one full export per 24h. |
| **Billing** | Stripe Embedded Checkout (Free / Pro / Business), signature-verified webhooks, Customer Portal handoff. Empty Stripe env shorts `/billing/*` to 503 in dev. |
| **Operator tooling** | Admin overlay (system / hardware / processes / models / tasks / logs / Queue / Developer / Recipients / Activity). Live capacity calculator. Per-user fair-queue depth + rate-limit headroom + Drain button. |

## Repository layout

| Path | What it contains |
| --- | --- |
| `backend/` | FastAPI app, models, migrations, vision pipeline, jobs, security, consent, signed URLs, deletion, retention, audit, billing. |
| `backend/api/` | HTTP routers (images, search, people, folders, tags, shares, account, admin, billing, consent, cloud, geo, etc.). |
| `backend/vision/` | OpenCLIP / Florence-2 / RetinaFace / ArcFace runtime, inference pool, classifiers. |
| `frontend/src/` | Shared API client + type definitions (`api/files.ts`, `api/people.ts`, `api/tags.ts`, `api/shares.ts`, etc.). |
| `frontend/neuthek/src/` | The actual user-facing React app — gallery, preview, map, upload modal, people view, admin overlay, account, share modal, best-of, etc. Vite-served (port 5174 dev). |
| `marketing/` | Public marketing site (React + Vite + small Express prerender). Independent from the app. |
| `migrations/versions/` | Alembic migrations (0001 → 0033). |
| `scripts/setup.py` | Stdlib-only one-shot setup: platform detect, GPU probe, fresh secret generation, `.env` write, docker/native install picker. |
| `scripts/setup.{sh,ps1}` | Platform-specific bootstrap wrappers around `setup.py` + dependency installs. |
| `scripts/backup-db.sh` / `.ps1` | Encrypted `pg_dump` → age → offsite + local. |
| `tests/` | pytest suite. Auth, RLS, deletion, export, retention, consent, filters, search, billing, hardening. |
| `PRIVACY.md` / `TERMS.md` / `SECURITY.md` / `DATA_PROCESSING.md` | Canonical compliance documents (12-section privacy notice, source-available terms, security disclosure + supported versions, B2B DPA template). |
| `REPO_HYGIENE.md` / `AUDIT.md` / `SECURITY_REVIEW.md` | Hygiene posture + audit/review history. |
| `todo.md` | Internal development tracker — what's still open, what shipped, sprint ordering. |
| `updates.md` | Working draft of the next weekly /updates entry. Copied into `marketing/src/data/updates.ts` at publish time. |

## The stack and why

| Layer | Pick | Why this one |
| --- | --- | --- |
| HTTP layer | **FastAPI on Python 3.12** | Async by default, automatic OpenAPI at `/docs`, Pydantic v2 for request/response validation that doubles as published schema. Same runtime as the ML side — no IPC between API and inference. |
| Source of truth | **PostgreSQL 16 + pgvector** | Vectors and ACID in one transaction. Deleting a row deletes its embedding atomically. FORCE Row-Level Security per user — even an app handler bug can't leak another tenant's row. |
| Queue + rate-limit | **Redis 7** | Cheap atomic LPUSH/BRPOP for the per-user fair scheduler; cheap atomic INCR with EXPIRE for rate-limit counters. Ephemeral state stays out of Postgres. |
| Object storage | **MinIO (S3 API)** | Wire-compatible with AWS / R2 / Backblaze, so swapping deployments is a config change. Runs anywhere — NUC, Pi, Kubernetes — same binary. SSE-S3 and SSE-KMS supported. |
| Edge | **Caddy** | Automatic Let's Encrypt + HSTS + HTTP/3. One config file. Defaults tighter than most hand-rolled nginx. |
| Auth | **fastapi-users + JWT + TOTP + Argon2id** | Production-tested, plugs into FastAPI's DI natively. We layer a consent-bundle hook on top of the user-create path without forking. |
| At-rest secrets | **Fernet via cryptography** | Authenticated encryption, no nonce-reuse footguns, key rotation built in. Wraps OAuth refresh tokens and TOTP secrets. Boot-time validator refuses prod start without `CLOUD_ENCRYPTION_KEY`. |
| Frontend | **React 18 + Vite + TanStack Query v5** | Vite HMR, TanStack Query removes most reasons to reach for Redux, React 18 transitions/Suspense are load-bearing for the gallery filter chips and preview surface. |
| Code preview | **Prism (~40 grammars)** | Highlight.js auto-detect mis-fires on small files; Prism's per-language grammars are explicit, ship as small modules, and we eager-load the 40 covering the source files people store in personal clouds. |
| Map | **Leaflet + supercluster** | Open-source, theme-aware tile layers from CartoDB. supercluster is the standard fast spatial-clustering index. Dynamic import + fallback so missing install doesn't crash the dev server. |
| Compose | **Docker Compose** | Self-host is the headline deployment story. Compose is the lowest-friction multi-container orchestration on a single host. Optional overlays (TLS, encrypted backups, Intel iGPU/NPU passthrough) layer in without touching the base. |
| Billing | **Stripe (Embedded Checkout)** | We never see a card number, no PCI scope. Empty env shorts billing endpoints in dev so self-host doesn't need it. |
| Tests | **pytest + pytest-asyncio** | Used to prove security properties end-to-end (upload + delete + assert 0 rows + 0 objects; cross-user data leak attempts return empty; full deletion across every table / bucket / cache). |

## The AI / ML models and why

Every model is pre-trained with **frozen weights**. We never fine-tune on user data.

| Model | Role | Why this one |
| --- | --- | --- |
| **OpenCLIP ViT-L-14** | Per-image 768-dim embedding for semantic search. Query text embeds into the same space; pgvector cosine ranks. | Open re-implementation of CLIP trained on LAION-2B — same architecture as OpenAI's closed CLIP, but openly licensed. ViT-L-14 is the sweet spot between accuracy and inference cost on a single consumer GPU. |
| **Florence-2-large** | Detailed image captions + inline OCR (scene-gated). | Purpose-built vision model from Microsoft Research: caption, detect, segment, OCR — all in one. Apache-2.0. Noticeably more concrete captions than BLIP-2 ("auth-flow review on a whiteboard" vs. "person near a whiteboard"). Small enough that 8/4-bit quant paths are tractable for self-hosters. |
| **Qwen2.5-Instruct** | Rewrites raw captions into human-readable summaries; document summarization via map-reduce over chunks. | Better instruction-following than Llama-3.2 at the same parameter count. Apache-2.0. Ships in 0.5B / 1.5B / 3B / 7B variants so operators can pick by hardware. The 1.5B variant runs at acceptable latency on CPU; the 4-bit GGUF path is well-trodden. |
| **RetinaFace + ArcFace (insightface buffalo_l)** | Opt-in face detection + 512-dim face embeddings + per-user clustering. | Reference open implementation. ArcFace's angular margin loss outperforms FaceNet's triplet loss on standard benchmarks and embeddings are stable across age + lighting. buffalo_l bundles both with consistent preprocessing. |
| **rawpy + LibRaw** | Camera RAW decoding (NEF / CR2 / ARW / DNG / RAF / ORF / RW2 / PEF). | LibRaw is the open standard dcraw evolved into. Pillow's RAW support is read-only and only the embedded preview. We re-encode the full sensor image at JPEG q95 so the gallery shows what your camera actually captured. |
| **LinUCB contextual bandit** | Picks codec + quality per image from a 32-dim feature vector. | Fixed "JPEG q85" is mediocre on everything. A neural policy would need per-user training data. LinUCB is the contextual-bandit baseline that learns online, converges in dozens of impressions per arm, and stays explainable. Per-user telemetry; we never share arm state across users. |
| **WordNet via NLTK** | Search synonym expansion (e.g. "vibrant" → "colorful, vivid, bright, saturated"). | CLIP handles semantic stretch; the FTS pass is token-exact and benefits from explicit synonym expansion. WordNet is offline (no API), tiny (~10 MB), exhaustive for English nouns/adjectives, and the long-standing standard. A small visual-domain overlay layers on top for photographer-sense synonyms WordNet doesn't link well. |

## Data and storage model

| Category | Storage | Notes |
| --- | --- | --- |
| Users | Postgres `users` | Email, Argon2id password, `display_name`, role (`user` / `admin` / `superuser`), TOTP state, `is_verified`, `age_confirmed`, `scheduled_delete_at`, `google_sub`. |
| Images | Postgres `images` | Ownership, blob keys, dimensions, MIME types, byte sizes, SHA-256, codec settings, `uploaded_at`, `captured_at` (EXIF DateTimeOriginal), `original_expires_at`, `deleted_at`, `is_starred`, `starred_at`, summary text + topic + points + signals, scene/content/indoor-outdoor labels, CLIP embedding, face likelihood, folder reference, source provider (for cloud-synced files). |
| Originals | MinIO `originals` bucket | Raw uploaded bytes per-user. Configurable retention (default 30d). EXIF stripped before storage unless `exif_retention` granted. |
| Served | MinIO `served` bucket | Compressed delivery variant. WebP / MozJPEG / AVIF / JXL per bandit decision. |
| Face crops | MinIO `faces` bucket | 256×256 per detected face. Only populated when `face_recognition` consent is granted. |
| Quarantine | MinIO `quarantine` bucket | Rejected uploads (failed validation). Swept by `sweep_expired_quarantine`. |
| GPS | Postgres `image_geo` | `lat`, `lng`, `taken_at`, `captured_with`, reverse-geocoded `place`. Only populated when `gps_retention` granted. |
| Persons | Postgres `persons` | Per-user named persons with `face_count` + sample face id. |
| Faces | Postgres `faces` | 512-dim ArcFace embedding per face, optional `person_id`, `cluster_id` for unlabelled. |
| Face detections | Postgres `face_detections` | Per-detection bbox + confidence + landmarks + crop blob key. |
| Manual person tags | Postgres `image_persons` | Direct M:N link between images and persons (multi-select tag flow), independent of face detection. |
| Tags | Postgres `tags` + `image_tags` + `folder_tags` | Per-user labels with 18 named chip colors. RLS-forced. |
| Folders | Postgres `folders` | First-class folders with parent/child + position. |
| Shares | Postgres `share_grants` | Per-(image, recipient) row with email pin + hashed token + expiry + audit trail. |
| Audit | Postgres `audit_log` | Append-only via trigger. Anonymizable via `user_id → NULL` transition only. |
| Consent | Postgres `consent_records` | One row per grant/withdraw. Timestamp + IP + UA + scope + policy version + signature. |
| Recovery codes | Postgres `recovery_codes` | 8 single-use Argon2id-hashed codes per user. |
| Bandit state | Postgres `bandit_state` + `bandit_global_prior` | Per-user compression policy weights + history. |
| Cloud sync | Postgres `cloud_links` + `cloud_files` | Per-provider OAuth state, Fernet-encrypted refresh tokens, mirrored file index. |
| Subscriptions | Postgres `subscriptions` + `stripe_events` | Tier, period end, dunning state. Idempotent webhook handling. |

## Privacy and security posture

| Concern | What's in place today |
| --- | --- |
| **No AI training on user data** | Hard guarantee. Every model uses pre-trained frozen weights — never fine-tuned on user content. |
| **No ads, no data sales** | Hard guarantee. The business model is paid hosted + free self-host. |
| **Per-user isolation** | Postgres FORCE Row-Level Security on every multi-tenant table. RLS context set via `set_current_user_id()` in every auth dependency. Cross-user data-leak test enforces this in CI. |
| **Encryption at rest** | Postgres host-volume encryption + per-deployment attestation. MinIO SSE-S3 or SSE-KMS (per-bucket KMS key IDs split biometric vs. content). Fernet for OAuth refresh tokens, TOTP secrets, and other in-row secrets. Encrypted backups via age sidecar. |
| **Encryption in transit** | TLS via Caddy + auto-Let's Encrypt + HSTS + HTTP/3. `docker-compose.tls.yml` overlay. Boot-time validator refuses unsafe production starts. |
| **Authentication** | Argon2id password hashing, JWT with TOTP 2FA, recovery codes, brute-force lockout with exponential backoff. Sign in with Google with link-to-existing flow. |
| **Signed URLs** | Download URLs clamped to ≤ 5 min TTL by `make_signed_download`. `verify_download` rejects anything past the cap. |
| **Audit log** | Append-only via Postgres trigger. UPDATEs raise except the single permitted `user_id → NULL` transition for the anonymization sweeper. |
| **Account deletion** | `backend/deletion.py::hard_delete_images` is the single source of truth. Covers originals + served + thumbnails + face crops + CLIP embeddings + AI summaries + EXIF / GPS row + face detections + face embeddings + persons + tags + shares + feedback events + cloud_files + (opt-in) bandit state. Audit rows preserved by design. Integration test asserts 0 rows + 0 objects after delete. |
| **Account-delete grace** | 30-day window from `scheduled_delete_at` before the sweeper purges. `/account/cancel-delete` un-schedules. |
| **Export** | One-click ZIP of files + summaries + embeddings + persons + faces + consents + audit. Rate-limited per 24h. |
| **Consent** | Six scopes (`gps_retention`, `exif_retention`, `ai_summary`, `semantic_search`, `face_recognition`, `bandit_compression_telemetry`). Each writes a row to `consent_records` with timestamp + IP + UA + signature + policy version. Face recognition uses a BIPA-grade explicit grant. |
| **Consent before signup** | Register payload carries consent bundle. `UserManager.create` writes ConsentRecord rows in the same transaction. |
| **EXIF / GPS** | Stripped from originals at upload by default. Opt-in `exif_retention` to keep camera metadata + `DateTimeOriginal`. Opt-in `gps_retention` to keep lat/lng + reverse-geocoded place. |
| **Upload validation** | MIME + magic-byte sniffer + format-specific re-decode before storage. Polyglot trailer strip. Archive walk with depth + expansion-ratio caps. Failed uploads land in the quarantine bucket with an audit row. |
| **No cookies** | Backend never sets a `Set-Cookie` header (test fails the build if it does). JWT lives in `Authorization: Bearer …`. CSRF against the API is structurally impossible. |
| **localStorage** | Theme, recent searches, cookie-banner state, JWT (cleared on sign-out). |
| **Rate limits** | 5/min/IP on auth endpoints, exponential lockout on failures. Per-user hourly caps on every heavy ML endpoint (backfill-summaries 3, backfill-vision 3, resummarize 30, redetect-faces 30, detect-and-label 10, best-of 30). |
| **Per-user fair scheduling** | ML job pipeline is a Redis FIFO per user with round-robin pull. Per-user in-flight cap of 1. Per-user queue cap of 1000. |
| **No cross-user vector leak** | Every CLIP / face query scoped to the authenticated owner. RLS at the DB layer is the defense-in-depth backstop. |
| **CI hygiene** | gitleaks against full history on every PR, real-PII-filename forbid, binary-fixture forbid under `tests/`. Pre-commit hook runs the same locally. 8 hygiene contracts enforced as pytest invariants. |

## Compliance status

Cross-references for an external auditor.

| Required | Where |
| --- | --- |
| Privacy Policy | [PRIVACY.md](PRIVACY.md) — 12 sections, A6-shipped |
| Terms of Service | [TERMS.md](TERMS.md) |
| Data Processing Agreement | [DATA_PROCESSING.md](DATA_PROCESSING.md) — B2B DPA template, 12 sections |
| Security policy | [SECURITY.md](SECURITY.md) — disclosure email + supported versions + prod checklist |
| Documented deletion process | PRIVACY.md §7 + `tests/test_a5_full_deletion.py` proof |
| Backup strategy | SECURITY.md "Encrypted backups → Retention" |
| HTTPS | `docker-compose.tls.yml` + Caddyfile, boot-validated |
| Strong secrets | `validate_production_settings` rejects unsafe deployments at boot |
| Consent log | `consent_records` table + B2 register-bundle backend path |
| Age gate | Explicit "I am at least 13" checkbox in `consents.jsx`, FE block + backend validator |
| External review | Booked before public launch |

## Setup

### Prerequisites

- Python 3.12+
- Node.js 20+ (for the frontend dev server)
- Docker + Docker Compose
- Optional: an NVIDIA / AMD / Apple Silicon / Intel GPU for the ML pipeline (CPU works, just slower)

### One-shot

```bash
python scripts/setup.py
```

The script is stdlib-only — it runs before any venv exists. What it does:

1. **Detects** Windows / Linux / macOS, available drives, GPU (CUDA / ROCm / Apple Metal / Intel XPU) with the right torch wheel `--index-url` hint.
2. **Generates** four fresh secrets: `JWT_SECRET` (48-byte URL-safe), `POSTGRES_PASSWORD` + `MINIO_SECRET_KEY` (24-byte URL-safe), `CLOUD_ENCRYPTION_KEY` (Fernet-compatible 32-byte → base64).
3. **Writes** a 56-key `.env` covering every variable the backend reads (DB, Redis, MinIO buckets + SSE, Fernet, Google / GitHub OAuth, Stripe, Resend, SMTP, rate-limit knobs).
4. **Brings up** the stack via `docker compose up -d`, or prints a per-platform native install checklist (apt / dnf / pacman / brew / Windows installer links) + a binary-on-PATH check.

Flags:

| Flag | Effect |
| --- | --- |
| `--yes` | Non-interactive; accepts all defaults; picks docker. |
| `--reset` | Overwrites existing `.env` (regenerates every secret — local data may need re-init). |
| `--data-dir PATH` | Overrides the interactive "where should neuthek store data?" prompt. |
| `--mode docker` | Skips the picker and runs `docker compose up -d`. |
| `--mode native` | Prints the install checklist instead of running docker. |
| `--mode skip` | Skips both — assumes services are running elsewhere. |
| `--no-stack` | Skips both stack paths entirely. |

### Manual setup

If you'd rather wire it up yourself:

```bash
# Backend deps
pip install -e ".[dev,ml]"     # ml extras = OpenCLIP + Florence-2 + RetinaFace etc.
docker compose up -d           # postgres + redis + minio
python -m alembic upgrade head # apply migrations (currently 33)
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000

# Frontend
cd frontend
npm install
npm run dev                    # http://localhost:5174
```

### Environment variables

The script generates these with sensible defaults; every key is also documented inline in the resulting `.env`. Highlights:

| Variable | Purpose |
| --- | --- |
| `APP_ENV` | `dev` / `prod`. Prod triggers the security boot validator. |
| `FRONTEND_BASE_URL` | Where the SPA is served (used in email links). |
| `DATABASE_URL` / `DATABASE_URL_SYNC` | Async + sync Postgres URLs. |
| `REDIS_URL` | Per-user fair queue + rate-limit counters. |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | MinIO API. |
| `MINIO_BUCKET_*` | Bucket names for originals / served / faces / quarantine. |
| `MINIO_SSE_MODE` / `MINIO_SSE_KMS_KEY_ID_*` | Object-storage encryption at rest. |
| `JWT_SECRET` / `JWT_LIFETIME_SECONDS` | JWT signing + lifetime. |
| `CLOUD_ENCRYPTION_KEY` | Fernet key for the at-rest secret box (refresh tokens, TOTP secrets). Required in prod. |
| `SECRET_MANAGER` | `env_file` / `docker_secrets` / `aws_secretsmanager`. |
| `TRUST_PROXY_HEADERS` | True behind a reverse proxy so X-Forwarded-For is honored. |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI` | Drive sync + Sign in with Google. |
| `GITHUB_OAUTH_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI` | GitHub sync. |
| `CLOUD_SYNC_HOURLY_ENABLED` / `_INTERVAL_SECONDS` | Background sweep cadence. |
| `RESEND_API_KEY` / `RESEND_FROM` | Newsletter + waitlist verification. |
| `SMTP_*` | Alternative mailer. Leave empty in dev to log emails to console. |
| `STRIPE_SECRET_KEY` / `_WEBHOOK_SECRET` / `_PRICE_ID_*` | Billing. Empty in dev → `/billing/*` returns 503. |
| `BACKUP_AGE_RECIPIENT` | age public key for the encrypted backup sidecar. |
| `AUTH_RATE_LIMIT_PER_MINUTE` / `AUTH_LOCKOUT_FAILURES` | Brute-force protection. |
| `DOWNLOAD_URL_TTL_SECONDS` / `REQUIRE_SIGNED_DOWNLOADS` | Signed-URL TTL cap. |
| `UPLOAD_MAX_BYTES` / `_COUNT_PER_HOUR` / `_BYTES_PER_DAY` / `_IMAGE_PIXELS` | Upload caps. |

## Common operations

| Task | Command |
| --- | --- |
| Start stack | `docker compose up -d` |
| Stop stack | `docker compose down` |
| Apply migrations | `python -m alembic upgrade head` |
| Run backend | `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000` |
| Run frontend (dev) | `cd frontend && npm run dev` (port 5174) |
| Run frontend (prod build) | `cd frontend && npm run build` |
| Encrypted backup | `bash scripts/backup-db.sh` (Windows: `pwsh scripts/backup_encrypted.ps1`) |
| Encrypted restore | `bash scripts/restore-db.sh ./backup.age` |
| Initialise MinIO buckets | `bash scripts/init_storage.sh` (Windows: `pwsh scripts/init_storage.ps1`) |
| Codec benchmark | `python scripts/bench_codecs.py [path/to/*.jpg]` |
| Mocked end-to-end demo | `python scripts/e2e_demo.py` |
| Force re-summarize library | `POST /images/backfill-summaries?force=true&limit=500` (or Account → AI features → Library maintenance) |
| Open API docs | `http://localhost:8000/docs` |
| Open MinIO console | `http://localhost:9001` |
| Open admin overlay | `http://localhost:5174/?admin=1` (superuser only) |

## API surface

FastAPI auto-generates the full OpenAPI doc at `/docs`. Highlights:

| Method | Path | Behavior |
| --- | --- | --- |
| `POST` | `/auth/jwt/login` | JWT login (TOTP-gated if enabled). |
| `POST` | `/auth/jwt/login-totp` | 2-factor follow-up. |
| `POST` | `/auth/register` | Account create with consent bundle. |
| `GET` | `/auth/google/login` | Google SSO start. |
| `GET` | `/users/me` | Current user + linked identities. |
| `POST` | `/account/totp/enroll` | Set up 2FA. |
| `POST` | `/account/totp/codes` | Regenerate recovery codes. |
| `POST` | `/account/export` | Portable ZIP (rate-limited). |
| `POST` | `/account/delete` | Schedule hard delete (30-day grace). |
| `POST` | `/account/cancel-delete` | Cancel scheduled delete. |
| `POST` | `/images/` | Upload. |
| `GET` | `/images/` | List with full filter surface: scene, content_type, indoor_outdoor, tag, person_id, near, taken_between, has_faces, has_gps, starred, trashed, folder_id, all. |
| `GET` | `/images/facets` | Filter chip options + counts (scenes, content types, indoor/outdoor, persons, tags, with_gps, with_faces, starred_count, date_range). |
| `GET` | `/images/{id}/original` / `/served` | Authenticated blob fetch. |
| `POST` | `/images/{id}/star` | Toggle favorite. |
| `POST` | `/images/{id}/resummarize` | Force re-caption. |
| `PATCH` | `/images/{id}/name` | Rename. |
| `DELETE` | `/images/{id}` | Soft delete (`?purge=true` → hard). |
| `POST` | `/images/bulk-{delete,restore,move}` | Bulk ops. |
| `POST` | `/images/best-of` | Score N selected on sharpness + exposure + face + use-case. |
| `POST` | `/images/backfill-{summaries,vision}` | Trigger re-processing. |
| `POST` | `/images/geo/backfill` | Re-extract EXIF GPS from existing originals. |
| `POST` | `/images/geo/backfill-places` | Reverse-geocode any pending rows. |
| `GET` | `/folders/` | Folder tree (with `?contains_type=image\|video\|doc` filter). |
| `POST` | `/folders/with-images` | Create folder + move N images atomically. |
| `GET` / `POST` / `PATCH` / `DELETE` | `/tags/...` + `/images/{id}/tags` + `/folders/{id}/tags` | Per-user tag system. |
| `POST` | `/shares/` | Grant a share. |
| `GET` | `/shares/incoming` | Files shared with me. |
| `GET` | `/people/` | Named persons + unlabelled clusters (face_recognition consent required). |
| `POST` | `/people/clusters/{id}` | Name a cluster. "Me" auto-resolves to your display name. |
| `POST` | `/people/detect-and-label` | Multi-select tag. |
| `GET` | `/search/?q=...` | Hybrid CLIP + FTS + WordNet expansion. |
| `POST` | `/cloud/google_drive/connect` | PKCE OAuth start. |
| `GET` | `/cloud/callback/google_drive` | OAuth callback. |
| `POST` | `/cloud/google_drive/sync` | Manual sweep. |
| `POST` | `/cloud/{src}/ai-opt-in` | Enable AI on synced files (re-arms vision workers). |
| `POST` | `/consent/{kind}/grant` / `/withdraw` | Per-scope consent. |
| `POST` | `/consent/face-recognition/grant` | BIPA-grade consent path. |
| `POST` | `/billing/checkout` / `/webhook` | Stripe Embedded Checkout + signature-verified webhook. |
| `GET` | `/billing/subscription` | Current tier + period. |
| `GET` | `/health` / `/health/db` | Liveness + DB ping. |

## Marketing site

`marketing/` contains the public landing site (React + Vite + a small Express prerender server for AI-engine crawlability). It's independent from the app — different port (5180 dev / 4173 build), different deployment target, different `package.json`. Pages: `/`, `/features`, `/compare`, `/developers`, `/roadmap`, `/hosting`, `/faq`, `/updates`, `/updates/:slug`, `/privacy`, `/terms`, `/waitlist`. The `server.mjs` prerender ships per-route HTML with FAQPage JSON-LD + structured data so ChatGPT / Perplexity / Google AI Overview / Bing Copilot can quote answers directly.

| Task | Command |
| --- | --- |
| Marketing dev | `cd marketing && npm run dev` (port 5180) |
| Marketing build | `cd marketing && npm run build` |
| Marketing prerender server | `cd marketing && node server.mjs` (port 5181 by default) |

## Tests

```bash
pytest                  # full suite
pytest tests/test_c9_filters.py -v   # one file
MINIO_SECRET_KEY=istorepass pytest   # if your local .env has an empty MinIO secret
```

Coverage areas:

- Health, auth, registration, consent lifecycle, age gate.
- Upload validation, archive walk safety, polyglot trailer strip, quarantine.
- Compression policy (codec dispatch, resize behavior, AVIF / JXL smoke).
- Vision pipeline (mocked) — content type, scene, indoor/outdoor labelling.
- Face pipeline — clustering, naming, BIPA consent flow.
- Cloud sync (Drive + GitHub) — OAuth state HMAC, conflict detection, AI opt-in.
- Search — hybrid scoring, synonym expansion, RLS isolation.
- C9 multi-axis filtering (10 cases — near consent gate, taken_between fallback, facets shape, etc.).
- C4.2 self-name binding (5 cases — substitution, 422, case-insensitive, rename path).
- C1 folders + tags (suggest-names, contains_type, tag CRUD, cross-user isolation).
- B1 EXIF strip + B2 consent-before-signup + B3 export + B4 retention sweepers.
- A4 RLS extension + A5 full deletion (asserts 0 rows + 0 objects).
- A7 repo hygiene (eight invariants).
- People-count regression (correlation-collapse guard).
- Account delete + grace + cancel + sweeper.
- Cross-user data-leak attempts return empty.

## What's still being built

Pulled straight from `todo.md`. Items are spec'd but not yet shipped.

| Item | Status |
| --- | --- |
| **Secret rotation worker** | A3 partial. Env hygiene + boot validator + gitleaks shipped; rotation tool for `JWT_SECRET` / DB / MinIO + `CLOUD_ENCRYPTION_KEY` migration tool still in flight. |
| **G2 Comments on shared docs** | Designed. `comments` table + anchor schema for PDF page-rect / slide / video time range + pin overlay + thread panel. |
| **C5.2 B2B migration** | Designed. Bulk import from SMB / NAS / Drive / Dropbox / OneDrive + per-source consent scopes + dry-run before commit + provider plugins. |
| **D1 Better image summaries** | Florence-2 + Qwen wired; richer scene/object hint pass + held-out eval set pending. |
| **D2 Better document summaries** | Qwen + pypdf wired; per-chunk embeddings + scanned-PDF OCR pending. |
| **D5 Command-style search DSL** | `/find`, `/show people: <name>`, `/best photo of <subject>`, `/in <folder>`, `/type <pill>`, `/before <date>`, `/after <date>`. |
| **D6 Fine-tune summaries from search behavior** | Per-user LoRA adapter, opt-in. Blocked on `C8.2` training pipeline. |
| **D7 Best-of-set user override telemetry** | Best Of works; logging the user's "no, THIS one" feedback for D6 is still owed. |
| **D8 Person re-detection on user signal** | "Mark as containing a person" → re-run RetinaFace with looser thresholds → fall back to manual box. |
| **E1–E6 Multi-data-type platform** | Promote `images` to one row in a wider `assets` table keyed by `data_kind`. Contacts (vCard), passwords vault (E2E AES-GCM with Argon2id-derived key the server never sees), game saves, IoT time-series. |
| **F1 Hardware tail** | CUDA / Intel XPU / Apple MPS dispatch shipped; AMD ROCm wheels + OpenVINO Intel NPU path still open. |
| **F2 Model quantization** | Florence-2 8/4-bit GPTQ + Qwen 4-bit GGUF + CLIP / RetinaFace INT8. |
| **F3 Lite no-AI profile** | Setup-wizard option to disable Florence + Qwen, fall back to BLIP + sumy. For Raspberry-Pi-class hosts. |
| **G3 Real-time team editing** | y.js + relay WebSocket. Documents only at first. |
| **H1–H4 Docs + CI cleanup** | README + comment-balance sweep + GitHub-ready .md + ruff/mypy/tsc CI gates. Unblocks the public source release. |
| **I.bis Project rename** | IStore → neuthek internal cleanup (env-var prefixes, bucket names, package metadata). Frontend already mounts as neuthek; storage names retain the historical prefix during the migration. |

## Contributing

1. Run `python scripts/setup.py` (or the platform setup script).
2. Branch from `main`.
3. Test your change — add coverage for behavior changes, especially anything touching auth, RLS, deletion, embeddings, or face workflows. Privacy impact gets called out in the PR body.
4. Pre-commit runs gitleaks + the binary-fixture forbid + the real-PII-filename forbid. Don't bypass.
5. Keep PRs small. Avoid mixing refactors with feature work.

## Security disclosure

If you find a security vulnerability, hold the report until the public disclosure address is published with the hosted launch — the product isn't released yet and there's no public inbound mail channel. Please don't post real secrets, private images, EXIF data, embeddings, or face data in public issues or pull requests.

## License

See [TERMS.md](TERMS.md) — source-available, personal/non-commercial use only during pre-release. When the public source drop happens (no committed date — hosted launches first), the license shipped with that release governs the self-host distribution.
