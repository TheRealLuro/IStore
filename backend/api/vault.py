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
  GET    /vault/meta            — KDF params + salt + verifier (+ account
                                  public/wrapped-private key), or 404.
  POST   /vault/setup           — create the vault (first time only).
  POST   /vault/account-key     — provision the account keypair (legacy vault).
  GET    /vault/items           — all items (incl. file metadata + wrapped key).
  POST   /vault/items           — create one small item (password/note/seed/card).
  PUT    /vault/items/{id}      — replace one item's nonce + ciphertext.
  DELETE /vault/items/{id}      — delete one item (frees its blob too).
  GET    /vault/folders         — all folders (flat; client builds the tree).
  POST   /vault/folders         — create a folder.
  PUT    /vault/folders/{id}    — rename / move a folder (no-cycle enforced).
  DELETE /vault/folders/{id}    — delete a folder + its whole subtree + blobs.
  POST   /vault/files           — upload an E2E-encrypted file (ciphertext → MinIO).
  GET    /vault/files/{id}      — stream a file's ciphertext (HTTP Range ok).
  GET    /vault/recipient-key   — a recipient's account public key, by email.
  POST   /vault/items/{id}/share— grant a recipient a sealed bundle for an item.
  GET    /vault/items/{id}/shares— who an item is shared with (owner view).
  GET    /vault/shares          — items shared WITH me (recipient view).
  DELETE /vault/shares/{id}     — revoke / decline a share (either party).
  GET    /vault/shares/{id}/file— stream a shared file's ciphertext (recipient).
  POST   /vault/items/{id}/public-link — create/replace an anyone-with-link share.
  GET    /vault/items/{id}/public-link — the item's current public link (owner).
  DELETE /vault/items/{id}/public-link — revoke the public link.
  GET    /vault/public/{token}        — UNAUTH: sealed bundle + KDF params.
  GET    /vault/public/{token}/file   — UNAUTH: stream a public file's ciphertext.
  DELETE /vault                 — wipe the vault (meta + items + folders + blobs).

