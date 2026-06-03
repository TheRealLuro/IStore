"""Read + write SFTP server exposing a neuthek user's files (SFTP-1).

WHAT THIS IS
------------
A standalone async SSH/SFTP server (asyncssh) that presents a neuthek
user's `folders` + `images` as a browsable, mountable filesystem. With
it a user can:

  * `sshfs user@host:/ /mnt/neuthek -p 2222`     (Linux / macOS)
  * "Connect to Server" -> `sftp://user@host:2222`  (macOS Finder)
  * add a WinSCP / Windows-Explorer-via-WinFsp session  (Windows)
  * `sftp -P 2222 user@host`                      (the OpenSSH CLI, all OS)

and see their folder tree as directories and their files as files, then
download / read them AND upload / create / rename / delete.

READ  : list (scandir) + stat + open-for-read + ranged read + close.
WRITE : open-for-write/put (-> store_upload), mkdir (-> Folder row),
        rename / posix_rename, remove (soft-delete image), rmdir
        (soft-delete folder subtree). setstat/fsetstat are accepted as
        no-ops so a client's post-upload chmod/utime doesn't fail `put`.

CRITICAL — every WRITE funnels through the EXACT same security as the
HTTP upload path:
  * `backend.image.store_upload` runs `validate_upload` (magic-byte
    sniff, size cap, SVG sanitize, content guard) + EXIF / video-metadata
    stripping, then stores the blobs + Image row. An SFTP upload is
    therefore exactly as safe as a web upload — no second, weaker code
    path.
  * `backend.api.storage.enforce_storage_quota` is called BEFORE the
    store, so a write that would push the account over quota is rejected
    (SSH_FX_FAILURE / quota message) and never lands.
  * Max upload size = `settings.upload_max_bytes` (the same per-file cap
    the web uploader enforces); a stream that exceeds it is aborted.

ABUSE PROTECTION ("protect from over usage")
---------------------------------------------
  * Per-user CONCURRENT-SESSION cap (`SFTP_MAX_SESSIONS_PER_USER`): a new
    connection past the cap is refused at auth time.
  * Per-connection RATE LIMITS: a token bucket on operations/sec
    (`SFTP_MAX_OPS_PER_SEC`) AND one on bytes/sec for reads+writes
    (`SFTP_MAX_BYTES_PER_SEC`). An over-rate op blocks (awaits) until the
    bucket refills, so one client can't saturate the box — it's throttled,
    not just rejected.
  * Per-connection CUMULATIVE-TRANSFER cap (`SFTP_MAX_SESSION_BYTES`):
    reads+writes are tallied and the session is cut off once it crosses the
    ceiling, so a single authentication can't move an unbounded volume.
  * Failed-auth lockout / exponential backoff via the SAME Redis counter
    machinery the web auth path uses (`backend.security`).
  * Stable, persisted host key (never regenerated per boot).
  * NO anonymous / keyboard-interactive / host-based / shell / exec access.

ARCHITECTURE
------------
  * Runs as its OWN process / container (`python -m backend.sftp_server`,
    compose service `sftp`, bound to 127.0.0.1:2222 by default). Isolated
    from the API so an SFTP bug can't take the web app down and vice
    versa.
  * Host key is generated ONCE and persisted to a volume
    (`SFTP_HOST_KEY_PATH`, default /var/neuthek/sftp/ssh_host_ed25519_key)
    so clients can pin it across restarts.

AUTH (fail closed)
------------------
  * SSH username = the neuthek account EMAIL (lowercased).
  * Two accepted methods, both resolving to the SAME user_id:
      1. public key  — must match a row in `sftp_authorized_keys`.
      2. password    — Argon2-checked against `sftp_credentials`. A
                       DEDICATED SFTP password, never the account login
                       password.
  * Unknown user, no key match, no password set, or wrong password ->
    rejected + a failed-attempt recorded.

PER-USER ISOLATION (mandatory)
------------------------------
Two independent fences, both keyed on the authenticated user_id:
  1. Every DB session opened for a connection runs with
     `app.current_user_id = <that user>` (the Postgres RLS GUC), so the
     FORCE-RLS policies on `folders` / `images` physically prevent the
     session from seeing OR writing another tenant's rows.
  2. Every query ALSO carries an explicit `user_id == <that user>` WHERE
     clause (defence in depth).
A connection can therefore only ever traverse + read + write the files of
the account it authenticated as. The path is NEVER trusted — it's resolved
component-by-component against the authenticated user's own rows.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import stat as stat_module
import sys
import tempfile
import time
from typing import Optional
from uuid import UUID

import asyncssh
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.context import (
    reset_current_user_id,
    reset_rls_bypass,
    set_current_user_id,
    set_rls_bypass,
)
from backend.db import SessionLocal
from backend.models import (
    Folder,
    Image,
    SftpAuthorizedKey,
    SftpCredential,
    User,
)
from backend.storage import storage

logger = logging.getLogger("backend.sftp")


# ---------------------------------------------------------------------------
# neuthek branding
# ---------------------------------------------------------------------------
# SFTP is a file protocol — there's no GUI surface to put an app icon on, so
# we brand the touch-points a mount actually exposes:
#   1. the SSH AUTH BANNER, shown by every client right after connect /
#      before (or during) the password prompt (asyncssh `send_auth_banner`);
#   2. a virtual read-only "Welcome to neuthek.txt" at the drive root (see
#      `_WELCOME_FILENAME` + the virtual-node plumbing below);
#   3. a virtual read-only `neuthek.ico` + `desktop.ini` pair at the drive
#      root (see `_ICON_FILENAME` / `_DESKTOP_INI_FILENAME`). When the share
#      is mounted as a Windows drive/folder that HONORS desktop.ini, Explorer
#      reads desktop.ini, finds `IconResource=neuthek.ico,0`, and shows the
#      neuthek mark as the folder icon + an InfoTip tooltip — the closest a
#      pure-SFTP mount can get to the OneDrive folder-branding look. (The full
#      OneDrive sidebar entry + per-file sync-status overlay icons require a
#      NATIVE Windows sync client / Cloud Files API + shell extensions and are
#      out of scope here.)
# All three are cosmetic and MUST NOT interfere with real files or quota.

# ASCII neuthek wordmark for the auth banner. Plain 7-bit ASCII so it renders
# in every terminal / SFTP client (no box-drawing or Unicode that a Windows
# console or minimal client might mangle).
_NEUTHEK_WORDMARK = r"""
                    _   _          _
  _ __   ___ _   _| |_| |__   ___| | __
 | '_ \ / _ \ | | | __| '_ \ / _ \ |/ /
 | | | |  __/ |_| | |_| | | |  __/   <
 |_| |_|\___|\__,_|\__|_| |_|\___|_|\_\
