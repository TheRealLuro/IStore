# Security Policy

## Reporting a vulnerability

Email the operator at **security@neuthek.app** (or whichever address
is listed on the public site at the time of disclosure) before
opening a public issue. Include:

- Reproduction steps + the affected route or feature
- Whether user content, credentials, biometric data, or audit
  integrity is involved
- The branch / commit / tag you tested against

We acknowledge within 72 hours. Coordinated disclosure preferred —
we publish a CVE-style note in `SECURITY_REVIEW.md` once a fix lands.

## Supported versions

Only the current `main` deployment is supported for security fixes.
Self-hosters running an older commit should pin to a tagged
release once the project starts cutting them; for now, track `main`.

---

## Production checklist (the §A2 attestation set)

Every public deployment must satisfy this list. The boot-time
validator in [backend/security.py](backend/security.py)
`validate_production_settings` enforces them and refuses to start
when `APP_ENV=prod` is set with any item missing.

| Knob | Required value | Why |
|------|----------------|-----|
| `APP_ENV` | `prod` | Locks behavior changes — disables dev shortcuts, enforces the rest of this table |
| `MINIO_SECURE` | `true` | MinIO talks TLS to the API |
| `FRONTEND_BASE_URL` | `https://…` | Auth tokens never traverse plaintext |
| `JWT_SECRET` | Rotated from `dev-only-jwt-secret-CHANGE-IN-PROD` | Boot rejects the literal dev string |
| `SECRET_MANAGER` | `docker_secrets` or a platform secret manager | Forces secrets out of `.env`, into `*_FILE` mounts / Vault / Render env-secrets |
| `MINIO_SSE_MODE` | `sse-s3` or `sse-kms` | Object storage encrypts every PUT at rest |
| `MINIO_SSE_KMS_KEY_ID_CONTENT` | Set when `sse-kms` | Required key ID for content buckets |
| `MINIO_SSE_KMS_KEY_ID_BIOMETRIC` | Set when `sse-kms`, distinct from content | §A2 mandates separate keys for biometric vs. content |
| `POSTGRES_AT_REST_ENCRYPTION` | `host_volume_confirmed` | Operator attests the DB volume is encrypted (see below) |
| `BACKUP_AGE_RECIPIENT` | `age1…` public key | Encrypted backups (see below) |
| `CLOUD_ENCRYPTION_KEY` | Valid Fernet key | Encrypts TOTP secrets + OAuth refresh tokens; auto-bootstrap is a dev-only convenience |
| `REQUIRE_SIGNED_DOWNLOADS` | `true` | Forces every image/share download through HMAC-signed URLs |

The admin overlay's System tab surfaces the live posture via
`GET /admin/system` → `encryption` block, with a green/amber/red
rollup so operators can verify at a glance.

---

## TLS termination

For Docker self-host:

1. Set `NEUTHEK_DOMAIN` + `DOMAIN_ACME_EMAIL` in `.env`.
2. Open the firewall on TCP 80 + 443 (and UDP 443 for HTTP/3).
3. Bring up the stack with the TLS overlay:

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d
   ```

Caddy ([Caddyfile](Caddyfile)) auto-acquires + renews Let's Encrypt
certificates against the configured domain. The backend stays on its
private `:8000` — only Caddy is internet-exposed. The Caddyfile sets
HSTS (1 year, preload-ready), strips the `Server` header, and
disables sensors via `Permissions-Policy`.

For Render / Fly / Vercel / Railway-hosted deployments the platform
terminates TLS upstream; you only set `FRONTEND_BASE_URL` to the
HTTPS endpoint and `MINIO_SECURE` per the table above.

---

## Postgres encryption at rest

The `POSTGRES_AT_REST_ENCRYPTION=host_volume_confirmed` knob is an
**attestation** — the application can't introspect host disk
encryption, so it relies on the operator to confirm one of:

1. **Cloud managed** — AWS RDS / GCP Cloud SQL / Render Postgres /
   Fly Postgres encrypt every volume by default. Nothing to do
   except set the env var.
2. **LUKS / dm-crypt** (Linux self-host) — encrypt the volume
   holding `data/postgres` with `cryptsetup luksFormat`, mount via
   `/etc/crypttab`, then point the compose `pg_data` bind-mount at
   the mounted path.
3. **BitLocker / FileVault / VeraCrypt** (Windows/macOS dev hosts) —
   encrypt the partition holding the project directory. Acceptable
   for single-user dev / personal deployments; not the recommended
   path for production.

Column-level encryption via `pgcrypto` (extension already enabled in
migration 0001) is available for **narrow** fields that aren't
indexed for search. Don't apply it to CLIP embeddings (pgvector) or
summary text (tsvector FTS) — column-level encryption breaks both.

---

## Encrypted backups

`scripts/backup-db.sh` runs `pg_dump` + `age` and writes the
encrypted dump locally; if `BACKUP_DEST_URL` is set it uploads to
that offsite destination via `mc cp`. The script lives in a small
alpine sidecar with `age` + `postgresql-client` + `minio-client`
preinstalled, so the host doesn't need any of those.

### One-time setup

1. Generate an age key pair locally (the `age` CLI is small —
   `brew install age` / `apt install age` / WinGet / scoop):

   ```bash
   age-keygen -o ~/.config/age/neuthek.key
   # Public key gets printed:
   # public key: age1u5cy7tdtmnff3y9...
   ```

2. **Move the private key off the application host.** A USB key in a
   safe, a password manager attachment, a teammate's laptop —
   anywhere that survives the host being wiped or compromised. The
   whole point of recipient-mode age is that the host can produce
   backups it can't itself decrypt.

3. Set the recipient (the **public** key) in `.env`:

   ```
   BACKUP_AGE_RECIPIENT=age1u5cy7tdtmnff3y9...
   ```

4. (Optional) Set an offsite destination. Common shapes:

   ```
   # Backblaze B2 via mc-alias (run once inside the sidecar to
   # persist the alias to the backup_mc_config volume):
   #   mc alias set b2 https://s3.us-west-002.backblazeb2.com KEY SECRET
   BACKUP_DEST_URL=b2/neuthek-backups
   ```

### Running a backup

```bash
# Manual / first-time:
docker compose -f docker-compose.yml -f docker-compose.backup.yml \
               --profile backup run --rm backup-runner