Public links keep the zero-knowledge property: the decryption key rides in the
URL fragment (never sent to the server); an optional password is mixed into the
key derivation client-side. The /public/* read endpoints are unauthenticated,
gated only by the high-entropy token, and the row carries only ciphertext.

Vault shares carry NO comments and no public links — a share is a direct,
revocable, read-only grant to a named neuthek account, sealed to its public
key. The server stores only an opaque sealed blob plus who→whom routing.

Vault files are end-to-end encrypted: the client encrypts the bytes under a
random per-file key (chunked AES-GCM), seals that key to its own account
public key (`wrapped_key`), and uploads only ciphertext. The server streams
the ciphertext to a dedicated MinIO bucket and NEVER sees the key or plaintext.

RLS fences every row to the owner; the queries also filter on user_id
as defence in depth. Writes are rate-limited.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import delete as sa_delete, func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.storage import DEFAULT_QUOTA_BYTES
from backend.auth.users import current_active_user
from backend.config import settings
from backend.db import get_session
from backend.models import (
    Image,
    User,
    VaultFolder,
    VaultItem,
    VaultMeta,
    VaultPublicLink,
    VaultShareGrant,
)
from backend.security import enforce_rate_limit
from backend.storage import storage

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
# VLT-8 account keypair. The public key is a P-256 raw uncompressed EC
# point — always exactly 65 bytes (0x04 ‖ X[32] ‖ Y[32]). The private key
# is stored AES-GCM-wrapped: nonce(12) ‖ ct(pkcs8 ≈138) ‖ tag(16) ≈ 166
# bytes; cap generously so a hostile client can't park a large blob here.
_ACCOUNT_PUBKEY_BYTES = 65
_MAX_ENC_PRIVKEY_BYTES = 512
# Folder names are short encrypted JSON ({name}); 8 KB is plenty and bounds
# abuse. The per-file key is sealed to the owner's account public key —
# sealToPublicKey of a 32-byte key is ~126 bytes; cap at 256.
_MAX_FOLDER_NAME_CT_BYTES = 8 * 1024
_MAX_WRAPPED_KEY_BYTES = 256
# A share's sealed bundle: for a file it's tiny (a 32-byte key + small
# metadata); for a secure item it's the item plaintext (a note can be tens of
# KB). Cap like an item's ciphertext so a hostile client can't park a big blob.
_MAX_SEALED_PAYLOAD_BYTES = 256 * 1024
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


def _decode_account_key(public_key_b64: str, enc_private_key_b64: str) -> tuple[bytes, bytes]:
    """Validate + decode the account keypair fields. The public key must be
    a 65-byte P-256 raw point; the wrapped private key is length-bounded.
    Server never inspects the bytes beyond size — both are opaque."""
    pub = _b64_field(
        public_key_b64, min_bytes=_ACCOUNT_PUBKEY_BYTES,
        max_bytes=_ACCOUNT_PUBKEY_BYTES, exact=_ACCOUNT_PUBKEY_BYTES,
        field="account_public_key",
    )
    enc_priv = _b64_field(
        enc_private_key_b64, min_bytes=_NONCE_BYTES + 1,
        max_bytes=_MAX_ENC_PRIVKEY_BYTES, field="enc_account_private_key",
    )
    return pub, enc_priv


def _meta_response(meta: VaultMeta) -> "VaultMetaResponse":
    return VaultMetaResponse(
        kdf=meta.kdf,
        kdf_iterations=meta.kdf_iterations,
        kdf_salt=_b64(meta.kdf_salt),
        verifier_nonce=_b64(meta.verifier_nonce),
        verifier_ct=_b64(meta.verifier_ct),
        account_public_key=_b64(meta.account_public_key) if meta.account_public_key else None,
        enc_account_private_key=(
            _b64(meta.enc_account_private_key) if meta.enc_account_private_key else None
        ),
    )


def _item_response(item: VaultItem) -> "VaultItemResponse":
    return VaultItemResponse(
        id=item.id,
        kind=item.kind,
        nonce=_b64(item.nonce),
        ciphertext=_b64(item.ciphertext),
        folder_id=item.folder_id,
        wrapped_key=_b64(item.wrapped_key) if item.wrapped_key else None,
        size_bytes=item.size_bytes,
        has_file=item.storage_key is not None,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _folder_response(f: VaultFolder) -> "VaultFolderResponse":
    return VaultFolderResponse(
        id=f.id,
        parent_id=f.parent_id,
        name_nonce=_b64(f.name_nonce),
        name_ct=_b64(f.name_ct),
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


def _effective_quota(user: User) -> int:
    qb = getattr(user, "quota_bytes", None)
    return int(qb) if qb is not None else DEFAULT_QUOTA_BYTES


async def _used_bytes(session: AsyncSession, user_id) -> int:
    """Total storage the user occupies across BOTH the AI drive (image
    originals/served) and the zero-knowledge vault — they share one quota
    pool, so vault uploads can't be used to dodge the account limit."""
    img = await session.scalar(
        select(func.coalesce(func.sum(Image.byte_size_served), 0)).where(
            Image.user_id == user_id, Image.deleted_at.is_(None)
        )
    )
    vault = await session.scalar(
        select(func.coalesce(func.sum(VaultItem.size_bytes), 0)).where(
            VaultItem.user_id == user_id
        )
    )
    return int(img or 0) + int(vault or 0)


async def _mirror_public_key(session: AsyncSession, user_id, pub: bytes) -> None:
    """Mirror the vault account public key onto the (cross-readable) users row
    so other accounts can fetch it to seal a shared item. vault_meta itself is
    FORCE-RLS to its owner and can't be read cross-user."""
    await session.execute(
        sa_update(User).where(User.id == user_id).values(vault_public_key=pub)
    )


async def _folder_or_404(session: AsyncSession, user_id, folder_id: UUID) -> VaultFolder:
    """Fetch a folder, enforcing ownership. RLS already fences by user; the
    explicit check turns a wrong-owner id into a clean 404 (not a 200 on
    someone else's row, nor a 500)."""
    folder = await session.get(VaultFolder, folder_id)
    if folder is None or folder.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Folder not found.")
    return folder


# ---- schemas ---------------------------------------------------------------


class VaultSetupRequest(BaseModel):
    """Client-generated KDF params + verifier. All opaque to the server."""
    kdf: Literal["PBKDF2-SHA256"] = "PBKDF2-SHA256"
    kdf_iterations: int
    kdf_salt: str          # base64, 16–64 bytes
    verifier_nonce: str    # base64, exactly 12 bytes
    verifier_ct: str       # base64, <= 256 bytes
    # VLT-8 — optional account keypair, supplied at first setup so sharing
    # works out of the box. Older vaults provision it later via
    # POST /vault/account-key. Both opaque to the server.
    account_public_key: str | None = None   # base64, exactly 65 bytes
    enc_account_private_key: str | None = None  # base64, <= 512 bytes

    @field_validator("kdf_iterations")
    @classmethod
    def _iter_range(cls, v: int) -> int:
        if v < _MIN_KDF_ITER or v > _MAX_KDF_ITER:
            raise ValueError(
                f"kdf_iterations must be between {_MIN_KDF_ITER} and {_MAX_KDF_ITER}"
            )
        return v


class VaultAccountKeyRequest(BaseModel):
    """Provision the account keypair for an existing vault that predates
    VLT-8 (or any vault whose keypair wasn't set at setup time)."""
    account_public_key: str          # base64, exactly 65 bytes
    enc_account_private_key: str     # base64, <= 512 bytes


class VaultMetaResponse(BaseModel):
    kdf: str
    kdf_iterations: int
    kdf_salt: str
    verifier_nonce: str
    verifier_ct: str
    # base64, present once the account keypair has been provisioned. The
    # private key is master-key-wrapped — the server never sees it unwrapped.
    account_public_key: str | None = None
    enc_account_private_key: str | None = None


# Small secure items the client encrypts wholesale into `ciphertext`. File
# items are NOT creatable here — they go through the multipart /vault/files
# endpoint (they carry an object-storage blob + wrapped key).
SmallItemKind = Literal["password", "note", "seed", "card"]


class VaultItemUpsert(BaseModel):
    kind: SmallItemKind
    nonce: str          # base64, exactly 12 bytes
    ciphertext: str     # base64, <= 256 KB
    folder_id: UUID | None = None  # null = vault root


class VaultItemUpdate(BaseModel):
    """Update keeps the same kind; only the encrypted payload changes."""
    nonce: str
    ciphertext: str


class VaultItemResponse(BaseModel):
    id: UUID
    kind: str
    nonce: str
    ciphertext: str
    folder_id: UUID | None = None
    # File items only: the per-file key sealed to the owner's account public
    # key (base64), the ciphertext byte size, and a flag the FE uses to know
    # it must fetch the blob from /vault/files/{id} to decrypt.
    wrapped_key: str | None = None
    size_bytes: int | None = None
    has_file: bool = False
    created_at: datetime
    updated_at: datetime


# ---- folder schemas --------------------------------------------------------


class VaultFolderCreate(BaseModel):
    parent_id: UUID | None = None
    name_nonce: str     # base64, exactly 12 bytes
    name_ct: str        # base64, <= 8 KB


class VaultFolderUpdate(BaseModel):
    """Rename and/or move. `parent_id` is applied as given (null = move to
    root); the client always sends the intended parent."""
    parent_id: UUID | None = None
    name_nonce: str
    name_ct: str


class VaultFolderResponse(BaseModel):
    id: UUID
    parent_id: UUID | None
    name_nonce: str
    name_ct: str
    created_at: datetime
    updated_at: datetime


# ---- share schemas (VLT-8 P5) ---------------------------------------------


class VaultRecipientKeyResponse(BaseModel):
    email: str
    display_name: str | None = None
    account_public_key: str  # base64 — used by the sharer to seal the bundle


class VaultShareCreate(BaseModel):
    recipient_email: str
    sealed_payload: str  # base64 — bundle sealed to the recipient's public key


class VaultShareRecipientResponse(BaseModel):
    """For the OWNER's "who is this shared with" list."""
    id: UUID
    recipient_email: str
    recipient_display_name: str | None = None
    created_at: datetime


class VaultIncomingShareResponse(BaseModel):
    """For the RECIPIENT's "shared with me" list. The recipient unseals
    `sealed_payload` client-side to recover the key/plaintext."""
    id: UUID
    kind: str
    size_bytes: int | None = None
    has_file: bool = False
    sealed_payload: str  # base64
    owner_email: str | None = None
    owner_display_name: str | None = None
    created_at: datetime


# ---- public link schemas (VLT-8 P7) ---------------------------------------


class VaultPublicLinkCreate(BaseModel):
    sealed_payload: str  # base64 — bundle sealed under the link key
    password_required: bool = False
    kdf_salt: str | None = None        # base64, present iff password_required
    kdf_iterations: int | None = None  # present iff password_required
    expires_in_days: int | None = None  # null = never


class VaultPublicLinkResponse(BaseModel):
    """Owner view: enough to build + display the link. The fragment secret is
    NOT here — it never leaves the creator's browser."""
    token: str
    password_required: bool
    expires_at: datetime | None = None
    created_at: datetime


class VaultPublicLinkView(BaseModel):
    """Unauthenticated viewer payload. The visitor derives the link key from
    the URL fragment (+ password) and decrypts `sealed_payload` locally."""
    kind: str
    has_file: bool = False
    size_bytes: int | None = None
    sealed_payload: str  # base64
    password_required: bool
    kdf_salt: str | None = None      # base64
    kdf_iterations: int | None = None


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
    return _meta_response(meta)


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
    # Account keypair is optional at setup but must be supplied as a pair.
    acct_pub: bytes | None = None
    acct_priv: bytes | None = None
    if payload.account_public_key is not None or payload.enc_account_private_key is not None:
        if payload.account_public_key is None or payload.enc_account_private_key is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "account_public_key and enc_account_private_key must be supplied together.",
            )
        acct_pub, acct_priv = _decode_account_key(
            payload.account_public_key, payload.enc_account_private_key,
        )
    meta = VaultMeta(
        user_id=user.id,
        kdf=payload.kdf,
        kdf_iterations=payload.kdf_iterations,
        kdf_salt=salt,
        verifier_nonce=v_nonce,
        verifier_ct=v_ct,
        account_public_key=acct_pub,
        enc_account_private_key=acct_priv,
    )
    session.add(meta)
    if acct_pub is not None:
        await _mirror_public_key(session, user.id, acct_pub)
    await session.commit()
    return _meta_response(meta)


@router.post("/account-key", response_model=VaultMetaResponse)
async def set_account_key(
    payload: VaultAccountKeyRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultMetaResponse:
    """Provision the account keypair for an existing vault that doesn't have
    one yet (vaults created before VLT-8). Idempotent-by-refusal: once set,
    the keypair can't be overwritten here — rotating it would orphan every
    file-key sealed to the old public key (and every share). The vault must
    already exist."""
    await enforce_rate_limit(
        key=f"vault:account-key:{user.id}",
        limit=5, window_seconds=3600,
        detail="Too many account-key attempts. Try again later.",
    )
    meta = await session.get(VaultMeta, user.id)
    if meta is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No vault set up. Create the vault first.",
        )
    if meta.account_public_key is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An account key already exists for this vault.",
        )
    pub, enc_priv = _decode_account_key(
        payload.account_public_key, payload.enc_account_private_key,
    )
    meta.account_public_key = pub
    meta.enc_account_private_key = enc_priv
    meta.updated_at = datetime.now(timezone.utc)
    await _mirror_public_key(session, user.id, pub)
    await session.commit()
    await session.refresh(meta)
    return _meta_response(meta)


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
    return [_item_response(r) for r in rows]


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
    if payload.folder_id is not None:
        await _folder_or_404(session, user.id, payload.folder_id)
    item = VaultItem(
        user_id=user.id, kind=payload.kind, nonce=nonce, ciphertext=ct,
        folder_id=payload.folder_id,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return _item_response(item)


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
    return _item_response(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vault_item(
    item_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    item = await session.get(VaultItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vault item not found.")
    # Free the object-storage blob first for file items. Best-effort: if the
    # object is already gone, storage.delete swallows it; the row goes either
    # way so we never leave a dangling DB pointer.
    storage_key = item.storage_key
    await session.delete(item)
    await session.commit()
    if storage_key:
        storage.delete(storage.bucket_vault, storage_key)


# ---- folders ---------------------------------------------------------------


async def _assert_no_cycle(session: AsyncSession, user_id, moving_id: UUID,
                           new_parent_id: UUID) -> None:
    """Reject a move that would put a folder inside its own subtree. Walks
    the ancestor chain of the proposed new parent; if it reaches the folder
    being moved, the move would create a cycle."""
    rows = (
        await session.execute(
            select(VaultFolder.id, VaultFolder.parent_id).where(
                VaultFolder.user_id == user_id
            )
        )
    ).all()
    parent_of = {fid: pid for fid, pid in rows}
    cur = new_parent_id
    seen: set = set()
    while cur is not None:
        if cur == moving_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot move a folder into itself or its own descendant.",
            )
        if cur in seen:  # defensive: never loop on a pre-existing cycle
            break
        seen.add(cur)
        cur = parent_of.get(cur)


@router.get("/folders", response_model=list[VaultFolderResponse])
async def list_vault_folders(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[VaultFolderResponse]:
    """Every folder for the user (flat). The client builds the tree and
    decrypts the names locally — the server can't read them."""
    rows = (
        await session.execute(
            select(VaultFolder)
            .where(VaultFolder.user_id == user.id)
            .order_by(VaultFolder.created_at.asc())
        )
    ).scalars().all()
    return [_folder_response(f) for f in rows]


@router.post("/folders", status_code=status.HTTP_201_CREATED,
             response_model=VaultFolderResponse)
async def create_vault_folder(
    payload: VaultFolderCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultFolderResponse:
    await enforce_rate_limit(
        key=f"vault:write:{user.id}",
        limit=300, window_seconds=3600,
        detail="Too many vault writes. Try again shortly.",
    )
    if await session.get(VaultMeta, user.id) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Set up your vault (master password) before adding folders.",
        )
    if payload.parent_id is not None:
        await _folder_or_404(session, user.id, payload.parent_id)
    name_nonce = _b64_field(payload.name_nonce, min_bytes=_NONCE_BYTES,
                            max_bytes=_NONCE_BYTES, exact=_NONCE_BYTES,
                            field="name_nonce")
    name_ct = _b64_field(payload.name_ct, min_bytes=1,
                         max_bytes=_MAX_FOLDER_NAME_CT_BYTES, field="name_ct")
    folder = VaultFolder(
        user_id=user.id, parent_id=payload.parent_id,
        name_nonce=name_nonce, name_ct=name_ct,
    )
    session.add(folder)
    await session.commit()
    await session.refresh(folder)
    return _folder_response(folder)


@router.put("/folders/{folder_id}", response_model=VaultFolderResponse)
async def update_vault_folder(
    folder_id: UUID,
    payload: VaultFolderUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultFolderResponse:
    """Rename and/or move a folder. `parent_id` is applied as given (null =
    vault root). A move into the folder's own subtree is rejected."""
    await enforce_rate_limit(
        key=f"vault:write:{user.id}",
        limit=300, window_seconds=3600,
        detail="Too many vault writes. Try again shortly.",
    )
    folder = await _folder_or_404(session, user.id, folder_id)
    if payload.parent_id is not None:
        if payload.parent_id == folder.id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Cannot move a folder into itself.",
            )
        await _folder_or_404(session, user.id, payload.parent_id)
        await _assert_no_cycle(session, user.id, folder.id, payload.parent_id)
    name_nonce = _b64_field(payload.name_nonce, min_bytes=_NONCE_BYTES,
                            max_bytes=_NONCE_BYTES, exact=_NONCE_BYTES,
                            field="name_nonce")
    name_ct = _b64_field(payload.name_ct, min_bytes=1,
                         max_bytes=_MAX_FOLDER_NAME_CT_BYTES, field="name_ct")
    folder.parent_id = payload.parent_id
    folder.name_nonce = name_nonce
    folder.name_ct = name_ct
    folder.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(folder)
    return _folder_response(folder)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vault_folder(
    folder_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Delete a folder and EVERYTHING inside it — nested folders and items —
    via the DB cascade. The object-storage blobs of any file items in the
    subtree are removed first so we don't orphan them in MinIO. Irreversible."""
    await enforce_rate_limit(
        key=f"vault:write:{user.id}",
        limit=300, window_seconds=3600,
        detail="Too many vault writes. Try again shortly.",
    )
    folder = await _folder_or_404(session, user.id, folder_id)
    # Build the subtree (this folder + all descendants) so we can collect the
    # storage keys before the cascade deletes the rows.
    rows = (
        await session.execute(
            select(VaultFolder.id, VaultFolder.parent_id).where(
                VaultFolder.user_id == user.id
            )
        )
    ).all()
    children: dict = defaultdict(list)
    for fid, pid in rows:
        children[pid].append(fid)
    subtree: list = []
    stack = [folder.id]
    while stack:
        cur = stack.pop()
        subtree.append(cur)
        stack.extend(children.get(cur, []))
    keys = (
        await session.execute(
            select(VaultItem.storage_key).where(
                VaultItem.user_id == user.id,
                VaultItem.folder_id.in_(subtree),
                VaultItem.storage_key.is_not(None),
            )
        )
    ).scalars().all()
    await session.delete(folder)  # cascade removes descendant folders + items
    await session.commit()
    for k in keys:
        if k:
            storage.delete(storage.bucket_vault, k)


# ---- files (object-storage-backed items) -----------------------------------


def _iter_vault_object(storage_key: str, offset: int, length: int):
    """Yield an object (or a byte range of it) from MinIO in 1 MiB chunks.
    A sync generator — Starlette iterates it in a threadpool, so the blocking
    reads never stall the event loop. `length == 0` means read to the end."""
    resp = storage.client.get_object(
        storage.bucket_vault, storage_key, offset=offset, length=length,
    )
    try:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        resp.close()
        resp.release_conn()


def _parse_range(header: str | None, size: int):
    """Parse a single-range `Range: bytes=…` header into an inclusive
    (start, end). Returns None for no/blank range (caller streams the whole
    object). Raises 416 with a Content-Range on an unsatisfiable range."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        raise HTTPException(
            status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            "Invalid range.", headers={"Content-Range": f"bytes */{size}"},
        )
    start_s, end_s = spec.split("-", 1)
    try:
        if start_s == "":
            n = int(end_s)
            if n <= 0:
                raise ValueError
            start = max(0, size - n)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        raise HTTPException(
            status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            "Invalid range.", headers={"Content-Range": f"bytes */{size}"},
        )
    if size == 0 or start > end or start >= size:
        raise HTTPException(
            status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            "Range not satisfiable.",
            headers={"Content-Range": f"bytes */{size}"},
        )
    end = min(end, size - 1)
    return start, end


