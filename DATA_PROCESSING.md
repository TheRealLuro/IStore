# Data Processing Addendum (DPA) — Template

> **This is a template, not legal advice.** Adapt with legal counsel
> before signing. Placeholders are marked `[…]`. The operator is
> responsible for finalizing it against the jurisdictions they serve
> (GDPR, UK GDPR, CCPA/CPRA, BIPA, etc.).

This DPA forms part of the agreement between **[Customer / Controller
legal name]** ("Customer") and **[Operator legal name]** ("Operator"
or "Processor") for use of the **neuthek** service.

---

## 1. Definitions

| Term | Meaning |
|---|---|
| **Controller** | The Customer (or its affiliates) that determines the purposes and means of Personal Data processing. |
| **Processor** | The Operator running neuthek for the Customer, processing Personal Data on the Controller's behalf. |
| **Personal Data** | Any information relating to an identified or identifiable natural person, as defined in GDPR Art. 4(1) and analogous laws. |
| **Special-Category Data** | Biometric data, health data, etc. — GDPR Art. 9 / CPRA "sensitive personal information." For neuthek this is **face crops and face embeddings**. |
| **Sub-processor** | Any third party engaged by Processor that processes Personal Data. |
| **Data Subject** | The natural person whose Personal Data is processed. |

---

## 2. Scope and roles

Processor processes Personal Data **on behalf of and per the
documented instructions of** Controller. The neuthek software is the
processing tool; Customer's choice of consent scopes, retention
windows, share durations, and admin configuration is the instruction
set.

### Categories of Personal Data processed

(Mirror of PRIVACY.md §2 — keep them in sync.)

- Account: email, Argon2-hashed password, display name, role.
- Account flags: `age_confirmed`, `is_verified`, `is_superuser`,
  `totp_enabled`.
- Subscription: Stripe customer ID, tier, dunning state (if Stripe is
  wired).
- Uploaded content: originals, served variants, thumbnails.
- Image metadata: EXIF (when `exif_retention` granted), GPS in
  `image_geo` (when `gps_retention` granted).
- AI-derived: summaries (`ai_summary`), CLIP embeddings
  (`semantic_search`).
- **Special-category**: face crops + face embeddings + person
  clusters (`face_recognition` only).
- Operational: bandit state, feedback events, tags, folders, shares.
- Compliance: audit log, consent records, recovery codes.

### Categories of Data Subjects

- Customer's authorized users and admins.
- People whose images Customer's users upload (data subjects depicted
  in photos / documents — special handling for faces, see §3).

### Duration

Processor processes the data for the term of the agreement plus the
time required to satisfy data-subject erasure requests
(see §6) and the operator's backup retention window
(see §7).

---

## 3. Processing purposes

Processor processes Personal Data **only** for these purposes:

1. **Storage** — store the bytes Customer's users upload.
2. **Service operation** — preview, search, share, organize, and
   manage the stored files per user actions.
3. **Optional AI features** — content summarization and semantic
   search, **only when the corresponding consent scope is granted**.
4. **Optional face recognition** — face detection and clustering,
   **only when `face_recognition` consent is granted** with the
   BIPA-grade signature flow.
5. **Security** — rate-limiting, brute-force protection, audit
   logging.
6. **Billing** — if the operator has wired Stripe, Processor passes
   the customer-level identifier to Stripe and stores Stripe's
   reference ID locally.
7. **Backup and disaster recovery** — encrypted dumps per
   SECURITY.md.

Processor will **not**:

- Sell, rent, or otherwise commercialize Personal Data.
- Train shared global models on Personal Data.
- Share Personal Data with third parties for advertising.
- Use Personal Data for purposes outside this list.

---

## 4. Security measures

Processor implements appropriate technical and organizational measures
("TOMs") per GDPR Art. 32, including but not limited to:

