"""C2 cloud-sync worker — Google Drive (read-only).

Drive is the only provider currently wired up. Dropbox
hooks are kept as `CloudProvider` literal entries + scope dicts so
the same `connect_provider` / `complete_oauth` / `sync_user_provider`
interface can light them up later, but every entrypoint short-
circuits with `CloudSyncNotConfigured` for anything other than
`google_drive` today. GitHub used to be a second provider; it was
removed because public repos turned the gallery into a dumping
ground of READMEs + build configs.

End-to-end flow
---------------
1. **FE** calls `POST /cloud/links/google_drive`. The server returns
   an `auth_url` + `state` token; the FE redirects the user there.
2. **User** authenticates with Google and grants the `drive.readonly`
   scope.
3. **Google** redirects back to
   `GET /cloud/callback/google_drive?code=...&state=...`.
4. The callback exchanges `code` for an access + refresh token,
   encrypts the refresh token via `secret_box`, and persists a
   `cloud_links` row.
5. **FE** calls `POST /cloud/links/{id}/sync` (or a worker fires it
   on a cron); we list the user's Drive files, diff against
   `cloud_files`, download the new ones via the existing image upload
   pipeline, and update `last_synced_at`.

Configuration (settings — see `backend/config.py`):

  CLOUD_ENCRYPTION_KEY        Fernet key used to encrypt refresh tokens.
  GOOGLE_OAUTH_CLIENT_ID      from Google Cloud Console > APIs & Services > Credentials
  GOOGLE_OAUTH_CLIENT_SECRET  same place
  GOOGLE_OAUTH_REDIRECT_URI   must match the URL registered with Google

If any of those are missing, every entrypoint raises
`CloudSyncNotConfigured`; `backend/api/cloud.py` translates that to a
503 with a clear message so the FE can show "configure cloud sync
first" instead of a blank error.

Privacy / compliance notes
--------------------------
* **Pull-only.** We never write back to Drive — no upload, no rename,
  no delete. The user's source of truth stays untouched.
* **`drive.readonly`** scope only. We don't request `drive` (full
  access) even though it would be simpler — Google's Limited Use
  policy treats restricted scopes more strictly and we don't need
  the extra surface.
* **Limited Use policy.** Drive content cannot be used to train AI
  models per Google's terms. AI summary + face scan are skipped on
  files synced from Drive (`Image.skip_ai_training` flag, set by the
  ingest path) until the user opts in per-source.
* **Refresh tokens encrypted at rest** via `secret_box.encrypt`.
  Per-tenant KMS key rotation is on the A3 roadmap; today the master
  key lives in env / .env (which is .gitignored).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.config import settings
from backend.key_derivation import oauth_cloud_sync_state_key
from backend.models import CloudFile, CloudLink, Folder, Image, User
from backend.secret_box import (
    MisconfiguredEncryption,
    decrypt as decrypt_token,
    encrypt as encrypt_token,
)


def _build_state(user_id: UUID) -> str:
    """HMAC-signed OAuth state: `<user_id>.<nonce>.<hex_mac>`.

    The state parameter is the only thing tying the callback back to a
    specific user. Without a signature, an attacker could craft a
    callback URL with `state=<victim_uuid>&code=<attacker_code>` and
    bind their own Google Drive to the victim's neuthek account —
    OAuth CSRF. The HMAC ensures the state truly originated from a
    /cloud/links/{provider} call we authorized.
    """
    nonce = secrets.token_urlsafe(16)
    payload = f"{user_id}.{nonce}"
    # CR-3: distinct subkey from the Google sign-in state HMAC. Before
    # this both flows used `settings.jwt_secret`; the state shapes were
    # different but the verifier in one path could be tricked by a
    # state minted in the other under sufficiently bad future refactors
    # (confused-deputy). Subkey separation is the structural fix.
    mac = hmac.new(
        oauth_cloud_sync_state_key(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{mac}"


def _verify_state(state: str) -> UUID:
    """Return the user_id encoded in a signed state, or raise ValueError."""
    if not isinstance(state, str) or state.count(".") != 2:
        raise ValueError("Malformed state")
    user_id_str, nonce, mac = state.split(".", 2)
    payload = f"{user_id_str}.{nonce}"
    expected = hmac.new(
        oauth_cloud_sync_state_key(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise ValueError("State signature mismatch")
    return UUID(user_id_str)

logger = logging.getLogger(__name__)

CloudProvider = Literal[
    "google_drive", "dropbox", "icloud", "proton_drive", "mega",
]


PROVIDER_SCOPES: dict[CloudProvider, list[str]] = {
    # Drive's `drive.readonly` is the smallest scope that lists+downloads
    # the user's files. We deliberately avoid `drive` (full r/w) so we
    # can never silently corrupt the user's source.
    #
    # The `openid email profile` triplet is appended so the same OAuth
    # grant doubles as a Google sign-in link: the token response
    # carries an id_token we can decode, capture the `sub`, and stamp
    # onto the user's `google_sub` column. Then a later "Sign in with
    # Google" lands the same person back in the same neuthek account
    # instead of forking a new one.
    "google_drive": [
        "https://www.googleapis.com/auth/drive.readonly",
        "openid",
        "email",
        "profile",
    ],
    # §C4.6 — Dropbox scopes (API v2). `files.content.read` covers
    # listing + reading the user's files. `account_info.read` lets us
    # display the connected account's email + display-name in the
    # Cloud sync panel. Dropbox issues short-lived access tokens by
    # default; we ask for refresh via `token_access_type=offline` on
    # the auth URL, not via scopes.
    "dropbox": [
        "files.content.read",
        "files.metadata.read",
        "account_info.read",
    ],
    # §C4.6 — iCloud. Apple doesn't expose OAuth scopes (or OAuth at
    # all for iCloud Drive). Empty list is a marker so the catalog's
    # status helper + the membership check in connect_provider know
    # this provider exists; the actual auth runs through a separate
    # /cloud/icloud/start + /cloud/icloud/verify pair, not the OAuth
    # connect/callback pair the other providers share.
    "icloud": [],
    # §C4.6 — Proton Drive + MEGA. Both are end-to-end encrypted
    # services with fragile / unmaintained Python clients; we drive
    # them via the rclone binary instead. No OAuth scopes — auth is
    # email + password (+ optional 2FA for Proton). Empty list is a
    # marker; the actual auth runs through the password-based
    # /cloud/proton-drive/start + /verify and /cloud/mega/start
    # endpoints.
    "proton_drive": [],
    "mega": [],
}


# ---------- §C4.6 — provider registry --------------------------------------
#
# Single source of truth for the FE's "Cloud sync" panel. Each entry
# describes how a provider should render + whether it's actually
# wired. The FE pulls this list via GET /cloud/providers and renders
# a card per entry, gating the "Connect" button on
# `status == "available"`.
#
# `status` values:
#   "available"     OAuth client + sync engine are both wired; user
#                   can connect right now.
#   "needs_setup"   Sync engine wired but client_id/client_secret
#                   aren't configured in env. FE shows "Needs setup"
#                   chip + link to provider docs.
#   "coming_soon"   Slot reserved, listing/download not shipped yet.
#                   FE shows greyed-out card + "Notify me" button.
#
# Status is computed at request time from settings so flipping the
# env vars promotes a provider from "needs_setup" → "available"
# without a code change.

def _icloud_status() -> str:
    """iCloud's "is it wired" check is binary: pyicloud importable
    means we can drive the Apple ID + 2FA dance. No env-var
    configuration to wait on (no OAuth client) and no needs_setup
    state — it's either there or it isn't."""
    try:
        import pyicloud  # noqa: F401
    except ImportError:
        return "coming_soon"
    return "available"


def _rclone_status() -> str:
    """Proton Drive + MEGA both ride on the rclone binary. The Python
    libraries for these E2E services are fragile (mega.py breaks
    every few months when MEGA tweaks its API; proton-python-client
    is similarly brittle), so we shell out to rclone's Go
    implementation which has been the most stable bridge for years.
    Returns "available" if rclone is on PATH, "coming_soon"
    otherwise — operators just need to install the static binary
    (the Dockerfile pulls a pinned version)."""
    import shutil as _shutil_rclone
    if _shutil_rclone.which("rclone") is None:
        return "coming_soon"
    return "available"


def _proton_drive_status() -> str:
    return _rclone_status()


def _mega_status() -> str:
    return _rclone_status()


def _provider_status(provider: str) -> str:
    if provider == "google_drive":
        if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
            return "needs_setup"
        return "available"
    if provider == "dropbox":
        if not settings.dropbox_oauth_client_id or not settings.dropbox_oauth_client_secret:
            return "needs_setup"
        return "available"
    if provider == "icloud":
        return _icloud_status()
    if provider == "proton_drive":
        return _proton_drive_status()
    if provider == "mega":
        return _mega_status()
    return "coming_soon"


def list_providers() -> list[dict]:
    """Provider catalog for the FE Cloud sync panel.

    Each entry carries `auth_shape` so the FE can pick the right
    connect affordance: "oauth" → redirect to the provider's consent
    page; "apple_id" → iCloud's Apple-ID + 2FA modal; "password" →
    Proton / MEGA's credentials modal. Status is derived from settings
    so flipping env vars promotes a provider from "needs_setup" →
    "available" without a code change.
    """
    return [
        {
            "id": "google_drive",
            "name": "Google Drive",
            "kind": "oauth2",
            "status": _provider_status("google_drive"),
            "auth_shape": "oauth",
            "blurb": "Mirror your Drive into neuthek. Read-only — we never write to your Drive.",
            "docs": "https://console.cloud.google.com/apis/credentials",
        },
        {
            "id": "dropbox",
            "name": "Dropbox",
            "kind": "oauth2",
            "status": _provider_status("dropbox"),
            "auth_shape": "oauth",
            "blurb": "Mirror your Dropbox. Read-only access; your files stay on Dropbox too.",
            "docs": "https://www.dropbox.com/developers/apps",
        },
        {
            "id": "icloud",
            "name": "iCloud Drive",
            "kind": "app_password",
            "status": _provider_status("icloud"),
            "auth_shape": "apple_id",
            "blurb": (
                "Direct iCloud Drive sync via pyicloud. Requires your "
                "Apple ID, password, and a 2FA code from one of your "
                "iDevices. Trust token expires every ~30 days; you'll "
                "be prompted to re-authenticate when that happens."
            ),
            "docs": None,
        },
        {
            "id": "proton_drive",
            "name": "Proton Drive",
            "kind": "credentials",
            "status": _provider_status("proton_drive"),
            "auth_shape": "password",
            "blurb": (
                "End-to-end encrypted Drive from Proton. Requires your "
                "Proton email, password, and 2FA code. Files are "
                "decrypted by rclone during sync so they can be "
                "re-encrypted with your neuthek key — Proton's E2E "
                "protection is preserved up to that handover point."
            ),
            "docs": None,
        },
        {
            "id": "mega",
            "name": "MEGA",
            "kind": "credentials",
            "status": _provider_status("mega"),
            "auth_shape": "password",
            "blurb": (
                "End-to-end encrypted cloud storage from MEGA. "
                "Requires your MEGA email + password. Files are "
                "decrypted during sync and re-encrypted server-side — "
                "when you migrate to neuthek you can revoke MEGA "
                "access entirely."
            ),
            "docs": None,
        },
    ]


# ---------- error type the API layer translates to 503 -------------------


class CloudSyncNotConfigured(RuntimeError):
    """Raised when a provider's OAuth client / encryption key is missing.

    Inherits from RuntimeError so the existing
    `except NotImplementedError` in api/cloud.py keeps working as a
    fallback while we transition; the new code path catches this
    directly.
    """


# ---------- value types -------------------------------------------------


@dataclass
class OAuthHandoff:
    auth_url: str
    state: str


# ---------- §C4.6 — Dropbox -------------------------------------------------
#
# Dropbox v2 OAuth endpoints:
#   AUTH:  https://www.dropbox.com/oauth2/authorize
#   TOKEN: https://api.dropboxapi.com/oauth2/token
#
# Default access tokens are 4-hour short-lived UNLESS the auth URL
# carries `token_access_type=offline` — which switches the response
# to (short_lived_access_token, refresh_token) pair. The refresh
# token is what we encrypt + store. Dropbox uses HTTP Basic for
# client auth on the token endpoint, NOT a body field, which is the
# main shape difference from Google.

_DROPBOX_AUTH_ENDPOINT = "https://www.dropbox.com/oauth2/authorize"
_DROPBOX_TOKEN_ENDPOINT = "https://api.dropboxapi.com/oauth2/token"


def _dropbox_auth_url(state: str) -> tuple[str, str | None]:
    """Build the Dropbox consent URL + return (url, code_verifier).

    Dropbox supports PKCE; we use it for defense-in-depth — even if
    our redirect URI ever leaks via a referrer log, an attacker
    without the verifier can't exchange the intercepted code.
    """
    import base64
    import hashlib
    import secrets as _secrets
    from urllib.parse import urlencode

    verifier = _secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    params = {
        "client_id": settings.dropbox_oauth_client_id,
        "response_type": "code",
        "redirect_uri": settings.dropbox_oauth_redirect_uri,
        "state": state,
        # `offline` is THE flag that makes Dropbox return a refresh
        # token. Without this we get a short-lived access token only
        # and the sync stops working the next day.
        "token_access_type": "offline",
        "scope": " ".join(PROVIDER_SCOPES["dropbox"]),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{_DROPBOX_AUTH_ENDPOINT}?{urlencode(params)}", verifier


async def _dropbox_exchange_code(code: str, verifier: str | None) -> dict:
    """POST to the Dropbox token endpoint. Uses HTTP Basic auth for
    the client_id / client_secret per Dropbox's docs (NOT body
    fields, which Google accepts)."""
    import httpx
    data = {
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.dropbox_oauth_redirect_uri,
    }
    if verifier:
        data["code_verifier"] = verifier
    auth = (
        settings.dropbox_oauth_client_id,
        settings.dropbox_oauth_client_secret,
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(_DROPBOX_TOKEN_ENDPOINT, data=data, auth=auth)
    if r.status_code >= 400:
        logger.warning(
            "dropbox token exchange failed: %s %s",
            r.status_code, r.text[:200],
        )
        raise CloudSyncNotConfigured(
            "Dropbox token exchange failed. Try connecting again, "
            "or check that the app's redirect URI matches "
            "DROPBOX_OAUTH_REDIRECT_URI exactly."
        )
    return r.json()


async def _dropbox_refresh_access_token(refresh_token: str) -> str:
    """Trade a refresh_token for a fresh short-lived access_token.

    Dropbox returns 4-hour access tokens. Each call to this helper
    gets us a fresh one without re-prompting consent. Auth is HTTP
    Basic with (client_id, client_secret) — same shape as the code
    exchange.
    """
    import httpx
    data = {
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    auth = (
        settings.dropbox_oauth_client_id,
        settings.dropbox_oauth_client_secret,
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(_DROPBOX_TOKEN_ENDPOINT, data=data, auth=auth)
    if r.status_code >= 400:
        logger.warning(
            "dropbox refresh failed: %s %s",
            r.status_code, r.text[:200],
        )
        raise CloudSyncNotConfigured(
            "Dropbox refresh token rejected. The user may need to "
            "reconnect from Settings → Cloud sync."
        )
    payload = r.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise CloudSyncNotConfigured("Dropbox refresh returned no access_token.")
    return access_token


_DROPBOX_API_BASE = "https://api.dropboxapi.com/2"
_DROPBOX_CONTENT_BASE = "https://content.dropboxapi.com/2"


async def _dropbox_collect_entries(refresh_token: str) -> list[dict]:
    """Walk every file in the user's Dropbox and return entry dicts.

    Dropbox's `/2/files/list_folder` with `recursive=true` returns
    the whole tree in pages of up to 2000 items each. We page with
    `/2/files/list_folder/continue` until `has_more=false`.

    Entries with `.tag == "deleted"` are skipped (Dropbox marks
    deletions in the listing for delta-sync clients; we're doing
    a snapshot pull so we just ignore them).
    """
    access_token = await _dropbox_refresh_access_token(refresh_token)
    import httpx

    out: list[dict] = []

    async def _post_listing(client: httpx.AsyncClient, url: str, body: dict) -> dict:
        nonlocal access_token
        for attempt in (0, 1):
            r = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if r.status_code == 401 and attempt == 0:
                access_token = await _dropbox_refresh_access_token(refresh_token)
                continue
            if r.status_code >= 400:
                logger.warning(
                    "dropbox list failed: %s %s",
                    r.status_code, r.text[:200],
                )
                # Dropbox surfaces app-config problems as 400s with
                # a descriptive body. Most common per-app failure:
                # the operator created the OAuth app but didn't tick
                # the scope checkboxes on the Permissions tab — we
                # can REQUEST those scopes at consent time but the
                # APP itself has to have them enabled by the owner
                # first. Without that, Dropbox accepts the OAuth
                # grant happily but rejects every API call with
                # "does not have the required scope X". Without this
                # branch, the user just sees "listing failed" and
                # has no idea the fix is on the developer console.
                body_lc = r.text.lower()
                if "does not have the required scope" in body_lc:
                    msg = (
                        "Your Dropbox app is missing API permissions. "
                        "Open the app at dropbox.com/developers/apps, "
                        "go to the Permissions tab, tick "
                        "files.content.read + files.metadata.read + "
                        "account_info.read, then Submit. After saving, "
                        "disconnect + reconnect Dropbox here so a fresh "
                        "OAuth grant picks up the new scopes."
                    )
                elif r.status_code in (401, 403):
                    msg = (
                        "Dropbox denied access. Reconnect from "
                        "Settings → Cloud sync."
                    )
                else:
                    msg = (
                        "Dropbox listing failed. Try again later, or "
                        "reconnect from Settings → Cloud sync."
                    )
                raise CloudSyncNotConfigured(msg)
            return r.json()
        raise CloudSyncNotConfigured("Dropbox listing retry exhausted.")

    async with httpx.AsyncClient(timeout=60.0) as client:
        payload = await _post_listing(
            client,
            f"{_DROPBOX_API_BASE}/files/list_folder",
            {"path": "", "recursive": True, "limit": 2000},
        )
        while True:
            for item in payload.get("entries", []):
                if item.get(".tag") != "file":
                    continue  # skip "folder" + "deleted" entries
                # Dropbox doesn't return mime — we derive it from
                # the extension later in the upload-validation path.
                # `path_display` is the human-readable path; we
                # split it into the filename + parent path so the
                # folder-mirror code can recreate the tree.
                full_path = item.get("path_display") or ""
                parent_path = full_path.rsplit("/", 1)[0].lstrip("/") if "/" in full_path else ""
                # Dropbox returns `content_hash` (its own custom
                # hash, NOT sha256) — see
                # https://www.dropbox.com/developers/reference/content-hash
                # for the spec. We store it in the sha256 column
                # because that's the change-detector — false-positive
                # collisions just re-download.
                content_hash = item.get("content_hash") or ""
                sha = bytes.fromhex(content_hash)[:32] if content_hash else None
                out.append({
                    "remote_id": item["id"],
                    "name": item["name"],
                    # Dropbox doesn't surface mime, but the upload-
                    # validation pipeline sniffs from bytes + extension
                    # anyway, so leave it None and let neuthek's MIME
                    # detection do the work.
                    "mime_type": None,
                    "modified_at": _parse_iso_time(item.get("server_modified")),
                    "remote_path": item["name"],
                    "remote_parent_path": parent_path,
                    "sha256": sha,
                    "size_bytes": int(item.get("size") or 0),
                })
            if not payload.get("has_more"):
                break
            payload = await _post_listing(
                client,
                f"{_DROPBOX_API_BASE}/files/list_folder/continue",
                {"cursor": payload["cursor"]},
            )

    return out


async def _dropbox_download(refresh_token: str, entry: dict) -> bytes:
    """POST /2/files/download. The file path goes in a special
    `Dropbox-API-Arg` JSON header (not the body — the body is the
    response). We use the file's stable ID (`id:...`) so renames
    on Dropbox's side don't break sync mid-pull.
    """
    access_token = await _dropbox_refresh_access_token(refresh_token)
    import httpx
    import json as _json
    headers = {
        "Authorization": f"Bearer {access_token}",
        # Dropbox-API-Arg is the spec'd way to pass arguments on
        # /content endpoints — the body is reserved for the file
        # bytes (or NULL on download).
        "Dropbox-API-Arg": _json.dumps({"path": entry["remote_id"]}),
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{_DROPBOX_CONTENT_BASE}/files/download",
            headers=headers,
        )
        if r.status_code >= 400:
            logger.warning(
                "dropbox download failed: id=%s %s %s",
                entry["remote_id"], r.status_code, r.text[:200],
            )
            raise CloudSyncNotConfigured(
                f"Dropbox download failed for {entry['name']!r}."
            )
        return r.content


async def _dropbox_folder_stats(refresh_token: str) -> dict:
    """Walk all files and sum sizes for the storage-panel linked-
    services row."""
    entries = await _dropbox_collect_entries(refresh_token)
    return {
        "file_count": len(entries),
        "total_bytes": sum(e["size_bytes"] for e in entries),
    }


# ---------- §C4.6 — iCloud Drive ------------------------------------------
#
# Apple doesn't expose OAuth for iCloud Drive — they don't have an
# equivalent at all. pyicloud drives the same Apple-ID + password +
# 2FA dance a Mac does at sign-in, then persists a trust token to a
# cookie directory so subsequent sessions resume without re-prompting.
#
# Three shape differences from every other provider here:
#
#   1) `encrypted_refresh_token` holds an encrypted JSON blob of
#      `{apple_id, password}`, not a single token string. pyicloud
#      needs both to re-construct a session when the trust token
#      eventually expires.
#
#   2) pyicloud is synchronous (requests-based, not httpx). Every
#      call into it MUST run inside `asyncio.to_thread` or it will
#      block the FastAPI event loop for the duration of the network
#      round-trip.
#
#   3) State persistence lives on disk, not in our database. pyicloud
#      writes `cookies.json` + `keyring.json` under `cookie_directory`;
#      production deployments mount `settings.pyicloud_cookie_root` as
#      a persistent volume so those files survive container restarts
#      and the trust token isn't invalidated on every redeploy.

import asyncio as _asyncio_icloud
import json as _json_icloud
import shutil as _shutil_icloud
from pathlib import Path as _Path_icloud


def _icloud_cookie_dir(link_id: int) -> _Path_icloud:
    """Per-CloudLink trust-state directory. Created on first access
    with 0o700 so the cookie + keyring files (which contain the
    session tokens Apple issued post-2FA) are not world-readable on
    multi-tenant hosts."""
    base = _Path_icloud(settings.pyicloud_cookie_root) / str(link_id)
    base.mkdir(parents=True, exist_ok=True)
    try:
        base.chmod(0o700)
    except (OSError, NotImplementedError):
        # Windows + some FUSE mounts don't support chmod; the perms
        # there are governed by the filesystem ACL anyway, so this is
        # best-effort.
        pass
    return base


def _icloud_pack_credentials(apple_id: str, password: str) -> str:
    """Bundle the credentials into a JSON blob + encrypt for storage
    in `cloud_links.encrypted_refresh_token`. Apple's trust token
    persists in the cookie_directory, but we keep the raw password
    around in case the trust expires (every ~30 days) and we need to
    rebuild a session from scratch — without it the only recovery
    path would be to disconnect + reconnect from scratch."""
    return encrypt_token(_json_icloud.dumps({
        "apple_id": apple_id, "password": password,
    })).decode("ascii")


def _icloud_unpack_credentials(encrypted_blob: str) -> tuple[str, str]:
    """Inverse of _icloud_pack_credentials. Returns (apple_id, password)."""
    raw = decrypt_token(encrypted_blob.encode("ascii"))
    payload = _json_icloud.loads(raw)
    return payload["apple_id"], payload["password"]


def _icloud_service_sync(link: "CloudLink") -> "PyiCloudService":
    """Build a pyicloud session from a persisted CloudLink. Runs
    synchronously — callers wrap in asyncio.to_thread.

    If the cookie_directory still holds a valid trust token (Apple
    grants ~30 days), pyicloud resumes the session without an HTTP
    round-trip past the validation step. If the trust expired, this
    re-issues the password authentication; the caller will see
    `service.requires_2fa=True` and surface "your iCloud session
    expired, please reconnect" to the user.
    """
    from pyicloud import PyiCloudService  # type: ignore
    apple_id, password = _icloud_unpack_credentials(
        link.encrypted_refresh_token,
    )
    cookie_dir = _icloud_cookie_dir(link.id)
    return PyiCloudService(
        apple_id=apple_id,
        password=password,
        cookie_directory=str(cookie_dir),
    )


async def _icloud_collect_entries(link: "CloudLink") -> list[dict]:
    """BFS the user's iCloud Drive. Returns the same entry dict shape
    every other provider emits.

    pyicloud doesn't surface a stable file-ID for Drive nodes (the
    underlying CloudKit `docwsid` isn't reliably exposed), so we use
    the full remote path as `remote_id`. Renames on the iCloud side
    look like a delete + add to our diff logic — fine for v1, the
    follow-on sync just re-downloads.

    Hash / etag is also unavailable through pyicloud's public API, so
    `sha256=None`; the (size, modified_at) pair drives change
    detection via the existing logic in `sync_user_provider`.
    """
    def _walk_sync() -> list[dict]:
        service = _icloud_service_sync(link)
        out: list[dict] = []
        # BFS queue holds (node, accumulated_parent_path) pairs. The
        # root maps to parent_path="" so files directly under root
        # land at the gallery root.
        queue: list[tuple[object, str]] = [(service.drive, "")]
        # Defensive depth cap — if pyicloud ever returns a cyclic
        # structure (it shouldn't, but iCloud's underlying CloudKit
        # has hit this in the wild), 50 levels is well past any
        # plausible real-world directory tree.
        visited_count = 0
        MAX_NODES = 100_000
        while queue:
            node, parent_path = queue.pop(0)
            visited_count += 1
            if visited_count > MAX_NODES:
                logger.warning(
                    "icloud: walk capped at %d nodes for link=%s",
                    MAX_NODES, link.id,
                )
                break
            try:
                children = node.get_children() or []
            except Exception:
                logger.exception(
                    "icloud: get_children failed at path=%r", parent_path,
                )
                continue
            for child in children:
                try:
                    ctype = getattr(child, "type", None)
                    cname = getattr(child, "name", None)
                    if not cname:
                        continue
                    if ctype == "folder":
                        child_path = (
                            f"{parent_path}/{cname}" if parent_path else cname
                        )
                        queue.append((child, child_path))
                        continue
                    if ctype != "file":
                        # pyicloud occasionally surfaces app-package
                        # nodes ("app_library") — skip.
                        continue
                    size = int(getattr(child, "size", 0) or 0)
                    modified = getattr(child, "date_modified", None)
                    # Path-as-id: parent_path + name, no leading slash.
                    full_path = (
                        f"{parent_path}/{cname}" if parent_path else cname
                    )
                    out.append({
                        "remote_id": full_path,
                        "name": cname,
                        "mime_type": None,
                        "modified_at": modified,
                        "remote_path": cname,
                        "remote_parent_path": parent_path,
                        # iCloud doesn't expose a usable content hash via
                        # pyicloud — let the (size, modified_at) pair
                        # drive change detection.
                        "sha256": None,
                        "size_bytes": size,
                    })
                except Exception:
                    logger.exception(
                        "icloud: error processing child under %r", parent_path,
                    )
                    continue
        return out

    return await _asyncio_icloud.to_thread(_walk_sync)


async def _icloud_download(link: "CloudLink", entry: dict) -> bytes:
    """Fetch the bytes of a single iCloud Drive file. `entry` is the
    dict produced by `_icloud_collect_entries` — we use its `remote_id`
    (the full path) to navigate from `service.drive` to the leaf."""
    def _dl_sync() -> bytes:
        service = _icloud_service_sync(link)
        node = service.drive
        # `remote_id` is the full path split by `/`. Navigate one
        # component at a time. pyicloud's `node.get(name)` returns the
        # matching child or raises KeyError.
        path = entry.get("remote_id") or ""
        parts = [p for p in path.split("/") if p]
        for part in parts:
            node = node.get(part)
        # `node.open()` returns a streaming Response-like object;
        # `.raw.read()` slurps the whole body. For very large files
        # the existing pipeline already buffers in memory (see the
        # CR-4 quota gate), so we don't optimize this further here.
        resp = node.open()
        try:
            data = resp.raw.read()
            if not isinstance(data, (bytes, bytearray)):
                # pyicloud returns a urllib3 raw response; coerce
                # defensively.
                data = bytes(data)
            return bytes(data)
        finally:
            try:
                resp.close()
            except Exception:
                pass

    return await _asyncio_icloud.to_thread(_dl_sync)


async def _icloud_folder_stats(link: "CloudLink") -> dict:
    """File count + total bytes for the storage-panel linked-services
    row. Same shape as the other `_*_folder_stats` helpers."""
    entries = await _icloud_collect_entries(link)
    return {
        "file_count": len(entries),
        "total_bytes": sum(int(e.get("size_bytes") or 0) for e in entries),
    }


# ---------- §C4.6 — Proton Drive + MEGA (via rclone) -----------------------
#
# Both providers are end-to-end encrypted services with fragile Python
# clients (mega.py and proton-python-client break every few months
# when upstream tweaks their APIs). The rclone Go binary has stable
# native support for both — its image cost is ~30 MB and the stability
# is dramatically better than the Python ecosystem alternative.
#
# Shape:
#   * `encrypted_refresh_token` holds an encrypted JSON blob of
#     {email, password, totp_secret_or_empty} for Proton, {email,
#     password} for MEGA. The rclone config file at
#     `{settings.rclone_config_root}/{link_id}.conf` is the working
#     copy rclone reads at runtime; the encrypted blob in the DB is
#     the durable source of truth (so a wiped volume just needs us
#     to re-materialize the config file from the DB).
#   * Each rclone invocation passes `--config` pointing at the per-
#     link file so credentials never leak across links and the
#     ambient `~/.config/rclone/rclone.conf` is never read.
#   * Entries returned by `rclone lsjson --recursive` already carry
#     {Path, Name, Size, MimeType, ModTime, IsDir, ID} — we flatten
#     into the same dict shape every other engine emits.

import json as _json_rclone
from pathlib import Path as _Path_rclone


def _proton_drive_pack_credentials(
    email: str, password: str, totp: str = "",
) -> str:
    """Bundle Proton credentials into an encrypted JSON blob for
    persistence in `cloud_links.encrypted_refresh_token`. `totp` is
    the TOTP *secret* (not the per-login 6-digit code) — empty if the
    account doesn't have 2FA on."""
    return encrypt_token(_json_rclone.dumps({
        "email": email, "password": password, "totp": totp or "",
    })).decode("ascii")


