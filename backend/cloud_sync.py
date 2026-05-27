"""C2 cloud-sync worker — Google Drive (read-only).

Drive is the only provider currently wired up. Dropbox / OneDrive
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

CloudProvider = Literal["google_drive", "dropbox", "onedrive"]


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
    # §C4.6 — OneDrive / Microsoft Graph scopes. `Files.Read.All` is
    # the equivalent of Drive's `drive.readonly` — list and read every
    # file the user has access to. `offline_access` is what makes
    # Microsoft return a refresh token (same role as Google's
    # `access_type=offline`). `User.Read` lets us pull the account
    # email + display name for the connected-account chip.
    "onedrive": [
        "Files.Read.All",
        "offline_access",
        "User.Read",
    ],
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

def _provider_status(provider: str) -> str:
    if provider == "google_drive":
        if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
            return "needs_setup"
        return "available"
    if provider == "onedrive":
        if not settings.onedrive_oauth_client_id or not settings.onedrive_oauth_client_secret:
            return "needs_setup"
        return "available"
    if provider == "dropbox":
        if not settings.dropbox_oauth_client_id or not settings.dropbox_oauth_client_secret:
            return "needs_setup"
        return "available"
    return "coming_soon"


def list_providers() -> list[dict]:
    """Provider catalog for the FE Cloud sync panel.

    The first three entries map to fully-wired providers (status
    derived from settings); the rest are placeholder slots so the
    UI shows the user "we know about these, they're on the
    roadmap". Adding a provider means appending here + flipping its
    status helper above.
    """
    return [
        {
            "id": "google_drive",
            "name": "Google Drive",
            "kind": "oauth2",
            "status": _provider_status("google_drive"),
            "blurb": "Mirror your Drive into neuthek. Read-only — we never write to your Drive.",
            "docs": "https://console.cloud.google.com/apis/credentials",
        },
        {
            "id": "onedrive",
            "name": "OneDrive",
            "kind": "oauth2",
            "status": _provider_status("onedrive"),
            "blurb": "Mirror your OneDrive (personal or work / school). Read-only.",
            "docs": "https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        },
        {
            "id": "dropbox",
            "name": "Dropbox",
            "kind": "oauth2",
            "status": _provider_status("dropbox"),
            "blurb": "Mirror your Dropbox. Read-only access; your files stay on Dropbox too.",
            "docs": "https://www.dropbox.com/developers/apps",
        },
        {
            "id": "icloud",
            "name": "iCloud Drive",
            "kind": "app_password",
            "status": "coming_soon",
            "blurb": "iCloud lacks a standard OAuth API. Coming via app-specific password support.",
            "docs": None,
        },
        {
            "id": "mega",
            "name": "MEGA",
            "kind": "credentials",
            "status": "coming_soon",
            "blurb": "End-to-end encrypted source. Decryption only in the viewer.",
            "docs": None,
        },
        {
            "id": "box",
            "name": "Box",
            "kind": "oauth2",
            "status": "coming_soon",
            "blurb": "Box.com mirror. OAuth shape ready; sync engine queued.",
            "docs": None,
        },
        {
            "id": "pcloud",
            "name": "pCloud",
            "kind": "oauth2",
            "status": "coming_soon",
            "blurb": "pCloud mirror. OAuth shape ready; sync engine queued.",
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


# ---------- §C4.6 — OneDrive (Microsoft Graph) ----------------------------
#
# Microsoft identity platform v2.0 endpoints:
#   AUTH:  https://login.microsoftonline.com/common/oauth2/v2.0/authorize
#   TOKEN: https://login.microsoftonline.com/common/oauth2/v2.0/token
#
# Tenant `common` accepts BOTH personal Microsoft accounts (Outlook,
# Live, Hotmail) AND work/school accounts. Use `consumers` to limit
# to personal accounts, or a tenant GUID to limit to one org. The
# rendered app at portal.azure.com must be registered with
# "Accounts in any organizational directory and personal Microsoft
# accounts" for `common` to work.
#
# Token shape (response from /token):
#   {access_token, refresh_token, expires_in, scope, token_type, ext_expires_in}
# Refresh tokens are long-lived (~90d sliding) but get rotated on
# refresh — every refresh response includes a new refresh_token that
# replaces the old one. The Google Drive flow doesn't rotate; OneDrive
# does.

_ONEDRIVE_AUTH_ENDPOINT_FMT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
_ONEDRIVE_TOKEN_ENDPOINT_FMT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


def _onedrive_tenant() -> str:
    """Tenant path segment for the Microsoft OAuth endpoint.

    `common` (default) accepts any Microsoft account, but the app
    registration's `signInAudience` must be set to
    `AzureADandPersonalMicrosoftAccount` for the consumer routing
    branch to work. Many users register the app as "My organization
    only" first and hit `unauthorized_client: not enabled for
    consumers` here. Setting ONEDRIVE_OAUTH_TENANT to the specific
    Directory (tenant) GUID from the app's Overview page swaps the
    URL to /{tenant_guid}/oauth2/v2.0/authorize, which works with
    "My organization only" without further Azure-side changes.
    """
    t = (settings.onedrive_oauth_tenant or "").strip()
    return t or "common"


def _onedrive_auth_url(state: str) -> tuple[str, str | None]:
    """Build the Microsoft consent URL + return (url, code_verifier).

    PKCE: we generate a code_verifier + S256 challenge so even if our
    redirect URI ever leaks via a referrer log, an attacker without
    the verifier can't exchange the intercepted code.
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
        "client_id": settings.onedrive_oauth_client_id,
        "response_type": "code",
        "redirect_uri": settings.onedrive_oauth_redirect_uri,
        "response_mode": "query",
        "scope": " ".join(PROVIDER_SCOPES["onedrive"]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # `prompt=select_account` lets the user pick which Microsoft
        # account to connect (useful when they have both a personal
        # and a work account signed in).
        "prompt": "select_account",
    }
    endpoint = _ONEDRIVE_AUTH_ENDPOINT_FMT.format(tenant=_onedrive_tenant())
    return f"{endpoint}?{urlencode(params)}", verifier


async def _onedrive_exchange_code(code: str, verifier: str | None) -> dict:
    """POST to the token endpoint to swap `code` for tokens. Returns
    the parsed JSON response or raises on any non-2xx."""
    import httpx
    data = {
        "client_id": settings.onedrive_oauth_client_id,
        "client_secret": settings.onedrive_oauth_client_secret,
        "code": code,
        "redirect_uri": settings.onedrive_oauth_redirect_uri,
        "grant_type": "authorization_code",
        "scope": " ".join(PROVIDER_SCOPES["onedrive"]),
    }
    if verifier:
        data["code_verifier"] = verifier
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            _ONEDRIVE_TOKEN_ENDPOINT_FMT.format(tenant=_onedrive_tenant()),
            data=data,
        )
    if r.status_code >= 400:
        # Don't echo the body — it may contain the OAuth error
        # description with internal endpoint info. Log + raise a
        # generic CloudSyncNotConfigured so the FE shows a clean
        # "could not connect" message.
        logger.warning(
            "onedrive token exchange failed: %s %s",
            r.status_code, r.text[:200],
        )
        raise CloudSyncNotConfigured(
            "OneDrive token exchange failed. Try connecting again, "
            "or check that the app's redirect URI matches "
            "ONEDRIVE_OAUTH_REDIRECT_URI exactly."
        )
    return r.json()


async def _onedrive_refresh_access_token(refresh_token: str) -> str:
    """Trade a refresh_token for a fresh access_token.

    Microsoft rotates refresh tokens on every refresh — the response
    carries a NEW refresh_token that replaces the one we just used.
    The Google flow doesn't rotate; OneDrive does. We currently
    discard the rotated token (don't write it back to cloud_links)
    because the old one stays valid for a short grace period and
    the next sync will refresh again anyway. A future improvement
    would persist the rotation so we always have the freshest
    refresh token on hand.

    Raises CloudSyncNotConfigured on any non-2xx so the API layer
    surfaces it as 503 "not_configured" to the FE.
    """
    import httpx
    data = {
        "client_id": settings.onedrive_oauth_client_id,
        "client_secret": settings.onedrive_oauth_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": " ".join(PROVIDER_SCOPES["onedrive"]),
    }
    endpoint = _ONEDRIVE_TOKEN_ENDPOINT_FMT.format(tenant=_onedrive_tenant())
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(endpoint, data=data)
    if r.status_code >= 400:
        logger.warning(
            "onedrive refresh failed: %s %s",
            r.status_code, r.text[:200],
        )
        raise CloudSyncNotConfigured(
            "OneDrive refresh token rejected. The user may need to "
            "reconnect from Settings → Cloud sync."
        )
    payload = r.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise CloudSyncNotConfigured("OneDrive refresh returned no access_token.")
    return access_token


_MS_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def _onedrive_collect_entries(refresh_token: str) -> list[dict]:
    """Walk every non-folder DriveItem in the user's OneDrive and
    return entry dicts in the same shape `sync_user_provider`
    expects from `_drive_collect_entries`.

    Strategy: BFS over `/me/drive/root/children` → recurse into
    each folder via `/me/drive/items/{id}/children`. Each page
    carries `@odata.nextLink` for pagination. We DON'T use Graph's
    `/me/drive/root/search(q='')` because the search index lags
    fresh uploads by minutes; recursive children listing returns
    consistent results immediately.

    Skips:
      - OneNote sections / notebooks (mime starts with
        application/vnd.ms-onenote) — they're container objects,
        not single files.
      - Files marked `package` (Microsoft's wrapper for
        multi-file documents). Mirroring those would corrupt
        them; the user can re-export to a single file if needed.
    """
    access_token = await _onedrive_refresh_access_token(refresh_token)
    headers = {"Authorization": f"Bearer {access_token}"}

    import httpx
    out: list[dict] = []

    async def _walk_folder(folder_id: str, parent_path: str) -> None:
        next_url = f"{_MS_GRAPH_BASE}/me/drive/items/{folder_id}/children"
        async with httpx.AsyncClient(timeout=60.0) as client:
            while next_url:
                # Trim @odata.nextLink response bytes: only ask Graph
                # for the fields we'll actually read. `select` cuts
                # the response ~3x on accounts with rich metadata.
                params = {
                    "$select": (
                        "id,name,size,file,folder,package,parentReference,"
                        "lastModifiedDateTime"
                    ),
                    "$top": 200,
                } if "$select" not in next_url else None
                r = await client.get(next_url, headers=headers, params=params)
                if r.status_code == 401:
                    # Access token expired mid-walk (rare; we refresh
                    # at the start, but the token has a 1h lifetime
                    # and a deep walk could exceed it). Refresh and
                    # try this page again exactly once before giving
                    # up — repeating refresh would mask a real auth
                    # break.
                    new_token = await _onedrive_refresh_access_token(refresh_token)
                    headers["Authorization"] = f"Bearer {new_token}"
                    r = await client.get(next_url, headers=headers, params=params)
                if r.status_code >= 400:
                    logger.warning(
                        "onedrive list failed: %s %s",
                        r.status_code, r.text[:200],
                    )
                    # Surface Microsoft's most common per-account
                    # failure modes in plain English. Without these
                    # the FE just shows a generic "failed" toast and
                    # the user has no idea whether to reconnect, pay
                    # for a license, or escalate to IT.
                    body_lc = r.text.lower()
                    if "spo license" in body_lc or "no onedrive" in body_lc:
                        msg = (
                            "Your Microsoft account doesn't have a "
                            "OneDrive license attached. Personal accounts "
                            "(Outlook.com / Hotmail / Live) include OneDrive; "
                            "work/school accounts need a Microsoft 365 "
                            "subscription with OneDrive for Business "
                            "enabled by your IT admin."
                        )
                    elif r.status_code in (401, 403):
                        msg = (
                            "OneDrive denied access. Reconnect from "
                            "Settings → Cloud sync and re-grant the "
                            "Files.Read.All permission."
                        )
                    else:
                        msg = (
                            "OneDrive listing failed. Try again later, "
                            "or reconnect from Settings → Cloud sync."
                        )
                    raise CloudSyncNotConfigured(msg)
                payload = r.json()
                for item in payload.get("value", []):
                    if item.get("package"):
                        continue  # multi-file wrapper, skip
                    if "folder" in item:
                        sub_path = (
                            f"{parent_path}/{item['name']}"
                            if parent_path else item["name"]
                        )
                        # Recurse into the sub-folder. Append to a
                        # work-queue in real code; for clarity we
                        # nest directly here. OneDrive folder
                        # depth is capped by the service so the
                        # recursion bottoms out.
                        await _walk_folder(item["id"], sub_path)
                        continue
                    if "file" not in item:
                        continue  # neither file nor folder — shortcut, etc.
                    mime = item["file"].get("mimeType") or ""
                    if mime.startswith("application/vnd.ms-onenote"):
                        continue
                    hashes = item["file"].get("hashes") or {}
                    sha256_hex = hashes.get("sha256Hash") or ""
                    sha = bytes.fromhex(sha256_hex)[:32] if sha256_hex else None
                    out.append({
                        "remote_id": item["id"],
                        "name": item["name"],
                        "mime_type": mime,
                        "modified_at": _parse_iso_time(item.get("lastModifiedDateTime")),
                        "remote_path": item["name"],
                        "remote_parent_path": parent_path,
                        "sha256": sha,
                        "size_bytes": int(item.get("size") or 0),
                    })
                next_url = payload.get("@odata.nextLink")

    # `root` is the magic id for the user's drive root. Same
    # walk seed every time.
    await _walk_folder("root", "")
    return out


async def _onedrive_download(refresh_token: str, entry: dict) -> bytes:
    """Stream the bytes of a single DriveItem.

    Uses `/me/drive/items/{id}/content` which 302-redirects to a
    pre-signed download URL on Microsoft's storage CDN. httpx
    follows redirects by default; we just consume the bytes.
    """
    access_token = await _onedrive_refresh_access_token(refresh_token)
    import httpx
    url = f"{_MS_GRAPH_BASE}/me/drive/items/{entry['remote_id']}/content"
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
        if r.status_code >= 400:
            logger.warning(
                "onedrive download failed: id=%s %s %s",
                entry["remote_id"], r.status_code, r.text[:200],
            )
            raise CloudSyncNotConfigured(
                f"OneDrive download failed for {entry['name']!r}."
            )
        return r.content


async def _onedrive_folder_stats(refresh_token: str) -> dict:
    """Walk every file and sum sizes. Used by the storage panel's
    `linked_services` row to surface the user's OneDrive total."""
    entries = await _onedrive_collect_entries(refresh_token)
    return {
        "file_count": len(entries),
        "total_bytes": sum(e["size_bytes"] for e in entries),
    }


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
# main shape difference from Google + Microsoft.

_DROPBOX_AUTH_ENDPOINT = "https://www.dropbox.com/oauth2/authorize"
_DROPBOX_TOKEN_ENDPOINT = "https://api.dropboxapi.com/oauth2/token"


def _dropbox_auth_url(state: str) -> tuple[str, str | None]:
    """Build the Dropbox consent URL + return (url, code_verifier).

    Dropbox supports PKCE; we use it for the same defense-in-depth
    reason as the OneDrive flow.
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
    fields, which Google + Microsoft accept)."""
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
    the whole tree in pages of up to 2000 items each — much simpler
    than OneDrive's per-folder recursion. We page with
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
    if provider not in ("google_drive", "onedrive", "dropbox"):
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' is not implemented yet."
        )

    # Verify encryption is configured *before* we send the user out so
    # they don't authenticate just to hit a 500 on the callback.
    _verify_encryption_ready()

    signed_state = _build_state(user_id)
    if provider == "onedrive":
        # §C4.6 — Microsoft identity platform v2.0. PKCE is required
        # for the "Single-page application" / public-client tenants
        # but optional for confidential clients with a secret. We use
        # the confidential flow (server holds the secret) so PKCE
        # isn't strictly needed; including it anyway is harmless and
        # matches the Google flow's defense-in-depth posture.
        if not settings.onedrive_oauth_client_id:
            raise CloudSyncNotConfigured(
                "OneDrive OAuth client not configured. Set "
                "ONEDRIVE_OAUTH_CLIENT_ID + ONEDRIVE_OAUTH_CLIENT_SECRET "
                "in .env. Register the app at portal.azure.com."
            )
        auth_url, verifier = _onedrive_auth_url(signed_state)
        if verifier:
            await _stash_pkce_verifier(signed_state, verifier)
        state = signed_state
        return OAuthHandoff(auth_url=auth_url, state=state)
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
    if provider not in ("google_drive", "onedrive", "dropbox"):
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' is not implemented yet."
        )

    refresh_token_to_store: str | None = None
    scopes = ""

    if provider == "onedrive":
        # §C4.6 — Microsoft Graph token exchange. The verifier was
        # stashed under sha256(state) in connect_provider; pull it
        # back so the PKCE check on the token endpoint passes.
        verifier = await _pop_pkce_verifier(state)
        token_payload = await _onedrive_exchange_code(code, verifier)
        refresh_token_to_store = token_payload.get("refresh_token")
        if not refresh_token_to_store:
            raise CloudSyncNotConfigured(
                "OneDrive did not return a refresh token. Make sure "
                "the app is registered with `offline_access` in its "
                "API permissions and try connecting again."
            )
        scopes = token_payload.get("scope", "")

    elif provider == "dropbox":
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
    if provider not in ("google_drive", "onedrive", "dropbox"):
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

    refresh_token = decrypt_token(link.encrypted_refresh_token.encode("ascii"))

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalars().first()
    if user is None:
        raise CloudSyncNotConfigured("User not found.")

    if provider == "google_drive":
        entries = await _drive_collect_entries(refresh_token)
    elif provider == "onedrive":
        entries = await _onedrive_collect_entries(refresh_token)
    elif provider == "dropbox":
        entries = await _dropbox_collect_entries(refresh_token)
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
    conflicts: list[str] = []

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
            blob = await _provider_download(provider, refresh_token, entry)
        except Exception:
            logger.exception(
                "%s download failed for %s", provider, entry["remote_id"]
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
        "skipped=%d skipped_quota=%d conflicts=%d budget_remaining=%d",
        user_id, provider, seen, pulled, skipped_unchanged,
        skipped_over_quota, len(conflicts), budget_remaining,
    )
    return {
        "seen": seen,
        "pulled": pulled,
        "skipped_unchanged": skipped_unchanged,
        "skipped_over_quota": skipped_over_quota,
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
        "onedrive": "OneDrive",
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
    so the asyncio loop stays free. OneDrive + Dropbox helpers are
    already httpx-native (async) so they're awaited directly."""
    import asyncio
    if provider == "google_drive":
        return await asyncio.to_thread(
            lambda: _drive_download(_drive_client(refresh_token), entry["remote_id"]),
        )
    if provider == "onedrive":
        return await _onedrive_download(refresh_token, entry)
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
    provider: str, refresh_token_enc: bytes,
) -> dict | None:
    """Dispatch to the per-provider stats walker. Returns None on any
    failure (revoked token / network down / Google API hiccup) so the
    storage panel can degrade gracefully — the rest of the page
    renders fine, the linked-services row just hides the "X total
    in Drive" line. Decryption errors are caught here so a misconfigured
    Fernet key doesn't kill the whole `/storage/usage` call."""
    from backend.secret_box import decrypt

    try:
        refresh_token = decrypt(refresh_token_enc)
    except Exception:
        logger.warning("provider_folder_stats: refresh-token decrypt failed for %s", provider)
        return None
    try:
        if provider == "google_drive":
            return await _drive_folder_stats(refresh_token)
        if provider == "onedrive":
            return await _onedrive_folder_stats(refresh_token)
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
    """ISO-8601 parser used by OneDrive (`lastModifiedDateTime`) and
    Dropbox (`server_modified`). Both providers return the same
    RFC 3339 / ISO-8601 shape Google does, but I kept a separate
    helper so future per-provider quirks (Dropbox's millisecond
    precision; OneDrive's optional timezone offset) can land here
    without changing Drive's path."""
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
