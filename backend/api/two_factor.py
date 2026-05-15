"""TOTP 2FA endpoints (todo §1.2.2).

Three setup-side routes (current user, authenticated):
  POST /account/2fa/setup     Generate a fresh secret, return QR PNG +
                              provisioning URI. `totp_enabled` stays
                              False until /verify lands a real code.
  POST /account/2fa/verify    Confirm a 6-digit code matches the
                              secret. On success, flips `totp_enabled`
                              and writes `totp_verified_at`.
  POST /account/2fa/disable   Wipes the secret + clears `totp_enabled`.
                              Requires a valid current code or the
                              user's password (defense-in-depth so a
                              hijacked session can't turn 2FA off).

Login-side route (public):
  POST /auth/jwt/login-totp   Same shape as /auth/jwt/login but accepts
                              a `totp_code` form field. Used by the FE
                              after the standard login responds with
                              "totp_required". TOTP-enabled users
                              cannot bypass this — the original
                              /auth/jwt/login refuses to mint a token
                              when totp_enabled=True without a code.

Secrets are persisted Fernet-encrypted via `backend.secret_box`. The
Fernet key lives outside the DB so a dump alone doesn't yield working
codes. Argon2 isn't suitable here — we need to recover the plaintext
secret on every verify, not just compare.
"""
from __future__ import annotations

import base64
import io
import logging
from datetime import datetime, timezone
from typing import Annotated

import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, status
from fastapi_users.password import PasswordHelper
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import add_audit
from backend.auth.users import current_active_user, get_jwt_strategy
from backend.config import settings
from backend.db import get_session
from backend.models import User
from backend.secret_box import decrypt, encrypt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account/2fa", tags=["account", "2fa"])
auth_router = APIRouter(prefix="/auth/jwt", tags=["auth"])

_PWD = PasswordHelper()


def _issuer() -> str:
    """Otpauth issuer label — appears under the entry in the user's
    authenticator app. Tied to env so prod ≠ dev visually."""
    base = "neuthek"
    env = (settings.app_env or "").lower()
    return base if env in {"prod", "production", ""} else f"{base} ({env})"


def _provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_issuer())