def _proton_drive_unpack_credentials(
    encrypted_blob: str,
) -> tuple[str, str, str]:
    """Inverse of _proton_drive_pack_credentials. Returns
    (email, password, totp)."""
    raw = decrypt_token(encrypted_blob.encode("ascii"))
    payload = _json_rclone.loads(raw)
    return (
        payload.get("email", ""),
        payload.get("password", ""),
        payload.get("totp", "") or "",
    )


def _mega_pack_credentials(email: str, password: str) -> str:
    """Bundle MEGA credentials into an encrypted JSON blob for
    persistence in `cloud_links.encrypted_refresh_token`."""
    return encrypt_token(_json_rclone.dumps({
        "email": email, "password": password,
    })).decode("ascii")


def _mega_unpack_credentials(encrypted_blob: str) -> tuple[str, str]:
    """Inverse of _mega_pack_credentials. Returns (email, password)."""
    raw = decrypt_token(encrypted_blob.encode("ascii"))
    payload = _json_rclone.loads(raw)
    return payload.get("email", ""), payload.get("password", "")


def _ensure_rclone_config(link: "CloudLink") -> _Path_rclone:
    """Make sure the per-link rclone config file exists on disk; if
    not (operator wiped the named volume, fresh container with the
    config volume not yet populated), rebuild it from the encrypted
    blob stored in the DB. Returns the config path.

    Without this re-materialization step, a `docker volume rm
    rclone_configs` would force every Proton / MEGA user to disconnect
    + reconnect — losing it from disk shouldn't matter because the
    durable source of truth lives in the encrypted DB column."""
    from backend import rclone_wrapper

    path = rclone_wrapper.config_path_for_link(link.id)
    if path.exists():
        return path
    # Re-materialize from the DB blob.
    if link.provider == "proton_drive":
        email, password, totp = _proton_drive_unpack_credentials(
            link.encrypted_refresh_token,
        )
        rclone_wrapper.write_proton_config(link.id, email, password, totp)
    elif link.provider == "mega":
        email, password = _mega_unpack_credentials(
            link.encrypted_refresh_token,
        )
        rclone_wrapper.write_mega_config(link.id, email, password)
    else:
        raise CloudSyncNotConfigured(
            f"_ensure_rclone_config called for non-rclone provider {link.provider!r}",
        )
    return path


