# neuthek — Security Review

**Type:** authorized, owner-requested, end-to-end review (read-only — no code
changed; fixes proposed as diffs only).
**Tree audited:** `main` at repo root (FastAPI API + React SPA + Express
marketing; migrations through `0047_vault_public_links`).
**Date:** 2026-05. **Companion:** see `SECURITY_AUDIT_DOSSIER.md` for the full
architecture/threat-model + offensive playbook.

> This review is evidence-driven and skeptical of the code's own claims. The
> project's "remediated" notes were treated as hypotheses; §6 reports which
> were confirmed. Coverage is stated honestly in §0.1 — not every one of the
> ~50 modules was line-audited; the weighted-priority areas were.

---

## 0. Executive summary

**Overall posture: strong for a product at this stage.** The hard parts are
done right: the zero-knowledge Vault’s server side is genuinely ciphertext-only;
JWT revocation is correctly alg-pinned (no `alg=none`); the Stripe webhook is
signature-verified + idempotent; OAuth `state` is HMAC+PKCE-protected with an
id-token audience check; the production-boot gate refuses dev defaults; RLS is
server-derived and fail-closed in production; `npm audit` is clean on both JS
surfaces.

**The five issues that matter most:**

1. **F1 — Google SSO silently skips neuthek TOTP** (MEDIUM). A user who enabled
   TOTP is single-factor on the SSO path; the design leans entirely on Google’s
   own 2FA. (`backend/auth/google_sso.py:~747`)
2. **F2 — The 6-digit magic-link code is brute-forceable from a rotating-IP
   pool** (MEDIUM). The lockout keys on `identity:IP`, so a fixed target email’s
   10⁶ code space has no identity-only hard cap. (`backend/api/email_link.py`,
   `backend/security.py:~633`)
3. **F3 — The cloud-sync worker disables RLS wholesale** (`app.rls_bypass='on'`)
   rather than scoping to the sync’s user — the tenant-isolation safety net is
   off for that session. (`backend/api/cloud.py:424`)
4. **F4 — No per-session revocation** — only a global `token_version` bump; you
   can’t kill one stolen session without logging everyone out. (design)
5. **F5 — Single app master key** derives all server subkeys; its leak forges
   signed URLs + decrypts at-rest OAuth/TOTP secrets (NOT the Vault). (design,
   blast-radius note)

**Does the zero-knowledge Vault claim hold?** **Yes, for the stated
honest-but-curious-server model**, based on code review + cryptographic
round-trip validation (§4). The server stores/returns only opaque
nonce‖ciphertext / sealed bundles and never receives the master password, a
per-file key, or the unwrapped account private key. The real caveats are *by
design* and belong in the threat model (§5): metadata leakage (item kind, file
sizes, folder graph), recipient-key trust-on-first-use under a *malicious*
server, offline brute-forceability of password-protected public links, and the
fact that **any XSS in the SPA defeats E2E while the vault is unlocked** (the
master key is in JS memory) — so SPA XSS findings are rated CRITICAL.

### 0.1 Coverage & method (be honest about what was done)
- **Deep manual review:** Vault crypto (client `crypto.ts` ↔ server `vault.py`
  + migrations), auth/session/MFA/SSO (`security.py`, `auth/*`, `two_factor.py`,
  `email_link.py`), RLS/multi-tenancy (`db.py`, `context.py`, policy
  migrations), secrets/config (`config.py`, `key_derivation.py`,
  `secret_box.py`, `signed_urls.py`), billing webhook (`billing.py`), edge/infra
  (`Dockerfile`, `docker-compose*`, `Caddyfile`, `deploy/Caddyfile`).
- **Lighter review (controls confirmed present; recommend dynamic + SAST):**
  file-ingestion pipeline (`images.py`, `upload_validation.py`, `ffmpeg_args.py`,
  `archive_upload.py`), SSRF surfaces (`cloud_sync.py`, `rclone_wrapper.py`,
  geocoder), marketing Express service.
- **Tools:** `npm audit` (frontend + marketing) → **0 vulnerabilities**. SAST
  (semgrep/bandit/pip-audit/gitleaks/trivy) were **not installed in this
  environment and were NOT run** — the report relies on manual review (which the
  brief prefers) plus the repo’s CI (CodeQL backend/frontend/marketing +
  Dependabot). **Action for the team: run the SAST/secret/dep/container
  scanners listed in `SECURITY_AUDIT_DOSSIER.md §I` on the audit branch.**
- **No dynamic testing** was performed against a live deploy (none provided);
  exploitation walkthroughs below are static-analysis-derived and marked with
  confidence.

---