"""


def _auth_banner() -> str:
    """The text every SFTP/SSH client shows on connect. Wordmark + a short
    welcome + a one-line note that this is the user's private drive. Sent
    via asyncssh `send_auth_banner` from `begin_auth`."""
    return (
        _NEUTHEK_WORDMARK
        + "\r\n  Welcome to your neuthek drive.\r\n"
        + "  Your files, on your terms - browse, read and upload below.\r\n"
        + "  Uploads are checked + counted against your storage quota, just "
        "like the web app.\r\n\r\n"
    )


# Name of the virtual welcome file surfaced at the drive root. Kept distinct
# from anything store_upload would ever create (it never makes a bare .txt at
# the root with this exact name), and the write path refuses to create /
# rename onto it, so a real upload can't shadow or replace it.
_WELCOME_FILENAME = "Welcome to neuthek.txt"


def _welcome_file_body() -> bytes:
    """Body of the virtual root welcome file. Static + tiny; carries the
    friendly message plus the live connection limits so a mounted user has a
    readable orientation file without opening the web app. Read-only, virtual,
    NOT counted against quota."""
    max_file_mb = settings.upload_max_bytes / (1024 * 1024)
    text = (
        "Welcome to neuthek\r\n"
        "==================\r\n"
        "\r\n"
        "This is your private neuthek drive, mounted over SFTP. The folders\r\n"
        "and files you see here are the same ones in your neuthek gallery —\r\n"
        "browse them, open them, and drag new files in to upload.\r\n"
        "\r\n"
        "A few things to know:\r\n"
        "  * Uploads go through the exact same checks as the web app\r\n"
        "    (type + safety validation, metadata stripping) and count\r\n"
        "    against your storage quota. When the quota is full, further\r\n"
        "    uploads are refused until you free up space.\r\n"
        f"  * Each file can be up to {max_file_mb:,.0f} MB.\r\n"
        "  * Deleting here moves items to Trash (recoverable), same as the\r\n"
        "    web app — nothing is hard-deleted out from under you.\r\n"
        "\r\n"
        "This file is a read-only welcome note. It isn't one of your real\r\n"
        "files, it doesn't use any of your quota, and you can't delete or\r\n"
        "overwrite it — it just lives at the root of every neuthek mount.\r\n"
        "\r\n"
        "Enjoy your neuthek drive.\r\n"
    )
    return text.encode("utf-8")


# Names of the virtual Windows-branding files surfaced at the drive root. As
# with the welcome file, the write/rename paths refuse to create / overwrite /
# rename onto these names, and a real same-named user file (if one ever
# existed) is listed INSTEAD of the virtual node (see `_list_dir`), so user
# data is never shadowed.
_ICON_FILENAME = "neuthek.ico"
_DESKTOP_INI_FILENAME = "desktop.ini"


# Cached bytes of the generated multi-resolution .ico. Built ONCE, lazily, on
# first request and memoized here so we don't re-render per listing/open. A
# value of b"" means "tried and failed" (PIL missing / draw error) — we cache
# that too so we don't retry the render on every call.
_ICON_BYTES_CACHE: Optional[bytes] = None


def _neuthek_icon_bytes() -> bytes:
    """Return the bytes of a real multi-resolution Windows .ico of the neuthek
    mark, generated once with Pillow and cached in a module global.

    The icon is the angular neuthek glyph from the loader/film mark — two white
    polyline strokes (path ~ ``M48 20 L44 44 L22 18 L18 44`` then
    ``M48 20 L34 30 L18 28``) laid out on a 66-unit design box — drawn centered
    on a dark rounded square. It's rendered at 256px and saved as a single
    .ico carrying the 16/32/48/256 px frames Windows Explorer picks from for
    different view sizes (``img.save(buf, format="ICO",
    sizes=[(16,16),(32,32),(48,48),(256,256)])`` — Pillow downscales the base
    image to each requested frame internally).

    Fully defensive: any failure (Pillow absent, draw error) is swallowed and
    an empty byte string is cached + returned, so a branding hiccup can never
    break import, a directory listing, or a read. The caller treats b"" as a
    zero-length file (harmless — Windows just falls back to a default icon)."""
    global _ICON_BYTES_CACHE
    if _ICON_BYTES_CACHE is not None:
        return _ICON_BYTES_CACHE
    try:
        import io

        from PIL import Image, ImageDraw

        # 66-unit design box (matches the loader/film glyph coordinates), drawn
        # at 256px and downscaled into the smaller frames by the ICO encoder.
        box = 66.0
        base_px = 256
        scale = base_px / box

        img = Image.new("RGBA", (base_px, base_px), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Dark rounded-square tile behind the mark (neuthek's dark surface).
        radius = max(2, int(base_px * 0.18))
        draw.rounded_rectangle(
            [0, 0, base_px - 1, base_px - 1], radius=radius, fill=(28, 30, 38, 255)
        )

        def _p(x: float, y: float) -> tuple[float, float]:
            return (x * scale, y * scale)

        stroke_w = max(1, int(round(base_px * 0.06)))
        # The two strokes of the neuthek angular mark.
        strokes = [
            [(48, 20), (44, 44), (22, 18), (18, 44)],
            [(48, 20), (34, 30), (18, 28)],
        ]
        for poly in strokes:
            draw.line(
                [_p(*pt) for pt in poly],
                fill=(255, 255, 255, 255),
                width=stroke_w,
                joint="curve",
            )

        buf = io.BytesIO()
        img.save(
            buf,
            format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
        )
        _ICON_BYTES_CACHE = buf.getvalue()
    except Exception:
        logger.warning("sftp: could not render neuthek.ico — serving empty icon", exc_info=True)
        _ICON_BYTES_CACHE = b""
    return _ICON_BYTES_CACHE


def _desktop_ini_body() -> bytes:
    """Body of the virtual root ``desktop.ini``. When the share is mounted as a
    drive/folder Windows Explorer honors desktop.ini for, Explorer reads this,
    resolves ``IconResource=neuthek.ico,0`` (the sibling virtual icon at the
    same root), and shows the neuthek folder icon + the InfoTip tooltip.

    UTF-8, CRLF — a normal Windows .ini. Read-only, virtual, NOT counted
    against quota."""
    text = (
        "[.ShellClassInfo]\r\n"
        "IconResource=neuthek.ico,0\r\n"
        "InfoTip=Your neuthek drive\r\n"
        "[ViewState]\r\n"
    )
    return text.encode("utf-8")


# Listener defaults. LOOPBACK-ONLY by design (default 127.0.0.1): the
# server binds the loopback interface in-code, so even if the compose
# port mapping were ever widened the listener itself refuses non-local
# connections. An operator who deliberately wants to expose the gateway
# (behind their own TLS/bastion) can set SFTP_BIND=0.0.0.0 explicitly —
# but the safe default is local-only and lives here, in code, not in the
# port mapping.
def _bind_host() -> str:
    return os.getenv("SFTP_BIND", "127.0.0.1")


def _bind_port() -> int:
    try:
        return int(os.getenv("SFTP_PORT", "2222"))
    except ValueError:
        return 2222


def _host_key_path() -> str:
    return os.getenv(
        "SFTP_HOST_KEY_PATH", "/var/neuthek/sftp/ssh_host_ed25519_key"
    )


def _max_sessions_per_user() -> int:
    try:
        return max(1, int(os.getenv("SFTP_MAX_SESSIONS_PER_USER", "5")))
    except ValueError:
        return 5


def _max_ops_per_sec() -> float:
    try:
        return max(1.0, float(os.getenv("SFTP_MAX_OPS_PER_SEC", "50")))
    except ValueError:
        return 50.0


def _max_bytes_per_sec() -> float:
    # Default 64 MiB/s per connection — generous for a real mount, but a
    # hard ceiling so one client can't peg the disk / network.
    try:
        return max(
            64.0 * 1024,
            float(os.getenv("SFTP_MAX_BYTES_PER_SEC", str(64 * 1024 * 1024))),
        )
    except ValueError:
        return float(64 * 1024 * 1024)


def _max_session_bytes() -> int:
    # Per-CONNECTION cumulative-transfer ceiling (reads + writes combined).
    # The bytes/sec bucket throttles instantaneous bandwidth; this caps the
    # TOTAL a single session may move before it must reconnect, so one
    # long-lived mount can't exfiltrate / ingest an unbounded volume on a
    # single authentication. Default 256 GiB — far above any legitimate
    # interactive session, low enough to bound abuse. 0/negative disables.
    try:
        return int(os.getenv("SFTP_MAX_SESSION_BYTES", str(256 * 1024 * 1024 * 1024)))
    except ValueError:
        return 256 * 1024 * 1024 * 1024


# Whether write operations are allowed at all. Defaults ON (the user asked
# for write); an operator can ship a read-only deployment with
# SFTP_READ_ONLY=true without touching code.
def _read_only() -> bool:
    return os.getenv("SFTP_READ_ONLY", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


# ---------------------------------------------------------------------------
# Per-user concurrent-session accounting
# ---------------------------------------------------------------------------
# Maps user_id -> live connection count. Guarded by a plain dict + the GIL;
# increments/decrements happen on the single asyncssh event loop thread, so
# no lock is needed. A connection that authenticates bumps the count; the
# count drops on connection_lost.
_active_sessions: dict[UUID, int] = {}


def _sessions_for(user_id: UUID) -> int:
    return _active_sessions.get(user_id, 0)


def _try_acquire_session(user_id: UUID) -> bool:
    """Reserve a session slot for `user_id`; False if the per-user cap is
    already reached."""
    cur = _active_sessions.get(user_id, 0)
    if cur >= _max_sessions_per_user():
        return False
    _active_sessions[user_id] = cur + 1
    return True


def _release_session(user_id: UUID) -> None:
    cur = _active_sessions.get(user_id, 0)
    if cur <= 1:
        _active_sessions.pop(user_id, None)
    else:
        _active_sessions[user_id] = cur - 1


# ---------------------------------------------------------------------------
# Token-bucket rate limiter (per connection)
# ---------------------------------------------------------------------------
class _TokenBucket:
    """A classic token bucket. `take(n)` awaits until `n` tokens are
    available, refilling at `rate` tokens/sec up to `capacity`. Used for
    ops/sec and bytes/sec throttling so a noisy client is SLOWED rather
    than hard-failed (smoother UX, same protection)."""

    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated = now

    async def take(self, n: float = 1.0) -> None:
        # Cap a single request's demand at the bucket capacity so an
        # oversized read can't deadlock waiting for tokens it can never
        # accumulate.
        n = min(n, self._capacity)
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                wait = deficit / self._rate if self._rate > 0 else 0.05
            # Sleep OUTSIDE the lock so other coroutines can refill-check.
            await asyncio.sleep(min(wait, 1.0))


# ---------------------------------------------------------------------------
# Host key
# ---------------------------------------------------------------------------
def load_or_create_host_key() -> asyncssh.SSHKey:
    """Load the persistent host key, generating + persisting one on first
    boot. Stable across restarts so clients' known_hosts pinning holds."""
    path = _host_key_path()
    try:
        if os.path.exists(path):
            key = asyncssh.read_private_key(path)
            logger.info("sftp: loaded host key from %s", path)
            return key
    except Exception:
        logger.exception(
            "sftp: existing host key at %s is unreadable — regenerating "
            "(clients will see a host-key-changed warning)", path,
        )

    key = asyncssh.generate_private_key("ssh-ed25519")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            os.chmod(os.path.dirname(path), 0o700)
        except OSError:
            pass
        data = key.export_private_key()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data if isinstance(data, (bytes, bytearray)) else data.encode())
        finally:
            os.close(fd)
        logger.info("sftp: generated + persisted new host key at %s", path)
    except Exception:
        logger.exception(
            "sftp: could not persist generated host key to %s — it will "
            "change on restart (clients re-prompt). Mount a writable "
            "volume there.", path,
        )
    return key