def _rclone_entries_to_dicts(
    raw: list[dict], *, default_mime: str | None = None,
) -> list[dict]:
    """Convert rclone's lsjson output to the entry-dict shape every
    other engine emits. Skips directories — we only ingest files."""
    out: list[dict] = []
    for entry in raw:
        if entry.get("IsDir"):
            continue
        name = entry.get("Name") or ""
        path = entry.get("Path") or name
        # rclone returns the full relative path in `Path`; the parent
        # is everything up to the last `/`. Empty when the file lives
        # at the root.
        parent_path = ""
        if "/" in path:
            parent_path = path.rsplit("/", 1)[0]
        # §C4.6 fix — remote_id MUST be the rclone-relative path,
        # NOT the upstream `ID`. rclone's `cat` command takes a path
        # argument like `mega:Pictures/foo.jpg`, never an ID. MEGA's
        # lsjson includes `ID` (their internal file id, e.g.
        # "0vJ3Ab7a") but it's informational only — passing it to
        # `rclone cat` 4xx's with "directory not found". An earlier
        # version of this function fell back to ID when present,
        # which silently broke MEGA downloads (the user saw
        # "pulled=0" even though lsjson returned the files). Renames
        # in the upstream cloud will appear to us as a new file + a
        # delete of the old one — semantically correct for a mirror-
        # and-show workflow.
        remote_id = path
        out.append({
            "remote_id": remote_id,
            "name": name,
            "mime_type": entry.get("MimeType") or default_mime,
            "modified_at": _parse_iso_time(entry.get("ModTime")),
            # `remote_path` carries the full relative path so a file
            # at "Pictures/2026/foo.jpg" doesn't collapse to
            # "foo.jpg" and lose its folder context in the gallery.
            "remote_path": path,
            "remote_parent_path": parent_path,
            # rclone surfaces a content hash only for some backends; for
            # E2E services like Proton + MEGA the upstream doesn't
            # expose one. Leave sha256=None and let (size, modified_at)
            # drive change detection.
            "sha256": None,
            "size_bytes": int(entry.get("Size") or 0),
        })
    return out


