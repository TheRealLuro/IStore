"""Zero-knowledge vault API — ciphertext-only CRUD (VLT-4).

This router is deliberately DUMB. Every payload it accepts or returns
is opaque ciphertext (base64) that the client encrypted/decrypted with
a key derived from a master password the server never sees. The server:

  * validates that binary fields are well-formed base64 of bounded
    length (so a client can't smuggle a 100 MB "ciphertext" or a
    malformed nonce),
  * stores / returns those bytes,
  * NEVER decrypts, NEVER sees the master password or the vault key,
  * NEVER logs ciphertext / salt / verifier material.

See migration 0044 for the full crypto contract. The matching client
crypto lives in the frontend (VLT-5): PBKDF2-SHA256 → AES-256-GCM.

Endpoints:
  GET    /vault/meta            — KDF params + salt + verifier, or 404.
  POST   /vault/setup           — create the vault (first time only).
  GET    /vault/items           — all items (kind + nonce + ciphertext).
  POST   /vault/items           — create one item.
  PUT    /vault/items/{id}      — replace one item's nonce + ciphertext.
  DELETE /vault/items/{id}      — delete one item.
  DELETE /vault                 — wipe the vault (meta + all items).

RLS fences every row to the owner; the queries also filter on user_id
as defence in depth. Writes are rate-limited.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import User, VaultItem, VaultMeta
from backend.security import enforce_rate_limit

router = APIRouter(prefix="/vault", tags=["vault"])


# ---- bounds (bytes, post-base64-decode) ------------------------------------
#
# A vault item plaintext is a tiny JSON blob ({title, username,
# password, url, notes}); even a long secure note is well under 64 KB.
# Cap the CIPHERTEXT generously at 256 KB so a pathological client
# can't park large blobs in the vault, while never rejecting a real
# entry. AES-GCM nonce is always 12 bytes; the salt 16; the verifier
# ciphertext is small (encrypts a 32-byte constant + 16-byte tag).
_MAX_CIPHERTEXT_BYTES = 256 * 1024
_NONCE_BYTES = 12
_MIN_SALT_BYTES = 16
_MAX_SALT_BYTES = 64
_MAX_VERIFIER_CT_BYTES = 256
# OWASP 2023 floor for PBKDF2-SHA256 is 600k. Enforce a floor so a
# buggy/hostile client can't silently weaken a user's KDF, and a
# ceiling so it can't wedge the user's browser with an absurd cost.
_MIN_KDF_ITER = 310_000
_MAX_KDF_ITER = 5_000_000


def _b64_field(value: str, *, min_bytes: int, max_bytes: int,
               exact: int | None = None, field: str) -> bytes:
    """Decode a strict-base64 string and bound its byte length. Raises
    422 on malformed base64 or out-of-range size. Returns the raw bytes
    for storage. Never logs the value."""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{field} is not valid base64.",
        )
    n = len(raw)
    if exact is not None and n != exact:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{field} must be exactly {exact} bytes.",
        )
    if n < min_bytes or n > max_bytes:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{field} length out of range.",
        )
    return raw


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


# ---- schemas ---------------------------------------------------------------


class VaultSetupRequest(BaseModel):
    """Client-generated KDF params + verifier. All opaque to the server."""
    kdf: Literal["PBKDF2-SHA256"] = "PBKDF2-SHA256"
    kdf_iterations: int
    kdf_salt: str          # base64, 16–64 bytes
    verifier_nonce: str    # base64, exactly 12 bytes
    verifier_ct: str       # base64, <= 256 bytes

    @field_validator("kdf_iterations")
    @classmethod
    def _iter_range(cls, v: int) -> int:
        if v < _MIN_KDF_ITER or v > _MAX_KDF_ITER:
            raise ValueError(
                f"kdf_iterations must be between {_MIN_KDF_ITER} and {_MAX_KDF_ITER}"
            )
        return v


class VaultMetaResponse(BaseModel):
    kdf: str
    kdf_iterations: int
    kdf_salt: str
    verifier_nonce: str
    verifier_ct: str


class VaultItemUpsert(BaseModel):
    kind: Literal["password", "note"]
    nonce: str          # base64, exactly 12 bytes
    ciphertext: str     # base64, <= 256 KB


class VaultItemUpdate(BaseModel):
    """Update keeps the same kind; only the encrypted payload changes."""
    nonce: str
    ciphertext: str


class VaultItemResponse(BaseModel):
    id: UUID
    kind: str
    nonce: str
    ciphertext: str
    created_at: datetime
    updated_at: datetime


# ---- endpoints -------------------------------------------------------------


@router.get("/meta", response_model=VaultMetaResponse)
async def get_vault_meta(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultMetaResponse:
    """Return the KDF params + salt + verifier so the client can derive
    the vault key and confirm the master password on unlock. 404 when
    the user hasn't set up a vault yet (the FE shows the setup flow)."""
    meta = await session.get(VaultMeta, user.id)
    if meta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No vault set up.")
    return VaultMetaResponse(
        kdf=meta.kdf,
        kdf_iterations=meta.kdf_iterations,
        kdf_salt=_b64(meta.kdf_salt),
        verifier_nonce=_b64(meta.verifier_nonce),
        verifier_ct=_b64(meta.verifier_ct),
    )


@router.post("/setup", status_code=status.HTTP_201_CREATED,
             response_model=VaultMetaResponse)