@router.post("/files", status_code=status.HTTP_201_CREATED,
             response_model=VaultItemResponse)
async def upload_vault_file(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    nonce: Annotated[str, Form()],
    ciphertext: Annotated[str, Form()],
    wrapped_key: Annotated[str, Form()],
    blob: Annotated[UploadFile, File()],
    folder_id: Annotated[str | None, Form()] = None,
) -> VaultItemResponse:
    """Upload an end-to-end-encrypted file into the vault. `blob` is the
    ciphertext (the client encrypted it with a random per-file key, chunked
    with AES-GCM); the server streams it straight to its own MinIO bucket
    without ever holding the key. `nonce`/`ciphertext` are the small
    encrypted metadata blob (filename, mime, plaintext size…); `wrapped_key`
    is the per-file key sealed to the owner's account public key. The server
    never sees plaintext, the per-file key, or the metadata."""
    await enforce_rate_limit(
        key=f"vault:write:{user.id}",
        limit=300, window_seconds=3600,
        detail="Too many vault writes. Try again shortly.",
    )
    if await session.get(VaultMeta, user.id) is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Set up your vault (master password) before adding files.",
        )
    parsed_folder: UUID | None = None
    if folder_id:
        try:
            parsed_folder = UUID(folder_id)
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid folder_id.",
            )
        await _folder_or_404(session, user.id, parsed_folder)
    nonce_b = _b64_field(nonce, min_bytes=_NONCE_BYTES, max_bytes=_NONCE_BYTES,
                         exact=_NONCE_BYTES, field="nonce")
    ct_b = _b64_field(ciphertext, min_bytes=1, max_bytes=_MAX_CIPHERTEXT_BYTES,
                      field="ciphertext")
    wk_b = _b64_field(wrapped_key, min_bytes=1, max_bytes=_MAX_WRAPPED_KEY_BYTES,
                      field="wrapped_key")
    # Size the spooled upload by seeking to the end, then rewind for streaming.
    fh = blob.file
    fh.seek(0, 2)
    length = fh.tell()
    fh.seek(0)
    if length <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Empty file.",
        )
    if length > settings.vault_upload_max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {settings.vault_upload_max_bytes // (1024 * 1024)} MB "
            "per-file limit.",
        )
    used = await _used_bytes(session, user.id)
    if used + length > _effective_quota(user):
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "This upload would exceed your storage quota.",
        )
    storage_key = f"{user.id}/{uuid4().hex}"
    try:
        await run_in_threadpool(
            storage.put_stream, storage.bucket_vault, storage_key, fh, length,
            "application/octet-stream",
        )
    except Exception:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Could not store the file. Try again.",
        )
    item = VaultItem(
        user_id=user.id, kind="file", nonce=nonce_b, ciphertext=ct_b,
        folder_id=parsed_folder, storage_key=storage_key, size_bytes=length,
        wrapped_key=wk_b,
    )
    session.add(item)
    try:
        await session.commit()
        await session.refresh(item)
    except Exception:
        # Roll back the orphaned object so a failed commit doesn't leak storage.
        await run_in_threadpool(storage.delete, storage.bucket_vault, storage_key)
        raise
    return _item_response(item)