async def _proton_drive_collect_entries(link: "CloudLink") -> list[dict]:
    """Walk every file in the user's Proton Drive via rclone lsjson.

    rclone decrypts each file's metadata locally as it walks the
    tree; the actual bytes stay encrypted until per-file download.
    See `_proton_drive_download` for the bytes path.
    """
    from backend import rclone_wrapper

    config_path = _ensure_rclone_config(link)
    raw = await rclone_wrapper.rclone_ls_json(
        rclone_wrapper.PROTON_REMOTE_NAME, config_path,
    )
    return _rclone_entries_to_dicts(raw)


async def _proton_drive_download(link: "CloudLink", entry: dict) -> bytes:
    """Fetch a single Proton Drive file's bytes via `rclone cat`.
    `entry["remote_id"]` doubles as the relative path that rclone
    understands; we constructed it that way in collect_entries
    (Proton's rclone backend doesn't always expose a stable ID)."""
    from backend import rclone_wrapper

    config_path = _ensure_rclone_config(link)
    return await rclone_wrapper.rclone_copy_to_stdout(
        rclone_wrapper.PROTON_REMOTE_NAME,
        entry.get("remote_id") or entry.get("name") or "",
        config_path,
    )


async def _proton_drive_folder_stats(link: "CloudLink") -> dict:
    """File count + total bytes for the storage panel. Walks the
    full lsjson tree — for very large Proton Drives this is one full
    listing per `/cloud/folder-stats/proton_drive` request, same
    cost shape as iCloud."""
    entries = await _proton_drive_collect_entries(link)
    return {
        "file_count": len(entries),
        "total_bytes": sum(int(e.get("size_bytes") or 0) for e in entries),
    }


async def _mega_collect_entries(link: "CloudLink") -> list[dict]:
    """Walk every file in the user's MEGA cloud via rclone lsjson."""
    from backend import rclone_wrapper

    config_path = _ensure_rclone_config(link)
    raw = await rclone_wrapper.rclone_ls_json(
        rclone_wrapper.MEGA_REMOTE_NAME, config_path,
    )
    return _rclone_entries_to_dicts(raw)


async def _mega_download(link: "CloudLink", entry: dict) -> bytes:
    """Fetch a single MEGA file's bytes via `rclone cat`."""
    from backend import rclone_wrapper

    config_path = _ensure_rclone_config(link)
    return await rclone_wrapper.rclone_copy_to_stdout(
        rclone_wrapper.MEGA_REMOTE_NAME,
        entry.get("remote_id") or entry.get("name") or "",
        config_path,
    )


async def _mega_folder_stats(link: "CloudLink") -> dict:
    """File count + total bytes for the storage panel."""
    entries = await _mega_collect_entries(link)
    return {
        "file_count": len(entries),
        "total_bytes": sum(int(e.get("size_bytes") or 0) for e in entries),
    }


# ---------- Google Drive --------------------------------------------------


def _google_flow():
    """Build a `google_auth_oauthlib.flow.Flow` from settings.

    Imported lazily so the module loads even when the optional
    google-auth-oauthlib package isn't installed (tests + non-cloud
    deployments). Raises CloudSyncNotConfigured if the OAuth client
    isn't set up.
    """
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise CloudSyncNotConfigured(
            "Google OAuth client not configured. Set "
            "GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET in .env. "
            "See SETUP.md > Google Drive."
        )
    try:
        from google_auth_oauthlib.flow import Flow  # type: ignore
    except ImportError as exc:
        raise CloudSyncNotConfigured(
            "google-auth-oauthlib is not installed. Run "
            '`pip install -e ".[cloud]"`.'
        ) from exc

    client_config = {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=PROVIDER_SCOPES["google_drive"],
        redirect_uri=settings.google_oauth_redirect_uri,
    )
    return flow


_PKCE_REDIS_PREFIX = "cloud:pkce:"
_PKCE_TTL_SECONDS = 600  # 10 min — Google's consent screen rarely takes longer
_PKCE_FALLBACK: dict[str, str] = {}


async def _stash_pkce_verifier(state: str, verifier: str) -> None:
    """Save the PKCE code_verifier under sha256(state) so `complete_oauth`
    can pick it up when Google calls back.

    google-auth-oauthlib v1.4+ auto-enables PKCE on Web Application
    clients. The auth URL carries `code_challenge`, and the callback
    MUST send the matching `code_verifier`. Without this stash, the
    token exchange dies with `invalid_grant: Missing code verifier`.

    Stored in Redis so two backend processes (uvicorn workers, a
    host-run dev server vs. the Docker container) can both reach it.
    Falls back to a process-local dict if Redis is unreachable —
    fine for local-dev when the same process handles both halves.
    """
    key = _PKCE_REDIS_PREFIX + hashlib.sha256(state.encode()).hexdigest()
    try:
        import redis.asyncio as redis  # type: ignore
        client = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await client.set(key, verifier, ex=_PKCE_TTL_SECONDS)
        finally:
            await client.aclose()
        return
    except Exception:
        _PKCE_FALLBACK[key] = verifier


async def _pop_pkce_verifier(state: str) -> str | None:
    """Retrieve + delete the stashed verifier. Single-use — the same
    state should never be exchanged twice."""
    key = _PKCE_REDIS_PREFIX + hashlib.sha256(state.encode()).hexdigest()
    try:
        import redis.asyncio as redis  # type: ignore
        client = redis.from_url(settings.redis_url, decode_responses=True)
        try:
            value = await client.get(key)
            if value is not None:
                await client.delete(key)
                return value
        finally:
            await client.aclose()
    except Exception:
        pass
    return _PKCE_FALLBACK.pop(key, None)


async def connect_provider(user_id: UUID, provider: CloudProvider) -> OAuthHandoff:
    """Build the auth URL the FE should send the user to.

    `state` is HMAC-signed with the JWT secret + a per-call nonce so
    the callback can resolve who started the flow without trusting
    the browser (see `_build_state`).

    For Google Drive we explicitly enable PKCE — google-auth-oauthlib
    v1.4+ auto-enables it on Web Application clients. The code_verifier
    lives in Redis (10 min TTL, keyed by sha256(state)) so the
    callback can retrieve it; without this, Google rejects the token
    exchange with "invalid_grant: Missing code verifier."
    """
    # OAuth providers only. iCloud / Proton Drive / MEGA bootstrap
    # through their own dedicated /cloud/{provider}/start endpoints
    # (password-based, not browser redirect), so they shouldn't
    # reach this connect_provider path.
    if provider not in ("google_drive", "dropbox"):
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' does not use the OAuth connect flow."
        )

    # Verify encryption is configured *before* we send the user out so
    # they don't authenticate just to hit a 500 on the callback.
    _verify_encryption_ready()

    signed_state = _build_state(user_id)
    if provider == "dropbox":
        # §C4.6 — Dropbox v2 OAuth. `token_access_type=offline` is the
        # equivalent of Google's `access_type=offline` — without it
        # Dropbox returns short-lived access tokens that expire in
        # ~4h with no refresh, and the sync stops working the next
        # day.
        if not settings.dropbox_oauth_client_id:
            raise CloudSyncNotConfigured(
                "Dropbox OAuth client not configured. Set "
                "DROPBOX_OAUTH_CLIENT_ID + DROPBOX_OAUTH_CLIENT_SECRET "
                "in .env. Register at dropbox.com/developers/apps."
            )
        auth_url, verifier = _dropbox_auth_url(signed_state)
        if verifier:
            await _stash_pkce_verifier(signed_state, verifier)
        state = signed_state
        return OAuthHandoff(auth_url=auth_url, state=state)
    if provider == "google_drive":
        flow = _google_flow()
        # Own the verifier lifecycle ourselves rather than relying on the
        # Flow object surviving between requests — it doesn't, since
        # connect_provider and complete_oauth run on different requests
        # (potentially different processes).
        flow.autogenerate_code_verifier = True
        # `access_type=offline` is what makes Google return a refresh
        # token. `prompt=consent` forces a refresh-token grant even on
        # re-auth so we never end up with a link row missing its
        # refresh token.
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=signed_state,
        )
        # Stash so `complete_oauth` can pair the code with the verifier.
        if flow.code_verifier:
            await _stash_pkce_verifier(state, flow.code_verifier)
    else:  # pragma: no cover — gated above
        raise CloudSyncNotConfigured(f"Provider '{provider}' not implemented")

    logger.info(
        "cloud_sync: connect_provider user=%s provider=%s state=%s",
        user_id, provider, state[:8],
    )
    return OAuthHandoff(auth_url=auth_url, state=state)