# ---------------------------------------------------------------------------
# DB helpers — every call pins to the authenticated user (RLS + WHERE)
# ---------------------------------------------------------------------------
class _UserSession:
    """Async-context wrapper that opens a SessionLocal with the RLS GUC
    set to `user_id`, so FORCE-RLS on folders/images fences the session."""

    def __init__(self, user_id: UUID) -> None:
        self._user_id = user_id
        self._token = None
        self._session = None

    async def __aenter__(self):
        self._token = set_current_user_id(self._user_id)
        self._session = SessionLocal()
        return await self._session.__aenter__()

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._session is not None:
                await self._session.__aexit__(exc_type, exc, tb)
        finally:
            if self._token is not None:
                reset_current_user_id(self._token)


def _safe_name(name: str) -> str:
    """Sanitize a folder/file name into a single safe path component for the
    VIRTUAL listing (display side). Folder names + original filenames are
    user-controlled free text; for a filesystem view we guarantee a single
    component with no separators / traversal."""
    if not name:
        return "untitled"
    cleaned = (
        name.replace("\x00", "")
        .replace("/", "_")
        .replace("\\", "_")
        .strip()
    )
    if cleaned in ("", ".", ".."):
        return "untitled" if cleaned == "" else f"_{cleaned}"
    return cleaned


def _image_filename(img_row) -> str:
    """Pick the display filename for an image row."""
    name = img_row.original_filename
    if name:
        return _safe_name(name)
    ext = ""
    mime = (img_row.mime_type_served or img_row.mime_type_original or "")
    if "/" in mime:
        sub = mime.split("/", 1)[1].split(";", 1)[0]
        ext = "." + {"jpeg": "jpg"}.get(sub, sub) if sub else ""
    return f"{str(img_row.id)[:8]}{ext}"


class _Node:
    """A resolved virtual node: a folder (dir), an image (file), or the
    synthetic neuthek welcome file (`virtual=True`)."""

    __slots__ = (
        "is_dir", "name", "folder_id", "image_id",
        "size", "mtime", "bucket", "key", "mime", "virtual",
    )

    def __init__(
        self,
        *,
        is_dir: bool,
        name: str,
        folder_id: Optional[UUID] = None,
        image_id: Optional[UUID] = None,
        size: int = 0,
        mtime: float = 0.0,
        bucket: Optional[str] = None,
        key: Optional[str] = None,
        mime: Optional[str] = None,
        virtual: bool = False,
    ) -> None:
        self.is_dir = is_dir
        self.name = name
        self.folder_id = folder_id
        self.image_id = image_id
        self.size = size
        self.mtime = mtime
        self.bucket = bucket
        self.key = key
        self.mime = mime
        # True for the synthetic neuthek files at the drive root — the
        # "Welcome to neuthek.txt" welcome note and the Windows-branding pair
        # (`neuthek.ico` + `desktop.ini`). All are served from memory, are
        # never an Image row, never billed, and never writable/removable/
        # renamable. The body served on `open` is selected by the node's name.
        self.virtual = virtual


def _welcome_node() -> _Node:
    """Build the virtual welcome-file node shown at the drive root. Sized to
    its in-memory body; carries `virtual=True` so `open` serves it from RAM
    and the write path refuses to touch it. It is NEVER an Image row, so it
    can't appear in usage / quota math."""
    body = _welcome_file_body()
    return _Node(
        is_dir=False,
        name=_WELCOME_FILENAME,
        size=len(body),
        mtime=time.time(),
        mime="text/plain; charset=utf-8",
        virtual=True,
    )


def _icon_node() -> _Node:
    """Build the virtual `neuthek.ico` node shown at the drive root. Sized to
    the generated icon bytes; `virtual=True` so `open` serves it from RAM and
    the write path refuses to touch it. NEVER an Image row → no quota impact."""
    body = _neuthek_icon_bytes()
    return _Node(
        is_dir=False,
        name=_ICON_FILENAME,
        size=len(body),
        mtime=time.time(),
        mime="image/x-icon",
        virtual=True,
    )


def _desktop_ini_node() -> _Node:
    """Build the virtual `desktop.ini` node shown at the drive root. Sized to
    its in-memory body; `virtual=True` so `open` serves it from RAM and the
    write path refuses to touch it. NEVER an Image row → no quota impact."""
    body = _desktop_ini_body()
    return _Node(
        is_dir=False,
        name=_DESKTOP_INI_FILENAME,
        size=len(body),
        mtime=time.time(),
        mime="text/plain; charset=utf-8",
        virtual=True,
    )


def _virtual_body(name: str) -> bytes:
    """Return the in-memory bytes a virtual root node serves on read, selected
    by the node's name. Single source of truth shared by the node builders'
    sizing and the read `open` path so size and content can never drift."""
    if name == _ICON_FILENAME:
        return _neuthek_icon_bytes()
    if name == _DESKTOP_INI_FILENAME:
        return _desktop_ini_body()
    return _welcome_file_body()


# All synthetic root-only branding filenames, for the listing-injection and
# write-guard checks below. Membership in this set is the single test for
# "is this a reserved virtual root name?".
_VIRTUAL_ROOT_NAMES = frozenset(
    {_WELCOME_FILENAME, _ICON_FILENAME, _DESKTOP_INI_FILENAME}
)


def _blob_source(img_row) -> tuple[str, Optional[str], int]:
    """Resolve (bucket, key, size) for the BEST available bytes of an image.
    Prefer the retained original blob; fall back to the served blob."""
    if img_row.original_blob_key:
        size = img_row.byte_size_original or img_row.byte_size_served or 0
        return storage.bucket_originals, img_row.original_blob_key, int(size or 0)
    size = img_row.byte_size_served or img_row.byte_size_original or 0
    return storage.bucket_served, img_row.served_blob_key, int(size or 0)


