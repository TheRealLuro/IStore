# Privacy Notice

_Last updated: 2026-05-16. Effective date: see the deployment's site
footer. Operators are responsible for keeping the public-facing copy in
sync with this file._

neuthek is private AI-assisted personal & business storage for the
account owner. We don't sell user data, don't share it with third
parties for advertising, and don't train shared global models on it.
This document describes exactly what we collect, why, how long we
keep it, and how to make it go away.

If you operate a neuthek deployment, **fill in the placeholders
marked `[Operator: …]`** before publishing this file. The framework
is shared; the operator-specific contact info / hosting choices are
not.

## Contact

- General questions: **[Operator: contact@your-domain]**
- Privacy questions / data-subject requests:
  **[Operator: privacy@your-domain]**
- Security vulnerabilities: **security@neuthek.app** (see
  [SECURITY.md](SECURITY.md) — different from privacy questions).

## 1. Who we are

The operator running neuthek for your account is the **data
controller** if you're a self-hoster; if you're a customer of a hosted
deployment, the hosting organization is the controller and we are the
processor. See [DATA_PROCESSING.md](DATA_PROCESSING.md) for the B2B
processor relationship and a DPA template.

## 2. What we collect

We collect the categories below. **None of this leaves your account**
except where the user has explicitly initiated a share — and even
shares are pinned to a specific recipient email.

| Category | What | Why |
|---|---|---|
| **Account** | Email, password (Argon2-hashed), display name, role, locale | Sign-in; account-level features (sharing, billing) |
| **Account flags** | `age_confirmed`, `is_verified`, `is_superuser`, `totp_enabled` | Age gate (§5), email verification, RBAC, 2FA state |
| **Subscription** | Stripe customer ID, current tier, dunning state | Billing — only when the operator has wired Stripe |
| **Uploads (originals)** | The files you upload — photos, video, documents, the bytes you sent us | Storage |
| **Uploads (served)** | Compressed / re-encoded versions for fast preview | Bandwidth efficiency |
| **Thumbnails** | 256–512 px previews | Gallery rendering |
| **EXIF (image-level)** | Camera make/model, capture timestamp, **stripped from the originals bucket by default** unless `exif_retention` is granted | EXIF is informative; default-strip is the safe posture per §B1 |
| **GPS** (sibling table `image_geo`) | `lat`, `lng`, `taken_at`, `captured_with` | Map view & near-by search — **only when `gps_retention` is granted** |
| **AI summary** | Topic, body, bullet points, signals (objects, scenes, captions) | Search by meaning — **only when `ai_summary` is granted** |
| **CLIP embeddings** | 768-float vector per image | Semantic search — **only when `semantic_search` is granted** |
| **Face crops** | 256×256 JPEG of each detected face | Biometric template **— only when `face_recognition` is granted (BIPA-grade written consent)** |
| **Face embeddings** | 512-float vector per face | Biometric template — same consent gate |
| **Person clusters** | (face → person_id) assignments and operator-supplied labels | People view — same consent gate |
| **Bandit state** | Per-(user, arm) learned compression preference weights | Adapts compression quality from rating signals |
| **Feedback events** | Image + arm + reward signal | Drives the bandit; deleted with the source image |
| **Tags** | User-assigned labels per image | Filtering |
| **Folders** | User-defined organizational containers | Filing |
| **Shares** | Recipient email + token hash + expiry per (image, recipient) | Sharing — see §6 |
| **Audit log** | Every meaningful action (sign-in, delete, consent change, share, admin action) | Security, compliance, deletion-trace |
| **Consent records** | Per-grant row with timestamp, IP, user-agent, signature, policy hash | Legal record — see §3 |
| **Recovery codes** | 8 single-use Argon2-hashed codes per user | Account recovery |

We do **NOT** collect:

- Your contacts, phone book, calendar, location-history, or any
  system-level data outside what you upload.
- Browser fingerprints, cross-site identifiers, ad IDs.
- Anything via third-party advertising / analytics SDKs — we don't
  load any.
- Cookies. See §4.

## 3. Consent log

Every grant/revoke of a consent scope writes a `consent_records` row
captured **before the action takes effect**, with:

- `granted_at` — UTC timestamp
- `ip` — client IP at the time of the action (honoring the operator's
  `TRUST_PROXY_HEADERS` setting)
- `user_agent` — UA string the browser sent
- `consent_kind` — which scope (`face_recognition`, `gps_retention`,
  `ai_summary`, `semantic_search`, `bandit_compression_telemetry`,
  `exif_retention`)
- `state` — `GRANTED` or `WITHDRAWN`
- `policy_version` — `v1`
- `policy_text_sha256` — hash of the policy file the user agreed to
- `signature_text` — the literal text the user typed (or display name
  fallback when collected via the §B2 register bundle)

The `consent_records` table has Postgres Row-Level-Security forced
on it — a user only sees their own rows even at the DB layer.

## 4. Cookies and local storage

The neuthek backend **does not set any cookies**. The test
`test_backend_does_not_set_cookies` runs on every CI build and fails
if a route ever returns a `Set-Cookie` header.

Authentication uses JWTs in the `Authorization: Bearer …` header, not
cookies, so cross-site request forgery against the API is structurally
impossible — a third-party site can't carry the user's auth header
along on a forged request.

The frontend uses **`localStorage`** (not cookies) for these keys
inside your browser:

