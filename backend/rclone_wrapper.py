"""§C4.6 — thin wrapper around the rclone CLI for Proton Drive + MEGA.

Both Proton Drive and MEGA are end-to-end encrypted services with
fragile Python clients (mega.py and proton-python-client break every
few months when upstream tweaks their APIs). rclone has stable native
support for both — its Go implementation is the most reliable bridge
we have for these services.

The wrapper:

  * **Per-link config files.** Every CloudLink gets its own rclone
    config at ``{settings.rclone_config_root}/{link_id}.conf`` so
    rotating one user's credentials doesn't touch any other link.
    Files are written with 0o600 permissions; rclone "obscure" is
    light obfuscation (not encryption — see the rclone docs), and the
    filesystem perms are the real security boundary.

  * **subprocess + asyncio.to_thread.** Each rclone invocation is a
    blocking call; we wrap in ``asyncio.to_thread`` so the FastAPI
    event loop stays responsive while a long ``lsjson`` walk runs.

  * **Tight timeouts.** ``lsjson`` capped at 5 min; ``cat`` capped at
    10 min per file. Beyond that we'd rather surface a clear error
    than hang a sync indefinitely.

Three public async helpers:

  - ``rclone_ls_json(remote, config_path)`` — list every file under
    the remote root as a flat JSON array.
  - ``rclone_copy_to_stdout(remote, path, config_path)`` — return the
    bytes of a single remote file.
  - ``rclone_remote_test(remote, config_path)`` — quick "are these
    credentials good?" probe via ``rclone about``.

Plus the config-writing helpers used by ``backend.cloud_sync`` /
``backend.api.cloud``:

  - ``write_proton_config(link_id, email, password, totp)``
  - ``write_mega_config(link_id, email, password)``
  - ``config_path_for_link(link_id)``
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from backend.config import settings

logger = logging.getLogger(__name__)


# rclone names used inside the per-link config files. Kept stable so
# tests + cloud_sync helpers can hard-code them.
PROTON_REMOTE_NAME = "proton-drive"
MEGA_REMOTE_NAME = "mega"


class RcloneError(RuntimeError):
    """Raised when an rclone invocation fails. Carries the captured
    stderr so callers can surface a useful message (e.g. "Proton said:
    2FA required") without re-running the command."""

    def __init__(self, message: str, *, stderr: str = "", returncode: int | None = None):
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


def _config_dir() -> Path:
    """Resolve + ensure the per-link config root exists. Creates the
    directory on first use so a fresh deploy doesn't need a manual
    mkdir before the first connect attempt."""
    root = Path(settings.rclone_config_root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except (OSError, NotImplementedError):
        # Windows + some FUSE mounts don't support chmod; the perms
        # there are governed by the filesystem ACL anyway.
        pass
    return root


def config_path_for_link(link_id: int | str) -> Path:
    """Return ``{config_root}/{link_id}.conf``. The link_id is either
    a numeric CloudLink.id from the DB or a tmp-prefixed token used
    during the Proton 2FA dance. Strip out the safe punctuation
    (``-`` and ``_``) and require everything else to be
    alphanumeric — path traversal would need a ``/`` or ``..`` and
    those would fail the isalnum() check.

    `secrets.token_urlsafe()` (used for the tmp ids) emits base64url
    output that mixes ``-`` and ``_``; an earlier version of this
    helper only allowed ``-`` and 500'd on the first underscore,
    surfacing in the browser as a "blocked by CORS policy" error
    (FastAPI 500s bypass the CORS middleware so the response has
    no Access-Control-Allow-Origin header — Chrome reports the
    absence as a CORS violation even though the real failure was
    a server-side ValueError).
    """
    sid = str(link_id)
    if not sid.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"Invalid rclone config link id: {link_id!r}")
    return _config_dir() / f"{sid}.conf"


def _obscure_password(plain: str) -> str:
    """Run ``rclone obscure`` to convert a plaintext password to the
    rclone-obscured form. This is **light obfuscation, not
    encryption** — rclone's docs make this explicit. The real
    security is the 0o600 permissions on the config file."""
    if not plain:
        return ""
    try:
        out = subprocess.run(
            ["rclone", "obscure", plain],
            capture_output=True, text=True, check=True, timeout=5,
        )
    except FileNotFoundError as exc:
        raise RcloneError("rclone binary not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RcloneError(
            "rclone obscure failed",
            stderr=exc.stderr or "",
            returncode=exc.returncode,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RcloneError("rclone obscure timed out") from exc
    return out.stdout.strip()


def _write_config_file(path: Path, contents: str) -> None:
    """Write an rclone config file with locked-down permissions.

    Created with mode 0o600 + chmod for belt-and-braces — on POSIX
    the perms land at create time; on Windows the chmod is best-
    effort (the file's ACL governs there)."""
    # Truncate+write under restrictive umask so the file isn't world-
    # readable for a moment before chmod lands. Using os.open to get
    # the mode at create time on POSIX.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contents)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


def write_proton_config(
    link_id: int | str, email: str, password: str, totp: str = "",
) -> Path:
    """Materialize a Proton Drive rclone config for the given link.

    Both ``password`` and ``totp`` (the TOTP secret, not the per-login
    code) are passed through ``rclone obscure`` before landing in the
    file. ``totp`` is optional — empty for accounts without 2FA.
    """
    obscured_pw = _obscure_password(password)
    obscured_2fa = _obscure_password(totp) if totp else ""
    body = (
        f"[{PROTON_REMOTE_NAME}]\n"
        "type = protondrive\n"
        f"username = {email}\n"
        f"password = {obscured_pw}\n"
    )
    if obscured_2fa:
        body += f"2fa = {obscured_2fa}\n"
    path = config_path_for_link(link_id)
    _write_config_file(path, body)
    return path


def write_mega_config(link_id: int | str, email: str, password: str) -> Path:
    """Materialize a MEGA rclone config for the given link."""
    obscured_pw = _obscure_password(password)
    body = (
        f"[{MEGA_REMOTE_NAME}]\n"
        "type = mega\n"
        f"user = {email}\n"
        f"pass = {obscured_pw}\n"
    )
    path = config_path_for_link(link_id)
    _write_config_file(path, body)
    return path


def _run_rclone_sync(
    args: list[str], *, timeout: float,
) -> subprocess.CompletedProcess:
    """Blocking ``rclone`` invocation. Run inside ``asyncio.to_thread``
    from the async helpers — never call directly from the event
    loop."""
    if shutil.which("rclone") is None:
        raise RcloneError("rclone binary not found on PATH")
    try:
        return subprocess.run(
            ["rclone", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RcloneError(
            f"rclone command timed out after {timeout:.0f}s: {args[0]}",
        ) from exc


async def rclone_ls_json(
    remote_name: str, config_path: str | Path,
) -> list[dict]:
    """Run ``rclone lsjson --recursive {remote}:`` and return the
    parsed JSON list. Each entry carries the standard rclone shape:
    ``{Path, Name, Size, MimeType, ModTime, IsDir, ID}``.

    Times out after 5 minutes — accounts with truly enormous trees
    can take longer, but at that point we'd rather surface an error
    and have the operator extend the timeout deliberately than have
    the API container quietly hold a worker for 30+ minutes.
    """
    args = [
        "--config", str(config_path),
        "lsjson", "--recursive",
        f"{remote_name}:",
    ]

    def _run() -> subprocess.CompletedProcess:
        return _run_rclone_sync(args, timeout=300.0)

    result = await asyncio.to_thread(_run)
    if result.returncode != 0:
        logger.warning(
            "rclone lsjson failed: remote=%s rc=%s stderr=%s",
            remote_name, result.returncode, result.stderr[:500],
        )
        raise RcloneError(
            "rclone listing failed.",
            stderr=result.stderr or "",
            returncode=result.returncode,
        )
    try:
        data = json.loads(result.stdout or "[]")
    except ValueError as exc:
        raise RcloneError("rclone lsjson returned non-JSON output") from exc
    if not isinstance(data, list):
        raise RcloneError("rclone lsjson returned unexpected shape")
    return data


async def rclone_copy_to_stdout(
    remote_name: str, remote_path: str, config_path: str | Path,
) -> bytes:
    """Run ``rclone cat {remote}:{path}`` and return the bytes. Used
    by the per-file download path. Times out after 10 minutes per
    file — anything that takes longer is almost certainly a stuck
    socket and not worth blocking a worker on.
    """
    args = [
        "--config", str(config_path),
        "cat",
        f"{remote_name}:{remote_path}",
    ]

    def _run() -> subprocess.CompletedProcess:
        # `subprocess.run` with `text=False` since the payload is
        # arbitrary binary (a JPEG, MP4, etc.). Override the helper to
        # bypass its text-mode default.
        if shutil.which("rclone") is None:
            raise RcloneError("rclone binary not found on PATH")
        try:
            return subprocess.run(
                ["rclone", *args],
                capture_output=True,
                check=False,
                timeout=600.0,
            )
        except subprocess.TimeoutExpired as exc:
            raise RcloneError("rclone cat timed out after 600s") from exc

    result = await asyncio.to_thread(_run)
    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        logger.warning(
            "rclone cat failed: remote=%s path=%s rc=%s stderr=%s",
            remote_name, remote_path, result.returncode, stderr[:500],
        )
        raise RcloneError(
            "rclone download failed.",
            stderr=stderr,
            returncode=result.returncode,
        )
    return result.stdout or b""


async def rclone_remote_test(
    remote_name: str, config_path: str | Path,
) -> tuple[bool, str]:
    """Quick connectivity probe — ``rclone about {remote}:``. Returns
    ``(ok, message)`` where ``ok`` is True on a zero exit code and
    ``message`` is the captured stderr (useful when ok=False so the
    caller can detect "2FA required" vs "wrong password" vs network
    failure).
    """
    args = [
        "--config", str(config_path),
        "about",
        f"{remote_name}:",
    ]

    def _run() -> subprocess.CompletedProcess:
        return _run_rclone_sync(args, timeout=30.0)

    try:
        result = await asyncio.to_thread(_run)
    except RcloneError as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, (result.stdout or "").strip()
    return False, (result.stderr or "").strip()


def is_2fa_required_error(message: str) -> bool:
    """Heuristic over rclone's stderr to detect "the account has 2FA
    enabled but the config has no 2fa field." rclone surfaces this as
    a few different phrasings depending on the backend; we look for
    the lowercased substrings that consistently appear across the
    Proton Drive paths.

    Used by the /cloud/proton-drive/start endpoint to decide between
    "persist the link" and "stash the session, prompt for 2FA."
    """
    if not message:
        return False
    lc = message.lower()
    # Proton's rclone backend phrasings observed in the wild:
    #   "2fa required" / "two-factor"
    #   "requires 2fa"
    #   "missing 2fa"
    return any(
        phrase in lc for phrase in (
            "2fa", "two-factor", "two factor", "totp", "second factor",
        )
    )
