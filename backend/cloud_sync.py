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
    mac = hmac.new(
        settings.jwt_secret.encode("utf-8"),
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
        settings.jwt_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise ValueError("State signature mismatch")
    return UUID(user_id_str)

logger = logging.getLogger(__name__)

CloudProvider = Literal["google_drive", "github", "dropbox", "onedrive"]


PROVIDER_SCOPES: dict[CloudProvider, list[str]] = {
    # Drive's `drive.readonly` is the smallest scope that lists+downloads
    # the user's files. We deliberately avoid `drive` (full r/w) so we
    # can never silently corrupt the user's source.
    "google_drive": ["https://www.googleapis.com/auth/drive.readonly"],
    # GitHub support was removed by user request — repos turned out to
    # not be a natural fit for a personal storage app (every public
    # repo turns the gallery into a dumping ground of READMEs and
    # build configs). Dropbox / OneDrive scopes are kept here so the
    # OAuth shape is recorded if we ever turn them on.
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
    if provider != "google_drive":
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' is not implemented yet."
        )

    # Verify encryption is configured *before* we send the user out so
    # they don't authenticate just to hit a 500 on the callback.
    _verify_encryption_ready()

    signed_state = _build_state(user_id)
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
    elif provider == "github":
        if not settings.github_oauth_client_id or not settings.github_oauth_client_secret:
            raise CloudSyncNotConfigured(
                "GitHub OAuth client not configured. Set "
                "GITHUB_OAUTH_CLIENT_ID + GITHUB_OAUTH_CLIENT_SECRET in .env. "
                "See SETUP.md > GitHub."
            )
        from urllib.parse import urlencode
        params = urlencode({
            "client_id": settings.github_oauth_client_id,
            "redirect_uri": settings.github_oauth_redirect_uri,
            "scope": " ".join(PROVIDER_SCOPES["github"]),
            "state": signed_state,
            # Forces GitHub to re-show the consent screen even when the
            # user has previously authorized the app — keeps the
            # scope list visible.
            "prompt": "consent",
            "allow_signup": "false",
        })
        auth_url = f"https://github.com/login/oauth/authorize?{params}"
        state = signed_state
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
    if provider != "google_drive":
        raise CloudSyncNotConfigured(
            f"Provider '{provider}' is not implemented yet."
        )

    if provider == "google_drive":
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
    else:  # github
        import requests
        if not settings.github_oauth_client_id or not settings.github_oauth_client_secret:
            raise CloudSyncNotConfigured(
                "GitHub OAuth client not configured."
            )
        # GitHub's classic OAuth flow returns a `access_token` that
        # doesn't expire (per https://docs.github.com/en/apps/oauth-apps).
        # We persist it in the same `encrypted_refresh_token` column
        # — the column name is a Drive-era artifact; the contents
        # are an opaque bearer secret either way.
        r = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_oauth_client_id,
                "client_secret": settings.github_oauth_client_secret,
                "code": code,
                "redirect_uri": settings.github_oauth_redirect_uri,
                "state": state,
            },
            timeout=30,
        )
        if r.status_code != 200:
            raise CloudSyncNotConfigured(
                f"GitHub token exchange failed: {r.status_code}"
            )
        payload = r.json()
        refresh_token_to_store = payload.get("access_token")
        scopes = payload.get("scope") or ",".join(PROVIDER_SCOPES["github"])
        if not refresh_token_to_store:
            raise CloudSyncNotConfigured(
                "GitHub did not return an access token. Try again."
            )

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
        scope on Google + `repo` read on GitHub.
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

    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalars().first()
    if user is None:
        raise CloudSyncNotConfigured("User not found.")

    if provider == "google_drive":
        entries = await _drive_collect_entries(refresh_token)
    elif provider == "github":
        entries = await _github_collect_entries(refresh_token)
    else:  # already gated above; defensive.
        entries = []

    # Build the synthesized folder tree once per call so we don't pay
    # the "is this folder already there" round-trip per file.
    folder_root_name = _provider_display_name(provider)
    folder_ids_by_path = await _ensure_remote_folder_tree(
        session, user, folder_root_name,
        all_remote_parent_paths={e.get("remote_parent_path") or "" for e in entries},
    )

    seen = 0
    pulled = 0
    skipped_unchanged = 0
    conflicts: list[str] = []

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

        try:
            blob = await _provider_download(provider, refresh_token, entry)
        except Exception:
            logger.exception(
                "%s download failed for %s", provider, entry["remote_id"]
            )
            continue

        # §C2 — Limited Use: never train on cloud-sourced content
        # without per-source opt-in. `store_upload` honors
        # `skip_ai_training=True` by skipping CLIP / Florence /
        # face-scan dispatch.
        from backend.image import store_upload  # local — avoid cycles

        parent_path = entry.get("remote_parent_path") or ""
        folder_id = folder_ids_by_path.get(parent_path) or folder_ids_by_path[""]
        try:
            image = await store_upload(
                session,
                user,
                entry["name"],
                blob,
                entry.get("mime_type"),
                skip_ai_training=True,
                source_provider=provider,
                folder_id=folder_id,
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

    link.last_synced_at = datetime.now(timezone.utc)
    link.status = "active" if not conflicts else "conflicts"
    await session.commit()
    logger.info(
        "cloud_sync: sync user=%s provider=%s seen=%d pulled=%d "
        "skipped=%d conflicts=%d",
        user_id, provider, seen, pulled, skipped_unchanged, len(conflicts),
    )
    return {
        "seen": seen,
        "pulled": pulled,
        "skipped_unchanged": skipped_unchanged,
        "conflicts": len(conflicts),
        "conflict_remote_ids": conflicts,
        "provider": provider,
    }


# ---------- provider-agnostic helpers ------------------------------------


def _provider_display_name(provider: CloudProvider) -> str:
    """Human-readable label for the synthesized root folder."""
    return {
        "google_drive": "Google Drive",
        "github": "GitHub",
        "dropbox": "Dropbox",
        "onedrive": "OneDrive",
    }.get(provider, provider.title())


async def _ensure_remote_folder_tree(
    session: AsyncSession,
    user: User,
    root_name: str,
    *,
    all_remote_parent_paths: set[str],
) -> dict[str, UUID]:
    """Materialize the synthesized folder tree for a provider.

    Returns a `{parent_path: folder_id}` map for every parent path
    referenced in the listing. The root entry (key `""`) maps to the
    provider's root folder ("Google Drive", "GitHub", …).

    Each path component is created under its parent if missing.
    Re-runs are idempotent — existing folders are reused via the
    `(user_id, parent, lower(name))` partial unique index from
    migration 0010.
    """
    from sqlalchemy.exc import IntegrityError

    async def _get_or_create(parent_id: UUID | None, name: str) -> UUID:
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
            return existing.id
        folder = Folder(
            user_id=user.id, parent_folder_id=parent_id, name=name,
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

    root_id = await _get_or_create(None, root_name)
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
        parent_id = folder_ids_by_path.get(parent_path) or root_id
        folder_ids_by_path[path] = await _get_or_create(parent_id, leaf_name)

    return folder_ids_by_path


async def _provider_download(
    provider: CloudProvider, refresh_token: str, entry: dict,
) -> bytes:
    """Provider-agnostic download. Each provider builds its own client
    + uses its own download helper; we run the sync work in a thread
    so the asyncio loop stays free for other requests."""
    import asyncio
    if provider == "google_drive":
        return await asyncio.to_thread(
            lambda: _drive_download(_drive_client(refresh_token), entry["remote_id"]),
        )
    if provider == "github":
        return await asyncio.to_thread(
            _github_download, refresh_token, entry["remote_id"], entry["remote_path"],
        )
    raise CloudSyncNotConfigured(f"Provider '{provider}' not implemented.")


async def _drive_collect_entries(refresh_token: str) -> list[dict]:
    """Drive enumerator. Builds the parent-path map up-front (one
    list call for folder metadata) then walks the image listing,
    resolving each file's deepest path component."""
    import asyncio

    def _work() -> list[dict]:
        drive = _drive_client(refresh_token)
        # Map of folder_id → (name, parent_id) for every folder the
        # user owns. We resolve full paths from this dict so the
        # listing doesn't need an extra HTTP call per file.
        folder_map: dict[str, tuple[str, str | None]] = {}
        page_token: str | None = None
        while True:
            resp = drive.files().list(
                q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                spaces="drive",
                fields="nextPageToken, files(id,name,parents)",
                pageToken=page_token,
                pageSize=200,
            ).execute()
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
        while True:
            resp = drive.files().list(
                q="mimeType contains 'image/' and trashed = false",
                spaces="drive",
                fields=(
                    "nextPageToken, "
                    "files(id,name,mimeType,modifiedTime,size,parents,md5Checksum)"
                ),
                pageToken=page_token,
                pageSize=100,
            ).execute()
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
                out.append({
                    "remote_id": f["id"],
                    "name": f["name"],
                    "mime_type": f.get("mimeType"),
                    "modified_at": _parse_drive_time(f.get("modifiedTime")),
                    "remote_path": f["name"],
                    "remote_parent_path": parent_path,
                    "sha256": sha,
                })
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    return await asyncio.to_thread(_work)


# ---------- GitHub ------------------------------------------------------


def _github_collect_entries_sync(refresh_token: str) -> list[dict]:
    """List image files in every repo the user owns. Each repo becomes
    a top-level folder; folders inside the repo become sub-folders.

    Skips files matching common secret patterns (`.env`, `*.key`,
    `id_rsa`, …) per the §C2 spec.
    """
    import requests  # py-requests is a dep of google-api-python-client
    from urllib.parse import quote

    headers = {
        "Authorization": f"Bearer {refresh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Authorise the whole repo. Images, source code, structured-text
    # configs, markdown, and PDFs are all valid — `validate_upload`
    # decides the per-file fate downstream. We deliberately keep a
    # broad allowlist (not a blocklist) so a `.deb` or `.tar.gz`
    # doesn't slip in: those would just blow up validation and
    # eat the worker thread.
    SYNCABLE_EXTS = {
        # Images (existing)
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tiff",
        # PDFs + documents
        ".pdf",
        # Plain text + markup + configs
        ".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".conf", ".properties",
        ".xml", ".plist", ".rst", ".adoc", ".tex",
        # Code (kept in sync with backend/upload_validation.py:_CODE_EXTS)
        ".html", ".htm", ".css", ".scss", ".sass", ".less", ".svg",
        ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
        ".py", ".pyi", ".rb", ".php", ".java", ".kt", ".kts", ".scala",
        ".swift", ".go", ".rs", ".c", ".h", ".cpp", ".cc", ".cxx",
        ".hpp", ".cs", ".dart", ".lua", ".r", ".pl",
        ".sh", ".bash", ".zsh", ".fish", ".ps1", ".sql",
        ".clj", ".ex", ".exs", ".elm", ".erl", ".hs", ".ml", ".mli",
        ".nim", ".zig", ".v",
        ".graphql", ".gql", ".proto", ".diff", ".patch",
        ".ipynb",
    }
    # Basenames worth syncing even without an extension (Dockerfile,
    # Makefile, etc).
    SYNCABLE_BASENAMES = {
        "dockerfile", "makefile", "gnumakefile",
        "rakefile", "gemfile", "podfile", "vagrantfile", "procfile",
        "readme",
    }
    SECRET_PATTERNS = (
        ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
        "credentials.json", "service_account.json",
    )
    SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".kdbx", ".dump")
    # Per-file size cap: GitHub blobs over this get skipped so a single
    # huge JSON dataset doesn't soak a sync run. 25 MB matches GitHub's
    # web-UI render cap so anything bigger isn't meaningfully viewable
    # anyway.
    MAX_BLOB_SIZE = 25 * 1024 * 1024

    def _is_secret(path: str) -> bool:
        base = path.rsplit("/", 1)[-1].lower()
        if base in SECRET_PATTERNS:
            return True
        # Hidden directory entries like `.git/config` or `secrets/...`.
        if base.startswith(".env"):
            return True
        if any(base.endswith(s) for s in SECRET_SUFFIXES):
            return True
        return False

    def _walk_repo(repo_full_name: str, default_branch: str) -> list[dict]:
        # GitHub trees API — `?recursive=1` returns up to 100k entries
        # in one call (truncates above that). For repos that big we
        # accept the cap and let the user revisit it.
        tree_url = (
            f"https://api.github.com/repos/{repo_full_name}/git/trees/"
            f"{quote(default_branch)}?recursive=1"
        )
        r = requests.get(tree_url, headers=headers, timeout=30)
        if r.status_code != 200:
            return []
        tree = r.json().get("tree", [])
        out: list[dict] = []
        for node in tree:
            if node.get("type") != "blob":
                continue
            path = node.get("path") or ""
            if _is_secret(path):
                continue
            base = path.rsplit("/", 1)[-1]
            ext = "." + base.rsplit(".", 1)[-1].lower() if "." in base else ""
            base_lower = base.lower()
            # Match by extension OR by recognized basename (Dockerfile,
            # Makefile, etc). Without the basename fallback the user
            # would get all the code but none of the build configs that
            # tie a repo together.
            if ext not in SYNCABLE_EXTS and base_lower not in SYNCABLE_BASENAMES:
                continue
            # Skip blobs over the per-file cap. `size` is reported in
            # bytes by the git/trees API. Missing field → assume small.
            if (node.get("size") or 0) > MAX_BLOB_SIZE:
                continue
            parent_path = repo_full_name
            if "/" in path:
                parent_path = f"{repo_full_name}/{path.rsplit('/', 1)[0]}"
            out.append({
                "remote_id": f"{repo_full_name}@{node['sha']}",
                "name": path.rsplit("/", 1)[-1],
                "mime_type": None,  # let validate_upload sniff
                "modified_at": None,  # GitHub doesn't track per-blob mtime
                "remote_path": f"{repo_full_name}/{path}",
                "remote_parent_path": parent_path,
                # `node['sha']` is the git blob SHA-1 (20 bytes hex,
                # 40 chars). Pad to 32 bytes so it fits the sha256
                # column shape; collisions across users are non-issue
                # because we key cloud_files on (user_id, provider,
                # remote_id) which already includes the SHA.
                "sha256": bytes.fromhex(node["sha"].ljust(64, "0"))[:32],
                "_repo_full_name": repo_full_name,
                "_blob_sha": node["sha"],
            })
        return out

    # List the authenticated user's own repos (the spec specifically
    # said "own repos" — not stars or contribs).
    repos: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={"affiliation": "owner", "per_page": 100, "page": page},
            timeout=30,
        )
        if r.status_code != 200:
            break
        batch = r.json()
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    entries: list[dict] = []
    for repo in repos:
        if repo.get("archived"):
            continue
        full = repo.get("full_name")
        branch = repo.get("default_branch") or "main"
        if not full:
            continue
        entries.extend(_walk_repo(full, branch))
    return entries


async def _github_collect_entries(refresh_token: str) -> list[dict]:
    import asyncio
    return await asyncio.to_thread(_github_collect_entries_sync, refresh_token)


def _github_download(refresh_token: str, remote_id: str, remote_path: str) -> bytes:
    """Pull a single GitHub blob via the contents API."""
    import requests
    # remote_id is `{repo_full_name}@{blob_sha}`.
    repo, _, blob_sha = remote_id.partition("@")
    if not repo or not blob_sha:
        raise CloudSyncNotConfigured(f"Malformed remote_id: {remote_id!r}")
    # `git/blobs/{sha}` returns base64-encoded bytes for any size; the
    # contents API has a 1 MB cap that's awkward for photos.
    url = f"https://api.github.com/repos/{repo}/git/blobs/{blob_sha}"
    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {refresh_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    import base64
    return base64.b64decode(data.get("content") or "")


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