async def _list_dir(session, user_id: UUID, folder_id: Optional[UUID]) -> list[_Node]:
    """Return the child nodes of a directory (folder_id None = root).
    Subfolders -> dir nodes; images directly in this folder -> file nodes.
    Names are de-duplicated deterministically. Both queries pin `user_id`
    AND rely on RLS."""
    nodes: list[_Node] = []
    used: dict[str, int] = {}

    def _unique(base: str, disambig: str) -> str:
        if base not in used:
            used[base] = 1
            return base
        used[base] += 1
        stem, dot, ext = base.partition(".")
        if dot:
            return f"{stem} ({disambig}).{ext}"
        return f"{base} ({disambig})"

    fq = select(
        Folder.id, Folder.name, Folder.updated_at, Folder.created_at,
    ).where(
        Folder.user_id == user_id,
        Folder.deleted_at.is_(None),
        (Folder.parent_folder_id == folder_id)
        if folder_id is not None
        else Folder.parent_folder_id.is_(None),
    ).order_by(Folder.name)
    for fid, fname, updated_at, created_at in (await session.execute(fq)).all():
        nm = _unique(_safe_name(fname), str(fid)[:8])
        mt = (updated_at or created_at)
        nodes.append(
            _Node(
                is_dir=True,
                name=nm,
                folder_id=fid,
                mtime=mt.timestamp() if mt else time.time(),
            )
        )

    iq = select(
        Image.id,
        Image.original_filename,
        Image.original_blob_key,
        Image.served_blob_key,
        Image.byte_size_original,
        Image.byte_size_served,
        Image.mime_type_original,
        Image.mime_type_served,
        Image.uploaded_at,
        Image.captured_at,
    ).where(
        Image.user_id == user_id,
        Image.deleted_at.is_(None),
        (Image.folder_id == folder_id)
        if folder_id is not None
        else Image.folder_id.is_(None),
    ).order_by(Image.original_filename.nulls_last(), Image.uploaded_at)
    for row in (await session.execute(iq)).all():
        bucket, key, size = _blob_source(row)
        if not key:
            continue
        nm = _unique(_image_filename(row), str(row.id)[:8])
        mt = (row.captured_at or row.uploaded_at)
        nodes.append(
            _Node(
                is_dir=False,
                name=nm,
                image_id=row.id,
                size=size,
                mtime=mt.timestamp() if mt else time.time(),
                bucket=bucket,
                key=key,
                mime=(row.mime_type_served or row.mime_type_original),
            )
        )

    # neuthek branding: surface the read-only virtual files at the ROOT only —
    # the welcome note plus the Windows folder-branding pair (neuthek.ico +
    # desktop.ini). Each is added ONLY if no real file/folder already owns that
    # exact name (the user's own data always wins — we never shadow or rename
    # their file). They're appended last and `virtual=True`, so they can't
    # displace a real node and never enter quota math.
    if folder_id is None:
        existing_names = {n.name for n in nodes}
        for builder, fname in (
            (_welcome_node, _WELCOME_FILENAME),
            (_icon_node, _ICON_FILENAME),
            (_desktop_ini_node, _DESKTOP_INI_FILENAME),
        ):
            if fname not in existing_names:
                nodes.append(builder())
                existing_names.add(fname)

    return nodes


def _norm_parts(path: str) -> list[str]:
    """Normalize a virtual path into clean components, resolving `.`/`..`
    purely (never touches the host FS).

    Splits on `/` DIRECTLY rather than via PurePosixPath: pathlib treats a
    leading `//` as a single significant POSIX component, so the old
    `PurePosixPath("/" + path)` turned every absolute client path (which
    already begins with `/`) into `//...` and produced a spurious `'//'`
    leading component — breaking resolution of `/` and every path beneath
    it. Plain string splitting is unambiguous and traversal-safe: empty
    segments (from leading/duplicate slashes) and `.` are dropped, `..`
    pops the last component (and can never escape above root because we
    only pop when the stack is non-empty)."""
    parts: list[str] = []
    for comp in (path or "").replace("\\", "/").split("/"):
        comp = comp.strip()
        if comp in ("", "."):
            continue
        if comp == "..":
            if parts:
                parts.pop()
            continue
        parts.append(comp)
    return parts


async def _resolve_path(
    session, user_id: UUID, path: str,
) -> Optional[_Node]:
    """Resolve a virtual absolute path to a node, walking the folder tree
    component-by-component against the DB. Returns None if not found.
    NEVER trusts a name beyond matching it against this user's own rows."""
    parts = _norm_parts(path)
    if not parts:
        return _Node(is_dir=True, name="/", folder_id=None, mtime=time.time())

    current = _Node(is_dir=True, name="/", folder_id=None)
    for part in parts:
        if not current.is_dir:
            return None
        children = await _list_dir(session, user_id, current.folder_id)
        match = next((c for c in children if c.name == part), None)
        if match is None:
            return None
        current = match
    return current


async def _resolve_dir(
    session, user_id: UUID, parts: list[str],
) -> Optional[_Node]:
    """Resolve a list of components to a DIRECTORY node, or None. Empty
    list -> the synthetic root."""
    if not parts:
        return _Node(is_dir=True, name="/", folder_id=None, mtime=time.time())
    node = await _resolve_path(session, user_id, "/" + "/".join(parts))
    if node is None or not node.is_dir:
        return None
    return node


# ---------------------------------------------------------------------------
# asyncssh SFTP server
# ---------------------------------------------------------------------------
def _attrs_for(node: _Node, *, writable: bool) -> asyncssh.SFTPAttrs:
    """Build SFTPAttrs for a node. When the volume is writable we advertise
    write bits so clients permit uploads/edits; read-only mode advertises
    r-x / r-- only."""
    if node.is_dir:
        permissions = stat_module.S_IFDIR | (0o700 if writable else 0o500)
        size = 0
    else:
        permissions = stat_module.S_IFREG | (0o600 if writable else 0o400)
        size = node.size
    return asyncssh.SFTPAttrs(
        size=size,
        permissions=permissions,
        uid=0,
        gid=0,
        atime=int(node.mtime) if node.mtime else int(time.time()),
        mtime=int(node.mtime) if node.mtime else int(time.time()),
    )


class _Denied(asyncssh.SFTPError):
    def __init__(self, op: str, reason: str = "") -> None:
        msg = reason or f"operation not permitted ({op})"
        super().__init__(asyncssh.FX_PERMISSION_DENIED, msg)


class _BlobFile:
    """An open read handle backed by a MinIO blob, read via ranged GETs."""

    is_write = False

    def __init__(self, bucket: str, key: str, size_hint: int) -> None:
        self._bucket = bucket
        self._key = key
        self._size = size_hint
        self._size_confirmed = False

    async def _ensure_size(self) -> None:
        if self._size_confirmed:
            return
        try:
            real = await asyncio.to_thread(storage.stat, self._bucket, self._key)
            self._size = int(real)
        except Exception:
            pass
        self._size_confirmed = True

    async def read(self, offset: int, size: int) -> bytes:
        await self._ensure_size()
        if offset >= self._size:
            return b""
        length = min(size, self._size - offset)
        if length <= 0:
            return b""
        return await asyncio.to_thread(
            storage.get_range, self._bucket, self._key, offset, length,
        )

    async def close(self) -> None:
        return None


class _MemoryFile:
    """An open READ handle over an in-memory byte string. Backs the virtual
    "Welcome to neuthek.txt" — no MinIO blob, no DB row, just bytes served
    by offset. Same read/close surface as `_BlobFile` so the server's read
    path treats it identically."""

    is_write = False

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._size = len(data)

    async def read(self, offset: int, size: int) -> bytes:
        if offset >= self._size or size <= 0:
            return b""
        return self._data[offset:offset + size]

    async def close(self) -> None:
        return None


class _UploadFile:
    """An open WRITE handle. Buffers incoming bytes to a spooled temp file
    (RAM up to a threshold, then disk) so a multi-GB upload doesn't sit in
    Python memory, then on `close()` funnels the assembled bytes through
    the SAME `store_upload` pipeline the web uploader uses.

    The upload is committed exactly once, on close. If the client aborts
    (connection drops) before close, `abort()` discards the temp file and
    nothing is stored — a half-upload never becomes a partial Image row.
    """

    is_write = True
    # Spool to disk after 8 MiB in RAM.
    _SPOOL_MAX = 8 * 1024 * 1024

    def __init__(
        self,
        *,
        user_id: UUID,
        folder_id: Optional[UUID],
        filename: str,
        max_bytes: int,
    ) -> None:
        self._user_id = user_id
        self._folder_id = folder_id
        self._filename = filename
        self._max_bytes = max_bytes
        self._buf = tempfile.SpooledTemporaryFile(
            max_size=self._SPOOL_MAX, mode="w+b",
        )
        self._size = 0
        self._closed = False
        self._aborted = False

    async def write(self, offset: int, data: bytes) -> int:
        if self._aborted:
            raise asyncssh.SFTPError(asyncssh.FX_FAILURE, "upload aborted")
        # Enforce the per-file cap as bytes arrive so an over-cap stream is
        # killed early rather than after buffering gigabytes.
        end = offset + len(data)
        if end > self._max_bytes:
            self._aborted = True
            try:
                self._buf.close()
            except Exception:
                pass
            raise asyncssh.SFTPError(
                asyncssh.FX_FAILURE,
                f"file exceeds the {self._max_bytes:,}-byte upload limit",
            )
        # Place the bytes at their offset. Nearly all clients write
        # sequentially from 0; we still honor sparse/seeked writes.
        await asyncio.to_thread(self._buf.seek, offset)
        await asyncio.to_thread(self._buf.write, data)
        if end > self._size:
            self._size = end
        return len(data)

    def _read_all(self) -> bytes:
        self._buf.seek(0)
        return self._buf.read()

    async def commit(self, server: "NeuthekSFTPServer") -> None:
        """Funnel the buffered bytes through quota + store_upload. Called by
        the server's close() handler so the heavy work lives in one place."""
        if self._closed or self._aborted:
            return
        self._closed = True
        try:
            raw = await asyncio.to_thread(self._read_all)
        finally:
            try:
                self._buf.close()
            except Exception:
                pass
        if not raw:
            # Empty file (a `touch`-style 0-byte create). store_upload
            # rejects empty bytes; treat as a no-op success rather than an
            # error so `touch` doesn't blow up. Nothing is stored.
            return
        await server._commit_upload(
            user_id=self._user_id,
            folder_id=self._folder_id,
            filename=self._filename,
            raw=raw,
        )

    def abort(self) -> None:
        self._aborted = True
        try:
            self._buf.close()
        except Exception:
            pass

    async def close(self) -> None:
        # Real commit happens via the server's close() (which has the
        # server context). This is the handle-level fallback.
        return None


