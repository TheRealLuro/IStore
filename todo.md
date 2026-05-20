# neuthek — Roadmap

> Project renamed from **IStore → neuthek** on 2026-05-09. The
> codebase, compose, Caddyfile, env vars, and tests have been
> rebranded (see "I.bis.1" below for the landed work). What's left
> trails into hosting/domain/brand-surface and the actual data
> migration (DB rename + bucket copy) — see I.bis.2 / I.bis.3.

This file tracks **what's still open** — broken, partial, or planned.
Shipped work isn't listed here; read `git log` for that.

**Next up:** Sprint D (sharing + onboarding). Sprint C compliance
work (A2/A4/A5/A6/A7) all shipped 2026-05-16; D7 (best-of picker) +
C8.5–C8.7 (per-user fair queue + heavy-endpoint rate limits + admin
Developer tab) shipped 2026-05-17. See §11. Weekly drafts of the
public /updates entry live in [updates.md](updates.md) — collect
each shipped item there as it lands so the Friday publish is a
copy/paste.

---

## 2. Compliance / hard requirements (block public deployment)

> NEVER mark these "done" until verified end-to-end with tests + a
> security review. Consent does not override illegal storage, secret
> leakage, or insufficient encryption.

### A2. Encryption at rest + in transit ✅ SHIPPED 2026-05-16
> Code, ops tooling, and docs all in. Per-deployment knobs live in
> [.env.example](.env.example) and the operator checklist is in
> [SECURITY.md](SECURITY.md). Boot-time validator in
> [backend/security.py](backend/security.py) fails fast on any
> drift. The `/admin/system` `encryption` block surfaces the live
> posture (including last-backup timestamp) to the dashboard. 11
> pytest cases in
> [tests/test_encryption_posture.py](tests/test_encryption_posture.py).
>
> Pieces:
> - **TLS termination** — `docker-compose.tls.yml` + `Caddyfile`
>   overlay with auto-Let's-Encrypt, HSTS, HTTP/3, sensor lockdown.
>   Documented in SECURITY.md "TLS termination."
> - **Object storage SSE** — `storage._sse` routes biometric vs.
>   content scopes to distinct keys under `sse-kms`; every PUT
>   audited.
> - **Postgres at-rest** — `host_volume_confirmed` attestation
>   knob with three documented paths (cloud-managed, LUKS, OS
>   disk encryption) in SECURITY.md.
> - **Encrypted backups** — `scripts/backup-db.sh` (+ `.ps1` for
>   Windows hosts) does `pg_dump | age recipient → local + offsite`,
>   `scripts/restore-db.sh` is the inverse. Sidecar container
>   ([Dockerfile.backup](Dockerfile.backup) +
>   [docker-compose.backup.yml](docker-compose.backup.yml)) bundles
>   `age` + `postgresql-client` + `mc` so the host needs no extra
>   binaries. The script writes a `backup.completed` audit row;
>   `/admin/system` reads the most recent one and reports
>   `at` / `bytes` / `upload_dest` / `age_seconds`.
> - **Secret-box (`CLOUD_ENCRYPTION_KEY`)** — required + Fernet-valid
>   at boot in prod, surfaced in posture as valid/invalid/unset
>   without leaking the key. Rotation helper (zero-downtime
>   re-encrypt) tracked under §A3.
>
> **Not in scope here** (separate workstreams):
> - **E3 user-key vault** — passwords get E2E client-side Argon2id +
>   WebCrypto AES-GCM. Untouched; lives under §E3 below.
> - **Secret rotation worker** (§A3) — currently a key change
>   orphans existing ciphertext until the migration tool ships.

### A3. Secret management 🟡
> Partial — `.env` hygiene + gitleaks coverage shipped under §A7,
> `SECRET_MANAGER` prod-boot validator + `TRUST_PROXY_HEADERS`
> shipped under §A4. Rotation worker + secret-access audit are
> the remaining open pieces.

**Shipped:**
- ✅ No `.env` in git; `.env.example` is the only env-shaped file
  tracked. `.gitignore` blocks `.env`, `.env.local`, `.env.*.local`;
  gitleaks CI + pre-commit hook keep it honest (§A7).
- ✅ Move secrets out of compose files into a secret manager —
  `SECRET_MANAGER=docker_secrets` (or a platform-native manager) is
  required by the `validate_production_settings` boot check (§A2).
- ✅ Auth-event audit (login, role mutation, share, consent grant)
  via `add_audit()` covers every endpoint that *uses* a secret.
- ✅ XFF-aware `client_ip()` so audit `details.ip` is real per
  `TRUST_PROXY_HEADERS` (§A4).

**Open:**
- ⏳ Rotate `JWT_SECRET`, MinIO root creds, DB passwords on a
  schedule. Document the rotation cadence in SECURITY.md and wire
  a scheduled task on the operator side.
- ⏳ Audit log every secret *access* (who read, when, from where) —
  currently we audit secret-bearing actions, not raw secret reads
  out of the secret manager.
- ⏳ **Secret-box rotation worker** — rotating
  `CLOUD_ENCRYPTION_KEY` today invalidates all existing ciphertext
  (TOTP secrets + cloud-OAuth refresh tokens). Ship a migration
  tool that reads with `CLOUD_ENCRYPTION_KEY`, writes with
  `CLOUD_ENCRYPTION_KEY_NEXT`, then promotes — the rotation flow
  is documented in [SECURITY.md](SECURITY.md) "Secret-box rotation."

