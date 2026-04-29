#!/usr/bin/env bash
# IStore — full server setup.
#
# Idempotent. Re-running is safe.
#
# What it does (top to bottom):
#   1. validate docker / docker compose / python3
#   2. materialize $ISTORE_DATA_ROOT directory tree
#   3. copy .env.example -> .env if missing
#   4. docker compose up -d  (postgres + redis + minio with bind-mounted volumes)
#   5. wait for all three healthchecks
#   6. create .venv if missing, install [dev] deps (add ml extras: ./setup.sh --ml)
#   7. alembic upgrade head
#   8. print next-step commands
#
# Usage:
#   ./scripts/setup.sh                   # base setup (Phase 0/1 deps only)
#   ./scripts/setup.sh --ml              # also installs torch + open_clip (~3 GB, CPU)
#   ./scripts/setup.sh --ml --gpu cu128  # GPU torch (Blackwell / RTX 50-series, e.g. 5070)
#   ./scripts/setup.sh --ml --gpu cu126  # GPU torch (modern Ada Lovelace / 40-series)
#   ./scripts/setup.sh --ml --gpu cu124  # GPU torch (older Ada Lovelace)
#   ./scripts/setup.sh --start           # also starts uvicorn in foreground after setup

set -euo pipefail

cd "$(dirname "$0")/.."

INSTALL_ML=0
START_AFTER=0
GPU_INDEX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ml) INSTALL_ML=1 ;;
    --start) START_AFTER=1 ;;
    --gpu)
      shift
      [ $# -gt 0 ] || { echo "--gpu requires a CUDA tag: cu128 / cu126 / cu124"; exit 2; }
      case "$1" in
        cu128|cu126|cu124|cu121|cu118) GPU_INDEX="https://download.pytorch.org/whl/$1" ;;
        *) echo "unknown CUDA tag: $1 (try cu128 cu126 cu124)"; exit 2 ;;
      esac
      ;;
    *) echo "unknown flag: $1"; exit 2 ;;
  esac
  shift
done

step() { printf "\n\033[1;36m== %s\033[0m\n" "$1"; }
ok()   { printf "  \033[1;32mok\033[0m %s\n" "$1"; }
fail() { printf "  \033[1;31mfail\033[0m %s\n" "$1"; exit 1; }

step "Prerequisites"
command -v docker >/dev/null 2>&1 || fail "docker not on PATH"
command -v docker compose >/dev/null 2>&1 || docker compose version >/dev/null 2>&1 || fail "docker compose not available"
command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 || fail "python not on PATH"
PY=$(command -v python3 || command -v python)
docker info >/dev/null 2>&1 || fail "docker daemon unreachable — start Docker Desktop"
ok "docker running, python at $PY"

step "Storage layout"
bash scripts/init_storage.sh
ok "data tree ready"

step "Environment file"
if [ ! -f .env ]; then
  cp .env.example .env
  ok "created .env from .env.example (edit JWT_SECRET before prod)"
else
  ok ".env already present"
fi

step "Docker compose up"
docker compose up -d
ok "containers requested"

step "Waiting for healthchecks"
for service in istore-postgres istore-minio istore-redis; do
  for i in $(seq 1 60); do
    status=$(docker inspect -f '{{.State.Health.Status}}' "$service" 2>/dev/null || echo "missing")
    [ "$status" = "healthy" ] && break
    sleep 2
  done
  [ "$status" = "healthy" ] || fail "$service not healthy after 120s (status=$status)"
  ok "$service healthy"
done

step "Python venv"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  ok "created .venv"
else
  ok ".venv already present"
fi

if [ -f .venv/bin/python ]; then
  VPY=.venv/bin/python
else
  VPY=.venv/Scripts/python
fi

step "Installing deps"
"$VPY" -m pip install --quiet --upgrade pip
if [ -n "$GPU_INDEX" ]; then
  ok "pre-installing GPU torch from $GPU_INDEX"
  "$VPY" -m pip install --quiet --upgrade --index-url "$GPU_INDEX" torch torchvision
fi
if [ "$INSTALL_ML" -eq 1 ]; then
  "$VPY" -m pip install --quiet -e ".[dev,ml]"
  ok "installed [dev,ml]"
else
  "$VPY" -m pip install --quiet -e ".[dev]"
  ok "installed [dev]  (re-run with --ml for vision pipeline)"
fi

step "Alembic migrations"
"$VPY" -m alembic upgrade head
ok "schema is at head"

step "Done"
echo
echo "Next steps:"
echo "  Start the API:    $VPY -m uvicorn backend.app:app --host 0.0.0.0 --port 8000"
echo "  Open MinIO admin: http://localhost:9001  (user/pass from .env)"
echo "  Tear down:        docker compose down"
echo "  Wipe data:        docker compose down -v && rm -rf \${ISTORE_DATA_ROOT:-./data}"

if [ "$START_AFTER" -eq 1 ]; then
  step "Starting uvicorn (foreground; Ctrl+C to stop)"
  exec "$VPY" -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
fi
