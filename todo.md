# neuthek — Roadmap

> Project renamed from **IStore → neuthek** on 2026-05-09. The codebase
> directory layout still references `IStore` in places; rename can land
> incrementally (folder, repo URL, package name, README, CI, infra).

This file tracks **what's still open** — broken, partial, or planned.
Shipped work isn't listed here; read `git log` for that.

> **Last shipped (2026-05-14/15)**: §1.1 + G1 sharing primitive
> (`share_grants` table + 7-route API + public `/share/{token}` viewer
> + ShareModal + bulk-share); §1.3 admin-overlay un-mock (all 6
> previously-mock tabs read real state via `backend/system_probes.py`
> + 6 new `/admin/*` endpoints; health rollup, friendly audit lines,
> thermals + NICs, kind-badge GPU display). First slices of §C8.2
> (`worker_heartbeats` + `model_runs` tables + worker emitter +
> dashboard wiring) and §F1 (multi-vendor accelerator probes —
> torch.cuda, IPEX-XPU, OpenVINO, WMI/lspci/PnP). 13 pytest cases
> for sharing pass; full sharer + recipient + revoke flow + admin
> overlay end-to-end smoke-tested in the browser. Before it: Sprint
> B + B+ AI-quality work (Florence-2 multi-model pipeline, Qwen
> rewriter, InternVL2 gated, PDF OCR, face re-detect cascade,
> reverse-geocode, Activity log, Trash).
> **Next up**: Sprint C (compliance + ship-blocking work). See §11.

---

## 1. Currently broken / partial

> Section closed as of 2026-05-15 — every item that was here is now
> shipped. The historical record below stays in case operators want
> to read the resolution notes.

### 1.2 Settings backlog ✅ SHIPPED 2026-05-15
All four sub-items closed:

- **TOTP 2FA** (§1.2.2) — new migration 0024 adds
  `users.totp_secret_enc` (Fernet-encrypted via `backend.secret_box`),
  `totp_enabled`, `totp_verified_at`. New router
  [backend/api/two_factor.py](backend/api/two_factor.py) wires
  `/account/2fa/{setup,verify,disable,status}` +
  `/auth/jwt/login-totp`. `UserManager.on_after_login` raises 401
  `totp_required` when 2FA is on, so the standard login endpoint
  can't issue a token for TOTP-enabled accounts. FE shows an inline
  QR (rendered server-side via `qrcode`) + a code-entry step in the
  sign-in form.
- **Notifications** (§1.2.3) — `notification_prefs` table keyed
  `(user_id, kind, channel)`; kinds = product_updates,
  security_alerts, account_activity, storage_warnings; channels =
  email + in_app. security_alerts default ON. Endpoints at
  `/account/notifications` (GET / PATCH). Tab restored to the
  Settings rail with a 4×2 toggle grid. Push channel deferred until
  the service-worker pass.
- **Plan / Invoices** (§1.2.4 *honest version*) — Account tab now
  renders a `PlanCard` reading `/storage/usage` with the real
  per-category breakdown + percent-of-quota meter tinted by
  threshold. No fake invoices, no fake Stripe checkout — the card
  says "no paid tier today" and leaves the upgrade affordance blank.
  Real billing lands as a separate workstream.
- **Per-scope `expires_at`** on Privacy rows — surfaced in `subFor`
  alongside "Granted …". The backend's been returning it since the
  consent state machine landed; the FE just wasn't formatting it.

---

## 2. Compliance / hard requirements (block public deployment)

> NEVER mark these "done" until verified end-to-end with tests + a
> security review. Consent does not override illegal storage, secret
> leakage, or insufficient encryption.

### A1. Upload validation hardening ✅ SHIPPED 2026-05-15
Every bullet that doesn't depend on the C1.5 archive uploader is in:

- **MIME + magic bytes** — `detect_magic` in
  [backend/upload_validation.py](backend/upload_validation.py) sniffs
  the first 512 bytes (JPEG / PNG / GIF / WebP / TIFF / PDF / OOXML
  ZIP / text) and ignores the client `Content-Type` for routing,
  using it only to reject obvious mismatches via `_client_mime_ok`.
- **Pillow re-decode + re-encode** — `_sanitize_image` calls
  `verify()` then `load()`, rejects decode failures with 415, and
  re-encodes JPEG/PNG/WebP/GIF/TIFF. The originals bucket only ever
  sees the sanitized output, so trailing-data polyglots
  (valid JPEG + appended `<script>`) lose the appended bytes at the
  encoder boundary. Verified by
  `test_image_polyglot_trailer_is_stripped_from_originals`.