| Key | Purpose | Survives sign-out? |
|---|---|---|
| `neuthek.theme` | Selected light/dark theme | yes |
| `neuthek.cookie` | The choice you made on the first-visit notice (so we don't show it again) | yes |
| `neuthek.recentSearches` | Up to 8 recent search queries | yes |
| `neuthek.auth_token` | JWT for the signed-in session | cleared on sign-out |

`localStorage` is per-origin and per-browser; clearing it (browser
settings → site data) is equivalent to "clear cookies" on a
cookie-based app. The first-visit notice is purely informational and
does not load tracking — accepting or declining changes nothing
about our processing.

## 5. Age gate

You must confirm you are old enough to use neuthek when you register
(`age_confirmed=true` on the registration payload). The server-side
validator rejects the registration if this is `false`. **Children
under 13 are prohibited** — neuthek does not implement the COPPA
verifiable-parental-consent flow, so we cannot lawfully service
under-13 accounts in the US.

If we become aware of an under-13 account, we will close it and delete
the associated data per §7.

## 6. Sharing

When you share a file you supply a recipient email and a duration.
We:

- Generate a 256-bit token, send the plaintext exactly once to the
  copy buffer, and store only an Argon2 hash on the server.
- Pin the share to the recipient email (case-folded, Unicode-NFKC
  normalized — homograph attacks are rejected).
- Cap durations at 30 days for existing-account recipients; 1 day for
  brand-new recipients (regardless of what the sharer requested).
- Audit-log creation, claim, replace, revoke, and every read of the
  shared bytes.

The recipient can read the bytes for the duration window. Revoking
the share invalidates the token; bytes are no longer servable.

We do **not** email the share link from our servers (operator's
email infra is not always available) — the sharer copies the URL
themselves.

## 7. Retention & deletion

You can delete individual files, bulk-selected files, or your whole
account. Deletion is **synchronous and immediate** and removes:

- The `originals` and `served` blobs from object storage
- The thumbnail blob
- The image row + every CASCADE sibling (EXIF/GPS, image_tags,
  feedback_events, share_grants, face_detections)
- Face crops in the biometric bucket + face embedding rows
- Orphan persons (face_count → 0)
- For account deletion: the user row, every consent record, recovery
  codes, bandit state, cloud-OAuth refresh tokens

The integration test
[`tests/test_a5_full_deletion.py`](tests/test_a5_full_deletion.py)
asserts every table + bucket returns zero rows / zero objects for the
deleted target.

**Audit log entries are NOT deleted** — they remain as a legal /
security record, with the user_id automatically anonymized to NULL
after `audit_log_retention_days` (default 365). The append-only
Postgres trigger `prevent_audit_mutation` enforces this at the DB
layer.

**Backups**: encrypted nightly backups capture the state at the
moment they ran. A user who hard-deletes after a backup ran will
still have their bytes in that backup file until the operator's
chosen retention window expires (default recommended **30 days**).
See [SECURITY.md](SECURITY.md) "Encrypted backups → Retention +
GDPR Article 17" for the two acceptable operator paths.

### Grace window

`/account/schedule-delete` stamps a `scheduled_delete_at` 30 days in
the future instead of nuking the row immediately. Inside the window
the user can still call `/account/cancel-delete` and recover.

## 8. Embeddings and biometrics

Embeddings are sensitive: a CLIP vector encodes semantic content,
and a face embedding is biometric data subject to BIPA / GDPR
special-category handling.

- **CLIP embeddings** (`images.clip_embedding`): per-user vector
  search; never compared cross-user. Deleted with the image row.
- **Face embeddings** (`faces.embedding`): only created when
  `face_recognition` is granted. Per-user. Deleted with the face row.
  Withdrawing `face_recognition` deletes every face row, every
  detection row, every face crop blob, and every orphan person row
  — in a single transaction — and writes an audit entry naming the
  count.
- Both vector classes live behind Row-Level-Security (forced) so the
  app layer can't accidentally cross-reference vectors from another
  user, even via a future bug in a WHERE clause.

We **never** train shared global models on user data. The bandit
state is per-(user, arm); the Florence-2 / Qwen / CLIP / RetinaFace
models we use are pre-trained and frozen for the duration of a
release.

## 9. Your rights

If you're in a jurisdiction with codified data-subject rights
(EU / EEA, UK, California, Illinois, etc.), the rights below apply.
Many of them apply to everyone anyway because we don't differentiate
processing by region:

- **Access**: `/account/export` returns a ZIP of every row about you
  (metadata.json) plus the actual originals/served/thumb/face-crop
  bytes.
- **Rectification**: change your display name, email, or profile via
  Account → Profile.
- **Erasure**: `/account/delete` (immediate) or
  `/account/schedule-delete` (30-day grace).
- **Portability**: the export ZIP at `/account/export` includes CLIP
  embeddings, summaries, GPS rows, consent records, audit-log entries
  scoped to your account.
- **Restriction / objection**: revoke any consent scope to halt the
  associated processing immediately; biometric data is wiped on
  revoke.

Email **[Operator: privacy@your-domain]** for anything the
self-service flows don't cover.

## 10. Children

neuthek is not for children. See §5.

## 11. International transfers

If the deployment is hosted, data may sit in the host's region (the
operator should disclose this on their site). neuthek itself stays
inside the operator's deployment — no cross-jurisdiction transfer is
performed by neuthek code.

## 12. Changes to this notice

Material changes are flagged in the changelog. The current
`policy_version` is `v1`. When we bump to `v2` we re-request consent
from every user for the affected scopes; the previous grants stay in
the consent log as an immutable record.