@router.get("/files/{item_id}")
async def download_vault_file(
    item_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Stream a file item's ciphertext back to the client (which decrypts it
    with the per-file key it unsealed from `wrapped_key`). Supports HTTP Range
    so encrypted video/audio can be scrubbed. The body is opaque ciphertext —
    served as octet-stream, never inline-rendered."""
    item = await session.get(VaultItem, item_id)
    if item is None or item.user_id != user.id or item.storage_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found.")
    size = await run_in_threadpool(
        storage.stat, storage.bucket_vault, item.storage_key,
    )
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Disposition": "attachment",
    }
    rng = _parse_range(request.headers.get("range"), size)
    if rng is not None:
        start, end = rng
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(length)
        return StreamingResponse(
            _iter_vault_object(item.storage_key, start, length),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type="application/octet-stream",
            headers=headers,
        )
    headers["Content-Length"] = str(size)
    return StreamingResponse(
        _iter_vault_object(item.storage_key, 0, 0),
        media_type="application/octet-stream",
        headers=headers,
    )


# ---- sharing (VLT-8 P5) ----------------------------------------------------
#
# A share seals the item's content key (files) or plaintext (secure items) to
# a recipient's account public key, client-side. The server stores an opaque
# sealed blob + routing and authorizes a recipient's ciphertext fetch. Vault
# shares have NO comments and no public links — a direct, revocable, read-only
# grant to a named account.


async def _user_by_email(session: AsyncSession, email: str) -> User | None:
    e = (email or "").strip()
    if not e:
        return None
    return await session.scalar(
        select(User).where(func.lower(User.email) == e.lower())
    )


@router.get("/recipient-key", response_model=VaultRecipientKeyResponse)
async def get_recipient_key(
    email: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultRecipientKeyResponse:
    """Resolve a recipient's vault account PUBLIC key by email so the sharer
    can seal a bundle to them. 404 when the address has no neuthek account or
    hasn't set up a vault. Rate-limited to slow address enumeration."""
    await enforce_rate_limit(
        key=f"vault:reckey:{user.id}",
        limit=60, window_seconds=3600,
        detail="Too many lookups. Try again shortly.",
    )
    target = await _user_by_email(session, email)
    if target is None or target.vault_public_key is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No neuthek vault found for that email.",
        )
    return VaultRecipientKeyResponse(
        email=target.email,
        display_name=getattr(target, "display_name", None),
        account_public_key=_b64(target.vault_public_key),
    )