- **Strip dangerous metadata** — SVG and HTML/script uploads are
  rejected outright; `_reject_scriptable_text` blocks `<script>`,
  `<iframe>`, `javascript:` etc. in any text-shaped document.
- **Per-user upload size + count rate limits** — `enforce_upload_limits`
  in [backend/security.py](backend/security.py) keys per
  `(user_id, ip)` for count and per-`user_id` for daily bytes; Redis
  in prod, in-memory fallback in dev.
- **Quarantine bucket** — every upload is mirrored to
  `bucket_quarantine` in parallel with validation. On success the
  quarantine blob is deleted (it was scratch). On failure it's
  **kept** and an `upload.quarantined` audit row records the
  quarantine key, byte count, client MIME, reason, and status code
  — operators can inspect rejected payloads without those bytes
  ever touching `originals`. Verified by
  `test_quarantine_keeps_blob_on_rejection_and_audits` +
  `test_quarantine_blob_is_deleted_on_success`.
- **Single dispatch table keyed by `data_kind`** — `_VALIDATORS`
  registry maps `image / document / video / other` to
  per-kind handlers. New §E data types (contact / password / save /
  iot_event) register via `register_validator(kind, fn)` without
  touching the dispatch path. Verified by
  `test_validator_dispatch_table_accepts_registration`.

Still owing (not blocking §A1 closure):
- **Reject zip bombs / oversized archive contents** — handled for
  OOXML (`_inspect_ooxml`: entry count, ratio, depth, path
  traversal, symlinks). The general archive uploader is **C1.5**;
  when that lands its handler reuses `_inspect_ooxml`'s checks
  with the same constants.
- A retention sweeper for the quarantine bucket itself lives in
  **§B4** — quarantined blobs persist indefinitely until then.

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
- ✅ **C1.1 Rename files** — shipped Sprint A. `PATCH /images/{id}/name`
  with centralized `validate_image_filename` validator (path separators,
  reserved Windows names, extension preservation, 255-byte cap, control
  chars).
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
  "name" field; persisted as `users.display_name`; used in the
  topbar greeting and across the UI in place of email.
- ⏳ **C4.2 "Me" → name binding** — when the user classifies a
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
- ✅ **C8.1 Visual / UX overhaul** SHIPPED 2026-05-15. Empty / loading /
  error states on every tab, health rollup banner, human-readable
  audit/log lines, filter chips on Audit + Logs, **routed `/admin`
  page** alongside the modal (with an "open as page →" link),
  **global search** across users / audit / images via
  `GET /admin/search`, **bulk Users actions** (bulk quota, bulk
  delete superuser-only, bulk revoke-consent) wired via
  `POST /admin/users/{bulk-quota, bulk-delete, bulk-revoke-consent}`.
- 🟡 **C8.2 Model + worker visibility** — operator-side bits shipped.
  `worker_heartbeats` + `model_runs` tables (migration 0023),
  `backend/heartbeats.py` emitter, the ml-worker ships a one-shot
  accelerator snapshot in heartbeat metadata, the Models tab shows
  live device + GPU memory + last-used, MinIO `istore-models`
  checkpoint bucket auto-created at boot. Still open:
  - Training-run telemetry (current step / loss curve / ETA) lands
    when D6 fine-tuning lands — extend `model_runs` with
    `started_at` / `finished_at` / `val_loss` / `artifact_key` then.
- ⏳ **C8.3 Quick-action runners**: from the dev view, trigger a
  re-summarize, re-embed, or re-detect-faces for a user / folder /
  date range without leaving the UI.

### C9. Multi-axis image filtering ⏳
Today's gallery has one filter axis (type pill: All / Photos / Videos /
Documents). Users can't pin down "all indoor photos from Vancouver
containing Sasha." Every signal needed is already in the DB —
`images.content_type`, `scene_label`, `indoor_outdoor`,
`image_geo.{lat,lng,place}`, `face_detections.person_id`, `image_tags`,
`summary_topic`, dates. The UX gap is composable filters in the FE
plus a query API that accepts them.

- **Backend** ([backend/api/images.py](backend/api/images.py)
  `list_images`): extend `GET /images/` to accept query params for
  `scene_label`, `indoor_outdoor`, `content_type`,
  `near=lat,lng,radius_km`, `person_id`, `tag`,
  `taken_between=ISO,ISO`, `is_starred`. AND-combine. Each gates on
  the relevant consent scope (location filter requires
  `gps_retention`, person filter requires `face_recognition`).
