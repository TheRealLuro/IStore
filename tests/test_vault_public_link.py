"""VLT-8 P7 public-link tests: zero-knowledge anyone-with-link sharing.

Sealed payloads are opaque to the server (real link-key crypto is validated in
the frontend Node harness), so these assert the routing + the trust boundary:
  - the /public/* read endpoints work with NO authentication, gated only by
    the high-entropy token, and return the sealed blob byte-for-byte,
  - password links carry public KDF params; creating without them is rejected,
  - re-creating rotates the token (old link dies),
  - expiry is enforced; expiry bounds are validated,
  - only the owner can manage a link; deleting the item cascades it.
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
    return r.json()["id"]


async def test_public_link_unauthenticated_view_and_stream(db_client):
    _, alice = await register_and_login(db_client, email="al_pl@example.com")
    await _setup_vault(db_client, alice)
    payload = os.urandom(9000)
    fid = await _upload(db_client, alice, payload)

    sealed = _b64(120)
    r = await db_client.post(f"/vault/items/{fid}/public-link", headers=alice,
                             json={"sealed_payload": sealed, "password_required": False})
    assert r.status_code == 201
    token = r.json()["token"]
    assert token and len(token) > 20

    # No auth header — anyone with the link.
    r = await db_client.get(f"/vault/public/{token}")
    assert r.status_code == 200
    v = r.json()
    assert v["sealed_payload"] == sealed  # byte-for-byte
    assert v["has_file"] is True and v["size_bytes"] == len(payload)
    assert v["password_required"] is False and v["kdf_salt"] is None

    r = await db_client.get(f"/vault/public/{token}/file")
    assert r.status_code == 200 and r.content == payload
    r = await db_client.get(f"/vault/public/{token}/file", headers={"Range": "bytes=2-11"})
    assert r.status_code == 206 and r.content == payload[2:12]


async def test_password_link_carries_kdf_and_requires_it(db_client):
    _, alice = await register_and_login(db_client, email="al_pw@example.com")
    await _setup_vault(db_client, alice)
    fid = await _upload(db_client, alice, os.urandom(500))

    salt = _b64(16)
    r = await db_client.post(f"/vault/items/{fid}/public-link", headers=alice, json={
        "sealed_payload": _b64(140), "password_required": True,
        "kdf_salt": salt, "kdf_iterations": 600000})
    assert r.status_code == 201
    v = (await db_client.get(f"/vault/public/{r.json()['token']}")).json()
    assert v["password_required"] is True
    assert v["kdf_salt"] == salt and v["kdf_iterations"] == 600000

    # password link without salt -> 422
    r = await db_client.post(f"/vault/items/{fid}/public-link", headers=alice, json={
        "sealed_payload": _b64(140), "password_required": True})
    assert r.status_code == 422


async def test_recreate_rotates_token(db_client):
    _, alice = await register_and_login(db_client, email="al_rot@example.com")
    await _setup_vault(db_client, alice)
    fid = await _upload(db_client, alice, os.urandom(400))
    t1 = (await db_client.post(f"/vault/items/{fid}/public-link", headers=alice,
          json={"sealed_payload": _b64(120), "password_required": False})).json()["token"]
    t2 = (await db_client.post(f"/vault/items/{fid}/public-link", headers=alice,
          json={"sealed_payload": _b64(120), "password_required": False})).json()["token"]
    assert t1 != t2
    assert (await db_client.get(f"/vault/public/{t1}")).status_code == 404
    assert (await db_client.get(f"/vault/public/{t2}")).status_code == 200


async def test_expiry_validation_and_enforcement(db_client):
    from sqlalchemy import text
    from backend.db import engine

    _, alice = await register_and_login(db_client, email="al_exp@example.com")
    await _setup_vault(db_client, alice)
    fid = await _upload(db_client, alice, os.urandom(400))

    r = await db_client.post(f"/vault/items/{fid}/public-link", headers=alice, json={
        "sealed_payload": _b64(120), "password_required": False, "expires_in_days": 7})
    assert r.status_code == 201 and r.json()["expires_at"]
    token = r.json()["token"]
    # out-of-range expiry rejected
    assert (await db_client.post(f"/vault/items/{fid}/public-link", headers=alice, json={
        "sealed_payload": _b64(120), "password_required": False, "expires_in_days": 0})).status_code == 422

    async with engine.begin() as c:
        await c.execute(text("UPDATE vault_public_links SET expires_at = now() - interval '1 hour' WHERE token = :t"), {"t": token})
    # (recreate to restore the row the previous expiry overwrote? the 0-days
    # attempt 422'd so the 7-day link is still the live one and now expired)
    assert (await db_client.get(f"/vault/public/{token}")).status_code == 404


async def test_owner_only_and_cascade(db_client):
    _, alice = await register_and_login(db_client, email="al_own@example.com")
    _, bob = await register_and_login(db_client, email="bo_own@example.com")
    await _setup_vault(db_client, alice)
    await _setup_vault(db_client, bob)
    fid = await _upload(db_client, alice, os.urandom(400))
    r = await db_client.post(f"/vault/items/{fid}/public-link", headers=alice,
                             json={"sealed_payload": _b64(120), "password_required": False})
    token = r.json()["token"]

    # bob can't touch alice's link
    assert (await db_client.post(f"/vault/items/{fid}/public-link", headers=bob,
            json={"sealed_payload": _b64(120), "password_required": False})).status_code == 404
    assert (await db_client.get(f"/vault/items/{fid}/public-link", headers=bob)).status_code == 404
    assert (await db_client.delete(f"/vault/items/{fid}/public-link", headers=bob)).status_code == 404

    # revoke works, then 404
    assert (await db_client.delete(f"/vault/items/{fid}/public-link", headers=alice)).status_code == 204
    assert (await db_client.get(f"/vault/public/{token}")).status_code == 404

    # re-create then delete the item -> link cascades
    token2 = (await db_client.post(f"/vault/items/{fid}/public-link", headers=alice,
              json={"sealed_payload": _b64(120), "password_required": False})).json()["token"]
    assert (await db_client.delete(f"/vault/items/{fid}", headers=alice)).status_code == 204
    assert (await db_client.get(f"/vault/public/{token2}")).status_code == 404


async def test_unknown_token_404(db_client):
    r = await db_client.get("/vault/public/totally-not-a-real-token-xyz")
    assert r.status_code == 404
