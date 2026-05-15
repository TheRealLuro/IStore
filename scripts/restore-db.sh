#!/bin/sh
# Restore a backup produced by backup-db.sh into a Postgres instance.
#
# Required env:
#   DATABASE_URL_SYNC          libpq URL to the target Postgres
#   BACKUP_AGE_IDENTITY        Path to the age private-key file. MUST
#                              live OFF the application host until the
#                              moment of restore — that's the whole
#                              point of recipient-mode age. Mount it
#                              into the sidecar with a `-v` for the
#                              single restore run, then unmount.
#
# Usage:
#   docker compose -f docker-compose.yml -f docker-compose.backup.yml \
#                  run --rm -v /off/host/key:/key:ro \
#                  -e BACKUP_AGE_IDENTITY=/key \
#                  backup-runner restore-db.sh /backups/neuthek-XXXX.dump.age
#
# Exits non-zero on decrypt / restore failure.

set -eu

: "${DATABASE_URL_SYNC:?DATABASE_URL_SYNC is required}"
: "${BACKUP_AGE_IDENTITY:?BACKUP_AGE_IDENTITY (path to age private-key file) is required}"

INPUT="${1:-}"
if [ -z "$INPUT" ]; then
  echo "Usage: $0 <path-to-backup.age>" >&2
  exit 1
fi
if [ ! -f "$INPUT" ]; then
  echo "Not found: $INPUT" >&2
  exit 1
fi
if [ ! -f "$BACKUP_AGE_IDENTITY" ]; then
  echo "age identity file not found: $BACKUP_AGE_IDENTITY" >&2
  exit 1
fi

PG_URL=$(printf '%s\n' "$DATABASE_URL_SYNC" | sed 's|postgresql+psycopg2://|postgresql://|')

TMP=$(mktemp -t neuthek-restore-XXXXXX.dump)
trap 'rm -f "$TMP"' EXIT INT TERM

echo "[restore] decrypting $INPUT ..."
age -d -i "$BACKUP_AGE_IDENTITY" -o "$TMP" "$INPUT"
echo "[restore] decrypted: $(stat -c%s "$TMP" 2>/dev/null || stat -f%z "$TMP") bytes"

echo "[restore] restoring into $PG_URL ..."
# --clean drops existing objects before recreating them — required if
# the target DB has the schema already (the usual case for a recovery
# into a fresh container of the same name).
# --if-exists keeps the DROP statements from blowing up on a virgin DB.
# --no-owner / --no-acl mirror the dump's flags so role mismatches
# don't fail the restore.
pg_restore --clean --if-exists --no-owner --no-acl --no-password \
           --dbname="$PG_URL" "$TMP"

echo "[restore] done"