```

The encrypted dump lands in `data/backups/neuthek-YYYYMMDD-HHMMSS.dump.age`
and, if `BACKUP_DEST_URL` is set, in the offsite store. The script
writes a `backup.completed` row to `audit_log` on success, which the
admin overlay reads via `GET /admin/system` → `encryption.backups.last`
so operators can answer "when did the last backup run?" without
grepping logs.

### Scheduling

Cron on a Linux host:

```cron
0 3 * * * cd /opt/neuthek && \
  docker compose -f docker-compose.yml -f docker-compose.backup.yml \
                 --profile backup run --rm backup-runner \
                 >> /var/log/neuthek-backup.log 2>&1
```

Windows Task Scheduler: run `pwsh.exe -File scripts\backup_encrypted.ps1`
nightly (the PowerShell variant predates the sidecar; use the
sidecar where possible for parity).

### Restoring

```bash
# Mount the OFF-HOST age private key for the duration of the restore:
docker compose -f docker-compose.yml -f docker-compose.backup.yml \
               --profile backup run --rm \
               -v "$HOME/.config/age/neuthek.key:/key:ro" \
               -e BACKUP_AGE_IDENTITY=/key \
               --entrypoint /sbin/tini -- backup-runner -- \
               /usr/local/bin/restore-db.sh /backups/neuthek-20260516-030000.dump.age
```

Verify by hitting `GET /health/db` and spot-checking a recent share.

### Retention + GDPR Article 17 ("right to erasure")

Backups capture state at a point in time, so a user who hard-deletes
their account or an image will still have their bytes present in
backup files written before the deletion. §A5 ("Deletion that
actually deletes") accepts two paths for honoring an erasure
request against the backup set; pick one and document which one
applies to your deployment:

1. **Time-bound retention** — set a fixed retention window for
   encrypted backups (default recommended: **30 days**). After that
   window, prune the oldest dumps; any user-data the deletion
   covered is now unrecoverable everywhere. This is the path most
   operators choose: a backup older than 30 days is rarely useful
   for restore anyway, and it bounds the GDPR exposure surface.
   Cron / scheduled task example:

   ```cron
   # Daily — prune backups older than 30 days, both local + offsite
   30 4 * * * cd /opt/neuthek && \
     find data/backups -name 'neuthek-*.dump.age' -mtime +30 -delete
   # If BACKUP_DEST_URL is set, also prune offsite:
   30 4 * * * docker compose -f docker-compose.yml -f docker-compose.backup.yml \
                  --profile backup run --rm backup-runner \
                  /usr/local/bin/prune-backups.sh 30
   ```

2. **Active backup re-write** — for incidents where the data is
   highly sensitive and the user has explicitly asked for accelerated
   erasure, restore the most recent backup, replay the deletion(s),
   and overwrite the file. This is operator-grade work — document
   in your incident-response runbook which engineers can execute it
   and which key-holder hands them the off-host age private key.

Whichever you choose, **state the chosen path in your privacy
policy** so users have an answer to "when does my data leave the
backup set?" The neuthek-published PRIVACY.md template (see
`PRIVACY.md`) calls this out under "How long we keep your data."

### Operator quick-checklist

- ☐ `BACKUP_AGE_RECIPIENT` set; private key stored off-host.
- ☐ `BACKUP_DEST_URL` configured (or document the local-only choice).
- ☐ Nightly backup scheduled (cron / Task Scheduler).
- ☐ Retention policy chosen + scheduled pruning (option 1 above).
- ☐ Restore tested at least once per quarter.
- ☐ Privacy-policy states backup retention window + erasure procedure.

---

## Secret-box rotation

`CLOUD_ENCRYPTION_KEY` is a Fernet key that encrypts:

- TOTP secrets in the `users` table
- Cloud-OAuth refresh tokens in `cloud_links`

To rotate without orphaning ciphertext:

1. Generate the new key locally:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Add it to the env as `CLOUD_ENCRYPTION_KEY_NEXT` alongside the
   existing `CLOUD_ENCRYPTION_KEY` (rotation helper landing under
   §A3 — see todo.md).
3. Run the rotation worker (also under §A3 — re-encrypts every
   ciphertext column with `_NEXT`, then promotes it).
4. Remove the old key.

Until the rotation helper ships, key changes invalidate every
ciphertext column — affected users must re-set TOTP + re-connect
any cloud accounts.

---

## Secret scanning

CI runs Gitleaks (`H4` workstream). Run locally before publishing:

```bash
gitleaks detect --source . --redact --verbose
```

If real credentials surface in history, rotate the credential
immediately and remove the history via `git filter-repo`. Don't
push a force-overwrite of `main` without coordinating with anyone
who has a checkout — they need to re-clone.
