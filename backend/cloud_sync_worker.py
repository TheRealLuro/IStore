"""§C2 — hourly cloud-sync sweep.

Background task launched from `backend/app.py:lifespan`. Wakes up on
`settings.cloud_sync_interval_seconds` (default 3600s), queries every
active `CloudLink`, and calls `sync_user_provider` for each. Errors
on one link don't poison the loop — we log + continue.

Why not an external scheduler (arq, celery, cron)?
  For v1, "every hour from inside the API process" is the simplest
  thing that works, and the wall-clock cost of the sweep is bounded
  by the number of cloud-connected users (small for self-host).
  When we hit the point where the sweep takes longer than the
  interval, or where we want explicit retry / backoff per link,
  arq becomes the right move. The API surface (`sync_user_provider`)
  is unchanged either way.

The task is launched at app startup and cancelled at shutdown via
the `lifespan` `try/finally`. Test runs flip `cloud_sync_hourly_enabled=
false` so pytest doesn't fire outbound HTTP.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from backend.config import settings
from backend.db import SessionLocal
from backend.models import CloudLink

logger = logging.getLogger(__name__)


async def run_hourly_sweep() -> None:
    """Forever-loop. Cancelled by the lifespan shutdown."""
    # Lazy import — the cloud_sync module pulls in google-api-python-client
    # transitively; if the operator hasn't installed the [cloud] extra
    # we'd raise at module-load. Importing inside the loop body keeps
    # the worker silent until it's actually needed.
    from backend.cloud_sync import (
        CloudSyncNotConfigured,
        sync_user_provider,
    )

    # First tick delay so we don't fight startup work for the first
    # minute of the process. After that, sleep `cloud_sync_interval_seconds`
    # between sweeps.
    initial_delay = min(60, settings.cloud_sync_interval_seconds)
    try:
        await asyncio.sleep(initial_delay)
    except asyncio.CancelledError:
        return

    while True:
        try:
            async with SessionLocal() as session:
                links = (
                    await session.execute(
                        select(CloudLink).where(
                            # `error`, `conflicts`, and `over_quota` links
                            # still get swept — the user may have resolved
                            # the underlying issue (or freed up space) and
                            # we'd otherwise leave the link stuck forever.
                            CloudLink.status.in_(
                                ("active", "conflicts", "error", "over_quota")
                            )
                        )
                    )
                ).scalars().all()
            for link in links:
                try:
                    async with SessionLocal() as session:
                        await sync_user_provider(
                            session, link.user_id, link.provider,
                        )
                except CloudSyncNotConfigured as exc:
                    # Missing creds / encryption key — log once at
                    # info, not error. Operator's call.
                    logger.info(
                        "cloud-sync skip link=%s provider=%s reason=%s",
                        link.id, link.provider, exc,
                    )
                except Exception:
                    logger.exception(
                        "cloud-sync failed link=%s provider=%s",
                        link.id, link.provider,
                    )
        except Exception:
            logger.exception("cloud-sync sweep iteration crashed")

        try:
            await asyncio.sleep(settings.cloud_sync_interval_seconds)
        except asyncio.CancelledError:
            return
