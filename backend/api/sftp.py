"""SFTP access management API (SFTP-1).

Lets a signed-in user manage how they reach the read+write SFTP server
(`backend/sftp_server.py`):

  GET    /sftp/info            — connection details: host hint, port,
                                 their SFTP username (= account email),
                                 whether a password is set, key count,
                                 ready-to-paste mount commands.
  GET    /sftp/keys            — list this user's registered public keys.
  POST   /sftp/keys            — register an OpenSSH public key.
  DELETE /sftp/keys/{id}       — remove a registered key.
  PUT    /sftp/password        — set / change the dedicated SFTP password.
  DELETE /sftp/password        — clear the SFTP password (disables pw auth).

All routes are owner-scoped: the authenticated user's id drives every
query (RLS GUC via `current_active_user` + explicit `user_id ==` filters),
so a user can only ever see / change their OWN SFTP credentials.

The SFTP PASSWORD is deliberately a SEPARATE secret from the account login
password (SFTP sends it to the server on every connect). We hash it with
the same Argon2 helper fastapi-users uses for account passwords and reuse
the account password-strength rules so it can't be a trivial string.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi_users.password import PasswordHelper
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import UserManager, current_active_user, get_user_manager
from backend.db import get_session
from backend.models import SftpAuthorizedKey, SftpCredential, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sftp", tags=["sftp"])

_PWD = PasswordHelper()


# ---------------------------------------------------------------------------
# Public-key validation + fingerprinting
# ---------------------------------------------------------------------------
def _parse_and_fingerprint(public_key: str) -> tuple[str, str]:
    """Validate an OpenSSH public key line and return (normalized_line,
    fingerprint). Raises HTTP 400 on anything that isn't a single valid
    public key.

    We import asyncssh lazily so the API container boots even if asyncssh
    isn't installed (it always is in this image, but the lazy import keeps
    the dependency localized to the SFTP feature)."""
    raw = (public_key or "").strip()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "public_key is required")
    if len(raw) > 16 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "public_key is too large")
    # Reject obvious private keys outright — a user pasting a PRIVATE key
    # here would leak it into a clear-text column. Fail closed.
    if "PRIVATE KEY" in raw.upper():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That looks like a PRIVATE key. Paste your PUBLIC key "
            "(the one-line `ssh-ed25519 AAAA... ` / `.pub` file).",
        )
    try:
        import asyncssh

        key = asyncssh.import_public_key(raw)
    except Exception:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Could not parse that as an OpenSSH public key. Expected a "
            "single line like `ssh-ed25519 AAAAC3Nza... comment` or the "
            "contents of an `id_*.pub` file.",
        )
    # Normalize to the canonical OpenSSH authorized_keys line so what we
    # store is exactly what the server re-parses.
    try:
        normalized = key.export_public_key().decode("utf-8").strip()
    except Exception:
        normalized = raw
    blob = key.public_data
    digest = hashlib.sha256(blob).digest()
    fpr = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
    return normalized, fpr


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SftpKeyIn(BaseModel):
    public_key: str = Field(..., description="OpenSSH public key line (id_*.pub contents).")
    label: Optional[str] = Field(None, max_length=200)


class SftpKeyOut(BaseModel):
    id: UUID
    fingerprint: str
    label: Optional[str]
    created_at: datetime
    last_used_at: Optional[datetime]


class SftpPasswordIn(BaseModel):
    password: str = Field(..., min_length=10, max_length=256)


class SftpInfo(BaseModel):
    username: str
    host: str
    port: int
    password_set: bool
    key_count: int
    # The SFTP server now supports read AND write (uploads funnel through
    # the same validation + quota as the web uploader). Operators can pin a
    # read-only deployment via SFTP_READ_ONLY=true on the sftp service.
    read_only: bool = False
    # Ready-to-paste mount/connect commands for each platform.
    commands: dict[str, str]


# ---------------------------------------------------------------------------
# Connection info
# ---------------------------------------------------------------------------
def _public_host_hint() -> str:
    """Best hint for the host a client should connect to. Operators set
    SFTP_PUBLIC_HOST when they expose the port; otherwise we say
    `localhost` (the safe default — the port binds to 127.0.0.1 in
    compose)."""
    return os.getenv("SFTP_PUBLIC_HOST", "localhost")


def _public_port() -> int:
    try:
        return int(os.getenv("SFTP_PUBLIC_PORT", os.getenv("SFTP_PORT", "2222")))
    except ValueError:
        return 2222


@router.get("/info", response_model=SftpInfo)
async def sftp_info(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SftpInfo:
    key_count = len(
        (
            await session.execute(
                select(SftpAuthorizedKey.id).where(
                    SftpAuthorizedKey.user_id == user.id
                )
            )
        ).all()
    )
    pw_set = (
        await session.execute(
            select(SftpCredential.user_id).where(
                SftpCredential.user_id == user.id
            )
        )
    ).first() is not None

    username = user.email
    host = _public_host_hint()
    port = _public_port()
    # Quote the username for the shell since emails contain no shell-special
    # chars except possibly `+`; still, keep commands copy-paste safe.
    commands = {
        # Read+write mount (no `ro`) — uploads into a folder create files
        # there; they go through the same validation + quota as the web app.
        "linux_mac_sshfs": (
            f"sshfs -p {port} '{username}@{host}:/' ~/neuthek "
            "-o reconnect,idmap=user"
        ),
        "sftp_cli": f"sftp -P {port} '{username}@{host}'",
        "macos_finder": f"sftp://{username}@{host}:{port}/   (Finder -> Go -> Connect to Server)",
        "windows_winscp": (
            f"WinSCP -> New Session -> File protocol: SFTP, Host name: {host}, "
            f"Port number: {port}, User name: {username}"
        ),
    }
    return SftpInfo(
        username=username,
        host=host,
        port=port,
        password_set=pw_set,
        key_count=key_count,
        commands=commands,
    )


# ---------------------------------------------------------------------------
# Public keys
# ---------------------------------------------------------------------------
@router.get("/keys", response_model=list[SftpKeyOut])
async def list_keys(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SftpKeyOut]:
    rows = (
        await session.execute(
            select(SftpAuthorizedKey)
            .where(SftpAuthorizedKey.user_id == user.id)
            .order_by(SftpAuthorizedKey.created_at.desc())
        )
    ).scalars().all()
    return [
        SftpKeyOut(
            id=r.id,
            fingerprint=r.fingerprint,
            label=r.label,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
        )
        for r in rows
    ]


@router.post("/keys", response_model=SftpKeyOut, status_code=status.HTTP_201_CREATED)
async def add_key(
    body: SftpKeyIn,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SftpKeyOut:
    normalized, fpr = _parse_and_fingerprint(body.public_key)
    row = SftpAuthorizedKey(
        user_id=user.id,
        public_key=normalized,
        fingerprint=fpr,
        label=(body.label or None),
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That key is already registered on your account.",
        )
    await add_audit(
        session,
        user_id=user.id,
        action="sftp.key.add",
        details={"fingerprint": fpr, "label": body.label or None},
    )
    await session.commit()
    return SftpKeyOut(
        id=row.id,
        fingerprint=row.fingerprint,
        label=row.label,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


@router.delete(
    "/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_key(
    key_id: UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    row = (
        await session.execute(
            select(SftpAuthorizedKey).where(
                SftpAuthorizedKey.id == key_id,
                SftpAuthorizedKey.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key not found")
    fpr = row.fingerprint
    await session.delete(row)
    await add_audit(
        session,
        user_id=user.id,
        action="sftp.key.delete",
        details={"fingerprint": fpr},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# SFTP password
# ---------------------------------------------------------------------------
@router.put(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def set_password(
    body: SftpPasswordIn,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    user_manager: Annotated[UserManager, Depends(get_user_manager)],
) -> Response:
    # Reuse the account password-strength policy so the SFTP password can't
    # be a trivial string. `validate_password` raises InvalidPasswordException
    # on a weak password; surface it as a 400.
    from fastapi_users import InvalidPasswordException

    try:
        await user_manager.validate_password(body.password, user)
    except InvalidPasswordException as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason)

    pw_hash = _PWD.hash(body.password)
    existing = (
        await session.execute(
            select(SftpCredential).where(SftpCredential.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(SftpCredential(user_id=user.id, password_hash=pw_hash))
    else:
        existing.password_hash = pw_hash
        existing.updated_at = datetime.now(timezone.utc)
    await add_audit(
        session, user_id=user.id, action="sftp.password.set", details={},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def clear_password(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    await session.execute(
        delete(SftpCredential).where(SftpCredential.user_id == user.id)
    )
    await add_audit(
        session, user_id=user.id, action="sftp.password.clear", details={},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
