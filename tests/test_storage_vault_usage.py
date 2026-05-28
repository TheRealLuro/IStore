"""The storage-usage bar must count the E2E Vault, not just the Drive.

Regression guard for VLT-8: /storage/usage previously summed only image
bytes, so the "storage used" bar under-reported once a user had files in the
encrypted Vault. These assert the Vault file blobs flow into both the grand
total (`used_bytes`) and the new `vault_bytes` component, while tiny secure
items (notes/passwords) stay unmetered.
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


async def test_usage_counts_vault_files(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)

    u0 = (await db_client.get("/storage/usage", headers=h)).json()
    assert u0["vault_bytes"] == 0 and u0["vault_count"] == 0
    base_used = u0["used_bytes"]

    payload = os.urandom(25000)
    r = await db_client.post(
        "/vault/files", headers=h,
        files={"blob": ("f.bin", payload, "application/octet-stream")},
        data={"nonce": _b64(12), "ciphertext": _b64(80), "wrapped_key": _b64(126)},
    )
    assert r.status_code == 201

    u1 = (await db_client.get("/storage/usage", headers=h)).json()
    assert u1["vault_bytes"] == len(payload)
    assert u1["vault_count"] == 1
    assert u1["used_bytes"] == base_used + len(payload)


async def test_secure_items_are_not_metered(db_client):
    _, h = await register_and_login(db_client)
    await _setup_vault(db_client, h)
    await db_client.post("/vault/items", headers=h, json={
        "kind": "note", "nonce": _b64(12), "ciphertext": _b64(200)})
    u = (await db_client.get("/storage/usage", headers=h)).json()
    # A secure note's tiny inline ciphertext doesn't consume metered storage.
    assert u["vault_bytes"] == 0 and u["vault_count"] == 0
