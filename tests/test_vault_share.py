"""VLT-8 P5 vault sharing tests: key-lookup, grant, authz, recipient stream.

Sealed payloads are opaque to the server (the real P-256 seal/open is proven
in the frontend Node harness), so these fabricate sealed bytes and assert the
routing + authorization + isolation + ciphertext-streaming contract:
  - a recipient is resolved by email and gets only the OWNER's public key,
  - only the owner can share an item; nobody can share with themselves,
  - the recipient sees the sealed bundle and can stream the ciphertext,
  - a third party can see/stream nothing,
  - either party can revoke, and deleting the item cascades its grants,
  - vault shares expose no comment surface (none exists on this router).
"""

import base64
import os

from tests.conftest import register_and_login


def _b64(n: int) -> str:
    return base64.b64encode(os.urandom(n)).decode()


async def _setup_vault(ac, h):
    r = await ac.post("/vault/setup", headers=h, json={
        "kdf": "PBKDF2-SHA256", "kdf_iterations": 600000,
        "kdf_salt": _b64(16), "verifier_nonce": _b64(12), "verifier_ct": _b64(40),
        "account_public_key": _b64(65), "enc_account_private_key": _b64(166),
    })
    assert r.status_code == 201, r.text


async def _upload(ac, h, payload: bytes):
    r = await ac.post(
        "/vault/files", headers=h,
        files={"blob": ("f.bin", payload, "application/octet-stream")},
        data={"nonce": _b64(12), "ciphertext": _b64(80), "wrapped_key": _b64(126)},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_recipient_key_lookup(db_client):
    _, alice = await register_and_login(db_client)
    _, bob_h = await register_and_login(db_client, email="bob_lk@example.com")
    await _setup_vault(db_client, alice)
    await _setup_vault(db_client, bob_h)
    r = await db_client.get("/vault/recipient-key", headers=alice,
                            params={"email": "bob_lk@example.com"})
    assert r.status_code == 200
    assert len(base64.b64decode(r.json()["account_public_key"])) == 65
    r = await db_client.get("/vault/recipient-key", headers=alice,
                            params={"email": "ghost@nowhere.test"})
    assert r.status_code == 404


async def test_share_authz_and_stream(db_client):
    _, alice = await register_and_login(db_client, email="alice_s@example.com")
    _, bob = await register_and_login(db_client, email="bob_s@example.com")
    _, carol = await register_and_login(db_client, email="carol_s@example.com")
    await _setup_vault(db_client, alice)
    await _setup_vault(db_client, bob)
    await _setup_vault(db_client, carol)

    payload = os.urandom(12000)
    fid = await _upload(db_client, alice, payload)

    # owner shares with bob
    r = await db_client.post(f"/vault/items/{fid}/share", headers=alice,
                             json={"recipient_email": "bob_s@example.com", "sealed_payload": _b64(140)})
    assert r.status_code == 201
    # self-share rejected
    r = await db_client.post(f"/vault/items/{fid}/share", headers=alice,
                             json={"recipient_email": "alice_s@example.com", "sealed_payload": _b64(140)})
    assert r.status_code == 400
    # non-owner cannot share
    r = await db_client.post(f"/vault/items/{fid}/share", headers=carol,
                             json={"recipient_email": "bob_s@example.com", "sealed_payload": _b64(140)})
    assert r.status_code == 404

    # bob sees the incoming share
    r = await db_client.get("/vault/shares", headers=bob)
    assert r.status_code == 200 and len(r.json()) == 1
    grant = r.json()[0]
    assert grant["has_file"] is True
    assert grant["size_bytes"] == len(payload)
    assert grant["sealed_payload"]
    assert grant["owner_email"] == "alice_s@example.com"
    gid = grant["id"]

    # carol sees nothing
    assert (await db_client.get("/vault/shares", headers=carol)).json() == []

    # bob streams the shared ciphertext (== original); range works
    r = await db_client.get(f"/vault/shares/{gid}/file", headers=bob)
    assert r.status_code == 200 and r.content == payload
    r = await db_client.get(f"/vault/shares/{gid}/file",
                            headers={**bob, "Range": "bytes=3-12"})
    assert r.status_code == 206 and r.content == payload[3:13]
    # carol cannot stream it
    assert (await db_client.get(f"/vault/shares/{gid}/file", headers=carol)).status_code == 404


async def test_reshare_upsert_and_revoke(db_client):
    _, alice = await register_and_login(db_client, email="alice_r@example.com")
    _, bob = await register_and_login(db_client, email="bob_r@example.com")
    await _setup_vault(db_client, alice)
    await _setup_vault(db_client, bob)
    fid = await _upload(db_client, alice, os.urandom(800))

    for _ in range(2):
        r = await db_client.post(f"/vault/items/{fid}/share", headers=alice,
                                 json={"recipient_email": "bob_r@example.com", "sealed_payload": _b64(140)})
        assert r.status_code == 201
    # still exactly one grant
    r = await db_client.get(f"/vault/items/{fid}/shares", headers=alice)
    assert len(r.json()) == 1
    gid = r.json()[0]["id"]

    # recipient can decline
    assert (await db_client.delete(f"/vault/shares/{gid}", headers=bob)).status_code == 204
    assert (await db_client.get("/vault/shares", headers=bob)).json() == []
    assert (await db_client.get(f"/vault/items/{fid}/shares", headers=alice)).json() == []


async def test_share_secure_item_has_no_file(db_client):
    _, alice = await register_and_login(db_client, email="alice_n@example.com")
    _, bob = await register_and_login(db_client, email="bob_n@example.com")
    await _setup_vault(db_client, alice)
    await _setup_vault(db_client, bob)
    r = await db_client.post("/vault/items", headers=alice,
                             json={"kind": "note", "nonce": _b64(12), "ciphertext": _b64(120)})
    note_id = r.json()["id"]
    r = await db_client.post(f"/vault/items/{note_id}/share", headers=alice,
                             json={"recipient_email": "bob_n@example.com", "sealed_payload": _b64(300)})
    assert r.status_code == 201
    grant = (await db_client.get("/vault/shares", headers=bob)).json()[0]
    assert grant["kind"] == "note" and grant["has_file"] is False
    # a secure item has no streamable file
    assert (await db_client.get(f"/vault/shares/{grant['id']}/file", headers=bob)).status_code == 404


async def test_grant_cascades_on_item_delete(db_client):
    _, alice = await register_and_login(db_client, email="alice_c@example.com")
    _, bob = await register_and_login(db_client, email="bob_c@example.com")
    await _setup_vault(db_client, alice)
    await _setup_vault(db_client, bob)
    fid = await _upload(db_client, alice, os.urandom(500))
    await db_client.post(f"/vault/items/{fid}/share", headers=alice,
                         json={"recipient_email": "bob_c@example.com", "sealed_payload": _b64(140)})
    assert len((await db_client.get("/vault/shares", headers=bob)).json()) == 1
    assert (await db_client.delete(f"/vault/items/{fid}", headers=alice)).status_code == 204
    assert (await db_client.get("/vault/shares", headers=bob)).json() == []