class NeuthekSFTPServer(asyncssh.SFTPServer):
    """Maps one authenticated neuthek user's files to an SFTP filesystem.
    One instance per SSH connection. The user_id is taken from the
    connection that auth bound."""

    def __init__(self, chan) -> None:
        super().__init__(chan)
        conn = chan.get_connection()
        self._user_id: Optional[UUID] = getattr(conn, "_neuthek_user_id", None)
        self._user_email: Optional[str] = getattr(conn, "_neuthek_user_email", None)
        self._read_only = _read_only()
        # Per-connection rate buckets.
        self._ops_bucket = _TokenBucket(_max_ops_per_sec(), _max_ops_per_sec())
        self._bytes_bucket = _TokenBucket(
            _max_bytes_per_sec(), _max_bytes_per_sec(),
        )
        # Per-connection cumulative-transfer ceiling (reads + writes). Once a
        # session has moved this many bytes it's cut off and must reconnect.
        self._session_bytes_cap = _max_session_bytes()
        self._session_bytes_used = 0

    # ---- helpers -------------------------------------------------------
    def _decode(self, path) -> str:
        if isinstance(path, bytes):
            return path.decode("utf-8", errors="replace")
        return str(path)

    def _require_user(self) -> UUID:
        if self._user_id is None:
            raise asyncssh.SFTPError(
                asyncssh.FX_PERMISSION_DENIED, "not authenticated"
            )
        return self._user_id

    async def _op(self) -> None:
        """Charge one operation against the per-connection ops bucket."""
        await self._ops_bucket.take(1)

    def _charge_session_bytes(self, n: int) -> None:
        """Add `n` to this connection's cumulative-transfer tally and abort
        the session (FX_FAILURE) once it crosses the per-session cap. Counts
        reads AND writes so neither direction can run unbounded on a single
        authentication. A cap of <=0 disables the ceiling."""
        if self._session_bytes_cap <= 0:
            return
        self._session_bytes_used += max(0, n)
        if self._session_bytes_used > self._session_bytes_cap:
            raise asyncssh.SFTPError(
                asyncssh.FX_FAILURE,
                "per-session transfer limit reached "
                f"({self._session_bytes_cap:,} bytes) — reconnect to continue",
            )

    def _deny_if_read_only(self, op: str) -> None:
        if self._read_only:
            raise _Denied(op, "this SFTP mount is read-only")

    # ---- read-side operations -----------------------------------------
    async def realpath(self, path) -> bytes:
        parts = _norm_parts(self._decode(path))
        canonical = "/" + "/".join(parts)
        return canonical.encode("utf-8")

    async def stat(self, path) -> asyncssh.SFTPAttrs:
        await self._op()
        user_id = self._require_user()
        spath = self._decode(path)
        async with _UserSession(user_id) as session:
            node = await _resolve_path(session, user_id, spath)
        if node is None:
            raise asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "No such file")
        return _attrs_for(node, writable=not self._read_only)

    async def lstat(self, path) -> asyncssh.SFTPAttrs:
        return await self.stat(path)

    async def fstat(self, file_obj) -> asyncssh.SFTPAttrs:
        size = getattr(file_obj, "_size", 0)
        writable = not self._read_only
        return asyncssh.SFTPAttrs(
            size=size,
            permissions=stat_module.S_IFREG | (0o600 if writable else 0o400),
            uid=0, gid=0,
            atime=int(time.time()), mtime=int(time.time()),
        )

    async def open(self, path, pflags, attrs):
        await self._op()
        user_id = self._require_user()
        spath = self._decode(path)
        write_mask = (
            asyncssh.FXF_WRITE
            | asyncssh.FXF_CREAT
            | asyncssh.FXF_TRUNC
            | asyncssh.FXF_APPEND
            | asyncssh.FXF_EXCL
        )
        wants_write = bool(pflags & write_mask)

        if not wants_write:
            # READ open.
            async with _UserSession(user_id) as session:
                node = await _resolve_path(session, user_id, spath)
                if node is None:
                    raise asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "No such file")
                if node.is_dir:
                    raise asyncssh.SFTPError(asyncssh.FX_FAILURE, "Is a directory")
            # The virtual root files (welcome note, neuthek.ico, desktop.ini)
            # are served from memory — no MinIO blob. The body is picked by the
            # node's name so the icon and the .ini each serve their own bytes.
            if node.virtual:
                return _MemoryFile(_virtual_body(node.name))
            return _BlobFile(node.bucket, node.key, node.size)

        # WRITE open.
        self._deny_if_read_only("open-for-write")
        parts = _norm_parts(spath)
        if not parts:
            raise _Denied("open-for-write", "cannot write the root directory")
        filename = parts[-1]
        parent_parts = parts[:-1]

        # The virtual root files (welcome note, neuthek.ico, desktop.ini) are
        # read-only — never let a write create/overwrite the synthetic node
        # (none has an Image row to back it, and the user shouldn't be able to
        # clobber the markers). These names live ONLY at the root, so the guard
        # is scoped to the root (no parent). Crucially, if a REAL same-named
        # user file exists, `_list_dir` lists that real file (not the virtual
        # node) and the user is entitled to overwrite their own data — so we
        # only refuse when the path resolves to the virtual node (i.e. there is
        # no real file shadowing the name).
        if not parent_parts and filename in _VIRTUAL_ROOT_NAMES:
            async with _UserSession(user_id) as session:
                existing = await _resolve_path(session, user_id, spath)
            if existing is None or existing.virtual:
                raise _Denied(
                    "open-for-write",
                    f"{filename!r} is a read-only neuthek file",
                )

        # Append is not representable in the store-once model (each upload
        # is an immutable Image). Reject it clearly rather than silently
        # truncating.
        if pflags & asyncssh.FXF_APPEND:
            raise _Denied(
                "open-for-append",
                "append is not supported — re-upload the whole file",
            )

        async with _UserSession(user_id) as session:
            parent = await _resolve_dir(session, user_id, parent_parts)
            if parent is None:
                raise asyncssh.SFTPError(
                    asyncssh.FX_NO_SUCH_FILE,
                    "Destination folder does not exist",
                )
            # FXF_EXCL means "fail if it already exists". Honor it against
            # the user's own rows.
            if pflags & asyncssh.FXF_EXCL:
                existing = await _resolve_path(session, user_id, spath)
                if existing is not None:
                    raise asyncssh.SFTPError(
                        asyncssh.FX_FAILURE, "File already exists"
                    )
            parent_folder_id = parent.folder_id

        return _UploadFile(
            user_id=user_id,
            folder_id=parent_folder_id,
            filename=_safe_name(filename),
            max_bytes=settings.upload_max_bytes,
        )

    async def read(self, file_obj, offset, size) -> bytes:
        # Throttle read bandwidth, then serve the range.
        await self._bytes_bucket.take(min(size, _max_bytes_per_sec()))
        data = await file_obj.read(offset, size)
        # Charge the bytes actually returned against the per-session ceiling
        # (a short read at EOF must not over-count toward the cap).
        self._charge_session_bytes(len(data))
        return data

    async def write(self, file_obj, offset, data) -> int:
        self._deny_if_read_only("write")
        if not getattr(file_obj, "is_write", False):
            raise _Denied("write", "file not opened for writing")
        # Count the incoming bytes toward the per-session ceiling BEFORE
        # buffering them, so an over-cap stream is cut off promptly.
        self._charge_session_bytes(len(data))
        await self._bytes_bucket.take(min(len(data), _max_bytes_per_sec()))
        return await file_obj.write(offset, data)

    async def close(self, file_obj) -> None:
        # For a write handle, this is where the upload is committed through
        # store_upload (quota + validation). For a read handle it's a no-op.
        if getattr(file_obj, "is_write", False):
            await file_obj.commit(self)
            return
        close = getattr(file_obj, "close", None)
        if close is not None:
            res = close()
            if asyncio.iscoroutine(res):
                await res

    async def scandir(self, path):
        await self._op()
        user_id = self._require_user()
        spath = self._decode(path)
        async with _UserSession(user_id) as session:
            node = await _resolve_path(session, user_id, spath)
            if node is None:
                raise asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "No such file")
            if not node.is_dir:
                raise asyncssh.SFTPError(asyncssh.FX_FAILURE, "Not a directory")
            children = await _list_dir(session, user_id, node.folder_id)

        writable = not self._read_only
        dir_attrs = asyncssh.SFTPAttrs(
            permissions=stat_module.S_IFDIR | (0o700 if writable else 0o500),
            size=0, uid=0, gid=0,
            atime=int(time.time()), mtime=int(time.time()),
        )
        for dot in (".", ".."):
            yield asyncssh.SFTPName(
                filename=dot.encode(),
                longname=_longname(dot, dir_attrs, is_dir=True, writable=writable).encode(),
                attrs=dir_attrs,
            )
        for c in children:
            attrs = _attrs_for(c, writable=writable)
            yield asyncssh.SFTPName(
                filename=c.name.encode("utf-8"),
                longname=_longname(
                    c.name, attrs, is_dir=c.is_dir, writable=writable,
                ).encode("utf-8"),
                attrs=attrs,
            )

    # ---- write-side operations ----------------------------------------
    async def _commit_upload(
        self,
        *,
        user_id: UUID,
        folder_id: Optional[UUID],
        filename: str,
        raw: bytes,
    ) -> None:
        """Store an SFTP-uploaded file via the SAME pipeline as a web
        upload: enforce_storage_quota -> store_upload (validate_upload +
        EXIF/metadata strip + blob writes + Image row). Per-user RLS is on
        for the whole transaction."""
        from backend.api.storage import enforce_storage_quota
        from backend.image import store_upload
        from backend.upload_validation import UploadValidationError

        async with _UserSession(user_id) as session:
            user = await session.get(User, user_id)
            if user is None:
                raise asyncssh.SFTPError(asyncssh.FX_PERMISSION_DENIED, "no such user")
            # Quota gate FIRST (raises HTTP 413 → map to SFTP failure).
            try:
                await enforce_storage_quota(session, user, len(raw))
            except Exception as exc:
                detail = getattr(exc, "detail", None) or str(exc)
                raise asyncssh.SFTPError(asyncssh.FX_FAILURE, f"quota: {detail}")
            try:
                # content_type=None → store_upload sniffs magic bytes itself
                # (the SFTP protocol gives us no MIME), exactly like a web
                # upload with a generic Content-Type.
                await store_upload(
                    session, user, filename, raw, None,
                    folder_id=folder_id,
                )
            except UploadValidationError as exc:
                raise asyncssh.SFTPError(
                    asyncssh.FX_FAILURE,
                    f"rejected: {getattr(exc, 'detail', str(exc))}",
                )
            except asyncssh.SFTPError:
                raise
            except Exception:
                logger.exception(
                    "sftp.upload.store_failed user=%s file=%s", user_id, filename,
                )
                raise asyncssh.SFTPError(
                    asyncssh.FX_FAILURE, "upload failed to store",
                )
        await _audit_sftp(user_id, "sftp.upload.ok", "-", filename)
        logger.info("sftp.upload.ok user=%s file=%s bytes=%d", user_id, filename, len(raw))

    async def mkdir(self, path, attrs):
        await self._op()
        self._deny_if_read_only("mkdir")
        user_id = self._require_user()
        parts = _norm_parts(self._decode(path))
        if not parts:
            raise _Denied("mkdir", "cannot create the root")
        name = parts[-1].strip()
        if not name or len(name) > 200:
            raise asyncssh.SFTPError(asyncssh.FX_FAILURE, "invalid folder name")
        parent_parts = parts[:-1]

        async with _UserSession(user_id) as session:
            parent = await _resolve_dir(session, user_id, parent_parts)
            if parent is None:
                raise asyncssh.SFTPError(
                    asyncssh.FX_NO_SUCH_FILE, "Parent folder does not exist"
                )
            # Don't let a new folder bury a virtual root marker (welcome note,
            # neuthek.ico, desktop.ini). Scoped to the root and only while the
            # name still resolves to the synthetic node — if a real same-named
            # node already exists the normal uniqueness handling applies, so a
            # user's pre-existing data is never blocked by this.
            if not parent_parts and name in _VIRTUAL_ROOT_NAMES:
                existing = await _resolve_path(session, user_id, "/" + name)
                if existing is None or existing.virtual:
                    raise _Denied(
                        "mkdir",
                        f"{name!r} is reserved by neuthek — pick another name",
                    )
            folder = Folder(
                user_id=user_id,
                parent_folder_id=parent.folder_id,
                name=name,
            )
            session.add(folder)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise asyncssh.SFTPError(
                    asyncssh.FX_FAILURE,
                    "a folder with that name already exists here",
                )
        await _audit_sftp(user_id, "sftp.mkdir.ok", "-", name)
        logger.info("sftp.mkdir.ok user=%s name=%s", user_id, name)

    async def rmdir(self, path):
        await self._op()
        self._deny_if_read_only("rmdir")
        user_id = self._require_user()
        spath = self._decode(path)
        from datetime import datetime, timezone

        async with _UserSession(user_id) as session:
            node = await _resolve_path(session, user_id, spath)
            if node is None or not node.is_dir or node.folder_id is None:
                raise asyncssh.SFTPError(
                    asyncssh.FX_NO_SUCH_FILE, "No such directory"
                )
            now = datetime.now(timezone.utc)
            folder_id = node.folder_id
            # Collect the subtree (this folder + every descendant) via a
            # recursive walk, then soft-delete folders + their images. This
            # mirrors the HTTP folder-delete cascade: nothing is hard-
            # deleted; everything lands in Trash where the user can restore.
            subtree = await _collect_subtree(session, user_id, folder_id)
            await session.execute(
                update(Folder)
                .where(Folder.id.in_(subtree), Folder.user_id == user_id)
                .values(deleted_at=now, updated_at=now)
            )
            await session.execute(
                update(Image)
                .where(
                    Image.folder_id.in_(subtree),
                    Image.user_id == user_id,
                    Image.deleted_at.is_(None),
                )
                .values(deleted_at=now)
            )
            await session.commit()
        await _audit_sftp(user_id, "sftp.rmdir.ok", "-", str(folder_id))
        logger.info("sftp.rmdir.ok user=%s folder=%s", user_id, folder_id)

    async def remove(self, path):
        await self._op()
        self._deny_if_read_only("remove")
        user_id = self._require_user()
        spath = self._decode(path)
        from datetime import datetime, timezone

        async with _UserSession(user_id) as session:
            node = await _resolve_path(session, user_id, spath)
            # The virtual root files (welcome note, neuthek.ico, desktop.ini)
            # can't be deleted — none is a real row. (The generic check below
            # would already reject them via `image_id is None`; this gives a
            # clearer message.)
            if node is not None and node.virtual:
                raise _Denied(
                    "remove",
                    f"{node.name!r} is a read-only neuthek file",
                )
            if node is None or node.is_dir or node.image_id is None:
                raise asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "No such file")
            now = datetime.now(timezone.utc)
            # Soft-delete (Trash) — same semantics as the web "delete"
            # action; the retention sweeper hard-deletes bytes after the
            # grace window. Reversible, never a surprise data loss.
            await session.execute(
                update(Image)
                .where(Image.id == node.image_id, Image.user_id == user_id)
                .values(deleted_at=now)
            )
            await session.commit()
            image_id = node.image_id
        await _audit_sftp(user_id, "sftp.remove.ok", "-", str(image_id))
        logger.info("sftp.remove.ok user=%s image=%s", user_id, image_id)

    async def rename(self, oldpath, newpath):
        await self._op()
        self._deny_if_read_only("rename")
        user_id = self._require_user()
        await self._do_rename(user_id, self._decode(oldpath), self._decode(newpath))

    async def posix_rename(self, oldpath, newpath):
        # The OpenSSH `posix-rename@openssh.com` extension most clients use.
        await self.rename(oldpath, newpath)

    async def _do_rename(self, user_id: UUID, old: str, new: str) -> None:
        from backend.upload_validation import (
            UploadValidationError,
            validate_image_filename,
        )

        old_parts = _norm_parts(old)
        new_parts = _norm_parts(new)
        if not old_parts or not new_parts:
            raise _Denied("rename", "cannot rename the root")
        new_name = new_parts[-1].strip()
        new_parent_parts = new_parts[:-1]

        async with _UserSession(user_id) as session:
            node = await _resolve_path(session, user_id, old)
            if node is None:
                raise asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "No such file")
            # The virtual root files (welcome note, neuthek.ico, desktop.ini)
            # are read-only: they can't be renamed/moved (none has a row), and
            # nothing may be renamed ONTO a reserved root name while it's still
            # the synthetic marker (that would shadow it). The user's real files
            # are never affected — this only protects the synthetic nodes, and
            # only when no real same-named file exists at the root.
            if node.virtual:
                raise _Denied(
                    "rename",
                    f"{node.name!r} is a read-only neuthek file",
                )
            if not new_parent_parts and new_name in _VIRTUAL_ROOT_NAMES:
                dest = await _resolve_path(session, user_id, new)
                if dest is None or dest.virtual:
                    raise _Denied(
                        "rename",
                        f"{new_name!r} is reserved by neuthek — pick another name",
                    )
            new_parent = await _resolve_dir(session, user_id, new_parent_parts)
            if new_parent is None:
                raise asyncssh.SFTPError(
                    asyncssh.FX_NO_SUCH_FILE, "Destination folder does not exist"
                )

            if node.is_dir:
                if node.folder_id is None:
                    raise _Denied("rename", "cannot rename the root")
                if not new_name or len(new_name) > 200:
                    raise asyncssh.SFTPError(
                        asyncssh.FX_FAILURE, "invalid folder name"
                    )
                # Prevent moving a folder into itself / its own subtree.
                if new_parent.folder_id is not None:
                    subtree = await _collect_subtree(session, user_id, node.folder_id)
                    if new_parent.folder_id in subtree:
                        raise asyncssh.SFTPError(
                            asyncssh.FX_FAILURE,
                            "cannot move a folder into its own subtree",
                        )
                from datetime import datetime, timezone
                await session.execute(
                    update(Folder)
                    .where(Folder.id == node.folder_id, Folder.user_id == user_id)
                    .values(
                        name=new_name,
                        parent_folder_id=new_parent.folder_id,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    raise asyncssh.SFTPError(
                        asyncssh.FX_FAILURE,
                        "a folder with that name already exists at the destination",
                    )
            else:
                if node.image_id is None:
                    raise asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "No such file")
                row = await session.get(Image, node.image_id)
                if row is None or row.user_id != user_id:
                    raise asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "No such file")
                # Validate the new filename through the SAME rule set the
                # web rename uses (extension preserved, no traversal/
                # reserved names, length cap). A bare move (same name) is
                # allowed even if validation of an unchanged tricky name
                # would trip — so we only validate when the name changed.
                final_name = row.original_filename
                if new_name and new_name != (row.original_filename or ""):
                    try:
                        final_name = validate_image_filename(
                            new_name, row.original_filename,
                        )
                    except UploadValidationError as exc:
                        raise asyncssh.SFTPError(
                            asyncssh.FX_FAILURE,
                            f"invalid filename: {getattr(exc, 'detail', str(exc))}",
                        )
                row.original_filename = final_name
                row.folder_id = new_parent.folder_id
                await session.commit()
        await _audit_sftp(user_id, "sftp.rename.ok", "-", f"{old} -> {new}")
        logger.info("sftp.rename.ok user=%s %s -> %s", user_id, old, new)

    async def symlink(self, oldpath, newpath):
        raise _Denied("symlink", "symlinks are not supported")

    async def readlink(self, path):
        raise asyncssh.SFTPError(asyncssh.FX_NO_SUCH_FILE, "Not a symlink")

    async def setstat(self, path, attrs):
        # Clients (sshfs / WinSCP / sftp `put`) routinely chmod / set times
        # on a file right after creating it. We don't model POSIX
        # permissions or mtime on our virtual FS, so accept-and-ignore: a
        # no-op success keeps `put` from failing while changing nothing.
        # In read-only mode, reject so the mount is honestly read-only.
        if self._read_only:
            raise _Denied("setstat", "this SFTP mount is read-only")
        return None

    async def fsetstat(self, file_obj, attrs):
        if self._read_only:
            raise _Denied("fsetstat", "this SFTP mount is read-only")
        return None

    async def statvfs(self, path):
        flags = (
            asyncssh.FXE_STATVFS_ST_RDONLY if self._read_only else 0
        )
        return asyncssh.SFTPVFSAttrs(
            bsize=4096, frsize=4096, blocks=0, bfree=0, bavail=0,
            files=0, ffree=0, favail=0, fsid=0,
            flags=flags, namemax=255,
        )

    async def fstatvfs(self, file_obj):
        return await self.statvfs(b"/")


