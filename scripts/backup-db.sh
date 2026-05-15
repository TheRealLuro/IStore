#!/bin/sh
# Encrypted Postgres backup — pg_dump | age recipient → local + offsite.
#
# Required env:
#   DATABASE_URL_SYNC       libpq URL Postgres is reachable at
#   BACKUP_AGE_RECIPIENT    age public key (`age1...`) — recipient of
#                           the encrypted backup. Private key MUST live
#                           OFF the application host — that's the whole
#                           point: a host compromise can't decrypt
#                           historic backups.
#
# Optional env:
#   BACKUP_OUTPUT_DIR       Default /backups. Where the encrypted dump
#                           lands locally before the upload.
#   BACKUP_DEST_URL         If set, mc cp uploads the encrypted file
#                           to this destination (e.g. s3/bucket/path/
#                           after `mc alias set s3 ...`). The
#                           sidecar's `mc-alias` step is invoked from
#                           backup-runner.sh.
#   BACKUP_RETAIN_LOCAL     0 = delete local copy after upload (default
#                           when BACKUP_DEST_URL is set); 1 = keep.
#
# Exits non-zero on dump / encrypt / upload failure. Writes an
# `audit_log` row `backup.completed` on success (so the admin
# overlay can surface "last successful backup" without a separate
# tracking table).

set -eu

: "${DATABASE_URL_SYNC:?DATABASE_URL_SYNC is required}"
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT is required (age public key)}"

OUT_DIR="${BACKUP_OUTPUT_DIR:-/backups}"
mkdir -p "$OUT_DIR"

TS=$(date -u +%Y%m%d-%H%M%S)
DUMP="$OUT_DIR/neuthek-$TS.dump"
ENC="$DUMP.age"

# pg_dump talks plain libpq; strip SQLAlchemy driver suffix if the
# env var still carries one ("postgresql+psycopg2://" → "postgresql://").
PG_URL=$(printf '%s\n' "$DATABASE_URL_SYNC" | sed 's|postgresql+psycopg2://|postgresql://|')

echo "[backup] dumping..."
# --format=custom gives us a single compressed file pg_restore likes;
# --no-owner / --no-acl let us restore into a fresh DB with a
# different role without permission acrobatics.
pg_dump --no-password --format=custom --no-owner --no-acl \
        --file="$DUMP" "$PG_URL"
echo "[backup] dump: $(stat -c%s "$DUMP" 2>/dev/null || stat -f%z "$DUMP") bytes"

echo "[backup] encrypting..."
age -r "$BACKUP_AGE_RECIPIENT" -o "$ENC" "$DUMP"
rm -f "$DUMP"
SIZE=$(stat -c%s "$ENC" 2>/dev/null || stat -f%z "$ENC")
echo "[backup] encrypted: $ENC ($SIZE bytes)"

UPLOADED_TO=""
if [ -n "${BACKUP_DEST_URL:-}" ]; then
  if ! command -v mc >/dev/null 2>&1; then
    echo "[backup] BACKUP_DEST_URL set but 'mc' binary missing — keeping local copy only" >&2
  else
    echo "[backup] uploading to $BACKUP_DEST_URL ..."
    if mc cp "$ENC" "$BACKUP_DEST_URL/"; then
      UPLOADED_TO="$BACKUP_DEST_URL"
      if [ "${BACKUP_RETAIN_LOCAL:-0}" = "0" ]; then
        rm -f "$ENC"
        echo "[backup] removed local copy after upload"
      fi
    else
      echo "[backup] upload failed — local copy kept at $ENC" >&2
      exit 2
    fi
  fi
fi

# Audit row — best effort. If psql isn't installed in this image OR
# the DB is briefly unreachable, the backup still succeeded; we
# don't want to fail the script over telemetry.
if command -v psql >/dev/null 2>&1; then
  DETAILS=$(printf '{"path":"%s","upload_dest":"%s","bytes":%s,"ts":"%s"}' \
                   "${ENC}" "${UPLOADED_TO}" "${SIZE}" "${TS}")
  psql "$PG_URL" -v ON_ERROR_STOP=0 \
       -c "INSERT INTO audit_log (user_id, action, details) VALUES (NULL, 'backup.completed', '$DETAILS'::jsonb)" \
       >/dev/null 2>&1 || echo "[backup] audit insert failed (non-fatal)"
fi

echo "[backup] done"
