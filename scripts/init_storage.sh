#!/usr/bin/env bash
# Materialize the persistent-storage directory tree under $ISTORE_DATA_ROOT.
# Idempotent — re-running is safe.
#
# On Linux, postgres in the container runs as uid 999. If you point
# ISTORE_DATA_ROOT at a directory you own, the container will get permission
# denied. Either:
#   - run this script with sudo (it will chown postgres/ to 999:999), or
#   - run docker compose with `--user $(id -u):$(id -g)` and a postgres image
#     that supports it (the official one does not by default), or
#   - set the directory to mode 777 in dev only (this script does that).
set -euo pipefail

ROOT="${ISTORE_DATA_ROOT:-./data}"

echo "ISTORE_DATA_ROOT=${ROOT}"

DIRS=(
  "postgres"
  "redis"
  "minio"
  "models"
  "backups"
)

for d in "${DIRS[@]}"; do
  path="${ROOT}/${d}"
  mkdir -p "${path}"
  echo "  ${path}"
done

# Permissions (dev-friendly; tighten for prod).
chmod -R 0777 "${ROOT}" 2>/dev/null || true

# Postgres uid 999 ownership if running as root.
if [ "$(id -u)" -eq 0 ]; then
  chown -R 999:999 "${ROOT}/postgres" 2>/dev/null || true
  echo "  chowned ${ROOT}/postgres to 999:999"
fi

echo "ok"
