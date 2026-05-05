# IStore — Roadmap

Working file for the project beyond Phase 11. Reorganized 2026-05-04 to capture
the new ideas/requirements/rules dump.

---

## Currently broken (priority order)

### 1. PreviewPanel shows the wrong document bitmap
The "framework showdown.pdf" preview tab is rendering "LBLF.pdf" content (Part 1
— Inventory…). Likely a cache key collision in `frontend/src/utils/docRender.ts`
(`getCachedPreview` / `setCachedPreview`) — the cache keys on `file.id`, but the
bitmap is being reused across cards. Check the `useEffect` in
[ThumbnailRenderer.tsx](frontend/src/components/ThumbnailRenderer.tsx) — verify
the cancellation flag actually discards the wrong bitmap when files swap quickly.

### 2. Face recognition still misses extreme close-ups (B&W eye-only crop)
`buffalo_l` RetinaFace requires landmarks (eyes + nose + mouth + jaw). An
eye-only B&W crop is structurally below the model's capability; lowering
`det_thresh` to 0.3 helped marginal cases but not this one. Options:
- Add a "verify person" UI flow so the user can manually attach a face crop to
  a person (stop trying to auto-detect what the model can't see).
- Try a second-pass detector (e.g. mediapipe) only when `buffalo_l` returns 0
  faces and `face_likelihood > 0.7`.

---

## Done since the last reorg

- **Phase 10 — doc card UI**: FileCard 4:3 uniform, ThumbnailRenderer fills
  edge-to-edge.
- **Phase 11 — AI Vision summaries (v1)**: BLIP captions + DistilBART doc
  summaries + sumy fallback + named-person splice + Account regenerate.
- **Phase 11 v2 — Florence-2 + Qwen2.5 rewriter (2026-05-04)**:
  - Florence-2-large primary captioner (`<MORE_DETAILED_CAPTION>` for body,
    `<OCR>` for whiteboard/classroom/screenshot/document scenes).
  - Qwen2.5-1.5B-Instruct rewriter takes (caption, names, OCR, scene) and
    produces one natural search-friendly sentence.
  - Regex `_clean_caption` + `_splice_names` + first-person pronoun rewrite
    kept as deterministic fallback when either model is unavailable.
  - BLIP retained as a caption-only fallback.
  - `pyproject.toml` adds `accelerate>=1.0`; transformers stays pinned `<5.0`.
- **PreviewPanel layout (2026-05-04)**: when the panel opens, the gallery
  shifts right (`md:pr-[480px]`) and a `backdrop-blur-[2px]` overlay sits
  between gallery and panel so the preview reads as the focal element.
- **Pronoun rewriting (regex)**: `_polish_after_splice` drops "that is" /
  "who is" filler and rewrites third-person → first-person when the spliced
  name is "Me" / "I".
- **RetinaFace `det_thresh=0.3`** (was 0.5) — picks up borderline B&W faces.
- **Search-click bug**: removed the click-away `setQuery("")` in SearchBar.
- **Phase 12 — folders + project status (2026-05-04)**:
  - Migration 0010 — new `folders` table (recursive `parent_folder_id`,
    soft delete, unique partial index on `(user_id, parent, lower(name))`)
    plus `images.folder_id`, `images.status`, `images.status_color`.
  - `Folder` model + `FolderRead/Create/Update`, `ImageMove`, `StatusSet`
    schemas.
  - `backend/api/folders.py` — `GET / POST / PATCH / DELETE` with
    recursive-CTE soft-delete and cycle-prevention on moves.
  - `PATCH /images/{id}/move` (drag-into-folder) and
    `PATCH /images/{id}/status` (set/clear project status).
  - FE: `FolderCard`, `NewFolderModal`, `Breadcrumb`, `JumpToTop`,
    `StatusPicker`, `ProfileSection`. `filterStore` carries `folderId` +
    `folderPath`. `FileGrid` mixes folders-then-files.
  - Search and person-view bypass folder scope via `?all=true`.
- **C7 UI polish**: jump-to-top button (scrolls past 1× viewport),
  preview panel tightened (`rounded-[28px]` corners, `top-4 right-4`
  insets, h-12 header).
- **C4 profile**: AccountModal now leads with a `ProfileSection` that
  edits email and password. Both flows re-auth with the current
  password (locally) before submitting; password change uses the same
  rules as signup.
- **Login button polish**: `bg-fg text-fg-inverse rounded-full h-12`
  (Apple-inverse pill) on the LoginPage.
- **Phase 13 — Section C (2026-05-04, second pass)**:
  - **C1**: folder action menu (Popover with Rename / Set status /
    Delete) replacing the no-op kebab; HTML5 drag-and-drop file→folder
    via custom MIME `application/x-istore-image`; sort dropdown
    (`SortMenu.tsx`) with 6 modes — folders still render first.
  - **C2 (scaffold)**: `cloud_links` + `cloud_files` migration 0014;
    `backend/cloud_sync.py` worker stub with provider scopes + clear
    NotImplementedError gating; `backend/api/cloud.py` endpoints
    returning 503 with a "needs A2/A3" message; FE `connectProvider`
    button surfaces the message rather than failing silently.
  - **C3 (scaffold)**: `image_geo` migration 0013; EXIF GPS extract in
    `backend/image.py` gated by `gps_retention` consent; `/images/geo`
    endpoint; FE `MapView` using maplibre-gl + supercluster (lazy
    `@vite-ignore` import, list fallback when libs absent); Map / Grid
    toggle in topbar.
  - **C4**: per-scope consent toggles via new generic
    `/consent/{kind}/grant|withdraw` endpoints + `PrivacyPanel.tsx`;
    storage breakdown card in `AccountModal`; email re-verification
    on change (`UserManager.on_after_update` hook).
  - **C6**: SMTP settings in `config.py`; `backend/email_send.py`
    templates wired into `UserManager.on_after_register /
    on_after_request_verify / on_after_forgot_password /
    on_after_reset_password`; fastapi-users `get_reset_password_router`
    + `get_verify_router` mounted; `recovery_codes` migration 0011 +
    `RecoveryCode` model + argon2-hashed 8-code regenerate +
    `/account/recovery-codes/login` (stateless JWT issue with
    constant-time mismatch path); LoginPage gains
    forgot/reset/recovery/verifying flows that consume `?action=&token=`
    URL params; AccountModal grows a Recovery Codes row + display
    modal that copy-to-clipboards the freshly-issued codes.
  - **C7**: light-theme tokens aligned to Apple HIG system grays
    (`--bg-page` → #F5F5F7, `--bg-elevated` → #F2F2F7, `--bg-border`
    → #E5E5E7); shadow `md` softened to a layered Apple-style stack.
  - **C8**: `current_superuser` dependency; `backend/api/admin.py` with
    `/admin/storage`, `/admin/users`, `/admin/users/{id}/quota`,
    `/admin/audit`; `users.quota_bytes` migration 0012 +
    `/storage/usage` honors per-user override; FE `AdminPanel.tsx`
    fullscreen dialog with Storage / Users / Audit tabs; entry button
    only renders for `is_superuser=true`.

- **Phase 13 audit pass (2026-05-04, third pass)**:
  - Fixed `backend/api/cloud.py` `revoke_link` — `from __future__
    import annotations` + `-> None` return type combined with
    `status_code=204` tripped FastAPI's
    `is_body_allowed_for_status_code` assertion (the future import
    leaves the annotation as the *string* `"None"`, which FastAPI
    treats as a serialized response model). Dropped the `-> None`
    annotation and left a comment so we don't reintroduce it.
  - Fixed `backend/email_send.py` — dev-mode stub used `logger.info`,
    but uvicorn silences non-uvicorn loggers below WARNING by
    default, so verification + reset links vanished into the void on
    a fresh install. Switched to `sys.stderr.write` + flush so the
    link prints unconditionally; verified end-to-end by triggering
    `/auth/forgot-password` and seeing the JWT show up in the
    uvicorn terminal.
  - Migrations 0011–0014 applied successfully against the dev DB
    (`account_recovery`, `admin_quota`, `image_geo`, `cloud_sync`).
  - Full test suite green: **82/82** in ~133 s.
  - FE type-check green after a one-line fix in
    `MapView.tsx`: `typeof import("supercluster").default` →
    `typeof import("supercluster")` (modern supercluster types
    expose the class as the module's default export, not as a
    `.default` member).

---

## Next-up roadmap (priority groupings)

The buckets below are ordered by the "must" rules at the bottom of this file:
**security/privacy/compliance work blocks anything that touches user data**.
Don't ship new ML/UX features until the corresponding privacy + security gates
are in place.

### A. Hard requirements before any public deployment

> **NEVER mark these "done" until verified end-to-end with tests + a security
> review.** Consent does not override illegal storage, secret leakage, or
> insufficient encryption.

#### A1. Upload validation hardening
- Validate MIME type *and* magic bytes (don't trust `Content-Type`).
- Re-decode every image through Pillow before storage; reject anything that
  fails decode (catches polyglot uploads).
- Strip dangerous metadata (XML payloads in SVG, embedded scripts, etc.).
- Per-user upload size + count rate limits.
- Reject zip bombs and oversized archive contents *after* archive support
  lands (see C2).
- Quarantine bucket for files awaiting validation; only promote to
  `originals` after all checks pass.

#### A2. Encryption at rest + in transit
- TLS everywhere — API, MinIO, frontend, admin tools. No plaintext HTTP in any
  environment label other than `dev`.
- MinIO server-side encryption (SSE-S3 or SSE-KMS) on all three buckets.
- Postgres data-at-rest encryption (volume-level on the host or `pgcrypto`
  for the most sensitive columns: face embeddings, EXIF, summary text).
- Backups encrypted with a key the host doesn't store.
- Separate encryption keys for biometric tables vs. content tables.

#### A3. Secret management
- No `.env` in git; commit a `.env.example` with placeholders only.
- Rotate `JWT_SECRET`, MinIO root creds, DB passwords on a schedule.
- Move secrets out of compose files into a secret manager (Vault, Docker
  secrets, or platform-native).
- Audit log every secret access (who read, when, from where).

#### A4. Access control + audit
- RBAC on top of per-user filtering — admin/superuser/user roles.
- Signed download URLs for `originals` + `served`; expire ≤ 5 min.
- Rate-limit auth endpoints (5/min per IP, exponential backoff after lockout).
- Brute-force protection (account lock after N failed attempts).
- Append-only audit log: auth events, deletes, consent changes, admin actions.
- Postgres RLS policies for biometric tables (currently app-layer enforced).

#### A5. Deletion that actually deletes everything
The single most-skipped feature. When a user deletes an image *or* their
account, all of these must go:
- `originals` object
- `served` variant
- thumbnail caches (frontend IndexedDB + backend if any)
- CLIP embedding row
- AI Vision summary text + topic + points
- EXIF row
- detected face crops in `faces` bucket
- face embeddings (`faces.embedding`)
- face detections (`face_detections`)
- person rows that have no remaining faces
- bandit reward / arm history (or anonymized)
- audit log entries reference but are NOT deleted (legal retention)
- backup invalidation eventually (document the retention)
Add an integration test that uploads, deletes, and asserts every table + bucket
returns 0 rows / 0 objects for the target.

#### A6. Compliance scaffolding
- `LICENSE` file (Apache 2.0 likely best fit — patent grant + permissive).
- `PRIVACY.md` — what we collect, why, retention, deletion process, embedding
  handling, biometric handling.
- `TERMS.md` — usage terms, dispute resolution, age gate.
- `SECURITY.md` — disclosure email, supported versions.
- `DATA_PROCESSING.md` — for B2B users (DPA template).
- Cookie banner if any cookies are set; document `Set-Cookie` for every cookie.
- Age gate (under-13 prohibited unless full COPPA flow is built).
- Consent log: every consent grant/revoke gets a row with timestamp, IP,
  user-agent, scope.

#### A7. Repo hygiene
The user dump explicitly said: don't push test scripts that help attackers.
- Audit current git history for: real user images, real EXIF, real
  embeddings, production credentials, DB dumps. Use `git log --stat -p` and
  `git filter-repo` if anything sensitive is found.
- `.gitignore` everything that could leak (`.env`, `data/`, `*.dump`).
- Tests use synthetic fixtures only (current state is mostly fine — the tiny
  PNGs in `test_summarize.py` are generated, not user photos).
- Remove `frontend/node_modules/` from git tracking if it's there (the
  current `git status` shows tracked changes to it, which is wrong).
- Add a CI step that fails on committed secrets (gitleaks / trufflehog).

### B. Privacy / consent

> **The Rules at the bottom of this file are non-negotiable.** Re-read before
> writing anything that touches embeddings, faces, or AI tags.

#### B1. EXIF / GPS handling
- Strip EXIF (especially GPS) by default on upload. Store only the fields the
  user explicitly opts into ("show camera info on previews").
- Surface the choice in the consent flow at signup, not buried in settings.
- Redact GPS from the EXIF preview row even when the data exists in the file. ( unless user consents.)

#### B2. AI/biometric consent flow
- Single explicit opt-in per scope: `face_detection`, `face_recognition`,
  `semantic_search`, `ai_summary`, `bandit_compression_telemetry`.
- Each scope has its own retention period and revocation flow.
- Revoke = immediate deletion of derived data, not "we'll stop processing
  next month" — the lawsuits target the deceptive lag.
- Display the current consent state in Account, with the date granted, the
  scope text the user agreed to, and a one-click revoke per scope.

#### B3. Export + portability
- `/account/export` returns a zip: originals + a JSON sidecar with all
  metadata + embeddings (encrypted) + summaries + people + consent log.
- Re-export must be rate-limited (1/day per user).
- Email a download link rather than streaming inline; link is signed,
  expires in 24h.

#### B4. Retention sweepers
- Originals: 30-day default (already documented), configurable per-user up
  to a cap.
- Bandit telemetry: 90 days then anonymize.
- Audit log: 1 year then archive.
- Deleted-account grace: 30 days then hard-delete everything.
- Each sweeper writes to the audit log so we can prove deletion happened.

### C. Features the user asked for

> Each item below has a **status** line (✅ done / 🟡 partial / ⏳ next /
> ⛔ blocked-on) and a **Next concrete step** so anyone picking this up
> can start without re-deriving scope.

#### C1. Folders, archives, and project organization
- ✅ **Folders end-to-end**: schema, API, FE, breadcrumb, click-to-enter,
  unique-name guard, recursive soft-delete, cycle-safe move.
- ✅ **Project status labels** on files (chip on FileCard + PreviewPanel
  picker + `PATCH /images/{id}/status`).
- ✅ **Folder action menu** (`FolderCard` Popover): inline rename, set
  status (label + color from a small palette), delete (confirm prompt).
  Backed by existing `PATCH/DELETE /folders/{id}`.
- ✅ **Drag-and-drop file → folder**: `FileCard` is `draggable`, sets
  `application/x-istore-image=<id>` on dragstart; `FolderCard` accepts
  the drop and calls `moveImageToFolder(id, folderId)` with a
  `ring-2 ring-accent` highlight on hover.
- ✅ **Sort controls**: `SortMenu.tsx` in topbar, 6 modes
  (uploaded asc/desc, name asc/desc, size asc/desc). Persisted to
  `filterStore.sortMode`; folders still render first regardless.
- ⏳ **Zip / 7z / tar / rar uploads**: blocked on **A1 upload validation**
  — archive paths are an extra attack surface (zip-slip, zip-bomb).
  When ready:
  1. New endpoint `POST /folders/upload-archive` (multipart). Body
     fields: `file`, optional `parent_folder_id`. Cap raw size at
     ~200 MB; reject anything bigger up-front.
  2. Inspect the archive *before* extracting: total uncompressed size
     ≤ 5× compressed (zip-bomb gate), entry count ≤ 5,000, max depth
     ≤ 10, no entry path containing `..` or absolute prefix
     (zip-slip), no symlinks (Python `zipfile` doesn't extract them
     by default — keep it that way).
  3. Auto-create a folder named after the archive stem.
  4. For each entry, route through the existing image upload pipeline
     so MIME validation, magic-bytes check, and bandit compression
     all apply. Set `folder_id` on each.
  5. Persist `source_archive_id` (column already added in 0010) so a
     future re-pack endpoint can rebuild the archive.
  - **Re-pack on download** (later): `GET /folders/{id}/archive` streams
    a zip of the folder contents in their original layout.
- ⏳ **Sort controls**: grid sort by upload-time / name / size; folders
  always first. Currently always upload-time-desc.

#### C2. Cloud sync (Drive / iCloud / GitHub / etc.)
- ⛔ **Blocked on A2/A3** (encrypted secret storage) — OAuth tokens are
  long-lived credentials and must be encrypted at rest with a key
  the host doesn't store. Don't ship until A2 lands.
- **Concrete plan when unblocked, one provider at a time**:
  1. **Drive first** — biggest user value, cleanest API, well-documented
     compliance ("Limited Use" requirements for sensitive scopes). Steps:
     - Register an OAuth client; only request `drive.readonly` scope.
     - New table `cloud_links(user_id, provider, encrypted_refresh_token,
       scopes, last_synced_at, status)`.
     - Hourly sync worker pulls file listings, diffs against
       `cloud_files(user_id, provider, remote_id, local_image_id,
       remote_modified, sha256)`, downloads new/changed files, runs
       them through the existing upload pipeline with a synthesized
       folder per-source-folder.
     - Conflict resolution: source wins (pull-only), surface
       conflicts in a banner.
     - Compliance check: Google's Limited Use terms forbid using
       Drive content to train models — disable AI summary + face
       scan on synced files unless user opts in per-source.
  2. **GitHub** — only repos the user owns; treat each repo as a
     folder. Skip secrets (.env, credentials.json, *.pem) by
     pattern. Useful but lower demand than Drive.
  3. **iCloud** — no first-party sync API for third parties. Either
     skip, or build a Mac-only Finder companion. Defer.
  4. **Dropbox / OneDrive** — same pattern as Drive; lower priority.

#### C3. Sort by GPS location
- ⛔ **Blocked on B1** (EXIF strip on by default) — we can't render a
  map until users have explicitly opted *in* to GPS retention.
- **Concrete plan when unblocked**:
  1. Add `lat REAL`, `lng REAL` columns to `images` (or a sibling
     `image_geo` table for clean B1 deletion). Populate during
     Pass A from EXIF.
  2. New endpoint `GET /images/geo` returns `[id, lat, lng,
     thumbnail_url]` only for images with consent + non-null GPS.
  3. FE: a "Map" view tab next to "Grid". Use **maplibre-gl** (BSD,
     no token, Mapbox-compatible style) over OpenStreetMap raster
     tiles to avoid Mapbox's commercial license. Cluster with
     `supercluster`.
  4. Click cluster → grid view filtered to that lat/lng bbox.

#### C4. Profile / settings area
- ✅ **Email + password change** (`ProfileSection.tsx`) — re-auths
  with current password before submitting, then re-issues JWT.
- ⏳ **Display name edit**: 5 lines added to `ProfileSection`. Wait
  until display_name is actually surfaced anywhere in the UI.
- ⏳ **Email re-verification on change**: blocked on **C6**.
  Currently the email column updates immediately. Once SMTP is wired
  up (C6), the flow becomes:
  - PATCH /users/me with new email → server stages it as
    `pending_email`, sends a verification mail, clears
    `is_verified=False` until clicked.
- ⏳ **Per-scope consent toggles**: today the consent modal is a
  single bundle. Split into individual scopes (face_detection,
  face_recognition, semantic_search, ai_summary,
  bandit_compression_telemetry). New endpoint `PATCH /consent/{scope}`
  with body `{state: 'GRANTED' | 'WITHDRAWN'}`.
- ⏳ **Storage usage breakdown + retention controls**: `StorageBar`
  already shows totals. Add a "Storage" section to AccountModal
  with the per-category breakdown (already on `/storage/usage`)
  and a slider for original-retention (default 30 days).
- ⏳ **Cloud provider connect buttons**: blocked on C2.

#### C5. Easy setup script
- ⏳ **Not started.** Real chunk of work — a Python script that:
  1. Detects platform (Win/Linux/Mac), available storage drives
     (`psutil.disk_partitions`), prompts the user to pick a path.
  2. Probes for CUDA (`nvidia-smi`), AMD (`rocm-smi`), Apple Silicon
     (`uname -m == arm64` on Darwin). Suggests the right torch
     wheel index URL.
  3. Generates `.env` with a fresh `JWT_SECRET` (`secrets.token_urlsafe(48)`),
     MinIO root creds, DB password.
  4. Either runs `docker compose up -d` or installs natively
     (asks the user).
  5. Optional: a tiny `Tk` or `webbrowser`-launched single-page
     wizard so the visual matches the app (Tailwind via CDN is fine
     for a one-page wizard).
- **Concrete first step**: write a CLI-only `scripts/setup.py` that
  prints a numbered checklist + writes `.env`. Wizard UI later.

#### C6. Account recovery
- ⛔ **Blocked on email infra.** Pick one:
  1. Bring-your-own SMTP (cheap, works with Gmail App Passwords for
     dev). Variables: `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`,
     `SMTP_FROM`.
  2. Transactional provider (Postmark / SendGrid / Resend).
  Either way: new module `backend/email.py` with a
  `send_template(to, template, ctx)` that the recovery + verification
  flows call.
- **Then**:
  1. **Forgot password**: fastapi-users already has the routes —
     `POST /auth/forgot-password`, `POST /auth/reset-password`.
     Wire `UserManager.on_after_forgot_password` to send the email.
  2. **Email verification on signup**:
     `UserManager.on_after_register` → send verification mail;
     `is_verified=False` until clicked. Block sensitive endpoints
     (delete account, export, password change) behind `is_verified`.
  3. **Recovery codes**: new `recovery_codes(user_id, code_hash,
     used_at)`. Generate 8 codes once at signup, show them once,
     store argon2-hashed.
  4. **TOTP 2FA**: `pyotp` + a QR code endpoint, second-factor
     check in the auth flow. Lower priority.

#### C7. UI polish
- ✅ **Jump-to-top** button (`JumpToTop.tsx`) — appears past 1×
  viewport, smooth-scrolls.
- ✅ **Preview panel** — `rounded-[28px]`, `top-4 right-4` insets,
  `h-12` header (was `h-14`), border softened to `border-divider/80`.
- ⏳ **Light theme**: needs token review — Apple-style means:
  - Card background closer to pure white (`#FAFAFA` → `#FFFFFF`).
  - Page background a faint cool gray (`#F5F5F7`).
  - Border tokens a single hairline (1px, `#E5E5E7`).
  - Shadows strictly small (`0 1px 2px rgb(0 0 0 / 0.04)`) — heavy
    shadows look "Material" not "Apple".
  - Increase line-height + tighten letter-spacing on headers
    (`tracking-[-0.01em]`).

#### C8. Dev dashboard
- ⛔ **Blocked on A4** (RBAC). The route only makes sense behind
  `is_superuser`, and right now `is_superuser` only protects a single
  retention sweeper endpoint.
- **Concrete plan when unblocked**:
  1. New router `backend/api/admin.py` mounted at `/admin/*`,
     all endpoints depend on a `current_superuser` from fastapi-users.
  2. `GET /admin/storage` — total bytes per bucket, per-user
     breakdown (top 50). Reuses the storage endpoint with an
     `as_admin` flag.
  3. `GET /admin/users` — list, with quota / total used / last
     active.
  4. `PATCH /admin/users/{id}/quota` — set per-user quota.
  5. `GET /admin/audit` — paginated audit log viewer
     (filter by user, action, time window).
  6. FE: `/admin` route (only rendered if `user.is_superuser`),
     same component library as the main app.

### D. Phase 9 — Hardening (carried over)

- arq + Redis worker for the vision pipeline (pure refactor; pipeline
  function unchanged, only call site moves).
- GPU inference subprocess with batching (50ms fill window) + Unix-socket
  IPC.
- Places365 for finer scene categorization (365 labels).
- RAM++ for richer tag generation (~4k vocab; replaces the curated CLIP
  zero-shot list).
- structlog + OpenTelemetry traces.
- GPU OOM back-pressure to arq (block when queue depth > N).
- Locust load test at 100 concurrent uploads.
- Nightly DB + MinIO backups.

---

## Rules — non-negotiable

These come straight from the user dump on 2026-05-04. **Read these before
designing anything that handles user data.**

### Privacy
1. **Informed consent.** Users must understand what data we collect, what
   AI processing happens, what's stored, how long, and how to delete. Plain
   language. Especially for embeddings, faces, semantic search, biometric
   features.
2. **Security.** HTTPS, encryption (at rest + in transit), access controls,
   secure storage, rate limiting, secret management, audit logging,
   deletion systems. If we collect sensitive data and leak it from poor
   security, consent does not save us.
3. **Data minimization.** Only collect what's necessary. Embeddings for
   semantic search → reasonable. Personality vectors → much harder to
   justify.
4. **User control.** Export, delete, revoke consent, disable AI features,
   remove biometric data.
5. **Honest disclosure.** No secret model training, no quiet expansion of
   data usage. The biggest lawsuits come from deceptive practices.
6. **Consent does not override everything.** Even with "I agree", some
   things stay illegal: unfair biometric practices, deceptive AI claims,
   unsafe retention, minor biometric data, discrimination profiling,
   inadequate security, hidden processing.
7. **Safest framing for IStore**: "Private AI-assisted photo organization
   for the account owner." Dangerous framing: "Global people recognition
   and profiling." Huge legal difference.

### Biometrics (the highest-risk surface)
The line is between "this image probably contains a face" (low risk) and
"this is Jason / find all photos of this person / cluster these identities"
(BIPA + GDPR special-category territory).
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
- **BIPA** (Illinois) — written informed consent, public retention policy,
  deletion schedule, no profit, secure storage. Violations are expensive.
- **COPPA** — under-13 users prohibited unless fully compliant.

### AI/ML guidelines
- Treat embeddings as sensitive data. Encrypt them. Delete with the source
  image. Never expose raw vectors. Never allow cross-user similarity.
- Every vector query scoped to the authenticated owner.
- No cross-user index, no shared semantic store, no accidental leakage.

### Security must-haves
- Encryption: HTTPS everywhere; SSE on object storage; PG volume encryption
  + `pgcrypto` for biometric columns; encrypted backups.
- Secrets: never commit, rotate on schedule, vault/secret manager.
- Access control: RBAC, signed URLs, rate limits, brute-force lockout,
  audit log.
- File validation: MIME + magic bytes, re-decode, size cap, malware scan,
  metadata strip.
- Logging: never log JWTs, raw bytes, embeddings, EXIF GPS, face metadata.

### Repo rules
Never commit: real user images, embeddings, EXIF-rich samples, prod
credentials, DB dumps, biometric data.
Use synthetic fixtures only.

### Pre-launch checklist
1. Security audit (auth/authz/JWT/upload/storage/secrets/rate-limit/deps).
2. Privacy audit (every stored field — why, how long, can users delete it).
3. Compliance checklist (Privacy + Terms + deletion + export + consent +
   cookies + biometric opt-in).
4. Infra hardening (HTTPS, encrypted backups, private object storage,
   firewall, monitoring, malware scanning).
5. AI/ML review (no cross-user leakage, no hidden biometric processing,
   no accidental training on user data).
6. Deletion testing (every table + bucket + cache + backup eventually).
7. Threat modeling (image leakage, metadata leakage, biometric misuse,
   bucket misconfig, search isolation failures).
8. External review (another dev + a security person + a privacy person).

### Required minimums before public release
Privacy Policy · Terms of Service · License · Security policy · Contact
email · Documented deletion process · Backup strategy · HTTPS · Strong
secrets.

---

## Quick reference

- Run backend: `.venv/Scripts/python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --log-level info`
- Run tests: `.venv/Scripts/python.exe -m pytest`
- Apply migrations: `.venv/Scripts/python.exe -m alembic upgrade head`
- Force-regenerate every summary: `POST /images/backfill-summaries?force=true&limit=500` (or **Account → Regenerate**)
- After any change to `backend/`, **restart uvicorn** — it doesn't hot-reload
  Python module changes by default.
