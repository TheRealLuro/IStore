#!/usr/bin/env python3
"""neuthek one-shot setup.

A single cross-platform Python script that takes a fresh checkout to a
running stack: detect storage + accelerator, generate strong secrets
(JWT_SECRET, Postgres password, MinIO secret key, CLOUD_ENCRYPTION_KEY
as a Fernet-compatible key), write `.env`, and (optionally) start the
docker compose stack — or print the native-install checklist for
operators who prefer to manage Postgres / Redis / MinIO themselves.

Used by:
  - End users on a single machine (`python scripts/setup.py`).
  - The dev team bootstrapping a new server (same command, `--yes`
    for non-interactive mode).

Design notes:
  - Stdlib only — runs before the venv is even created.
  - Idempotent: never overwrites an existing `.env` without `--reset`.
  - Brand: the project is "neuthek". Fresh installs land on `neuthek`
    / `neuthek-*` defaults across DB role, DB name, MinIO buckets.
    Operators upgrading from a pre-rebrand install can pin the
    legacy `istore` / `istore-*` values in `.env` so existing data
    on disk stays accessible.
"""
from __future__ import annotations

import argparse
import base64
import os
import platform
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Windows' default cp1252 console can't print box-drawing glyphs. Switch
# stdout to utf-8 (best-effort; falls through silently on older Pythons).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        pass


# ---------- styling ---------------------------------------------------------

# ANSI escape codes; respect NO_COLOR per https://no-color.org.
_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return text if _NO_COLOR else f"\033[{code}m{text}\033[0m"


def header(text: str) -> None:
    bar = "─" * (len(text) + 2)
    print()
    print(_c("36", f"╭{bar}╮"))
    print(_c("36", f"│ {text} │"))
    print(_c("36", f"╰{bar}╯"))


def info(text: str) -> None:
    print(_c("90", "  • ") + text)


def ok(text: str) -> None:
    print(_c("32", "  ✓ ") + text)


def warn(text: str) -> None:
    print(_c("33", "  ! ") + text)


def err(text: str) -> None:
    print(_c("31", "  ✗ ") + text)


def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(_c("36", "? ") + prompt + suffix + " ").strip()
        if raw:
            return raw
        if default is not None:
            return default


