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
        return RedirectResponse(
            url=f"{fe_root}/?cloud_error={error}", status_code=302
        )
    if not code or not state:
        return RedirectResponse(
            url=f"{fe_root}/?cloud_error=missing_code_or_state", status_code=302
        )
    if provider not in PROVIDER_SCOPES:
        return RedirectResponse(
            url=f"{fe_root}/?cloud_error=unsupported_provider", status_code=302
        )

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


@router.post("/links/{link_id}/sync", response_model=SyncResponse)
async def trigger_sync(
    link_id: int,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SyncResponse:
    """Run a sync for one cloud link, synchronously, returning counts.

    Synchronous because Drive listing for a typical user takes a few
    seconds — fast enough for an API call, no need for arq yet. When
    the sync grows past 30 seconds we'll move it behind the worker.
    """
    link = await session.get(CloudLink, link_id)
    if link is None or link.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    try:
        result = await sync_user_provider(session, user.id, link.provider)  # type: ignore[arg-type]
    except CloudSyncNotConfigured as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except Exception as exc:
        # Catch provider-side failures (googleapiclient HttpError 403/404,
        # GitHub 401, network errors) and translate to a clean 502 with
        # the operator-actionable message extracted. Without this, the
        # exception escapes the request thread and uvicorn drops the
        # connection mid-response — the browser then surfaces it as
        # "Failed to fetch" with no detail the user can act on.
        logger.exception(
            "trigger_sync: provider call failed user=%s provider=%s",
            user.id, link.provider,
        )
        msg = _extract_provider_error_message(exc) or "Sync failed. Try again in a moment."
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=msg) from exc
    return SyncResponse(**result)


def _extract_provider_error_message(exc: Exception) -> str | None:
    """Pull the human-readable error text out of a Google/GitHub client
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
    diff index just stops being a useful reference."""
    row = await session.get(CloudLink, link_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
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

        ids = (
            await session.execute(
                select(ImageModel.id).where(
                    ImageModel.user_id == user.id,
                    ImageModel.source_provider == link.provider,
                    ImageModel.deleted_at.is_(None),
                    ImageModel.pending_summary.is_(True),
                )
            )
        ).scalars().all()
        for img_id in ids:
            try:
                # Fair-queue requires user_id so jobs land in this
                # user's per-user list (round-robin between users).
                if await job_q.enqueue_summarize(user.id, img_id):
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
