"""§C4.1 — display_name is required on signup.

Before, /auth/register accepted `display_name: None` (or omitted
the field entirely). The FE then showed the email localpart as a
placeholder greeting which leaked email everywhere a friendly name
should appear.

This PR makes `display_name` a required, validated field on the
UserCreate schema:

  - 1–80 chars after `.strip()`
  - whitespace-only inputs rejected
  - ASCII control characters (\\x00–\\x1f, \\x7f) rejected
  - leading/trailing whitespace silently trimmed
  - returned by /users/me + GET /account/* exactly as persisted

Existing rows from before this change keep their NULL display_name;
the constraint applies at the registration boundary, not at the
column. Settings → Account still lets a legacy user fill theirs.
"""
from __future__ import annotations

import uuid


PASSWORD = "Aa1!aaaaaa"


async def _post_register(client, **overrides):
    """Minimal helper: builds a default-valid register body and
    overrides specific fields the test cares about."""
    body = {
        "email": f"c41-{uuid.uuid4().hex[:8]}@example.com",
        "password": PASSWORD,
        "display_name": "Alice Test",
        "age_confirmed": True,
    }
    body.update(overrides)
    return await client.post("/auth/register", json=body)


async def test_register_succeeds_with_display_name(db_client):
    """Happy path. Field present + valid → 201; the row carries
    the trimmed display_name."""
    r = await _post_register(db_client, display_name="Alice Test")
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["display_name"] == "Alice Test"


async def test_register_rejects_missing_display_name(db_client):
    """Omitting the field entirely → 422 with a field_required
    message. (Pydantic surfaces the missing-key error before our
    validator runs.)"""
    r = await db_client.post(
        "/auth/register",
        json={
            "email": f"c41-miss-{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
            "age_confirmed": True,
        },
    )
    assert r.status_code == 422, r.text
    body = r.json()
    # Pydantic v2 returns a list of errors under `detail`; check
    # that at least one points at display_name.
    fields = {tuple(err.get("loc") or ()) for err in body.get("detail", [])}
    assert any("display_name" in loc for loc in fields), body


async def test_register_rejects_null_display_name(db_client):
    """Explicit `null` in the JSON body → 422 (not silently
    converted to empty / default)."""
    r = await _post_register(db_client, display_name=None)
    assert r.status_code == 422, r.text


async def test_register_rejects_empty_display_name(db_client):
    """Empty string → 422 (min_length=1 on the Field constraint)."""
    r = await _post_register(db_client, display_name="")
    assert r.status_code == 422, r.text


async def test_register_rejects_whitespace_only_display_name(db_client):
    """Whitespace-only ("   ", "\\t", etc.) → 422 from the validator
    (the field-level min_length=1 passes because length is >0, but
    the validator's post-strip empty-check catches it)."""
    r = await _post_register(db_client, display_name="   ")
    assert r.status_code == 422, r.text


async def test_register_rejects_too_long_display_name(db_client):
    """81 chars → 422 (max_length=80 on the Field constraint)."""
    r = await _post_register(db_client, display_name="x" * 81)
    assert r.status_code == 422, r.text


async def test_register_accepts_exactly_80_chars(db_client):
    """Boundary: 80 chars on the dot → still accepted."""
    name = "x" * 80
    r = await _post_register(db_client, display_name=name)
    assert r.status_code in (200, 201), r.text
    assert r.json()["display_name"] == name


async def test_register_trims_leading_trailing_whitespace(db_client):
    """`"  Alice  "` → persisted as `"Alice"`. The mode='before'
    validator runs ahead of length constraints + persistence."""
    r = await _post_register(db_client, display_name="  Alice  ")
    assert r.status_code in (200, 201), r.text
    assert r.json()["display_name"] == "Alice"


async def test_register_rejects_control_characters(db_client):
    """Embedded NUL / tab / form-feed → 422. Tabs at the edge get
    stripped by the trim, but embedded ones fall to the control-
    char check."""
    for bad in ("Alice\x00", "Al\x07ice", "A\x1ble", "Alice\x7f"):
        r = await _post_register(db_client, display_name=bad)
        assert r.status_code == 422, (
            f"control char in {bad!r} should reject; got {r.status_code} "
            f"{r.text}"
        )


async def test_register_accepts_unicode_and_emoji(db_client):
    """Unicode letters, accents, scripts other than Latin, and
    emoji are all valid. The control-char check uses ord(c) so
    non-ASCII printables pass."""
    for good in ("Алиса", "Ångström", "中村", "Alice 🌟", "О'Брайен"):
        r = await _post_register(
            db_client, display_name=good,
            email=f"c41-uni-{uuid.uuid4().hex[:8]}@example.com",
        )
        assert r.status_code in (200, 201), (
            f"unicode name {good!r} should pass; got {r.status_code} {r.text}"
        )
        assert r.json()["display_name"] == good


async def test_userread_returns_display_name(db_client):
    """After signup, GET /users/me should return the display_name
    we sent (so the FE greeting reads the right value)."""
    from tests.conftest import register_and_login

    name = f"Test Persona {uuid.uuid4().hex[:4]}"
    _, headers = await register_and_login(
        db_client,
        email=f"c41-me-{uuid.uuid4().hex[:8]}@example.com",
        display_name=name,
    )
    r = await db_client.get("/users/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["display_name"] == name