## 0.2 Recon — trust-boundary map

```
Browser ──(HttpOnly neuthek_session cookie)──► Caddy (TLS/HSTS/headers, CF-IP trust)
   │                                              │
   ├─ SPA (React, no token in JS)            FastAPI/uvicorn ── 18 routers / ~198 ops
   │                                              │  per-endpoint deps: current_active_user /
   │                                              │  current_admin_user / current_superuser
   │                                              │  + middleware: CsrfOrigin, SecurityControls
   │                                              │    (rate-limit/lockout), SecurityHeaders
   ▼                                              ▼
  /v/{token} public viewer                Postgres (FORCE RLS, GUC app.current_user_id)
  (SPA route, key in #fragment)           Redis (lockout+rate counters, job queue)
                                          MinIO (server-named object keys)
                                          ML worker (Redis BLPOP; RLS-bypassed session — F3)
                                              │ outbound (SSRF-relevant):
                                   Google OAuth · iCloud (pyicloud) · Proton/MEGA (rclone) ·
                                   Nominatim geocode · Stripe
```
**GUC integrity:** `app.current_user_id` is set in `db.py:_set_rls_context`
from a request-scoped ContextVar populated by the auth dependency
(`auth/users.py` `set_current_user_id(user.id)`) — i.e. **server-derived from
the validated JWT, never from client input.** In production with no user in
context, *neither* GUC is set → RLS compares against NULL → **0 rows
(fail-closed)**. `app.rls_bypass='on'` is auto-set only in `dev/test/local`
(`db.py:50-51`) — except the worker path (F3).

---

## 1. Findings table

| ID | Title | Severity (CVSS 3.1) | Confidence | Location | CWE |
|----|-------|--------------------|-----------|----------|-----|
| F1 | Google SSO bypasses user’s neuthek TOTP | MEDIUM — 6.3 `AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:H/A:N` | Confirmed | `backend/auth/google_sso.py:~745-755` | CWE-308/CWE-287 |
| F2 | 6-digit magic-link code brute-forceable via rotating IPs | MEDIUM — 6.5 `AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N` | Likely | `backend/api/email_link.py:415-460`, `backend/security.py:~619-635` | CWE-307 |
| F3 | Cloud-sync worker disables RLS wholesale (`app.rls_bypass`) | MEDIUM — 5.3 (def-in-depth) `AV:N/AC:H/PR:L/S:U/C:L/I:L/A:N` | Confirmed (design) | `backend/api/cloud.py:424` | CWE-285 |
| F4 | No per-session JWT revocation (global `tv` bump only) | LOW — 3.5 `AV:N/AC:H/PR:L/C:L/I:N/A:N` | Confirmed (design) | `backend/auth/users.py` (`token_version`) | CWE-613 |
| F5 | Single app master key — broad blast radius | LOW/INFO — n/a (design) | Confirmed (design) | `backend/key_derivation.py`, `backend/secret_box.py` | CWE-320 |
| R1 | Vault metadata leakage (kind/size/folder graph) | INFO (by design) | Confirmed | `backend/api/vault.py` | CWE-200 |
| R2 | Password public links offline-brute-forceable | INFO (by design) | Confirmed | `frontend/src/vault/crypto.ts` | CWE-307 |
| R3 | Recipient-key TOFU under malicious server | INFO (by design) | Confirmed | share flow | CWE-295 |

---

## 2. Per-finding detail

### F1 — Google SSO bypasses the user’s neuthek TOTP — MEDIUM (Confirmed)
**Location:** `backend/auth/google_sso.py` (the OAuth callback, ~lines 745-755):
the branch `if user.totp_enabled and user.totp_secret_enc:` writes an audit row
`action="auth.sso.bypass_totp"` and then proceeds to
`token = await strategy.write_token(user)` + sets the session cookie. The
password path (`users.py` `on_after_login`) and the magic-link path
(`email_link.py`) both *require* TOTP; the SSO path explicitly does not.

**Exploitation (Confirmed by code; preconditions realistic):** A victim enables
neuthek TOTP (believing it protects the account) but their Google account has
weak/no 2FA, or an attacker has a live Google session on a shared/compromised
device, or Google creds were credential-stuffed. The attacker clicks “Sign in
with Google” → neuthek mints a full session JWT with **no neuthek second
factor**. The user has no way to require neuthek-TOTP on the SSO path.

**Impact:** Full account takeover that bypasses the user’s configured MFA →
read/modify all Drive content; the Vault still needs the master password, but
account-level settings, sharing, billing, and Drive data are exposed.