async def _collect_subtree(session, user_id: UUID, folder_id: UUID) -> list[UUID]:
    """Return [folder_id, *all descendant folder ids] for a user, walking
    `parent_folder_id` breadth-first against the user's own (RLS-fenced)
    rows. Bounded by a visited-set so a malformed cycle can't loop."""
    out: list[UUID] = [folder_id]
    frontier = [folder_id]
    seen = {folder_id}
    while frontier:
        rows = (
            await session.execute(
                select(Folder.id).where(
                    Folder.user_id == user_id,
                    Folder.deleted_at.is_(None),
                    Folder.parent_folder_id.in_(frontier),
                )
            )
        ).scalars().all()
        nxt = []
        for fid in rows:
            if fid not in seen:
                seen.add(fid)
                out.append(fid)
                nxt.append(fid)
        frontier = nxt
    return out


def _longname(
    name: str, attrs: asyncssh.SFTPAttrs, *, is_dir: bool, writable: bool,
) -> str:
    """Build an `ls -l`-style longname line some clients display."""
    if is_dir:
        perms = "drwx------" if writable else "dr-x------"
    else:
        perms = "-rw-------" if writable else "-r--------"
    size = attrs.size or 0
    return f"{perms} 1 neuthek neuthek {size:>12} Jan  1 00:00 {name}"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def _fingerprint_for_key(key: asyncssh.SSHKey) -> str:
    blob = key.public_data
    digest = hashlib.sha256(blob).digest()
    import base64
    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


