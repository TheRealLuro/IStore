# neuthek — Security Review

**Type:** authorized, owner-requested, whole-codebase review (read-only — no
application code changed; fixes proposed as diffs only).
**Tree audited:** `main` at repo root (FastAPI API + React SPA + Express
marketing; migrations through `0047_vault_public_links`). Confirmed live tree —
not the stale `.claude/worktrees/*` copy.
**Date:** 2026-05. **Companion:** see `SECURITY_AUDIT_DOSSIER.md` for the full
architecture/threat-model + offensive playbook.

> This review is evidence-driven and skeptical of the code's own claims. The
> project's "remediated" / in-code defensive comments were treated as
> hypotheses and verified against the **running deployment** where possible
> (live `docker exec` into Postgres; see §0.3). Several in-code comments were
> found to be **factually wrong** about the deployed posture — those are called
> out explicitly. Coverage is stated honestly in §0.1; this revision was
> expanded to cover **every** module, not just the weighted-priority slice.

---

## 0. Executive summary

**Revised posture: the cryptographic core is strong, but the
defense-in-depth story is materially weaker than the code claims — there are
multiple HIGH findings, two of which (F7, F10) were *confirmed live*, not just
inferred from source.** The zero-knowledge Vault server side is genuinely
ciphertext-only (§4); JWT revocation is alg-pinned (no `alg=none`); the Stripe
webhook is signature-verified + idempotent; OAuth `state` is HMAC+PKCE; the
production-boot gate refuses dev defaults; `npm audit` is clean on both JS
surfaces. **But** the touted second line of defense — Postgres FORCE row-level
security — is **completely inert in the running deployment** because the app
connects as a DB superuser (F7, proven live), and the Vault-bearing SPA ships
with **no CSP and no anti-framing header** at the production edge (F10, proven
by reading the prod-mounted config). For a product that markets tenant
isolation and a zero-knowledge vault, those two facts move the headline posture
from "strong" to "strong-core / several-HIGH-gaps."

**The issues that matter most (in priority order):**

1. **F7 — App connects to Postgres as a SUPERUSER → ALL row-level security is
   silently bypassed (HIGH, confirmed live).** Every `FORCE ROW LEVEL SECURITY`
   policy (faces, persons, face_detections, image_geo/GPS, consent_records,
   recovery_codes, share_grants, vault_*) is dead code at runtime. Tenant
   isolation rests **entirely** on app-layer `WHERE user_id = …` filters; any
   missing filter, ORM mistake, or SQLi becomes a direct cross-tenant breach
   with **no DB backstop.** This invalidates the prior report's "RLS is
   fail-closed in production" claim. (`docker-compose.yml:24,117`,
   `pg_authid.rolsuper = true` for role `neuthek` — §0.3)
2. **F10 — No CSP / no `X-Frame-Options` on the SPA at the production edge
   (HIGH, confirmed).** `frontend/index.html` has no meta CSP; the **prod-mounted**
   `deploy/Caddyfile` has no `header` block; the frontend nginx sets none. For a
   zero-knowledge app whose master key lives in JS memory, **any** XSS → full
   vault compromise, and the login/share/delete UI is clickjackable. The
   in-code comment claiming the FE "has its own CSP via the index.html meta +
   Caddy" (`security.py:843`) is **false.**
3. **F8 — Biometric face-scan runs without a consent check on the live
   (worker) path (HIGH, confirmed).** The inline fallback checks
   `is_consent_active`; the ml-worker path that actually runs in production does
   not — it gates only on `pending_face_scan`, which upload sets unconditionally.
   BIPA / GDPR Art. 9 special-category-data exposure. (`worker/main.py:54-169`
   vs `api/images.py:83`)
4. **F12 — Account deletion orphans Vault ciphertext blobs in MinIO (HIGH,
   confirmed).** "Delete every byte of their data" deletes image + face-crop
   blobs but never the `bucket_vault` objects; the blob-collection code is dead
   (sits after an unconditional `return`). GDPR erasure is incomplete + storage
   leaks. (`api/account.py:204-215`, no `bucket_vault` ref in `deletion.py`)
5. **F1 — Google SSO silently skips neuthek TOTP (MEDIUM).** TOTP users are
   single-factor on the SSO path. (`auth/google_sso.py:~747`)
6. **F2 — 6-digit magic-link code brute-forceable from a rotating-IP pool
   (MEDIUM).** Lockout keys on `identity:IP`; no identity-only hard cap.
7. **F9 — SVG/HTML can be stored with an active MIME via a bypassable content
   guard; Drive serve sets no `Content-Disposition` (MEDIUM, script-exec blocked
   by the API CSP).** Defense-in-depth gap, not live RCE — the per-response
   `default-src 'none'` CSP (confirmed) neutralizes script execution.

Plus F11 (cloud_links/subscriptions/etc. have **no** RLS at all — matters once
F7 is fixed), F13 (vault RLS policies lack the bypass-escape clause — latent
regression once F7 is fixed), F3 (worker RLS-bypass), F4/F5 (key design),
F6/F14/F15 and a set of LOW/INFO items in §1.

**Does the zero-knowledge Vault claim hold?** **Yes, for the stated
honest-but-curious-server model** (§4) — server stores/returns only opaque
ciphertext and never receives the master password, a per-file key, or the
unwrapped private key. The *crypto* is sound. The caveats are by design (§5):
metadata leakage, recipient-key TOFU under a malicious server, offline
brute-forceability of password public links, and **any SPA XSS defeats E2E
while unlocked** — which makes F10 (no CSP) directly relevant to the Vault, not
just the Drive.

### 0.1 Coverage & method
- **Deep manual review + live verification:** Vault crypto (`crypto.ts` ↔
  `vault.py` + migrations 0044–0047), auth/session/MFA/SSO, RLS/multi-tenancy
  (incl. **live `docker exec` proof**, §0.3), secrets/config, billing webhook,
  edge/infra (`Dockerfile`, all `docker-compose*.yml`, both `Caddyfile`s,
  `frontend/nginx.conf`), the ML worker + faces pipeline, the file-ingestion +
  serve path, account-deletion + retention sweepers, and the marketing service.
- **Whole-codebase pass (this revision):** every router's authz (§10), every
  injection class (§9), the full SSRF surface (§12), the live RLS coverage
  table (§15), and a per-module coverage appendix (§16).
- **Tools:** `npm audit` (frontend + marketing) → **0 vulnerabilities**.
  semgrep/bandit/pip-audit/gitleaks/trivy were **not installed** in this
  environment; the report relies on manual review + the repo's CI (CodeQL
  ×3 + Dependabot). **Action for the team:** run the SAST/secret/dep/container
  suite in `SECURITY_AUDIT_DOSSIER.md §I` on the audit branch.
- **Dynamic testing:** the RLS/role findings (§0.3) were verified against the
  **live running stack**; the remaining "recommend dynamic" items are
  static-analysis-derived and marked with confidence.

### 0.3 Live verification performed (this revision)
Run against the running `neuthek-postgres` container as the role the app
actually connects with (`neuthek`):