@router.post("/items/{item_id}/share", status_code=status.HTTP_201_CREATED,
             response_model=VaultShareRecipientResponse)
async def share_vault_item(
    item_id: UUID,
    payload: VaultShareCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultShareRecipientResponse:
    """Grant a recipient access to one item. The client has already sealed the
    bundle to the recipient's public key; we store it + routing. Re-sharing the
    same item to the same recipient replaces the prior grant."""
    await enforce_rate_limit(
        key=f"vault:share:{user.id}",
        limit=100, window_seconds=3600,
        detail="Too many shares. Try again shortly.",
    )
    item = await session.get(VaultItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vault item not found.")
    recipient = await _user_by_email(session, payload.recipient_email)
    if recipient is None or recipient.vault_public_key is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No neuthek vault found for that email.",
        )
    if recipient.id == user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "You can’t share an item with yourself.",
        )
    sealed = _b64_field(payload.sealed_payload, min_bytes=1,
                        max_bytes=_MAX_SEALED_PAYLOAD_BYTES, field="sealed_payload")
    existing = await session.scalar(
        select(VaultShareGrant).where(
            VaultShareGrant.item_id == item_id,
            VaultShareGrant.recipient_user_id == recipient.id,
        )
    )
    if existing is not None:
        existing.sealed_payload = sealed
        existing.kind = item.kind
        existing.storage_key = item.storage_key
        existing.size_bytes = item.size_bytes
        grant = existing
    else:
        grant = VaultShareGrant(
            item_id=item_id,
            owner_user_id=user.id,
            recipient_user_id=recipient.id,
            kind=item.kind,
            storage_key=item.storage_key,
            size_bytes=item.size_bytes,
            sealed_payload=sealed,
        )
        session.add(grant)
    await session.commit()
    await session.refresh(grant)
    return VaultShareRecipientResponse(
        id=grant.id,
        recipient_email=recipient.email,
        recipient_display_name=getattr(recipient, "display_name", None),
        created_at=grant.created_at,
    )