async def _resolve_user_by_email(email: str) -> Optional[UUID]:
    """Look up the user_id for a login email. Runs with RLS BYPASS because
    no user context exists yet — the bypass is scoped to this lookup only;
    it never reaches a client."""
    if not email:
        return None
    token = set_rls_bypass(True)
    try:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(User.id, User.is_active).where(
                        User.email == email.lower()
                    )
                )
            ).first()
        if row is None:
            return None
        uid, is_active = row
        if not is_active:
            return None
        return uid
    finally:
        reset_rls_bypass(token)


async def _public_key_matches(user_id: UUID, key: asyncssh.SSHKey) -> bool:
    """True if `key` matches one of the user's authorized keys. Fingerprint
    first (indexed), then byte-equality of the public blob as the
    authoritative check."""
    fpr = _fingerprint_for_key(key)
    presented_blob = key.public_data
    token = set_rls_bypass(True)
    try:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(SftpAuthorizedKey.id, SftpAuthorizedKey.public_key)
                    .where(
                        SftpAuthorizedKey.user_id == user_id,
                        SftpAuthorizedKey.fingerprint == fpr,
                    )
                )
            ).all()
            for row_id, publine in rows:
                try:
                    stored = asyncssh.import_public_key(publine)
                except Exception:
                    continue
                if stored.public_data == presented_blob:
                    try:
                        await session.execute(
                            update(SftpAuthorizedKey)
                            .where(SftpAuthorizedKey.id == row_id)
                            .values(last_used_at=_now())
                        )
                        await session.commit()
                    except Exception:
                        pass
                    return True
        return False
    finally:
        reset_rls_bypass(token)


