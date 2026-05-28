"""VLT-8 vault-as-drive API tests: account keypair, folders, encrypted files.

Pure tests cover the Range parser (the security-sensitive bit of the file
download path). Integration tests (db_client) drive the real routes with
MinIO stubbed by conftest, asserting the zero-knowledge contract holds:
the server stores + streams ciphertext byte-for-byte, never interprets it,
and fences everything to the owner.
"""

import base64
import os
import uuid

import pytest

from fastapi import HTTPException

from backend.api.vault import _parse_range
from tests.conftest import register_and_login


def _b64(n: int) -> str:
    return base64.b64encode(os.urandom(n)).decode()


# ---------- pure: HTTP Range parser ----------


def test_parse_range_none_for_blank():
    assert _parse_range(None, 100) is None
    assert _parse_range("", 100) is None
    assert _parse_range("items=0-1", 100) is None  # not a bytes range


def test_parse_range_basic():
    assert _parse_range("bytes=0-9", 100) == (0, 9)
    assert _parse_range("bytes=10-19", 100) == (10, 19)


def test_parse_range_open_ended_clamps_to_size():
    assert _parse_range("bytes=50-", 100) == (50, 99)
    assert _parse_range("bytes=0-999", 100) == (0, 99)


def test_parse_range_suffix():
    assert _parse_range("bytes=-20", 100) == (80, 99)


def test_parse_range_unsatisfiable_raises_416():
    for bad in ("bytes=100-200", "bytes=200-300", "bytes=abc-def", "bytes=-0"):
        with pytest.raises(HTTPException) as ei:
            _parse_range(bad, 100)
        assert ei.value.status_code == 416


# ---------- integration: setup + keypair ----------


