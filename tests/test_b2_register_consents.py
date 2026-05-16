"""§B2 — consents collected BEFORE the register call.

Previously: /auth/register created the user, then the FE called
/consent/{kind}/grant per scope post-signup. Legal requirement is
"consent precedes data collection," so consent rows must exist
before the User row is externally visible.

These tests verify:
- POST /auth/register with a `consents` bundle persists
  ConsentRecord rows in the same transaction as the user.
- Each consent + state value is validated against SUPPORTED_SCOPES;
  bogus scopes are silently dropped (logged) so a malformed FE
  doesn't break account creation.
- The `consent_signature` value is stored verbatim on each grant
  row (so the audit chain has the user's typed name).
- Registration without consents still works (legacy path).
- An audit row `consent.register.<kind>.<state>` is written per
  grant.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from tests.conftest import fetch_user_id


PASSWORD = "Aa1!aaaaaa"


async def _register(client, email: str, *, consents=None, signature: str | None = None):
    body = {
        "email": email,
        "password": PASSWORD,
        "age_confirmed": True,
    }
    if consents is not None:
        body["consents"] = consents
    if signature is not None:
        body["consent_signature"] = signature
    return await client.post("/auth/register", json=body)


async def test_register_without_consents_still_works(db_client):
    r = await _register(db_client, "no-consent@example.com")
    assert r.status_code in (200, 201), r.text


async def test_register_with_consents_writes_consent_rows(db_client):
    email = "with-consent@example.com"
    consents = [
        {"kind": "gps_retention", "state": "GRANTED"},
        {"kind": "ai_summary",    "state": "WITHDRAWN"},
        {"kind": "face_recognition", "state": "GRANTED"},
    ]
    r = await _register(db_client, email, consents=consents, signature="Test User")
    assert r.status_code in (200, 201), r.text

    uid = await fetch_user_id(email)
    from backend.db import SessionLocal
    from backend.models import ConsentRecord

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == uid)
            )
        ).scalars().all()
        states = {(r.consent_kind, r.state) for r in rows}
        assert ("gps_retention", "GRANTED") in states
        assert ("ai_summary", "WITHDRAWN") in states
        assert ("face_recognition", "GRANTED") in states
        # Signature is stamped on each grant.
        sigs = {r.signature_text for r in rows}
        assert sigs == {"Test User"}


async def test_register_drops_unsupported_scope(db_client):
    """The legacy /consent endpoints 400 on a bad kind; the register
    bundle silently skips so one stray field doesn't break the
    whole signup."""
    email = "bad-scope@example.com"
    consents = [
        {"kind": "made_up_scope", "state": "GRANTED"},
        {"kind": "gps_retention", "state": "GRANTED"},
    ]
    r = await _register(db_client, email, consents=consents)
    assert r.status_code in (200, 201), r.text

    uid = await fetch_user_id(email)
    from backend.db import SessionLocal
    from backend.models import ConsentRecord

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == uid)
            )
        ).scalars().all()
        kinds = {r.consent_kind for r in rows}
        assert "gps_retention" in kinds
        assert "made_up_scope" not in kinds


async def test_register_consents_write_audit_rows(db_client):
    email = "audit-consent@example.com"
    consents = [{"kind": "gps_retention", "state": "GRANTED"}]
    r = await _register(db_client, email, consents=consents)
    assert r.status_code in (200, 201)

    uid = await fetch_user_id(email)
    from backend.db import SessionLocal
    from backend.models import AuditLog

    async with SessionLocal() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.user_id == uid,
                    AuditLog.action == "consent.register.gps_retention.granted",
                )
            )
        ).scalars().all()
        assert len(rows) == 1


async def test_register_rejects_bad_state(db_client):
    """`state` must be GRANTED or WITHDRAWN — the pydantic regex on
    ConsentBundleItem rejects anything else with a 422."""
    r = await _register(
        db_client,
        "bad-state@example.com",
        consents=[{"kind": "gps_retention", "state": "lol"}],
    )
    assert r.status_code == 422
