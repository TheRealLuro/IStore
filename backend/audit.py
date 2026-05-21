from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AuditLog


# Audit D5 — hard cap on the serialized size of `details`. Audit
# row writes accept user-controlled keys/values (comment anchor
# payloads, cloud-sync remote paths, etc.) with no upstream cap,
# so an attacker could bloat the `audit_log` table by submitting
# pathologically large structures. 4 KiB serialized is well above
# the size of every legitimate audit row in this codebase (typical
# ~150 bytes) and well below the size at which JSONB column storage
# starts paying TOAST costs.
#
# When the cap fires, we don't drop the audit entirely — that would
# erase the fact that the action happened. Instead we replace
# `details` with a tombstone payload that records the original
# size and the top-level keys, so an operator looking at the row
# can still see WHAT was attempted even if not the full payload.
_MAX_DETAILS_BYTES = 4096

# Audit F12 — keys that hold PII or quasi-PII. The retention
# anonymizer (`sweep_audit_log_anonymize` in backend/retention.py)
# walks every aged-out row and replaces values for keys in this
# set with None, so the GDPR storage-limitation horizon applies
# to the JSONB blob, not just the `user_id` column.
#
# Every new audit `details` key that may carry user data MUST be
# added here (or to `_NON_PII_DETAILS_KEYS` below). A test in
# tests/test_audit_details_pii_coverage.py walks every add_audit
# call site and fails on un-classified keys.
PII_DETAILS_KEYS: frozenset[str] = frozenset({
    "email",
    "recipient_email",
    "ip",
    "ua",
    "user_agent",
    "google_sub",
    "identity",
    # `previous_email` is the OLD address on email-change rows —
    # also PII, also scrubbed.
    "previous_email",
})


def _cap_details(details: dict[str, Any]) -> dict[str, Any]:
    """If `details` serializes to more than `_MAX_DETAILS_BYTES`,
    replace with a small tombstone that records the original size
    and top-level keys. Audit-trail completeness is preserved
    (we always know an action happened) without giving an attacker
    a JSONB-bloat DoS lever."""
    try:
        encoded = json.dumps(details, default=str)
    except (TypeError, ValueError):
        # Non-JSON-serializable values shouldn't normally reach us,
        # but a future caller passing a dataclass / datetime / etc.
        # shouldn't crash the audit path. Best-effort: stringify.
        return {"_audit_serialize_error": True, "_keys": sorted(details.keys())}
    if len(encoded) <= _MAX_DETAILS_BYTES:
        return details
    return {
        "_truncated": True,
        "_original_size": len(encoded),
        "_max_size": _MAX_DETAILS_BYTES,
        "_keys": sorted(details.keys()),
    }


async def add_audit(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    action: str,
    details: dict[str, Any] | None = None,
    flush: bool = False,
) -> AuditLog:
    capped = _cap_details(details or {})
    row = AuditLog(user_id=user_id, action=action, details=capped)
    session.add(row)
    if flush:
        await session.flush()
    return row
