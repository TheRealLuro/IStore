#!/usr/bin/env python3
"""neuthek one-shot setup.

A single cross-platform Python script that takes a fresh checkout to a
running stack: detect storage + GPU, generate strong secrets, write
`.env`, and (optionally) start the docker compose stack.

Used by:
  - End users on a single machine (`python scripts/setup.py`).
  - The dev team bootstrapping a new server (same command, --yes for
    non-interactive mode).

Design notes:
  - Stdlib only — runs before the venv is even created.
  - Idempotent: never overwrites an existing `.env` without `--reset`.
  - Visual: matches the rest of the app — uses the same accent color
    in the box-drawing prompts (cyan ≈ the in-app accent blue).
"""
from __future__ import annotations

import argparse
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


# ---------- styling ----------------------------------------------------------

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
        # else loop until user gives something


def ask_yn(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    raw = input(_c("36", "? ") + f"{prompt} [{d}] ").strip().lower()
    if not raw:
        return default
    return raw[0] == "y"


# ---------- detection -------------------------------------------------------


def detect_drives() -> list[tuple[str, int, int]]:
    """Return [(mountpoint, total_gb, free_gb)] for fixed disks. Falls back
    cleanly when `psutil` isn't available (we run before the venv exists)."""
    try:
        import psutil  # type: ignore
    except ImportError:
        # Use shutil.disk_usage on common roots.
        candidates = ["C:\\", "D:\\", "E:\\", "/", "/home", "/mnt"] if platform.system() == "Windows" else ["/", "/home", "/mnt", "/data"]
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


def detect_gpu() -> Optional[str]:
    """Return a short string describing the available accelerator, or None.

    Probes are best-effort; a missing `nvidia-smi` doesn't fail anything.
    """
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "Apple Silicon (use the default torch wheel)"

    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
            if out:
                first = out.splitlines()[0]
                return f"NVIDIA: {first} (use cu128 wheel for Blackwell, cu121 otherwise)"
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    if shutil.which("rocm-smi"):
        return "AMD ROCm detected (use the rocm6.x torch wheel)"

    return None


# ---------- env file --------------------------------------------------------

ENV_KEYS = {
    "JWT_SECRET": "secret",
    "MINIO_ROOT_USER": "istore",
    "MINIO_ROOT_PASSWORD": "secret",
    "POSTGRES_USER": "istore",
    "POSTGRES_PASSWORD": "secret",
    "POSTGRES_DB": "istore",
    "DATABASE_URL": "default",
    "DATABASE_URL_SYNC": "default",
    "REDIS_URL": "redis://localhost:6379/0",
    "MINIO_ENDPOINT": "localhost:9000",
    "ISTORE_DATA_DIR": "data",
    # C6 — set if you have SMTP. Empty means "log emails to console
    # (development mode)".
    "SMTP_HOST": "",
    "SMTP_PORT": "587",
    "SMTP_USER": "",
    "SMTP_PASS": "",
    "SMTP_FROM": "noreply@istore.local",
    "FRONTEND_BASE_URL": "http://localhost:5173",
}


def write_env(path: Path, values: dict[str, str], reset: bool) -> bool:
    """Write `.env`. Returns True if a file was actually written."""
    if path.exists() and not reset:
        warn(f".env already exists at {path}. Pass --reset to overwrite.")
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by scripts/setup.py — edit values as needed.",
        "# DO NOT COMMIT this file. .gitignore should already exclude it.",
        "",
    ]
    for k, v in values.items():
        # Quote values that contain special chars; bare otherwise.
        if any(ch in v for ch in ' #$"\''):
            lines.append(f'{k}="{v}"')
        else:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok(f"wrote {path} ({len(values)} keys)")
    return True