| TOM | Where it lives |
|---|---|
| TLS in transit | `docker-compose.tls.yml` + Caddyfile + HSTS |
| Object-storage encryption at rest | MinIO SSE-S3 / SSE-KMS, separate keys for biometric vs. content scopes |
| Database encryption at rest | Operator attests via `POSTGRES_AT_REST_ENCRYPTION=host_volume_confirmed` |
| Encrypted nightly backups | `age`-encrypted recipient-mode dumps, off-host private key |
| Password hashing | Argon2id (m=65536, t=3, p=4) |
| Bearer-only auth (no cookies) | JWT in `Authorization` header; CI-asserted |
| Rate-limit + lockout on auth | 5/min/IP + exponential backoff to 15 min |
| Brute-force lockout on TOTP, recovery codes, share-claim | Same middleware |
| Postgres Row-Level Security | FORCE'd on every biometric, consent, recovery, share, GPS, feedback, and bandit table |
| Append-only audit log | DB-level trigger blocks DELETE; UPDATE only allowed for the user-anonymization transition |
| Signed download URLs ≤ 5 min | HMAC-signed; verifier rejects any URL whose `expires` is > cap from now |
| RBAC | `users.role IN ('user','admin','superuser')` with `current_admin_user` / `current_superuser` deps |
| Consent log with IP + UA | Captured before action takes effect |
| File-validation pipeline | Magic-byte + re-decode + size cap + EXIF strip by default |
| Secret manager (prod) | `SECRET_MANAGER=docker_secrets` or platform-native |
| Boot-time prod-config validator | `validate_production_settings` refuses unsafe deployments |

These TOMs are reviewed at least annually; material changes are
communicated to Controller per §9.

---

## 5. Sub-processors

Processor will not engage Sub-processors without Controller's
authorization. Current Sub-processors and their roles:

| Sub-processor | Role | Region |
|---|---|---|
| **[Cloud host name]** | Compute, storage, network | **[Region]** |
| **[Backup destination]** | Encrypted backup target | **[Region]** |
| **[Email provider]** | Transactional email | **[Region]** |
| **[Stripe Payments, Inc.]** | Payment processing (if wired) | US (with EU data-processing addenda) |

Replacing a Sub-processor or adding a new one: Processor notifies
Controller at least **30 days** in advance. Controller may object;
unresolved objections are grounds for termination.

---

## 6. Data Subject rights

Processor assists Controller in responding to data-subject requests
(access, rectification, erasure, portability, restriction,
objection) via:

- **Access + portability**: `/account/export` returns a ZIP with
  every row about the user plus the relevant bytes.
- **Rectification**: in-app profile editor + email re-verification.
- **Erasure**: `/account/delete` (immediate) or
  `/account/schedule-delete` (30-day grace). Backed by the §A5
  integration test that asserts every table + bucket is empty
  after a delete.
- **Restriction / objection**: revoking any consent scope halts the
  associated processing and (for `face_recognition`) wipes the
  biometric data synchronously.

Processor must surface a data-subject request received directly to
Controller **within 5 business days** if Controller is the
appropriate responder under applicable law.

---

## 7. Retention and deletion

Default retention windows (operator can shorten in `.env`):

| Surface | Default | Setting |
|---|---|---|
| Original blobs (hybrid retention) | 30 days, then served-only | `Image.original_expires_at` |
| Quarantine bucket | 30 days | `upload_quarantine_retention_days` |
| Feedback events | 90 days post-trainer-consumption | `feedback_retention_days` |
| Audit log user_id link | 365 days, then NULL | `audit_log_retention_days` |
| Scheduled account delete grace | 30 days | `account_delete_grace_days` |
| Encrypted backups | Operator-chosen (recommended **30 days** rolling) | Operator runbook |

On termination of the agreement, Processor returns or deletes
Controller's Personal Data within **30 days** at Controller's choice,
except as retention is required by applicable law (audit-log rows,
billing records).

---

## 8. Breach notification

If Processor becomes aware of a Personal Data breach affecting
Controller's data, Processor notifies Controller **without undue
delay and in any case within 72 hours** of becoming aware. The
notification includes:

- The nature of the breach, categories of data involved, approximate
  number of data subjects + records affected.
- Likely consequences.
- Measures taken (or proposed) to address it.

Security disclosure channel: **security@neuthek.app** — see
[SECURITY.md](SECURITY.md).

---

## 9. International data transfers

Where Processor (or any Sub-processor) is outside the EEA / UK / a
country with an adequacy decision, transfers rely on Standard
Contractual Clauses (Module 3: Processor-to-Processor) and any
required supplementary measures.

---

## 10. Audits

Controller may, at its expense and at most **once per twelve months**
(more often if law or a credible breach justifies), audit
Processor's compliance with this DPA. Audits proceed against the
TOMs in §4 and the deletion guarantees in §7. Processor will respond
to reasonable audit questions within 30 days.

---

## 11. Liability and indemnification

Liability and indemnification are governed by the underlying service
agreement between Controller and Processor.

---

## 12. Effective date and term

This DPA is effective from **[Effective date]** and remains in force
for the duration of the underlying service agreement.

---

**Signatures**

For Customer (Controller):
- Name: __________________________
- Title: __________________________
- Date: __________________________

For Operator (Processor):
- Name: __________________________
- Title: __________________________
- Date: __________________________
