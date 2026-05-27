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
    """Resolve the client IP for rate-limit / lockout / audit keying.

    We only trust `X-Forwarded-For` / `X-Real-IP` headers when the
    deployment opts in via `TRUST_PROXY_HEADERS`. Otherwise, any
    attacker can spoof those headers when the API is reachable
    directly (misconfigured ingress, internal port exposed) and
    bypass every per-IP control — auth lockout, upload count cap,
    share-claim throttle. The default is "off"; operators behind a
    real reverse proxy (Caddy, nginx, CDN) flip it on knowing the
    proxy strips and re-sets the header.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # First entry in the comma-separated chain is the original
            # client (per RFC 7239). Strip any port suffix some proxies
            # append (`1.2.3.4:5678`).
            first = forwarded.split(",", 1)[0].strip()
            return first.rsplit(":", 1)[0] if first.count(":") == 1 and "." in first else first
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
    # Stricter JWT_SECRET check. The old "startswith('dev-only')"
    # filter only caught the exact compose default; setting
    # `JWT_SECRET=""` or `JWT_SECRET="secret"` or any short string
    # bypassed it. Reject anything: blank, a known weak default, OR
    # shorter than 32 chars (Argon2id needs entropy + a real
    # rotation policy makes this a useful floor).
    _weak_jwt = {
        "", "secret", "changeme", "change-me", "test", "jwt-secret",
        "dev", "dev-secret", "development",
    }
    if (
        not settings.jwt_secret
        or settings.jwt_secret.startswith("dev-only")
        or settings.jwt_secret.lower() in _weak_jwt
        or len(settings.jwt_secret) < 32
    ):
        errors.append(
            "JWT_SECRET must be a strong (>=32 char) rotated value outside "
            "dev/test — not the compose default, not blank, not a known weak value."
        )
    # Audit CR-8 — weak DB credential rejection. The dev compose
    # falls back to `neuthek:neuthek@postgres:5432/neuthek` (and the
    # historical `istore:istore@…` for operators on older .env
    # files); without this guard a production deploy could ship
    # with either weak default if the operator forgot to set
    # POSTGRES_PASSWORD in .env. We parse the credential out of the
    # async DSN (and the sync DSN — alembic uses it) and reject the
    # known-weak set, including both the new and legacy product names.
    from urllib.parse import urlparse  # local import — only needed in this validator path

    _weak_db_passwords = {
        "", "neuthek", "istore", "postgres", "password", "changeme",
        "change-me", "admin", "root", "test", "secret",
    }
    for label, dsn in (
        ("DATABASE_URL", settings.database_url),
        ("DATABASE_URL_SYNC", settings.database_url_sync),
    ):
        try:
            password = urlparse(dsn).password or ""
        except Exception:
            password = ""
        if password.lower() in _weak_db_passwords:
            errors.append(
                f"{label} carries a known-weak password — reject the "
                "compose defaults (`neuthek`, legacy `istore`, blank, "
                "or any of `postgres` / `password` / `changeme`). Set "
                "POSTGRES_PASSWORD to a rotated random secret in .env "
                "before deploying."
            )
            break  # one message is enough; both DSNs share the same root cause

    # MinIO access key + secret share the same problem. The compose
    # defaults are `neuthek:neuthekpass` (new) and `istore:istorepass`
    # (legacy); production validators in the rest of this function
    # force MINIO_SECURE=true and the SSE config, so the weak-credential
    # leak would surface in prod logs as "access denied"-shaped errors,
    # but better to refuse the boot.
    _weak_minio_passwords = {
        "", "neuthek", "neuthekpass", "istore", "istorepass",
        "minio", "minioadmin", "password", "changeme", "secret",
    }
    _weak_minio_access_keys = {
        "", "neuthek", "istore", "minio", "minioadmin", "admin",
    }
    if settings.minio_access_key.lower() in _weak_minio_access_keys:
        errors.append(
            "MINIO_ACCESS_KEY is a known-weak compose default — set it "
            "to a rotated value in .env before deploying."
        )
    if settings.minio_secret_key.lower() in _weak_minio_passwords:
        errors.append(
            "MINIO_SECRET_KEY is a known-weak compose default — set it "
            "to a rotated random secret in .env before deploying."
        )

    # Audit CR-10 — gap closures the original validator missed. Each
    # of these is a "production deploy should not boot with the dev
    # default" check. Order: cheap config presence first, then the
    # cross-config consistency check (FRONTEND_BASE_URL vs CORS
    # allowlist), so a misconfigured operator sees the simpler
    # individual errors before the relational one.

    # Reverse-proxy header trust. Without TRUST_PROXY_HEADERS=true,
    # `client_ip()` returns the proxy's address for every request
    # — so per-IP rate-limits, auth-lockout counters, and audit
    # `details.ip` all key to the proxy. Indistinguishable from "no
    # rate limit" in practice.
    if not settings.trust_proxy_headers:
        errors.append(
            "TRUST_PROXY_HEADERS must be true outside dev/test — "
            "production sits behind a reverse proxy (Caddy/nginx/CF) "
            "that strips + re-sets X-Forwarded-For. Without this, "
            "per-IP rate limits and lockout counters all key on the "
            "proxy's address."
        )

    # Rate limits + auth lockout. `SECURITY_RATE_LIMITS_ENABLED=false`
    # would silently no-op every limit set by enforce_rate_limit and
    # by SecurityControlsMiddleware. Easy to flip during a "let me
    # just test something" pass and forget to flip back.
    if not settings.security_rate_limits_enabled:
        errors.append(
            "SECURITY_RATE_LIMITS_ENABLED must be true outside "
            "dev/test — rate-limits + auth-lockout silently no-op "
            "when this is false."
        )

    # JWT lifetime upper bound. A 30-day session JWT is a 30-day blast
    # radius on theft because we don't yet have token-version-based
    # revocation (audit finding A8). 24h is the default; cap at 7d
    # so an operator can extend modestly but not catastrophically.
    _max_jwt_lifetime = 7 * 24 * 60 * 60  # 7 days in seconds
    if settings.jwt_lifetime_seconds <= 0:
        errors.append(
            "JWT_LIFETIME_SECONDS must be positive — a non-positive "
            "value would mint already-expired tokens."
        )
    elif settings.jwt_lifetime_seconds > _max_jwt_lifetime:
        errors.append(
            f"JWT_LIFETIME_SECONDS={settings.jwt_lifetime_seconds} is "
            f"longer than the {_max_jwt_lifetime} s (7 d) production "
            "ceiling. Without token-version revocation (audit A8) a "
            "stolen JWT stays valid for the full lifetime; cap the "
            "blast radius."
        )

    # Account-delete grace window. A zero / negative grace = immediate
    # hard-delete with no operator-cancel window. The schedule_delete
    # endpoint refuses to schedule when grace is non-positive, so a
    # 0 setting bricks the account-delete flow entirely.
    if settings.account_delete_grace_days <= 0:
        errors.append(
            "ACCOUNT_DELETE_GRACE_DAYS must be > 0 — a non-positive "
            "value disables the user-cancel window on a scheduled "
            "deletion and breaks /account/schedule-delete."
        )

    # Google SSO consistency. If either OAuth credential is set, both
    # MUST be (a half-configured client lets /auth/google/login 302
    # to Google with an invalid client_id; Google rejects with an
    # opaque error the user sees).
    has_google_id = bool(settings.google_oauth_client_id)
    has_google_secret = bool(settings.google_oauth_client_secret)
    if has_google_id != has_google_secret:
        errors.append(
            "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET "
            "must be set together (or both empty to disable SSO). "
            "Half-configuring lands users on a Google error page."
        )

    # CORS / FRONTEND_BASE_URL alignment. The CORS allowlist lives in
    # `backend.app.create_app` as a hardcoded tuple; production
    # deployments that set FRONTEND_BASE_URL to a real prod hostname
    # need that hostname to also appear in the allowlist or every
    # credentialed request from the SPA 502s with a CORS error.
    # Import lazily so we don't pull app-construction code into the
    # validator's module-import time.
    try:
        from backend.app import ALLOWED_ORIGINS
        cors_allowed = ALLOWED_ORIGINS
    except Exception:  # pragma: no cover — best-effort hint
        cors_allowed = None
    if cors_allowed is not None:
        fe_origin = settings.frontend_base_url.rstrip("/")
        normalized = {o.rstrip("/") for o in cors_allowed}
        if fe_origin and fe_origin not in normalized:
            errors.append(
                f"FRONTEND_BASE_URL={fe_origin!r} is not in the CORS "
                "allowlist (backend.app.create_app's allowed_origins). "
                "Every credentialed FE request will fail CORS. Add "
                "the prod hostname to the allowlist."
            )

    # Stripe webhook consistency. The /billing/webhook endpoint
    # verifies an `Stripe-Signature` header against
    # STRIPE_WEBHOOK_SECRET. Misconfigured = the endpoint silently
    # accepts forged events (or rejects every legit one); either
    # mode is a billing-state corruption risk.
    if settings.stripe_secret_key and not settings.stripe_webhook_secret:
        errors.append(
            "STRIPE_SECRET_KEY is set without STRIPE_WEBHOOK_SECRET — "
            "/billing/webhook can't verify Stripe signatures. Either "
            "set the webhook secret or unset the API key to disable "
            "billing."
        )

    # SMTP host required when production needs verification / reset /
    # recovery emails. Empty smtp_host makes email_send fall back to
    # writing the full message (including reset-token URLs and
    # plaintext recovery codes) to stderr → log aggregator. Audit
    # finding A4.
    if not settings.smtp_host:
        errors.append(
            "SMTP_HOST is required outside dev/test — without it, "
            "email_send writes verification / reset / recovery-code "
            "bodies to stderr where any log reader can mint sessions."
        )

    if settings.secret_manager in {"", "env_file"}:
        errors.append("SECRET_MANAGER must be docker_secrets or a platform secret manager.")
    if settings.postgres_at_rest_encryption != "host_volume_confirmed":
        errors.append("POSTGRES_AT_REST_ENCRYPTION=host_volume_confirmed is required.")
    if settings.minio_sse_mode not in {"sse-s3", "sse-kms"}:
        errors.append("MINIO_SSE_MODE must be sse-s3 or sse-kms outside dev/test.")
    if settings.minio_sse_mode == "sse-kms":
        # When KMS is enabled, both key IDs MUST be set — falling back
        # to SseS3 silently for the biometric scope defeats the §A2
        # "separate keys for biometric vs. content" requirement and
        # the operator would never notice the drift from the dashboard.
        if not settings.minio_sse_kms_key_id_content:
            errors.append(
                "MINIO_SSE_KMS_KEY_ID_CONTENT is required when MINIO_SSE_MODE=sse-kms."
            )
        if not settings.minio_sse_kms_key_id_biometric:
            errors.append(
                "MINIO_SSE_KMS_KEY_ID_BIOMETRIC is required when MINIO_SSE_MODE=sse-kms."
            )
        if (
            settings.minio_sse_kms_key_id_content
            and settings.minio_sse_kms_key_id_biometric
            and settings.minio_sse_kms_key_id_content == settings.minio_sse_kms_key_id_biometric
        ):
            errors.append(
                "MINIO_SSE_KMS_KEY_ID_CONTENT and MINIO_SSE_KMS_KEY_ID_BIOMETRIC must "
                "differ — same-key separation defeats the threat model."
            )
    if not settings.backup_age_recipient:
        errors.append("BACKUP_AGE_RECIPIENT is required for encrypted backups.")
    # CLOUD_ENCRYPTION_KEY is the Fernet key for TOTP secrets +
    # cloud-OAuth refresh tokens. In prod we require the operator to
    # set it explicitly — the secret_box auto-bootstrap is a dev
    # convenience that writes an ephemeral key to /app/.env inside
    # the container, which evaporates on rebuild and renders every
    # piece of ciphertext unreadable. We've hit that bug in dev; we
    # never want to hit it in prod.
    if not settings.cloud_encryption_key:
        errors.append(
            "CLOUD_ENCRYPTION_KEY must be set explicitly (the dev auto-bootstrap "
            "doesn't survive container rebuilds and would orphan every encrypted secret)."
        )
    else:
        try:
            from cryptography.fernet import Fernet  # noqa: PLC0415 — lazy import keeps boot light when cloud creds are off
            Fernet(settings.cloud_encryption_key.encode())
        except Exception:
            errors.append(
                "CLOUD_ENCRYPTION_KEY is not a valid Fernet key "
                "(must be 44-char URL-safe base64). Regenerate with "
                "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`."
            )
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


async def _tier_limits_for(user_id: str) -> tuple[int, int]:
    """Look up the rate limits for the user's current tier.

    Returns (uploads_per_hour, bytes_per_day). Free is the floor;
    Pro/Business override upward. If the subscription row or plan
    row is missing we fall back to the global `upload_max_*` settings
    so existing dev installs (no `plans` table populated yet) keep
    working.

    Implementation note: we look up by `user_id` not by the User
    object so the route handler doesn't have to load extra rows on
    every upload. The query is one SELECT against a two-column
    table joined on PK; the cost is below the noise floor of the
    upload itself.
    """
    try:
        from uuid import UUID

        from sqlalchemy import select

        from backend.db import SessionLocal
        from backend.models import Plan, Subscription

        async with SessionLocal() as session:
            sub = (
                await session.execute(
                    select(Subscription).where(Subscription.user_id == UUID(user_id))
                )
            ).scalar_one_or_none()
            tier = sub.tier if sub is not None else "free"
            plan = (
                await session.execute(select(Plan).where(Plan.tier == tier))
            ).scalar_one_or_none()
            if plan is not None:
                return plan.upload_max_per_hour, plan.upload_max_bytes_per_day
    except Exception:
        # DB unreachable or `plans` not migrated yet — fall through.
        pass
    return settings.upload_max_count_per_hour, settings.upload_max_bytes_per_day


async def enforce_upload_limits(user_id: str, request: Request, byte_count: int) -> None:
    if not settings.security_rate_limits_enabled:
        return
    count_cap, byte_cap = await _tier_limits_for(user_id)
    # Count-per-hour limit is keyed on user_id ONLY so it caps the
    # CPU/processing budget per account. Keying on (user_id, ip) let
    # a single user rotate IPs (VPN, mobile data, Tor) and get N×
    # the budget — defeating the throttle for the exact case it's
    # designed for. Per-IP throttling for anonymous abuse is handled
    # separately by the auth lockout + claim-share rate limiters.
    await enforce_rate_limit(
        key=f"upload:count:{user_id}",
        limit=count_cap,
        window_seconds=3600,
        detail="Upload rate limit exceeded for your plan",
    )
    await enforce_rate_limit(
        key=f"upload:bytes:{user_id}",
        limit=byte_cap,
        window_seconds=24 * 3600,
        amount=byte_count,
        detail="Daily upload byte limit exceeded for your plan",
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
        # /auth/cookie/login is the browser FE's primary login route.
        # Before this entry existed the cookie-flavored login was the
        # ONLY public auth endpoint with no rate-limit and no audit row
        # — the dispatch() short-circuit at `path in self._AUTH_PATHS`
        # treated it as a non-auth route. The credential-attempt
        # check at the bottom of dispatch() already matches via
        # `path.endswith("/login")`, so adding the path here is the
        # only change required to get per-IP burst + per-identity
        # lockout + `auth.login.succeeded`/`failed` audit rows for
        # browser logins.
        "/auth/cookie/login",
        # §1.2.2 — TOTP login endpoint. Same per-IP burst + lockout
        # policy as the password path so a TOTP-enabled account can't
        # be code-guessed.
        "/auth/jwt/login-totp",
        "/auth/forgot-password",
        "/auth/reset-password",
        "/auth/request-verify-token",
        # §C6b — /auth/verify consumes the email-verification JWT.
        # It's the path an attacker would hammer with a leaked-token
        # list, so it needs the same per-IP burst + per-identity
        # lockout as /auth/reset-password.
        "/auth/verify",
        # Magic-link sign-in. /request would be mailbox-spam fuel
        # without rate limiting; /consume is the path an attacker
        # would hammer with a leaked-token list. /consume-code is
        # the brute-force surface for the 6-digit code (1M space,
        # 15-min TTL) — locked down here so an attacker can't run
        # through the space faster than the TTL expires.
        "/auth/email-link/request",
        "/auth/email-link/consume",
        "/auth/email-link/consume-code",
        "/account/recovery-codes/login",
        # todo §1.1 / G1 — share-claim is the same threat shape as
        # recovery-codes-login (POST + secret token + identity binding),
        # so it gets the same per-IP burst + 24h failure-lockout policy.
        "/shares/claim",
    }
    # Auth-style protection on GET endpoints that take a secret in the
    # URL. Keyed by prefix because the path includes the share token.
    # Per-IP rate limiting still applies via the route-level
    # `enforce_rate_limit` call inside `preview_share`; this entry only
    # extends the lockout machinery (which is identity-based).
    _AUTH_PATH_PREFIXES = ("/shares/preview/",)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        is_post_auth = request.method == "POST" and path in self._AUTH_PATHS
        is_get_share_preview = (
            request.method == "GET"
            and any(path.startswith(p) for p in self._AUTH_PATH_PREFIXES)
        )
        if (
            not settings.security_rate_limits_enabled
            or settings.app_env.lower() == "test"
            or not (is_post_auth or is_get_share_preview)
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

        # Audit A5 — lockout key now includes BOTH identity and IP so
        # an attacker on one IP can't lock the real victim out from
        # every other IP. The previous key `auth:lock:{path}:{identity}`
        # gave any attacker who knew a target email a 60s–15min DoS
        # lever against named users (admins, public personas, the
        # sole owner of a self-hosted instance). Per-IP burst rate
        # limit at line 585 still catches single-IP brute force;
        # distributed brute force needs a separate CAPTCHA/WAF layer
        # (out of audit scope).
        #
        # When `identity` is empty (no email in body), we fall back to
        # ip-only — same as before — because there's nothing to scope
        # on. That's the share-preview path, where the ip-only key
        # IS the intended unit.
        lock_scope = f"{identity}:{ip}" if identity else ip
        lock_key = f"auth:lock:{request.url.path}:{lock_scope}"
        fail_key = f"auth:fail:{request.url.path}:{lock_scope}"
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
        # Treat any failed attempt against an "auth-shaped" endpoint as
        # a credential-guess that should count toward lockout, not just
        # /login. Share-claim and share-preview both take a secret in
        # the request and need the same brute-force pressure relief.
        # NOTE: `.endswith("/login")` previously excluded `/login-totp`,
        # which meant failed 6-digit TOTP attempts didn't increment the
        # lockout counter — a real brute-force loophole. The explicit
        # path check covers it now.
        is_credential_attempt = (
            request.url.path.endswith("/login")
            or request.url.path == "/auth/jwt/login-totp"
            or request.url.path == "/account/recovery-codes/login"
            or is_get_share_preview
            or request.url.path == "/shares/claim"
        )
        if is_credential_attempt and response.status_code >= 400:
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


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Reject mutating requests that arrive with our auth cookie but
    a cross-origin `Origin` header.

    Modern browsers default the auth cookie's SameSite=Lax behavior
    to "cookies don't ride along on cross-origin POSTs", which by
    itself blocks the basic CSRF surface. This middleware is
    defence-in-depth for the (small) set of browsers / clients that
    handle SameSite incorrectly or where a future framework choice
    relaxes it. The check:

        if method is mutating
           AND the auth cookie is present
           AND Origin (or Referer if Origin missing) doesn't match
               one of our explicit allowed origins
        → 403 with `csrf_origin_blocked`

    Bearer-token callers (no cookie) skip the check entirely — the
    Authorization header is impossible to forge from another origin
    without a CORS preflight that we control. The login endpoint
    itself is also exempt: it always crosses the no-cookie boundary
    (you're TRYING to set the cookie), so an Origin requirement
    there is solved by CORS, not CSRF.

    The allow-list is derived from `settings.csrf_allowed_origins`,
    defaulting to the CORS allowlist (since we already trust those
    origins to make credentialed requests).
    """

    _SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    # These paths intentionally don't require a CSRF-Origin match.
    # `/auth/cookie/login` is the moment the cookie is BEING SET —
    # the request can't carry it yet. Health endpoints are
    # unauthenticated. `/auth/jwt/*` uses Bearer not cookies.
    _EXEMPT_PREFIXES = (
        "/auth/cookie/login",
        "/auth/cookie/logout",  # idempotent + needs to work cross-origin from logout buttons
        "/auth/jwt/",
        "/auth/forgot-password",
        "/auth/reset-password",
        "/auth/request-verify-token",
        "/auth/verify",
        "/auth/register",
        "/auth/google/",
        # §H#7 — magic-link passwordless sign-in. Same "moment the
        # cookie is being set" exemption as /auth/cookie/login: the
        # consume endpoints are public POSTs that bootstrap the
        # session, so the incoming request can't carry the cookie
        # yet (the user hasn't logged in). Without this, a SECOND
        # POST to /consume after the first one set a cookie (e.g.
        # the user accidentally double-clicks) hits CSRF instead
        # of the proper "already used" 400.
        "/auth/email-link/",
        # §C6c — recovery-code login. Same shape: public POST that
        # mints a session for users who can't log in normally.
        "/account/recovery-codes/login",
        "/health",
        "/shares/preview/",  # public share endpoints
        "/shares/claim",
    )

    def __init__(self, app, *, allowed_origins: tuple[str, ...]) -> None:
        super().__init__(app)
        # Normalise to scheme://host[:port] (no path, no trailing slash)
        # so the comparison against the Origin header is exact-match.
        self._allowed = tuple(o.rstrip("/") for o in allowed_origins)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        method = request.method.upper()
        if method in self._SAFE_METHODS:
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(p) for p in self._EXEMPT_PREFIXES):
            return await call_next(request)
        # Only enforce when the request is authenticated via the
        # auth cookie — Bearer-token requests are exempt because the
        # Authorization header can't be set from another origin
        # without a preflight, which CORS already gates.
        from backend.auth.users import COOKIE_NAME  # late import to avoid cycle
        if COOKIE_NAME not in request.cookies:
            return await call_next(request)
        origin = request.headers.get("origin") or ""
        if not origin:
            # Some browsers omit Origin on same-origin POSTs. Fall
            # back to Referer in that case — it's less reliable but
            # still distinguishes another tab on attacker.example
            # from the same page on our origin.
            referer = request.headers.get("referer") or ""
            if referer:
                try:
                    from urllib.parse import urlparse
                    p = urlparse(referer)
                    if p.scheme and p.netloc:
                        origin = f"{p.scheme}://{p.netloc}"
                except Exception:
                    origin = ""
        if origin.rstrip("/") not in self._allowed:
            return JSONResponse(
                {"detail": "csrf_origin_blocked"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add baseline security headers to every response.

    Before this middleware landed, security headers (CSP / HSTS /
    X-Frame-Options / X-Content-Type-Options / Referrer-Policy /
    Permissions-Policy) were ONLY set by the production Caddy
    reverse proxy. Any deployment that didn't front the API with
    Caddy — internal LAN, ngrok-tunnel, the dev compose, a direct
    `uvicorn` for debugging — shipped responses with no headers at
    all, leaving every browser at the most permissive defaults.

    We set conservative values here so they're always present, then
    let the Caddy layer override them in prod if the operator wants
    different policy. The HSTS header is omitted in plain-HTTP
    contexts so that a dev install on localhost doesn't accidentally
    pin the browser to HTTPS for a port it can't serve.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        # Static defaults. CSP is restrictive because our API only
        # serves JSON / opaque media bytes — no inline HTML, no
        # script execution, no `<iframe>` embedding. The frontend
        # is served from a separate origin and has its own CSP via
        # the index.html meta + Caddy.
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=()",
        )
        # CSP: every API response is JSON or an opaque media blob —
        # nothing should ever be parsed as HTML. `default-src 'none'`
        # is the safest possible default; the frontend never reads
        # these headers and the browser only enforces them on HTML
        # navigations, so this is purely defence-in-depth for the
        # rare misconfigured client that interprets bytes as HTML.
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        # HSTS only on HTTPS contexts. Setting it on HTTP would be a
        # browser no-op anyway, but adding the header on plain HTTP
        # is confusing to anyone debugging.
        if request.url.scheme == "https":
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )
        return response