### A4. Access control + audit ✅ SHIPPED 2026-05-16
> RBAC, audit, RLS, signed-URL TTL cap, brute-force lockout — all in.
>
> - **RBAC** (`backend/auth/users.py`): `users.role` column with check
>   constraint `IN ('user', 'admin', 'superuser')` (migration 0016).
>   Three dependencies: `current_active_user` (everyone),
>   `current_admin_user` (role admin or superuser), `current_superuser`
>   (fastapi-users `is_superuser=True`). Role-mutation endpoint
>   `PATCH /admin/users/{id}/role` is superuser-only AND refuses to
>   demote the **last remaining** superuser (new in this pass).
> - **Signed download URLs** (`backend/signed_urls.py`):
>   `make_signed_download` clamps to `download_url_ttl_max_seconds`
>   (default 300 = 5 min); `verify_download` rejects any URL whose
>   `expires` is more than the cap from `now()`. Defense in depth
>   against a config drift that would issue long-lived links.
> - **Auth rate limit + exponential backoff**
>   (`backend/security.py::SecurityControlsMiddleware`): 5/min per IP,
>   24h fail-counter, lockout window
>   `min(base * 2^(failures - threshold), max)` with base=60s, max=15m.
>   Applied to `/auth/jwt/login`, `/auth/jwt/login-totp`,
>   `/auth/forgot-password`, `/auth/reset-password`,
>   `/auth/request-verify-token`, `/account/recovery-codes/login`,
>   `/shares/claim`, and `/shares/preview/{token}`.
> - **Append-only audit log** (migration 0016 + refined 0026): trigger
>   `prevent_audit_mutation` raises on every UPDATE except the one
>   permitted transition (`user_id IS NOT NULL → NULL` for the
>   anonymization sweeper, every other column unchanged) and on every
>   DELETE. Auth events, deletes, consent changes, admin actions, share
>   ops, and the new `admin.search` row all go through `add_audit()`.
> - **Postgres RLS** (migrations 0016 + 0027): FORCE ROW LEVEL SECURITY
>   on biometric tables (`faces`, `face_detections`, `persons`) plus
>   `image_geo`, `feedback_events`, `consent_records`, `recovery_codes`,
>   `bandit_state` (`user_id::text = current_setting('app.current_user_id')`)
>   and `share_grants` (dual-perspective predicate covering both
>   `sharer_user_id` and `recipient_user_id`). RLS context is set
>   via `set_current_user_id()` in every auth dependency; dev/test
>   uses `app.rls_bypass=on`.

### A5. Deletion that actually deletes ✅ SHIPPED 2026-05-16
> `backend/deletion.py::hard_delete_images` is the single source of
> truth; called from `/images/{id}` DELETE, `/images/bulk-delete`,
> `/account/trash/empty`, `/account/delete`, and
> `retention.sweep_scheduled_account_deletes`. The function covers
> every artifact from the A5 checklist:
>
> | Artifact | Path |
> |----------|------|
> | `originals` blob | Phase 2 — explicit `storage.delete` |
> | `served` blob | Phase 2 — explicit `storage.delete` |
> | thumbnail blob | Phase 2 — explicit `storage.delete` |
> | CLIP embedding row | Column on `images`; cascades with row delete |
> | AI summary text/topic/points | Columns on `images`; cascade |
> | EXIF / GPS row (`image_geo`) | FK CASCADE on `image_id` |
> | face crops (`faces` bucket) | Phase 2 — explicit `storage.delete` per `FaceDetection.crop_blob_key` |
> | face embeddings (`faces.embedding`) | Phase 5 — explicit delete of orphan faces |
> | face detections (`face_detections`) | FK CASCADE on `image_id` |
> | orphan persons (`persons.face_count == 0`) | Phase 6 — explicit |
> | image_tags m2m | FK CASCADE on `image_id` |
> | share_grants on these images | FK CASCADE on `image_id` |
> | feedback_events for these images | FK CASCADE on `image_id` |
> | cloud_files pointers | Phase 3 — explicit delete |
> | bandit reward / arm history | Phase 8 — opt-in via `reset_bandit=True` (account-delete only) |
> | audit_log | NOT deleted; append-only trigger preserves chain of custody |
>
> **FE cache eraser** (`frontend/neuthek/src/cache-eraser.js`):
> `eraseImageCaches(qc, [imageId, ...])` invalidates every list-level
> query, `removeQueries` per id, revokes any registered blob URLs,
> and clears matching `CacheStorage` entries (no-op today; forward-
> compat for a future service worker). Called from gallery card
> delete, preview-panel delete, and the bulk-bar delete.
>
> **Backup retention**: SECURITY.md "Encrypted backups → Retention +
> GDPR Article 17" documents the two acceptable paths (30-day
> rolling pruning or active backup re-write) and provides an
> operator checklist.
>
> **Integration test**: `tests/test_a5_full_deletion.py` —
> seeds an image with every sibling row + bucket object, asserts
> per-image delete leaves 0 rows / 0 objects, preserves bandit
> state + consent records + the audit row referencing the image_id;
> account-level delete additionally wipes bandit_state. A signed-URL
> TTL-cap test asserts a 1-hour forged signature fails verification.

### A6. Compliance scaffolding ✅ SHIPPED 2026-05-16
> - **PRIVACY.md** rewritten end-to-end: 12 sections covering what
>   we collect / why / retention / deletion / consent log /
>   cookies-vs-localStorage / age gate / sharing / embeddings &
>   biometrics / data-subject rights / children / international
>   transfers / change-control. Mirrors the model layer 1:1.
> - **SECURITY.md** — disclosure email `security@neuthek.app` +
>   "Supported versions" section already shipped (verified by the
>   §A7 hygiene test `test_security_doc_contains_disclosure_email`).
> - **DATA_PROCESSING.md** expanded to a proper DPA template with
>   12 sections (definitions, scope, processing purposes, security
>   TOMs, sub-processors, data-subject rights, retention, breach
>   notification, international transfers, audits, liability, term).
>   Operator-placeholder fields marked `[Operator: …]` for
>   customization before signing.
> - **Cookie banner copy** rewritten — neuthek doesn't set cookies
>   (`test_backend_does_not_set_cookies` keeps that true). Banner
>   now describes what we actually do: `localStorage` for theme,
>   recent searches, and the JWT. PRIVACY.md §4 carries the full
>   storage inventory.
> - **Age gate** — explicit "I am at least 13 years old" checkbox
>   added to the consents flow ([consents.jsx](frontend/neuthek/src/consents.jsx)).
>   Required to advance past the Terms step. Wired through to the
>   `register()` call (`age13` payload → `age_confirmed` flag);
>   FE blocks with a clean error before submission, backend
>   `UserCreate._require_age_gate` validator backstops at the API
>   boundary.
> - **Consent log** — every grant/withdraw writes a `consent_records`
>   row with `granted_at` (UTC), `ip`, `user_agent`, `consent_kind`,
>   `state`, `policy_version`, `policy_text_sha256`, `signature_text`.
>   IP capture standardized to use `client_ip(request)` in
>   [backend/consent.py](backend/consent.py) so it honors the
>   `TRUST_PROXY_HEADERS` setting (matches every other per-IP
>   surface). RLS forces row-level isolation on `consent_records`
>   (migration 0027).