def _qr_png_base64(otpauth_uri: str) -> str:
    """Render the provisioning URI as a small PNG and return the
    base64 string. Inline data URI on the FE = no third-party QR
    service touches the secret."""
    img = qrcode.make(otpauth_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _verify_code(secret: str, code: str) -> bool:
    """One-window tolerance (≈30 s either side of `now`) so a code the
    user just-barely-typed-late still works. `pyotp.TOTP.verify`
    handles the constant-time compare for us."""
    try:
        return bool(pyotp.TOTP(secret).verify(code.strip(), valid_window=1))
    except Exception:
        return False


# ---------- setup-side endpoints ----------


class TwoFactorSetupResponse(BaseModel):
    secret: str            # base32 secret, shown once so the user can paste it manually
    otpauth_uri: str       # the URI the QR encodes
    qr_png_base64: str     # PNG bytes for direct <img src="data:image/png;base64,..."/> rendering
    issuer: str


class TwoFactorStatusResponse(BaseModel):
    enabled: bool
    verified_at: datetime | None = None


class TwoFactorVerifyBody(BaseModel):
    code: str


class TwoFactorDisableBody(BaseModel):
    # Either a current TOTP code OR the password — both are accepted so
    # a user who lost their authenticator can still disable 2FA by
    # re-confirming their password.
    code: str | None = None
    password: str | None = None


@router.get("/status", response_model=TwoFactorStatusResponse)
async def two_factor_status(
    user: Annotated[User, Depends(current_active_user)],
) -> TwoFactorStatusResponse:
    return TwoFactorStatusResponse(
        enabled=bool(user.totp_enabled),
        verified_at=user.totp_verified_at,
    )


@router.post("/setup", response_model=TwoFactorSetupResponse)
async def two_factor_setup(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TwoFactorSetupResponse:
    """Roll a fresh secret + return QR. `totp_enabled` stays False until
    /verify lands. Calling setup twice overwrites the unverified secret
    — useful for users who lost the QR before completing setup."""
    if user.totp_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "2FA is already enabled. Disable it first to re-roll the secret.",
        )
    secret = pyotp.random_base32()
    user.totp_secret_enc = encrypt(secret)
    await session.commit()

    uri = _provisioning_uri(secret, user.email)
    return TwoFactorSetupResponse(
        secret=secret,
        otpauth_uri=uri,
        qr_png_base64=_qr_png_base64(uri),
        issuer=_issuer(),
    )


@router.post("/verify", response_model=TwoFactorStatusResponse)
async def two_factor_verify(
    body: TwoFactorVerifyBody,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TwoFactorStatusResponse:
    """Confirm the code matches the stored secret. On the first
    successful match, flips `totp_enabled` to True."""
    if not user.totp_secret_enc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No 2FA setup in progress. Call /account/2fa/setup first.",
        )
    secret = decrypt(user.totp_secret_enc)
    if not _verify_code(secret, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid 2FA code.")
    was_enabled = bool(user.totp_enabled)
    user.totp_enabled = True
    user.totp_verified_at = datetime.now(timezone.utc)
    await add_audit(
        session,
        user_id=user.id,
        action="account.2fa.enabled" if not was_enabled else "account.2fa.verified",
        details={},
    )
    await session.commit()
    return TwoFactorStatusResponse(
        enabled=True, verified_at=user.totp_verified_at,
    )


@router.post("/disable", response_model=TwoFactorStatusResponse)
async def two_factor_disable(
    body: TwoFactorDisableBody,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TwoFactorStatusResponse:
    """Wipe the TOTP secret + flip `totp_enabled` to False. Requires
    either a valid current code OR the user's password. Without one of
    those a hijacked session can't silently turn 2FA off."""
    if not user.totp_enabled and not user.totp_secret_enc:
        return TwoFactorStatusResponse(enabled=False, verified_at=None)

    confirmed = False
    if body.code and user.totp_secret_enc:
        secret = decrypt(user.totp_secret_enc)
        if _verify_code(secret, body.code):
            confirmed = True
    if not confirmed and body.password and user.hashed_password:
        try:
            ok, _ = _PWD.verify_and_update(body.password, user.hashed_password)
            confirmed = bool(ok)
        except Exception:
            confirmed = False
    if not confirmed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide a current 2FA code or your password to disable 2FA.",
        )

    user.totp_secret_enc = None
    user.totp_enabled = False
    user.totp_verified_at = None
    await add_audit(
        session, user_id=user.id, action="account.2fa.disabled", details={},
    )
    await session.commit()
    return TwoFactorStatusResponse(enabled=False, verified_at=None)


# ---------- login-side endpoint ----------


class LoginTotpResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@auth_router.post("/login-totp", response_model=LoginTotpResponse)
async def login_with_totp(
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    totp_code: Annotated[str, Form()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoginTotpResponse:
    """Login endpoint that requires a TOTP code alongside the password.
    Used by the FE after the standard /auth/jwt/login responds with
    `{"detail": "totp_required"}`. Same lockout middleware path as the
    other auth endpoints (added to `_AUTH_PATHS` in `backend.security`
    so failed TOTP attempts count toward brute-force lockout)."""
    target = (
        await session.execute(select(User).where(User.email == username.lower()))
    ).scalar_one_or_none()
    if target is None or not target.is_active or not target.hashed_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid credentials")

    ok, updated_hash = _PWD.verify_and_update(password, target.hashed_password)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid credentials")
    if updated_hash is not None:
        target.hashed_password = updated_hash

    if not target.totp_enabled or not target.totp_secret_enc:
        # 2FA isn't enabled — the user shouldn't be here. Send them to
        # the plain /auth/jwt/login route instead.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This account doesn't have 2FA enabled. Sign in normally.",
        )
    secret = decrypt(target.totp_secret_enc)
    if not _verify_code(secret, totp_code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid 2FA code.")

    target.totp_verified_at = datetime.now(timezone.utc)
    await session.commit()

    strategy = get_jwt_strategy()
    token = await strategy.write_token(target)
    return LoginTotpResponse(access_token=token)
