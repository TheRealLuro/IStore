# Security Policy

## Supported Versions

Only the current mainline deployment is supported for security fixes.

## Reporting

Report vulnerabilities privately to the project operator before public
disclosure. Include reproduction steps, affected endpoints, and whether the
issue involves user content, credentials, biometric data, or audit integrity.

## Production Baseline

Public deployments must use the Compose+Caddy production baseline or an
equivalent platform:

- HTTPS at the public entrypoint.
- `APP_ENV=prod`.
- Redis reachable through `REDIS_URL`; production startup fails without it.
- `SECRET_MANAGER=docker_secrets` or a platform secret manager.
- Sensitive settings provided through `_FILE` paths or platform secrets, not a
  production `.env`.
- `MINIO_SECURE=true`.
- `MINIO_SSE_MODE=sse-s3` or `sse-kms`.
- Distinct KMS key IDs for content and biometric scopes when KMS is used.
- `POSTGRES_AT_REST_ENCRYPTION=host_volume_confirmed`.
- `BACKUP_AGE_RECIPIENT` set and backups encrypted before leaving the host.
- `REQUIRE_SIGNED_DOWNLOADS=true` and `VITE_REQUIRE_SIGNED_DOWNLOADS=true`.

## Backup Retention

Use `scripts/backup_encrypted.ps1` from a host with encrypted storage. The script
creates a Postgres dump plus MinIO data archive and encrypts the result with
`age`. Store the private age identity outside the application host. Deleted
content can remain in old backups until the backup retention window expires.

## Secret Scanning

CI runs Gitleaks. Run `gitleaks detect --source . --redact --verbose` before
publishing or rewriting history. If real credentials or user data are found in
history, rotate the credential and remove the history with a dedicated
repository-rewrite procedure.