def build_env_values(data_dir: str) -> dict[str, str]:
    """Generate fresh secrets + the rest of the defaults."""
    jwt = secrets.token_urlsafe(48)
    pg_password = secrets.token_urlsafe(24)
    minio_password = secrets.token_urlsafe(24)
    return {
        **ENV_KEYS,
        "JWT_SECRET": jwt,
        "MINIO_ROOT_PASSWORD": minio_password,
        "POSTGRES_PASSWORD": pg_password,
        "DATABASE_URL": f"postgresql+asyncpg://istore:{pg_password}@localhost:5432/istore",
        "DATABASE_URL_SYNC": f"postgresql+psycopg2://istore:{pg_password}@localhost:5432/istore",
        "ISTORE_DATA_DIR": data_dir,
    }


# ---------- docker / native bootstrap --------------------------------------


def has_docker() -> bool:
    return shutil.which("docker") is not None


def docker_up(repo_root: Path) -> bool:
    """Run docker compose up -d. Returns True on success."""
    compose = repo_root / "docker-compose.yml"
    if not compose.exists():
        warn("no docker-compose.yml found; skipping container bootstrap")
        return False
    info("starting docker compose…")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=repo_root,
            check=True,
        )
        ok("compose stack is up (postgres, minio, redis)")
        return True
    except subprocess.CalledProcessError:
        err("docker compose up failed — start it manually with `docker compose up -d`")
        return False


# ---------- main flow -------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="neuthek one-shot setup")
    parser.add_argument("--yes", action="store_true",
                        help="non-interactive; accept all defaults")
    parser.add_argument("--reset", action="store_true",
                        help="overwrite existing .env (WARNING: regenerates secrets)")
    parser.add_argument("--data-dir", default=None,
                        help="path for neuthek data (overrides interactive prompt)")
    parser.add_argument("--no-docker", action="store_true",
                        help="skip the docker compose up step")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    header("neuthek setup")
    info(f"repo: {repo_root}")
    info(f"platform: {platform.platform()}")
    info(f"python: {sys.version.split()[0]}")

    # ----- detect storage -----
    header("Storage")
    drives = detect_drives()
    if drives:
        for mount, total, free in drives:
            print(f"    {mount:<30}  {total} GB total  {free} GB free")
    else:
        warn("could not enumerate disks — `pip install psutil` for better detection")

    if args.data_dir:
        data_dir = args.data_dir
    elif args.yes:
        data_dir = str(repo_root / "data")
    else:
        suggested = str(repo_root / "data")
        data_dir = ask("Where should neuthek store its data?", default=suggested)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    ok(f"data directory: {data_dir}")

    # ----- detect GPU -----
    header("Accelerator")
    gpu = detect_gpu()
    if gpu:
        ok(gpu)
        info("install GPU torch wheels with `pip install -e \".[ml]\"` "
             "and pick the right --index-url for your CUDA version.")
    else:
        warn("no GPU detected — neuthek runs CPU-only but inference is slow")
        info("for NVIDIA: install drivers, then `pip install torch --index-url https://download.pytorch.org/whl/cu121`")

    # ----- write .env -----
    header(".env")
    env_path = repo_root / ".env"
    values = build_env_values(data_dir)
    written = write_env(env_path, values, reset=args.reset)
    if not written and not args.reset:
        info("keeping existing .env — pass --reset to regenerate secrets")

    # ----- docker bootstrap -----
    header("Stack")
    if args.no_docker:
        info("--no-docker passed; skipping compose")
    elif not has_docker():
        warn("docker not found on PATH; install Docker Desktop and re-run with --reset")
    else:
        do_up = args.yes or ask_yn("Start docker compose now?", default=True)
        if do_up:
            docker_up(repo_root)

    # ----- finishing instructions -----
    header("Next steps")
    print(f"  1. Activate the venv:    {_c('36', '.venv\\\\Scripts\\\\activate' if platform.system() == 'Windows' else 'source .venv/bin/activate')}")
    print(f"  2. Install deps:         {_c('36', 'pip install -e \".[dev,ml]\"')}")
    print(f"  3. Run migrations:       {_c('36', 'python -m alembic upgrade head')}")
    print(f"  4. Start backend:        {_c('36', 'python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000')}")
    print(f"  5. Start frontend:       {_c('36', 'cd frontend && npm install && npm run dev')}")
    print()
    ok("Setup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