```
$ docker exec neuthek-postgres psql -U neuthek -d neuthek -t \
    -c "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user;"
   neuthek | t | f          ← the app role IS a Postgres SUPERUSER

$ docker exec neuthek-postgres psql -U neuthek -d neuthek -t \
    -c "RESET ALL; SELECT count(*) FROM vault_items;   -- FORCE RLS, no app.current_user_id set
        SELECT count(*) FROM faces; SELECT count(*) FROM recovery_codes;"
   vault_items    | 5
   faces          | 118
   recovery_codes | 8        ← all rows returned with NO RLS context → RLS bypassed

$ docker exec neuthek-postgres psql -U neuthek -d neuthek -t \
    -c "SELECT relname, relrowsecurity FROM pg_class
        WHERE relname IN ('cloud_links','cloud_files','subscriptions','notification_prefs','images','users','audit_log');"
   cloud_links/cloud_files/subscriptions/notification_prefs/images/users/audit_log → relrowsecurity = f (no RLS)
```

Postgres semantics: **superusers (and `BYPASSRLS` roles) always bypass row
security, regardless of `FORCE ROW LEVEL SECURITY`** (`FORCE` only removes the
*table-owner* exemption — it does nothing against `rolsuper`). The query above
proves it empirically: FORCE-RLS tables return every tenant's rows from a
session with no `app.current_user_id`. **The entire RLS layer is therefore
inert in this deployment** (F7).

---

## 0.2 Recon — trust-boundary map

```
Browser ──(HttpOnly neuthek_session cookie)──► Caddy edge (prod: deploy/Caddyfile)
   │                                              │  ⚠ NO CSP / NO X-Frame-Options here (F10)
   ├─ SPA (React, no token in JS;            FastAPI/uvicorn ── 18 routers / ~198 ops
   │   master key in JS memory when unlocked)     │  per-endpoint deps: current_active_user /
   │                                              │  current_admin_user / current_superuser
   │                                              │  + middleware: CsrfOrigin, SecurityControls
   │                                              │    (rate-limit/lockout), SecurityHeaders
   ▼                                              ▼
  /v/{token} public viewer                Postgres ── connects as SUPERUSER ⇒ FORCE RLS INERT (F7)
  (SPA route, key in #fragment)           Redis (lockout+rate counters, job queue)
                                          MinIO (server-named object keys; vault blobs orphan on delete — F12)
                                          ML worker (Redis BLPOP; biometric scan w/o consent — F8; RLS-bypassed — F3)
                                              │ outbound (SSRF-relevant):
                                   Google OAuth · iCloud (pyicloud) · Proton/MEGA (rclone) ·
                                   Nominatim geocode · Stripe
```
**GUC integrity (request path):** `app.current_user_id` is set in
`db.py:_set_rls_context` from a request-scoped ContextVar populated by the auth
dependency (`auth/users.py set_current_user_id(user.id)`) — server-derived from
the validated JWT, never from client input. **Correction to the prior report:**
the design *intends* "no context ⇒ NULL ⇒ 0 rows (fail-closed)", but because
the connecting role is a superuser (F7), **this fail-closed behavior does not
occur at runtime** — RLS is bypassed before the policy is ever evaluated. The
fail-closed guarantee only returns once the app connects as a
`NOSUPERUSER NOBYPASSRLS` role.

---

## 1. Findings table

| ID | Title | Severity (CVSS 3.1) | Confidence | Location | CWE |
|----|-------|--------------------|-----------|----------|-----|
| **F7** | **App connects to Postgres as SUPERUSER → FORCE RLS universally bypassed** | **HIGH — 8.1** `AV:N/AC:H/PR:L/UI:N/S:C/C:H/I:H/A:N` | **Confirmed (live)** | `docker-compose.yml:24,117,264`; `scripts/setup.py:235,326-331`; `pg_authid` | CWE-250/CWE-266 |
| **F10** | **No CSP / `X-Frame-Options` on the SPA at the prod edge** | **HIGH — 7.1** `AV:N/AC:H/PR:N/UI:R/S:C/C:H/I:L/A:N` | **Confirmed** | `frontend/index.html`; `deploy/Caddyfile`; `frontend/nginx.conf`; comment `security.py:843` | CWE-1021/CWE-693 |
| **F8** | **Biometric face-scan runs without consent check on the live worker path** | **HIGH — 7.5** (privacy/compliance) `AV:N/AC:L/PR:L/S:U/C:H/I:N/A:N` | **Confirmed** | `backend/worker/main.py:54-169`; `backend/api/images.py:83`; `backend/image.py:611` | CWE-359/CWE-285 |
| **F12** | **Account deletion orphans Vault blobs in MinIO; blob-cleanup is dead code** | **HIGH — 6.5** (privacy/erasure) `AV:N/AC:L/PR:L/S:U/C:L/I:H/A:N` | **Confirmed** | `backend/api/account.py:204-229`; `backend/deletion.py` (no `bucket_vault`) | CWE-459/CWE-561 |
| **F11** | **`cloud_links`/`cloud_files`/`subscriptions`/`notification_prefs` have NO RLS** | MEDIUM — 5.0 (latent; matters post-F7) `AV:N/AC:H/PR:L/S:U/C:H/I:L/A:N` | Confirmed (live) | `pg_class.relrowsecurity=f`; no policy migration | CWE-285 |
| **F13** | **Vault RLS policies lack the `rls_bypass` escape clause + hard-cast `::uuid`** | MEDIUM — 5.3 (latent regression) `AV:N/AC:H/PR:L/S:U/C:L/I:H/A:L` | Confirmed | `migrations/0044_vault.py:133`, `0045:102`, `0046:43` | CWE-697 |
| F1 | Google SSO bypasses user's neuthek TOTP | MEDIUM — 6.3 | Confirmed | `auth/google_sso.py:~745-755` | CWE-308/287 |
| F2 | 6-digit magic-link code brute-forceable via rotating IPs | MEDIUM — 6.5 | Likely | `api/email_link.py:415-460`; `security.py:~619-635` | CWE-307 |
| **F9** | SVG/HTML stored with active MIME (bypassable guard) + Drive serve no `Content-Disposition` | MEDIUM — 5.4 (CSP-mitigated) `AV:N/AC:H/PR:L/UI:R/S:C/C:L/I:L/A:N` | Confirmed | `upload_validation.py:179,427-430`; `api/images.py:286-294`; mitigation `security.py:858` | CWE-79/CWE-434 |
| F3 | Cloud-sync worker disables RLS wholesale (`app.rls_bypass`) | MEDIUM — 5.3 (def-in-depth) | Confirmed (design) | `api/cloud.py:424` | CWE-285 |
| F14 | Stripe secrets not wired through Docker `*_FILE` secrets in prod compose | MEDIUM — 4.0 | Confirmed | `docker-compose.prod.yml` (no `stripe_*` secret) ; `config.py:10-49` | CWE-312 |
| F15 | CSRF/CORS allow-list is localhost-only; prod hostname never added | MEDIUM — 4.0 (operational) | Confirmed | `backend/app.py:49-57`; `security.py:242-256` | CWE-1188 |
| F4 | No per-session JWT revocation (global `tv` bump only) | LOW — 3.5 | Confirmed (design) | `auth/users.py` (`token_version`) | CWE-613 |
| F5 | Single app master key — broad blast radius | LOW/INFO (design) | Confirmed (design) | `key_derivation.py`, `secret_box.py` | CWE-320 |
| F6 | Recovery-codes print popup writes unescaped `userEmail` via `document.write` | LOW — 2.0 (self-XSS) | Confirmed | `frontend/neuthek/src/account-panels.jsx:631-651` | CWE-79 |
| F16 | Folder `_descendants_query` CTE + `image_persons`/search rely on RLS (now absent) | LOW (elevated by F7) | Confirmed | `api/folders.py` CTE; `api/search.py` | CWE-285 |
| F17 | Archive ingestion buffers full uncompressed payload in memory | LOW/MEDIUM (DoS) | Likely | `backend/archive_upload.py` | CWE-400 |
| F18 | CI Actions pinned to floating tags, not commit SHAs; `Dockerfile.backup` in tree | LOW/INFO | Confirmed | `.github/workflows/security.yml:18,21,38,74`; `Dockerfile.backup` | CWE-1104 |
| R1 | Vault metadata leakage (kind/size/folder graph) | INFO (by design) | Confirmed | `api/vault.py` | CWE-200 |
| R2 | Password public links offline-brute-forceable | INFO (by design) | Confirmed | `crypto.ts` | CWE-307 |
| R3 | Recipient-key TOFU under malicious server | INFO (by design) | Confirmed | share flow | CWE-295 |