### A7. Repo hygiene ✅ SHIPPED 2026-05-16
> - **`.gitignore`** tightened: added `*.age`, `*.gpg`, `*.enc`,
>   `*.kdbx`, `id_rsa*`, `id_ed25519*`, `*.ovpn`, `.netrc`,
>   `.npmrc`, `.pypirc`, `.cargo/credentials`,
>   `.docker/config.json`, real-PII-shaped filenames (`*passport*`,
>   `*ssn*`, `*production_*.dump`), AI weight caches
>   (`*.safetensors`, `*.onnx`, `*.gguf`, `*.bin` + node_modules
>   `.bin` shim exemption), and local DB files (`neuthek.db`,
>   `postgres-data/`, `pgdata/`).
> - **CI** — three security jobs in
>   [.github/workflows/security.yml](.github/workflows/security.yml):
>   - `gitleaks` — full-history secret scan, runs on every PR + push
>     to main. Output redacted so a found secret isn't itself leaked
>     by the CI log.
>   - `forbid-real-pii-fixtures` — filename heuristic that fails the
>     build if a real-PII-shaped path (passport, SSN, prod dump, etc.)
>     is in the tracked tree.
>   - `synthetic-fixtures` — fails the build if any binary file
>     (`*.jpg/*.png/*.pdf/*.dump/*.sql/…`) lands under `tests/`.
>     Test bytes are generated in-process via `_png_bytes()` /
>     `_docx_bytes()` / `insert_face()` helpers.
> - **Pre-commit** — [.pre-commit-config.yaml](.pre-commit-config.yaml)
>   runs the same gitleaks scan locally before commit + blocks
>   real-PII-shaped filenames + refuses NEW `node_modules/` paths
>   in the staged diff.
> - **gitleaks config** ([.gitleaks.toml](.gitleaks.toml)) — narrow
>   allowlist (`env.example`, `SECURITY_REVIEW.md`, migrations,
>   tests, CI workflows) + regex allowlist for the constant-time
>   dummy Argon2 hash and the `dev-only-jwt-secret-CHANGE-IN-PROD`
>   sentinel. Real findings remain real.
> - **Pytest invariants** — [tests/test_a7_repo_hygiene.py](tests/test_a7_repo_hygiene.py)
>   asserts the 8 hygiene contracts inside the normal test run so
>   a regression shows up in the dev loop before CI:
>   (1) no binary fixtures under `tests/`,
>   (2) compliance docs exist + non-empty,
>   (3) SECURITY.md disclosure email + supported versions,
>   (4) PRIVACY.md covers required A6 topics,
>   (5) CI runs gitleaks against full history,
>   (6) `.gitignore` blocks the canonical sensitive patterns,
>   (7) no `real_*` / `prod_*` path literals in tests,
>   (8) append-only audit-log trigger migration still present.
> - **Repo hygiene doc** —
>   [REPO_HYGIENE.md](REPO_HYGIENE.md) captures the full A7
>   posture + the documented `node_modules/` historical-debt
>   cleanup procedure (the .gitignore covers it but ~8.6k files
>   from before the rule still sit in the index — operators can
>   run the documented `git rm --cached` cleanup at their
>   discretion).

---

## 3. Privacy / consent

### B1. EXIF / GPS handling ✅ SHIPPED 2026-05-16
> Originals bucket sees zero EXIF unless the user opts in. New
> `exif_retention` consent scope (alongside the existing
> `gps_retention`); `store_upload` calls `_strip_exif_bytes` before
> writing to originals when neither scope is GRANTED. PNG / GIF are
> no-ops (no EXIF in the format); JPEG / WebP / TIFF re-encode
> without the EXIF blob. 5 pytest cases in
> [tests/test_b1_exif_strip.py](tests/test_b1_exif_strip.py).
>
> Consent flow surface (UI toggle for `exif_retention`) tracked under
> the consents-modal pass — current FE submits `exif_retention=WITHDRAWN`
> by default at signup, matching the strip-by-default posture.

### B2. Consent BEFORE signup ✅ SHIPPED 2026-05-16
> Register payload now carries `consents: [{kind, state}, ...]` +
> `consent_signature`. The `UserManager.create()` override
> ([backend/auth/users.py](backend/auth/users.py)) extracts the bundle,
> writes the ConsentRecord rows in the same transaction as the User
> row, then audits each as `consent.register.<kind>.<state>`. Unknown
> scopes are silently dropped (one stray field can't break account
> creation). Legacy clients that send no bundle still work — they
> fall through to the post-signup consents modal as before.
> 5 pytest cases in
> [tests/test_b2_register_consents.py](tests/test_b2_register_consents.py)
> + verified end-to-end against the live host backend (2 consent
> rows + 2 audit rows landed on a register call).

### B3. Export + portability ✅ SHIPPED 2026-05-16
> `/account/export` ZIP now carries `clip_embedding` + `summary` +
> `summary_topic` + `summary_points` per image alongside the
> already-shipped persons / faces / consents / audit_log payload.
> Rate-limited to one full export per `account_export_min_hours_between`
> (default 24h) via an `account.export` audit row; returns 429 with
> `Retry-After` once exceeded. 4 pytest cases in
> [tests/test_b3_export.py](tests/test_b3_export.py).
> Signed-email-link variant deferred until SMTP is configured per
> deployment (config knobs already exist in
> [backend/config.py](backend/config.py); endpoint can layer on top
> of the existing export when needed).

### B4. Retention sweepers ✅ SHIPPED 2026-05-16
> Five sweepers in [backend/retention.py](backend/retention.py),
> each idempotent + audit-logged, each exposed via a superuser
> admin endpoint:
>
> | Sweeper | Default horizon | Admin route |
> |---------|-----------------|-------------|
> | `sweep_expired_originals` | per-row `original_expires_at` (default 30d) | `POST /admin/retention/sweep` |
> | `sweep_expired_quarantine` | `upload_quarantine_retention_days` (30d) | `POST /admin/quarantine/sweep` |
> | `sweep_feedback_events` | `feedback_retention_days` (90d) | `POST /admin/retention/sweep-feedback` |
> | `sweep_audit_log_anonymize` | `audit_log_retention_days` (365d) | `POST /admin/retention/sweep-audit` |
> | `sweep_scheduled_account_deletes` | `account_delete_grace_days` (30d) | `POST /admin/retention/sweep-accounts` |
>
> Scheduled deletion flow (§B4 "30-day grace"): migration 0026 adds
> `users.scheduled_delete_at`; `/account/schedule-delete` stamps it
> `now + grace_days` instead of nuking the row, `/account/cancel-delete`
> NULLs it, the sweeper picks up anything past its timestamp. The
> immediate `/account/delete` route still works for "delete now."
>
> Audit-log anonymization works against the append-only trigger via
> a refined version (migration 0026) that permits exactly one
> transition: `user_id IS NOT NULL → user_id IS NULL` with every
> other column unchanged. Everything else still RAISES. 12 pytest
> cases in [tests/test_b4_retention.py](tests/test_b4_retention.py).
>
> Per-user retention cap (originals horizon) — still owed under §C4.5.

---

## 4. Product features