- **Frontend** ([frontend/neuthek/src/gallery.jsx](frontend/neuthek/src/gallery.jsx)
  `TypeChips` + new `FilterStrip`,
  [frontend/src/api/files.ts](frontend/src/api/files.ts) `ListFilters`):
  a filter strip below the type pills with chips per axis ("Indoor" /
  "Vancouver" / "Sasha" / "Starred"), each removable; clicking opens a
  small picker (scene list / map radius / people grid / tag list).
  Persist the filter in the URL so it survives reloads. Multi-axis
  composes the React-Query key (`["files", scope, filters]`) — no
  other plumbing change.
- **Out of scope here**: the auto-generated facets ("show 12 most
  common scenes from your library") — defer until D1 is tuned so the
  labels are trustworthy.

---

## 5. Search & AI quality

### D1. Better image summaries ⏳
Florence-2 detailed caption + scene-gated OCR + Qwen rewrite are wired.
What's still open:
- **Scene/object hint pass**: pick a model "like RAM++ but better"
  (richer open vocab, captioning-grade recognition). Feed labels into
  Qwen's rewrite prompt as structured hints so the summary reads
  "auth-flow review on a whiteboard" instead of "person standing near
  a whiteboard". Candidates to evaluate: RAM++, Places365 (365-label
  scene classifier), CogVLM2 tagger, OpenCLIP zero-shot against a
  curated 4k-vocab list.
- **Person-aware splice** using display-name binding (**C4.2**) instead
  of "Me" / generic third-person.
- **Held-out eval set**: "user search queries that should match this
  image" — measure recall@5 to drive prompt tuning.

### D2. Better document summaries ⏳
Qwen2.5-Instruct is the primary doc summarizer (map-reduce over chunks);
pypdf + pdfminer.six fallback are wired. What's still open:
- **Per-chunk embeddings** indexed alongside the doc so we can answer
  "where in this doc is X" — enables jump-to-section hits in semantic
  search.
- **OCR fallback for image-only PDFs** (scanned docs return 0 text
  from both pypdf and pdfminer; rasterize via PyMuPDF then route each
  page through Florence-2 `<OCR>`).

### D3. Hybrid search (CLIP + FTS) ⏳
`GET /search` blends CLIP cosine similarity with Postgres FTS over
`summary` + `summary_topic` + `summary_points` + `original_filename`
(0.45/0.55 weight; tsvector GIN index via migration `0017_summary_fts`).
What's still open:
- **Re-summarize backfill** — run `POST /images/backfill-summaries`
  on existing rows so old DistilBART output is replaced by the new
  Qwen path. Trigger via Account → AI features → Library maintenance.
- **Score telemetry** — log (query, top-10 ids, blend weights) per
  search so we can tune the blend empirically. Anonymized, consent-
  gated under `bandit_compression_telemetry`.

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

### F1. Backend runs on all major GPU/CPU vendors 🟡
**Detection + dispatch** shipped 2026-05-15. `backend.vision.runtime`
chains torch.cuda → torch.backends.mps → torch.xpu (IPEX) → CPU and
writes the choice to `runtime.toml` at first probe so model loads
don't re-detect every time. The worker's accelerator probe lists
everything torch.cuda, IPEX-XPU, OpenVINO `available_devices`, WMI
Win32_PnPEntity ("AI Boost"/NPU) and lspci can see, and ships the
snapshot in heartbeat metadata so the API container's Hardware tab
renders the GPU/NPU/iGPU view with CUDA / iGPU / NPU kind badges
even when the API process has no first-hand access. NVIDIA GPU
passthrough is wired into the default `docker-compose.yml`; Intel
iGPU + NPU passthrough is gated behind the opt-in
`docker-compose.intel.yml` overlay (needs Intel WSL graphics + NPU
drivers on the host).

**Still open**:
- AMD ROCm (Linux) — needs `torch + onnxruntime-rocm` wheels + a
  ROCm CI image. The torch dispatcher will pick it up automatically
  when present.
- OpenVINO-as-inference-path for Intel iGPU + NPU. Torch doesn't
  target the NPU directly; using it requires converting Florence-2 /
  Qwen / CLIP to OpenVINO IR and routing inference through
  `Core.compile_model(device='NPU')`. Detection-only today.
- AMD/Intel quantization variants (covered by F2).

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