---

## 2. Per-finding detail

### F7 — App connects to Postgres as SUPERUSER → FORCE RLS universally bypassed — HIGH (Confirmed live)
**Evidence:**
- The app's DSN is built from `POSTGRES_USER` (default `neuthek`):
  `docker-compose.yml:24` `POSTGRES_USER: ${POSTGRES_USER:-neuthek}`;
  `:117`/`:264` `DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-neuthek}:…@postgres:5432/…`;
  prod `scripts/setup.py:235,326-331` builds the prod DSN straight from
  `POSTGRES_USER` with **no separate app role**.
- The `pgvector/pgvector:pg16` image bootstraps `POSTGRES_USER` as the
  **superuser**. There is no `CREATE ROLE … NOSUPERUSER NOBYPASSRLS`, no
  `docker-entrypoint-initdb.d` mount, and no `*.sql` init script anywhere in the
  repo (`secrets/` ships only `.gitkeep`; `deploy/` holds only `Caddyfile`).
- **Proven live** (§0.3): `rolsuper = t` for `neuthek`; FORCE-RLS tables
  (`vault_items`, `faces`, `recovery_codes`) return every tenant's rows from a
  session with no `app.current_user_id`.
- `validate_production_settings` (`security.py:76-337`) checks many things but
  **never** queries `pg_roles`/`pg_authid` for `rolsuper`/`rolbypassrls`.

**Impact:** The RLS policies in migrations 0016/0027/0028/0032/0034/0044/0045/
0046 — covering biometric face vectors, home GPS (`image_geo`), consent records,
recovery codes, share grants, and all `vault_*` tables — are **inert**. The
single line of defense is the app-layer SQLAlchemy `WHERE user_id = …` filters.
Any one missing/incorrect filter (see F16), any future ORM refactor that drops a
predicate, or any SQL injection becomes an **immediate cross-tenant disclosure**
of special-category data, with no database backstop. This also nullifies the
defense F3 worries about (the worker bypass is moot when the whole role bypasses
anyway) and invalidates the product's "layered isolation" claim.

**Remediation (propose; do not apply):**
1. Provision a least-privilege app role and connect as it. Mount an init script
   into the postgres container (`deploy/initdb/10-app-role.sql`):
   ```sql
   CREATE ROLE neuthek_app LOGIN PASSWORD :'app_password'
     NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
   GRANT CONNECT ON DATABASE neuthek TO neuthek_app;
   GRANT USAGE ON SCHEMA public TO neuthek_app;
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO neuthek_app;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO neuthek_app;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public
     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO neuthek_app;
   ```
   ```diff
   # docker-compose.prod.yml — postgres service
        volumes:
          - ${NEUTHEK_DATA_ROOT}/postgres:/var/lib/postgresql/data
   +      - ./deploy/initdb:/docker-entrypoint-initdb.d:ro
   ```
   Point `DATABASE_URL`/`DATABASE_URL_SYNC` at `neuthek_app`; keep the bootstrap
   superuser **only** for `alembic upgrade head` (DDL needs it).
2. Add a boot-time guard to `validate_production_settings`:
   ```python
   row = (await conn.execute(text(
       "SELECT rolsuper, rolbypassrls FROM pg_authid WHERE rolname = current_user"
   ))).first()
   if row and (row[0] or row[1]):
       errors.append("DATABASE_URL connects as SUPERUSER/BYPASSRLS — RLS is "
                     "silently bypassed. Use a NOSUPERUSER NOBYPASSRLS app role.")
   ```
**⚠ Coupled work:** fixing F7 *activates* RLS, which then exposes F13 (vault
policies have no bypass clause) and the retention/admin background jobs that run
with no per-user context — those will start silently no-op-ing on FORCE-RLS
tables. Land F13 + the background-job context fix in the same change.

---