### C1. Folders, files, naming, organization ✅ SHIPPED 2026-05-16
> C1.1 Rename + C1.5 Archive uploads (zip / tar) shipped previously.
> C1.2–C1.6 + the folder-status-from-menu deferral all closed in
> this pass. See [tests/test_c1_folders_tags.py](tests/test_c1_folders_tags.py)
> for the acceptance suite (6 tests covering suggest-names,
> contains_type, clear-history, tag CRUD + cross-user isolation,
> attach-by-label create-on-the-fly).

- ✅ **C1.2 AI-suggested smart names** — `GET /images/{id}/suggest-names`
  returns ≤ 3 filename-safe proposals built from the existing
  Florence-2/Qwen summary signals (topic, summary, scene, captured
  place, named people). Pure read; never re-runs inference. Falls
  back to scene + capture-date when `pending_summary=True` and the
  FE shows a hint that the suggestions will sharpen after the
  summary lands. The rename modal merges the AI proposals with the
  client deterministic suggestions and keeps Regenerate as a
  one-click refetch. The user always confirms — backend never
  auto-renames.
- ✅ **C1.3 Type-pill ∧ folder visibility** — `GET /folders?contains_type=image|video|document`
  uses a recursive CTE that walks each image's folder chain to
  surface every ancestor; only folders whose subtree contains at
  least one file of that category survive the filter. Folders now
  show under the Image / Video / Document type pills (previously
  hidden); mock-only pills (contact / password / gamesave / iot)
  still hide folders.
- ✅ **C1.4 Clear search history** — `DELETE /search/history`
  writes an audit row `search.history.cleared` and returns 204.
  Today the recent queries live in browser localStorage so the FE
  clears them locally too; the endpoint is the forward-compat shim
  for a future server-side store + leaves the audit trail.
  "Clear history" pill at the dropdown bottom calls both paths.
- ✅ **C1.5+ 7z / RAR support** — optional `py7zr` / `rarfile`
  extras land in `pyproject.toml [project.optional-dependencies]
  archives`. When the package is importable, the magic-byte sniffer
  in `backend/archive_upload.py` routes the file through the same
  inspect-then-store pipeline as zip/tar (path safety, depth cap,
  expansion ratio cap). When it isn't, the legacy 415 "repack"
  message surfaces with install instructions.
- ✅ **C1.6 Tag system (status-as-tag unification)** —
  Migration 0028 makes `tags` per-user (FK CASCADE on `users.id`),
  adds `color` + `created_at` + `updated_at`, switches uniqueness
  to `(user_id, lower(label))`, denormalizes `user_id` onto
  `image_tags` for RLS, creates the new `folder_tags` join with
  the same shape, FORCEs RLS on all three tables, and backfills
  every `(user_id, status, status_color)` tuple from `images` +
  `folders` into the new table. Legacy `status` / `status_color`
  columns remain as a read-only shim for one release.
  New REST surface:
    `GET /tags/` · `POST /tags/` · `PATCH /tags/{id}` · `DELETE /tags/{id}`
    `POST /images/{id}/tags` · `DELETE /images/{id}/tags/{tag_id}`
    `POST /folders/{id}/tags` · `DELETE /folders/{id}/tags/{tag_id}`
  Attach supports both `tag_id` (existing) and `label` (create-and-
  attach in one round trip). 18 allowed chip colors;
  case-folded label uniqueness per user. `ImageRead.tags` and
  `FolderRead.tags` arrays surface the attached tags so the gallery
  card renders them without a follow-up fetch.
  FE: [tag-picker.jsx](frontend/neuthek/src/tag-picker.jsx)
  popover handles type-to-filter, Enter-to-create, per-tag recolor.
  Wired into the file card menu's "Tags…" row. The recursive
  inline color picker uses the 18 named tones; the chip itself
  tints via `tagChipStyle(color)` so the picker output drops into
  the card without extra CSS.
- ✅ **Set folder status from menu** — folded into §C1.6. The folder
  context-menu now supports the same Tags picker via
  `POST /folders/{id}/tags`. Status pill replaced by colored tag
  chips on the folder card (FolderRead.tags surfaces them).

### C2. Cloud sync (Drive / iCloud / GitHub / etc.) ✅ SHIPPED 2026-05-16
> Drive + GitHub end-to-end. OAuth state is HMAC-signed (per the
> §A4 fix), refresh tokens are Fernet-encrypted at rest via
> `secret_box` (§A2). 6 pytest cases in
> [tests/test_c2_cloud_sync.py](tests/test_c2_cloud_sync.py).
> Operator-facing setup in [.env.example](.env.example).

**What's in:**
- ✅ `drive.readonly` scope only. Pull-only — `store_upload` is the
  only write path, never back to the remote.
- ✅ `cloud_links(user_id, provider, encrypted_refresh_token, scopes,
   last_synced_at, status)` — schema lives in migration 0014.
  Status enum: `active` / `conflicts` / `error`.
- ✅ Hourly worker —
  [`backend/cloud_sync_worker.py:run_hourly_sweep`](backend/cloud_sync_worker.py)
  launched from `lifespan`. Sweeps every active link;
  `CLOUD_SYNC_INTERVAL_SECONDS` knob; disabled in test/dev via
  `CLOUD_SYNC_HOURLY_ENABLED=false`. Per-link errors don't poison
  the loop.
