from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import parse_qs

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from backend.config import settings


@dataclass
class _Counter:
    value: int
    expires_at: float


_COUNTERS: dict[str, _Counter] = {}
_LOCK = asyncio.Lock()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


async def _redis_client():
    try:
        import redis.asyncio as redis  # type: ignore
    except ImportError:
        return None
    try:
        client = redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return client
    except Exception:
        return None


async def require_redis_when_production() -> None:
    if not settings.is_production:
        return
    if await _redis_client() is None:
        raise RuntimeError(
            "Production rate limiting requires Redis. Install redis and set REDIS_URL."
        )


async def validate_production_settings() -> None:
    if not settings.is_production:
        return
    errors: list[str] = []
    if not settings.minio_secure:
        errors.append("MINIO_SECURE must be true outside dev/test.")
    if settings.frontend_base_url.startswith("http://"):
        errors.append("FRONTEND_BASE_URL must be https outside dev/test.")
    if settings.jwt_secret.startswith("dev-only"):
        errors.append("JWT_SECRET must be rotated outside dev/test.")
    if settings.secret_manager in {"", "env_file"}:
        errors.append("SECRET_MANAGER must be docker_secrets or a platform secret manager.")
    if settings.postgres_at_rest_encryption != "host_volume_confirmed":
        errors.append("POSTGRES_AT_REST_ENCRYPTION=host_volume_confirmed is required.")
    if settings.minio_sse_mode not in {"sse-s3", "sse-kms"}:
        errors.append("MINIO_SSE_MODE must be sse-s3 or sse-kms outside dev/test.")
    if not settings.backup_age_recipient:
        errors.append("BACKUP_AGE_RECIPIENT is required for encrypted backups.")
    await require_redis_when_production()
    if errors:
        raise RuntimeError("Production configuration is unsafe: " + " ".join(errors))


async def increment_window(key: str, window_seconds: int, amount: int = 1) -> int:
    redis = await _redis_client()
    if redis is not None:
        pipe = redis.pipeline()
        pipe.incrby(key, amount)
        pipe.expire(key, window_seconds, nx=True)
        value, _ = await pipe.execute()
        await redis.aclose()
        return int(value)

    now = time.time()
    async with _LOCK:
        row = _COUNTERS.get(key)
        if row is None or row.expires_at <= now:
            row = _Counter(value=0, expires_at=now + window_seconds)
            _COUNTERS[key] = row
        row.value += amount
        return row.value


async def get_counter(key: str) -> int:
    redis = await _redis_client()
    if redis is not None:
        value = await redis.get(key)
        await redis.aclose()
        return int(value or 0)

    now = time.time()
    row = _COUNTERS.get(key)
    if row is None or row.expires_at <= now:
        return 0
    return row.value


async def clear_counter(key: str) -> None:
    redis = await _redis_client()
    if redis is not None:
        await redis.delete(key)
        await redis.aclose()
        return
    _COUNTERS.pop(key, None)


async def _audit_auth_event(
    *,
    action: str,
    path: str,
    ip: str,
    identity: str,
    status_code: int | None = None,
) -> None:
    try:
        from sqlalchemy import select

        from backend.audit import add_audit
        from backend.db import SessionLocal
        from backend.models import User

        async with SessionLocal() as session:
            user_id = None
            if identity:
                row = (
                    await session.execute(
                        select(User.id).where(User.email == identity)
                    )
                ).scalar_one_or_none()
                user_id = row
            await add_audit(
                session,
                user_id=user_id,
                action=action,
                details={
                    "path": path,
                    "ip": ip,
                    "identity": identity or None,
                    "status_code": status_code,
                },
            )
            await session.commit()
    except Exception:
        # Auth must never fail because the audit sink is unavailable.
        return


async def enforce_rate_limit(
    *,
    key: str,
    limit: int,
    window_seconds: int,
    amount: int = 1,
    detail: str = "Rate limit exceeded",
) -> None:
    if not settings.security_rate_limits_enabled:
        return
    value = await increment_window(key, window_seconds, amount)
    if value > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail)


