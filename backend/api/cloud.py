"""C2 cloud-sync HTTP API.

  GET    /cloud/links                 List the caller's cloud links.
  POST   /cloud/links/{provider}      Initiate OAuth — returns the auth URL.
  GET    /cloud/callback/{provider}   OAuth provider redirects back here.
  DELETE /cloud/links/{id}            Revoke a link (deletes refresh token).
  POST   /cloud/links/{id}/sync       Trigger a manual sync run.

The OAuth callback validates `state` (which carries the user_id we
issued in `connect_provider`), exchanges the code for tokens, encrypts
the refresh token via `secret_box`, and redirects the user back to
the FE with a `?cloud_connected=<provider>` query string so the FE
can toast.
"""
from __future__ import annotations

import json as _json
import logging
import secrets as _ic_secrets
import shutil as _ic_shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path as _ic_Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.cloud_sync import (
    CloudSyncNotConfigured,
    PROVIDER_SCOPES,
    _verify_state,
    complete_oauth,
    connect_provider,
    sync_user_provider,
)
from backend.config import settings
from backend.db import SessionLocal, get_session
from backend.models import CloudLink, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cloud", tags=["cloud"])


# Allow-list of `?error=` values the /cloud/callback handler is
# willing to echo back to the FE via `?cloud_error=…`.
#
# Same shape as `backend/auth/google_sso.py::_ALLOWED_OAUTH_ERRORS`
# (RFC 6749 §4.1.2.1 + OIDC core §3.1.2.6 + the internal codes the
# FE auth/cloud-sync handlers know how to render). Anything outside
# the allow-list collapses to `unknown` and the raw value is logged
# at WARNING for diagnostics.
#
# Keys are lower-cased for case-insensitive matching; values are the
# canonical strings echoed back.
_ALLOWED_CLOUD_OAUTH_ERRORS: dict[str, str] = {
    code: code
    for code in (
        # RFC 6749 §4.1.2.1
        "invalid_request",
        "unauthorized_client",
        "access_denied",
        "unsupported_response_type",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable",
        # OIDC core §3.1.2.6
        "interaction_required",
        "login_required",
        "account_selection_required",
        "consent_required",
        "invalid_request_uri",
        "invalid_request_object",
        "request_not_supported",
        "request_uri_not_supported",
        "registration_not_supported",
        # FE-known internal codes (kept in sync with app.jsx
        # `cloud_error` handler)
        "missing_code_or_state",
        "unsupported_provider",
        "bad_state",
        "not_configured",
        "internal",
    )
}


class CloudLinkRead(BaseModel):
    id: int
    provider: str
    status: str
    scopes: str | None
    last_synced_at: str | None
    created_at: str
    # §C2 — persisted AI opt-in state, surfaced so the FE can show
    # "Enabled ✓" / "Paused ✓" on a fresh page load. Off by default
    # to honor Google Drive Limited Use compliance until the user
    # explicitly opts in.
    ai_opted_in: bool = False


class ProviderFolderStats(BaseModel):
    """Stats on what's IN the linked Drive folder on the provider's
    side. Lets the storage panel say "your Drive holds X GB across N
    files; we've mirrored M of those here" so the user can reconcile
    neuthek's local 1 GB footprint with their 28 GB Drive folder."""

    provider: str
    file_count: int
    total_bytes: int