async def setup_vault(
    payload: VaultSetupRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultMetaResponse:
    """Create the vault for the first time. Refuses (409) if one already
    exists — overwriting the KDF params / verifier would orphan every
    existing item (they were encrypted under the old key). Changing the
    master password is a separate re-key flow (client re-encrypts all
    items, then replaces meta) — not this endpoint."""
    await enforce_rate_limit(
        key=f"vault:setup:{user.id}",
        limit=5, window_seconds=3600,
        detail="Too many vault setup attempts. Try again later.",
    )
    existing = await session.get(VaultMeta, user.id)
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A vault already exists. Use the change-master-password flow "
            "to re-key it.",
        )
    salt = _b64_field(payload.kdf_salt, min_bytes=_MIN_SALT_BYTES,
                      max_bytes=_MAX_SALT_BYTES, field="kdf_salt")
    v_nonce = _b64_field(payload.verifier_nonce, min_bytes=_NONCE_BYTES,
                         max_bytes=_NONCE_BYTES, exact=_NONCE_BYTES,
                         field="verifier_nonce")
    v_ct = _b64_field(payload.verifier_ct, min_bytes=1,
                      max_bytes=_MAX_VERIFIER_CT_BYTES, field="verifier_ct")
    meta = VaultMeta(
        user_id=user.id,
        kdf=payload.kdf,
        kdf_iterations=payload.kdf_iterations,
        kdf_salt=salt,
        verifier_nonce=v_nonce,
        verifier_ct=v_ct,
    )
    session.add(meta)
    await session.commit()
    return VaultMetaResponse(
        kdf=meta.kdf,
        kdf_iterations=meta.kdf_iterations,
        kdf_salt=_b64(meta.kdf_salt),
        verifier_nonce=_b64(meta.verifier_nonce),
        verifier_ct=_b64(meta.verifier_ct),
    )


@router.get("/items", response_model=list[VaultItemResponse])
async def list_vault_items(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[VaultItemResponse]:
    """Return every encrypted item for the user. The client decrypts +
    searches them locally — the server has no plaintext to filter on."""
    rows = (
        await session.execute(
            select(VaultItem)
            .where(VaultItem.user_id == user.id)
            .order_by(VaultItem.created_at.asc())
        )
    ).scalars().all()
    return [
        VaultItemResponse(
            id=r.id, kind=r.kind,
            nonce=_b64(r.nonce), ciphertext=_b64(r.ciphertext),
            created_at=r.created_at, updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("/items", status_code=status.HTTP_201_CREATED,
             response_model=VaultItemResponse)
async def create_vault_item(
    payload: VaultItemUpsert,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultItemResponse:
    await enforce_rate_limit(
        key=f"vault:write:{user.id}",
        limit=300, window_seconds=3600,
        detail="Too many vault writes. Try again shortly.",
    )
    # Refuse items before the vault is set up — otherwise they'd be
    # encrypted under a key with no recorded KDF params to re-derive.
    if await session.get(VaultMeta, user.id) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Set up your vault (master password) before adding items.",
        )
    nonce = _b64_field(payload.nonce, min_bytes=_NONCE_BYTES,
                       max_bytes=_NONCE_BYTES, exact=_NONCE_BYTES, field="nonce")
    ct = _b64_field(payload.ciphertext, min_bytes=1,
                    max_bytes=_MAX_CIPHERTEXT_BYTES, field="ciphertext")
    item = VaultItem(
        user_id=user.id, kind=payload.kind, nonce=nonce, ciphertext=ct,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return VaultItemResponse(
        id=item.id, kind=item.kind,
        nonce=_b64(item.nonce), ciphertext=_b64(item.ciphertext),
        created_at=item.created_at, updated_at=item.updated_at,
    )


@router.put("/items/{item_id}", response_model=VaultItemResponse)
async def update_vault_item(
    item_id: UUID,
    payload: VaultItemUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultItemResponse:
    await enforce_rate_limit(
        key=f"vault:write:{user.id}",
        limit=300, window_seconds=3600,
        detail="Too many vault writes. Try again shortly.",
    )
    item = await session.get(VaultItem, item_id)
    # RLS already fences by user, but check explicitly so a wrong-owner
    # id returns 404 (not 200 with someone else's row, and not 500).
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vault item not found.")
    nonce = _b64_field(payload.nonce, min_bytes=_NONCE_BYTES,
                       max_bytes=_NONCE_BYTES, exact=_NONCE_BYTES, field="nonce")
    ct = _b64_field(payload.ciphertext, min_bytes=1,
                    max_bytes=_MAX_CIPHERTEXT_BYTES, field="ciphertext")
    item.nonce = nonce
    item.ciphertext = ct
    item.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(item)
    return VaultItemResponse(
        id=item.id, kind=item.kind,
        nonce=_b64(item.nonce), ciphertext=_b64(item.ciphertext),
        created_at=item.created_at, updated_at=item.updated_at,
    )


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vault_item(
    item_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    item = await session.get(VaultItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vault item not found.")
    await session.delete(item)
    await session.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def wipe_vault(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Destroy the entire vault — every item AND the meta. Used by the
    "forgot master password / reset vault" path: there is no recovery
    of the contents (that's the point of zero-knowledge), so this lets
    the user start over with a fresh master password. Irreversible."""
    await enforce_rate_limit(
        key=f"vault:wipe:{user.id}",
        limit=5, window_seconds=3600,
        detail="Too many vault resets. Try again later.",
    )
    await session.execute(
        sa_delete(VaultItem).where(VaultItem.user_id == user.id)
    )
    await session.execute(
        sa_delete(VaultMeta).where(VaultMeta.user_id == user.id)
    )
    await session.commit()
