# neuthek — Roadmap

This file tracks **what's still open** — broken, partial, or planned.
Shipped work isn't listed here in detail; weekly recaps live on
[neuthek.com/updates](https://neuthek.com/updates) and the full
history is in `git log`.

**Last updated:** 2026-05-26 — security audit complete (PR #5 → #41
landed all critical + high findings), W21/W22 newsletters published,
docker stack refreshed to non-root multi-stage images, last user-
feedback batch (email-verify UI, filter `near`, video summary
quality) merged.

---

## What's already in main

Don't re-do these. Cross-references for the auditor:

| Workstream | Status | Where to look |
|---|---|---|
| §A2 Encryption at rest + in transit | ✅ | SECURITY.md, `tests/test_encryption_posture.py` |
| §A3 Secret management — `.env` hygiene + boot validator | ✅ | `backend/security.py::validate_production_settings` |
| §A4 Access control + audit | ✅ | RBAC + RLS migration 0027, signed-URL ≤5min, append-only audit |
| §A5 Deletion that actually deletes | ✅ | `backend/deletion.py`, `tests/test_a5_full_deletion.py` |
| §A6 Compliance scaffolding | ✅ | PRIVACY.md, TERMS.md, age gate, consent log |
| §A7 Repo hygiene | ✅ | `.gitleaks.toml`, pre-commit, `tests/test_a7_repo_hygiene.py` |
| §B1 EXIF / GPS handling | ✅ | gps_retention consent + scoped EXIF strip |
| §B2 Consent before signup | ✅ | `consents.jsx` + register-bundle |
| §B3 Export + portability | ✅ | `/account/export` endpoint |
| §B4 Retention sweepers | ✅ | `backend/retention.py` + PII scrubber |
| §C1 Folders, files, naming, organization | ✅ | C1.6 tag unification on migration 0028 |
| §C2 Drive cloud sync | ✅ | `backend/cloud_sync.py` + Redis lock + retry + revoke on delete |
| §C3 Map refinements | ✅ | supercluster + grid backdrop + reverse-geocode unstick |
| §C4.2 "Me" → real-name binding | ✅ | `resolve_self_name`, inline rename prompt |
| §C5.1 One-command self-host setup | ✅ | `scripts/setup.py` (stdlib-only, GPU-detecting) |
| §C8.3 / C8.5 / C8.6 / C8.7 Admin dashboard suite | ✅ | Queue tab, fair scheduler, rate limits, Developer tab |
| §C9 Multi-axis image filtering | ✅ | scene + content + tag + person + date + near + has-X |
| §D7 Best-of-set image picker | ✅ | `backend/best_of.py`, three modes, CLIP cosine |
| §G2 Comments | ✅ | migration 0034, `comments` table, threaded API + side panel |
| **Security audit** (152 findings) | ✅ | 25 PRs merged: CR-2…CR-10, A5/A8, U2/U3/U5/U6, D1/D2/D5+F12, CR-3 HKDF, CR-4 cloud quota, CR-5 SSO takeover, CR-6 ffmpeg, CR-7 non-root containers, CodeQL ReDoS+open-redirect (backend wave 1+2), marketing rate-limit swap, frontend client-redirect+http-iframe |
| **Marketing site** (homepage, FAQ, roadmap, compare, /updates, waitlist with email verify, newsletter broadcast) | ✅ | `marketing/` — Render-hosted |
| **JWT revocation via `token_version`** | ✅ | migration 0040, bumps on password reset + 2FA disable |
| **Project rename istore → neuthek** | ✅ | code refs, defaults, validator, live data migration |

---

## What's still open

### Account & onboarding

- ⏳ **C4.1 Display name on signup** — registration form gains a
  required "name" field; persisted as `users.display_name`; used in
  the topbar greeting and across the UI in place of email. (Currently
  the `Settings → Account` page accepts a display name but signup
  doesn't ask for one.)
- ⏳ **C4.3 Email re-verification on change** — backend hook is wired;
  needs the FE staged-email banner: "Click the link we sent to
  <new>; until then, your account email is still <old>."
- ⏳ **C4.5 Storage retention controls UI** — surface the per-category
  breakdown (already returned by `/storage/usage`) and add a slider
  for original-retention (default 30 days; existing backend supports
  per-user override).
- ⏳ **C4.6 Cloud provider connect buttons** — the Drive flow works
  end-to-end; the UI for "add another link" / "manage existing
  links" needs the polish pass that wasn't done in C2.
- ⏳ **C6 Account recovery** — needs all of:
  1. Pick a transactional email provider (Resend is already wired
     for the waitlist on the marketing side; extend it to the app).
  2. Forgot-password (fastapi-users routes exist; wire
     `UserManager.on_after_forgot_password` + the FE banner).
  3. Email verification on signup, gate sensitive endpoints behind
     `is_verified`.
  4. Recovery codes (scaffolded in migration 0011; need UI for
     generate / show-once / consume).
  5. TOTP 2FA UI (backend shipped; FE has the QR + verify modal but
     not the disable / regenerate-codes flow).
- ⏳ **C5.2 B2B migration tooling** — bulk import (drag-a-folder-tree
  via SMB/NAS path or desktop companion with resume support),
  per-source consent scopes, dry-run report (estimated bytes / file
  count / incompatible types), provider plug-ins, side-by-side
  migration phase. **Provider plug-in matrix** (each is an adapter
  over the existing `cloud_files` schema + the C2 OAuth /
  encrypted-refresh-token plumbing):

  | Provider | Auth | Priority | Notes |
  |---|---|---|---|
  | Google Drive | OAuth 2.0 | ✅ shipped | C2 |
  | OneDrive (consumer + Business) | OAuth 2.0 | High | Microsoft Graph API. Business tenants need delegated admin consent. |
  | Dropbox | OAuth 2.0 | High | Standard scope; v2 API is the cleanest of the bunch. |
  | iCloud Drive | n/a (no public API) | Hard | Apple has no first-party API for third-party access. Options: WebDAV via iCloud's Web client (fragile, scrapes), or require the user to export to a folder and use the §K2 SFTP server. Park as "available on macOS hosts only via local mount." |
  | Mega | SDK | Medium | Megacmd / Megalink SDK; account credentials over their custom protocol. |
  | Box | OAuth 2.0 | Medium | Enterprise focus; needs admin consent for delegated tokens. |
  | S3 / S3-compatible (R2, Backblaze, Wasabi) | AWS sigv4 | High | Most "give me an access-key + bucket" flows. Treat as a generic provider. |
  | SMB / NAS / mounted path | local FS or service account | High (B2B) | Desktop-companion path; reuses the upload pipeline. |
  | FTP / SFTP source | password / key | Low | Many small businesses still have one of these. |
  | Backblaze B2 (native API) | App key | Low | Their S3-compat surface covers this; native is faster for huge libraries. |
  | pCloud | OAuth 2.0 | Low | Niche, growing in the EU privacy crowd. |
  | MEGA NextCloud | OAuth 2.0 | Low | Self-host market overlap with neuthek; users may want to migrate. |

  Build in order of `Priority`: OneDrive + Dropbox + S3 + SMB first
  (covers ~90 % of "switch from your current drive" requests).
  iCloud is a known limitation; document the workaround in the
  Help page rather than promising what we can't deliver.

### Search & AI quality

- ⏳ **D1 Better image summaries** — Florence-2 detailed caption +
  scene-gated OCR + Qwen rewrite are wired. Still open:
  - Scene/object hint pass — evaluate RAM++ / Places365 / CogVLM2
    tagger / OpenCLIP-against-curated-4k-vocab. Feed labels into
    Qwen's prompt as structured hints so summaries read "auth-flow
    review on a whiteboard" instead of "person standing near a
    whiteboard."
  - Person-aware splice — substitute the user's display name (C4.2 is
    in) instead of "Me" / generic third-person.
  - Held-out eval set: "user search queries that should match this
    image" — drive prompt tuning with recall@5.
- ⏳ **D2 Better document summaries** — Qwen2.5-Instruct map-reduce
  over chunks is wired; pypdf + pdfminer.six fallback is in.
  Still open:
  - Per-chunk embeddings indexed alongside the doc so "where in this
    doc is X" surfaces a jump-to-section hit in semantic search.
  - OCR fallback for image-only PDFs (rasterize via PyMuPDF, route
    each page through Florence-2 `<OCR>`).
- ⏳ **D3 Hybrid search backfill + telemetry** — CLIP+FTS blend on
  `/search` is live (0.65 CLIP / 0.35 FTS). Still open:
  - Re-summarize backfill — `POST /images/backfill-summaries?force=
    true&limit=500` to replace old DistilBART output with Qwen.
  - Score telemetry — log (query, top-10 ids, blend weights) per
    search to tune blend empirically. Consent-gated under
    `bandit_compression_telemetry`.
- ⏳ **D4 Semantic search through folders** — Blocked on D1+D2
  stabilizing. Extend semantic search to match folder *titles +
  aggregated child summaries* so "the trip to Mexico" hits the
  folder. Cache an aggregate embedding per folder, invalidate on
  child add/move/delete.
- ⏳ **D5 Command-style search bar** — small DSL parser:
  `/find <query>`, `/show people: <name>`, `/best photo of <subject>`,
  `/in <folder>`, `/type <pill>`, `/before <date>`, `/after <date>`.
  Falls back to natural-language when no prefix.
- ⏳ **D8 Person re-detection on user signal** — UI affordance: "Mark
  as containing a person." Backend re-runs RetinaFace at
  `det_thresh=0.15`, falls back to mediapipe face-mesh, then prompts
  for a manual box if still empty.
- ⏳ **Video summary Batch 2** — Batch 1 shipped (frame rate 1/5s →
  1/3s, caption-quality filter, summary_signals telemetry). Still
  owed:
  - Scene-cut detection (histogram diff before sampling) so frames
    bias toward information-dense moments.
  - Pre-Qwen caption dedup so "a man in a suit | a man in a dark
    suit" collapses to one before rollup.
  - Audio-presence signal so "silent video" rows can be audited
    separately from "transcript good but captions thin" rows.
- ⏳ **D6 Fine-tune from search behavior** — blocked on
  **C8.2 training telemetry** below.
- ⏳ **D9 Knowledge-graph organization (smart connect)** — surface
  *implicit* connections the user never typed. Today neuthek has
  the raw signals (CLIP embeddings, named persons, tags, scene /
  content_type, EXIF timestamps + GPS) but no overlay that turns
  them into "this photo of Sasha at the beach connects to the
  contract draft you wrote that week and to the Zoom transcript
  where she signed it." Two-layer build:
  1. **Edge extraction.** Per-asset features → edges in a graph
     table: `(asset_a, asset_b, kind, weight)` where `kind` ∈
     {same_person, same_place_radius, same_day, same_topic_clip,
     same_topic_text, shared_tag, captured_within_event,
     manual_link}. Computed asynchronously on a background pass +
     incrementally on each new upload via the existing
     `pending_summary` worker.
  2. **Smart-connect UI.** A graph view (force-directed; rendered
     with vis.js or sigma.js) that lets the user start from a
     selected asset and explore connected nodes by edge kind.
     Filters: "only same_person", "only same_topic", etc.
     Manual `kind=manual_link` lets the user link nodes by hand
     for relationships the auto layer can't infer. Cached
     aggregates per (folder, person, tag) so the graph paints
     fast on libraries with 10k+ assets.

  Inspired by Neo4j-style relationship modelling but **without**
  the operational overhead of a separate graph DB — Postgres +
  pgvector handles every signal we need today. Add a real graph
  DB only if (a) the read patterns prove genuinely cross-cutting
  enough that recursive CTEs become a bottleneck and (b) we have
  >100k assets per user.
- ⏳ **D10 In-image text scanner + translator** — Florence-2 already
  has `<OCR>` task (we use it for image-only PDFs); we have
  `pillow`-based crop. Combine into a "viewer-side magnifying
  glass" that:
  1. **Detects text in the current asset** (image / PDF page /
     video frame at current playhead). Pulls existing Florence
     `<OCR_WITH_REGION>` output where present; falls back to a
     live OCR pass when the cache is empty.
  2. **Highlights text bounding boxes** as an overlay on the
     big-view image / PDF / video frame so the user can hover
     each detected region.
  3. **Translates on demand** — click a region → modal with
     source-language detection + target-language selector +
     translated text. Translator can be local (NLLB-200 distilled,
     ships in a Lite-mode-compatible quantization) or a paid
     provider (DeepL / Google Translate) gated behind operator
     env var.
  4. **UI placement** — a floating bubble on the lightbox edge
     (mirrors the existing comment-panel collapsible — same
     interaction language). Tap to open the scanner panel; the
     panel shows detected text + translation + a "copy" button.
     For video, the panel scrubs alongside playback so each
     frame's text is the current focus.

  Adjacencies: the existing `summary_signals` JSONB column can
  cache the detected-text regions per asset so reopening the
  viewer doesn't re-run the OCR pass. Translation cache is a
  separate small table keyed by `(text_hash, target_lang)` so
  repeat-translations of "Exit" don't re-bill DeepL.

### Admin / observability

- ⏳ **C8.2+ Training-run telemetry** — extends `model_runs` with
  `started_at` / `finished_at` / `val_loss` / `artifact_key`. Lands
  alongside D6 fine-tuning.

### Multi-data-type platform (months)

- ⏳ **E1 Data-type taxonomy + schema** — promote `images` to one row
  in a wider `assets` table keyed by `data_kind` (image, video,
  document, contact, password, save, iot_event). Type-specific
  tables hang off `assets.id`. New types are a sub-table + handler
  module without touching the core gallery flow.
- ⏳ **E2 Contacts** — import vCard / CSV; per-contact fields (name,
  emails, phones, notes, photo). Searchable + foldered + taggable.
  Photo doubles as a face source (with consent).
- ⏳ **E3 Passwords (vault)** — **end-to-end encryption hard
  requirement.** Server stores ciphertext only; key derives from
  the user's password via Argon2id, never leaves the client.
  Recovery via §C6 recovery codes. Schema:
  `vault_items(id, asset_id, ciphertext, nonce, kdf_params,
  schema_version)`.
- ⏳ **E4 Game saves** — opaque blobs with versioned history (last N
  retained). Per-game folder; optional desktop-launcher uploader.
- ⏳ **E5 IoT data** — time-series ingest endpoint per device-token;
  partitioned `iot_events` table; per-device timeline + chart;
  per-device retention cap.
- ⏳ **E6 Cross-type features** — search across types, per-type
  encryption envelopes (§A2), per-type retention sweeper (§B4).

### Native filesystem access (K — SSH / SFTP / WebDAV / FUSE)

> Make a user's neuthek library look like a folder in their host
> OS's file explorer + reachable from a terminal. Two consumption
> shapes:

- ⏳ **K1 SFTP server** — `paramiko`-based daemon embedded in the
  backend (or a sidecar container) that authenticates against the
  same `users` table + a per-user app-password the user generates
  in Settings → Sign-in & security. Filesystem layout matches the
  neuthek folder tree 1:1 — uploads via SFTP go through the same
  `store_upload` path (so they pick up EXIF strip, transcode,
  CLIP embed, all the rest). Read-only first cut; writes land in
  v2 once we've audited the metadata propagation. Listen-port +
  hostname documented for `sftp://user@neuthek.example.com:2222`.
- ⏳ **K2 WebDAV server** — same authentication shape; works in
  every desktop file manager (Windows "Add a network location",
  macOS "Connect to Server", GNOME / KDE / Cinnamon natively).
  More forgiving than SFTP about partial reads; supports the
  "preview thumbnail" flow without downloading the whole file.
  Uses [wsgidav](https://github.com/mar10/wsgidav) or
  [bottle-webdav](https://github.com/agile-geoscience/bottle-webdav)
  as the wire-protocol layer; the per-resource provider hooks
  into the existing image / folder Pydantic models.
- ⏳ **K3 SMB / CIFS share** — Windows Explorer's preferred
  protocol. Spin up Samba in a sidecar container with the same
  per-user app-password auth (translated via PAM module or a
  simple `smbpasswd`-shaped table populated by the API on
  password creation). Documented as `\\neuthek\library` mount.
- ⏳ **K4 FUSE filesystem (desktop companion)** — for users who
  want a real local-fs mount (offline-capable cache, lazy
  download on `open`). Cross-platform via
  [fuse-rs](https://crates.io/crates/fuser) or Python
  [fusepy](https://github.com/fusepy/fusepy). Read-through cache
  to a configurable local directory; writes upload back through
  the API. Linux + macOS first; Windows via WinFsp.
- ⏳ **K5 Command-line client** — small `neuthek` binary (Rust
  or Go for single-binary cross-platform deploys) that
  authenticates via stored token + supports `neuthek ls /Photos`,
  `neuthek cp ./IMG_1234.jpg /Photos/`, `neuthek search "snowy
  sunset"`, `neuthek share <id> --recipient alice@…`. Same
  per-user app-password auth as the protocol servers. Ships
  prebuilt binaries via GitHub Releases for Linux (x86_64 +
  arm64), macOS (universal), and Windows (msi installer).

  **Cross-cutting concerns for K:**
  - Per-user **app-passwords** (different from the account
    password) so the SFTP / WebDAV / SMB clients can authenticate
    without exposing the real password. Revocable per
    application. Backed by a new `user_app_passwords` table.
  - **Rate limits + audit** — every protocol-level READ or WRITE
    writes an audit row keyed by `(user_id, app_password_id,
    protocol, path, action)`. Counted against the same per-user
    throttles the HTTP API uses (so an SFTP backup job + a UI
    upload share the daily byte budget).
  - **Encryption-at-rest gate** — when §L1 ships, the protocol
    servers MUST refuse reads of E2E-encrypted assets and emit
    a clear error pointing the user to the web UI for decryption.

### End-to-end encryption (L — user-held keys, like Mega / Proton)

> Today neuthek encrypts at rest with operator-held keys
> (`backend.storage._sse` + Fernet-encrypted refresh tokens). For
> the privacy promise to be airtight against a server compromise,
> we need a path where the user's per-asset keys never leave the
> client. Modelled after Mega / Proton Drive — opt-in per user;
> coexists with the operator-key path for users who want the
> server-side AI features instead.

- ⏳ **L1 User-held master key** — generated client-side at signup
  (WebCrypto `crypto.subtle.generateKey({name: "AES-GCM",
  length: 256})`). Encrypted with an Argon2id-stretched key
  derived from the user's password; persisted server-side ONLY
  as ciphertext (`users.encrypted_master_key`,
  `users.master_key_kdf_params`). Recovery via §C6 recovery
  codes — each code is an alternate AES-key-wrap of the same
  master key so any one code unlocks the account.
- ⏳ **L2 Per-asset envelope keys** — upload pipeline becomes:
  client generates a fresh AES-GCM-256 data key, encrypts the
  file body locally, wraps the data key under the master key,
  POSTs `(ciphertext_blob, wrapped_data_key, nonce, kdf_version)`.
  Server only ever sees ciphertext + the wrapped key. Existing
  `images` rows extend with `encryption_envelope` JSONB so
  legacy unencrypted rows coexist while users migrate.
- ⏳ **L3 Streaming download with on-device decryption** — the
  served-blob endpoint returns the ciphertext + the wrapped data
  key; the client unwraps with the master key and decrypts in a
  `ReadableStream` so the user sees content without buffering
  the whole file. Range requests work but require the server
  to return the GCM auth tag at the end of the requested range
  (or to chunk the ciphertext so each chunk has its own tag —
  the standard "STREAM" construction).
- ⏳ **L4 AI features explicit opt-out** — server-side AI
  (Florence, CLIP, Qwen) cannot read E2E-encrypted assets.
  Account → AI features panel surfaces the trade clearly:
  "Enable E2E encryption" disables the AI pipeline for new
  uploads + grays out the search box for E2E assets ("no
  semantic search on encrypted files — server can't read
  them"). A Lite-mode-on-device CLIP/captioner is the
  follow-up that gives encrypted users a partial AI experience
  by running everything client-side; that's an L5 item.
- ⏳ **L5 Client-side AI (Lite mode for E2E)** — distill a
  quantized OpenCLIP + Florence-2-base + a small caption-style
  model into a WASM/WebGPU-running bundle (~150 MB total) that
  runs in a service worker. Embeddings are computed on the
  client and uploaded as ciphertext so semantic search still
  works on E2E libraries — the server stores per-asset
  embeddings encrypted under the master key, and the search
  query also gets embedded client-side. Cross-similarity in the
  ciphertext space requires order-preserving / homomorphic
  encryption that we *aren't* shipping; instead, search becomes
  "client downloads the per-asset wrapped embeddings + does the
  cosine compare locally." Practical for libraries up to ~10k
  assets per user; beyond that we need a smarter shape (Bloom-
  filter pre-filter or true HE, R&D scope).
- ⏳ **L6 Master-key rotation** — the user can rotate their
  master key from Settings. Backend re-wraps every
  `encryption_envelope` under the new master key (one bulk
  worker pass). Recovery codes regenerate. The old master key
  is securely shredded once the rewrap completes; until then
  the user can still log in with either.
- ⏳ **L7 Per-share E2E** — sharing a file with another user
  requires either (a) sending them the unwrapped data key
  (which they then store under their own master key), or
  (b) re-wrapping the data key under the recipient's public
  key. Use the recipient's master public key (derived from
  their master key via X25519) so the share doesn't require
  the sender to know the recipient's password. Same shape as
  Proton Drive's "share with another Proton account."

  **Cross-cutting concerns for L:**
  - **Recovery codes are the only password-reset path** — a
    forgotten password without a recovery code = unrecoverable
    library (Mega's stance). The signup UX must surface this
    clearly: "Save your recovery code somewhere offline. We
    cannot recover your data without it."
  - **AI feature opt-in** is now per-asset, not per-account.
    A user can keep some folders unencrypted (AI-eligible) and
    encrypt others. The folder-create modal grows an
    "Encryption" toggle.
  - **WebDAV / SFTP gates** (§K) must refuse E2E assets — the
    protocol servers run on the operator-side and can't
    decrypt. They return 423 Locked with a body pointing at
    the web UI.
  - **Operator transparency** — `validate_production_settings`
    surfaces in the boot log "E2E available for users:
    YES/NO" so an operator knows the posture without scraping
    settings.

### Hardware / quantization / Lite mode

- ⏳ **F1 Backend on all GPU vendors** — detection + dispatch +
  heartbeat-driven accelerator probe already shipped. Still open:
  - AMD ROCm (Linux) — torch + onnxruntime-rocm wheels + ROCm CI
    image.
  - OpenVINO inference path for Intel iGPU + NPU — convert
    Florence-2 / Qwen / CLIP to OpenVINO IR + route through
    `Core.compile_model(device='NPU')`. Detection-only today.
  - AMD/Intel quantization variants — covered by F2 below.
- ⏳ **F2 Model quantization**:
  - Florence-2-large → 8-bit GPTQ ≥8 GB GPU, 4-bit smaller; CPU →
    ONNX INT8.
  - Qwen2.5-1.5B → 4-bit GGUF for CPU/Apple, GPTQ for CUDA/ROCm.
  - CLIP / RetinaFace → ONNX INT8.
  - Quant level becomes a config option, not a code change.
- ⏳ **F3 Headless / Lite profile** — setup wizard offers a profile
  that disables Florence-2 + Qwen and falls back to BLIP captions
  + sumy summaries. For Raspberry-Pi-class hosts AND as a no-AI
  privacy stance for paranoid users.

### Collaboration

- ⏳ **G3 Real-time team editing** — document type only. Likely path:
  y.js + relay WebSocket, persisted snapshot per N seconds. Big
  separate workstream; schedule after §F lands so we know the
  hardware budget.

### Brand / hosting / docs hygiene

- ⏳ **I.bis.2 Hosting / external refs**:
  - GitHub repo rename + redirect.
  - Register `neuthek.app` (or chosen TLD), set up `privacy@` /
    `dpo@` / `security@` addresses; point legal docs at the new
    contacts.
  - `LICENSE` `Copyright (c) … IStore Authors` → `neuthek Authors`.
  - OAuth client app names with Google / Microsoft / Apple,
    transactional email "From" name.
- ⏳ **I.bis.3 Brand surface in the app**:
  - Sidebar logo (current `NeuthekMark` is in; OG image set and
    favicon retina variants still owed).
  - Email templates (verification, reset, recovery codes) — header
    brand, signature, From name.
- ⏳ **I.bis.1.future Live data migration for legacy operators** —
  Operators still on `istore-*` buckets / `istore` Postgres role
  need:
  - MinIO mirror to `neuthek-*` buckets, retire old after backup
    window.
  - `pg_dump` from `istore` → restore into `neuthek`, flip
    `DATABASE_URL`, keep old DB read-only 30 days.

### Docs hygiene

- ⏳ **H1 README rewrite** — current "frontend files exist as
  placeholders" assertion is stale. New shape: hero +
  screenshots, "what you can actually do", install (one-liner via
  C5.1), self-host notes, feature status, security posture,
  contributing, license.
- ⏳ **H2 Code-comment balance** — sweep `backend/` and `frontend/src/`
  for comments that just restate the next line, multi-paragraph
  docstrings on internal helpers, stale TODOs referencing shipped
  phases. Keep comments that explain *why*.
- ⏳ **H3 GitHub-ready .md files** — every top-level `.md` rendered
  on github.com should look intentional. Scope: README, ROADMAP
  (this file slimmed for public), CONTRIBUTING, SECURITY, PRIVACY,
  TERMS, LICENSE summary.
- ⏳ **H4 CI / lint tightening** — `ruff` + `mypy --strict` on
  backend; `tsc --noEmit` + `eslint` already run on FE — wire both
  into a GitHub Actions workflow that gates merges. `gitleaks` is
  already in (§A7).

### Billing

- ⏳ **J1 Stripe billing follow-ups** — Free / Pro / Business tiers,
  Embedded Checkout, signed webhooks, Customer Portal all shipped.
  Still owed:
  - **Automatic Tax** — disabled in checkout until operator wires
    Stripe Tax.
  - **VAT / GST collection** — blocked on Automatic Tax.
  - **`past_due` grace UI** — status flips but tier doesn't yank;
    needs a banner + grace clock during Stripe dunning.
  - **Promo codes / annual upgrade discounts** — Stripe supports
    natively; needs route exposure.
  - **In-app plan-change flow** — today plan swap is via Customer
    Portal; could land `subscription.modify(...)` direct if
    friction proves real.

### Security tail

- ⏳ **A3 Secret rotation worker** — currently a key change orphans
  existing ciphertext until a migration tool ships. Path: zero-
  downtime re-encrypt over `cloud_links.encrypted_refresh_token`
  and any other Fernet-encrypted column.
- ⏳ **A3 Secret-access audit** — every read of `CLOUD_ENCRYPTION_KEY`
  / `JWT_SECRET` / etc. emits an audit row so operator dashboards
  can catch unexpected accesses.
- ⏳ **Threat-model document** — security review pass shipped (see
  AUDIT.md), but a formal STRIDE-style threat-model doc is owed.
- ⏳ **External review** — book a dev + security person + privacy
  person before public launch.
- ⏳ **Infra hardening tail** — firewall config docs, monitoring +
  alerting wiring, anti-malware integration on the upload path
  beyond MIME + re-decode.

---

## Recommended order

Grouped into themed sprints. Each sprint is roughly one to two
weeks of focused work; sprints can run in parallel where they don't
share files. The order inside each sprint is the actual order to
do the items.

### Sprint H — Account essentials (~1 week, days each)

> Highest-leverage UX work. Most of it is wiring existing backend
> hooks into the FE. Each one is small individually but together
> unlocks "this app feels complete on signup."

1. **C4.1 Display name on signup** — small registration-form change;
   unblocks the "personalized greeting" + makes §C4.2 land for
   every new user without a follow-up prompt.
2. **C6 Account recovery** — pick Resend (already wired for the
   marketing waitlist), then ship in this order: forgot-password →
   email verification on signup → recovery codes UI →
   TOTP-disable/regenerate UI. Two-day pass each.
3. **C4.3 Email re-verification on change** — backend hook is wired;
   add the staged-email banner.
4. **C4.5 Storage retention controls UI** — wraps `/storage/usage`
   in a real settings panel.
5. **C4.6 Cloud provider connect buttons** — link-management UI.
6. **C7 Light theme refinement** — last polish pass (small, visual).

### Sprint I — Search & AI quality (~2 weeks)

> The user's biggest open-ended ask. Each item below feeds into the
> next; do them in this exact order.

7. **D1 Image summaries — RAM++/scene-hints pass** — evaluate
   candidates, pick one, plumb labels into Qwen's prompt as
   structured hints.
8. **D1 — person-aware splice** — substitute display name (C4.2
   already in; needs the C4.1 + C6 verification done so every user
   has one).
9. **D1 — held-out eval set** — recall@5 measurement against
   user-shaped queries. Drives the rest of the AI-quality tuning.
10. **D2 — per-chunk doc embeddings + OCR-only-PDF fallback**.
11. **D3 — re-summarize backfill** (one admin click) **+ search
    score telemetry** (consent-gated).
12. **Video summary Batch 2** — scene-cut detection + pre-Qwen
    caption dedup + audio-presence signal.
13. **D8 Person re-detection on user signal** — small UI affordance
    + cascade detector + manual-box fallback.
14. **D5 Command-style search bar** — small DSL parser; can ride
    alongside the rest.
15. **D4 Semantic folder search** — depends on D1+D2 stable.

### Sprint J — B2B migration + cloud-provider matrix (~2 weeks)

> Unblocks customer acquisition. Order chosen by priority column in
> the §C5.2 matrix. Each provider is ~1-2 days of work because the
> `cloud_files` schema + OAuth + retry/lock infra are reused.

16. **C5.2 dry-run report** — estimated bytes / file count /
    incompatible types preview before commit. UI-only on the
    existing cloud-sync surface.
17. **OneDrive provider** — Microsoft Graph API; OAuth 2.0 +
    delegated admin consent for Business tenants.
18. **Dropbox provider** — OAuth 2.0 + Dropbox API v2.
19. **S3 / S3-compatible provider** — access-key + bucket; covers
    R2 / Backblaze / Wasabi via a generic adapter.
20. **SMB / NAS provider** — service-account credentials + path;
    desktop-companion fallback for environments where the server
    can't see the share directly.
21. **Mega provider** — niche but the brand recognition is high.
22. **Box provider** — enterprise focus, ships with the rest of
    the OAuth 2.0 adapters.
23. **pCloud / Backblaze native** — slot when there's a customer
    asking.
24. **iCloud Drive helper** — document the macOS-mount workaround
    in the Help page rather than promising a native API.

### Sprint J.5 — Hosting + brand (~few days, can run alongside J)

25. **I.bis.2 Hosting** — buy `neuthek.app`, set up email aliases,
    rename the GitHub repo, update OAuth client names + From
    addresses, swap the LICENSE copyright.
26. **I.bis.3 Brand surface** — favicon retina variants + OG image
    set + branded email templates.
27. **I.bis.1.future Data migration helper** — `pg_dump` + bucket
    mirror script for operators on the legacy `istore-*` setup.

### Sprint K — Repo + CI hygiene (~few days, parallelizable)

20. **H1 README rewrite**.
21. **H2 Code-comment sweep**.
22. **H3 GitHub-ready .md polish**.
23. **H4 CI lint tightening** (`ruff` + `mypy --strict` + `tsc`
    gates).

### Sprint L — Billing tail (~few days)

24. **J1 `past_due` grace UI** + **promo codes** — most user-facing.
25. **J1 In-app plan-change** — `subscription.modify(...)` direct.
26. **J1 Automatic Tax** + **VAT/GST** — operator-gated; wait for a
    real revenue need.

### Sprint M — Pre-launch security tail (~1 week)

27. **STRIDE threat-model document** — formal write-up of the
    audit cycle's findings + residual risks.
28. **A3 Secret rotation worker** — zero-downtime re-encrypt.
29. **A3 Secret-access audit rows**.
30. **External review** — book the dev + security + privacy
    reviewers.
31. **Infra hardening tail** — firewall + monitoring + anti-malware
    runbook.

### Sprint N — Native filesystem access (~2 weeks)

> Make neuthek mountable. App-passwords + SFTP + WebDAV cover ~95%
> of "I want this to look like a folder on my computer" requests.
> CLI client is the developer-facing complement.

32. **K-shared app-password infrastructure** — `user_app_passwords`
    table + Settings UI to generate/revoke. Reused by every K
    item below. Add audit rows on first-use + on revoke.
33. **K1 SFTP server** — paramiko-based; read-only first cut.
    Routes through the same `store_upload` pipeline so uploads
    via SFTP still get EXIF strip / transcode / CLIP embed.
34. **K2 WebDAV server** — `wsgidav`-style provider over the
    existing folder + image models. Works in every desktop file
    manager natively.
35. **K3 SMB / CIFS share** — Samba sidecar container with
    per-user app-password auth. Windows Explorer's preferred
    protocol.
36. **K5 Command-line client** — small Go binary with `ls`, `cp`,
    `search`, `share` subcommands. Same app-password auth as
    the protocol servers. GitHub Releases for cross-platform
    binaries.
37. **K4 FUSE filesystem (desktop companion)** — Linux + macOS
    via fusepy; Windows via WinFsp. Read-through cache for
    offline work. Larger scope; schedule last.

### Sprint O — End-to-end encryption (~3 weeks — biggest piece)

> Opt-in per user. Co-exists with the operator-key path so users
> can choose AI features vs. zero-trust posture. Mega / Proton
> Drive pattern.

38. **L1 User-held master key** — generated client-side at
    signup; encrypted with Argon2id-stretched password; stored
    server-side as ciphertext only. Recovery codes are alternate
    AES-wraps of the same master key.
39. **L2 Per-asset envelope keys** — client encrypts every
    upload locally; wraps the data key under the master key;
    POSTs `(ciphertext, wrapped_data_key, nonce)`. Server never
    sees plaintext.
40. **L3 Streaming download with on-device decryption** —
    ReadableStream-style decrypt so large files work without
    buffering. Range requests via STREAM-construction chunked
    GCM.
41. **L4 AI feature gating** — Account → AI features panel
    surfaces the trade clearly. E2E assets exit the server-side
    Florence / CLIP / Qwen pipeline.
42. **L7 Per-share E2E re-wrap** — sharing an E2E asset
    re-wraps the data key under the recipient's master public
    key (X25519). Mirrors Proton Drive's share model.
43. **L6 Master-key rotation** — Settings → Rotate master key.
    One-pass background rewrap; old key shredded once complete.
44. **L5 Client-side AI (Lite mode for E2E)** — quantized
    OpenCLIP + Florence-2-base running in WebGPU / WASM so E2E
    users get semantic search. Heaviest engineering of the set;
    R&D-shape until the build budget is in. Slot last.

### Sprint P — Smart connect + in-image translator (~1 week)

> Two distinct features but they ride the same "viewer-side power
> tool" UX surface, so build them together so the lightbox layout
> only changes once.

45. **D10 In-image text scanner + translator** — Florence OCR
    region overlay + per-region translate panel. Floating bubble
    on the lightbox edge, mirroring the existing comment-panel
    UX language.
46. **D9 Knowledge-graph organization** — edge-extraction pass
    (same_person / same_place / same_day / same_topic /
    shared_tag) + force-directed graph view in the gallery.
    Inspired by Neo4j relationship modelling; built on top of
    Postgres + pgvector (no separate graph DB).

### Sprint Q — Multi-data-type platform (months — biggest piece)

> Schedule when there's customer demand for one of the
> non-image types. E1 is the foundation; E2-E6 are independent.

47. **E1** promote `images` → `assets(data_kind)`.
48. **E2** Contacts (smallest, validates the E1 shape).
49. **E3** Passwords vault — overlaps with §L (E2E); land §L
    first then E3 reuses the master-key + envelope shape.
50. **E4** Game saves.
51. **E5** IoT data.
52. **E6** Cross-type search + tagging + retention.

### Sprint R — Hardware, quant, collab (longest tail)

> Each item is real but none unblocks a launch. Slot whenever
> hardware reach or self-host friction is the loudest complaint.

53. **F2 Model quantization** (biggest user-visible win for
    self-hosters).
54. **F1 ROCm / OpenVINO NPU**.
55. **F3 Lite profile**.
56. **C8.2 Training-run telemetry**.
57. **D6 Fine-tune from search** (blocked on C8.2).
58. **G3 Real-time team editing**.

---

## Rules — non-negotiable

These come straight from the user dump on 2026-05-04. **Read before
designing anything that touches user data.**

### Privacy
1. **Informed consent.** Plain language. Especially for embeddings,
   faces, semantic search, biometrics. Collected via popup before
   signup, revoked from settings.
2. **Security.** HTTPS, encryption at rest + in transit, RBAC,
   secure storage, rate limiting, secret management, audit logging,
   deletion systems. Passwords E2E.
3. **Data minimization.** Only collect what's necessary.
4. **User control.** Export, delete, revoke consent, disable AI,
   remove biometric data.
5. **Honest disclosure.** No secret model training, no quiet
   expansion of data usage.
6. **Consent does not override everything.** Even with "I agree":
   unfair biometric practices, deceptive AI claims, unsafe retention,
   minor biometric data, discrimination profiling, inadequate
   security, hidden processing — all still illegal.
7. **Safest framing**: "Private AI-assisted personal & business
   storage for the account owner." Avoid: "Global people recognition
   and profiling."

### Biometrics (highest-risk surface)
- Opt-in only, written-consent-grade explicit.
- Local / on-device processing where possible.
- Separate biometric DB + separate keys.
- Immediate deletion on revoke.
- No profit motive on biometric data ever.

### Frameworks that apply
- **GDPR** — EU users: right-to-be-forgotten, portability, consent
  logging, processing records, breach notification.
- **CCPA / CPRA** — California users.
- **BIPA** (Illinois) — written informed consent, retention policy,
  deletion schedule, no profit, secure storage.
- **COPPA** — under-13 prohibited unless fully compliant.

### AI/ML
- Embeddings = sensitive data. Encrypt them. Delete with the source.
  Never expose raw vectors. Never allow cross-user similarity.
- Every vector query scoped to the authenticated owner.
- No cross-user index, no shared semantic store, no accidental
  leakage.
- No silent model training on user data. D6 fine-tune requires
  explicit opt-in + per-user adapter, never a shared global update.

### Repo
Never commit: real user images, embeddings, EXIF-rich samples, prod
credentials, DB dumps, biometric data, vault ciphertext, real
contacts, real IoT logs. Synthetic fixtures only.

---

## Pre-launch checklist

1. ✅ Security audit (auth/authz/JWT/upload/storage/secrets/
   rate-limit/deps) — see AUDIT.md + SECURITY_REVIEW.md + the 25
   PRs that landed end-to-end through the audit cycle.
2. ✅ Privacy audit — PRIVACY.md §2 + A5 + B4.
3. ✅ Compliance checklist — A6 closed: PRIVACY.md, TERMS.md, A5
   deletion, B3 export, B2 consent-before-signup, A6 cookie banner,
   B1/face consent gate, A6 age gate.
4. 🟡 Infra hardening — HTTPS ✅, encrypted backups ✅, private
   buckets ✅. **Operator-specific remaining:** firewall, monitoring
   alerting, malware scanning beyond MIME + re-decode.
5. ✅ AI/ML review — `test_cross_user_leak.py`, RLS on biometric
   tables, PRIVACY.md §8.
6. ✅ Deletion testing — `tests/test_a5_full_deletion.py`.
7. ⏳ **Threat-model document** (Sprint M item).
8. ⏳ **External review** (Sprint M item).

| Required | Where |
|---|---|
| Privacy Policy | [PRIVACY.md](PRIVACY.md) |
| Terms of Service | [TERMS.md](TERMS.md) |
| License | [LICENSE](LICENSE) |
| Security policy | [SECURITY.md](SECURITY.md) |
| Contact email | `security@neuthek.app` (SECURITY.md), `privacy@…` (PRIVACY.md) |
| Documented deletion process | PRIVACY.md §7 + `tests/test_a5_full_deletion.py` |
| Backup strategy | SECURITY.md "Encrypted backups" |
| HTTPS | `docker-compose.tls.yml` + Caddyfile, boot-validated |
| Strong secrets | `validate_production_settings` rejects unsafe deployments |
| Pre-signup consent | `consents.jsx` + B2 register-bundle |

---

## Quick reference

- Run backend: `.venv/Scripts/python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --log-level info`
- Run tests: `.venv/Scripts/python.exe -m pytest`
- Apply migrations: `.venv/Scripts/python.exe -m alembic upgrade head`
- Force-regenerate every summary: **Account → AI features → Library
  maintenance → Re-summarize entire library** (or
  `POST /images/backfill-summaries?force=true&limit=500`).
- After any change to `backend/`, **restart uvicorn** — it doesn't
  hot-reload Python module changes by default.
- Run frontend: `cd frontend && npm run dev` (Vite, port 5173).
- Weekly newsletter drafts queue in [updates.md](updates.md) — copy
  to `marketing/src/data/updates.ts` + mirror to
  `updates-index.json` + bump `sitemap.xml` at publish time.