async def enforce_upload_limits(user_id: str, request: Request, byte_count: int) -> None:
    if not settings.security_rate_limits_enabled:
        return
    ip = client_ip(request)
    await enforce_rate_limit(
        key=f"upload:count:{user_id}:{ip}",
        limit=settings.upload_max_count_per_hour,
        window_seconds=3600,
        detail="Upload rate limit exceeded",
    )
    await enforce_rate_limit(
        key=f"upload:bytes:{user_id}",
        limit=settings.upload_max_bytes_per_day,
        window_seconds=24 * 3600,
        amount=byte_count,
        detail="Daily upload byte limit exceeded",
    )


def _extract_identity(path: str, content_type: str, body: bytes) -> str:
    if path.endswith("/login") and "application/x-www-form-urlencoded" in content_type:
        parsed = parse_qs(body.decode("utf-8", errors="ignore"))
        return (parsed.get("username") or [""])[0].lower()
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return ""
    if isinstance(data, dict):
        return str(data.get("email") or data.get("username") or "").lower()
    return ""


class SecurityControlsMiddleware(BaseHTTPMiddleware):
    """Rate-limit public auth endpoints and add lockout after failures.

    Disabled in tests by default so existing integration helpers can create
    many users. Production validation separately requires Redis.
    """

    _AUTH_PATHS = {
        "/auth/jwt/login",
        "/auth/forgot-password",
        "/auth/reset-password",
        "/auth/request-verify-token",
        "/account/recovery-codes/login",
    }

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            not settings.security_rate_limits_enabled
            or settings.app_env.lower() == "test"
            or request.method != "POST"
            or request.url.path not in self._AUTH_PATHS
        ):
            return await call_next(request)

        body = await request.body()
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive  # type: ignore[attr-defined]
        ip = client_ip(request)
        identity = _extract_identity(
            request.url.path, request.headers.get("content-type", ""), body
        )
        auth_key = f"auth:ip:{ip}:{request.url.path}"
        try:
            await enforce_rate_limit(
                key=auth_key,
                limit=settings.auth_rate_limit_per_minute,
                window_seconds=60,
                detail="Too many authentication attempts",
            )
        except HTTPException as exc:
            await _audit_auth_event(
                action="auth.rate_limit",
                path=request.url.path,
                ip=ip,
                identity=identity,
                status_code=exc.status_code,
            )
            return JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code
            )

        lock_key = f"auth:lock:{request.url.path}:{identity or ip}"
        fail_key = f"auth:fail:{request.url.path}:{identity or ip}"
        locked = await get_counter(lock_key)
        if locked:
            await _audit_auth_event(
                action="auth.lockout",
                path=request.url.path,
                ip=ip,
                identity=identity,
                status_code=status.HTTP_423_LOCKED,
            )
            return JSONResponse(
                {"detail": "Account temporarily locked. Try again later."},
                status_code=status.HTTP_423_LOCKED,
            )

        response = await call_next(request)
        if request.url.path.endswith("/login") and response.status_code >= 400:
            await _audit_auth_event(
                action="auth.login.failed",
                path=request.url.path,
                ip=ip,
                identity=identity,
                status_code=response.status_code,
            )
            failures = await increment_window(fail_key, 24 * 3600)
            if failures >= settings.auth_lockout_failures:
                exponent = min(failures - settings.auth_lockout_failures, 4)
                seconds = min(
                    settings.auth_lockout_base_seconds * (2 ** exponent),
                    settings.auth_lockout_max_seconds,
                )
                await increment_window(lock_key, seconds)
                await _audit_auth_event(
                    action="auth.login.locked",
                    path=request.url.path,
                    ip=ip,
                    identity=identity,
                    status_code=status.HTTP_423_LOCKED,
                )
        elif response.status_code < 400:
            await clear_counter(fail_key)
            await clear_counter(lock_key)
            action = (
                "auth.login.succeeded"
                if request.url.path.endswith("/login")
                else "auth.token.succeeded"
            )
            await _audit_auth_event(
                action=action,
                path=request.url.path,
                ip=ip,
                identity=identity,
                status_code=response.status_code,
            )
        return response
