"""Zero-knowledge vault API tests (VLT-4).

Two layers:
  - Pure unit tests for the base64 input gate (_b64_field) — the
    security-critical validation that bounds what the server stores.
  - Integration tests (db_client) for the round-trip, the setup/409
    guards, and cross-user isolation. These skip without a live DB.

The defining property under test: the server stores + returns
ciphertext BYTE-FOR-BYTE and never needs to interpret it.
"""

import base64
import uuid as _uuid

import pytest

from fastapi import HTTPException

from backend.api.vault import _b64_field, _NONCE_BYTES
from tests.conftest import register_and_login


# ---------- pure: the base64 input gate ----------


def test_b64_field_roundtrips_valid():
    raw = bytes(range(12))
    out = _b64_field(
        base64.b64encode(raw).decode(), min_bytes=12, max_bytes=12,
        exact=12, field="nonce",
    )
    assert out == raw


def test_b64_field_rejects_bad_base64():
    with pytest.raises(HTTPException) as ei:
        _b64_field("not!!base64", min_bytes=1, max_bytes=99, field="x")
    assert ei.value.status_code == 422


def test_b64_field_rejects_wrong_exact_length():
    # 11 bytes where exactly 12 required (nonce).
    raw = bytes(11)
    with pytest.raises(HTTPException) as ei:
        _b64_field(
            base64.b64encode(raw).decode(), min_bytes=_NONCE_BYTES,
            max_bytes=_NONCE_BYTES, exact=_NONCE_BYTES, field="nonce",
        )
    assert ei.value.status_code == 422


def test_b64_field_rejects_oversize():
    raw = b"x" * 1000
    with pytest.raises(HTTPException) as ei:
        _b64_field(base64.b64encode(raw).decode(), min_bytes=1,
                   max_bytes=256, field="ciphertext")
    assert ei.value.status_code == 422


def test_b64_field_rejects_under_min():
    with pytest.raises(HTTPException) as ei:
        _b64_field(base64.b64encode(b"").decode(), min_bytes=1,
                   max_bytes=256, field="ciphertext")
    assert ei.value.status_code == 422


# ---------- integration: round-trip + guards + isolation ----------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _setup_body():
    return {
        "kdf": "PBKDF2-SHA256",
        "kdf_iterations": 600000,
        "kdf_salt": _b64(bytes(range(16))),
        "verifier_nonce": _b64(bytes(range(12))),
        "verifier_ct": _b64(b"verifier-ciphertext-bytes"),
    }


async def test_vault_setup_and_meta_roundtrip(db_client):
    _t, headers = await register_and_login(
        db_client, email=f"v{_uuid.uuid4().hex[:8]}@example.com")

    # No vault yet → 404.
    r = await db_client.get("/vault/meta", headers=headers)
    assert r.status_code == 404

    body = _setup_body()
    r = await db_client.post("/vault/setup", json=body, headers=headers)
    assert r.status_code == 201, r.text
    got = r.json()
    assert got["kdf_iterations"] == 600000
    assert got["kdf_salt"] == body["kdf_salt"]
    assert got["verifier_ct"] == body["verifier_ct"]

    # Meta now readable + identical.
    r = await db_client.get("/vault/meta", headers=headers)
    assert r.status_code == 200
    assert r.json()["verifier_nonce"] == body["verifier_nonce"]


async def test_second_setup_conflicts(db_client):
    _t, headers = await register_and_login(
        db_client, email=f"v{_uuid.uuid4().hex[:8]}@example.com")
    assert (await db_client.post("/vault/setup", json=_setup_body(),
                                 headers=headers)).status_code == 201
    r = await db_client.post("/vault/setup", json=_setup_body(), headers=headers)
    assert r.status_code == 409


async def test_low_kdf_iterations_rejected(db_client):
    _t, headers = await register_and_login(
        db_client, email=f"v{_uuid.uuid4().hex[:8]}@example.com")
    body = _setup_body()
    body["kdf_iterations"] = 1000  # below the 310k floor
    r = await db_client.post("/vault/setup", json=body, headers=headers)
    assert r.status_code == 422