**Remediation (propose; do not apply):** Make SSO honor neuthek TOTP, default
-secure. When `user.totp_enabled`, issue a short-lived `totp_pending` token
bound to `user.id` and redirect the SPA to a TOTP-completion step instead of
minting the session:
```python
# google_sso.py — replace the bypass branch
if user.totp_enabled and user.totp_secret_enc:
    pending = mint_totp_pending_token(user.id, ttl=300)  # signed, single-use
    return RedirectResponse(_fe_landing({"sso_totp": pending}), status_code=302)
# SPA collects the 6-digit code → POST /auth/totp/complete-sso {pending, code}
# → verifies TOTP, then write_token(user) + set cookie.
```
At minimum, gate the current behavior behind an explicit, default-off
`allow_sso_totp_bypass` per-user setting so the user opts into the weaker flow.
**Refs:** CWE-308 (use of single-factor where MFA expected), OWASP ASVS 2.2.

---

### F2 — 6-digit magic-link code brute-forceable from a rotating-IP pool — MEDIUM (Likely)
**Location:** `backend/api/email_link.py:415-460` (`POST /auth/email-link/
consume-code` takes `{email, code}`, 10⁶ space, 15-min TTL); lockout in
`backend/security.py:~619-635` (`lock_scope = f"{identity}:{ip}"`, A5 fix). The
code’s own comments (`email_link.py:139-142, 432-434`) state it relies on the
per-IP `_AUTH_PATHS` lockout — there is **no identity-only hard attempt cap.**

**Exploitation (Likely; bounded by code, needs a proxy pool to realize):** The
A5 fix deliberately keyed the lock on `identity:IP` to avoid a victim-lockout
DoS — correct for password login, but it means a fixed target email accrues no
global failure count. An attacker guessing the 6-digit code for a known email
from many IPs (botnet/proxy pool) gets a fresh 5-attempt/60s budget *per IP* and
the `identity:IP` lock never trips for the email. Within the 15-min TTL,
~10⁵–10⁶ attempts (≈10–100 % hit) is feasible with a moderate pool.

**Impact:** Passwordless account takeover of a targeted email. (Other small
-keyspace paths are fine: TOTP requires a valid password first + `valid_window=1`;
recovery codes are ~50-bit; reset/verify are signed JWTs.)

**Remediation (propose):** Add an **identity-scoped** hard cap independent of
the IP-scoped DoS-safe lock — invalidate the in-flight code after N total
failures across all IPs:
```python
# security.py (for /auth/email-link/consume-code, /totp, recovery paths)
ident_fail = await increment_window(f"auth:identfail:{path}:{identity}", 900)
if ident_fail >= 10:
    await clear_counter(_code_key(identity))   # burn the code; force re-request
    raise HTTPException(423, "Too many attempts — request a new code.")
```
Also consider shrinking the code TTL (e.g. 5 min) and/or making each wrong
guess consume one of a small fixed budget tied to the code itself.
**Refs:** CWE-307, OWASP ASVS 2.2.1.

---

### F3 — Cloud-sync worker disables RLS wholesale — MEDIUM, defense-in-depth (Confirmed)
**Location:** `backend/api/cloud.py:424` —
`await s.execute(sql_text("SET LOCAL app.rls_bypass='on'"))`. The RLS policies
(migrations `0016`, `0027`, `0028`, `0032`, `0034`) are
`USING (user_id = current_setting('app.current_user_id', true)::uuid OR
current_setting('app.rls_bypass', true) = 'on')`, so this turns isolation **off
for the whole worker DB session**.

**Exploitation (no known active exploit; it’s a removed safety net):** The
worker already knows the `user_id` it is syncing for, but instead of setting
`app.current_user_id = that_user`, it flips the global bypass. If any query in
the sync path (now or after a future refactor) fails to filter by `user_id`,
RLS will not catch the cross-tenant read/write — the last line of defense is
gone precisely in the long-running, multi-thousand-row code path.

**Impact:** Latent cross-tenant data exposure/corruption if a worker query is
ever mis-scoped. Confidentiality + integrity, cross-tenant.

**Remediation (propose):** Scope the worker to its user instead of bypassing:
```python
async with session_factory() as s:
    await s.execute(text("SELECT set_config('app.current_user_id', :uid, true)"),
                    {"uid": str(user_id)})
    # RLS now fences the worker to exactly this user — same as a request.
```
Reserve `app.rls_bypass` for genuinely cross-user maintenance (and even then,
prefer a dedicated DB role with explicit, audited scope). **Refs:** CWE-285.

---

### F4 — No per-session JWT revocation — LOW, design (Confirmed)
**Location:** `backend/auth/users.py` — the session JWT carries `tv`
(token_version); revocation is a *global* `token_version` increment (on password
reset + 2FA disable). There is no per-session `jti`/allow-list.

