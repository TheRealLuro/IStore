"""Consent state machine + HTTP endpoints (Phase 4).

Append-only `consent_records` table is the source of truth. The current state
for a (user, kind) pair is the latest row. State transitions:

    (no row)  -> GRANTED                 (POST /consent/face-recognition/grant)
    GRANTED   -> WITHDRAWN               (POST /consent/face-recognition/withdraw)
                  + cascade-delete biometric rows
    WITHDRAWN -> GRANTED                 (re-grant; biometric data must be
                                          re-derived from images on next upload
                                          or via /consent/.../backfill)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.users import current_active_user
from backend.db import get_session
from backend.models import (
    AuditLog,
    ConsentRecord,
    Face,
    FaceDetection,
    ImageGeo,
    Person,
    User,
)
from backend.storage import storage

logger = logging.getLogger(__name__)

POLICY_VERSION = "v1"
POLICY_PATH = Path(__file__).resolve().parent.parent / "policies" / "face_recognition_v1.md"
CONSENT_KIND = "face_recognition"
RETENTION_DAYS = 3 * 365  # 3 years from grant per BIPA §15(a)

# C3/C4 — every new privacy scope must be listed here. Routing the
# generic /consent/{kind} endpoints through this allowlist prevents the
# DB column from accumulating typos / arbitrary values.
SUPPORTED_SCOPES: tuple[str, ...] = (
    "face_recognition",      # Pass B face detection + clustering
    "gps_retention",         # store EXIF GPS in image_geo
    "ai_summary",             # Florence-2/Qwen scene + topic summaries
    "semantic_search",        # CLIP embeddings retained for vector search
    "bandit_compression_telemetry",  # LinUCB reward signals
)

router = APIRouter(prefix="/consent", tags=["consent"])


# ---------- helpers ----------

def _read_policy_text() -> str:
    return POLICY_PATH.read_text(encoding="utf-8")


def _policy_sha256() -> bytes:
    return hashlib.sha256(_read_policy_text().encode("utf-8")).digest()


async def _latest_consent(
    session: AsyncSession, user_id, kind: str = CONSENT_KIND
) -> ConsentRecord | None:
    stmt = (
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id, ConsentRecord.consent_kind == kind)
        .order_by(ConsentRecord.granted_at.desc())
        .limit(1)
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def is_consent_active(session: AsyncSession, user_id) -> bool:
    """Public helper used by image.py to gate Pass B."""
    rec = await _latest_consent(session, user_id)
    return rec is not None and rec.state == "GRANTED"


async def is_scope_active(
    session: AsyncSession, user_id, kind: str
) -> bool:
    """Generic check for any supported consent scope. Used by C3 + C4
    for non-face scopes (gps_retention, ai_summary, …)."""
    rec = await _latest_consent(session, user_id, kind=kind)
    return rec is not None and rec.state == "GRANTED"


def _validate_scope(kind: str) -> None:
    if kind not in SUPPORTED_SCOPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported consent scope: {kind!r}",
        )


# ---------- schemas ----------

class ConsentStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: str
    state: str  # 'GRANTED' | 'WITHDRAWN' | 'NONE'
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    policy_version: str | None = None


class GrantPayload(BaseModel):
    signature_text: str = Field(..., min_length=2, max_length=200)
    consent_collection: bool
    consent_retention: bool


class PolicyResponse(BaseModel):
    version: str
    text: str
    sha256_hex: str


# ---------- endpoints ----------

@router.get("/face-recognition/policy", response_model=PolicyResponse)
async def get_policy() -> PolicyResponse:
    text = _read_policy_text()
    return PolicyResponse(
        version=POLICY_VERSION,
        text=text,
        sha256_hex=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


@router.get("/face-recognition", response_model=ConsentStatus)
async def get_status(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStatus:
    rec = await _latest_consent(session, user.id)
    if rec is None:
        return ConsentStatus(kind=CONSENT_KIND, state="NONE")
    return ConsentStatus(
        kind=rec.consent_kind,
        state=rec.state,
        granted_at=rec.granted_at,
        expires_at=rec.expires_at,
        policy_version=rec.policy_version,
    )


@router.post(
    "/face-recognition/grant",
    response_model=ConsentStatus,
    status_code=status.HTTP_201_CREATED,
)
async def grant(
    payload: GrantPayload,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStatus:
    if not (payload.consent_collection and payload.consent_retention):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Both consent boxes must be checked.",
        )
    if not payload.signature_text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Signature is required.")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=RETENTION_DAYS)

    rec = ConsentRecord(
        user_id=user.id,
        consent_kind=CONSENT_KIND,
        state="GRANTED",
        policy_version=POLICY_VERSION,
        policy_text_sha256=_policy_sha256(),
        signature_text=payload.signature_text.strip(),
        ip=str(request.client.host) if request.client else None,
        user_agent=request.headers.get("user-agent"),
        granted_at=now,
        expires_at=expires,
    )
    session.add(rec)

    session.add(
        AuditLog(
            user_id=user.id,
            action="consent.face_recognition.grant",
            details={
                "policy_version": POLICY_VERSION,
                "expires_at": expires.isoformat(),
            },
        )
    )

    await session.commit()
    await session.refresh(rec)

    return ConsentStatus(
        kind=rec.consent_kind,
        state=rec.state,
        granted_at=rec.granted_at,
        expires_at=rec.expires_at,
        policy_version=rec.policy_version,
    )


@router.post("/face-recognition/withdraw", response_model=ConsentStatus)
async def withdraw(
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStatus:
    """Withdraw consent and HARD-DELETE all biometric rows + crop blobs."""
    # 1. Collect crop blob keys before we drop the rows.
    crop_keys = (
        await session.execute(
            select(FaceDetection.crop_blob_key)
            .where(
                FaceDetection.user_id == user.id,
                FaceDetection.crop_blob_key.is_not(None),
            )
        )
    ).scalars().all()

    # 2. Delete in dependency order: face_detections -> faces -> persons.
    fd_count = (
        await session.execute(
            delete(FaceDetection).where(FaceDetection.user_id == user.id)
        )
    ).rowcount or 0
    face_count = (
        await session.execute(delete(Face).where(Face.user_id == user.id))
    ).rowcount or 0
    person_count = (
        await session.execute(delete(Person).where(Person.user_id == user.id))
    ).rowcount or 0

    # 3. Delete blobs from MinIO (best-effort).
    blob_deleted = 0
    for key in crop_keys:
        try:
            storage.delete(storage.bucket_faces, key)
            blob_deleted += 1
        except Exception as exc:
            logger.warning("face crop delete failed for %s: %s", key, exc)

    # 4. Append the WITHDRAWN row.
    rec = ConsentRecord(
        user_id=user.id,
        consent_kind=CONSENT_KIND,
        state="WITHDRAWN",
        policy_version=POLICY_VERSION,
        policy_text_sha256=_policy_sha256(),
        signature_text=None,
        ip=str(request.client.host) if request.client else None,
        user_agent=request.headers.get("user-agent"),
        granted_at=datetime.now(timezone.utc),
        expires_at=None,
    )
    session.add(rec)

    # 5. Audit-log the deletion proof.
    session.add(
        AuditLog(
            user_id=user.id,
            action="consent.face_recognition.withdraw",
            details={
                "face_detections_deleted": int(fd_count),
                "faces_deleted": int(face_count),
                "persons_deleted": int(person_count),
                "crop_blobs_deleted": blob_deleted,
            },
        )
    )

    await session.commit()
    await session.refresh(rec)

    return ConsentStatus(
        kind=rec.consent_kind,
        state=rec.state,
        granted_at=rec.granted_at,
        expires_at=rec.expires_at,
        policy_version=rec.policy_version,
    )


# ---------- C4 generic per-scope consent ----------
#
# These are simpler than face-recognition: no signed-statement step
# (face_recognition is BIPA-grade; the others aren't), retention horizon
# matches the legacy 3-year for predictability, and the policy text
# hash points at a generic policy file (face_recognition_v1.md kept as
# the canonical reference until a per-scope policy lands).


class ScopesStatus(BaseModel):
    """Map of scope → state for the FE consent panel."""

    states: dict[str, str]


@router.get("/scopes", response_model=ScopesStatus)
async def get_scope_states(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ScopesStatus:
    """One-shot view of every supported scope's current state.

    A simpler shape than calling /consent/{kind} N times — used by the
    Account → Privacy panel so a single render shows every toggle.
    """
    out: dict[str, str] = {}
    for kind in SUPPORTED_SCOPES:
        rec = await _latest_consent(session, user.id, kind=kind)
        out[kind] = rec.state if rec else "NONE"
    return ScopesStatus(states=out)


@router.get("/{kind}", response_model=ConsentStatus)
async def get_scope_status(
    kind: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStatus:
    _validate_scope(kind)
    rec = await _latest_consent(session, user.id, kind=kind)
    if rec is None:
        return ConsentStatus(kind=kind, state="NONE")
    return ConsentStatus(
        kind=rec.consent_kind,
        state=rec.state,
        granted_at=rec.granted_at,
        expires_at=rec.expires_at,
        policy_version=rec.policy_version,
    )


@router.post(
    "/{kind}/grant",
    response_model=ConsentStatus,
    status_code=status.HTTP_201_CREATED,
)
async def grant_scope(
    kind: str,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStatus:
    _validate_scope(kind)
    if kind == "face_recognition":
        # Force callers through the BIPA-grade `/consent/face-recognition/grant`
        # route — that path enforces the signed-statement payload.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Use /consent/face-recognition/grant for face_recognition.",
        )
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=RETENTION_DAYS)
    rec = ConsentRecord(
        user_id=user.id,
        consent_kind=kind,
        state="GRANTED",
        policy_version=POLICY_VERSION,
        policy_text_sha256=_policy_sha256(),
        signature_text=None,
        ip=str(request.client.host) if request.client else None,
        user_agent=request.headers.get("user-agent"),
        granted_at=now,
        expires_at=expires,
    )
    session.add(rec)
    session.add(
        AuditLog(
            user_id=user.id,
            action=f"consent.{kind}.grant",
            details={"policy_version": POLICY_VERSION},
        )
    )
    await session.commit()
    await session.refresh(rec)
    return ConsentStatus(
        kind=rec.consent_kind,
        state=rec.state,
        granted_at=rec.granted_at,
        expires_at=rec.expires_at,
        policy_version=rec.policy_version,
    )


@router.post("/{kind}/withdraw", response_model=ConsentStatus)
async def withdraw_scope(
    kind: str,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentStatus:
    _validate_scope(kind)
    if kind == "face_recognition":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Use /consent/face-recognition/withdraw for face_recognition.",
        )

    # Per-scope cascade. Add new branches here when additional scopes
    # gain side-effect data. The audit log captures the row counts so a
    # later auditor can prove the deletion happened.
    deletion_counts: dict[str, int] = {}
    if kind == "gps_retention":
        n = (
            await session.execute(
                delete(ImageGeo).where(ImageGeo.user_id == user.id)
            )
        ).rowcount or 0
        deletion_counts["image_geo_deleted"] = int(n)
    # ai_summary / semantic_search / bandit_compression_telemetry are
    # left for follow-up — the cascade requires touching summary text /
    # CLIP embeddings / bandit_state, which is C4-scoped work.

    rec = ConsentRecord(
        user_id=user.id,
        consent_kind=kind,
        state="WITHDRAWN",
        policy_version=POLICY_VERSION,
        policy_text_sha256=_policy_sha256(),
        signature_text=None,
        ip=str(request.client.host) if request.client else None,
        user_agent=request.headers.get("user-agent"),
        granted_at=datetime.now(timezone.utc),
        expires_at=None,
    )
    session.add(rec)
    session.add(
        AuditLog(
            user_id=user.id,
            action=f"consent.{kind}.withdraw",
            details=deletion_counts,
        )
    )
    await session.commit()
    await session.refresh(rec)
    return ConsentStatus(
        kind=rec.consent_kind,
        state=rec.state,
        granted_at=rec.granted_at,
        expires_at=rec.expires_at,
        policy_version=rec.policy_version,
    )