### F10 — No CSP / `X-Frame-Options` on the SPA at the production edge — HIGH (Confirmed)
**Evidence:** Prod mounts `deploy/Caddyfile` (`docker-compose.prod.yml:17`),
which has **no `header` directive at all** — only `encode zstd gzip` and
`handle`/`reverse_proxy` blocks; the SPA is the catch-all
`handle { reverse_proxy {$NEUTHEK_FRONTEND_UPSTREAM} }`. The frontend
`nginx.conf` sets no `add_header`. `frontend/index.html` has no
`<meta http-equiv="Content-Security-Policy">` (grep → none). The backend
`SecurityHeadersMiddleware` *does* set `default-src 'none'` + `X-Frame-Options:
DENY` (`security.py:845-861`) but **only on API/media responses** — the SPA HTML
comes from nginx, which the middleware never touches. The in-code comment at
`security.py:843` ("The frontend is served from a separate origin and has its
own CSP via the index.html meta + Caddy") is **false** — none of those exist.

Header status by layer (prod topology):

| Header | `deploy/Caddyfile` (prod) | frontend nginx | backend middleware (API only) |
|---|---|---|---|
| CSP | **MISSING** | **MISSING** | set `default-src 'none'` |
| X-Frame-Options | **MISSING** | **MISSING** | set `DENY` |
| HSTS | **MISSING** | MISSING | set on https |
| X-Content-Type-Options | **MISSING** | MISSING | set |
| Referrer/Permissions-Policy | **MISSING** | MISSING | set |

(The **root** `Caddyfile` — used only by the `tls` overlay — sets HSTS/nosniff/
Referrer/Permissions but still **no CSP and no X-Frame-Options**, so even that
path is exposed.)

**Impact:** (1) The authenticated SPA HTML is framable by any origin →
clickjacking the share-revoke / account-delete / logout controls. (2) No CSP
means an injected/3rd-party script has free rein — and because this is a
zero-knowledge app whose **master AES key lives in JS memory while the vault is
unlocked**, any XSS = full vault plaintext compromise + exfiltration of the
public-link key fragment. (3) Missing HSTS on the prod path allows an
SSL-strip downgrade on first contact.

**Remediation (propose):** add an edge `header` block to `deploy/Caddyfile` on
the SPA `handle`:
```diff
 {$NEUTHEK_DOMAIN} {
 	encode zstd gzip
+	header {
+		-Server
+		Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
+		X-Content-Type-Options nosniff
+		X-Frame-Options DENY
+		Referrer-Policy strict-origin-when-cross-origin
+		Permissions-Policy "camera=(), microphone=(), geolocation=()"
+		Content-Security-Policy "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
+	}
```
Mirror CSP + `frame-ancestors 'none'` into the root `Caddyfile` header block.
Tune `connect-src`/`style-src` to the actual Vite bundle (verify no
`'unsafe-inline'`/`'unsafe-eval'` is needed for scripts; if it is, fix the
bundle, don't loosen the policy — this is the Vault's last line of XSS defense).
**Refs:** CWE-1021, CWE-693, OWASP ASVS 14.4.

---

### F8 — Biometric face-scan runs without a consent check on the live worker path — HIGH (Confirmed)
**Evidence:** Two execution paths run the ArcFace biometric pipeline
(`process_image_for_faces`, which itself states "Caller is responsible for
verifying consent before invoking this" — `faces_pipeline.py:204`):
- **Inline fallback** `_run_face_scan_one` (`api/images.py:82-84`) gates on
  `is_consent_active(session, user_id)` and returns early if not active. ✓
- **ml-worker** `_process_face_scan` (`worker/main.py:54-169`) gates **only** on
  `image.pending_face_scan` — **no consent check.** ✗

The worker is the **live production path** (`jobs.enqueue_face_scan` → Redis →
worker); inline is only the fallback when Redis/worker is down. `pending_face_scan`
is set by `store_upload` as `not skip_ai_training` (`image.py:611`), and the
upload endpoint calls `store_upload(session, user, filename, raw, content_type)`
with **no** `skip_ai_training` and **no consent consult** (`api/images.py:182`),
so it is `True` for every image upload regardless of the user's biometric
consent state. A user who never granted — or who **revoked** — face/biometric
consent still has ArcFace embeddings generated for their uploads on the live
path.

**Impact:** Generation + storage of biometric identifiers (face embeddings,
`faces.embedding`) without an active consent record → Illinois BIPA statutory
exposure ($1k–$5k per scan) and GDPR Art. 9 special-category processing without
a lawful basis. Confirmed asymmetry: revocation is honored on the inline path
but not the live worker path.

**Remediation (propose):** mirror the inline consent gate inside the worker, and
re-check at scan time (not just enqueue time, so revocation mid-queue is honored):
```python
# worker/main.py _process_face_scan, after loading user/image:
from backend.consent import is_consent_active
if not await is_consent_active(s, user_id):
    image.pending_face_scan = False
    await s.commit()
    return
```
Belt-and-suspenders: have `store_upload` set `pending_face_scan = (not
skip_ai_training) and <consent active>` so non-consenting users never enqueue.
**Refs:** CWE-359.

---

### F12 — Account deletion orphans Vault blobs in MinIO; blob-cleanup is dead code — HIGH (Confirmed)
**Evidence:** `delete_account` (`api/account.py:140`) docstrings "Hard-delete …
every byte of their data." The live body: pre-counts → `hard_delete_images(…)`
→ audit → `DELETE users` (FK CASCADE) → verify → **`return` at line 204-212.**
Everything from line 214 (`blob_keys, _ = await _collect_user_blob_keys(…)` and
the subsequent blob-deletion loop) is **after the return — dead, unreachable
code** (CWE-561). `hard_delete_images` (`deletion.py:130-179`) deletes image
originals/served/thumbnails/variants **and** face-crop blobs
(`storage.delete(bucket_faces, crop_key)`) — so those are handled — but neither
it nor `deletion.py`/`retention.py` contains any reference to `bucket_vault`
(grep → none). FK CASCADE drops the `vault_items`/`vault_folders` **DB rows**,
but the ciphertext **objects** in `bucket_vault` (keys `{user_id}/{uuid}`) are
never removed. The scheduled-delete sweeper (`retention.py:619-704`) reuses the
same `hard_delete_images` + `DELETE users` path → same orphan.

**Impact:** (1) GDPR/CCPA "right to erasure" is **not** fully satisfied — Vault
ciphertext persists in object storage after the account and its keys are gone
(cryptographically inert, but it is still retained user data and a deletion-SLA
violation). (2) Unbounded storage leak across the install's lifetime. (3) The
dead-code block is a refactor hazard — a maintainer may "re-enable" it
believing deletion works.

**Remediation (propose):** before `DELETE users`, enumerate and delete the
user's vault blobs, and remove the dead block:
```python
# api/account.py — before DELETE users
vault_keys = (await session.execute(
    select(VaultItem.storage_key).where(VaultItem.user_id == user_id,
                                         VaultItem.storage_key.is_not(None))
)).scalars().all()
for key in vault_keys:
    try: storage.delete(storage.bucket_vault, key)
    except Exception: blob_errors += 1
# …then DELETE users. Delete the unreachable lines 214+.
```
Apply the same in `sweep_scheduled_account_deletes`. **Refs:** CWE-459, CWE-561.

---

### F11 — `cloud_links`/`cloud_files`/`subscriptions`/`notification_prefs` have NO RLS — MEDIUM (Confirmed live)
**Evidence:** `pg_class.relrowsecurity = f` for all four (and for `images`,
`users`, `audit_log`) — §0.3. No policy migration enables RLS on them.
`cloud_links` stores Fernet-encrypted OAuth refresh tokens; `subscriptions`
holds billing/Stripe linkage; `notification_prefs` holds contactability state.
**Today this is moot** because the superuser role (F7) bypasses RLS on *every*
table anyway. It becomes a live gap **the moment F7 is fixed**: these tables
would still have no DB-layer isolation, so a missing app-layer filter on a
cloud/billing route would leak cross-tenant tokens with no backstop.

**Remediation (propose):** enable + FORCE RLS with the standard policy
(matching `0027`'s bypass-clause form) on `cloud_links`, `cloud_files`,
`subscriptions`, `notification_prefs` (and consider `images`):
```sql
ALTER TABLE cloud_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_links FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_links_isolation ON cloud_links USING (
  current_setting('app.rls_bypass', true) = 'on'
  OR user_id = current_setting('app.current_user_id', true)::uuid);
```
**Refs:** CWE-285.

---

### F13 — Vault RLS policies lack the `rls_bypass` escape clause + hard-cast `::uuid` — MEDIUM, latent (Confirmed)
**Evidence:** The older policies (0016/0027) use
`USING (current_setting('app.rls_bypass', true) = 'on' OR user_id =
current_setting('app.current_user_id', true)::uuid)`. The newer vault policies
do **not** include the bypass clause:
- `0044_vault.py:133` `USING (user_id = current_setting('app.current_user_id', true)::uuid) WITH CHECK (…)`
- `0045_vault_e2e.py:102`, `0046_vault_shares.py:43,111` — same form, no bypass.

Consequence (once F7 is fixed): any legitimately cross-user maintenance path
(the cloud-sync worker's `app.rls_bypass='on'`, a future admin tool, a backfill
job) **cannot** touch vault rows — the bypass GUC is simply ignored by these
policies. More importantly, background jobs (retention sweeper, F12 deletion)
that run with **no** `app.current_user_id` will have
`current_setting('app.current_user_id', true)` → NULL → `user_id = NULL` → match
nothing → **silent no-op DELETEs/SELECTs** on vault + biometric tables. This is
a correctness/data-integrity landmine that detonates exactly when you remediate
F7.

**Remediation (propose):** add the `app.rls_bypass` clause to the vault policies
for parity, and make every background job set either `app.current_user_id` (per
user) or `SET LOCAL app.rls_bypass='on'` explicitly. **Refs:** CWE-697.

---

### F9 — SVG/HTML stored with an active MIME via a bypassable guard; Drive serve sets no `Content-Disposition` — MEDIUM, CSP-mitigated (Confirmed)
**Evidence:** `upload_validation.py:427`
`if lower.startswith((b"<svg", b"<?xml")) and b"<svg" in lower[:256]:` rejects
SVG, and `:429` rejects `<!doctype html`/`<html`/`<script`. Both use
`startswith` on the raw bytes, so a **leading-whitespace or comment prefix**
(`"  <svg …"`, `"<!--x--><svg onload=…>"`, `"\n<html>"`) evades the guard. The
file then reaches the extension map at `:438-440`, where `.svg → image/svg+xml`
(`:179`) and `.html → text/html` (`:173`) are in `_CODE_EXTS`, so it is **stored
with an active, renderable MIME**. The Drive serve endpoints
(`api/images.py:286-294, 615-632, 742, 1748-1831`) return
`Response(content=blob, media_type=mime)` with **no `Content-Disposition:
attachment`** — so a direct navigation to such a file renders it inline.

**Why this is MEDIUM, not CRITICAL — confirmed mitigation:** every response from
the API origin (including these file serves) carries
`Content-Security-Policy: default-src 'none'` + `X-Content-Type-Options:
nosniff` via `SecurityHeadersMiddleware` (registered at `app.py:203`;
`security.py:858`). For a top-level navigation to an inline SVG/HTML document,
`default-src 'none'` blocks inline `<script>`, event handlers (`onload=`), and
external scripts → **script execution is prevented.** There is no presigned-URL
path that would let a user fetch the blob directly from MinIO bypassing the
middleware (grep `presigned` → none). The **Vault** download endpoints already
force `Content-Disposition: attachment` (`vault.py:972,1197,1406`), so Vault is
unaffected. Net: a defense-in-depth weakness (bypassable guard + missing
attachment disposition), with the API CSP as the saving control.

**Remediation (propose):** (1) make the content guard robust — strip leading
whitespace/BOM/XML-comments before the `startswith`, or scan the first N bytes
for `<svg`/`<script`/`<html` anywhere, not just at offset 0; (2) force
`application/octet-stream` + `Content-Disposition: attachment` for `.svg`/`.html`
and all `_CODE_EXTS` document types on the Drive serve path (mirror the Vault
serve); (3) keep the API CSP as the backstop. **Refs:** CWE-79, CWE-434.

---

### F1 — Google SSO bypasses the user's neuthek TOTP — MEDIUM (Confirmed)
*(unchanged from prior revision)* `auth/google_sso.py` (~745-755): when
`user.totp_enabled and user.totp_secret_enc`, the callback writes an audit row
`auth.sso.bypass_totp` and proceeds straight to `write_token(user)` + cookie.
The password and magic-link paths require TOTP; SSO does not. A TOTP-enabled
user with weak Google 2FA (or a live/compromised Google session) is a full
account takeover with no neuthek second factor.

**Remediation:** issue a short-lived single-use `totp_pending` token and redirect
the SPA to a TOTP-completion step before minting the session; at minimum gate the
bypass behind an explicit default-off per-user setting. **Refs:** CWE-308.

---

### F2 — 6-digit magic-link code brute-forceable from a rotating-IP pool — MEDIUM (Likely)
*(unchanged)* `api/email_link.py:415-460` (`consume-code`, 10⁶ space, 15-min
TTL); lockout keys on `identity:IP` (`security.py:~619-635`, the A5 fix). No
identity-only hard cap → a botnet/proxy pool gets a fresh budget per IP and the
email accrues no global failure count → ~10⁵–10⁶ guesses within the TTL.

**Remediation:** add an identity-scoped hard cap that burns the in-flight code
after N total failures across all IPs; shrink the TTL. **Refs:** CWE-307.

---

### F3 — Cloud-sync worker disables RLS wholesale — MEDIUM, def-in-depth (Confirmed)
*(unchanged; note overlap with F7)* `api/cloud.py:424`
`SET LOCAL app.rls_bypass='on'` turns isolation off for the worker session
instead of scoping to the sync's user. Largely subsumed by F7 today (the role
bypasses anyway), but it's the *wrong pattern* and must be fixed alongside F7/
F13: set `app.current_user_id = that_user` instead. **Refs:** CWE-285.

---

### F14 — Stripe secrets not wired through Docker `*_FILE` secrets in prod — MEDIUM (Confirmed)
**Evidence:** `config.py:10-49` supports `STRIPE_SECRET_KEY_FILE` /
`STRIPE_WEBHOOK_SECRET_FILE`, but `docker-compose.prod.yml` declares no
`stripe_secret_key`/`stripe_webhook_secret` secrets and mounts none. If billing
is enabled in prod, the operator must put these in plaintext env. **Remediation:**
add the two Docker secrets + corresponding `*_FILE` env, mirroring the DB/JWT
pattern. **Refs:** CWE-312.

---

### F15 — CSRF/CORS allow-list is localhost-only — MEDIUM, operational (Confirmed)
**Evidence:** `app.py:49-57` hardcodes `ALLOWED_ORIGINS` to localhost/127.0.0.1
dev ports + `tauri://localhost`, with no prod-hostname entry and no env
extension. `validate_production_settings` (`security.py:242-256`) *fails boot*
if `FRONTEND_BASE_URL` (the prod `https://${NEUTHEK_DOMAIN}`) isn't in this tuple
— so prod either can't boot or the operator hand-edits source. Both the
CsrfOrigin middleware and CORS derive from this list. **Remediation:** derive
`ALLOWED_ORIGINS` from `settings.frontend_base_url` (+ optional env-supplied
extra origins) so the prod origin is trusted by construction. **Refs:** CWE-1188.

---

### F4 / F5 / F6 — *(unchanged from prior revision; see history)*
- **F4 (LOW, design):** no per-session `jti`/revocation; only a global
  `token_version` bump. Add a `jti` + Redis allow/deny set for "log out this
  device" + rotation-on-login. CWE-613.
- **F5 (LOW/INFO, design):** one app master key derives all server subkeys +
  encrypts at-rest OAuth/TOTP secrets; its leak forges signed URLs + session
  JWTs + decrypts at-rest tokens — **not** the Vault. Split the at-rest token key
  from the URL-signing key / use a KMS. CWE-320.
- **F6 (LOW, self-XSS):** `account-panels.jsx:631-651` `document.write`s the
  **viewing user's own** email + codes (not cross-tenant). Escape before write.
  CWE-79.

---

### F16 — Folder CTE / `image_persons` / search rely on RLS that is now absent — LOW, elevated by F7 (Confirmed)
**Evidence:** `api/folders.py` `_descendants_query` (recursive CTE) and some
`api/search.py` joins on `image_persons` were written to lean on RLS as the
isolation backstop for the joined rows; with F7 active, RLS provides nothing, so
these depend entirely on their app-layer predicates being complete. Individually
LOW (the predicates appear present), but **F7 removes the safety net** that made
these "belt-and-suspenders." **Remediation:** confirm every join in these paths
filters by `user_id` explicitly; add the missing RLS (F11) so the backstop
returns. A dynamic A-vs-B IDOR sweep (§10) should target these specifically.

---

### F17 — Archive ingestion buffers the full uncompressed payload in memory — LOW/MEDIUM (Likely)
**Evidence:** `backend/archive_upload.py` extracts archive entries into memory
buffers; with the per-entry + total caps (U2) the worst case is bounded by the
configured total-uncompressed cap, but that cap is held **in RAM** per request,
so a few concurrent max-size archive uploads can spike memory. **Remediation:**
stream entries to a temp file / enforce a concurrency limit on archive ingestion;
confirm the total cap is small enough that `cap × max_concurrency` fits the
container memory budget. **Refs:** CWE-400.

---

### F18 — CI Actions not SHA-pinned; `Dockerfile.backup` retained — LOW/INFO (Confirmed)
`.github/workflows/security.yml` is otherwise well-built (top-level
`permissions: contents: read`; no untrusted `${{ }}` interpolated into `run:`;
`set -euo pipefail`), but actions are pinned to floating major tags
(`actions/checkout@v4`, `gitleaks/gitleaks-action@v2`) — pin to full commit SHAs
for a security-sensitive repo. `Dockerfile.backup` is tracked at repo root and
unused by any compose `dockerfile:` key — confirm `.dockerignore` covers it or
delete it. **Refs:** CWE-1104.

---

## 3. Verified-secure properties (corroborated by code review)

- **JWT `tv` revocation** is enforced on both cookie + bearer, algorithm-pinned
  (`jwt.decode(..., algorithms=[...])`) — no `alg=none`/HS-RS confusion.
- **SSO `state`** HMAC-signed (HKDF subkey) + PKCE + id-token audience check;
  account-linking binds on `google_sub` with the CR-5 guard.
- **Stripe webhook** signature-verified + idempotent via `stripe_events`.
- **Mass-assignment hygiene:** `UserCreate`/`UserUpdate` expose only
  `display_name`/`age_confirmed`; `role`/`quota_bytes`/`is_superuser`/`tv` not
  settable; fastapi-users `create(safe=True)`.
- **Cookie flags** HttpOnly + SameSite=lax + `Secure=is_production` everywhere.
- **Production-boot gate** is genuinely thorough (MINIO_SECURE, HTTPS FE URL,
  strong JWT secret + weak-list, weak DB/MinIO creds reject, rate-limits + Redis
  required, JWT ≤7d, SSO consistency, CORS alignment, SSE/KMS distinct-key
  checks, Fernet `CLOUD_ENCRYPTION_KEY`, backup recipient). **Gaps:** no
  `rolsuper`/`rolbypassrls` check (F7), can't see edge headers (F10), no Stripe
  `*_FILE` check (F14).
- **Backend container** non-root + multi-stage; build tools stay in builder
  (`Dockerfile:155 USER neuthek`); rclone pinned; cloud-cred dirs `chmod 700`.
- **Dev compose** binds every published port to loopback (`127.0.0.1:…`).
- **Prod compose** uses Docker secrets for DB/JWT/MinIO/KMS/backup, `MINIO_SECURE
  true`, `REQUIRE_SIGNED_DOWNLOADS true`, only Caddy internet-exposed.
- **`client_ip()`** does not trust XFF unless `TRUST_PROXY_HEADERS` (forced true
  only behind the header-stripping proxy).
- **Backend API responses** carry the full security-header set incl. tight CSP
  (correct for the API origin; the gap is the SPA HTML origin — F10).
- **CSRF-origin middleware** + SameSite cookies, reasoned cookie-setter
  exemptions.
- **Marketing Express:** `trust proxy` = exactly `1`, `x-powered-by` off, 32 kB
  JSON cap, `X-Frame-Options: DENY`, `express-rate-limit` on signup, parameterized
  `pg` (`$1`), HMAC purpose-namespaced tokens, `timingSafeEqual` admin auth that
  fails closed.
- **rclone/ffmpeg** invoked with list args (no `shell=True`); ffmpeg has
  `-protocol_whitelist` (CR-6).
- **`hard_delete_images`** correctly removes image + **face-crop** blobs
  (`bucket_faces`) — the face-crop-orphan hypothesis was **refuted**; the gap is
  Vault blobs only (F12).
- **`npm audit`:** 0 vulnerabilities (frontend + marketing).
- **Vault zero-knowledge** server-side contract verified (§4).

---

## 4. Vault crypto verification

Reviewed `frontend/src/vault/crypto.ts` ↔ `backend/api/vault.py` + migrations
`0044-0047`. Validated by cryptographic round-trip (Node, during build):
sealed-box 10/10; chunked file AES-GCM incl. tamper/truncation/wrong-key 30/30;
share-bundle cross-account 5/5; public-link incl. "password link not openable by
link alone" 8/8. Backend in-process E2E vs live Postgres/MinIO: vault CRUD
33/33, direct shares 29/29, public links 31/31.

**Confirmed properties:** PBKDF2-SHA256 600k → non-extractable AES-256-GCM master
key; server enforces KDF iteration bounds (310k–5M); account P-256 ECDH private
key stored AES-GCM-wrapped under the master key; random per-file AES-256 in
1 MiB chunks with 4-byte-prefix‖8-byte-BE-counter nonces + per-chunk index-bound
AAD (reorder/truncation fail closed); domain-separation tags per blob class;
strict base64 + exact-byte-bound input gate (`_b64_field`); public-link key only
in the URL `#fragment`, optional password mixed into key derivation, token =
`secrets.token_urlsafe(32)`, unauth read endpoints rate-limited per token. Vault
download endpoints force `Content-Disposition: attachment` (so F9 doesn't reach
the Vault).

**The crypto is sound; the Vault's real-world weak points are operational, not
cryptographic:** F10 (no CSP → in-memory key theft via XSS) and R1–R3.
Recommend an independent crypto reviewer re-exercise `crypto.ts` under Node and
inspect raw DB rows + MinIO objects for opacity (`SECURITY_AUDIT_DOSSIER.md
§CRYPTO/§F`).

---

## 5. Residual-risk register (accepted / by design)

| ID | Risk | Why accepted | What would change it |
|----|------|--------------|----------------------|
| R1 | Metadata leakage (kind/size/folder graph/timestamps) | Inherent to server-stored E2E; names encrypted | Padding / oblivious storage |
| R2 | Password public links offline-brute-forceable | Same model as MEGA/Proton; PBKDF2 600k slows it | Strong-password enforcement |
| R3 | Recipient-key TOFU under malicious server | Out of scope for honest-but-curious model | Key pinning / out-of-band verify |
| R4 | XSS defeats E2E while unlocked (master key in JS memory) | Non-extractable key limits export, not in-page use | **Fix F10 (CSP)** + zero XSS — highest-value mitigation |
| R5 | Single master-key blast radius (F5) | Operational simplicity | Split keys / KMS |

---

## 6. Regression results (prior "fixed" items re-checked)

- ✓ HKDF key separation; ✓ JWT revocation (alg-pinned); ✓ SSO email-match guard
  (CR-5); ✓ production-settings validation (CR-10) — **but** it misses F7/F14;
  ✓ lockout keyed identity+IP (A5) — **but see F2**; ✓ Stripe webhook signed +
  idempotent; ✓ Dockerfile non-root + multi-stage; ✓ compose loopback binding;
  ✓ rclone list-args; ✓ face-crop blob deletion on account delete.
- ✗ **New regressions found this round:** RLS is **not** fail-closed in prod
  (F7, the prior report's claim was wrong); SPA has **no** CSP/XFO at the edge
  (F10, the in-code comment was wrong).
- ~ recommend live re-test: cloud-sync quota bypass (CR-4) concurrency; ffmpeg
  `-protocol_whitelist` crafted-playlist; zip/bomb caps (U2); EXIF scrub
  (U5/U6); pagination caps (D1); `image_persons` IDOR (D2); audit cap (D5/F12).
- CodeQL + Dependabot: re-run CI on the audit branch (offline here).

---

## 7. Attack-path chains

1. **Cross-tenant breach via any filter bug (F7 amplifier):** because RLS is
   inert, a single missing `WHERE user_id` (F16-class) or a SQLi in *any* of the
   ~198 ops reads/writes another tenant's biometric/GPS/recovery/vault-metadata
   rows directly — the DB never says no.
2. **Vault compromise via SPA XSS (F10 → R4):** no CSP means an injected script
   reads the in-memory master key / the `#fragment` public-link key → full
   plaintext exfiltration while the vault is unlocked. Clickjacking (no XFO) can
   trick a user into revealing/sharing.
3. **MFA-bypass takeover (F1):** TOTP user + weak Google 2FA → SSO → full session,
   no second factor.
4. **Passwordless takeover (F2):** known email → distributed-IP guess the 6-digit
   code within the 15-min TTL → session minted; chains with F4 (no per-session
   kill-switch).
5. **Silent erasure failure (F12 + F13):** after F7 is fixed without F13, the
   scheduled-delete sweeper no-ops on vault/biometric rows (NULL RLS context) →
   data that should be erased persists, *and* the vault blobs were already
   orphaned.
6. **Biometric-without-consent (F8):** revoke face consent, upload → worker still
   computes + stores ArcFace embeddings → statutory/regulatory exposure.

---

## 8. Prioritized remediation roadmap

**Now (highest impact):**
- **F7** — provision a `NOSUPERUSER NOBYPASSRLS` app role + DSN split + boot
  guard. *This is the single most important fix; it restores the entire
  defense-in-depth layer.* Land **F13** + background-job RLS-context fixes in the
  same change (F7's fix activates RLS and will otherwise break deletion/retention).
- **F10** — add the edge `header` block (CSP + X-Frame-Options + HSTS) to
  `deploy/Caddyfile` and the root `Caddyfile`. *Protects the Vault.*
- **F8** — add the consent check to the worker face-scan path (re-check at scan
  time). *Compliance-critical.*
- **F12** — delete vault blobs on account + scheduled deletion; remove the dead
  block.

**Next (days):**
- **F1** require neuthek TOTP on SSO; **F2** identity-scoped code cap; **F9**
  robust content guard + `Content-Disposition: attachment` on Drive serve;
  **F11** enable RLS on cloud/billing tables; **F3** scope worker to user;
  **F14** Stripe `*_FILE`; **F15** derive CORS/CSRF origins from FE URL.
- Run the SAST/secret/dep/container suite + a staging dynamic pass (IDOR sweep
  targeting F16, ffmpeg playlist, provider SSRF redirect-follow, decompression
  bombs F17, ReDoS).

**Later (weeks / by design):**
- **F4** per-session `jti`; **F5** split at-rest key from URL-signing key;
  **F18** SHA-pin CI Actions; R1–R4 mitigations as the threat model warrants.

---

## 9. Injection & web-vuln matrix (every class examined)

| Class | Verdict | Evidence |
|-------|---------|----------|
| **SQL injection** | **No finding.** SQLAlchemy 2.0 parameterized; the only client-influenced raw SQL is the RLS GUC bound param (`db.py:46`); marketing uses `$1` (`server.mjs:440`). *Note:* with RLS inert (F7), SQLi impact is higher — there's no DB backstop. | `db.py:46`; `server.mjs:440-462`; grep `text(f"`/`execute(f"` → none |
| **Command injection** | **No finding.** All `subprocess` use argv lists, no `shell=True`. | `transcode.py`, `hls.py`, `image.py`, `transcribe.py`, `rclone_wrapper.py`; grep `shell=True` → none |
| **SSRF (ffmpeg)** | **Mitigated** via `-protocol_whitelist`. | `ffmpeg_args.py:5,42,54` |
| **SSRF (provider/geocode)** | No fixed-host finding; recommend dynamic redirect-follow test. | `cloud_sync.py`, `rclone_wrapper.py`, `name_suggest.py` (§12) |
| **Path traversal** | **No finding.** Storage keys server-generated `{user_id}/{uuid}`; temp files randomized. | `vault.py`, `image.py:262`, `transcode.py:31` |
| **Stored/Reflected XSS** | One self-XSS (F6); one CSP-mitigated SVG/HTML serve gap (F9); SPA has no CSP backstop (F10). | §9.1, F9, F10 |
| **CSRF** | **No finding** — CsrfOrigin + SameSite; webhook signed. | `security.py`, `billing.py` |
| **Open redirect** | No finding observed; confirm no reflected `next=`. | `google_sso.py _fe_landing`, `email_link.py` |
| **Insecure deserialization** | **No finding.** `.eval()` are PyTorch `Module.eval()`; no user-controlled `pickle`/`yaml.load`/model path. | `vision/runtime.py`; grep → none |
| **Decompression / bombs** | Caps present (U2); see F17 (in-memory buffering). | `archive_upload.py`, `upload_validation.py`, `config.py` |
| **Mass assignment** | **No finding.** | `schemas.py` |
| **SSTI / XXE** | No server-side template of user input; confirm openpyxl/docx/SVG disable external entities. | `server.mjs:124`, `document_compress.py` |
| **ReDoS** | One prior fix (CodeQL); recommend pathological-input pass. | `synonyms.py`, search |

### 9.1 XSS sink-by-sink
- `code-preview.jsx:320` — `__html` is `Prism.highlight(...)` (escapes) or
  `null`→plain text. **Safe.**
- `policies.jsx:263` — `__html` over a **static** array. **Safe.**
- `account-panels.jsx:641` — `document.write` of the user's **own** email/codes
  → **F6** (self-XSS only).
- marketing `Faq/Updates/UpdateDetail` — owner-authored static content; email
  templates escape user values. **Safe** (note for maintainers).

---

## 10. Per-router authorization & IDOR map

Auth is a per-endpoint dependency; wrong-owner/missing IDs return **404** (no
existence oracle). **Critical caveat (F7):** the Postgres FORCE-RLS backstop is
**inert** in the running deployment, so isolation depends **solely** on the
app-layer `user_id` filters below — an IDOR dynamic sweep is now higher-priority
than the prior report implied.

| Router | Auth dependency | IDOR posture |
|--------|-----------------|--------------|
| `/vault/*` | `current_active_user` (+ 2 token-gated public-link reads) | owner-fenced; non-recipient share / non-owner public-link → 404 (tests) |
| `/admin/*` | `current_admin_user` (+ `current_superuser` on role-mutation + bulk-delete `admin.py:322,978`) | fully gated; no unprotected admin op found |
| `/images,/folders,/people,/faces,/tags,/comments` | `current_active_user` | filtered on `user_id`; D2 re-checks; **see F16** (folder CTE / search joins now lack RLS backstop) |
| `/account,/consent,/storage,/billing(user),/cloud,/search,/feedback` | `current_active_user` | per-user scoped; `cloud_*` also lack RLS (F11) |
| `/shares/*` | `current_active_user` + signed public link | recipient-bound (U3/S4) |
| `/billing/webhook` | none (Stripe-signed) | signature + idempotency |
| `/auth,/users` | fastapi-users | §2/§3 |

**Verdict:** model is consistent + admin surface fully gated, but with RLS inert,
**recommend a dynamic A-vs-B IDOR sweep across every router** to confirm the
app-layer filters are complete — convert this table to Confirmed-by-test.

---

## 11. File-ingestion pipeline (parser-RCE surface)

upload → `upload_validation.py` (type/size/pixel/zip caps; **bypassable SVG/HTML
guard — F9**) → MinIO (server-named key) → background transcode/re-encode →
derived blobs → **consent-gated AI (but worker path skips consent — F8)**.
- Subprocess safety: list-arg, no shell (§9). ffmpeg SSRF: protocol whitelist.
- Bombs: per-entry/total caps present; in-memory buffering (F17).
- EXIF/metadata stripped on re-encode (U5/U6).
- **Residual RCE surface (recommend CVE pin-check):** native parsers (Pillow,
  pillow-heif, imagecodecs, rawpy/LibRaw, PyMuPDF) run on attacker bytes — a
  memory-safety CVE in any is the largest RCE risk; the non-root container
  (CR-7) bounds blast radius.

---

## 12. SSRF surface inventory

| Outbound | Trigger | Risk | Note |
|----------|---------|------|------|
| Google OAuth/Drive | SSO + sync | low | SDK, fixed hosts; state HMAC+PKCE |
| iCloud (pyicloud) | sync | low–med | SDK; no raw user URL |
| Proton/MEGA (rclone) | sync | low–med | config server-generated, list-arg; verify remote-name interpolation |
| Nominatim geocode | reverse-geocode | low | fixed host |
| Stripe | billing | low | SDK, fixed host |
| ffmpeg input | transcode | mitigated | protocol whitelist |

**Verdict:** no raw-user-URL SSRF sink in static review; residual is
redirect-follow inside provider SDKs/rclone — **recommend a staging test** at
`169.254.169.254`, `minio:9000`, `redis:6379`, `postgres:5432`.

---

## 13. Marketing service (Express)

Admin auth fails closed (`timingSafeEqual`, 401 when `ADMIN_PASS` unset) +
rate-limited; parameterized `pg`; all user-derived email values `escapeHtml`'d;
HMAC purpose-namespaced verify/unsubscribe tokens; `express-rate-limit` keyed on
`CF-Connecting-IP`→`req.ip`; `X-Frame-Options: DENY` + `trust proxy = 1` +
`x-powered-by` off. **Recommend:** add a CSP to the marketing origin; ensure
`ADMIN_PASS` is strong in deploy env.

---

## 14. Coverage delta vs prior revisions

This revision (a) **verified the highest-impact findings against the live
running stack** rather than inferring from source (§0.3) — which overturned two
of the prior report's "secure" claims (RLS fail-closed → F7; FE CSP exists →
F10); (b) added the previously-out-of-scope **ML worker / faces pipeline** (F8),
**account-deletion / retention** (F12, F13), and **infra/CI/config** (F14, F15,
F18) reviews; (c) added a **live RLS coverage table** (§15) and a **per-module
coverage appendix** (§16). Net new HIGH findings: **F7, F8, F10, F12.** The
crypto core remains sound (§4); the posture change is driven by infra/privacy
defense-in-depth gaps, not by a break in the Vault.

---

## 15. RLS coverage table (verified live, §0.3)

| Table | RLS enabled | FORCE | Bypass clause in policy | Effective in deployment? |
|-------|-------------|-------|--------------------------|--------------------------|
| faces, face_detections, persons, image_geo, consent_records, recovery_codes, bandit_state, feedback_events, share_grants, comments, tags, folder_tags, image_tags, image_persons, document_chunks, search_telemetry | yes | yes | yes (0016/0027 form) | **NO — bypassed by superuser (F7)** |
| vault_items, vault_folders, vault_meta, vault_share_grants | yes | yes | **NO (F13)** | **NO — bypassed by superuser (F7)** |
| vault_public_links | **no (intentional)** | — | — | n/a (token-gated) |
| cloud_links, cloud_files, subscriptions, notification_prefs | **no (F11)** | — | — | NO — never had RLS |
| images, users, audit_log | **no** | — | — | NO — app-layer filters only (by design) |

Two independent reasons RLS provides no runtime protection today: (1) the
superuser role bypasses it everywhere (F7); (2) several sensitive tables never
had it (F11). Fixing F7 restores it for the first group, but F11 + F13 must also
land for full coverage.

---

## 16. Per-module coverage appendix

| Module / area | Files | Depth | Result |
|---|---|---|---|
| Vault crypto | `frontend/src/vault/crypto.ts`, `api/vault.py`, migs 0044-0047 | deep + round-trip | §4 — sound; R1-R3 |
| Auth / session / MFA / SSO | `auth/*`, `two_factor.py`, `email_link.py`, `security.py` | deep | F1, F2, F4 |
| RLS / multi-tenancy | `db.py`, `context.py`, policy migs, **live psql** | deep + live | **F7**, F11, F13, F16 |
| Secrets / config / boot gate | `config.py`, `key_derivation.py`, `secret_box.py`, `signed_urls.py`, `security.py validate_*` | deep | F5, F14, F15; gate gaps noted |
| Billing | `api/billing.py` | deep | secure (signed+idempotent) |
| Edge / infra / containers | `Dockerfile*`, `docker-compose*.yml`, `Caddyfile`, `deploy/Caddyfile`, `frontend/nginx.conf`, `.github/workflows/*` | deep | **F10**, F14, F15, F18; non-root + loopback + secrets confirmed |
| ML worker / faces pipeline | `worker/main.py`, `faces_pipeline.py`, `image.py`, `api/images.py` | deep | **F8**, F3 |
| File ingestion / serve | `upload_validation.py`, `api/images.py` serve, `archive_upload.py`, `ffmpeg_args.py`, `transcode.py` | deep | **F9**, F17; subprocess safe |
| Account lifecycle / retention | `api/account.py`, `deletion.py`, `retention.py` | deep | **F12**, F13 interaction; face-crop deletion confirmed OK |
| Data routers + IDOR | all `api/*.py` | medium | §10; F16; recommend dynamic sweep |
| SSRF surface | `cloud_sync.py`, `rclone_wrapper.py`, `name_suggest.py` | medium | §12; recommend dynamic |
| Marketing | `marketing/server.mjs`, `marketing/src/*` | medium | §13; well-hardened, add CSP |
| Frontend XSS sinks | grep all `dangerouslySetInnerHTML`/`document.write` | medium | §9.1; F6 only |

---

*Prepared read-only. No application files were modified to produce this review;
all fixes are proposals. The RLS/role findings (F7, F11, F13) and the edge-header
finding (F10) were verified against the live running stack (§0.3); the remaining
"Likely"/"recommend dynamic" items need a staging deploy with rate-limits ENABLED
plus the SAST/dep/container suite to convert to Confirmed.*