### G1. Sharing primitive ✅ SHIPPED 2026-05-14
Per-image share grants live at
[backend/api/shares.py](backend/api/shares.py) (7 routes), backed by
the `share_grants` table (migration 0022). Argon2-hashed tokens,
recipient-email pinning, server-side 1-day cap for new-user
recipients, brute-force lockout on `/shares/claim`, public
`/share/{token}` viewer that doesn't expose the rest of the owner's
library. Permission set is currently `view_download`; comment/edit
are tracked under **G2 / G3** below. 13 pytest cases pass in
[tests/test_shares.py](tests/test_shares.py).

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

> Each item links back to its detail above. Sprint A + B + B+ landed
> (2026-05-13/14). Start at C.

### Sprint B — AI quality ✅ LANDED 2026-05-14

D1 multi-model pipeline (Florence-2 regions + OD + OpenCLIP concept
vocab + gated InternVL2-4B + Qwen synthesis), D2 PDF OCR fallback via
PyMuPDF, D8 face re-detect cascade with mediapipe — all shipped. Doc
preview rasterize + summary `topic`/prompt sharpening landed in the
B+ patch.

### Sprint C — compliance (next, ship-blocking; ~1 week)

4. **A6** audit existing PRIVACY.md / SECURITY.md /
   DATA_PROCESSING.md; fill gaps; wire consent log row per grant.
5. **A1** finishing — per-user rate limits + persistent quarantine
   bucket (most of the validator already shipped).
6. **A5** deletion integration test (uses fixtures in
   `tests/conftest.py`).
7. **A2** SSE/TLS posture confirmation in prod compose.

### Sprint D — sharing + onboarding (~2 weeks)

8. ✅ **§1.1 + G1** sharing primitive — `share_grants` table + 7-route
   `/shares/...` API + ShareModal + public viewer. (Landed 2026-05-14.)
8a. ✅ **§1.2** Settings backlog — TOTP 2FA + Notifications +
    Plan card + per-scope Expires_at. (Landed 2026-05-15.)
9. **G2** comments — `comments` table + pin overlay + thread panel.
10. **C5.1** setup script — platform detect, GPU probe, `.env`
    generation, `docker compose` or native install.
11. **C5.2** B2B migration — bulk import + per-source scopes +
    dry-run + provider plugins.
12. **C2** Drive cloud sync — pull-only, AI-off by default per
    Limited Use.

### Sprint E — multi-axis filters + UX polish (~1 week)

13. **C9** multi-axis image filtering — backend params + chip UI +
    URL persistence.
14. **C3** map refinements — supercluster migration once > 2 000 pins
    (reverse-geocode fill already shipped).
15. **C4.2** "Me" → display-name binding.
16. **§1.2** surface or hide remaining backend-gated Settings rows
    (Plan/Invoices/Notifications/2FA TOTP/per-scope `expires_at`).
17. ✅ **§1.3** unmock the remaining Admin overlay tabs — all 6 tabs
    read real state, health rollup banner, human-readable
    audit/log/task rendering, kind-badged GPU/NPU view, first slices
    of **C8.2** (worker_heartbeats + model_runs) and **F1**
    (multi-vendor accelerator detection) landed alongside.
    (Landed 2026-05-15.)

### Sprint F — multi-data-type platform (months)

20. **E1** promote `images` → `assets(data_kind)`.
21. **E2** Contacts (vCard/CSV).
22. **E3** Passwords vault (E2E encryption, Argon2id).
23. **E4** Game saves (versioned blobs).
24. **E5** IoT data (time-series, partitioned).
25. **E6** cross-type search + tagging + retention.

### Sprint G — hardware + collab + hygiene (longest tail)

26. 🟡 **F1** hardware dispatch — detection + torch dispatch
    (CUDA→MPS→XPU→CPU) + `runtime.toml` shipped 2026-05-15. Still
    owing: ROCm wheels, OpenVINO inference path (model conversion to
    IR for NPU), AMD quantization variants.
27. **F2** quantization (Florence-2 8/4-bit, Qwen 4-bit GGUF,
    CLIP / RetinaFace INT8).
28. **F3** Lite profile.
29. **G3** real-time team editing (y.js + WebSocket).
30. **D6** fine-tune from search (once **C8.2** ships).
31. **D7** best-of-set picker.
32. **H1–H4** repo hygiene + CI tightening.
33. **I.bis** project rename — admin churn, parallelizable.

### Things to NOT work on yet
- Plan / Invoices / Stripe billing UI — no payment backend.
- TOTP 2FA — recovery codes adequate; lower priority than **C6**.
- Activity log panel UI — needs a per-user audit-export endpoint.
- Plan-card pricing copy — premature.

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
