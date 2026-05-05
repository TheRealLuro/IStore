"""C2 cloud-sync worker — Google Drive (read-only).

Drive is the first provider we wire up because it has the cleanest
public API and the most consistent OAuth story. GitHub / Dropbox /
OneDrive land later behind the same `connect_provider` /
`complete_oauth` / `sync_user_provider` interface.

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

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import CloudFile, CloudLink, User
from backend.secret_box import (
    MisconfiguredEncryption,
    decrypt as decrypt_token,
    encrypt as encrypt_token,
)

logger = logging.getLogger(__name__)

CloudProvider = Literal["google_drive", "github", "dropbox", "onedrive"]


PROVIDER_SCOPES: dict[CloudProvider, list[str]] = {
    # Drive's `drive.readonly` is the smallest scope that lists+downloads
    # the user's files. We deliberately avoid `drive` (full r/w) so we
    # can never silently corrupt the user's source.
    "google_drive": ["https://www.googleapis.com/auth/drive.readonly"],
    "github": ["repo", "read:user"],
    "dropbox": ["files.content.read"],
    "onedrive": ["Files.Read.All", "offline_access"],
}


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


def connect_provider(user_id: UUID, provider: CloudProvider) -> OAuthHandoff:
    """Build the auth URL the FE should send the user to.

    `state` carries the user_id (signed by Google's flow library) so
    the callback can resolve who started the flow without trusting the
    browser.
    """
    if provider != "google_drive":
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' is not implemented yet. Drive only."
        )

    # Verify encryption is configured *before* we send the user out to
    # Google — better to fail upfront than after the user authenticated.
    _verify_encryption_ready()

    flow = _google_flow()
    # `access_type=offline` is what makes Google return a refresh token.
    # `prompt=consent` forces a refresh-token grant even on re-auth so
    # we never end up with a link row missing its refresh token.
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=str(user_id),
    )
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
    state: str,  # noqa: ARG001 — Google verifies this in fetch_token
) -> int:
    """Exchange `code` for tokens, encrypt + store, return cloud_links.id."""
    if provider != "google_drive":
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' is not implemented yet."
        )

    flow = _google_flow()
    # `fetch_token` performs the code → access_token + refresh_token
    # exchange. Google verifies the state internally against the value
    # we set in connect_provider (we passed user_id in).
    flow.fetch_token(code=code)
    creds = flow.credentials

    if not creds.refresh_token:
        # Without a refresh token we can't sync after the access token
        # expires (~1 hour). Force the user to retry — usually means
        # they hit "Continue" without "Allow" or had a stale grant.
        raise CloudSyncNotConfigured(
            "Google did not return a refresh token. Revoke any prior "
            "consent at https://myaccount.google.com/permissions and "
            "try again."
        )

    encrypted = encrypt_token(creds.refresh_token)
    scopes = ",".join(creds.scopes or [])

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
    """Pull the user's Drive listing, ingest new files. Returns a small
    summary dict. Designed to be safe to call repeatedly."""
    if provider != "google_drive":
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
    drive = _drive_client(refresh_token)

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise CloudSyncNotConfigured("User not found.")

    # Only ingest images for now (cleanest mapping to our pipeline).
    # Drive query: image MIME types, owned by the user, not in trash.
    listed = _drive_list_images(drive)

    seen = 0
    pulled = 0
    for entry in listed:
        seen += 1
        existing = (
            await session.execute(
                select(CloudFile).where(
                    CloudFile.user_id == user_id,
                    CloudFile.provider == provider,
                    CloudFile.remote_id == entry["id"],
                )
            )
        ).scalar_one_or_none()
        remote_mod = _parse_drive_time(entry.get("modifiedTime"))
        if existing is not None and existing.remote_modified == remote_mod:
            continue  # unchanged — skip

        try:
            blob = _drive_download(drive, entry["id"])
        except Exception:
            logger.exception("drive download failed for %s", entry["id"])
            continue

        # Ingest through the standard image pipeline so MIME / magic-byte
        # validation, bandit compression, and consent gates all apply.
        from backend.image import store_upload  # local import — avoid cycles

        try:
            image = await store_upload(
                session, user, entry["name"], blob, entry.get("mimeType")
            )
        except Exception:
            logger.exception("ingest failed for %s", entry["name"])
            continue

        if existing is None:
            session.add(
                CloudFile(
                    user_id=user_id,
                    provider=provider,
                    remote_id=entry["id"],
                    remote_path=entry["name"],
                    local_image_id=image.id,
                    remote_modified=remote_mod,
                    last_synced_at=datetime.now(timezone.utc),
                )
            )
        else:
            existing.local_image_id = image.id
            existing.remote_path = entry["name"]
            existing.remote_modified = remote_mod
            existing.last_synced_at = datetime.now(timezone.utc)
        pulled += 1

    link.last_synced_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info(
        "cloud_sync: sync user=%s provider=%s seen=%d pulled=%d",
        user_id, provider, seen, pulled,
    )
    return {"seen": seen, "pulled": pulled, "provider": provider}


# ---------- low-level Drive helpers --------------------------------------


def _drive_client(refresh_token: str):
    """Build an authenticated Drive v3 client."""
    from google.auth.transport.requests import Request  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from googleapiclient.discovery import build  # type: ignore

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=PROVIDER_SCOPES["google_drive"],
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
    """Download a Drive file's bytes."""
    from googleapiclient.http import MediaIoBaseDownload  # type: ignore
    from io import BytesIO

    request = drive.files().get_media(fileId=file_id)
    buf = BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def _parse_drive_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # Drive returns RFC 3339 with a trailing Z.
    raw = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
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
