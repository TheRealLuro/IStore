# neuthek — Roadmap

> Project renamed from **IStore → neuthek** on 2026-05-09. The codebase
> directory layout still references `IStore` in places; rename can land
> incrementally (folder, repo URL, package name, README, CI, infra).

This file tracks **what's still open** — broken, partial, or planned.
Shipped work is intentionally not listed. Read commit history (or the
detailed status fields per item below) for what's done.

> **Sprint A landed 2026-05-13**: sidebar storage query gated (no more
> auth-screen 401s), folder cards show subfolder counts, `is_starred`
> backend + `POST /images/{id}/star` + optimistic FE toggle (replaces
> the localStorage star), per-image rename via
> `PATCH /images/{id}/name` with a centralized filename validator
> (reserved Windows names, path separators, extension preservation,
> 255-byte cap), aggressive dep cleanup (~230 npm packages removed +
> orphan `src/utils/*` deleted), `.gitignore` hardened. Fixed a
> latent asyncpg `%s` placeholder bug in `backend/db.py` along the way.

---

## 1. Currently broken / partial

### 1.1 PreviewPanel doc-bitmap cache collision
"framework showdown.pdf" preview tab renders "LBLF.pdf" content. Cache
key collision in [frontend/src/utils/docRender.ts](frontend/src/utils/docRender.ts)
(`getCachedPreview` / `setCachedPreview`) — cache keys on `file.id` but
the bitmap is reused across cards. Verify the cancellation flag in
[ThumbnailRenderer.tsx](frontend/src/components/ThumbnailRenderer.tsx)
discards the wrong bitmap when files swap quickly.

### 1.2 Face recognition misses extreme close-ups
`buffalo_l` RetinaFace requires landmarks (eyes + nose + mouth + jaw).
Eye-only B&W crops are structurally below the model's capability;
`det_thresh=0.3` helped marginal cases but not extreme ones. Fix in
**D7**: when a user manually labels a photo as containing a person,
retry detection at a much lower threshold before falling back to
manual-attach UI.

### 1.3 Map: pins still bare lat/lng
Pass-5 wired the map (canvas always renders, cross-folder query, EXIF
backfill, fitBounds). Still TODO: reverse-geocode pass so popup shows
"Big Sur, CA" instead of `36.27, -121.81`. Backend would call out to
Nominatim or a self-hosted geocoder and cache results in `image_geo.place`
(column already exists, currently always null).

### 1.4 No backend for sharing / "Shared with"
Real sharing needs: `share_grants` table (`image_id` → `user_id` or
email + role + when), `POST /images/{id}/shares` to invite,
`DELETE /images/{id}/shares/{id}` to revoke, `GET /images/{id}/shares`
for the preview panel to populate `file.sharedWith`. Long pole —
Sprint D.

### 1.5 Theme tokens leftover
neuthek uses `--ink-*` / `--surface-*` tokens. Some legacy
`--bg-page` / `--bg-elevated` references and Tailwind class fragments
still exist in `frontend/neuthek/styles/*.css`. Sweep and remove.

### 1.6 Settings: features hidden until backends ship
The Settings rail (Account / Privacy / Security / AI features / Your
data) is now fully wired for everything that has a backend. The
following sections are deliberately hidden in the UI today and need
backend work before they come back:
- **Plan / Invoices** (in Account tab) — needs Stripe / billing
  backend.
- **Notifications tab** (entire tab gone) — needs email + push
  notification backends + `notification_prefs` table.
- **Activity log** (in Your data) — needs a per-user
  `/account/activity` export endpoint (audit log already exists for
  superuser; this is the user-facing slice).
- **Trash** (in Your data) — needs `/account/trash` listing +
  `/account/trash/empty` endpoint.
- **2FA TOTP** beyond recovery codes — pyotp + QR endpoint.
- **Per-scope `expires_at`** on Privacy rows — backend now returns it
  via `/consent/scopes`, but the UI just shows `granted_at` for now.
  Surface "Expires Apr 14, 2029" subtitle once retention preferences
  are user-controllable.

### 1.7 Admin overlay: per-row actions not wired
Storage / Users / Audit tabs read live data, but per-user actions
on the Users tab (edit quota → `updateUserQuota`, promote/demote
role → `updateUserRole`) aren't wired. Endpoints exist; UI work only.
Models / Tasks / Logs / System / Processes / Hardware tabs are still
prototype mock — keep behind the `MOCK` pill until **C8.2** / **F1**
backend surfaces land.

---

## 2. Compliance / hard requirements (block public deployment)

> NEVER mark these "done" until verified end-to-end with tests + a
> security review. Consent does not override illegal storage, secret
> leakage, or insufficient encryption.

