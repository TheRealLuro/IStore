"""Symmetric encryption for OAuth refresh tokens + admin secrets.

Wraps `cryptography.fernet` with a single key sourced from
`settings.cloud_encryption_key`. This is **dev-grade** — a real
deployment moves to per-tenant keys via KMS / Vault under A2/A3.
The interface is the same either way; future work just swaps the
key resolver.

**Auto-bootstrap.** If the env var is missing on first call we
generate a fresh Fernet key, write it to `.env` (creating the file
if needed), and use it from then on. This removes the "operator must
manually generate a key before anything works" footgun without
weakening the security model: the key still lives in `.env` (which is
.gitignored) rather than the database, so a DB dump alone can't
decrypt anything.

Why dev-grade is OK to ship now:
  - The OAuth refresh token is server-side only; it never reaches the
    browser. Treating it as opaque ciphertext at rest is a meaningful
    improvement over plaintext storage.
  - The key lives in env / .env, not in the database.
  - Rotating the key invalidates all stored ciphertext, which is the
    correct behavior on key compromise.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from backend.config import settings

logger = logging.getLogger(__name__)


class MisconfiguredEncryption(RuntimeError):
    """Raised when the underlying ciphertext can't be decrypted (key
    rotated, row tampered, or environment changed). Callers translate
    to a 503 / "user must re-connect" UX."""


# Bootstrap is one-shot: only the first thread that hits a missing key
# should write to .env. The lock keeps two parallel uvicorn workers from
# generating two keys and stomping each other.
_BOOTSTRAP_LOCK = threading.Lock()


def _bootstrap_key() -> str:
    """Generate a fresh Fernet key and persist it to .env so subsequent
    boots see it. Returns the key string (utf-8). Idempotent under the
    bootstrap lock."""
    with _BOOTSTRAP_LOCK:
        if settings.cloud_encryption_key:
            return settings.cloud_encryption_key  # someone else won the race

        new_key = Fernet.generate_key().decode("utf-8")

        # Write into .env if present; create the file if not. Pydantic
        # settings only reads .env at startup, so we also poke the
        # in-memory settings object directly so this process sees the
        # key without a restart.
        env_path = _resolve_env_path()
        try:
            existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        except OSError:
            existing = ""
        if "CLOUD_ENCRYPTION_KEY=" in existing:
            # Already there but empty — replace the line.
            new_lines = []
            for line in existing.splitlines():
                if line.startswith("CLOUD_ENCRYPTION_KEY="):
                    new_lines.append(f"CLOUD_ENCRYPTION_KEY={new_key}")
                else:
                    new_lines.append(line)
            existing = "\n".join(new_lines) + "\n"
        else:
            sep = "" if existing.endswith("\n") or not existing else "\n"
            existing = existing + sep + f"CLOUD_ENCRYPTION_KEY={new_key}\n"

        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text(existing, encoding="utf-8")
        except OSError:
            # Fall through — we still have the key in memory; the
            # container restart will hit this branch again, which is OK.
            logger.warning(
                "secret_box: could not persist generated key to %s — "
                "key will not survive a process restart", env_path,
            )

        # Update both pydantic settings and the live os.environ so other
        # modules that haven't been imported yet pick it up.
        settings.cloud_encryption_key = new_key  # type: ignore[misc]
        os.environ["CLOUD_ENCRYPTION_KEY"] = new_key

        logger.info(
            "secret_box: generated and persisted a new "
            "CLOUD_ENCRYPTION_KEY to %s", env_path,
        )
        return new_key


def _resolve_env_path() -> Path:
    # Project root .env. The backend module lives at <root>/backend/, so
    # walk one level up. Falls back to CWD if the relative resolution
    # finds something nonsensical.
    here = Path(__file__).resolve()
    candidate = here.parent.parent / ".env"
    return candidate


def _box() -> Fernet:
    key = (settings.cloud_encryption_key or "").encode()
    if not key:
        # First-call bootstrap: generate + persist + load.
        key = _bootstrap_key().encode()
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise MisconfiguredEncryption(
            "CLOUD_ENCRYPTION_KEY is invalid (must be a 44-char URL-safe "
            "base64 Fernet key). Delete the line from .env to regenerate."
        ) from exc


def encrypt(plaintext: str) -> bytes:
    """Encrypt a UTF-8 string. Returns Fernet bytes (URL-safe b64)."""
    return _box().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    """Decrypt back to UTF-8. Raises MisconfiguredEncryption on tamper /
    wrong key — caller treats both as "must re-OAuth"."""
    try:
        return _box().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise MisconfiguredEncryption(
            "Refresh token cannot be decrypted (key rotated, "
            "row tampered, or key changed). User must re-connect "
            "their cloud account."
        ) from exc