**Impact:** A user who suspects one device is compromised cannot kill just that
session — the only lever logs out every device. Also, two logins at the same
`tv` mint interchangeable tokens (no session rotation on login).

**Remediation (propose):** Add a `jti` to the session JWT + a Redis allow/deny
set, enabling “log out this device” and rotation-on-login. Lower priority; the
HttpOnly+SameSite cookie + CSRF-origin middleware already bound the practical
risk. **Refs:** CWE-613, CWE-384.

---

### F5 — Single app master key blast radius — LOW/INFO, design (Confirmed)
**Location:** `backend/key_derivation.py` derives all server subkeys
(signed-download/share/stream, OAuth-state) via HKDF from one master; the
session JWT is signed with the raw `jwt_secret`; `secret_box.py` (Fernet)
encrypts OAuth refresh tokens + TOTP secrets at rest under one key.

**Impact:** Compromise of the master/jwt secret forges signed media URLs +
session JWTs and decrypts at-rest OAuth/TOTP secrets. **It does NOT compromise
the Vault** (client-keyed). Document the rotation story and consider distinct
keys / a KMS for the at-rest token key vs the URL-signing key.
**Refs:** CWE-320.

---

## 3. Verified-secure properties (corroborated by code review)

- **JWT `tv` revocation is correctly enforced on both cookie + bearer
  transports, algorithm-pinned** (`jwt.decode(..., algorithms=[...])`) — no
  `alg=none`/HS-RS confusion; forging `tv` requires the HMAC key.
  (`auth/users.py`)
- **SSO `state` HMAC-signed (HKDF subkey) + PKCE + id-token audience check;
  account-linking binds on `google_sub` with a CR-5 guard refusing to attach to
  an unverified password-bearing local row.** (`auth/google_sso.py`)
- **Stripe webhook** verifies the signature with `STRIPE_WEBHOOK_SECRET` and is
  idempotent via the `stripe_events` table; lives outside auth by design.
  (`backend/api/billing.py:251-273`)
- **Mass-assignment hygiene:** `UserCreate`/`UserUpdate` expose only
  `display_name`/`age_confirmed`; `role`/`quota_bytes`/`is_superuser`/
  `token_version` are not settable via register/profile.
- **Cookie flags** HttpOnly + SameSite=lax + `Secure=is_production` on every
  session-cookie setter (`users.py`, `email_link.py`, `account.py`).
- **Production-boot gate** (`security.py validate_production_settings`) rejects
  weak/short JWT secret, weak DB/MinIO creds, disabled rate-limits, missing
  Redis, env-file secret manager, JWT lifetime >7d, CORS/FRONTEND mismatch.
- **RLS request path** is server-derived + fail-closed in production (§0.2).
- **rclone** is invoked via `subprocess.run` with **list args, no `shell=True`**
  (`rclone_wrapper.py`); ffmpeg uses an `-protocol_whitelist` (CR-6).
- **`npm audit`:** 0 vulnerabilities (frontend + marketing).
- **Vault zero-knowledge** server-side contract verified (§4).

---

## 4. Vault crypto verification

Reviewed `frontend/src/vault/crypto.ts` ↔ `backend/api/vault.py` + migrations
`0044-0047`. Validated by cryptographic round-trip (Node, during build):
sealed-box 10/10; chunked file AES-GCM incl. tamper/truncation/wrong-key 30/30;
share-bundle cross-account 5/5; public-link incl. “password link not openable
by link alone” 8/8. Backend in-process E2E vs live Postgres/MinIO: vault CRUD
33/33, direct shares 29/29, public links 31/31.

**Confirmed properties:** PBKDF2-SHA256 600k → non-extractable AES-256-GCM
master key; server enforces KDF iteration bounds (310k–5M) so it can’t force a
weaker KDF; account P-256 ECDH private key stored AES-GCM-wrapped under the
master key; random per-file AES-256 in 1 MiB chunks with 4-byte-prefix‖8-byte-BE
-counter nonces + per-chunk index-bound AAD (reorder/truncation fail closed);
domain-separation tags per blob class; strict base64 + exact-byte-bound input
gate (`_b64_field`); public-link key only in the URL `#fragment`, optional
password mixed into key derivation, token = `secrets.token_urlsafe(32)` (256-bit),
unauth read endpoints rate-limited per token.

**Recommended for the auditor:** an independent crypto reviewer should bundle
`crypto.ts` under Node and re-exercise the primitives, and inspect raw DB rows +
MinIO objects to confirm opacity — see `SECURITY_AUDIT_DOSSIER.md §CRYPTO/§F`.