- ✅ Diff against `cloud_files(user_id, provider, remote_id,
  local_image_id, remote_modified, sha256, remote_parent_path)`.
  When `remote_modified` matches AND `sha256` matches, the file is
  skipped without re-downloading (Drive bumps `modifiedTime` on
  permission edits that don't touch bytes).
- ✅ **Synthesized folder per source-folder.**
  `_ensure_remote_folder_tree` materializes a "Google Drive" /
  "GitHub" root folder per user, then walks every distinct
  `remote_parent_path` and creates the matching subtree
  idempotently. The new ImageRead carries this `folder_id` via
  the existing folder column — no new column needed.
- ✅ **Pull-only; conflicts surfaced in a banner.** The sync worker
  detects "local image was edited after last sync" (uploaded_at >
  last_synced_at, or deleted_at set), refuses to overwrite, writes
  a `cloud.sync.conflict` audit row, and flips the link to
  `status='conflicts'`. The FE
  [`cloud-sync-panel.jsx`](frontend/neuthek/src/cloud-sync-panel.jsx)
  surfaces the affected files in a banner.
- ✅ **Limited Use: AI off by default for synced files.** Migration
  0029 adds `images.skip_ai_training` + `images.source_provider`.
  `store_upload(...skip_ai_training=True, source_provider=...)`
  skips the CLIP / Florence-2 / Qwen vision pass and sets
  `pending_summary=False` + `pending_face_scan=False` so the
  post-commit background workers leave the row alone. User can
  opt-in per source via `POST /cloud/links/{id}/ai-opt-in` — this
  flips the flag in bulk + re-arms the pending flags so the
  workers pick the rows up.
- ✅ **GitHub second.** Own repos only (`affiliation=owner` against
  /user/repos), recursive `git/trees/<branch>?recursive=1` walk.
  Image-extension filter (jpg/jpeg/png/gif/webp/heic/bmp/tiff).
  Secret-pattern skip list covers `.env*`, `id_rsa*`, `id_ed25519*`,
  `*.key`, `*.pem`, `*.p12`, `*.pfx`, `*.kdbx`, `*.dump`, `*.sql`,
  `credentials.json`, `service_account.json`.
- ✅ FE settings panel —
  [`cloud-sync-panel.jsx`](frontend/neuthek/src/cloud-sync-panel.jsx)
  + new "Cloud sync" tab in the Account modal. Connect / Sync now /
  Disconnect / AI opt-in / Conflict banner, all wired to the new
  endpoints via [`frontend/src/api/cloud.ts`](frontend/src/api/cloud.ts).

**Deferred** (not in §C2 scope per spec):
- iCloud / Dropbox / OneDrive — once a user actually asks for them.
- Two-way sync — out of scope; we never write back to remotes.

**Operator setup** ([.env.example](.env.example)):
- `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` /
  `GOOGLE_OAUTH_REDIRECT_URI` — Drive
- `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` /
  `GITHUB_OAUTH_REDIRECT_URI` — GitHub
- `CLOUD_ENCRYPTION_KEY` — Fernet key (required since §A2)
- `CLOUD_SYNC_HOURLY_ENABLED=true` (default) + `CLOUD_SYNC_INTERVAL_SECONDS=3600`

### C3. Map view refinements ✅ SHIPPED 2026-05-18
> 1. ✅ Reverse-geocode worker (`_reverse_geocode_one` in
>    [backend/image.py](backend/image.py) + the `_reverse_geocode`
>    helper in [backend/api/images.py](backend/api/images.py)) fires
>    after upload commit and fills `image_geo.place`. Nominatim
>    cached by rounded coords. Stale rows backfilled lazily by
>    `POST /images/geo/backfill-places`; the FE auto-triggers it on
>    detection of any pending row.
> 2. ✅ Supercluster swap done in
>    [frontend/neuthek/src/map.jsx](frontend/neuthek/src/map.jsx).
>    Old O(N×K) pixel-space loop replaced with a `Supercluster`
>    index built once per item-set change and queried at the current
>    bbox + zoom on each render. Radius 60 px, maxZoom 16, minPoints 2.
>    Click a cluster → `getClusterExpansionZoom` + `flyTo` smoothly
>    expands into smaller clusters or individual pins. Handles
>    10k+ pins without the prior render stall.
> 3. ✅ Grid backdrop on `.map4-canvas .leaflet-container` (subtle
>    28 px grid lines at ~6 % contrast light / ~5 % dark) so a fast
>    zoom-out or pan into unloaded space reads as designed empty
>    space rather than "the map broke." Tiles still cover it once
>    they resolve.
>
> Deferred: per-pin staggered scale-in animation after a fitBounds —
> visual polish, not blocking.

### C4. Profile / settings
- ⏳ **C4.1 Display name on signup** — registration form gains required
  "name" field; persisted as `users.display_name`; used in the
  topbar greeting and across the UI in place of email.
- ✅ **C4.2 "Me" → name binding** — shipped 2026-05-18.
  Backend helper [resolve_self_name](backend/api/people.py) substitutes
  the literal token "Me" (case-insensitive, trimmed) with
  `user.display_name` at every person-rename entry point:
  `name_cluster`, `detect-and-label`, `rename_person`. AI summaries
  automatically splice the user's real name because the Person row
  literally stores it. If the account has no display name yet, the
  endpoint returns 422 with `detail.code = "missing_display_name"`
  and the FE [nameable-chip](frontend/neuthek/src/nameable-chip.jsx)
  catches it: inline prompt → PATCH `/users/me` → retry the original
  rename, all without leaving the people view. 5 pytest cases in
  [tests/test_c4_self_name.py](tests/test_c4_self_name.py).

  Limitation: if the user later changes their display name in
  account settings, the existing Person row keeps the old name. A
  follow-up could add `Person.is_self` + propagation; today the
  user can just rename the person manually.
- ⏳ **C4.3 Email re-verification on change** — backend hook is there;
  needs the FE staged-email banner ("Click the link we sent to <new>;
  until then, your account email is still <old>").
- ⏳ **C4.5 Storage retention controls** — surface the per-category
  breakdown (already on `/storage/usage`) and add a slider for
  original-retention (default 30 days).
- ⏳ **C4.6 Cloud provider connect buttons** — blocked on **C2**.

### C5. Onboarding & B2B migration
- ✅ **C5.1 Easy setup script** — shipped 2026-05-18.
  [scripts/setup.py](scripts/setup.py) is stdlib-only (runs before
  the venv exists). Detects Windows / Linux / macOS, enumerates
  drives, probes for CUDA (nvidia-smi) / ROCm (rocm-smi) / Apple
  Metal / Intel XPU (xpu-smi or clinfo) with the matching torch
  wheel index hint. Generates 4 fresh secrets — JWT_SECRET via
  `secrets.token_urlsafe(48)`, POSTGRES_PASSWORD + MINIO_SECRET_KEY
  via `token_urlsafe(24)`, CLOUD_ENCRYPTION_KEY as a Fernet-shaped
  `base64.urlsafe_b64encode(secrets.token_bytes(32))` key.
  Writes a 56-key `.env` covering everything the backend reads
  (DB / Redis / MinIO buckets + SSE / Fernet / Google OAuth /
  GitHub OAuth / Stripe / Resend / SMTP / rate-limit knobs).
  `--mode` picks `docker compose up -d` or a per-platform native
  install checklist (apt / dnf / pacman / brew / Windows installer
  links) plus a binary-on-PATH check for Postgres / Redis / MinIO.
  Tk / webbrowser wizard UI still deferred per the original spec
  ("Wizard UI later").
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
> C8.1 visual overhaul + C8.2 model/worker visibility (heartbeats,
> model_runs, VRAM-per-user estimator) shipped — see git log.
> C8.3 Quick-action runners + C8.4 Queue tab + C8.5 Developer tab
> all shipped 2026-05-17 (see §C8.5 / §C8.6 below).
- ✅ **C8.3 Quick-action runners** — Account → AI features →
  Library maintenance now exposes Reprocess summaries, Re-scan all
  faces, Generate PDF thumbnails, and Reclassify images (vision
  backfill for Drive-synced photos that skipped vision at upload).
  All four are user-scoped + rate-limited (see §C8.6).
- ⏳ **C8.2+ Training-run telemetry** (step / loss curve / ETA) — lands
  alongside D6 fine-tuning. Extends `model_runs` with `started_at` /
  `finished_at` / `val_loss` / `artifact_key`.

### C8.5. Per-user fair queue + Queue tab ✅ SHIPPED 2026-05-17
> The job queue was a single Redis FIFO — a user kicking off a 500-job
> "reclassify entire library" pass starved every other user on the
> shared ML thread for the rest of the run. Rewrote
> [backend/jobs.py](backend/jobs.py) as per-user lists with
> round-robin worker pull (`fair_dequeue`). Per-user in-flight cap of
> 1 means the user who just got served goes to the back of the line;
> per-user queue cap of 1000 prevents a script from filling Redis.
>
> Admin Queue tab (in `admin.jsx`) shows per-user pending depth +
> in-flight counters + rate-limit headroom for every gated endpoint,
> plus a Drain button per row that calls
> `POST /admin/queue/drain-user`. Polls every 3 s. The scheduler
> config (caps + windows) is exposed at the top so operators can
> verify what's actually being enforced.

### C8.6. Heavy-endpoint rate limits ✅ SHIPPED 2026-05-17
> Every endpoint that triggers ML work now has a per-user hourly
> limit via the existing `enforce_rate_limit` helper. Keyed on
> `ml:<endpoint>:<user_id>` so the existing
> [/admin/queue](backend/api/admin.py) endpoint can read the
> headroom for the dashboard.
>
> | Endpoint | Limit | Why |
> |---|---|---|
> | `POST /images/backfill-summaries` | 3 / hour | Each call queues up to 500 jobs |
> | `POST /images/backfill-vision` | 3 / hour | Inline pipeline, minutes of GPU |
> | `POST /images/{id}/resummarize` | 30 / hour | Per-item retries should feel free |
> | `POST /images/{id}/redetect-faces` | 30 / hour | Cascade detector is heavy |
> | `POST /people/detect-and-label` | 10 / hour | × 50 images per call = 500 runs/hr cap |
> | `POST /images/best-of` | 30 / hour | OpenCV + CLIP encode on up to 30 photos |

### C8.7. Admin Developer tab ✅ SHIPPED 2026-05-17
> New "Developer" tab in the admin overlay. Drops the Stack inventory
> cards; centers on a live Capacity estimate calculator:
> users / photos-per-user / avg-MB-per-photo / uploads-per-user-per-day
> → storage TB, RAM GB, VRAM GB, CPU vCPU (+ physical cores), ML
> worker count, predicted speeds (search, upload, CLIP embed,
> Florence caption + per-hour throughput).
>
> Constants in `RealDeveloperTab.CAP` are measured from the running
> docker-compose stack (`docker stats` for RSS, `nvidia-smi` for
> VRAM) — provenance comments on every line. RAM scales with worker
> count since each worker container holds its own CPU-RAM copy of
> the model weights.
>
> Plus a 9-row Performance benchmarks reference table (dev vs.
> projected production numbers) and the full API surface in a
> compact monospace code-block panel grouped by section.

### C9. Multi-axis image filtering ✅ SHIPPED 2026-05-18
> Backend [backend/api/images.py](backend/api/images.py) `list_images`
> accepts the full filter surface — `scene`, `content_type`,
> `indoor_outdoor`, `tag`, `person_id`, `person`, `has_faces`,
> `has_gps`, `min_face_likelihood`, plus the two new
> `near=lat,lng,radius_km` (Haversine bounding-box on `image_geo`,
> 403 without `gps_retention` consent) and `taken_between=ISO,ISO`
> (range against `COALESCE(image_geo.taken_at, uploaded_at)`, either
> side can be empty). All AND-combine.
>
> Facets endpoint extended with `persons` (top named persons by
> count, empty list when `face_recognition` consent is off),
> `tags` (top tags with id + label + color + count), `starred_count`,
> and `date_range: {earliest, latest}` so the FE can size the date
> picker without a default-1970 placeholder.
>
> Frontend FiltersDropdown ([frontend/neuthek/src/app.jsx](frontend/neuthek/src/app.jsx))
> reuses the existing Cmd-K-style search panel — adds People and
> Tags chip groups (Tags carry their assigned color), plus a
> Date-range row above the chips with two `<input type="date">`
> bounded by the `facets.date_range` min/max so users can't pick
> nonsense ranges. Filter state lives at the App level and writes
> back to the URL via `history.replaceState` so reload + back/forward
> + link-sharing preserves the active chips. React-Query cache key
> includes every axis so toggling shows a separate cached page
> instead of mutating the current one in place.
>
> 10 pytest cases in [tests/test_c9_filters.py](tests/test_c9_filters.py)
> cover near consent gate, near bad-format, near bounding box,
> taken_between fallback to uploaded_at, taken_between preferring
> image_geo.taken_at when present, and the facets payload (persons
> empty without consent, tags surface user labels, starred_count +
> date_range present).
>
> "Out of scope" deferrals from the original spec still hold: a
> map-based radius picker UI (the `near=` URL param works today for
> power users + future map integration) and auto-generated facets
> ("show 12 most common scenes") — both wait on D1 tuning first.

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

### D7. Best-of-set image picker ✅ SHIPPED 2026-05-17
> `POST /images/best-of` body `{image_ids: [2..30], mode, use_case?}`
> returns each image ranked with a per-criterion breakdown
> (sharpness, exposure, face quality, optional use-case match).
> Three modes:
>
>   - **overall** — single ranked list; top is the keeper.
>   - **burst**   — greedy single-linkage cluster on CLIP embeddings
>                   (cosine > 0.85 == same burst), returns best per
>                   cluster.
>   - **use_case** — composite × CLIP cosine to a use-case prompt.
>                    Takes either one of 25 presets (People / Scenes
>                    / Content / Style) or arbitrary free text; the
>                    backend wraps free text in
>                    "a sharp well-composed photograph of <text>"
>                    before encoding.
>
> Scoring lives in [backend/best_of.py](backend/best_of.py).
> Signals:
>   - Sharpness via OpenCV Laplacian variance on the served bytes,
>     normalized min-max across the batch so the sharpest in the
>     selection always sits at 100.
>   - Exposure via mean grayscale luminance distance from mid-gray.
>   - Face quality via max FaceDetection.detection_confidence from
>     existing rows (no extra model run).
>   - Use-case cosine via the shared CLIP text encoder + per-image
>     `clip_embedding`, stretched [0.15, 0.35] → [0, 100].
>
> FE: [bestof.jsx](frontend/neuthek/src/bestof.jsx) — opened from
> BulkActionBar with the actual selection, image rendering via
> `AuthedThumb` / `useAuthedBlobUrl` (the served-variant endpoint
> requires a Bearer token so CSS `background-image` 401s without
> the blob wrapper). Mode + use-case chips re-trigger the scoring
> pass. "Keep this one" calls `bulkDelete(loserIds)` → losers go
> to Trash with confirmation. Rate-limited to 30/hr/user.
>
> User override + per-override telemetry for §D6 fine-tuning is
> still owed — for now the modal lets you click any other frame
> but doesn't log the override anywhere.

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
> Detection + dispatch + heartbeat-driven accelerator probe shipped —
> see git log. Still open:
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

### G2. Comments ✅ SHIPPED 2026-05-18
> Migration 0034 adds the `comments` table — `image_id` (CASCADE on
> image delete), nullable `user_id` (SET NULL on account delete so
> the comment outlives its author and we can still render replies
> under "former user"), self-FK `parent_id` (CASCADE for reply
> trees), `body`, schema-less `anchor_json` JSONB (PDF page+rect,
> slide index, video time-range, image-normalized xy — new asset
> shapes don't need a migration), `created_at` / `updated_at` /
> `deleted_at` (soft delete preserves reply tree integrity).
>
> RLS is intentionally lighter than other per-user tables. Comments
> have three legitimate readers (commenter, image owner, active
> share recipient), and the recipient check isn't expressible in a
> single RLS predicate without joining `share_grants`. The DB
> policy here only blocks the cheapest accident (writes that
> aren't the commenter's own or on their own images); the API
> layer enforces the full owner-OR-active-share check.
>
> Endpoints in [backend/api/comments.py](backend/api/comments.py):
>
>   GET    /images/{image_id}/comments         — list (threaded)
>   POST   /images/{image_id}/comments         — create
>   PATCH  /comments/{comment_id}              — edit (author only)
>   DELETE /comments/{comment_id}              — soft delete
>                                                (author OR image owner)
>
> Author can edit and delete their own. Image owner can delete any
> comment on their files (mod-on-own-stuff) but cannot edit a
> recipient's comment — that would let an owner put words in a
> sharer's mouth.
>
> 7 pytest cases in [tests/test_g2_comments.py](tests/test_g2_comments.py)
> cover owner happy-path, edit, soft-delete-then-list (body blanks,
> row stays), reply parent-image validation, stranger 404
> enumeration resistance, share-recipient read+write, and owner-
> deletes-recipient-comments (but cannot edit).
>
> Frontend [comment-panel.jsx](frontend/neuthek/src/comment-panel.jsx)
> mounted alongside every full-display surface (image lightbox /
> PDF modal / code modal) via the `.lightbox--comments` class on
> the wrapper. 360 px wide left-edge panel, slides in from the
> left, closes implicitly when the parent surface closes. Header
> with comment count, threaded list with avatar + byline + relative
> time + body + Reply/Edit/Delete actions, compose textarea pinned
> bottom with ⌘↵-to-send. Below 720 px the panel goes full-width
> at the bottom of the viewport (mobile / tablet portrait). The
> conversation lives on the server — re-opening the file resumes
> the thread.

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

### I.bis.1 Local checkout and code refs ✅ (landed)
- ✅ `docker-compose*.yml`, `Dockerfile` references, env-var defaults
  (`POSTGRES_USER/DB`, `MINIO_ACCESS_KEY/SECRET_KEY`, all four
  `MINIO_BUCKET_*` names).
- ✅ `backend/config.py` defaults flipped to `neuthek` / `neuthek-*`.
- ✅ `backend/security.py` validator rejects BOTH `neuthek` and
  legacy `istore` as known-weak credentials.
- ✅ Caddyfile placeholders + prod compose env keys renamed
  `ISTORE_*` → `NEUTHEK_*` with `${NEUTHEK_X:-${ISTORE_X}}` fallback
  so legacy operator `.env` files still resolve.
- ✅ `tests/conftest.py` test DB renamed `neuthek_phase4_test`;
  Postgres credentials read from env vars with `neuthek` default.
- ✅ `.env.example`, `README.md`, `SETUP.md`, `scripts/setup.py`,
  `marketing/DEPLOY.md` updated.
- 🟡 Still pending (out of this PR): `pyproject.toml` `name` is
  already `neuthek` from a prior pass; `frontend/package.json`
  remains `neuthek-frontend` (already correct); `alembic.ini`
  migration tag — currently no project-specific tag set, fine.

### I.bis.1.future — data migration ⏳
- Storage bucket names — **do not rename live buckets**. Add new
  `neuthek-{originals,served,faces,…}` buckets in MinIO and run a
  one-time mirror; flip the `.env` `MINIO_BUCKET_*` values when
  ready, retire the old names after a backup window. Existing
  operators with `istore-*` data pin those values in `.env` for
  now.
- Database name — same approach: pg_dump, restore into `neuthek`,
  flip `DATABASE_URL`, keep the old DB read-only for 30 days.
  Existing operators with an `istore` Postgres role keep
  `POSTGRES_USER=istore POSTGRES_DB=istore` in `.env` for now.

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

## 10b. Billing

### J1. Stripe billing — follow-ups ⏳
> Free / Pro / Business tiers, Embedded Checkout, signature-verified
> webhooks, Customer Portal handoff all shipped — see git log + the
> §J1 plumbing in [backend/billing.py](backend/billing.py) /
> [backend/api/billing.py](backend/api/billing.py) /
> [frontend/neuthek/src/billing.jsx](frontend/neuthek/src/billing.jsx).
> Operator setup steps live in [.env.example](.env.example). Still
> owing:
- **Automatic Tax** — disabled in checkout until the operator wires
  up Stripe Tax in the dashboard.
- **VAT / GST collection** — out of scope until Stripe Tax is set up.
- **`past_due` grace UI** — status flips but tier doesn't yank;
  needs a dedicated banner + grace clock during Stripe dunning.
- **Promo codes / annual upgrade discounts** — Stripe supports
  natively, needs route exposure.
- **In-app plan-change flow** — today plan swap goes through Stripe's
  Customer Portal. Could land `subscription.modify(...)` direct if
  the friction proves real.

---

## 11. Recommended priority order

### Sprint C — compliance (✅ shipped 2026-05-16)

All compliance items now closed:

1. **A2** Encryption at rest + in transit — ✅ shipped (boot validator,
   SSE-KMS dual-key, age-encrypted backups, posture in `/admin/system`).
2. **A4** Access control + audit — ✅ shipped (RBAC roles, signed-URL
   TTL cap ≤ 5 min, append-only audit trigger, RLS extended in
   migration 0027, last-superuser guard).
3. **A5** Deletion that actually deletes — ✅ shipped
   (`backend/deletion.py` covers every checklist row;
   `tests/test_a5_full_deletion.py` is the integration test that
   uploads + deletes + asserts 0 rows / 0 objects).
4. **A6** Compliance scaffolding — ✅ shipped (PRIVACY.md +
   DATA_PROCESSING.md rewritten, age gate enforced both ends, consent
   log carries ts + IP + UA + scope + sig, cookie banner reflects
   `localStorage` reality).
5. **A7** Repo hygiene — ✅ shipped (tightened `.gitignore`, three CI
   security jobs, pre-commit gitleaks chain,
   `tests/test_a7_repo_hygiene.py` asserts the invariants in the
   dev loop, REPO_HYGIENE.md documents posture + `node_modules/`
   historical-debt cleanup procedure).

### Sprint D — sharing + onboarding (next, ~2 weeks)

1. ~~**G2** comments~~ — ✅ shipped 2026-05-18 (comments table +
   threaded API + left-side CommentPanel in every full-display
   surface).
2. ~~**C5.1** setup script~~ — ✅ shipped 2026-05-18
   (`scripts/setup.py` with platform / GPU detect, fresh-secret
   generation, docker-or-native picker).
3. **C5.2** B2B migration — bulk import + per-source scopes +
   dry-run + provider plugins.
4. ~~**C2** Drive cloud sync~~ — ✅ shipped (Drive + GitHub, hourly
   sweep, conflict banner, per-source AI opt-in).

### Sprint E — multi-axis filters + UX polish (~1 week)

5. ~~**C9** multi-axis image filtering~~ — ✅ shipped 2026-05-18
   (`near` + `taken_between` filters + extended facets payload + FE
   People/Tags/Date-range chips + URL persistence).
6. ~~**C3** map refinements~~ — ✅ shipped 2026-05-18 (supercluster
   swap + grid backdrop + click-cluster-to-zoom; reverse-geocode
   fill + auto-backfill trigger already in).
7. ~~**C4.2** "Me" → display-name binding~~ — ✅ shipped 2026-05-18
   (server-side substitution + FE inline prompt for missing display
   name).

### Sprint F — multi-data-type platform (months)

8. **E1** promote `images` → `assets(data_kind)`.
9. **E2** Contacts (vCard/CSV).
10. **E3** Passwords vault (E2E encryption, Argon2id).
11. **E4** Game saves (versioned blobs).
12. **E5** IoT data (time-series, partitioned).
13. **E6** cross-type search + tagging + retention.

### Sprint G — hardware + collab + tail (longest tail)

14. **F1** tail — ROCm wheels, OpenVINO inference path (NPU), AMD
    quant variants.
15. **F2** quantization (Florence-2 8/4-bit, Qwen 4-bit GGUF,
    CLIP / RetinaFace INT8).
16. **F3** Lite profile.
17. **G3** real-time team editing (y.js + WebSocket).
18. **D6** fine-tune from search (once C8.2 training telemetry ships).
19. ~~**D7** best-of-set picker~~ — ✅ shipped 2026-05-17.
20. **H1–H4** README rewrite + comment sweep + GitHub-ready .md
    polish + CI lint tightening (the §A7 secret-scanning piece
    already landed; H4 here refers to `ruff` / `mypy --strict` /
    `tsc --noEmit` gates).
21. **I.bis** project rename — admin churn, parallelizable.

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
1. ✅ Security audit (auth/authz/JWT/upload/storage/secrets/rate-limit/
   deps) — A4 covers auth/authz/rate-limit/lockout; A1 covers upload
   validation + quarantine; A2 covers storage + secrets posture.
   See [AUDIT.md](AUDIT.md) and [SECURITY_REVIEW.md](SECURITY_REVIEW.md).
2. ✅ Privacy audit (every stored field — why, how long, can users
   delete it) — [PRIVACY.md](PRIVACY.md) §2 lists every field with
   the why; A5 + B4 prove deletion + retention.
3. ✅ Compliance checklist (Privacy + Terms + deletion + export +
   consent + cookies + biometric opt-in + age gate) — A6 closed:
   PRIVACY.md, [TERMS.md](TERMS.md), A5 deletion, B3 export, B2
   consent-before-signup, A6 cookie banner accuracy, B1/face consent
   gate, A6 age gate.
4. 🟡 Infra hardening (HTTPS, encrypted backups, private object
   storage, firewall, monitoring, malware scanning) — HTTPS via
   `docker-compose.tls.yml`, encrypted backups via §A2 age sidecar,
   private buckets via SSE. **Operator-specific bits remain:**
   firewall configuration, monitoring/alerting wiring, and
   anti-malware integration on the upload path beyond MIME + re-decode.
5. ✅ AI/ML review (no cross-user leakage, no hidden biometric
   processing, no accidental training on user data) —
   `test_cross_user_leak.py`, RLS on biometric tables (§A4 + 0027),
   PRIVACY.md §8 documents the "no shared global training" stance.
6. ✅ Deletion testing (every table + bucket + cache + backup
   eventually) — `tests/test_a5_full_deletion.py` asserts every
   row + bucket; SECURITY.md "Encrypted backups → Retention" covers
   the backup expiry path.
7. ⏳ Threat modeling (image leakage, metadata leakage, biometric
   misuse, bucket misconfig, search isolation failures, vault key
   extraction) — security review pass shipped (see AUDIT.md), but a
   formal STRIDE-style threat-model document is still owed.
8. ⏳ External review (another dev + a security person + a privacy
   person) — book before public launch.

### Required minimums before public release

All in. Cross-references for the auditor:

| Required | Where |
|---|---|
| Privacy Policy | [PRIVACY.md](PRIVACY.md) — 12 sections, A6-shipped |
| Terms of Service | [TERMS.md](TERMS.md) |
| License | [LICENSE](LICENSE) |
| Security policy | [SECURITY.md](SECURITY.md) — disclosure email + supported versions + prod checklist |
| Contact email | `security@neuthek.app` (SECURITY.md), `[Operator: privacy@…]` (PRIVACY.md) |
| Documented deletion process | PRIVACY.md §7 + `tests/test_a5_full_deletion.py` proof |
| Backup strategy | SECURITY.md "Encrypted backups" + retention path under GDPR Art. 17 |
| HTTPS | `docker-compose.tls.yml` + Caddyfile, boot-validated |
| Strong secrets | `validate_production_settings` rejects unsafe deployments at boot |
| Pre-signup consent popup flow | [consents.jsx](frontend/neuthek/src/consents.jsx) + B2 register-bundle backend path |

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