async def _password_matches(user_id: UUID, password: str) -> bool:
    """Verify an SFTP password against `sftp_credentials` (Argon2)."""
    if not password:
        return False
    from fastapi_users.password import PasswordHelper

    helper = PasswordHelper()
    token = set_rls_bypass(True)
    try:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(SftpCredential.password_hash).where(
                        SftpCredential.user_id == user_id
                    )
                )
            ).first()
        if row is None:
            return False
        ok, _ = helper.verify_and_update(password, row[0])
        if ok:
            token2 = set_rls_bypass(True)
            try:
                async with SessionLocal() as s2:
                    await s2.execute(
                        update(SftpCredential)
                        .where(SftpCredential.user_id == user_id)
                        .values(last_used_at=_now())
                    )
                    await s2.commit()
            except Exception:
                pass
            finally:
                reset_rls_bypass(token2)
        return ok
    finally:
        reset_rls_bypass(token)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


class NeuthekSSHServer(asyncssh.SSHServer):
    """Per-connection SSH server policy: resolves the username -> user_id,
    validates the public key / password, enforces per-IP lockout AND the
    per-user concurrent-session cap. Fail-closed everywhere."""

    def __init__(self) -> None:
        self._conn: Optional[asyncssh.SSHServerConnection] = None
        self._peer_ip: str = "unknown"
        self._username: str = ""
        self._user_id: Optional[UUID] = None
        self._session_reserved_for: Optional[UUID] = None

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        self._conn = conn
        try:
            peer = conn.get_extra_info("peername")
            if peer:
                self._peer_ip = peer[0]
        except Exception:
            pass

    def connection_lost(self, exc: Optional[Exception]) -> None:
        # Release the per-user session slot reserved at auth time.
        if self._session_reserved_for is not None:
            _release_session(self._session_reserved_for)
            self._session_reserved_for = None

    # --- lockout (shared Redis counters with the web auth path) ---------
    def _lock_key(self) -> str:
        scope = f"{self._username}:{self._peer_ip}" if self._username else self._peer_ip
        return f"auth:lock:sftp:{scope}"

    def _fail_key(self) -> str:
        scope = f"{self._username}:{self._peer_ip}" if self._username else self._peer_ip
        return f"auth:fail:sftp:{scope}"

    async def _is_locked(self) -> bool:
        if not settings.security_rate_limits_enabled:
            return False
        try:
            from backend.security import get_counter
            return bool(await get_counter(self._lock_key()))
        except Exception:
            return False

    async def _record_failure(self) -> None:
        if not settings.security_rate_limits_enabled:
            return
        try:
            from backend.security import increment_window
            failures = await increment_window(self._fail_key(), 24 * 3600)
            if failures >= settings.auth_lockout_failures:
                exponent = min(failures - settings.auth_lockout_failures, 4)
                seconds = min(
                    settings.auth_lockout_base_seconds * (2 ** exponent),
                    settings.auth_lockout_max_seconds,
                )
                await increment_window(self._lock_key(), seconds)
        except Exception:
            pass

    async def _clear_failures(self) -> None:
        try:
            from backend.security import clear_counter
            await clear_counter(self._fail_key())
            await clear_counter(self._lock_key())
        except Exception:
            pass

    def _bind_user(self, user_id: UUID, uname: str) -> bool:
        """Finalize a successful auth: enforce the per-user session cap and,
        if a slot is available, bind the user onto the connection. Returns
        False (auth denied) when the user is already at their session cap."""
        if not _try_acquire_session(user_id):
            logger.warning(
                "sftp.auth.session_cap ip=%s user=%s active=%d cap=%d",
                self._peer_ip, uname, _sessions_for(user_id),
                _max_sessions_per_user(),
            )
            return False
        self._session_reserved_for = user_id
        self._user_id = user_id
        if self._conn is not None:
            setattr(self._conn, "_neuthek_user_id", user_id)
            setattr(self._conn, "_neuthek_user_email", uname)
        return True

    # --- asyncssh auth hooks -------------------------------------------
    def begin_auth(self, username: str) -> bool:
        self._username = (username or "").strip().lower()
        # neuthek branding: push the auth banner the moment auth begins so
        # every client shows the wordmark + welcome line before/around the
        # credential prompt. Best-effort + wrapped — a banner failure must
        # NEVER block authentication.
        try:
            if self._conn is not None:
                self._conn.send_auth_banner(_auth_banner())
        except Exception:
            logger.debug("sftp: send_auth_banner failed", exc_info=True)
        return True  # auth always required

    def public_key_auth_supported(self) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def kbdint_auth_supported(self) -> bool:
        return False

    async def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        uname = (username or "").strip().lower()
        self._username = uname
        if await self._is_locked():
            logger.warning("sftp.auth.locked ip=%s user=%s", self._peer_ip, uname)
            return False
        try:
            user_id = await _resolve_user_by_email(uname)
            if user_id is None:
                await self._record_failure()
                logger.info("sftp.auth.pubkey.unknown_user ip=%s user=%s", self._peer_ip, uname)
                return False
            if await _public_key_matches(user_id, key):
                if not self._bind_user(user_id, uname):
                    return False  # at session cap
                await self._clear_failures()
                await _audit_sftp(user_id, "sftp.auth.pubkey.ok", self._peer_ip, uname)
                logger.info("sftp.auth.pubkey.ok ip=%s user=%s", self._peer_ip, uname)
                return True
            await self._record_failure()
            logger.info("sftp.auth.pubkey.no_match ip=%s user=%s", self._peer_ip, uname)
            return False
        except Exception:
            logger.exception("sftp.auth.pubkey.error ip=%s user=%s", self._peer_ip, uname)
            return False

    async def validate_password(self, username: str, password: str) -> bool:
        uname = (username or "").strip().lower()
        self._username = uname
        if await self._is_locked():
            logger.warning("sftp.auth.locked ip=%s user=%s", self._peer_ip, uname)
            return False
        try:
            user_id = await _resolve_user_by_email(uname)
            if user_id is None:
                await self._record_failure()
                logger.info("sftp.auth.pw.unknown_user ip=%s user=%s", self._peer_ip, uname)
                return False
            if await _password_matches(user_id, password):
                if not self._bind_user(user_id, uname):
                    return False  # at session cap
                await self._clear_failures()
                await _audit_sftp(user_id, "sftp.auth.password.ok", self._peer_ip, uname)
                logger.info("sftp.auth.pw.ok ip=%s user=%s", self._peer_ip, uname)
                return True
            await self._record_failure()
            logger.info("sftp.auth.pw.bad ip=%s user=%s", self._peer_ip, uname)
            return False
        except Exception:
            logger.exception("sftp.auth.pw.error ip=%s user=%s", self._peer_ip, uname)
            return False


async def _audit_sftp(user_id: Optional[UUID], action: str, ip: str, identity: str) -> None:
    """Best-effort audit row for an SFTP event. Mirrors the web auth audit
    shape so SFTP activity lands in the same audit_log table."""
    try:
        from backend.audit import add_audit
        token = set_rls_bypass(True)
        try:
            async with SessionLocal() as session:
                await add_audit(
                    session,
                    user_id=user_id,
                    action=action,
                    details={"ip": ip, "identity": identity, "transport": "sftp"},
                )
                await session.commit()
        finally:
            reset_rls_bypass(token)
    except Exception:
        return


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------
async def start_sftp_server() -> asyncssh.SSHAcceptor:
    host_key = load_or_create_host_key()
    host = _bind_host()
    port = _bind_port()

    acceptor = await asyncssh.create_server(
        NeuthekSSHServer,
        host,
        port,
        server_host_keys=[host_key],
        sftp_factory=NeuthekSFTPServer,
        # Do NOT enable a shell / exec / PTY — asyncssh only starts the SFTP
        # subsystem because we pass sftp_factory and no process factory;
        # session/exec requests are refused by default.
        allow_scp=False,
        login_timeout=30,
        keepalive_interval=30,
        keepalive_count_max=4,
    )
    logger.info(
        "neuthek SFTP server listening on %s:%s (mode=%s); host key %s; "
        "limits: sessions/user=%d ops/s=%.0f bytes/s=%.0f max-file=%d "
        "max-session-bytes=%d",
        host, port, "read-only" if _read_only() else "read+write",
        _host_key_path(), _max_sessions_per_user(), _max_ops_per_sec(),
        _max_bytes_per_sec(), settings.upload_max_bytes, _max_session_bytes(),
    )
    return acceptor


async def _amain() -> None:
    logging.basicConfig(
        level=os.getenv("SFTP_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    acceptor = await start_sftp_server()
    stop = asyncio.Event()

    def _sig(*_a):
        stop.set()

    try:
        import signal
        loop = asyncio.get_running_loop()
        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(s, _sig)
            except (NotImplementedError, RuntimeError):
                pass
    except Exception:
        pass

    await stop.wait()
    acceptor.close()
    await acceptor.wait_closed()
    logger.info("neuthek SFTP server stopped")


def main() -> None:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
