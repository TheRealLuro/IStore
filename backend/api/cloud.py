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

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
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
        provider, link.encrypted_refresh_token.encode("utf-8") if isinstance(link.encrypted_refresh_token, str) else link.encrypted_refresh_token,
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
from datetime import datetime, timezone

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