### A1. Upload validation hardening ⏳
- MIME type *and* magic bytes (don't trust `Content-Type`).
- Re-decode every image through Pillow before storage; reject decode
  failures (catches polyglot uploads).
- Strip dangerous metadata (XML payloads in SVG, embedded scripts).
- Per-user upload size + count rate limits.
- Reject zip bombs / oversized archive contents (after **C1.5** lands).
- Quarantine bucket for files awaiting validation; only promote to
  `originals` after all checks pass.
- Generalize beyond images: when **E** lands (contacts, passwords,
  saves, IoT) each new data type needs its own validator + quarantine
  rule. Single dispatch table keyed by `data_kind`.

### A2. Encryption at rest + in transit 🟡
- TLS everywhere — API, MinIO, frontend, admin tools. No plaintext
  HTTP outside `dev`.
- MinIO server-side encryption (SSE-S3 or SSE-KMS) on all buckets.
  Prod compose has `MINIO_SSE_MODE=sse-s3`; verify it's actually on.
- Postgres data-at-rest encryption (volume-level on the host or
  `pgcrypto` for sensitive columns: face embeddings, EXIF, summary text).
- Backups encrypted with a key the host doesn't store.
- Separate encryption keys for biometric tables vs. content tables.
- Once **E** lands: each new data type gets its own encryption envelope.
  Vaulted data (passwords) MUST be E2E with a user-derived key the
  server never sees.

### A3. Secret management ⏳
- No `.env` in git; commit `.env.example` with placeholders only.
- Rotate `JWT_SECRET`, MinIO root creds, DB passwords on a schedule.
- Move secrets out of compose files into a secret manager (Vault,
  Docker secrets, platform-native).
- Audit log every secret access (who read, when, from where).

### A4. Access control + audit ⏳
- RBAC on top of per-user filtering — admin/superuser/user roles.
- Signed download URLs for `originals` + `served`; expire ≤ 5 min.
- Rate-limit auth endpoints (5/min per IP, exponential backoff after
  lockout).
- Brute-force protection (account lock after N failed attempts).
- Append-only audit log: auth events, deletes, consent changes, admin
  actions.
- Postgres RLS policies for biometric tables (currently app-layer
  enforced).

### A5. Deletion that actually deletes ⏳
The single most-skipped feature. When a user deletes an image *or* the
account, all of the following must go:
- `originals` object, `served` variant
- Thumbnail caches (FE IndexedDB + BE caches)
- CLIP embedding row, AI summary text/topic/points
- EXIF row, GPS row
- Detected face crops in `faces` bucket
- Face embeddings (`faces.embedding`), face detections
  (`face_detections`)
- Person rows that have no remaining faces
- Bandit reward / arm history (or anonymized)
- Audit log entries: referenced but NOT deleted (legal retention)
- Backup invalidation eventually (document the retention)
- Once **E** lands: contacts, password vault items, save blobs, IoT
  telemetry — each with its own eraser worker.

Add an integration test that uploads, deletes, and asserts every
table + bucket returns 0 rows / 0 objects for the target.

### A6. Compliance scaffolding 🟡
- ⏳ **PRIVACY.md** — what we collect, why, retention, deletion
  process, embedding handling, biometric handling.
- ⏳ **SECURITY.md** — disclosure email, supported versions.
- ⏳ **DATA_PROCESSING.md** — for B2B users (DPA template).
- ⏳ Cookie banner if any cookies are set; document `Set-Cookie` for
  every cookie.
- ⏳ Age gate (under-13 prohibited unless full COPPA flow is built).
- ⏳ Consent log: every grant/revoke gets a row with timestamp, IP,
  user-agent, scope.

### A7. Repo hygiene ⏳
- Audit git history for: real user images, real EXIF, real
  embeddings, prod credentials, DB dumps. Use `git log --stat -p` and
  `git filter-repo` if anything sensitive surfaces.
- `.gitignore` everything that could leak (`.env`, `data/`, `*.dump`).
- Tests use synthetic fixtures only.
- CI step that fails on committed secrets (gitleaks / trufflehog).

---

## 3. Privacy / consent

### B1. EXIF / GPS handling ⏳
- Strip EXIF (especially GPS) by default on upload. Store only fields
  the user explicitly opts into ("show camera info on previews").
- Surface the choice in the consent flow at signup, not buried in
  settings.
- Redact GPS from EXIF preview row even when data exists in the file
  (unless user consents).

### B2. Consent BEFORE signup ⏳
Today neuthek shows consents *after* the form; legal requirement is
"before account creation." Reorder so the register call only fires
post-consent (wiring is half-there in
`auth.jsx → handleConsentsComplete`).

### B3. Export + portability ⏳
- `/account/export` returns a zip: originals + JSON sidecar with all
  metadata + embeddings (encrypted) + summaries + people + consent log.
- Re-export rate-limited (1/day per user).
- Email a download link; link is signed, expires in 24 h.

### B4. Retention sweepers ⏳
- Originals: 30-day default, configurable per-user up to a cap.
- Bandit telemetry: 90 days then anonymize.
- Audit log: 1 year then archive.
- Deleted-account grace: 30 days then hard-delete everything.
- Each sweeper writes to the audit log so we can prove deletion happened.

---

## 4. Product features

### C1. Folders, files, naming, organization
- ⏳ **C1.1 Rename files**: `PATCH /images/{id}/name` validating
  type-correct conventions (no path separators; preserves extension;
  rejects reserved Windows names like `CON`, `PRN`; trims to 255 bytes;
  collapses whitespace). Updates `images.original_name` only; storage
  key stays UUID. Re-runs search index update.
- ⏳ **C1.2 AI-suggested smart names**: "Suggest a name" affordance on
  rename that asks the existing summarizer for 3 short, filename-safe
  proposals from content (e.g. "Whiteboard sketch — auth flow"). Reuses
  Florence-2 + Qwen. Never auto-renames without confirmation.
- ⏳ **C1.3 Type-pill ∧ folder visibility**: type pills (Images /
  Documents / etc.) currently hide folders containing them. Required:
  folder stays visible iff it contains ≥ 1 file of that type
  (recursive). Either extend `GET /folders` with `?contains_type=...`
  or compute client-side from the listing.
- ⏳ **C1.4 Clear search history**: search bar keeps recent queries
  with no clear control. Add a "Clear history" button at the dropdown
  bottom + `DELETE /search/history` endpoint.
- ⏳ **C1.5 Archive uploads (zip / 7z / tar / rar)** — blocked on **A1**:
  1. `POST /folders/upload-archive` (multipart). Cap raw size ~200 MB.
  2. Inspect before extracting: total uncompressed ≤ 5× compressed,
     entry count ≤ 5 000, max depth ≤ 10, no `..` or absolute paths,
     no symlinks.
  3. Auto-create folder from archive stem; route every entry through
     the existing image upload pipeline so MIME / magic-bytes /
     bandit-compression all apply.
  4. Persist `source_archive_id` (column already added in 0010) so a
     future re-pack endpoint can rebuild.
- ⏳ **C1.6 Tag system (status-as-tag unification)**: user intent is
  "status should just be tags." Add a generic `tags` table + many-to-
  many `image_tags`, migrate `images.status` / `images.status_color`
  into named tags ("In Review", "Published", …), let users create
  arbitrary tags with colors. Filter pills become tag filters; folder
  statuses follow the same model.
- ⏳ **Set folder status from menu** — legacy popover had it; deferred
  into **C1.6** unification.

### C2. Cloud sync (Drive / iCloud / GitHub / etc.)
- ⛔ Blocked on **A2/A3** (encrypted secret storage). OAuth refresh
  tokens are long-lived credentials.
- One provider at a time; Drive first (biggest user value, cleanest
  API, well-documented Limited Use compliance).
  1. `drive.readonly` scope only.
  2. `cloud_links(user_id, provider, encrypted_refresh_token, scopes,
     last_synced_at, status)`.
  3. Hourly worker pulls listings, diffs against `cloud_files(user_id,
     provider, remote_id, local_image_id, remote_modified, sha256)`,
     pulls new/changed through the existing upload pipeline with a
     synthesized folder per source-folder.
  4. Pull-only; conflicts surfaced in a banner.
  5. Disable AI summary + face scan on synced files unless user opts
     in per-source (Google Limited Use forbids using Drive content to
     train models).
- GitHub second (own repos, treat each repo as a folder, skip secrets
  by pattern). iCloud / Dropbox / OneDrive deferred.

### C3. Map view refinements
- ⛔ Blocked on **B1** (EXIF strip on by default + per-user opt-in).
- Mechanically wired (pass-5) and visually approved — current
  CartoDB Voyager / DarkMatter look stays. Outstanding refinements:
  1. Reverse-geocode worker that fills `image_geo.place` (see 1.3) so
     popups read "Big Sur, CA" instead of bare lat/lng. Cache results
     server-side (Nominatim is rate-limited).
  2. Migrate the inline pixel-space clusterer to `supercluster` once
     pin counts pass ~2 000 — current clustering re-walks every visible
     point on each render, which won't scale past that. Click cluster
     → gallery view filtered by cluster bbox.
  3. Per-pin animated entrance (staggered scale-in) when first arriving
     after a fitBounds — visual polish, not load-bearing.

### C4. Profile / settings
- ⏳ **C4.1 Display name on signup** — registration form gains required
  "Display name" field; persisted as `users.display_name`; used in the
  topbar greeting and across the UI in place of email.
- ⏳ **C4.2 "Me" → display-name binding** — when the user classifies a
  person as **Me**, the summarizer must (a) auto-rename that person row
  to the user's display name and (b) splice the display name (not "Me")
  into AI summaries. If `display_name` is empty, prompt during the
  classification flow.
- ⏳ **C4.3 Email re-verification on change** — backend hook is there;
  needs the FE staged-email banner ("Click the link we sent to <new>;
  until then, your account email is still <old>").
- ⏳ **C4.5 Storage retention controls** — surface the per-category
  breakdown (already on `/storage/usage`) and add a slider for
  original-retention (default 30 days).
- ⏳ **C4.6 Cloud provider connect buttons** — blocked on **C2**.

### C5. Onboarding & B2B migration
- ⏳ **C5.1 Easy setup script** (single-host, dev/self-host):
  1. Detect platform (Win/Linux/Mac), available drives, prompt for
     path.
  2. Probe for CUDA / AMD / Apple Silicon / Intel ARC; suggest the
     right torch wheel index URL (ties into **F1**).
  3. Generate `.env` with fresh `JWT_SECRET`
     (`secrets.token_urlsafe(48)`), MinIO root creds, DB password.
  4. Either `docker compose up -d` or native install (user picks).
  5. Optional `Tk` / `webbrowser`-launched single-page wizard.
  - **First step**: CLI-only `scripts/setup.py` that prints a
    numbered checklist + writes `.env`. Wizard UI later.
- ⏳ **C5.2 B2B migration tooling** — headline B2B promise is
  "switching from your current drive should be smooth, quick, simple."
  1. Bulk import endpoints: drag-a-folder-tree (server-side walks an
     SMB / NAS / mounted path with a service-account credential), or
     desktop companion that streams uploads with resume support.
  2. Per-source scopes: pulled files get a per-source consent scope
     (e.g. "AI summarization for Marketing share = on, for HR share =
     off") so legal can sign off per dataset.
  3. Migration dry-run: report estimated total bytes, file count,
     incompatible types, and blocked-by-policy items before commit.
  4. Provider plug-ins: Drive / OneDrive / Dropbox / Box / S3 / SMB.
     Each is a thin adapter over the same `cloud_files` schema (fans
     out from **C2**).
  5. Side-by-side phase: keep both systems live with one-way pull
     until the customer flips DNS / clients to neuthek.

### C6. Account recovery
- ⛔ Blocked on email infra. Pick one:
  1. Bring-your-own SMTP (cheap; works with Gmail App Passwords).
  2. Transactional provider (Postmark / SendGrid / Resend).
- Then:
  1. Forgot-password (fastapi-users routes already exist; wire
     `UserManager.on_after_forgot_password`).
  2. Email verification on signup; gate sensitive endpoints behind
     `is_verified`.
  3. Recovery codes (already scaffolded in 0011).
  4. TOTP 2FA via `pyotp` + QR endpoint. Lower priority.

### C7. Light theme refinement ⏳
Tokens close to Apple HIG, shadows strictly small
(`0 1px 2px rgb(0 0 0 / 0.04)`); tighten letter-spacing on headers
(`tracking-[-0.01em]`).

### C8. Dev / Admin dashboard
- ⏳ **C8.1 Visual / UX overhaul**: dev view "needs way more
  refinements visually, tool-wise, and ease of use." Concrete passes:
  1. Replace modal-fullscreen `AdminPanel` with a routed `/admin`
     page with its own layout (sidebar tabs, breadcrumbs).
  2. Global search box that filters across users, audit events, images.
  3. Empty / loading / error states for every tab.
  4. Bulk-action toolbar (bulk quota, bulk delete, bulk consent
     revocation).
- ⏳ **C8.2 Model training visibility**: surface live training /
  fine-tuning runs — current step, loss curve, GPU usage, ETA. Save
  checkpoints to a `models/` bucket as `.pkl` / `safetensors` with
  metadata row (`model_runs(id, model_name, started_at, finished_at,
  val_loss, artifact_key)`). Powers **D5**.
- ⏳ **C8.3 Quick-action runners**: from the dev view, trigger a
  re-summarize, re-embed, or re-detect-faces for a user / folder /
  date range without leaving the UI.

---

## 5. Search & AI quality

### D1. Better image summaries 🟡
- ✅ OCR cap raised 400→1500 chars in the Qwen rewriter prompt
  (whiteboards stop being truncated mid-equation).
- ✅ Florence-2 detailed caption + scene-gated OCR + Qwen rewrite live.
- ⏳ Add a scene-and-objects pass (RAM++ tags or Places365) and feed
  those into Qwen's prompt as structured hints for better content
  understanding ("auth-flow review on a whiteboard" instead of
  "person standing near a whiteboard").
- ⏳ Person-aware splice using display-name binding (**C4.2**) instead
  of "Me" / generic third-person.
- ⏳ Held-out eval set: "user search queries that should match this
  image" — measure recall@5 to drive prompt tuning.

### D2. Better document summaries 🟡
- ✅ Replaced DistilBART with Qwen2.5-Instruct as primary summarizer.
  Long docs are chunked → per-chunk summaries → merged in a second
  pass (map-reduce). DistilBART stays as fallback. LLM also fills in
  topic + keypoints when extraction returned nothing usable.
- ✅ pdfminer.six fallback for layout-heavy / two-column PDFs that
  pypdf can't parse.
- ⏳ Per-chunk embeddings indexed alongside the doc so we can answer
  "where in this doc is X" — enables jump-to-section hits in semantic
  search.
- ⏳ OCR fallback for image-only PDFs (scanned docs return 0 text from
  both pypdf and pdfminer; route through Florence-2 OCR per page).

### D3. Hybrid search (CLIP + FTS) 🟡
- ✅ `GET /search` now blends CLIP cosine similarity (visual) with
  Postgres FTS over `summary` + `summary_topic` + `summary_points` +
  `original_filename` (textual). Weighted 0.45 CLIP / 0.55 text.
  Migration `0017_summary_fts` adds a generated tsvector column with
  a GIN index for query-time speed.
- ⏳ **Re-summarize backfill**: run `POST /images/backfill-summaries`
  on existing rows to populate the new doc summary fields. Existing
  DistilBART output is fine but lower quality than the new Qwen path.
- ⏳ **Score telemetry**: log (query, top-10 ids, blend weights) per
  search so we can tune the blend without guessing. Anonymized,
  consent-gated under `bandit_compression_telemetry`.

### D4. Semantic search through folders ⏳
Blocked on **D1**+**D2** stabilizing. Once content summaries are
reliable, extend semantic search to match folder *titles + aggregated
child summaries* so a query like "the trip to Mexico" hits the folder,
not just the photos. Cache an aggregate embedding per folder;
invalidate on child add/move/delete.

### D5. Command-style search bar ⏳
Treat the search bar as a small DSL parser:
- `/find <query>` → semantic search (default).
- `/show people: <name>[ + <name>...]` → filter by people.
- `/best photo of <subject>` → run **D7** over the matching set.
- `/in <folder>` → scope to a folder by name.
- `/type <pill>` → restrict file kind.
- `/before <date>` / `/after <date>` → temporal filters.

Falls back to natural-language query if no command prefix.

### D6. Fine-tune the summary model from search behavior ⏳
- ⛔ Blocked on **C8.2** (model-training pipeline).
- Log (query → clicked result) pairs (consented) and use them as a
  soft-label dataset to fine-tune the rewriter so future summaries
  match the way *this* user phrases their searches. Per-user adapter
  (LoRA) so we don't pollute a global model.

### D7. Best-of-set image picker ⏳
User flow: select N similar photos, "Pick the best." Backend scores
by sharpness, exposure, eyes-open / smile (face landmark signal),
composition (rule-of-thirds), and an optional user-preference axis
("subjectively best per this user"). Returns a ranked list with the
top one highlighted; user can override and the override is logged
for **D6**.

### D8. Person re-detection on user signal ⏳
1. UI affordance on a photo: "Mark as containing a person."
2. Backend re-runs RetinaFace at `det_thresh=0.15` *and* falls back
   to mediapipe face-mesh if `buffalo_l` still finds nothing.
3. If still empty, prompt the user to draw a box and attach a person
   manually — that crop becomes a labeled face for `face_recognition`.

---

## 6. Multi-data-type platform

> Vision: neuthek stores **everything** — contacts, passwords, game
> saves, IoT data — not just images. Each type must be (a)
> distinguishable and (b) compatible with the existing features
> (search, encryption, sharing, retention).

### E1. Data-type taxonomy + schema ⏳
Promote `images` to be one row in a wider `assets` table keyed by
`data_kind` (`image`, `video`, `document`, `contact`, `password`,
`save`, `iot_event`). Type-specific tables hang off `assets.id`. New
types are added as a new sub-table + handler module without touching
the core gallery flow.

### E2. Contacts ⏳
Import vCard / CSV; per-contact fields (name, emails, phones, notes,
photo). Searchable, foldered, taggable. Photo doubles as a face
source for face_recognition (with consent).

### E3. Passwords (vault) ⏳
**Hard requirement: end-to-end encryption.** Server stores ciphertext
only; encryption key derives from the user's password via Argon2id and
never leaves the client. Recovery via recovery codes (**C6**). Schema:
`vault_items(id, asset_id, ciphertext, nonce, kdf_params, schema_version)`.

### E4. Game saves ⏳
Treat as opaque blobs with a versioned history (last N versions
retained, prune oldest). Per-game folder. Optional upload-from-
companion-app for desktop launchers.

### E5. IoT data ⏳
Time-series ingestion endpoint (`POST /iot/ingest`) per device-token;
stores rows in a partitioned `iot_events` table. FE shows a per-
device timeline + simple chart. Retention is per-device with a hard cap.

### E6. Cross-type features ⏳
- Search must work across types: a query for "Jason" should match
  contacts AND images of Jason AND any document that names him. Tag
  system from **C1.6** is shared.
- Encryption envelopes per type (**A2**).
- Per-type retention sweeper (**B4**).

---

## 7. Hardware compatibility & quantization

> Goal: neuthek self-hosts cleanly on whatever the user has — NVIDIA
> CUDA, AMD ROCm, Intel ARC / oneAPI, Apple Silicon Metal, or
> CPU-only — without manual model-format wrangling.

### F1. Backend runs on all major GPU/CPU vendors ⏳
Detection layer (in **C5.1** setup script + at runtime) picks the
right backend:
- NVIDIA CUDA → torch + onnxruntime-gpu.
- AMD ROCm (Linux) → torch + onnxruntime-rocm.
- Apple Silicon → torch + MPS / CoreML for vision models.
- Intel ARC / iGPU → onnxruntime + OpenVINO EP.
- CPU fallback → onnxruntime CPU + ggml/llama.cpp for the rewriter.

Runtime probes the device once at boot and writes the chosen backend
to a `runtime.toml` so we don't re-probe on every inference.

### F2. Model quantization ⏳
- Florence-2-large → 8-bit GPTQ for ≥ 8 GB GPUs, 4-bit for smaller;
  CPU path uses ONNX INT8.
- Qwen2.5-1.5B → 4-bit GGUF for CPU/Apple, GPTQ for CUDA/ROCm.
- CLIP / RetinaFace → ONNX INT8.
- Make the quant level a config option, not a code change.

### F3. Headless / low-resource mode ⏳
Setup wizard offers a "Lite" profile that disables Florence-2 + Qwen
and falls back to BLIP captions + sumy summaries. Useful for
Raspberry-Pi-class hosts and as a no-AI privacy stance for paranoid
users.

---

## 8. Collaboration on shared documents

> User intent: when documents or slideshows are shared, there should
> be an edit tab where people can comment or edit them as a team.

### G1. Sharing primitive ⏳
Per-asset share link with explicit permission (`view`, `comment`,
`edit`); link is signed, expires, revocable. Recipients see a
stripped-down viewer that doesn't expose the rest of the owner's
library.

### G2. Comments ⏳
`comments(id, asset_id, author_user_id_or_email, body, anchor_json,
created_at)` where `anchor_json` is a free-form pointer (page+rect
for PDFs, slide index for slideshows, time range for video). FE
renders pins on the asset and a thread panel on the right.

### G3. Real-time team editing ⏳
Document type only at first; out of scope for images. Likely path:
y.js + a relay WebSocket, persisted snapshot per N seconds. Big,
separate workstream — schedule after **F** lands so we know what
hardware budget we're working with on self-host.

---

## 9. Repo & docs hygiene

### H1. README rewrite ⏳
Currently asserts "frontend files exist as placeholders" — no longer
true. New structure: hero + screenshots, "what you can actually do,"
install (one-liner via **C5.1**), self-host notes, status of features,
security posture, contributing, license.

### H2. Code-comment balance ⏳
Sweep `backend/` and `frontend/src/` for:
- Comments that just restate the next line ("// increment i").
- Multi-paragraph docstrings on internal helpers.
- Out-of-date "TODO" comments referring to phases that shipped.

Keep comments that explain *why* (constraints, hidden invariants,
workaround for a specific bug).

### H3. GitHub-ready .md files ⏳
Every top-level `.md` rendered on github.com should look intentional:
short headings, no broken anchors, no internal paths that only make
sense locally, link to the right files. In scope: README, ROADMAP
(this file slimmed for public), CONTRIBUTING, SECURITY, PRIVACY,
TERMS, LICENSE summary.

### H4. CI / lint tightening ⏳
Add `ruff` + `mypy --strict` for backend; `tsc --noEmit` + `eslint`
already run on FE — wire both into a GitHub Actions workflow that
gates merges. Plus `gitleaks` for secrets (see **A7**).

---

## 10. Project rename: IStore → neuthek

Decided 2026-05-09. The frontend already mounts as "neuthek"; the
rest of the codebase, infra, and docs trail behind. Land each piece
in its own commit so blame stays useful and reverts are cheap.

### I.bis.1 Local checkout and code refs ⏳
- Rename the repo directory `IStore/` → `neuthek/` on disk; update
  any local `cd IStore` shortcuts.
- Search/replace `IStore` → `neuthek` (case-preserving) across:
  - `pyproject.toml` (`name`, `description`, console scripts).
  - `frontend/package.json` (`"name": "istore-frontend"` →
    `"name": "neuthek-frontend"`).
  - `docker-compose*.yml`, `Dockerfile`s, image tags, network names.
  - `alembic.ini` migration tag and any logger names.
  - `backend/config.py` env prefixes (e.g. `ISTORE_*` → `NEUTHEK_*`)
    with a deprecation read of the old prefix for one release.
  - Test fixtures, sample data filenames, README, TERMS, PRIVACY,
    SECURITY copy.
- Storage bucket names — **do not rename live buckets**. Add new
  `neuthek-{originals,served,faces,…}` buckets in MinIO and run a
  one-time mirror; flip the config when ready, retire the old names
  after a backup window.
- Database name — same approach: pg_dump, restore into `neuthek`,
  flip `DATABASE_URL`, keep the old DB read-only for 30 days.

### I.bis.2 Hosting / external refs ⏳
- GitHub repo rename (org admin) — GitHub keeps redirects, but update
  SSH/HTTPS remotes everywhere they're hard-coded.
- Domain: register `neuthek.app` (or chosen TLD) and set up
  `privacy@`, `dpo@`, `security@`. Point legal docs at the new
  contact addresses.
- `LICENSE` `Copyright (c) … IStore Authors` → `neuthek Authors`.
- Slack/Notion/Linear projects, OAuth client app names with Google /
  Microsoft / Apple, transactional email "From" name.

### I.bis.3 Brand surface in the app ⏳
- Sidebar logo / favicon / OG image asset set.
- Email templates (verification, reset, recovery codes) — header
  brand, signature, From name.

---

## 11. Recommended priority order

> Picked 2026-05-09. Each item links back to its detail above.

### Sprint B — AI quality (next up)

1. **D1** scene/object hint pass — pick a model stronger than RAM++
   (richer vocab, captioning-grade recognition), feed labels into the
   Qwen rewrite prompt. Re-summarize the library, hand-eval ~20 results,
   tune prompts.
2. **D2** OCR fallback for image-only PDFs — when pypdf + pdfminer both
   return empty, rasterize page-by-page (PyMuPDF) and route each page
   through Florence-2 `<OCR>`.
3. **D8** person re-detect on user signal — UX cascade:
   `RetinaFace 0.3 → RetinaFace lower → mediapipe → user draws box`.
   Adds mediapipe dep.

### Sprint C — compliance (ship-blocking; parallel to B)

4. **A6** audit existing PRIVACY.md / SECURITY.md / DATA_PROCESSING.md
   for completeness; fill the gaps.
5. **A1** finishing — per-user rate limits + persistent quarantine
   bucket (most of the validator is already in place).
6. **A5** deletion integration test (uses existing fixtures in
   `tests/conftest.py`).
7. **A2** SSE/TLS posture confirmation.

### Sprint D — sharing & onboarding

8. **§1.4** sharing backend (`share_grants` table + endpoints +
   preview wiring).
9. **C5.1** setup script.
10. **C5.2** B2B migration tooling.
11. **C2** Drive cloud sync (after **A2**/**A3**).

### Long-term roadmap

12. **C3** GPS map refinements (reverse-geocode + supercluster).
13. **C4.2** "Me" → display-name binding.
14. **Section E** — multi-data-type platform.
15. **Section F** — hardware compatibility & quantization.
16. **Section G** — collaboration / comments / real-time edit.
17. **Section H** — repo & docs hygiene.
18. **I.bis** project rename — admin work, parallelizable.

### Things to NOT work on yet
- Plan / Invoices / Stripe billing UI — there's no payment backend
  and won't be until commercial launch.
- TOTP 2FA — recovery codes cover the lockout case adequately for
  now; lower priority than C6.
- Activity log panel UI — needs a per-user audit-export endpoint
  that doesn't exist yet.
- Plan card pricing copy — premature; pricing shouldn't be hard-coded.

### Phase 9 backend hardening (carried over)
- arq + Redis worker for the vision pipeline (refactor; pipeline
  function unchanged, only call site moves).
- GPU inference subprocess with batching (50 ms fill window) +
  Unix-socket IPC.
- Places365 for finer scene categorization (365 labels). Powers **D1**.
- RAM++ for richer tag generation (~4 k vocab). Powers **D1**.
- structlog + OpenTelemetry traces.
- GPU OOM back-pressure to arq (block when queue depth > N).
- Locust load test at 100 concurrent uploads.
- Nightly DB + MinIO backups.

---

## 12. Rules — non-negotiable

These come straight from the user dump on 2026-05-04 (and reinforced
2026-05-09). **Read these before designing anything that handles user
data.**

### Privacy
1. **Informed consent.** Users must understand what data we collect,
   what AI processing happens, what's stored, how long, how to delete.
   Plain language. Especially for embeddings, faces, semantic search,
   biometric features. **Collected via popup before signup**, revoked
   from settings.
2. **Security.** HTTPS, encryption (at rest + in transit), access
   controls, secure storage, rate limiting, secret management, audit
   logging, deletion systems. **Every data type is encrypted** (images,
   contacts, passwords, saves, IoT) — passwords end-to-end with a key
   the server never sees.
3. **Data minimization.** Only collect what's necessary. Embeddings for
   semantic search → reasonable. Personality vectors → much harder to
   justify.
4. **User control.** Export, delete, revoke consent, disable AI
   features, remove biometric data.
5. **Honest disclosure.** No secret model training, no quiet expansion
   of data usage. The biggest lawsuits come from deceptive practices.
6. **Consent does not override everything.** Even with "I agree", some
   things stay illegal: unfair biometric practices, deceptive AI claims,
   unsafe retention, minor biometric data, discrimination profiling,
   inadequate security, hidden processing.
7. **Safest framing for neuthek**: "Private AI-assisted personal &
   business storage for the account owner." Dangerous framing: "Global
   people recognition and profiling." Huge legal difference.

### Biometrics (the highest-risk surface)
The line is between "this image probably contains a face" (low risk)
and "this is Jason / find all photos of this person / cluster these
identities" (BIPA + GDPR special-category territory).
- Opt-in only, written-consent-grade explicit.
- Local/on-device processing where possible.
- Separate biometric DB + separate keys.
- Immediate deletion on revoke.
- No profit motive on biometric data ever.

### Frameworks that apply
- **GDPR** — if any EU user can reach the app. Right-to-be-forgotten,
  portability, consent logging, processing records, breach notification.
- **CCPA / CPRA** — California users; disclose collected data, allow
  deletion + export, disclose AI/derived metadata.
- **BIPA** (Illinois) — written informed consent, public retention
  policy, deletion schedule, no profit, secure storage. Violations are
  expensive.
- **COPPA** — under-13 users prohibited unless fully compliant.

### AI/ML guidelines
- Treat embeddings as sensitive data. Encrypt them. Delete with the
  source asset. Never expose raw vectors. Never allow cross-user
  similarity.
- Every vector query scoped to the authenticated owner.
- No cross-user index, no shared semantic store, no accidental leakage.
- **No silent model training on user data.** Per-user fine-tuning
  (D6) requires a separate explicit opt-in scope and a per-user adapter,
  never a shared global update.

### Security must-haves
- Encryption: HTTPS everywhere; SSE on object storage; PG volume
  encryption + `pgcrypto` for biometric / vault columns; encrypted
  backups.
- Secrets: never commit, rotate on schedule, vault/secret manager.
- Access control: RBAC, signed URLs, rate limits, brute-force lockout,
  audit log.
- File validation: MIME + magic bytes, re-decode, size cap, malware
  scan, metadata strip.
- Logging: never log JWTs, raw bytes, embeddings, EXIF GPS, face
  metadata, vault ciphertext.

### Repo rules
Never commit: real user images, embeddings, EXIF-rich samples, prod
credentials, DB dumps, biometric data, vault ciphertext, real
contacts, real IoT logs. Use synthetic fixtures only.

### Pre-launch checklist
1. Security audit (auth/authz/JWT/upload/storage/secrets/rate-limit/
   deps).
2. Privacy audit (every stored field — why, how long, can users delete
   it).
3. Compliance checklist (Privacy + Terms + deletion + export + consent
   + cookies + biometric opt-in + age gate).
4. Infra hardening (HTTPS, encrypted backups, private object storage,
   firewall, monitoring, malware scanning).
5. AI/ML review (no cross-user leakage, no hidden biometric processing,
   no accidental training on user data).
6. Deletion testing (every table + bucket + cache + backup eventually).
7. Threat modeling (image leakage, metadata leakage, biometric misuse,
   bucket misconfig, search isolation failures, vault key extraction).
8. External review (another dev + a security person + a privacy person).

### Required minimums before public release
Privacy Policy · Terms of Service · License · Security policy · Contact
email · Documented deletion process · Backup strategy · HTTPS · Strong
secrets · Pre-signup consent popup flow.

---

## 13. Quick reference

- Run backend: `.venv/Scripts/python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --log-level info`
- Run tests: `.venv/Scripts/python.exe -m pytest`
- Apply migrations: `.venv/Scripts/python.exe -m alembic upgrade head`
- Force-regenerate every summary: **Account → AI features → Library
  maintenance → Re-summarize entire library** (or `POST
  /images/backfill-summaries?force=true&limit=500`) — recommended after
  the D2 Qwen-based
  summarizer change so existing rows benefit.
- After any change to `backend/`, **restart uvicorn** — it doesn't
  hot-reload Python module changes by default.
- Run frontend (neuthek): `cd frontend && npm run dev` (Vite, port 5173).
  Production build: `npm run build`. Vite resolves `@/...` →
  `frontend/src/...`; the neuthek source lives at `frontend/neuthek/src/`
  and imports the API client + zustand stores via `@/api/*` and
  `@/stores/*`.
