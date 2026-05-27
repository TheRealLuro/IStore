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
  count / incompatible types), provider plug-ins (Drive ✅,
  OneDrive / Dropbox / Box / S3 / SMB still TODO), side-by-side
  migration phase. **Note:** Drive is in place via C2; the remaining
  providers each fan out from the same `cloud_files` schema.

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

### Sprint J — B2B migration + hosting + brand (~1 week)

> Unblocks customer acquisition. The hosting/brand pieces are
> mostly admin churn but each one is a blocker for a real launch.

16. **C5.2 B2B migration tooling** — bulk import + dry-run + at
    least one new provider plugin (OneDrive or Dropbox).
17. **I.bis.2 Hosting** — buy `neuthek.app`, set up email aliases,
    rename the GitHub repo, update OAuth client names + From
    addresses, swap the LICENSE copyright.
18. **I.bis.3 Brand surface** — favicon retina variants + OG image
    set + branded email templates.
19. **I.bis.1.future Data migration helper** — `pg_dump` + bucket
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

### Sprint N — Multi-data-type platform (months — biggest piece)

> Schedule when there's customer demand for one of the
> non-image types. E1 is the foundation; E2-E6 are independent.

32. **E1** promote `images` → `assets(data_kind)`.
33. **E2** Contacts (smallest, validates the E1 shape).
34. **E3** Passwords vault (E2E encryption — highest engineering
    bar after E1).
35. **E4** Game saves.
36. **E5** IoT data.
37. **E6** Cross-type search + tagging + retention.

### Sprint O — Hardware, quant, collab (longest tail)

> Each item is real but none unblocks a launch. Slot whenever
> hardware reach or self-host friction is the loudest complaint.

38. **F2 Model quantization** (biggest user-visible win for
    self-hosters).
39. **F1 ROCm / OpenVINO NPU**.
40. **F3 Lite profile**.
41. **C8.2 Training-run telemetry**.
42. **D6 Fine-tune from search** (blocked on C8.2).
43. **G3 Real-time team editing**.

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