async def _setup_vault(ac, h, with_keypair=True):
    body = {
        "kdf": "PBKDF2-SHA256", "kdf_iterations": 600000,
        "kdf_salt": _b64(16), "verifier_nonce": _b64(12), "verifier_ct": _b64(40),
    }
    if with_keypair:
        body["account_public_key"] = _b64(65)
        body["enc_account_private_key"] = _b64(166)
    r = await ac.post("/vault/setup", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_setup_persists_account_keypair(db_client):
    _, h = await register_and_login(db_client)
    meta = await _setup_vault(db_client, h)
    assert meta["account_public_key"]
    assert meta["enc_account_private_key"]
    r = await db_client.get("/vault/meta", headers=h)
    assert r.status_code == 200
    assert r.json()["account_public_key"] == meta["account_public_key"]


async def test_account_key_pair_required_together(db_client):
    _, h = await register_and_login(db_client)
    r = await db_client.post("/vault/setup", headers=h, json={
        "kdf": "PBKDF2-SHA256", "kdf_iterations": 600000,
        "kdf_salt": _b64(16), "verifier_nonce": _b64(12), "verifier_ct": _b64(40),
        "account_public_key": _b64(65),  # private half missing
    })
    assert r.status_code == 422


async def test_account_key_provision_then_409(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h, with_keypair=False)
    r = await db_client.post("/vault/account-key", headers=h, json={
        "account_public_key": _b64(65), "enc_account_private_key": _b64(166)})
    assert r.status_code == 200
    assert r.json()["account_public_key"]
    # second time → already exists
    r = await db_client.post("/vault/account-key", headers=h, json={
        "account_public_key": _b64(65), "enc_account_private_key": _b64(166)})
    assert r.status_code == 409


async def test_account_public_key_must_be_65_bytes(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h, with_keypair=False)
    r = await db_client.post("/vault/account-key", headers=h, json={
        "account_public_key": _b64(64), "enc_account_private_key": _b64(166)})
    assert r.status_code == 422


# ---------- integration: folders ----------


async def test_folder_crud_and_nesting(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    r = await db_client.post("/vault/folders", headers=h, json={
        "parent_id": None, "name_nonce": _b64(12), "name_ct": _b64(40)})
    assert r.status_code == 201
    fid = r.json()["id"]
    r = await db_client.post("/vault/folders", headers=h, json={
        "parent_id": fid, "name_nonce": _b64(12), "name_ct": _b64(40)})
    assert r.status_code == 201
    cid = r.json()["id"]
    # rename child
    r = await db_client.put(f"/vault/folders/{cid}", headers=h, json={
        "parent_id": fid, "name_nonce": _b64(12), "name_ct": _b64(60)})
    assert r.status_code == 200
    r = await db_client.get("/vault/folders", headers=h)
    assert r.status_code == 200 and len(r.json()) == 2


async def test_folder_move_into_descendant_rejected(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    r = await db_client.post("/vault/folders", headers=h, json={
        "parent_id": None, "name_nonce": _b64(12), "name_ct": _b64(40)})
    fid = r.json()["id"]
    r = await db_client.post("/vault/folders", headers=h, json={
        "parent_id": fid, "name_nonce": _b64(12), "name_ct": _b64(40)})
    cid = r.json()["id"]
    # move parent under its own child
    r = await db_client.put(f"/vault/folders/{fid}", headers=h, json={
        "parent_id": cid, "name_nonce": _b64(12), "name_ct": _b64(40)})
    assert r.status_code == 400
    # move into itself
    r = await db_client.put(f"/vault/folders/{fid}", headers=h, json={
        "parent_id": fid, "name_nonce": _b64(12), "name_ct": _b64(40)})
    assert r.status_code == 400


async def test_create_folder_bad_parent_404(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    r = await db_client.post("/vault/folders", headers=h, json={
        "parent_id": str(uuid.uuid4()), "name_nonce": _b64(12), "name_ct": _b64(40)})
    assert r.status_code == 404


# ---------- integration: encrypted files ----------


async def test_file_upload_download_roundtrip(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    r = await db_client.post("/vault/folders", headers=h, json={
        "parent_id": None, "name_nonce": _b64(12), "name_ct": _b64(40)})
    fid = r.json()["id"]

    payload = os.urandom(40000)
    r = await db_client.post(
        "/vault/files", headers=h,
        files={"blob": ("c.bin", payload, "application/octet-stream")},
        data={"nonce": _b64(12), "ciphertext": _b64(80),
              "wrapped_key": _b64(126), "folder_id": fid},
    )
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["has_file"] is True
    assert item["size_bytes"] == len(payload)
    assert item["wrapped_key"]
    assert item["folder_id"] == fid
    assert item["kind"] == "file"
    iid = item["id"]

    # full download
    r = await db_client.get(f"/vault/files/{iid}", headers=h)
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers.get("accept-ranges") == "bytes"

    # range download
    r = await db_client.get(f"/vault/files/{iid}",
                            headers={**h, "Range": "bytes=10-29"})
    assert r.status_code == 206
    assert r.content == payload[10:30]
    assert r.headers.get("content-range") == f"bytes 10-29/{len(payload)}"


async def test_file_listed_in_items(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    payload = os.urandom(1000)
    r = await db_client.post(
        "/vault/files", headers=h,
        files={"blob": ("c.bin", payload, "application/octet-stream")},
        data={"nonce": _b64(12), "ciphertext": _b64(80), "wrapped_key": _b64(126)},
    )
    iid = r.json()["id"]
    r = await db_client.get("/vault/items", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1 and rows[0]["id"] == iid and rows[0]["has_file"] is True


async def test_file_upload_bad_folder_404(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    r = await db_client.post(
        "/vault/files", headers=h,
        files={"blob": ("c.bin", b"abc", "application/octet-stream")},
        data={"nonce": _b64(12), "ciphertext": _b64(80), "wrapped_key": _b64(126),
              "folder_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404


async def test_delete_item_frees_blob(db_client):
    from backend.storage import storage
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    payload = os.urandom(2000)
    r = await db_client.post(
        "/vault/files", headers=h,
        files={"blob": ("c.bin", payload, "application/octet-stream")},
        data={"nonce": _b64(12), "ciphertext": _b64(80), "wrapped_key": _b64(126)},
    )
    iid = r.json()["id"]
    r = await db_client.delete(f"/vault/items/{iid}", headers=h)
    assert r.status_code == 204
    # downloading the now-deleted item → 404
    r = await db_client.get(f"/vault/files/{iid}", headers=h)
    assert r.status_code == 404


async def test_delete_folder_cascades_items(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    r = await db_client.post("/vault/folders", headers=h, json={
        "parent_id": None, "name_nonce": _b64(12), "name_ct": _b64(40)})
    fid = r.json()["id"]
    await db_client.post(
        "/vault/files", headers=h,
        files={"blob": ("c.bin", os.urandom(500), "application/octet-stream")},
        data={"nonce": _b64(12), "ciphertext": _b64(80), "wrapped_key": _b64(126),
              "folder_id": fid},
    )
    r = await db_client.delete(f"/vault/folders/{fid}", headers=h)
    assert r.status_code == 204
    r = await db_client.get("/vault/items", headers=h)
    assert r.status_code == 200 and len(r.json()) == 0
    r = await db_client.get("/vault/folders", headers=h)
    assert r.status_code == 200 and len(r.json()) == 0


# ---------- integration: cross-user isolation ----------


async def test_cross_user_isolation(db_client):
    _, h1 = await register_and_login(db_client)
    await _setup_vault(db_client, h1)
    r = await db_client.post("/vault/folders", headers=h1, json={
        "parent_id": None, "name_nonce": _b64(12), "name_ct": _b64(40)})
    fid = r.json()["id"]
    r = await db_client.post(
        "/vault/files", headers=h1,
        files={"blob": ("c.bin", os.urandom(300), "application/octet-stream")},
        data={"nonce": _b64(12), "ciphertext": _b64(80), "wrapped_key": _b64(126),
              "folder_id": fid},
    )
    iid = r.json()["id"]

    _, h2 = await register_and_login(db_client)
    await _setup_vault(db_client, h2)
    assert (await db_client.get(f"/vault/files/{iid}", headers=h2)).status_code == 404
    assert (await db_client.delete(f"/vault/items/{iid}", headers=h2)).status_code == 404
    assert (await db_client.delete(f"/vault/folders/{fid}", headers=h2)).status_code == 404
    # h1's data is intact
    assert (await db_client.get(f"/vault/files/{iid}", headers=h1)).status_code == 200