---

## 5. Residual-risk register (accepted / by design)

| ID | Risk | Why accepted | What would change it |
|----|------|--------------|----------------------|
| R1 | **Metadata leakage** — server sees item kind, file sizes (ciphertext length), counts, folder graph, timestamps. | Inherent to a server-stored E2E store; names are encrypted. | Padding, oblivious storage — large effort. |
| R2 | **Password public links are offline-brute-forceable** once the sealed blob is fetched. | Same model as MEGA/Proton public links; PBKDF2 600k slows it. | Strong-password enforcement; server-side guessing limits don’t help (offline). |
| R3 | **Recipient-key TOFU** — a malicious server could serve an attacker public key during a share. | Out of scope for honest-but-curious model. | Key pinning / out-of-band verification / a transparency log. |
| R4 | **XSS defeats E2E while unlocked** — master key is in JS memory. | Non-extractable CryptoKey limits export, not in-page use. | Strict CSP + zero XSS; rate SPA XSS as CRITICAL. |
| R5 | **Single master-key blast radius** (F5). | Operational simplicity. | Split keys / KMS, rotation tooling. |

---

## 6. Regression results (prior “fixed” items re-checked statically)

Confirmed present in current code (✓ = code verified; ~ = present, recommend a
live re-test):
- ✓ HKDF key separation (`key_derivation.py`); ✓ JWT revocation via
  `token_version` (alg-pinned); ✓ SSO email-match takeover guard
  (`google_sub`-bound, CR-5); ✓ production-settings validation (CR-10); ✓ lockout
  keyed by identity+IP (A5) — **but see F2** (the same keying enables distributed
  code brute-force); ✓ Stripe webhook signed + idempotent; ✓ Dockerfile
  non-root + multi-stage; ✓ compose loopback binding; ✓ rclone list-args.
- ~ cloud-sync quota bypass (CR-4) — accounting now shares one pool; recommend a
  concurrency race test. ~ ffmpeg `-protocol_whitelist` (CR-6) — present;
  recommend a crafted-playlist dynamic test. ~ zip-bomb caps (U2), EXIF/
  metadata scrub (U5/U6), pagination caps (D1), `image_persons` IDOR (D2), audit
  cap + PII scrub (D5/F12) — code present; recommend dynamic confirmation.
- CodeQL (ReDoS/open-redirect/stack-trace/client-redirect) + Dependabot
  (`xlsx`/`node-tar`): re-run the CI workflows on the audit branch to confirm
  green (not runnable in this offline environment).

---

## 7. Attack-path chains

1. **MFA-bypass takeover (F1):** victim has neuthek TOTP + weak Google 2FA →
   attacker with the Google session signs in via SSO → full session, no second
   factor → pivots to sharing/billing/Drive.
2. **Passwordless takeover (F2):** attacker knows a target email → requests a
   magic code (or waits for the victim to) → distributed-IP guesses the 6-digit
   code within the 15-min TTL → session minted. Chains with the absence of a
   per-session kill-switch (F4): the victim can only recover by a global
   token-version bump (password reset).
3. **Latent cross-tenant write (F3):** a future mis-scoped query in the
   RLS-bypassed cloud-sync worker writes/reads another user’s rows with no RLS
   backstop.

---

## 8. Prioritized remediation roadmap

**Now (≤1 day each):**
- **F1** — require neuthek TOTP on the SSO path (or explicit opt-out). *High
  impact, low effort.*
- **F2** — add an identity-scoped hard cap on `consume-code` (and TOTP/recovery
  paths). *High impact, low effort.*
- **F3** — set `app.current_user_id` in the sync worker instead of
  `app.rls_bypass='on'`. *Medium impact, low effort.*

**Next (days):**
- **F4** — per-session `jti` + revocation set; rotate on login.
- Run the full SAST/secret/dep/container scanner suite (§0.1) + a staging
  dynamic pass against the file-pipeline (parser RCE), SSRF surfaces, and the
  unauthenticated public-link/share endpoints; confirm the §6 “~” regressions.

**Later (weeks / by design):**
- **F5** — split the at-rest token key from the URL-signing key; rotation
  tooling/KMS.
- Residual-risk mitigations R1–R4 as the threat model warrants (CSP hardening
  for R4 is the highest-value of these and protects the Vault).

---

*Prepared read-only. No files were modified to produce this review; all fixes
above are proposals. Re-run against a staging deploy with rate-limits ENABLED
and the SAST suite installed to convert the “Likely” findings and “~”
regressions to Confirmed.*