@router.get("/items/{item_id}/shares", response_model=list[VaultShareRecipientResponse])
async def list_item_shares(
    item_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[VaultShareRecipientResponse]:
    """Who an item is currently shared with (owner view), for revoke UI."""
    item = await session.get(VaultItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vault item not found.")
    rows = (
        await session.execute(
            select(VaultShareGrant, User.email, User.display_name)
            .join(User, User.id == VaultShareGrant.recipient_user_id)
            .where(
                VaultShareGrant.item_id == item_id,
                VaultShareGrant.owner_user_id == user.id,
            )
            .order_by(VaultShareGrant.created_at.asc())
        )
    ).all()
    return [
        VaultShareRecipientResponse(
            id=g.id, recipient_email=email, recipient_display_name=dn,
            created_at=g.created_at,
        )
        for (g, email, dn) in rows
    ]


@router.get("/shares", response_model=list[VaultIncomingShareResponse])
async def list_incoming_shares(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[VaultIncomingShareResponse]:
    """Items shared WITH me. The client unseals each sealed_payload with its
    account private key to recover the key/plaintext."""
    rows = (
        await session.execute(
            select(VaultShareGrant, User.email, User.display_name)
            .join(User, User.id == VaultShareGrant.owner_user_id)
            .where(VaultShareGrant.recipient_user_id == user.id)
            .order_by(VaultShareGrant.created_at.desc())
        )
    ).all()
    return [
        VaultIncomingShareResponse(
            id=g.id, kind=g.kind, size_bytes=g.size_bytes,
            has_file=g.storage_key is not None,
            sealed_payload=_b64(g.sealed_payload),
            owner_email=email, owner_display_name=dn,
            created_at=g.created_at,
        )
        for (g, email, dn) in rows
    ]


@router.delete("/shares/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share(
    grant_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Revoke a share. Either party may remove it — the owner revokes access,
    the recipient declines/removes it from their list."""
    grant = await session.get(VaultShareGrant, grant_id)
    if grant is None or (
        grant.owner_user_id != user.id and grant.recipient_user_id != user.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found.")
    await session.delete(grant)
    await session.commit()


@router.get("/shares/{grant_id}/file")
async def download_shared_file(
    grant_id: UUID,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Stream a shared FILE's ciphertext to the recipient (who decrypts it with
    the per-file key recovered from the sealed bundle). Range-capable. The grant
    carries its own copy of the object key, so this never reads the owner's
    RLS-fenced vault_items row."""
    grant = await session.get(VaultShareGrant, grant_id)
    if grant is None or grant.recipient_user_id != user.id or grant.storage_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shared file not found.")
    size = await run_in_threadpool(
        storage.stat, storage.bucket_vault, grant.storage_key,
    )
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Disposition": "attachment",
    }
    rng = _parse_range(request.headers.get("range"), size)
    if rng is not None:
        start, end = rng
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(length)
        return StreamingResponse(
            _iter_vault_object(grant.storage_key, start, length),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type="application/octet-stream",
            headers=headers,
        )
    headers["Content-Length"] = str(size)
    return StreamingResponse(
        _iter_vault_object(grant.storage_key, 0, 0),
        media_type="application/octet-stream",
        headers=headers,
    )


# ---- public links (VLT-8 P7) ----------------------------------------------
#
# An "anyone with the link" share. Still zero-knowledge: the decryption key
# lives in the URL fragment (never sent to the server). When password-set, the
# key is derived from the fragment secret AND the password client-side, so the
# stored blob can't be opened with the link alone. The public read endpoints
# are UNAUTHENTICATED and gated only by the high-entropy token.

_PUBLIC_LINK_MAX_EXPIRY_DAYS = 365


async def _live_public_link(session: AsyncSession, token: str) -> VaultPublicLink | None:
    link = await session.scalar(
        select(VaultPublicLink).where(VaultPublicLink.token == token)
    )
    if link is None:
        return None
    if link.expires_at is not None and link.expires_at <= datetime.now(timezone.utc):
        return None
    return link


@router.post("/items/{item_id}/public-link", status_code=status.HTTP_201_CREATED,
             response_model=VaultPublicLinkResponse)
async def create_public_link(
    item_id: UUID,
    payload: VaultPublicLinkCreate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultPublicLinkResponse:
    """Create (or replace) the public link for an item. Replacing rotates the
    token, so any previously-shared link stops working."""
    await enforce_rate_limit(
        key=f"vault:publiclink:{user.id}",
        limit=100, window_seconds=3600,
        detail="Too many link operations. Try again shortly.",
    )
    item = await session.get(VaultItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vault item not found.")
    sealed = _b64_field(payload.sealed_payload, min_bytes=1,
                        max_bytes=_MAX_SEALED_PAYLOAD_BYTES, field="sealed_payload")
    salt: bytes | None = None
    iters: int | None = None
    if payload.password_required:
        if not payload.kdf_salt or not payload.kdf_iterations:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "kdf_salt and kdf_iterations are required for a password link.",
            )
        salt = _b64_field(payload.kdf_salt, min_bytes=_MIN_SALT_BYTES,
                          max_bytes=_MAX_SALT_BYTES, field="kdf_salt")
        if payload.kdf_iterations < _MIN_KDF_ITER or payload.kdf_iterations > _MAX_KDF_ITER:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "kdf_iterations out of range.",
            )
        iters = payload.kdf_iterations
    expires_at: datetime | None = None
    if payload.expires_in_days is not None:
        d = payload.expires_in_days
        if d <= 0 or d > _PUBLIC_LINK_MAX_EXPIRY_DAYS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "expires_in_days out of range.",
            )
        expires_at = datetime.now(timezone.utc) + timedelta(days=d)
    # One link per item — drop any existing one so the token rotates.
    await session.execute(
        sa_delete(VaultPublicLink).where(
            VaultPublicLink.item_id == item_id,
            VaultPublicLink.owner_user_id == user.id,
        )
    )
    link = VaultPublicLink(
        token=secrets.token_urlsafe(32),
        item_id=item_id,
        owner_user_id=user.id,
        kind=item.kind,
        storage_key=item.storage_key,
        size_bytes=item.size_bytes,
        sealed_payload=sealed,
        password_required=payload.password_required,
        kdf_salt=salt,
        kdf_iterations=iters,
        expires_at=expires_at,
    )
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return VaultPublicLinkResponse(
        token=link.token,
        password_required=link.password_required,
        expires_at=link.expires_at,
        created_at=link.created_at,
    )


@router.get("/items/{item_id}/public-link", response_model=VaultPublicLinkResponse)
async def get_public_link(
    item_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultPublicLinkResponse:
    """The current public link for an item (owner view), or 404."""
    link = await session.scalar(
        select(VaultPublicLink).where(
            VaultPublicLink.item_id == item_id,
            VaultPublicLink.owner_user_id == user.id,
        )
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No public link.")
    return VaultPublicLinkResponse(
        token=link.token,
        password_required=link.password_required,
        expires_at=link.expires_at,
        created_at=link.created_at,
    )


@router.delete("/items/{item_id}/public-link", status_code=status.HTTP_204_NO_CONTENT)
async def delete_public_link(
    item_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Revoke the public link — the URL stops working immediately."""
    link = await session.scalar(
        select(VaultPublicLink).where(
            VaultPublicLink.item_id == item_id,
            VaultPublicLink.owner_user_id == user.id,
        )
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No public link.")
    await session.delete(link)
    await session.commit()


@router.get("/public/{token}", response_model=VaultPublicLinkView)
async def view_public_link(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VaultPublicLinkView:
    """UNAUTHENTICATED. Return the sealed bundle + public KDF params for a
    link. The visitor derives the key from the URL fragment (+ password) and
    decrypts locally — the server never sees the key or plaintext."""
    await enforce_rate_limit(
        key=f"vault:publicview:{token}",
        limit=240, window_seconds=3600,
        detail="Too many requests for this link. Try again shortly.",
    )
    link = await _live_public_link(session, token)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link is unavailable.")
    return VaultPublicLinkView(
        kind=link.kind,
        has_file=link.storage_key is not None,
        size_bytes=link.size_bytes,
        sealed_payload=_b64(link.sealed_payload),
        password_required=link.password_required,
        kdf_salt=_b64(link.kdf_salt) if link.kdf_salt else None,
        kdf_iterations=link.kdf_iterations,
    )


@router.get("/public/{token}/file")
async def download_public_link_file(
    token: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """UNAUTHENTICATED. Stream a public file link's ciphertext (Range-capable).
    The visitor decrypts with the key derived from the fragment (+ password)."""
    await enforce_rate_limit(
        key=f"vault:publicfile:{token}",
        limit=240, window_seconds=3600,
        detail="Too many requests for this link. Try again shortly.",
    )
    link = await _live_public_link(session, token)
    if link is None or link.storage_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link is unavailable.")
    size = await run_in_threadpool(
        storage.stat, storage.bucket_vault, link.storage_key,
    )
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Disposition": "attachment",
    }
    rng = _parse_range(request.headers.get("range"), size)
    if rng is not None:
        start, end = rng
        length = end - start + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(length)
        return StreamingResponse(
            _iter_vault_object(link.storage_key, start, length),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type="application/octet-stream",
            headers=headers,
        )
    headers["Content-Length"] = str(size)
    return StreamingResponse(
        _iter_vault_object(link.storage_key, 0, 0),
        media_type="application/octet-stream",
        headers=headers,
    )


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
    # Collect every file blob first so wiping the rows doesn't orphan objects
    # in MinIO.
    keys = (
        await session.execute(
            select(VaultItem.storage_key).where(
                VaultItem.user_id == user.id,
                VaultItem.storage_key.is_not(None),
            )
        )
    ).scalars().all()
    await session.execute(
        sa_delete(VaultItem).where(VaultItem.user_id == user.id)
    )
    # Folders cascade-delete with their items, but wipe them explicitly too
    # (items may be folder-less; folders may be empty).
    await session.execute(
        sa_delete(VaultFolder).where(VaultFolder.user_id == user.id)
    )
    await session.execute(
        sa_delete(VaultMeta).where(VaultMeta.user_id == user.id)
    )
    await session.commit()
    for k in keys:
        if k:
            storage.delete(storage.bucket_vault, k)