@router.get("/folder-stats/{provider}", response_model=ProviderFolderStats | None)
async def folder_stats(
    provider: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProviderFolderStats | None:
    """Return the user's total file count + byte size on the given
    cloud provider. Walks the provider's API directly — for Drive
    this is one paginated `files.list` walk (~3s on a 50k-file
    account). Frontend calls this lazily from the storage panel so
    a slow Drive doesn't block the page render.

    Returns None when (a) no link exists for this user/provider,
    (b) the link is revoked, (c) the provider API call failed —
    callers render the section without the "X GB total" line.
    """
    link = (
        await session.execute(
            select(CloudLink)
            .where(
                CloudLink.user_id == user.id,
                CloudLink.provider == provider,
                CloudLink.status == "active",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if not link or not link.encrypted_refresh_token:
        return None
    from backend.cloud_sync import provider_folder_stats
    stats = await provider_folder_stats(
        provider,
        link.encrypted_refresh_token.encode("utf-8") if isinstance(link.encrypted_refresh_token, str) else link.encrypted_refresh_token,
        link=link,
    )
    if stats is None:
        return None
    return ProviderFolderStats(
        provider=provider,
        file_count=stats["file_count"],
        total_bytes=stats["total_bytes"],
    )


# §C4.6 — provider catalog for the FE Cloud sync panel. Public read
# (well, current_active_user-gated like /links). Static computed
# from settings, no DB hit; updates without restart when env vars
# change (since `_provider_status` reads settings live).
class CloudProviderInfo(BaseModel):
    id: str
    name: str
    kind: str
    status: str  # "available" | "needs_setup" | "coming_soon"
    # §C4.6 — drives the FE's connect affordance. "oauth" → browser
    # redirect; "apple_id" → iCloud password+2FA modal; "password" →
    # Proton / MEGA credentials modal.
    auth_shape: str = "oauth"
    blurb: str
    docs: str | None = None


@router.get("/providers", response_model=list[CloudProviderInfo])
async def list_provider_catalog(
    user: Annotated[User, Depends(current_active_user)],
) -> list[CloudProviderInfo]:
    """Static catalog of every cloud provider neuthek knows about,
    with per-provider status derived from settings. The FE Cloud
    sync panel renders this list as cards; status drives whether
    the user sees a "Connect" button (available), a "Needs setup"
    chip with docs link (needs_setup), or a "Coming soon" greyed-
    out card (coming_soon)."""
    from backend.cloud_sync import list_providers
    return [CloudProviderInfo(**p) for p in list_providers()]


@router.get("/links", response_model=list[CloudLinkRead])
async def list_links(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CloudLinkRead]:
    rows = (
        await session.execute(
            select(CloudLink)
            .where(CloudLink.user_id == user.id)
            .order_by(CloudLink.created_at.desc())
        )
    ).scalars().all()
    return [
        CloudLinkRead(
            id=row.id,
            provider=row.provider,
            status=row.status,
            scopes=row.scopes,
            last_synced_at=row.last_synced_at.isoformat() if row.last_synced_at else None,
            created_at=row.created_at.isoformat(),
            ai_opted_in=bool(row.ai_opted_in),
        )
        for row in rows
    ]


class ConnectResponse(BaseModel):
    auth_url: str
    state: str


@router.post(
    "/links/{provider}",
    response_model=ConnectResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def initiate_connect(
    provider: str,
    user: Annotated[User, Depends(current_active_user)],
) -> ConnectResponse:
    if provider not in PROVIDER_SCOPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported provider")
    try:
        handoff = await connect_provider(user.id, provider)  # type: ignore[arg-type]
    except (CloudSyncNotConfigured, NotImplementedError) as exc:
        # Surface a clear 503 to the FE so the connect button can show
        # "configure cloud sync first" without a parallel feature flag.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return ConnectResponse(auth_url=handoff.auth_url, state=handoff.state)


@router.get("/callback/{provider}")
async def oauth_callback(
    provider: str,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
):
    """Provider OAuth redirect target.

    Note this endpoint is **not** behind `current_active_user`: when
    Google redirects the user's browser back, the request carries the
    Google session cookie, not our JWT. Trust comes from the signed
    `state` parameter — we recover the user_id from there.

    Returns a 302 to the FE with a query string the FE can read and
    toast on. Errors are surfaced the same way (`?cloud_error=...`)
    so the user sees something rather than a blank backend page.
    """
    fe_root = settings.frontend_base_url.rstrip("/")

    if error:
        # CodeQL "URL redirection from remote source": the `error`
        # query param is set by the OAuth provider (or whoever else
        # hits the callback URL), so we cannot trust its contents.
        # The query string lives in the URL we send a 302 to, so an
        # attacker could otherwise inject CRLFs / oversize garbage /
        # attacker-chosen strings into the FE's
        # `Could not finish connecting (${failed})` text. Pin `error`
        # to an allow-list of RFC 6749 / OIDC standard codes plus the
        # FE-known internal codes; anything else collapses to
        # `unknown` and the raw value is logged once for diagnostics.
        sanitized_error = _ALLOWED_CLOUD_OAUTH_ERRORS.get(
            error.lower(), "unknown",
        )
        if sanitized_error == "unknown":
            logger.warning(
                "cloud_callback: received non-allowlisted error code "
                "(logged but not echoed): %r",
                error[:64],
            )
        return RedirectResponse(
            url=f"{fe_root}/?cloud_error={sanitized_error}", status_code=302
        )
    if not code or not state:
        return RedirectResponse(
            url=f"{fe_root}/?cloud_error=missing_code_or_state", status_code=302
        )
    if provider not in PROVIDER_SCOPES:
        return RedirectResponse(
            url=f"{fe_root}/?cloud_error=unsupported_provider", status_code=302
        )
    # Re-bind `provider` to the canonical allow-listed key from
    # PROVIDER_SCOPES rather than the raw path param. The membership
    # check above already guarantees the two strings are equal, but
    # rebinding makes the data-flow explicit to CodeQL's
    # `py/url-redirection` query (which couldn't otherwise prove that
    # the value flowing into the final `?cloud_connected={provider}`
    # redirect on line ~250 is from a closed set) AND prevents future
    # refactors from accidentally widening the input.
    provider = next(p for p in PROVIDER_SCOPES if p == provider)

    # Verify the HMAC-signed state we issued in connect_provider. This
    # is the OAuth CSRF defense: without it, an attacker could craft a
    # callback URL with `state=<victim_uuid>&code=<attacker_code>` and
    # bind their own Google Drive to the victim's neuthek account.
    try:
        user_id = _verify_state(state)
    except ValueError:
        return RedirectResponse(
            url=f"{fe_root}/?cloud_error=bad_state", status_code=302
        )

    # Use a fresh session (this request isn't auth-gated, so no
    # `Depends(get_session)`).
    async with SessionLocal() as session:
        try:
            await complete_oauth(session, user_id, provider, code, state)  # type: ignore[arg-type]
        except CloudSyncNotConfigured as exc:
            logger.warning("oauth_callback rejected: %s", exc)
            return RedirectResponse(
                url=f"{fe_root}/?cloud_error=not_configured", status_code=302
            )
        except Exception:
            logger.exception("oauth_callback crashed for provider=%s", provider)
            return RedirectResponse(
                url=f"{fe_root}/?cloud_error=internal", status_code=302
            )

    return RedirectResponse(
        url=f"{fe_root}/?cloud_connected={provider}", status_code=302
    )


class SyncResponse(BaseModel):
    seen: int
    pulled: int
    # §C2 — augmented with the diff counters added by the new sync
    # worker. `skipped_unchanged` counts files whose sha256 +
    # modifiedTime matched the last sync (no re-download). `conflicts`
    # is the count of files where the local copy was edited after the
    # last sync — we refuse to overwrite and the FE shows a banner.
    skipped_unchanged: int = 0
    conflicts: int = 0
    conflict_remote_ids: list[str] = []
    provider: str


# In-process progress board for cloud syncs. The browser used to hold
# the HTTP request open for the entire sync — which, after we dropped
# the image-only filter and started pulling EVERY file, can take
# minutes for accounts with thousands of items. Uvicorn / the proxy
# closes the connection mid-walk and the browser surfaces "Failed to
# fetch." We now kick off the sync as a background task and let the
# FE poll a status endpoint until done.
#
# Keys are (user_id, provider). Values: {state, started_at, counts,
# error}. In-memory because a single backend process drives all
# syncs today; scale-out would move this to Redis.
import asyncio

_SYNC_PROGRESS: dict[tuple, dict] = {}


def _sync_key(user_id, provider: str) -> tuple:
    return (str(user_id), provider)


async def _run_sync_background(
    session_factory, user_id, provider: str, link_id: int,
) -> None:
    """Background worker. Opens its own session because the request-scoped
    session has long since closed by the time this runs.

    Holds the CS9 per-link Redis mutex for the duration of the sync so
    a parallel manual click on another uvicorn worker OR the hourly
    cron tick can't double-pull. The lock is link-scoped (not (user,
    provider)) so two different cloud accounts the user owns can sync
    in parallel."""
    from backend.cloud_sync_lock import sync_lock

    key = _sync_key(user_id, provider)
    _SYNC_PROGRESS[key] = {
        "state": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "counts": None,
        "error": None,
    }
    async with sync_lock(link_id) as acquired:
        if not acquired:
            # Another worker (other uvicorn process / cron) is mid-
            # sync for this link. The trigger_sync handler already
            # checked the in-process progress dict and the lock state
            # before getting here, but a parallel acquire can still
            # lose the race. Treat the second arrival as a no-op so
            # the caller's poll sees the existing run finish out.
            _SYNC_PROGRESS[key] = {
                "state": "running",
                "started_at": _SYNC_PROGRESS[key]["started_at"],
                "counts": None,
                "error": None,
            }
            logger.info(
                "background_sync: lock contention user=%s provider=%s "
                "link=%s — bowing to in-flight sibling",
                user_id, provider, link_id,
            )
            return
        try:
            async with session_factory() as s:
                from sqlalchemy import text as sql_text
                # Workers don't go through the per-request middleware that
                # stamps `app.current_user_id`, so the RLS policies on
                # `images` / `image_geo` etc. would block writes. Bypass.
                await s.execute(sql_text("SET LOCAL app.rls_bypass='on'"))
                result = await sync_user_provider(s, user_id, provider)
            _SYNC_PROGRESS[key] = {
                "state": "done",
                "started_at": _SYNC_PROGRESS[key]["started_at"],
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "counts": result,
                "error": None,
            }
        except CloudSyncNotConfigured as exc:
            _SYNC_PROGRESS[key] = {
                "state": "error",
                "started_at": _SYNC_PROGRESS[key]["started_at"],
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "counts": None,
                "error": str(exc),
            }
        except Exception as exc:
            logger.exception(
                "background_sync: provider call failed user=%s provider=%s",
                user_id, provider,
            )
            msg = _extract_provider_error_message(exc) or "Sync failed. Try again in a moment."
            _SYNC_PROGRESS[key] = {
                "state": "error",
                "started_at": _SYNC_PROGRESS[key]["started_at"],
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "counts": None,
                "error": msg,
            }


@router.post("/links/{link_id}/sync", response_model=SyncResponse)
async def trigger_sync(
    link_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SyncResponse:
    """Start a sync in the background and return immediately.

    Previously synchronous — the API call held the HTTP request open
    for the entire Drive walk + per-file download. For an account
    with thousands of items that exceeds the browser's fetch timeout
    AND any reverse-proxy idle limit, surfacing as "Failed to fetch."
    Now we fire-and-forget and the FE polls
    `GET /cloud/links/{id}/sync-status` for progress + final counts.

    The response is the SAME shape as the legacy synchronous one but
    populated with the zero counts; the FE treats `state=running`
    as "sync just started, poll for updates." Idempotent — clicking
    Sync again while one is already running returns the existing
    progress instead of starting a second concurrent walk.
    """
    link = await session.get(CloudLink, link_id)
    if link is None or link.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    key = _sync_key(user.id, link.provider)
    existing = _SYNC_PROGRESS.get(key)
    if existing and existing.get("state") == "running":
        # Already running — return zero counts; FE polls.
        return SyncResponse(
            provider=link.provider,
            seen=0, pulled=0, skipped_unchanged=0, conflicts=0,
        )
    from backend.db import SessionLocal
    asyncio.create_task(
        _run_sync_background(
            SessionLocal, user.id, link.provider, link.id,
        )
    )
    # SyncResponse requires `seen` + `pulled` (the diff-counter
    # shape from sync_user_provider). The previous arg names
    # (`scanned/created/updated/deleted/skipped/errors`) were
    # leftover from an earlier draft of the model and Pydantic
    # rejected the response — surfacing in the FE as a generic
    # "Failed to fetch" since the 500 body had no useful detail.
    # The bug was masked while only Google Drive was wired (that
    # path didn't reach this return because the existing-link
    # short-circuit above fires); OneDrive + Dropbox both hit
    # it on first sync. Same zero-counts intent ("sync just
    # started, poll for updates") with the correct field names.
    return SyncResponse(
        provider=link.provider,
        seen=0, pulled=0, skipped_unchanged=0, conflicts=0,
    )


@router.get("/links/{link_id}/sync-status")
async def sync_status(
    link_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Poll endpoint for an in-flight sync. Returns the latest
    progress dict the background task wrote to `_SYNC_PROGRESS`.

    Response shape:
        {state: "idle" | "running" | "done" | "error",
         started_at: iso8601 | null,
         finished_at: iso8601 | null,
         counts: SyncResponse | null,
         error: str | null}
    """
    link = await session.get(CloudLink, link_id)
    if link is None or link.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    key = _sync_key(user.id, link.provider)
    progress = _SYNC_PROGRESS.get(key)
    if progress is None:
        return {
            "state": "idle",
            "started_at": None,
            "finished_at": None,
            "counts": None,
            "error": None,
        }
    return progress


def _extract_provider_error_message(exc: Exception) -> str | None:
    """Pull the human-readable error text out of a Google API client
    exception so the FE can show "enable the Drive API" instead of a
    blank "internal error" page. Returns None when the message isn't
    a known provider-error shape — callers fall back to a generic
    message.
    """
    # googleapiclient.errors.HttpError carries the JSON error body on
    # `.error_details` (list[dict]) and the HTTP status on `.resp.status`.
    error_details = getattr(exc, "error_details", None)
    if error_details:
        try:
            first = error_details[0]
            if isinstance(first, dict) and first.get("message"):
                return str(first["message"])
        except Exception:
            pass
    # Fall back to str() which on HttpError includes the URL + JSON
    # body — useful but verbose. Trim it.
    text = str(exc)
    if not text:
        return None
    return text[:500]


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_link(
    link_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    # NB: don't annotate this with `-> None`. With `from __future__ import
    # annotations`, FastAPI sees "None" as a string return type and treats
    # it as a serialized response model, which trips the
    # `is_body_allowed_for_status_code(204)` assert. Either drop the
    # annotation or drop the future import; we drop the annotation here.
    """Drop a cloud link. The accompanying cloud_files rows aren't
    auto-deleted — local images stay (the user can manage them), the
    diff index just stops being a useful reference.

    CS3 — before deleting the row, ask Google to revoke the refresh
    token so the user's Google account stops listing this app as a
    connected service. Best-effort: revoke failures get audited but
    do NOT block the local delete (the user clicked "disconnect"
    and the row going away is the bigger correctness guarantee than
    Google's account-listing hygiene).
    """
    row = await session.get(CloudLink, link_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")

    from backend.audit import add_audit

    revoke_outcome: str = "skipped"
    if row.provider == "google_drive" and row.encrypted_refresh_token:
        from backend.cloud_sync import revoke_google_refresh_token
        from backend.secret_box import decrypt as decrypt_token
        try:
            refresh_token = decrypt_token(
                row.encrypted_refresh_token.encode("ascii")
            )
        except Exception:
            # Decryption failed (e.g. CLOUD_ENCRYPTION_KEY rotated
            # since the row was written). Nothing we can revoke;
            # local delete still goes through.
            logger.exception(
                "revoke_link: refresh-token decrypt failed link=%s — "
                "proceeding with local delete only",
                link_id,
            )
            revoke_outcome = "decrypt_failed"
        else:
            ok = await revoke_google_refresh_token(refresh_token)
            revoke_outcome = "revoked" if ok else "revoke_failed"

    await add_audit(
        session,
        user_id=user.id,
        action="cloud.link.revoked",
        details={
            "link_id": link_id,
            "provider": row.provider,
            "revoke_outcome": revoke_outcome,
        },
    )
    await session.execute(
        sa_delete(CloudLink).where(CloudLink.id == link_id)
    )
    await session.commit()


# §C2 — per-source AI opt-in / opt-out. When the user explicitly
# opts in, we flip `skip_ai_training=False` on every image from this
# provider AND set `pending_summary=True` + `pending_face_scan=True`
# so the existing background workers pick the rows up on the next
# pass. Opt-out goes the other way — sets `skip_ai_training=True`
# and zeroes the pending flags so the workers stop processing.


class AiOptInBody(BaseModel):
    opted_in: bool


@router.post("/links/{link_id}/ai-opt-in")
async def set_ai_opt_in(
    link_id: int,
    body: AiOptInBody,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Flip the Limited-Use AI flag for every image from this source."""
    from sqlalchemy import update as sa_update
    from backend.models import Image as ImageModel

    link = await session.get(CloudLink, link_id)
    if link is None or link.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")

    # Persist the toggle on the link itself so a page reload reflects
    # the right state — local FE state used to evaporate on refresh,
    # leaving the user thinking the click didn't take.
    link.ai_opted_in = bool(body.opted_in)

    res = await session.execute(
        sa_update(ImageModel)
        .where(
            ImageModel.user_id == user.id,
            ImageModel.source_provider == link.provider,
            ImageModel.deleted_at.is_(None),
        )
        .values(
            skip_ai_training=not body.opted_in,
            # When opting in, mark pending so the workers re-queue.
            pending_summary=body.opted_in,
            pending_face_scan=body.opted_in,
        )
    )
    from backend.audit import add_audit
    await add_audit(
        session,
        user_id=user.id,
        action="cloud.ai_opt_in" if body.opted_in else "cloud.ai_opt_out",
        details={"provider": link.provider, "image_count": int(res.rowcount or 0)},
    )

    # When opting IN, enqueue summarize jobs for every newly-eligible
    # image so the ml-worker actually starts processing them. Without
    # this nudge, flipping the toggle only marks rows pending —
    # nothing pushes them into the Redis queue, and the
    # summarize-progress counter sits at "0 of N" forever. (The
    # `/images/summarize-progress` poll also drains a few rows per
    # tick as a safety net, but enqueuing eagerly here makes the FE
    # ticking start the moment the user clicks "Enable".)
    enqueued = 0
    if body.opted_in:
        from sqlalchemy import select
        from backend import jobs as job_q

        # Fetch rows with their category + pending_face_scan so we can
        # pick the RIGHT job type per row. Before this, the enqueue
        # always used `enqueue_summarize` — videos never got
        # face_scan jobs even though `pending_face_scan=True` had
        # been set on them. That's why every video in the user's
        # library showed `pending_face_scan=t` AND zero detected
        # faces: the right job was never enqueued.
        rows = (
            await session.execute(
                select(
                    ImageModel.id,
                    ImageModel.category,
                    ImageModel.pending_face_scan,
                ).where(
                    ImageModel.user_id == user.id,
                    ImageModel.source_provider == link.provider,
                    ImageModel.deleted_at.is_(None),
                    ImageModel.pending_summary.is_(True),
                )
            )
        ).all()
        for img_id, category, needs_faces in rows:
            try:
                # Mirror the dispatch from api/images.py + cloud_sync.py:
                # face_scan_then_summarize when BOTH faces + summary
                # are needed (image / video rows); face_scan alone for
                # rows where summary is already done; summarize alone
                # for the rest (audio, docs, images with face data).
                if needs_faces and category in ("image", "video"):
                    ok = await job_q.enqueue_face_scan_then_summarize(
                        user.id, img_id,
                    )
                else:
                    ok = await job_q.enqueue_summarize(img_id)
                if ok:
                    enqueued += 1
            except Exception:
                logger.exception("ai-opt-in: enqueue failed for %s", img_id)

    await session.commit()
    return {
        "affected": int(res.rowcount or 0),
        "enqueued": enqueued,
        "provider": link.provider,
        "opted_in": body.opted_in,
    }


@router.get("/links/{link_id}/conflicts")
async def list_conflicts(
    link_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Return the latest conflict events for a cloud link so the FE can
    surface "we couldn't sync N files — review them" banner. Reads the
    audit log; conflicts are an `action='cloud.sync.conflict'` row."""
    from backend.models import AuditLog

    link = await session.get(CloudLink, link_id)
    if link is None or link.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    # Pull conflicts since the last successful sync. `last_synced_at`
    # is updated on every run including conflict-only ones, so we
    # really want "since the last conflict-free run" — but the link
    # row carries `status="active"|"conflicts"`. For v1 we just
    # return the last 50.
    rows = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.user_id == user.id,
                AuditLog.action == "cloud.sync.conflict",
            )
            .order_by(AuditLog.id.desc())
            .limit(50)
        )
    ).scalars().all()
    items = []
    for r in rows:
        details = r.details or {}
        if details.get("provider") == link.provider:
            items.append({
                "remote_id": details.get("remote_id"),
                "remote_path": details.get("remote_path"),
                "reason": details.get("reason"),
                "at": r.created_at.isoformat() if r.created_at else None,
            })
    return {"provider": link.provider, "conflicts": items}


# ---------- §C4.6 — iCloud Drive sign-in dance -----------------------------
#
# iCloud is the only provider in the catalog that doesn't use OAuth —
# Apple doesn't expose a third-party OAuth flow for iCloud Drive at
# all. The user enters their Apple ID + password directly into a
# neuthek form; we construct a pyicloud session; if 2FA is required
# (it almost always is in 2024+) we stash the in-progress session in
# memory, return a session_id, prompt the user for the 6-digit code
# from their iDevice, then validate the code and persist the link.
#
# In-memory stash is fine: the only state we need to remember between
# the two requests is a live PyiCloudService object (not serializable
# to Redis cleanly) and it only needs to live ~minutes (the typical
# time between "show me a code" and "here's the code"). Scale-out
# would either pin the connect flow to a single backend instance via
# a sticky session OR rebuild the session from scratch on /verify
# (which forces a second password challenge and is ugly UX).
#
# Entry shape:
#   {service, user_id, apple_id, password, tmp_dir, created_at}

_ICLOUD_PENDING: dict[str, dict] = {}
_ICLOUD_PENDING_TTL = timedelta(minutes=5)


def _icloud_sweep_pending() -> None:
    """Best-effort GC for abandoned sign-in attempts. Runs at the top
    of every /icloud/start request so stale entries don't accumulate
    in long-running processes. 5 min TTL is generous: the typical
    2FA prompt → code path is well under 30 seconds, so anything
    older than 5 min is either a user who closed the tab or a
    network-partition victim."""
    now = datetime.now(timezone.utc)
    stale = [
        k for k, v in _ICLOUD_PENDING.items()
        if now - v["created_at"] > _ICLOUD_PENDING_TTL
    ]
    for k in stale:
        entry = _ICLOUD_PENDING.pop(k, None)
        if entry:
            tmp_dir = entry.get("tmp_dir")
            if tmp_dir:
                _ic_shutil.rmtree(tmp_dir, ignore_errors=True)


class ICloudStartRequest(BaseModel):
    apple_id: EmailStr
    password: str


class ICloudTrustedDevice(BaseModel):
    """Trusted-device entry surfaced by pyicloud's
    `service.trusted_devices`. We pass the redacted phone / device
    name through to the FE so the user can see WHERE Apple sent
    their code — and pick a different device if the first one
    didn't deliver. The `id` is the JSON-stringified original dict
    that `send_verification_code` consumes; opaque to the FE.
    """
    id: str
    label: str


class ICloudStartResponse(BaseModel):
    requires_2fa: bool = False
    session_id: str | None = None
    link_id: int | None = None
    message: str | None = None
    # §C4.6 — populated when requires_2fa is True. Helps the user
    # confirm Apple actually sent a code (and to which device).
    # Empty when Apple's auto-push fires correctly and the user
    # doesn't need a fallback.
    trusted_devices: list[ICloudTrustedDevice] = []
    code_sent_to: str | None = None


@router.post("/icloud/start", response_model=ICloudStartResponse)
async def icloud_start(
    payload: ICloudStartRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ICloudStartResponse:
    """Step 1 of the iCloud connect handshake. Constructs a pyicloud
    session against Apple's auth endpoints. If the response says
    2FA-required (almost always — Apple enforces it on most accounts),
    we stash the live session object in `_ICLOUD_PENDING` keyed by a
    server-generated session_id and return it to the FE so the user
    can submit the 2FA code on a second request. If 2FA is NOT
    required (already-trusted device, or rare accounts without 2FA),
    we promote the temp cookie directory immediately + persist the
    link in one shot."""
    _icloud_sweep_pending()

    session_id = _ic_secrets.token_urlsafe(24)
    # Temp cookie dir for the 2FA dance. We use the session_id as the
    # directory name (NOT the user's id) so two parallel sign-in
    # attempts by the same user — e.g. a user who opened a second
    # tab — don't trample each other. On verify success we move this
    # dir to the permanent /<link_id>/ location; on failure / TTL
    # expiry we delete it.
    tmp_dir = _ic_Path(settings.pyicloud_cookie_root) / "_tmp" / session_id
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        from pyicloud import PyiCloudService  # type: ignore
        from pyicloud.exceptions import PyiCloudFailedLoginException  # type: ignore
    except ImportError:
        _ic_shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "pyicloud is not installed on this deployment.",
        )

    try:
        svc = await asyncio.to_thread(
            PyiCloudService,
            payload.apple_id,
            payload.password,
            str(tmp_dir),  # cookie_directory positional
        )
    except PyiCloudFailedLoginException as exc:
        _ic_shutil.rmtree(tmp_dir, ignore_errors=True)
        # Apple's error messages are user-readable ("Incorrect Apple
        # ID or password"); pass them through verbatim so the user
        # gets actionable feedback. Keep the prefix so the FE can
        # distinguish "Apple said no" from "our backend exploded".
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Apple sign-in failed: {exc}",
        )
    except Exception:
        _ic_shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception("icloud_start: unexpected failure")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Could not reach iCloud. Try again in a moment.",
        )

    # User-reported regression: pyicloud's `requires_2fa` was True
    # even when Apple's auth server had already accepted the session
    # (the user got the "new sign-in" email but no 2FA challenge ever
    # arrived on their iDevices, and the "Get Verification Code" menu
    # didn't appear in Settings → Apple ID). The cause is pyicloud's
    # internal state machine: after a fresh PyiCloudService(...) call
    # the library sets requires_2fa=True PREEMPTIVELY, then only
    # clears it after probing the session. We bypassed that probe
    # and went straight into the 2FA UI, leaving the user without a
    # code to enter.
    #
    # Fix: check `is_trusted_session` first. If Apple already trusts
    # us (cookies from a previous attempt, or the account doesn't
    # actually have 2FA enforced on Drive), skip the 2FA UI entirely
    # and persist the link.
    is_trusted = bool(getattr(svc, "is_trusted_session", False))
    requires_2fa = bool(getattr(svc, "requires_2fa", False))
    requires_2sa = bool(getattr(svc, "requires_2sa", False))

    if is_trusted:
        # Apple already considers us authenticated. Persist + return.
        link_id = await _icloud_persist_link(
            session, user.id, payload.apple_id, payload.password, tmp_dir,
        )
        return ICloudStartResponse(
            requires_2fa=False,
            link_id=link_id,
            message="Session already trusted — no 2FA needed.",
        )

    if requires_2sa or requires_2fa:
        # Both branches now go through the SAME path: explicitly ask
        # Apple to send a code via `send_verification_code`. We used
        # to only do this for `requires_2sa` and rely on Apple's
        # auto-push for `requires_2fa` — but the auto-push fails
        # silently for some accounts (Apple's anti-abuse heuristics,
        # offline trusted devices, or accounts that mix legacy + new
        # 2FA flags). Users would see "enter the code" but no code
        # would ever arrive. Calling send_verification_code() in
        # both branches makes the delivery explicit; we also return
        # the trusted-device list so the FE can show WHERE the code
        # was sent and let the user pick a different device on
        # resend.
        device_list: list[ICloudTrustedDevice] = []
        code_sent_to: str | None = None
        raw_devices: list[dict] = []
        try:
            raw_devices = list(svc.trusted_devices or [])
            for dev in raw_devices:
                # pyicloud's device dicts use varied key names per
                # account flavor. Build a user-readable label out
                # of whatever we get; fall back to a generic string
                # if none of the expected fields exist.
                label = (
                    dev.get("deviceName")
                    or dev.get("phoneNumber")
                    or dev.get("name")
                    or "Trusted device"
                )
                device_list.append(ICloudTrustedDevice(
                    id=_json.dumps(dev, sort_keys=True),
                    label=str(label),
                ))
            if raw_devices:
                try:
                    await asyncio.to_thread(
                        svc.send_verification_code, raw_devices[0]
                    )
                    code_sent_to = device_list[0].label
                except Exception:
                    # If Apple rejects the send (rate-limited,
                    # device offline, ...) we still want the modal
                    # to render with the device list so the user
                    # can retry on a different one.
                    logger.exception(
                        "icloud_start: send_verification_code "
                        "failed on device %r",
                        device_list[0].label,
                    )
        except Exception:
            logger.exception(
                "icloud_start: trusted_devices probe failed; "
                "user will need to fall back to Settings → Apple ID"
            )
        _ICLOUD_PENDING[session_id] = {
            "service": svc,
            "user_id": user.id,
            "apple_id": payload.apple_id,
            "password": payload.password,
            "tmp_dir": tmp_dir,
            "is_2sa": requires_2sa,
            # §C4.6 — the device dict we sent the code to. /verify
            # needs this to call validate_verification_code(device,
            # code) — pyicloud's method for codes that were
            # explicitly requested via send_verification_code (as
            # opposed to validate_2fa_code, which only accepts the
            # auto-pushed flavor). User's code was rejected with
            # "didn't match" because we always called the 2fa
            # variant; /verify now tries both. The list is stored
            # in send-order so resend can update it.
            "code_sent_device": raw_devices[0] if raw_devices else None,
            "all_devices": raw_devices,
            "created_at": datetime.now(timezone.utc),
        }
        return ICloudStartResponse(
            requires_2fa=True,
            session_id=session_id,
            trusted_devices=device_list,
            code_sent_to=code_sent_to,
        )

    # Neither 2FA nor 2SA, AND not flagged as trusted — fall back to
    # persisting the link. The account presumably has no 2FA on
    # iCloud Drive at all. Surfaces in the toast so the user knows
    # nothing more is needed.
    link_id = await _icloud_persist_link(
        session, user.id, payload.apple_id, payload.password, tmp_dir,
    )
    return ICloudStartResponse(
        requires_2fa=False,
        link_id=link_id,
        message="iCloud connected (no 2FA challenge from Apple).",
    )


class ICloudResendRequest(BaseModel):
    session_id: str
    # `device_id` is the opaque JSON-stringified trusted_devices
    # entry from /start's response. Omit to resend to the first
    # device (same as /start's default).
    device_id: str | None = None


class ICloudResendResponse(BaseModel):
    code_sent_to: str | None = None


@router.post("/icloud/resend-code", response_model=ICloudResendResponse)
async def icloud_resend_code(
    payload: ICloudResendRequest,
    user: Annotated[User, Depends(current_active_user)],
) -> ICloudResendResponse:
    """Re-request a 2FA code, optionally to a different trusted
    device. Used by the FE's modal when Apple's first delivery
    doesn't arrive (push silent-failed, SMS lost, etc).

    No DB writes; just talks to Apple via the stashed pyicloud
    session. The session must still be pending (5-min TTL).
    """
    _icloud_sweep_pending()
    pending = _ICLOUD_PENDING.get(payload.session_id)
    if pending is None or pending["user_id"] != user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in session not found or expired. Start over.",
        )
    svc = pending["service"]
    try:
        raw_devices = list(svc.trusted_devices or [])
    except Exception:
        logger.exception("icloud_resend: trusted_devices raised")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Couldn't reach iCloud's device list. Try again.",
        )
    if not raw_devices:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No trusted devices on this Apple ID. Use Settings → "
            "Apple ID → Sign-In & Security → Get Verification Code "
            "on a device already signed in to this account.",
        )
    # Pick which device to send to. Match against the opaque
    # device_id (JSON-stringified) from /start's response.
    pick = raw_devices[0]
    label = (
        pick.get("deviceName") or pick.get("phoneNumber")
        or pick.get("name") or "Trusted device"
    )
    if payload.device_id:
        try:
            wanted = _json.loads(payload.device_id)
            for dev in raw_devices:
                if dev == wanted:
                    pick = dev
                    label = (
                        dev.get("deviceName") or dev.get("phoneNumber")
                        or dev.get("name") or "Trusted device"
                    )
                    break
        except Exception:
            pass  # fall through to first device
    try:
        await asyncio.to_thread(svc.send_verification_code, pick)
    except Exception:
        logger.exception(
            "icloud_resend: send_verification_code failed on %r", label
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Apple refused to send a code to {label}. Try another "
            f"device or use Settings → Apple ID → Get Verification Code.",
        )
    # Update the stashed device so /verify uses
    # validate_verification_code(picked_device, code) — the code
    # that just got sent. Without this, the user resends to phone B
    # but /verify still validates against phone A's session state
    # and Apple rejects the code as "wrong".
    pending["code_sent_device"] = pick
    return ICloudResendResponse(code_sent_to=label)


class ICloudVerifyRequest(BaseModel):
    session_id: str
    code: str


class ICloudVerifyResponse(BaseModel):
    link_id: int


@router.post("/icloud/verify", response_model=ICloudVerifyResponse)
async def icloud_verify(
    payload: ICloudVerifyRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ICloudVerifyResponse:
    """Step 2 of the iCloud connect handshake. Validates the user's
    6-digit code against the stashed pyicloud session, trusts the
    session so future syncs don't re-prompt, then persists the link.
    """
    _icloud_sweep_pending()
    pending = _ICLOUD_PENDING.get(payload.session_id)
    if pending is None or pending["user_id"] != user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in session not found or expired. Start over.",
        )
    svc = pending["service"]
    # Apple's codes are 6 digits; the user may paste with spaces /
    # dashes (some authenticator UIs render "123-456"). Strip both
    # before sending.
    code = payload.code.strip().replace(" ", "").replace("-", "")
    # §C4.6 — pyicloud has TWO validation methods:
    #   validate_2fa_code(code)            — for codes from
    #     Apple's auto-push (modern 2FA flow).
    #   validate_verification_code(dev,c)  — for codes we
    #     explicitly requested via send_verification_code (older
    #     2SA flow + any account where Apple's auto-push doesn't
    #     fire and we had to explicitly trigger).
    # User got the code (we explicitly triggered) but typing it
    # into our form was rejected because we always called
    # validate_2fa_code. Try BOTH — first the modern path, fall
    # back to the device-bound path with the stashed device dict.
    ok = False
    last_err: Exception | None = None
    try:
        ok = await asyncio.to_thread(svc.validate_2fa_code, code)
    except Exception as e:
        last_err = e
        logger.warning(
            "icloud_verify: validate_2fa_code raised — falling "
            "back to validate_verification_code: %s", e,
        )
    if not ok:
        dev = pending.get("code_sent_device")
        if dev is not None:
            try:
                ok = await asyncio.to_thread(
                    svc.validate_verification_code, dev, code,
                )
            except Exception as e:
                last_err = e
                logger.exception(
                    "icloud_verify: validate_verification_code raised"
                )
    if not ok:
        # Both methods failed. Don't drop the pending entry — the
        # user might have typoed and want to retry. The 5-min TTL
        # still applies.
        if last_err is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That code didn't match. Apple may have rejected it "
                "(expired or already used) — try requesting a fresh "
                "one with 'Try a different device' above.",
            )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That code didn't match. Check your iDevice and try again.",
        )
    # Trust this session so the cookie_directory carries the long-
    # lived trust token. Without this, every sync would re-prompt.
    try:
        await asyncio.to_thread(svc.trust_session)
    except Exception:
        # Trust failure isn't fatal — the user is already authed; the
        # next sync will just have to re-prompt for 2FA. Log so an
        # operator can investigate.
        logger.exception(
            "icloud_verify: trust_session failed; sync will re-prompt 2FA",
        )
    link_id = await _icloud_persist_link(
        session, user.id,
        pending["apple_id"], pending["password"], pending["tmp_dir"],
    )
    _ICLOUD_PENDING.pop(payload.session_id, None)
    return ICloudVerifyResponse(link_id=link_id)


async def _icloud_persist_link(
    session: AsyncSession,
    user_id: UUID,
    apple_id: str,
    password: str,
    tmp_dir: _ic_Path,
) -> int:
    """Encrypt the credentials, write the CloudLink row, and move the
    pyicloud cookie directory from the temp location to its permanent
    `<pyicloud_cookie_root>/<link_id>/` home so subsequent syncs
    resume the trust state."""
    from backend.cloud_sync import _icloud_pack_credentials

    encrypted_blob = _icloud_pack_credentials(apple_id, password)

    # Upsert: a user re-connecting iCloud after disconnecting (or
    # after a trust-token expiry that forced a re-auth) reuses their
    # existing link row.
    existing = (
        await session.execute(
            select(CloudLink).where(
                CloudLink.user_id == user_id,
                CloudLink.provider == "icloud",
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        link = CloudLink(
            user_id=user_id,
            provider="icloud",
            encrypted_refresh_token=encrypted_blob,
            scopes="",
            status="active",
        )
        session.add(link)
    else:
        existing.encrypted_refresh_token = encrypted_blob
        existing.scopes = ""
        existing.status = "active"
        link = existing
    await session.commit()
    await session.refresh(link)

    perm = _ic_Path(settings.pyicloud_cookie_root) / str(link.id)
    perm.parent.mkdir(parents=True, exist_ok=True)
    if perm.exists():
        # If a previous link with the same id ever existed (only
        # possible on a re-connect that hit the same row), the old
        # cookies are no longer valid for the freshly-authenticated
        # session — drop them so move() succeeds.
        _ic_shutil.rmtree(perm, ignore_errors=True)
    try:
        _ic_shutil.move(str(tmp_dir), str(perm))
    except Exception:
        # On Windows, shutil.move across drives can fail under
        # specific permissions; copytree + rmtree is the fallback.
        logger.exception(
            "icloud: shutil.move failed for link=%s; using copy fallback",
            link.id,
        )
        _ic_shutil.copytree(str(tmp_dir), str(perm), dirs_exist_ok=True)
        _ic_shutil.rmtree(tmp_dir, ignore_errors=True)
    return link.id


# ---------- §C4.6 — Proton Drive sign-in (via rclone) ----------------------
#
# Proton Drive is end-to-end encrypted; we drive the sync with rclone
# which decrypts files locally so the rest of the pipeline can re-
# encrypt them with the user's neuthek key. Two-step flow (mirrors
# iCloud's):
#
#   /cloud/proton-drive/start  →  email + password
#       If Proton replies "2FA required" (rclone's stderr carries
#       a recognizable phrase), stash a pending session + return
#       requires_2fa=True with a session_id. Otherwise persist the
#       link immediately.
#
#   /cloud/proton-drive/verify →  session_id + 6-digit code
#       Rewrite the rclone config with the 2fa field set, probe
#       again, persist on success.
#
# Pending session storage shape mirrors _ICLOUD_PENDING — same TTL
# (5 min), same sweep on every /start, same in-process dict.
#
# Entry shape: {email, password, user_id, tmp_link_id, created_at}.

_PROTON_PENDING: dict[str, dict] = {}
_PROTON_PENDING_TTL = timedelta(minutes=5)


def _proton_sweep_pending() -> None:
    """GC stale pending Proton sign-in sessions. Called at the top of
    every /start so abandoned entries don't accumulate. The cleanup
    also removes the tmp rclone config file (the real config will be
    rewritten under the final link_id on success)."""
    from backend import rclone_wrapper

    now = datetime.now(timezone.utc)
    stale = [
        k for k, v in _PROTON_PENDING.items()
        if now - v["created_at"] > _PROTON_PENDING_TTL
    ]
    for k in stale:
        entry = _PROTON_PENDING.pop(k, None)
        if entry and entry.get("tmp_link_id"):
            try:
                tmp_path = rclone_wrapper.config_path_for_link(
                    entry["tmp_link_id"],
                )
                tmp_path.unlink(missing_ok=True)
            except Exception:
                logger.exception(
                    "proton_sweep_pending: tmp config cleanup failed",
                )


class ProtonStartRequest(BaseModel):
    email: EmailStr
    password: str


class ProtonStartResponse(BaseModel):
    requires_2fa: bool = False
    session_id: str | None = None
    link_id: int | None = None
    message: str | None = None


def _proton_tmp_link_id() -> str:
    """Per-pending-session id for the rclone config file. Prefixed
    with `tmp-` so it never collides with real CloudLink integer ids.
    The probe writes a tmp config keyed on this; if the credentials
    are accepted, we copy them under the real link id (or simply
    re-materialize from the persisted blob on first sync)."""
    return f"tmp-{_ic_secrets.token_urlsafe(12)}"


@router.post("/proton-drive/start", response_model=ProtonStartResponse)
async def proton_drive_start(
    payload: ProtonStartRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProtonStartResponse:
    """Step 1 of the Proton Drive connect handshake. Writes a temp
    rclone config with the email + password and probes
    `rclone about proton-drive:`. If Proton says "2FA required"
    (rclone's stderr carries a recognizable phrase), stash the
    pending entry and return requires_2fa=True. Otherwise persist
    the link in one shot.
    """
    from backend import rclone_wrapper
    from backend.cloud_sync import _proton_drive_pack_credentials

    _proton_sweep_pending()

    # Probe with a temp config; we won't know the real link_id until
    # the link row is persisted (or never — if the credentials don't
    # work we never write a row).
    tmp_link_id = _proton_tmp_link_id()
    try:
        config_path = rclone_wrapper.write_proton_config(
            tmp_link_id, payload.email, payload.password, totp="",
        )
    except rclone_wrapper.RcloneError as exc:
        logger.warning("proton_drive_start: write_proton_config failed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"rclone not available: {exc}",
        )

    ok, msg = await rclone_wrapper.rclone_remote_test(
        rclone_wrapper.PROTON_REMOTE_NAME, config_path,
    )
    if ok:
        # No 2FA — persist directly.
        link_id = await _proton_persist_link(
            session, user.id,
            payload.email, payload.password, totp="",
        )
        # Delete the tmp config; the persistence step writes a fresh
        # one under the real link_id (and re-materializes on demand
        # via _ensure_rclone_config if it's ever wiped).
        try:
            config_path.unlink(missing_ok=True)
        except Exception:
            pass
        return ProtonStartResponse(
            requires_2fa=False, link_id=link_id,
            message="Proton Drive connected.",
        )

    if rclone_wrapper.is_2fa_required_error(msg):
        session_id = _ic_secrets.token_urlsafe(24)
        _PROTON_PENDING[session_id] = {
            "user_id": user.id,
            "email": payload.email,
            "password": payload.password,
            "tmp_link_id": tmp_link_id,
            "created_at": datetime.now(timezone.utc),
        }
        return ProtonStartResponse(
            requires_2fa=True, session_id=session_id,
        )

    # Credentials rejected outright. Clean up the tmp config and
    # surface Proton's message verbatim — the user needs actionable
    # feedback ("wrong password" vs "account locked" etc).
    try:
        config_path.unlink(missing_ok=True)
    except Exception:
        pass
    logger.warning(
        "proton_drive_start: probe failed (no 2fa indicator) msg=%s",
        msg[:300],
    )
    # Detect specific failure modes and surface a more helpful
    # message than rclone's raw Go struct dump.
    #
    # When the account authenticates fine but Proton's `/shares`
    # endpoint returns NO shares, rclone dumps an empty Share{}
    # struct (`ShareID:"", LinkID:"", VolumeID:"", ...`). The cause
    # is almost always that the user never visited drive.proton.me
    # — Proton Drive is opt-in and the user's first visit
    # provisions their root share + volume. Without that, rclone
    # auth succeeds but there's literally nothing to mirror.
    pretty = msg[:300] or "unknown error"
    is_no_drive = "ShareID:\"\"" in msg and "VolumeID:\"\"" in msg
    if is_no_drive:
        pretty = (
            "Your Proton account is authenticated, but Proton Drive "
            "isn't initialized. Visit https://drive.proton.me once "
            "with this account (it'll provision your root volume), "
            "then come back and click Connect again."
        )
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f"Proton sign-in failed: {pretty}",
    )


class ProtonVerifyRequest(BaseModel):
    session_id: str
    code: str


class ProtonVerifyResponse(BaseModel):
    link_id: int


@router.post("/proton-drive/verify", response_model=ProtonVerifyResponse)
async def proton_drive_verify(
    payload: ProtonVerifyRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProtonVerifyResponse:
    """Step 2 of the Proton Drive connect handshake. Rewrites the
    rclone config with the 2fa field set to the user-supplied code,
    probes again, persists the link on success."""
    from backend import rclone_wrapper
    from backend.cloud_sync import _proton_drive_pack_credentials

    _proton_sweep_pending()
    pending = _PROTON_PENDING.get(payload.session_id)
    if pending is None or pending["user_id"] != user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sign-in session not found or expired. Start over.",
        )

    code = payload.code.strip().replace(" ", "").replace("-", "")
    tmp_link_id = pending["tmp_link_id"]
    config_path = rclone_wrapper.write_proton_config(
        tmp_link_id,
        pending["email"], pending["password"], totp=code,
    )
    ok, msg = await rclone_wrapper.rclone_remote_test(
        rclone_wrapper.PROTON_REMOTE_NAME, config_path,
    )
    if not ok:
        logger.warning(
            "proton_drive_verify: probe rejected the 2FA code msg=%s",
            msg[:300],
        )
        # Don't drop the pending entry — the user might want to retry
        # with a fresh code from the authenticator.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"That code didn't work: {msg[:200] or 'try a fresh code'}",
        )

    link_id = await _proton_persist_link(
        session, user.id,
        pending["email"], pending["password"], totp=code,
    )
    try:
        config_path.unlink(missing_ok=True)
    except Exception:
        pass
    _PROTON_PENDING.pop(payload.session_id, None)
    return ProtonVerifyResponse(link_id=link_id)


async def _proton_persist_link(
    session: AsyncSession,
    user_id: UUID,
    email: str,
    password: str,
    totp: str,
) -> int:
    """Encrypt the Proton credentials, upsert the CloudLink row,
    materialize the rclone config under the final link id."""
    from backend import rclone_wrapper
    from backend.cloud_sync import _proton_drive_pack_credentials

    encrypted_blob = _proton_drive_pack_credentials(email, password, totp)

    existing = (
        await session.execute(
            select(CloudLink).where(
                CloudLink.user_id == user_id,
                CloudLink.provider == "proton_drive",
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        link = CloudLink(
            user_id=user_id,
            provider="proton_drive",
            encrypted_refresh_token=encrypted_blob,
            scopes="",
            status="active",
        )
        session.add(link)
    else:
        existing.encrypted_refresh_token = encrypted_blob
        existing.scopes = ""
        existing.status = "active"
        link = existing
    await session.commit()
    await session.refresh(link)

    # Write the per-link rclone config under the final id so the next
    # sync run finds it without re-materializing from the encrypted
    # blob.
    rclone_wrapper.write_proton_config(link.id, email, password, totp)
    return link.id


# ---------- §C4.6 — MEGA sign-in (via rclone) ------------------------------
#
# MEGA's rclone backend doesn't surface a 2FA hook (MEGA's web 2FA
# protects the browser sign-in only; the SDK rclone uses under the
# hood accepts the bare password). Single-step flow:
#
#   /cloud/mega/start  →  email + password
#       Probe `rclone about mega:`. On success, persist + return
#       link_id. On failure, 400.

class MegaStartRequest(BaseModel):
    email: EmailStr
    password: str


class MegaStartResponse(BaseModel):
    link_id: int
    message: str | None = None


@router.post("/mega/start", response_model=MegaStartResponse)
async def mega_start(
    payload: MegaStartRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MegaStartResponse:
    """Single-step MEGA connect. Writes a temp rclone config, probes
    with `rclone about`, persists on success."""
    from backend import rclone_wrapper
    from backend.cloud_sync import _mega_pack_credentials

    tmp_link_id = f"tmp-{_ic_secrets.token_urlsafe(12)}"
    try:
        config_path = rclone_wrapper.write_mega_config(
            tmp_link_id, payload.email, payload.password,
        )
    except rclone_wrapper.RcloneError as exc:
        logger.warning("mega_start: write_mega_config failed: %s", exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"rclone not available: {exc}",
        )

    ok, msg = await rclone_wrapper.rclone_remote_test(
        rclone_wrapper.MEGA_REMOTE_NAME, config_path,
    )
    if not ok:
        try:
            config_path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.warning(
            "mega_start: probe failed msg=%s", msg[:300],
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"MEGA sign-in failed: {msg[:300] or 'unknown error'}",
        )

    link_id = await _mega_persist_link(
        session, user.id, payload.email, payload.password,
    )
    try:
        config_path.unlink(missing_ok=True)
    except Exception:
        pass
    return MegaStartResponse(
        link_id=link_id, message="MEGA connected.",
    )


async def _mega_persist_link(
    session: AsyncSession,
    user_id: UUID,
    email: str,
    password: str,
) -> int:
    """Encrypt the MEGA credentials, upsert the CloudLink row,
    materialize the per-link rclone config under the final id."""
    from backend import rclone_wrapper
    from backend.cloud_sync import _mega_pack_credentials

    encrypted_blob = _mega_pack_credentials(email, password)

    existing = (
        await session.execute(
            select(CloudLink).where(
                CloudLink.user_id == user_id,
                CloudLink.provider == "mega",
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        link = CloudLink(
            user_id=user_id,
            provider="mega",
            encrypted_refresh_token=encrypted_blob,
            scopes="",
            status="active",
        )
        session.add(link)
    else:
        existing.encrypted_refresh_token = encrypted_blob
        existing.scopes = ""
        existing.status = "active"
        link = existing
    await session.commit()
    await session.refresh(link)

    rclone_wrapper.write_mega_config(link.id, email, password)
    return link.id