async def test_item_before_setup_conflicts(db_client):
    _t, headers = await register_and_login(
        db_client, email=f"v{_uuid.uuid4().hex[:8]}@example.com")
    r = await db_client.post("/vault/items", headers=headers, json={
        "kind": "password",
        "nonce": _b64(bytes(range(12))),
        "ciphertext": _b64(b"some-ciphertext"),
    })
    assert r.status_code == 409


async def test_item_crud_roundtrips_ciphertext_exactly(db_client):
    _t, headers = await register_and_login(
        db_client, email=f"v{_uuid.uuid4().hex[:8]}@example.com")
    await db_client.post("/vault/setup", json=_setup_body(), headers=headers)

    ct = bytes(range(200))  # arbitrary binary "ciphertext"
    nonce = bytes(range(12))
    r = await db_client.post("/vault/items", headers=headers, json={
        "kind": "password", "nonce": _b64(nonce), "ciphertext": _b64(ct),
    })
    assert r.status_code == 201, r.text
    item = r.json()
    item_id = item["id"]
    # Byte-for-byte round trip — server stored opaque bytes.
    assert base64.b64decode(item["ciphertext"]) == ct
    assert base64.b64decode(item["nonce"]) == nonce

    # List shows it.
    r = await db_client.get("/vault/items", headers=headers)
    assert r.status_code == 200
    assert any(i["id"] == item_id for i in r.json())

    # Update replaces the encrypted payload.
    ct2 = bytes(reversed(range(200)))
    r = await db_client.put(f"/vault/items/{item_id}", headers=headers, json={
        "nonce": _b64(nonce), "ciphertext": _b64(ct2),
    })
    assert r.status_code == 200
    assert base64.b64decode(r.json()["ciphertext"]) == ct2

    # Delete.
    r = await db_client.delete(f"/vault/items/{item_id}", headers=headers)
    assert r.status_code == 204
    r = await db_client.get("/vault/items", headers=headers)
    assert all(i["id"] != item_id for i in r.json())


async def test_cross_user_isolation(db_client):
    # User A creates an item; user B must not see/update/delete it.
    _ta, ha = await register_and_login(
        db_client, email=f"a{_uuid.uuid4().hex[:8]}@example.com")
    _tb, hb = await register_and_login(
        db_client, email=f"b{_uuid.uuid4().hex[:8]}@example.com")
    await db_client.post("/vault/setup", json=_setup_body(), headers=ha)
    r = await db_client.post("/vault/items", headers=ha, json={
        "kind": "note", "nonce": _b64(bytes(range(12))),
        "ciphertext": _b64(b"A's secret"),
    })
    a_item = r.json()["id"]

    # B's list never contains A's item.
    r = await db_client.get("/vault/items", headers=hb)
    assert all(i["id"] != a_item for i in r.json())
    # B can't update or delete A's item → 404.
    assert (await db_client.put(f"/vault/items/{a_item}", headers=hb, json={
        "nonce": _b64(bytes(range(12))), "ciphertext": _b64(b"hijack"),
    })).status_code == 404
    assert (await db_client.delete(
        f"/vault/items/{a_item}", headers=hb)).status_code == 404


async def test_wipe_clears_everything(db_client):
    _t, headers = await register_and_login(
        db_client, email=f"v{_uuid.uuid4().hex[:8]}@example.com")
    await db_client.post("/vault/setup", json=_setup_body(), headers=headers)
    await db_client.post("/vault/items", headers=headers, json={
        "kind": "password", "nonce": _b64(bytes(range(12))),
        "ciphertext": _b64(b"x"),
    })
    r = await db_client.delete("/vault", headers=headers)
    assert r.status_code == 204
    # Meta gone (404) + items gone.
    assert (await db_client.get("/vault/meta", headers=headers)).status_code == 404
    assert (await db_client.get("/vault/items", headers=headers)).json() == []
