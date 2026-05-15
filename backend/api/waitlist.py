"""Public waitlist signup + admin viewer.

Two surfaces:

  POST /waitlist/signup       — public, unauthenticated, rate-limited
                                10/min/IP. Idempotent on email; the
                                response never confirms whether the
                                email was new or repeat (anti-enum).

  GET  /admin/waitlist        — superuser-only. Returns the full list
                                for the admin overlay to render.

  PATCH /admin/waitlist/{id}/notified  — flips `notified=true` so the
                                launch-notification batch can mark
                                rows as already-pinged.

Audit trail: every public signup writes a `waitlist.signup` row in
audit_log with the recipient email + IP + UA. Admin reads are not
audited (it's the standard admin browse pattern; the existing
SecurityControlsMiddleware brute-force guard covers misuse).

This file is deliberately small and self-contained so the marketing
site can call it without pulling any of the photo-pipeline dependency
chain in. The only required ext is `citext` (already enabled by an
earlier migration, re-enabled by 0025 defensively)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import current_superuser
from backend.db import get_session
from backend.models import User, WaitlistSignup
from backend.schemas import (
    WaitlistEntryRead,
    WaitlistSignupCreate,
    WaitlistSignupResult,
)
from backend.security import client_ip, enforce_rate_limit


router = APIRouter(tags=["waitlist"])


# --------------------------------------------------------------------- #
# Public surface — marketing site signup.
# --------------------------------------------------------------------- #


@router.post(
    "/waitlist/signup",
    response_model=WaitlistSignupResult,
    status_code=status.HTTP_200_OK,
)
async def signup(
    payload: WaitlistSignupCreate,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WaitlistSignupResult:
    """Public signup. Idempotent on email; never reveals whether the
    address was already on the list (so an attacker can't enumerate
    members by trying emails and observing the response)."""

    ip = client_ip(request)
    await enforce_rate_limit(
        key=f"waitlist:signup:{ip}",
        limit=10,
        window_seconds=60,
        detail="Too many signup attempts. Please try again in a minute.",
    )

    user_agent = (request.headers.get("user-agent") or "")[:500] or None

    stmt = pg_insert(WaitlistSignup).values(
        email=payload.email,
        use_case=payload.use_case,
        source="marketing-site",
        ip=ip if ip != "unknown" else None,
        user_agent=user_agent,
    )
    # On conflict, refresh use_case + created_at so the admin sees the
    # most recent intent. Leave the original ip / ua intact for fraud
    # review (first signal is the honest one).
    stmt = stmt.on_conflict_do_update(
        index_elements=[WaitlistSignup.email],
        set_={
            "use_case": stmt.excluded.use_case,
            "created_at": datetime.now(timezone.utc),
        },
    )

    stmt = stmt.returning(WaitlistSignup.id)
    res = await session.execute(stmt)
    if res.scalar_one_or_none() is None:
        raise HTTPException(status_code=500, detail="signup failed")

    # The public response is intentionally indifferent between new and
    # duplicate signups so an attacker can't enumerate members by
    # probing emails. The admin viewer is the source of truth.
    await add_audit(
        session,
        user_id=None,
        action="waitlist.signup",
        details={
            "email": payload.email,
            "use_case": payload.use_case,
            "ip": ip,
            "ua": user_agent,
        },
    )
    await session.commit()

    return WaitlistSignupResult(ok=True, already_signed_up=False)


# --------------------------------------------------------------------- #
# Admin surface — viewer.
# --------------------------------------------------------------------- #


@router.get("/admin/waitlist", response_model=list[WaitlistEntryRead])
async def list_waitlist(
    _admin: Annotated[User, Depends(current_superuser)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 500,
    offset: int = 0,
) -> list[WaitlistEntryRead]:
    """Newest-first list of waitlist signups. Pagination is bounded
    by `limit` (max 500) so a stray ?limit=999999 doesn't dump the
    whole table over the wire."""

    limit = max(1, min(500, limit))
    offset = max(0, offset)

    rows = (
        await session.execute(
            select(WaitlistSignup)
            .order_by(WaitlistSignup.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return [WaitlistEntryRead.model_validate(r) for r in rows]


@router.patch(
    "/admin/waitlist/{entry_id}/notified",
    response_model=WaitlistEntryRead,
)
async def mark_notified(
    entry_id: int,
    _admin: Annotated[User, Depends(current_superuser)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WaitlistEntryRead:
    """Flip `notified=True` and stamp `notified_at`. Idempotent."""

    now = datetime.now(timezone.utc)
    res = await session.execute(
        update(WaitlistSignup)
        .where(WaitlistSignup.id == entry_id)
        .values(notified=True, notified_at=now)
        .returning(WaitlistSignup)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="waitlist entry not found")
    await session.commit()
    return WaitlistEntryRead.model_validate(row)