async def complete_oauth(
    session: AsyncSession,
    user_id: UUID,
    provider: CloudProvider,
    code: str,
    state: str,  # noqa: ARG001 — caller already verified state HMAC
) -> int:
    """Exchange `code` for tokens, encrypt + store, return cloud_links.id."""
    # OAuth-only path. The non-OAuth providers (iCloud / Proton Drive /
    # MEGA) persist their credentials via their own dedicated
    # /cloud/{provider}/start endpoints.
    if provider not in ("google_drive", "dropbox"):
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' does not use the OAuth callback path."
        )

    refresh_token_to_store: str | None = None
    scopes = ""

    if provider == "dropbox":
        # §C4.6 — Dropbox token exchange.
        verifier = await _pop_pkce_verifier(state)
        token_payload = await _dropbox_exchange_code(code, verifier)
        refresh_token_to_store = token_payload.get("refresh_token")
        if not refresh_token_to_store:
            raise CloudSyncNotConfigured(
                "Dropbox did not return a refresh token. Make sure "
                "the app's auth URL carries `token_access_type=offline` "
                "and try again."
            )
        scopes = token_payload.get("scope", "") or ",".join(PROVIDER_SCOPES["dropbox"])

    elif provider == "google_drive":
        # `OAUTHLIB_RELAX_TOKEN_SCOPE` keeps Flow.fetch_token from
        # rejecting Google's scope expansion (`email` → full URI form,
        # `include_granted_scopes` adding back drive.readonly etc).
        # Same workaround the SSO module uses.
        import os
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

        flow = _google_flow()
        # Restore the PKCE verifier stashed during connect_provider so
        # Google accepts the exchange. Without this, fetch_token raises
        # `invalid_grant: Missing code verifier` because we sent a
        # code_challenge in the original auth URL but no verifier here.
        verifier = await _pop_pkce_verifier(state)
        if verifier:
            flow.code_verifier = verifier
        # `fetch_token` performs the code → access_token + refresh_token
        # exchange. Google verifies the state internally.
        flow.fetch_token(code=code)
        creds = flow.credentials
        if not creds.refresh_token:
            # Without a refresh token we can't sync after the access
            # token expires (~1 hour). Force a retry — usually means
            # the user clicked "Continue" without "Allow" or had a
            # stale grant.
            raise CloudSyncNotConfigured(
                "Google did not return a refresh token. Revoke any "
                "prior consent at https://myaccount.google.com/permissions "
                "and try again."
            )
        refresh_token_to_store = creds.refresh_token
        scopes = ",".join(creds.scopes or [])

        # Drive's OAuth scopes now include openid/email/profile (see
        # PROVIDER_SCOPES) so the token response carries an id_token.
        # Decode it, capture the Google `sub`, and stamp it onto the
        # User row — that's what lets the SSO callback ("Sign in with
        # Google") map a future sign-in back to THIS neuthek account.
        # Best-effort: a failure here doesn't block the Drive
        # connection from succeeding, since the user may have skipped
        # the identity scopes on the consent screen.
        try:
            id_token_raw = getattr(creds, "id_token", None)
            if id_token_raw:
                from google.oauth2 import id_token as google_id_token  # type: ignore
                from google.auth.transport import requests as google_requests  # type: ignore

                claims = google_id_token.verify_oauth2_token(
                    id_token_raw,
                    google_requests.Request(),
                    settings.google_oauth_client_id,
                )
                sub = claims.get("sub")
                if sub:
                    user_row = (
                        await session.execute(
                            select(User).where(User.id == user_id)
                        )
                    ).scalar_one_or_none()
                    if user_row is not None and user_row.google_sub != sub:
                        user_row.google_sub = sub
                        logger.info(
                            "cloud_sync: linked user=%s to google_sub=%s",
                            user_id, sub,
                        )
        except Exception:
            logger.exception("cloud_sync: id_token decode skipped (non-fatal)")

    encrypted = encrypt_token(refresh_token_to_store)

    # Upsert: if the user already has a link for this provider, replace it.
    existing = (
        await session.execute(
            select(CloudLink).where(
                CloudLink.user_id == user_id, CloudLink.provider == provider
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        link = CloudLink(
            user_id=user_id,
            provider=provider,
            encrypted_refresh_token=encrypted.decode("ascii"),
            scopes=scopes,
            status="active",
        )
        session.add(link)
    else:
        existing.encrypted_refresh_token = encrypted.decode("ascii")
        existing.scopes = scopes
        existing.status = "active"
        link = existing

    await session.commit()
    await session.refresh(link)
    logger.info(
        "cloud_sync: complete_oauth user=%s provider=%s link=%s",
        user_id, provider, link.id,
    )
    return link.id


async def sync_user_provider(
    session: AsyncSession, user_id: UUID, provider: CloudProvider
) -> dict:
    """Pull the provider's listing, ingest new/changed files, mirror the
    remote folder tree into neuthek folders, surface conflicts.

    Behaviour:
      - **Pull-only.** Never writes back to the remote. `drive.readonly`
        scope on Google.
      - **Folder mirroring.** A root folder named `{Provider display
        name}` is created under the user's root; every remote folder
        gets a matching neuthek folder under it.
      - **Diff key.** A row in `cloud_files` keyed on `(user_id,
        provider, remote_id)` tracks `remote_modified` + `sha256`.
        We re-download only when both have changed (timestamp updates
        without a content change happen when Drive permission edits
        bump `modifiedTime` but the bytes are identical).
      - **Limited Use.** Every image is ingested with
        `skip_ai_training=True` + `source_provider=provider`. The
        user can opt back in per-source via the cloud-link settings
        UI; that flips the flag + re-queues the file through the
        normal summarize/face-scan workers.
      - **Conflict detection.** If the local image attached to a
        `cloud_files` row has been edited locally (renamed, deleted,
        re-tagged) since the last sync, we log an audit row
        `cloud.sync.conflict` and **do not overwrite** — the user
        gets a banner pointing at the affected files and chooses
        whether to re-pull manually.

    Returns a summary dict: `{seen, pulled, skipped_unchanged,
    conflicts, provider}`.
    """
    if provider not in (
        "google_drive", "dropbox", "icloud", "proton_drive", "mega",
    ):
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' is not implemented yet."
        )

    link = (
        await session.execute(
            select(CloudLink).where(
                CloudLink.user_id == user_id, CloudLink.provider == provider
            )
        )
    ).scalar_one_or_none()
    if link is None or not link.encrypted_refresh_token:
        raise CloudSyncNotConfigured(
            f"No active {provider} connection. Connect one in Settings."
        )

    # iCloud doesn't carry a refresh token; the "credential" persisted
    # in `encrypted_refresh_token` is an encrypted JSON blob of the
    # Apple ID + password (so the saved pyicloud session can be
    # resumed against the cookie directory). The download/listing
    # helpers re-construct a PyiCloudService from `link` directly so we
    # only need the decrypted bytes for the OAuth-shaped providers.
    refresh_token = (
        decrypt_token(link.encrypted_refresh_token.encode("ascii"))
        if provider != "icloud" else ""
    )

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalars().first()
    if user is None:
        raise CloudSyncNotConfigured("User not found.")

    if provider == "google_drive":
        entries = await _drive_collect_entries(refresh_token)
    elif provider == "dropbox":
        entries = await _dropbox_collect_entries(refresh_token)
    elif provider == "icloud":
        entries = await _icloud_collect_entries(link)
    elif provider == "proton_drive":
        entries = await _proton_drive_collect_entries(link)
    elif provider == "mega":
        entries = await _mega_collect_entries(link)
    else:  # already gated above; defensive.
        entries = []

    # Build the synthesized folder tree once per call so we don't pay
    # the "is this folder already there" round-trip per file.
    folder_root_name = _provider_display_name(provider)
    folder_ids_by_path = await _ensure_remote_folder_tree(
        session, user, folder_root_name,
        provider=provider,
        all_remote_parent_paths={e.get("remote_parent_path") or "" for e in entries},
    )

    # CR-4 — quota gating for cloud-sync.
    #
    # Pre-fix, a user with a 100 GB cap could `POST /cloud/links/{id}/sync`
    # against a 10 TB Drive and we'd happily download every byte before
    # the FS quota check (which lives on the user's account-level
    # storage panel, NOT on the write path) caught up. By the time
    # MinIO ran out of space the cluster was already wedged.
    #
    # Shape of the gate:
    #   * Pre-flight: compute the user's current footprint (SQL-only,
    #     skips MinIO stat()) and derive `budget_remaining`. If the
    #     user is already at-or-over quota, fail-fast with a single
    #     audit row and an empty result; the FE sees `pulled=0,
    #     skipped_over_quota=seen` and can prompt the user to upgrade
    #     or free up space.
    #   * Per-entry: each entry exposes `size_bytes` from the listing
    #     (Drive's `size` field). If the next
    #     download would push the budget below zero, skip it + audit
    #     `cloud.sync.skipped_quota`. We keep the loop going — a
    #     smaller entry later in the list may still fit, and aborting
    #     the whole sync on the first oversized entry would feel
    #     surprising.
    #   * Post-write: decrement the running budget by the *actual*
    #     stored bytes (EXIF strip + transcode can shave a few % off
    #     the Drive-reported number).
    from backend.api.storage import compute_used_bytes_fast, effective_quota_bytes

    used_bytes = await compute_used_bytes_fast(session, user_id)
    quota_bytes = effective_quota_bytes(user)
    budget_remaining = max(0, quota_bytes - used_bytes)

    seen = 0
    pulled = 0
    skipped_unchanged = 0
    skipped_over_quota = 0
    # VLT-1 — files Apple's "Optimize Storage" has evicted from the
    # cloud (only a placeholder/stub exists; the real bytes live on a
    # device). pyicloud's download returns empty / near-empty bytes
    # for these, which used to fail validation with a confusing
    # "Unsupported file type" error. We now count + skip them cleanly.
    skipped_dataless = 0
    # VLT-2 — files whose type isn't supported even after the allow-list
    # broadening (residual html/svg/script blocks + truly unrecognized
    # binaries). Counted + skipped with a WARNING instead of an ERROR
    # stack trace so a folder of exotic files doesn't flood the log.
    skipped_unsupported = 0
    conflicts: list[str] = []

    from backend.upload_validation import UploadValidationError

    if budget_remaining <= 0:
        await add_audit(
            session,
            user_id=user_id,
            action="cloud.sync.over_quota",
            details={
                "provider": provider,
                "quota_bytes": quota_bytes,
                "used_bytes": used_bytes,
                "entries_pending": len(entries),
            },
        )
        # Update link status so the FE shows "over quota" instead of
        # "active" while the user reclaims space.
        link.last_synced_at = datetime.now(timezone.utc)
        link.status = "over_quota"
        await session.commit()
        logger.warning(
            "cloud_sync: user=%s provider=%s at-or-over quota — "
            "used=%d / quota=%d, refusing to download %d pending entries",
            user_id, provider, used_bytes, quota_bytes, len(entries),
        )
        return {
            "seen": len(entries),
            "pulled": 0,
            "skipped_unchanged": 0,
            "skipped_over_quota": len(entries),
            "conflicts": 0,
            "conflict_remote_ids": [],
            "provider": provider,
            "over_quota": True,
        }

    for entry in entries:
        seen += 1
        existing = (
            await session.execute(
                select(CloudFile).where(
                    CloudFile.user_id == user_id,
                    CloudFile.provider == provider,
                    CloudFile.remote_id == entry["remote_id"],
                )
            )
        ).scalar_one_or_none()

        # Tombstone check (migration 0039). When the user deletes a
        # synced file in neuthek, we stamp `cloud_files.excluded_at`.
        # Skip those on every subsequent sync — the user's intent is
        # "I don't want this file in my library." Without this, every
        # delete was undone by the next sync ("I deleted it 20 times
        # and it keeps coming back").
        if existing is not None and existing.excluded_at is not None:
            skipped_unchanged += 1
            continue

        # Folder-level tombstone: if the file's remote parent path is
        # under a folder the user explicitly deleted (synced folder
        # with `deleted_at IS NOT NULL`), skip it. Same intent — they
        # deleted the whole folder; don't re-import its files.
        parent_path = entry.get("remote_parent_path") or ""
        if parent_path and await _is_folder_path_excluded(
            session, user_id, provider, parent_path,
        ):
            skipped_unchanged += 1
            continue

        remote_mod = entry.get("modified_at")
        # SHA dedup: if we already pulled this exact byte sequence, the
        # remote-side timestamp bump alone isn't worth re-downloading.
        if (
            existing is not None
            and existing.remote_modified == remote_mod
            and entry.get("sha256") is not None
            and existing.sha256 == entry["sha256"]
        ):
            skipped_unchanged += 1
            continue

        # Conflict: the existing local image was edited after our last
        # sync. Refuse to overwrite.
        if existing is not None and existing.local_image_id is not None:
            local = await session.get(Image, existing.local_image_id)
            if local is not None and existing.last_synced_at and (
                (local.uploaded_at and local.uploaded_at > existing.last_synced_at)
                or (local.deleted_at is not None)
            ):
                conflicts.append(entry["remote_id"])
                await add_audit(
                    session,
                    user_id=user_id,
                    action="cloud.sync.conflict",
                    details={
                        "provider": provider,
                        "remote_id": entry["remote_id"],
                        "remote_path": entry.get("remote_path"),
                        "reason": "local_change_after_sync",
                    },
                )
                continue

        # CR-4 per-entry quota gate. The listing tells us the
        # remote-reported size up front, so we can refuse to download
        # an entry that we already know won't fit. Without this gate,
        # a 5 GB Drive file would be fully fetched into memory + then
        # rejected at `store_upload` time — wasted bandwidth and a
        # memory spike. Missing/zero `size_bytes` (some providers
        # don't always populate it) falls through to the legacy
        # behavior: download + let the rest of the pipeline decide.
        entry_size = int(entry.get("size_bytes") or 0)
        if entry_size > budget_remaining:
            skipped_over_quota += 1
            await add_audit(
                session,
                user_id=user_id,
                action="cloud.sync.skipped_quota",
                details={
                    "provider": provider,
                    "remote_id": entry["remote_id"],
                    "remote_path": entry.get("remote_path"),
                    "entry_size_bytes": entry_size,
                    "budget_remaining": budget_remaining,
                    "quota_bytes": quota_bytes,
                },
            )
            continue

        try:
            if provider == "icloud":
                blob = await _icloud_download(link, entry)
            elif provider == "proton_drive":
                blob = await _proton_drive_download(link, entry)
            elif provider == "mega":
                blob = await _mega_download(link, entry)
            else:
                blob = await _provider_download(provider, refresh_token, entry)
        except Exception:
            logger.exception(
                "%s download failed for %s", provider, entry["remote_id"]
            )
            continue

        # VLT-1 — dataless / evicted-file guard. Apple's "Optimize
        # iPhone/Mac Storage" keeps only a placeholder for files whose
        # bytes have been pushed off-device; pyicloud's download then
        # returns empty or a tiny stub. Other providers can also hand
        # back a 0-byte body for a transient error. Either way, an
        # empty blob can't be a real file — feeding it to store_upload
        # throws "Unsupported or unrecognized file type" and spams the
        # log. Skip it with a clear status + a one-time audit row so
        # the user can see WHY a file didn't come across, and the next
        # sync (after they download it on a device) picks it up.
        #
        # Threshold: the listing reported `size_bytes`; if the download
        # came back dramatically smaller (< 16 bytes, or < 1% of the
        # reported size when the reported size was non-trivial), treat
        # it as a stub. 16 bytes is below any real file with a magic
        # header; the 1% rule catches partial stubs.
        reported_size = int(entry.get("size_bytes") or 0)
        is_stub = (
            len(blob) < 16
            or (reported_size > 1024 and len(blob) < reported_size * 0.01)
        )
        if is_stub:
            skipped_dataless += 1
            await add_audit(
                session,
                user_id=user_id,
                action="cloud.sync.skipped_dataless",
                details={
                    "provider": provider,
                    "remote_id": entry["remote_id"],
                    "remote_path": entry.get("remote_path"),
                    "downloaded_bytes": len(blob),
                    "reported_size_bytes": reported_size,
                    "reason": "dataless_or_evicted_file",
                },
            )
            continue

        # Safety net: even when the listing-reported size was 0 or
        # missing, the actual byte count is now known. If the
        # downloaded blob alone would blow the budget, drop it on the
        # floor before `store_upload` writes it to MinIO. This closes
        # the gap for providers / endpoints that don't populate `size`
        # on the listing.
        if len(blob) > budget_remaining:
            skipped_over_quota += 1
            await add_audit(
                session,
                user_id=user_id,
                action="cloud.sync.skipped_quota",
                details={
                    "provider": provider,
                    "remote_id": entry["remote_id"],
                    "remote_path": entry.get("remote_path"),
                    "entry_size_bytes": len(blob),
                    "budget_remaining": budget_remaining,
                    "quota_bytes": quota_bytes,
                    "reason": "post_download_size_check",
                },
            )
            continue

        # §C2 — Limited Use: cloud sources default to skip_ai_training
        # (no summarization / no face scan / no CLIP embedding) UNTIL
        # the user explicitly flips `cloud_links.ai_opted_in` to True
        # in the Cloud Sync settings panel. Once opted in, every
        # newly-synced file rides the same AI pipeline that direct
        # uploads do — pending_face_scan + pending_summary stay True
        # and the worker picks the row up post-commit.
        #
        # Before this read, the sync hardcoded `skip_ai_training=True`
        # regardless of the toggle state, so flipping "Enable AI
        # features for Google Drive files" looked enabled in the UI
        # but had zero effect on a fresh sync — the user's complaint.
        from backend.image import store_upload  # local — avoid cycles

        skip_ai_training = not bool(link.ai_opted_in)

        parent_path = entry.get("remote_parent_path") or ""
        folder_id = folder_ids_by_path.get(parent_path) or folder_ids_by_path.get("")
        # Empty `folder_ids_by_path` means the user excluded the root
        # "Google Drive" folder OR every relevant subfolder is
        # excluded. Skip the file rather than dumping it at the
        # gallery root.
        if folder_id is None:
            skipped_unchanged += 1
            continue
        try:
            image = await store_upload(
                session,
                user,
                entry["name"],
                blob,
                entry.get("mime_type"),
                skip_ai_training=skip_ai_training,
                source_provider=provider,
                folder_id=folder_id,
            )
            # Dispatch the appropriate worker jobs. The direct-upload
            # endpoint (api/images.py::create_image) normally enqueues
            # transcode for videos + face_scan/summarize for AI-eligible
            # rows; the sync path bypasses that endpoint so we have to
            # repeat the dispatch logic here. Before this block,
            # cloud-synced videos got NO transcode job → no HLS
            # rendition, no poster JPEG, no `thumbnail_blob_key` → the
            # gallery card sat with a generic mp4 glyph forever.
            from backend import jobs as job_q

            # Transcode runs REGARDLESS of `skip_ai_training` — it's
            # about producing browser-playable HLS + a poster JPEG,
            # not about AI training. Every video upload needs it.
            if image.category in ("video", "audio"):
                try:
                    await job_q.enqueue_transcode_video(user.id, image.id)
                except Exception:
                    logger.exception(
                        "cloud_sync: transcode enqueue failed for %s — "
                        "video will play from the original mp4 but "
                        "won't get a poster thumbnail until requeued",
                        image.id,
                    )

            # AI jobs gated on the cloud link's opt-in toggle.
            if not skip_ai_training:
                needs_faces = (
                    image.category in ("image", "video")
                    and image.pending_face_scan
                )
                needs_summary = image.pending_summary
                try:
                    if needs_faces and needs_summary:
                        await job_q.enqueue_face_scan_then_summarize(
                            user.id, image.id,
                        )
                    elif needs_faces:
                        await job_q.enqueue_face_scan(user.id, image.id)
                    elif needs_summary:
                        # enqueue_summarize signature is just (image_id,)
                        # — the worker resolves user_id from the row.
                        await job_q.enqueue_summarize(image.id)
                except Exception:
                    logger.exception(
                        "cloud_sync: AI job enqueue failed for %s — "
                        "row stays pending; the summarize-progress "
                        "poll will pick it up as a safety net",
                        image.id,
                    )
        except UploadValidationError as exc:
            # VLT-2 — a genuinely unsupported / blocked type (after the
            # allow-list broadening, this is the residual tail:
            # html/svg/script blocks + anything still unrecognized).
            # Skip it as "unsupported" with a single WARNING — not an
            # ERROR with a stack trace — so the log stays readable when
            # a Drive folder has a handful of exotic files.
            skipped_unsupported += 1
            logger.warning(
                "cloud_sync: skipping unsupported file %s (%s)",
                entry["name"], exc,
            )
            continue
        except Exception:
            logger.exception("ingest failed for %s", entry["name"])
            continue

        if existing is None:
            session.add(
                CloudFile(
                    user_id=user_id,
                    provider=provider,
                    remote_id=entry["remote_id"],
                    remote_path=entry.get("remote_path"),
                    remote_parent_path=parent_path or None,
                    local_image_id=image.id,
                    remote_modified=remote_mod,
                    sha256=entry.get("sha256"),
                    last_synced_at=datetime.now(timezone.utc),
                )
            )
        else:
            existing.local_image_id = image.id
            existing.remote_path = entry.get("remote_path")
            existing.remote_parent_path = parent_path or None
            existing.remote_modified = remote_mod
            existing.sha256 = entry.get("sha256")
            existing.last_synced_at = datetime.now(timezone.utc)
        pulled += 1
        # Decrement the running budget. Use the MAX of (the listing's
        # reported size, the actual stored bytes) so an attacker who
        # under-reports size on the listing — passing the per-entry
        # gate cheaply — still gets debited the real footprint. The
        # stored bytes are the truth on disk; the listing claim is
        # what we'd otherwise let slip through. Falling back to the
        # downloaded blob length is the defensive case for when both
        # columns are NULL (shouldn't happen on a successful upload).
        actual_bytes = (
            int(image.byte_size_served or 0)
            + int(image.byte_size_original or 0)
        ) or len(blob)
        budget_remaining = max(
            0, budget_remaining - max(actual_bytes, entry_size),
        )

    link.last_synced_at = datetime.now(timezone.utc)
    if skipped_over_quota and pulled == 0:
        link.status = "over_quota"
    elif conflicts:
        link.status = "conflicts"
    else:
        link.status = "active"
    await session.commit()
    logger.info(
        "cloud_sync: sync user=%s provider=%s seen=%d pulled=%d "
        "skipped=%d skipped_quota=%d skipped_dataless=%d "
        "skipped_unsupported=%d conflicts=%d budget_remaining=%d",
        user_id, provider, seen, pulled, skipped_unchanged,
        skipped_over_quota, skipped_dataless, skipped_unsupported,
        len(conflicts), budget_remaining,
    )
    return {
        "seen": seen,
        "pulled": pulled,
        "skipped_unchanged": skipped_unchanged,
        "skipped_over_quota": skipped_over_quota,
        "skipped_dataless": skipped_dataless,
        "skipped_unsupported": skipped_unsupported,
        "conflicts": len(conflicts),
        "conflict_remote_ids": conflicts,
        "provider": provider,
        "over_quota": bool(skipped_over_quota and pulled == 0),
    }


# ---------- provider-agnostic helpers ------------------------------------


def _provider_display_name(provider: CloudProvider) -> str:
    """Human-readable label for the synthesized root folder."""
    return {
        "google_drive": "Google Drive",
        "dropbox": "Dropbox",
        "icloud": "iCloud Drive",
        "proton_drive": "Proton Drive",
        "mega": "MEGA",
    }.get(provider, provider.title())


async def _is_folder_path_excluded(
    session: AsyncSession,
    user_id: UUID,
    provider: str,
    remote_parent_path: str,
) -> bool:
    """Return True if the user has soft-deleted a synced folder at
    `remote_parent_path` OR at any of its ancestors. Used by the
    file-level sync loop to skip files whose containing folder the
    user explicitly removed — without this, deleting a Drive folder
    in neuthek pulls every file inside it back on the next sync."""
    # Check the exact path AND every ancestor prefix. e.g. for
    # "Trip 2024/Day 1/morning" we check that exact string PLUS
    # "Trip 2024/Day 1" PLUS "Trip 2024".
    candidates = [remote_parent_path]
    cur = remote_parent_path
    while "/" in cur:
        cur = cur.rsplit("/", 1)[0]
        candidates.append(cur)
    if not candidates:
        return False
    row = (
        await session.execute(
            select(Folder.id).where(
                Folder.user_id == user_id,
                Folder.cloud_provider == provider,
                Folder.cloud_remote_path.in_(candidates),
                Folder.deleted_at.is_not(None),
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _ensure_remote_folder_tree(
    session: AsyncSession,
    user: User,
    root_name: str,
    *,
    provider: str,
    all_remote_parent_paths: set[str],
) -> dict[str, UUID]:
    """Materialize the synthesized folder tree for a provider.

    Returns a `{parent_path: folder_id}` map for every parent path
    referenced in the listing. The root entry (key `""`) maps to the
    provider's root folder (e.g. "Google Drive").

    Each path component is created under its parent if missing.
    Re-runs are idempotent — existing folders are reused via the
    `(user_id, parent, lower(name))` partial unique index from
    migration 0010.

    Excluded-folder semantics (migration 0039): if the user has
    soft-deleted a synced folder at the same remote_path, we do NOT
    recreate it under the same name+parent — that's how previous
    syncs ended up duplicating "Google Drive" folders every time
    the user deleted them. Instead we omit that path (and every
    subpath) from the returned map; the caller's file loop then
    sees them as unreachable and skips the contained files.
    """
    from sqlalchemy.exc import IntegrityError

    async def _get_or_create(
        parent_id: UUID | None, name: str, remote_path: str,
    ) -> UUID | None:
        """Returns the folder id, or None when the user has explicitly
        excluded this remote_path (soft-deleted synced folder)."""
        # First: is there a SOFT-DELETED synced folder at this exact
        # remote_path? If so, that's the user's "exclude" marker — do
        # not resurrect, do not create a new sibling with the same
        # name. Return None so the caller drops the whole subtree.
        excluded = (
            await session.execute(
                select(Folder.id).where(
                    Folder.user_id == user.id,
                    Folder.cloud_provider == provider,
                    Folder.cloud_remote_path == remote_path,
                    Folder.deleted_at.is_not(None),
                )
                .limit(1)
            )
        ).first()
        if excluded is not None:
            return None

        existing = (
            await session.execute(
                select(Folder).where(
                    Folder.user_id == user.id,
                    Folder.parent_folder_id == parent_id if parent_id is not None
                        else Folder.parent_folder_id.is_(None),
                    Folder.deleted_at.is_(None),
                    Folder.name == name,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Backfill provenance on first sync after migration 0039
            # — pre-existing rows have NULL cloud_* fields, but they
            # ARE synced folders. Tag them so future deletes are
            # honoured.
            if not existing.cloud_provider:
                existing.cloud_provider = provider
                existing.cloud_remote_path = remote_path
            return existing.id
        folder = Folder(
            user_id=user.id, parent_folder_id=parent_id, name=name,
            cloud_provider=provider,
            cloud_remote_path=remote_path,
        )
        session.add(folder)
        try:
            await session.flush()
        except IntegrityError:
            # Raced with another sync run on the same user. Pull it.
            await session.rollback()
            existing = (
                await session.execute(
                    select(Folder).where(
                        Folder.user_id == user.id,
                        Folder.parent_folder_id == parent_id if parent_id is not None
                            else Folder.parent_folder_id.is_(None),
                        Folder.deleted_at.is_(None),
                        Folder.name == name,
                    )
                )
            ).scalar_one()
            return existing.id
        return folder.id

    # Root folder. Its `cloud_remote_path` is the empty string ""
    # which acts as a sentinel — the user can soft-delete the root
    # "Google Drive" folder and the whole subtree gets dropped
    # from sync without us needing to enumerate every descendant.
    root_id = await _get_or_create(None, root_name, "")
    if root_id is None:
        # User has excluded the root folder entirely. Nothing more
        # to materialize; caller treats this as "no folders, drop
        # everything from this sync run."
        return {}
    folder_ids_by_path: dict[str, UUID] = {"": root_id}

    # Each parent path looks like `a/b/c`. We collect each prefix +
    # build them in order so deeper folders find their parent.
    needed_paths: set[str] = set()
    for path in all_remote_parent_paths:
        if not path:
            continue
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            needed_paths.add("/".join(parts[:i]))

    for path in sorted(needed_paths, key=lambda p: p.count("/")):
        parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
        leaf_name = path.rsplit("/", 1)[-1]
        parent_id = folder_ids_by_path.get(parent_path)
        if parent_id is None:
            # Either the parent path didn't get built (ancestor was
            # excluded) — drop this whole subtree too.
            continue
        fid = await _get_or_create(parent_id, leaf_name, path)
        if fid is not None:
            folder_ids_by_path[path] = fid

    return folder_ids_by_path


async def _provider_download(
    provider: CloudProvider, refresh_token: str, entry: dict,
) -> bytes:
    """Provider-agnostic download. Each provider builds its own client
    + uses its own download helper; we run blocking work in a thread
    so the asyncio loop stays free. The Dropbox helper is already
    httpx-native (async) so it's awaited directly."""
    import asyncio
    if provider == "google_drive":
        return await asyncio.to_thread(
            lambda: _drive_download(_drive_client(refresh_token), entry["remote_id"]),
        )
    if provider == "dropbox":
        return await _dropbox_download(refresh_token, entry)
    raise CloudSyncNotConfigured(f"Provider '{provider}' not implemented.")


async def _drive_collect_entries(refresh_token: str) -> list[dict]:
    """Drive enumerator. Builds the parent-path map up-front (one
    list call for folder metadata) then walks the image listing,
    resolving each file's deepest path component."""
    import asyncio

    def _work() -> list[dict]:
        # CS10 — every `.execute()` call below is wrapped via
        # `with_drive_retry`, so transient Drive 429 / 5xx errors get
        # retried with exponential backoff instead of aborting the
        # listing partway through.
        from backend.cloud_sync_retry import with_drive_retry

        drive = _drive_client(refresh_token)
        # Map of folder_id → (name, parent_id) for every folder the
        # user owns. We resolve full paths from this dict so the
        # listing doesn't need an extra HTTP call per file.
        folder_map: dict[str, tuple[str, str | None]] = {}
        page_token: str | None = None
        while True:
            resp = with_drive_retry(
                lambda: drive.files().list(
                    q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                    spaces="drive",
                    fields="nextPageToken, files(id,name,parents)",
                    pageToken=page_token,
                    pageSize=200,
                ).execute(),
                op="drive.files.list(folders)",
            )
            for f in resp.get("files", []):
                parents = f.get("parents") or []
                folder_map[f["id"]] = (f["name"], parents[0] if parents else None)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        def resolve_path(parent_id: str | None) -> str:
            parts: list[str] = []
            cursor = parent_id
            # Avoid loops via depth cap.
            for _ in range(50):
                if cursor is None or cursor not in folder_map:
                    break
                name, next_parent = folder_map[cursor]
                parts.append(name)
                cursor = next_parent
            return "/".join(reversed(parts))

        out: list[dict] = []
        page_token = None
        # 2026-05 change: drop the `mimeType contains 'image/'` filter.
        # Before this, sync only pulled photos — the user's videos /
        # documents / audio / archives never made it into neuthek even
        # though we accept all of them on direct upload. Now we list
        # EVERY non-folder file in the user's Drive and let the import
        # path filter by what neuthek can actually handle. Google's
        # native Docs / Sheets / Slides (mimeType `application/vnd.google-apps.*`)
        # are skipped server-side — they can't be downloaded as bytes
        # without an export step, which is a separate workstream.
        #
        # Filter: NOT a Google-native doc (those need export) AND NOT
        # trashed. Drive returns folders separately; we already walked
        # those above into `folder_map`.
        DRIVE_FILES_QUERY = (
            "trashed = false "
            "and not mimeType contains 'application/vnd.google-apps' "
            "and mimeType != 'application/vnd.google-apps.folder'"
        )
        while True:
            resp = with_drive_retry(
                lambda: drive.files().list(
                    q=DRIVE_FILES_QUERY,
                    spaces="drive",
                    fields=(
                        "nextPageToken, "
                        "files(id,name,mimeType,modifiedTime,size,parents,md5Checksum)"
                    ),
                    pageToken=page_token,
                    pageSize=100,
                ).execute(),
                op="drive.files.list(files)",
            )
            for f in resp.get("files", []):
                parents = f.get("parents") or []
                parent_id = parents[0] if parents else None
                parent_path = resolve_path(parent_id) if parent_id else ""
                # Drive's md5Checksum is hex; convert to bytes for the
                # `cloud_files.sha256` column. Drive doesn't expose
                # sha256, but md5 is sufficient for "did the bytes
                # change" — collisions are exceedingly rare in this
                # context and a false-positive would just trigger a
                # harmless re-download.
                md5_hex = f.get("md5Checksum") or ""
                sha = bytes.fromhex(md5_hex.ljust(64, "0"))[:32] if md5_hex else None
                # Drive returns `size` as a stringified int (e.g. "1048576")
                # for regular files, and omits it entirely for Google-native
                # docs (which we already filter out above). Defensive int()
                # so an unexpected non-integer value collapses to 0 rather
                # than crashing the listing.
                size_raw = f.get("size")
                try:
                    size_bytes = int(size_raw) if size_raw is not None else 0
                except (TypeError, ValueError):
                    size_bytes = 0
                out.append({
                    "remote_id": f["id"],
                    "name": f["name"],
                    "mime_type": f.get("mimeType"),
                    "modified_at": _parse_drive_time(f.get("modifiedTime")),
                    "remote_path": f["name"],
                    "remote_parent_path": parent_path,
                    "sha256": sha,
                    "size_bytes": size_bytes,
                })
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    return await asyncio.to_thread(_work)


# ---------- Provider stats (Drive size discovery) ----------
#
# Surfaces "how big is this account's library on the provider side"
# so the storage panel can honestly say "your Drive holds 28 GB
# across 1,247 files; we've mirrored 59 of those here." Without this,
# the user sees neuthek's local 1 GB usage and can't reconcile it
# with the 28 GB Drive zip they downloaded directly from Google.


async def _drive_folder_stats(refresh_token: str) -> dict:
    """Walk all non-folder, non-Google-native files in this user's Drive
    and sum `size`. Excludes trashed items. One-shot, paginates server-
    side. Returns `{file_count, total_bytes}` — the same shape we'd
    show in the storage panel header.

    Drive's `about.get()` is global account quota (all of Google,
    not just the user's library) so we walk files explicitly.
    Reasonably fast — `pageSize=1000` * O(1) per page; an account
    with 50K files returns in ~3 s.
    """
    import asyncio

    def _work() -> dict:
        drive = _drive_client(refresh_token)
        count = 0
        total = 0
        page_token: str | None = None
        # Same query shape as the enumerator above — drop Google-native
        # docs (`application/vnd.google-apps.*`) because they have no
        # `size` and can't be downloaded as bytes.
        q = (
            "trashed = false "
            "and not mimeType contains 'application/vnd.google-apps' "
            "and mimeType != 'application/vnd.google-apps.folder'"
        )
        while True:
            resp = drive.files().list(
                q=q,
                spaces="drive",
                fields="nextPageToken, files(id,size)",
                pageToken=page_token,
                pageSize=1000,
            ).execute()
            for f in resp.get("files", []):
                count += 1
                # `size` is a string in Drive's JSON; cast safely.
                sz = f.get("size")
                if sz:
                    try:
                        total += int(sz)
                    except (TypeError, ValueError):
                        pass
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return {"file_count": count, "total_bytes": total}

    return await asyncio.to_thread(_work)


async def provider_folder_stats(
    provider: str, refresh_token_enc: bytes, link: "CloudLink | None" = None,
) -> dict | None:
    """Dispatch to the per-provider stats walker. Returns None on any
    failure (revoked token / network down / Google API hiccup) so the
    storage panel can degrade gracefully — the rest of the page
    renders fine, the linked-services row just hides the "X total
    in Drive" line. Decryption errors are caught here so a misconfigured
    Fernet key doesn't kill the whole `/storage/usage` call.

    iCloud / Proton Drive / MEGA take the optional `link` kwarg
    because their stats walkers need the CloudLink row (iCloud for
    the cookie_directory path; Proton + MEGA for the per-link rclone
    config file) rather than a decrypted token string. The OAuth
    providers ignore it.
    """
    from backend.secret_box import decrypt

    if provider == "icloud":
        if link is None:
            return None
        try:
            return await _icloud_folder_stats(link)
        except Exception:
            logger.exception("provider_folder_stats: icloud walk failed")
            return None
    if provider == "proton_drive":
        if link is None:
            return None
        try:
            return await _proton_drive_folder_stats(link)
        except Exception:
            logger.exception("provider_folder_stats: proton_drive walk failed")
            return None
    if provider == "mega":
        if link is None:
            return None
        try:
            return await _mega_folder_stats(link)
        except Exception:
            logger.exception("provider_folder_stats: mega walk failed")
            return None

    try:
        refresh_token = decrypt(refresh_token_enc)
    except Exception:
        logger.warning("provider_folder_stats: refresh-token decrypt failed for %s", provider)
        return None
    try:
        if provider == "google_drive":
            return await _drive_folder_stats(refresh_token)
        if provider == "dropbox":
            return await _dropbox_folder_stats(refresh_token)
    except Exception:
        logger.exception("provider_folder_stats: %s walk failed", provider)
    return None


# ---------- token revocation ---------------------------------------------


# Google's OAuth 2.0 token revocation endpoint. Revoking a refresh
# token there invalidates the whole grant — every access token + the
# refresh token itself, across every API the original consent
# covered. Documented at:
#   https://developers.google.com/identity/protocols/oauth2/web-server#tokenrevoke
GOOGLE_OAUTH_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


async def revoke_google_refresh_token(refresh_token: str) -> bool:
    """POST to Google's revoke endpoint. Returns True iff Google
    confirmed the revocation, False on any other outcome.

    Audit CS3 — DELETE /cloud/links/{id} used to just drop the
    `cloud_links` row, leaving Google's grant active on the user's
    Google account. Users had to also visit
    `myaccount.google.com/permissions` to actually disconnect — a
    silent privacy footgun. We now call revoke before the DB delete
    so disconnecting in neuthek really means disconnecting.

    The revoke is best-effort:
      * 200 → success.
      * 400 with `invalid_token` → already revoked (or never valid).
        We treat that as a no-op success because the user's intent
        is satisfied.
      * Network error / 5xx / anything else → False. The caller
        proceeds with the local delete regardless; the row going
        away matters more than the remote-side hygiene, and the
        user can manually revoke from Google's UI if they really
        need to.

    Always runs under a tight timeout so a hung Google endpoint
    can't stall the DELETE request indefinitely.
    """
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("revoke_google_refresh_token: httpx not installed; skipping revoke")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                GOOGLE_OAUTH_REVOKE_URL,
                data={"token": refresh_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception:
        logger.exception("revoke_google_refresh_token: network error")
        return False
    if resp.status_code == 200:
        return True
    if resp.status_code == 400:
        # `invalid_token` here means the token is already gone or
        # never existed — user's intent is satisfied either way.
        try:
            body = resp.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and body.get("error") == "invalid_token":
            logger.info(
                "revoke_google_refresh_token: token already revoked / unknown"
            )
            return True
    logger.warning(
        "revoke_google_refresh_token: unexpected status=%s body=%s",
        resp.status_code, resp.text[:200],
    )
    return False


# ---------- low-level Drive helpers --------------------------------------


def _drive_client(refresh_token: str):
    """Build an authenticated Drive v3 client.

    IMPORTANT: don't pass `scopes=` to the Credentials constructor.
    `google-auth`'s refresh-token grant includes any non-empty
    `scopes` field as the `scope` parameter in the POST body to
    Google's token endpoint — and Google's refresh endpoint REJECTS
    requests that ask for scopes the user's original consent didn't
    cover.

    Users who linked Drive before we added `openid email profile`
    to the scope list (so the OAuth grant doubles as a Google
    sign-in link) still have a refresh token authorized only for
    `drive.readonly`. Re-requesting all four at refresh time blows
    up with:

        google.auth.exceptions.RefreshError:
          ('invalid_scope: Bad Request',
           {'error': 'invalid_scope', 'error_description': 'Bad Request'})

    Leaving scopes off the refresh request lets Google echo back
    whatever scopes were originally granted — works regardless of
    whether the user authorized 1 scope or 4.
    """
    from google.auth.transport.requests import Request  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from googleapiclient.discovery import build  # type: ignore

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        # scopes intentionally omitted — see docstring above.
    )
    creds.refresh(Request())
    # cache_discovery=False silences a stale-cache warning that fires
    # on every cold start otherwise.
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _drive_list_images(drive) -> Iterable[dict]:
    """Generator over the user's image files in Drive (paginated)."""
    page_token: str | None = None
    while True:
        resp = drive.files().list(
            q="mimeType contains 'image/' and trashed = false",
            spaces="drive",
            fields="nextPageToken, files(id,name,mimeType,modifiedTime,size)",
            pageToken=page_token,
            pageSize=100,
        ).execute()
        for f in resp.get("files", []):
            yield f
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def _drive_download(drive, file_id: str) -> bytes:
    """Download a Drive file's bytes.

    Wrapped in `with_drive_retry` (CS10) so a transient Drive 429
    / 5xx on `next_chunk()` retries with exponential backoff instead
    of skipping the file. Per-chunk retries would be tighter but
    `MediaIoBaseDownload` is stateful and re-running it from scratch
    is simpler than threading retry into the chunk loop."""
    from io import BytesIO

    from googleapiclient.http import MediaIoBaseDownload  # type: ignore

    from backend.cloud_sync_retry import with_drive_retry

    def _download_once() -> bytes:
        request = drive.files().get_media(fileId=file_id)
        buf = BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    return with_drive_retry(_download_once, op=f"drive.files.get_media({file_id})")


def _parse_drive_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # Drive returns RFC 3339 with a trailing Z.
    raw = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _parse_iso_time(raw: str | None) -> datetime | None:
    """ISO-8601 parser used by Dropbox (`server_modified`). Returns
    the same RFC 3339 / ISO-8601 shape Google does, but kept as a
    separate helper so future per-provider quirks (Dropbox's
    millisecond precision, optional timezone offsets) can land
    here without changing Drive's path."""
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        # Some providers occasionally emit fractional seconds with
        # more than 6 digits (Python's max). Strip past the dot if
        # we can recover.
        try:
            head, frac = raw.split(".", 1)
            tz_idx = max(frac.find("+"), frac.find("-"))
            if tz_idx > 0:
                frac, tz = frac[:tz_idx], frac[tz_idx:]
            else:
                tz = ""
            return datetime.fromisoformat(f"{head}.{frac[:6]}{tz}")
        except Exception:
            return None


# ---------- preflight check ----------------------------------------------


def _verify_encryption_ready() -> None:
    """Round-trip a tiny payload to confirm the Fernet key works
    *before* we hand the user off to Google. Surfaces missing/invalid
    keys as a clear 503 in the API layer."""
    try:
        decrypt_token(encrypt_token("ping"))
    except MisconfiguredEncryption as exc:
        raise CloudSyncNotConfigured(str(exc)) from exc