def ask_yn(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    raw = input(_c("36", "? ") + f"{prompt} [{d}] ").strip().lower()
    if not raw:
        return default
    return raw[0] == "y"


def ask_choice(prompt: str, choices: list[tuple[str, str]], default: str) -> str:
    """choices: [(key, label), ...]. Returns key."""
    keys = [k for k, _ in choices]
    print(_c("36", "? ") + prompt)
    for k, label in choices:
        marker = "›" if k == default else " "
        print(f"    {marker} {k}  — {label}")
    while True:
        raw = input(f"    pick [{default}]: ").strip().lower()
        if not raw:
            return default
        if raw in keys:
            return raw


# ---------- detection -------------------------------------------------------


def detect_platform() -> dict[str, str]:
    sysname = platform.system()
    return {
        "system": sysname,
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
    }


def detect_drives() -> list[tuple[str, int, int]]:
    """Return [(mountpoint, total_gb, free_gb)] for fixed disks. Falls back
    cleanly when `psutil` isn't available (we run before the venv exists)."""
    try:
        import psutil  # type: ignore
    except ImportError:
        candidates = (
            ["C:\\", "D:\\", "E:\\", "F:\\"]
            if platform.system() == "Windows"
            else ["/", "/home", "/mnt", "/data", "/var", "/opt"]
        )
        rows: list[tuple[str, int, int]] = []
        for c in candidates:
            try:
                u = shutil.disk_usage(c)
                rows.append((c, u.total // (1024 ** 3), u.free // (1024 ** 3)))
            except OSError:
                continue
        return rows

    rows = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
            rows.append((part.mountpoint, u.total // (1024 ** 3), u.free // (1024 ** 3)))
        except (PermissionError, OSError):
            continue
    return rows


def detect_accelerator() -> dict[str, Optional[str]]:
    """Probe for CUDA / ROCm / Apple Metal / Intel XPU. Returns kind + hint
    pointing at the right torch wheel index URL.

    All probes are best-effort: a missing tool is a 'not detected', never
    a hard failure."""
    out: dict[str, Optional[str]] = {"kind": None, "detail": None, "wheel_hint": None}

    # Apple Silicon
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        out["kind"] = "apple"
        out["detail"] = "Apple Silicon (Metal Performance Shaders)"
        out["wheel_hint"] = "Default torch wheel; MPS is auto-selected."
        return out

    # NVIDIA CUDA
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip()
            if r:
                out["kind"] = "cuda"
                first = r.splitlines()[0]
                out["detail"] = f"NVIDIA — {first}"
                out["wheel_hint"] = (
                    "pip install torch --index-url https://download.pytorch.org/whl/cu121  "
                    "(or cu128 for RTX 50-series Blackwell)"
                )
                return out
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    # AMD ROCm (Linux only)
    if shutil.which("rocm-smi") or shutil.which("rocminfo"):
        out["kind"] = "rocm"
        out["detail"] = "AMD ROCm"
        out["wheel_hint"] = "pip install torch --index-url https://download.pytorch.org/whl/rocm6.0"
        return out

    # Intel Arc / XPU
    if shutil.which("xpu-smi") or shutil.which("xpuinfo"):
        out["kind"] = "xpu"
        out["detail"] = "Intel Arc / XPU"
        out["wheel_hint"] = (
            "pip install torch intel-extension-for-pytorch  "
            "(intel/intel-extension-for-pytorch on PyPI)"
        )
        return out
    if shutil.which("clinfo"):
        try:
            r = subprocess.run(["clinfo", "-l"], capture_output=True, text=True, timeout=5).stdout
            if "Intel" in r and ("Arc" in r or "Graphics" in r):
                out["kind"] = "xpu"
                out["detail"] = "Intel iGPU / Arc (via OpenCL)"
                out["wheel_hint"] = (
                    "pip install torch intel-extension-for-pytorch — "
                    "Intel XPU support in the engine is detect-only today; "
                    "inference falls back to CPU."
                )
                return out
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    return out


# ---------- env file --------------------------------------------------------

# Every key the backend or docker-compose reads. Defaults match
# `docker-compose.yml` so a fresh checkout works without manual editing.
# Comments preserved as `# ...` rows in the generated file.
ENV_TEMPLATE: list[tuple[str, str, str]] = [
    # (key, default, comment)
    ("APP_ENV", "dev", "dev | prod — prod triggers the security boot validator"),
    ("FRONTEND_BASE_URL", "http://localhost:5173", "where the SPA is served from"),

    ("POSTGRES_USER", "neuthek", "DB user (fresh install default; legacy installs may pin `istore`)"),
    ("POSTGRES_PASSWORD", "", "fresh password generated at setup time"),
    ("POSTGRES_DB", "neuthek", "DB name (fresh install default; legacy installs may pin `istore`)"),
    ("DATABASE_URL", "", "asyncpg URL; derived from POSTGRES_* above"),
    ("DATABASE_URL_SYNC", "", "psycopg2 URL for Alembic; derived"),
    ("POSTGRES_AT_REST_ENCRYPTION", "", "host_volume | luks | os_disk | (empty) — A2 attestation"),

    ("REDIS_URL", "redis://localhost:6379/0", "Redis for rate-limits + job queue"),

    ("MINIO_ENDPOINT", "localhost:9000", "MinIO host:port"),
    ("MINIO_ACCESS_KEY", "neuthek", "MinIO access key (root user)"),
    ("MINIO_SECRET_KEY", "", "fresh secret generated at setup time"),
    ("MINIO_SECURE", "false", "true once TLS-terminated"),
    ("MINIO_BUCKET_ORIGINALS", "neuthek-originals", ""),
    ("MINIO_BUCKET_SERVED", "neuthek-served", ""),
    ("MINIO_BUCKET_FACES", "neuthek-faces", ""),
    ("MINIO_BUCKET_QUARANTINE", "neuthek-quarantine", ""),
    ("MINIO_SSE_MODE", "off", "off | sse-s3 | sse-kms"),
    ("MINIO_SSE_KMS_KEY_ID_CONTENT", "", "KMS key for content buckets (sse-kms only)"),
    ("MINIO_SSE_KMS_KEY_ID_BIOMETRIC", "", "KMS key for the biometric/faces bucket"),

    ("JWT_SECRET", "", "fresh URL-safe 48-byte secret"),
    ("JWT_LIFETIME_SECONDS", "86400", "1 day default; rotate via JWT_SECRET regen"),

    ("SECRET_MANAGER", "env_file", "env_file | docker_secrets | aws_secretsmanager — required in prod"),
    ("TRUST_PROXY_HEADERS", "false", "true behind a reverse proxy so X-Forwarded-For is honored"),

    ("UPLOAD_MAX_BYTES", "209715200", "200 MB per upload"),
    ("UPLOAD_MAX_COUNT_PER_HOUR", "300", "per-user soft cap"),
    ("UPLOAD_MAX_BYTES_PER_DAY", "10737418240", "10 GB/day per user"),
    ("UPLOAD_MAX_IMAGE_PIXELS", "120000000", "decompression-bomb guard"),

    ("DOWNLOAD_URL_TTL_SECONDS", "300", "signed-URL TTL cap (5 min)"),
    ("REQUIRE_SIGNED_DOWNLOADS", "false", "true forces signed URLs even in-app"),
    ("SECURITY_RATE_LIMITS_ENABLED", "true", ""),
    ("AUTH_RATE_LIMIT_PER_MINUTE", "5", ""),
    ("AUTH_LOCKOUT_FAILURES", "5", "lockout after N consecutive failures"),

    ("BACKUP_AGE_RECIPIENT", "", "age public key for encrypted backups (see SECURITY.md)"),

    ("CLOUD_ENCRYPTION_KEY", "", "Fernet key for secret-box (refresh tokens, TOTP); required in prod"),
    ("GOOGLE_OAUTH_CLIENT_ID", "", "Drive sync — fill in from Google Cloud Console"),
    ("GOOGLE_OAUTH_CLIENT_SECRET", "", ""),
    ("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/cloud/callback/google_drive", ""),
    ("GITHUB_OAUTH_CLIENT_ID", "", "GitHub sync — fill in from GitHub Developer Settings"),
    ("GITHUB_OAUTH_CLIENT_SECRET", "", ""),
    ("GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/cloud/callback/github", ""),
    ("CLOUD_SYNC_HOURLY_ENABLED", "true", ""),
    ("CLOUD_SYNC_INTERVAL_SECONDS", "3600", "1 hour"),

    ("SMTP_HOST", "", "leave empty in dev to log emails to the console"),
    ("SMTP_PORT", "587", ""),
    ("SMTP_USER", "", ""),
    ("SMTP_PASS", "", ""),
    ("SMTP_FROM", "neuthek <noreply@neuthek.local>", ""),

    ("RESEND_API_KEY", "", "Resend HTTP API for waitlist + newsletter (W21)"),
    ("RESEND_FROM", "neuthek <noreply@neuthek.com>", ""),

    ("STRIPE_SECRET_KEY", "", "leave empty in dev → /billing/* returns 503"),
    ("STRIPE_PUBLISHABLE_KEY", "", ""),
    ("STRIPE_WEBHOOK_SECRET", "", ""),
    ("STRIPE_PRICE_ID_PRO", "", ""),
    ("STRIPE_PRICE_ID_BUSINESS", "", ""),

    ("NEUTHEK_DATA_DIR", "data", "where neuthek puts local data when not using docker volumes"),
]


def fresh_fernet_key() -> str:
    """Generate a Fernet-compatible key without requiring `cryptography` to
    be installed. A Fernet key is `base64.urlsafe_b64encode(os.urandom(32))`
    per the spec."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def build_env_values(data_dir: str) -> dict[str, str]:
    """Fill the template with fresh secrets + the user-chosen data dir."""
    jwt = secrets.token_urlsafe(48)
    pg_password = secrets.token_urlsafe(24)
    minio_secret = secrets.token_urlsafe(24)
    cloud_key = fresh_fernet_key()

    values: dict[str, str] = {}
    for key, default, _ in ENV_TEMPLATE:
        values[key] = default

    values["JWT_SECRET"] = jwt
    values["POSTGRES_PASSWORD"] = pg_password
    values["MINIO_SECRET_KEY"] = minio_secret
    values["CLOUD_ENCRYPTION_KEY"] = cloud_key
    values["DATABASE_URL"] = (
        f"postgresql+asyncpg://{values['POSTGRES_USER']}:{pg_password}"
        f"@localhost:5432/{values['POSTGRES_DB']}"
    )
    values["DATABASE_URL_SYNC"] = (
        f"postgresql+psycopg2://{values['POSTGRES_USER']}:{pg_password}"
        f"@localhost:5432/{values['POSTGRES_DB']}"
    )
    values["NEUTHEK_DATA_DIR"] = data_dir
    return values


def write_env(path: Path, values: dict[str, str], reset: bool) -> bool:
    """Write `.env`. Returns True if a file was actually written."""
    if path.exists() and not reset:
        warn(f".env already exists at {path}. Pass --reset to overwrite.")
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# neuthek environment — generated by scripts/setup.py.",
        "# DO NOT COMMIT this file. .gitignore already excludes it.",
        "# Run with --reset to regenerate every secret (existing data may break).",
        "",
    ]
    for key, _, comment in ENV_TEMPLATE:
        if comment:
            lines.append(f"# {comment}")
        v = values.get(key, "")
        if any(ch in v for ch in ' #$"\''):
            lines.append(f'{key}="{v}"')
        else:
            lines.append(f"{key}={v}")
        if comment:
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok(f"wrote {path} ({len(values)} keys, 4 fresh secrets)")
    return True


# ---------- docker / native bootstrap ---------------------------------------


def has_docker() -> bool:
    return shutil.which("docker") is not None


def has_postgres() -> bool:
    return shutil.which("postgres") is not None or shutil.which("pg_ctl") is not None


def has_redis() -> bool:
    return shutil.which("redis-server") is not None


def has_minio() -> bool:
    return shutil.which("minio") is not None


def docker_up(repo_root: Path) -> bool:
    """Run `docker compose up -d`. Returns True on success."""
    compose = repo_root / "docker-compose.yml"
    if not compose.exists():
        warn("no docker-compose.yml found; skipping container bootstrap")
        return False
    info("starting docker compose…")
    try:
        subprocess.run(["docker", "compose", "up", "-d"], cwd=repo_root, check=True)
        ok("compose stack is up (postgres, minio, redis)")
        return True
    except subprocess.CalledProcessError:
        err("docker compose up failed — try `docker compose up -d` manually")
        return False


def print_native_checklist() -> None:
    """Print a per-platform install checklist for operators who want to run
    Postgres / Redis / MinIO directly on the host instead of in Docker."""
    sysname = platform.system()
    info("native install — run each service yourself, point .env at localhost.")
    if sysname == "Linux":
        print("    Postgres 16 + pgvector:")
        print("      Debian/Ubuntu: sudo apt install postgresql-16 postgresql-16-pgvector")
        print("      Fedora:        sudo dnf install postgresql-server postgresql-pgvector")
        print("      Arch:          sudo pacman -S postgresql pgvector")
        print("    Redis 7:        sudo apt install redis  (or `sudo dnf install redis`)")
        print("    MinIO:          curl -O https://dl.min.io/server/minio/release/linux-amd64/minio")
    elif sysname == "Darwin":
        print("    Postgres 16 + pgvector:  brew install postgresql@16 pgvector")
        print("    Redis 7:                 brew install redis")
        print("    MinIO:                   brew install minio/stable/minio")
    elif sysname == "Windows":
        print("    Postgres 16 + pgvector:  https://www.postgresql.org/download/windows/")
        print("                             pgvector via Stack Builder or build from source")
        print("    Redis 7:                 use Docker (no first-party Windows build)")
        print("    MinIO:                   https://min.io/download#/windows")
    else:
        print(f"    Unrecognized OS: {sysname}. See SECURITY.md for the manual steps.")

    print()
    info("once running, the .env defaults (localhost:5432 / 6379 / 9000) work as-is.")


# ---------- main flow -------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="neuthek one-shot setup")
    parser.add_argument("--yes", action="store_true",
                        help="non-interactive; accept all defaults")
    parser.add_argument("--reset", action="store_true",
                        help="overwrite existing .env (regenerates every secret)")
    parser.add_argument("--data-dir", default=None,
                        help="path for neuthek data (overrides interactive prompt)")
    parser.add_argument("--mode", choices=["docker", "native", "ask"], default="ask",
                        help="docker = `docker compose up`; native = print install checklist")
    parser.add_argument("--no-stack", action="store_true",
                        help="skip both the docker compose AND native checklist sections")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    header("neuthek setup")
    plat = detect_platform()
    info(f"repo:     {repo_root}")
    info(f"platform: {plat['system']} {plat['release']} ({plat['machine']})")
    info(f"python:   {plat['python']}")

    # ----- detect storage -----
    header("Storage")
    drives = detect_drives()
    if drives:
        for mount, total, free in drives:
            print(f"    {mount:<30}  {total} GB total  {free} GB free")
    else:
        warn("could not enumerate disks — `pip install psutil` improves detection")

    if args.data_dir:
        data_dir = args.data_dir
    elif args.yes:
        data_dir = str(repo_root / "data")
    else:
        suggested = str(repo_root / "data")
        data_dir = ask("Where should neuthek store its data?", default=suggested)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    ok(f"data directory: {data_dir}")

    # ----- detect accelerator -----
    header("Accelerator")
    acc = detect_accelerator()
    if acc["kind"]:
        ok(acc["detail"] or acc["kind"])
        if acc["wheel_hint"]:
            info(acc["wheel_hint"])
    else:
        warn("no GPU detected — neuthek runs CPU-only but inference will be slow")
        info("for NVIDIA, install drivers + cu121 wheels: "
             "pip install torch --index-url https://download.pytorch.org/whl/cu121")

    # ----- write .env -----
    header(".env")
    env_path = repo_root / ".env"
    values = build_env_values(data_dir)
    written = write_env(env_path, values, reset=args.reset)
    if not written and not args.reset:
        info("keeping existing .env — pass --reset to regenerate every secret")

    # ----- stack bootstrap (docker | native) -----
    header("Stack")
    if args.no_stack:
        info("--no-stack passed; skipping")
    else:
        mode = args.mode
        if mode == "ask":
            if not has_docker():
                warn("docker not on PATH — falling back to the native checklist.")
                mode = "native"
            elif args.yes:
                mode = "docker"
            else:
                pick = ask_choice(
                    "How do you want to run Postgres / Redis / MinIO?",
                    [
                        ("docker", "one command, isolated containers (recommended)"),
                        ("native", "I'll install Postgres / Redis / MinIO myself"),
                        ("skip", "I already have the services running"),
                    ],
                    default="docker",
                )
                mode = pick

        if mode == "docker":
            if not has_docker():
                err("docker not on PATH — install Docker Desktop and re-run.")
            else:
                docker_up(repo_root)
        elif mode == "native":
            print_native_checklist()
            print()
            info("local service detection:")
            ok("postgres binary on PATH") if has_postgres() else warn("postgres not on PATH")
            ok("redis-server on PATH") if has_redis() else warn("redis-server not on PATH")
            ok("minio binary on PATH") if has_minio() else warn("minio not on PATH")
        elif mode == "skip":
            info("skipping; make sure your services are reachable on the URLs in .env")

    # ----- finishing instructions -----
    header("Next steps")
    activate = ".venv\\Scripts\\activate" if plat["system"] == "Windows" else "source .venv/bin/activate"
    print(f"  1. Activate venv:    {_c('36', activate)}")
    print(f"  2. Install deps:     {_c('36', 'pip install -e .[dev,ml]')}")
    print(f"  3. Run migrations:   {_c('36', 'python -m alembic upgrade head')}")
    print(f"  4. Start backend:    {_c('36', 'python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000')}")
    print(f"  5. Start frontend:   {_c('36', 'cd frontend && npm install && npm run dev')}")
    print()
    info("docs: README, SECURITY.md, PRIVACY.md")
    ok("Setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
