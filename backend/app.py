from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.api.account import admin_router as admin_router
from backend.api.account import router as account_router
from backend.api.admin import router as admin_dashboard_router
from backend.api.billing import router as billing_router
from backend.api.cloud import router as cloud_router
from backend.api.comments import router as comments_router
from backend.api.faces import router as faces_router
from backend.api.feedback import router as feedback_router
from backend.api.folders import router as folders_router
from backend.api.images import router as images_router
from backend.api.people import router as people_router
from backend.api.search import router as search_router
from backend.api.shares import router as shares_router
from backend.api.storage import router as storage_router
from backend.api.tags import folder_attach_router as tag_folder_attach_router
from backend.api.tags import image_attach_router as tag_image_attach_router
from backend.api.tags import router as tags_router
from backend.api.email_link import router as email_link_router
from backend.api.two_factor import auth_router as two_factor_auth_router
from backend.api.two_factor import router as two_factor_router
from backend.auth.google_sso import router as google_sso_router
from backend.auth.users import auth_backend, cookie_auth_backend, fastapi_users
from backend.config import settings
from backend.consent import router as consent_router
from backend.context import reset_current_user_id, set_current_user_id
from backend.db import engine
from backend.schemas import UserCreate, UserRead, UserUpdate
from backend.security import (
    CsrfOriginMiddleware,
    SecurityControlsMiddleware,
    SecurityHeadersMiddleware,
    validate_production_settings,
)
from backend.storage import storage


# Single source of truth for "origins we trust to make credentialed
# requests against this API." Module-level so `validate_production_
# settings` can cross-check against FRONTEND_BASE_URL at boot, AND
# so `create_app()` and the CSRF middleware read the same list.
# Adding a new frontend origin = add it here once.
ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "tauri://localhost",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await validate_production_settings()
    storage.ensure_buckets()
    # §C2 — hourly cloud-sync sweep. Pulls every active CloudLink in
    # the background while the API serves traffic. Disabled when
    # `CLOUD_SYNC_HOURLY_ENABLED=false` so test/dev runs don't fire
    # outbound HTTP from pytest, and when the OAuth credentials
    # aren't configured (the worker wakes up, sees no eligible
    # links, and goes back to sleep).
    import asyncio

    from backend.cloud_sync_worker import run_hourly_sweep

    task: asyncio.Task | None = None
    if settings.cloud_sync_hourly_enabled:
        task = asyncio.create_task(run_hourly_sweep())

    # Pre-warm the CLIP text encoder so the first /search?q=... request
    # doesn't pay the 5-10 s cold-load tax. We saw a real "matrix math"
    # query take 9.6 s end-to-end where ~9 s was just torch loading
    # OpenCLIP weights into memory on first call. The warmup runs in a
    # background thread so it doesn't block startup (API serves requests
    # while the model loads — search-only queries that arrive before
    # warmup finishes still pay the cold-load cost, but anything else
    # comes up immediately). Wrapped in a broad except so an [ml]-less
    # build doesn't 503 on startup.
    async def _prewarm_clip():
        try:
            from backend.vision.runtime import encode_text_cached
            await asyncio.to_thread(encode_text_cached, "warm")
        except Exception:
            pass
    asyncio.create_task(_prewarm_clip())

    # §B4 — daily retention sweeper. Before this lived in the
    # lifespan the 30-day original-TTL sweep was only reachable via
    # the superuser endpoint, which meant in practice originals
    # accumulated forever and storage cost ballooned. We now run
    # `sweep_expired_originals` on a 24h tick. Sweeper is idempotent
    # and a no-op when nothing is expired, so the cost on a healthy
    # install is one Postgres query per day.
    from backend.db import SessionLocal
    from backend.retention import (
        sweep_audit_log_anonymize,
        sweep_expired_originals,
        sweep_expired_quarantine,
        sweep_feedback_events,
        sweep_orphan_blobs,
        sweep_scheduled_account_deletes,
    )

    async def _retention_loop():
        # Wait a minute on first boot so we don't compete with the
        # CLIP warmup + cloud-sync sweep for DB connections during
        # the first second of life. Then run all five sweepers
        # serially every 24 h. Each one writes its own audit row;
        # any failure is logged + the loop continues to the next.
        # Before this round only `sweep_expired_originals` was wired
        # to a daily tick — quarantine bytes / consumed feedback
        # rows / aged audit-log rows / scheduled-delete accounts
        # accumulated indefinitely because no cron ever ran them.
        import logging
        log = logging.getLogger(__name__)
        await asyncio.sleep(60)
        while True:
            for name, fn in (
                ("originals", sweep_expired_originals),
                ("quarantine", sweep_expired_quarantine),
                ("feedback", sweep_feedback_events),
                ("audit_anonymize", sweep_audit_log_anonymize),
                ("account_deletes", sweep_scheduled_account_deletes),
                # Orphan-blob sweep walks the MinIO buckets and deletes
                # objects no Image row points at. Catches the leak from
                # failed uploads / re-syncs where the blob was written
                # before the DB row commit, AND any blob a prior delete
                # didn't fully clean up. Runs last because it's the
                # slowest (one full bucket listing per call) and we want
                # the cheaper sweeps to land their audit rows first.
                ("orphans", sweep_orphan_blobs),
            ):
                try:
                    async with SessionLocal() as s:
                        res = await fn(s)
                    log.info("retention.%s: %s", name, res)
                except Exception:
                    log.exception(
                        "retention.%s: sweep crashed — continuing", name,
                    )
            await asyncio.sleep(24 * 60 * 60)

    retention_task = asyncio.create_task(_retention_loop())

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        retention_task.cancel()
        try:
            await retention_task
        except (asyncio.CancelledError, Exception):
            pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="neuthek",
        version="0.1.0",
        description="AI-driven personal storage with privacy-compliant face recognition.",
        lifespan=lifespan,
    )

    # Resolved at app-construction time from the module-level
    # constant declared below. Kept as a local alias so the existing
    # middleware-wiring code below reads unchanged.
    allowed_origins = ALLOWED_ORIGINS
    # Middleware order matters (and is counterintuitive).
    # `add_middleware` does `user_middleware.insert(0, …)`, so the
    # LAST middleware added becomes the OUTERMOST wrapper. The
    # OUTERMOST sees every response on the way out, including
    # 403/401 short-circuits from middlewares INSIDE it.
    #
    # We want CORS to be OUTERMOST so its `Access-Control-Allow-Origin`
    # header lands on every response — including the 403s from
    # CsrfOriginMiddleware. Previously CORS was added first (=
    # innermost), so a Csrf 403 returned WITHOUT CORS headers and
    # the browser surfaced it as "blocked by CORS policy" with no
    # Allow-Origin header, obscuring the real error.
    #
    # Correct order:
    #   1. SecurityControlsMiddleware  (innermost — closest to router)
    #   2. SecurityHeadersMiddleware
    #   3. CsrfOriginMiddleware
    #   4. CORSMiddleware              (outermost — added last)
    app.add_middleware(SecurityControlsMiddleware)
    # Always-on baseline headers (CSP / HSTS / X-Frame / etc.). Sits
    # between Security and Csrf so its headers land on every response,
    # including Csrf 403s and Security 429s.
    app.add_middleware(SecurityHeadersMiddleware)
    # CSRF defence-in-depth: reject mutating requests that present
    # our auth cookie but originate from an Origin we don't trust.
    # See backend/security.py::CsrfOriginMiddleware for the exempt
    # path list (login, health, public share routes).
    app.add_middleware(CsrfOriginMiddleware, allowed_origins=tuple(allowed_origins))
    # CORS added LAST so it's the OUTERMOST wrapper. Every response
    # (including Csrf 403, Security 429, anything from the router)
    # goes through CORS on the way out and picks up the
    # `Access-Control-Allow-Origin` header. Without this ordering,
    # the browser sees responses without the CORS header and
    # surfaces them as generic "blocked by CORS policy" errors
    # that hide the real backend error message.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Original-Expired"],
    )

    # CORS-on-500: FastAPI's default 500 handler is invoked from
    # inside Starlette's exception-handling middleware, which sits
    # INSIDE our CORS layer in the middleware stack. When a route
    # raises an unhandled exception, the 500 response does NOT get
    # the Access-Control-Allow-Origin header attached. Browsers then
    # report the error as "blocked by CORS policy: No
    # 'Access-Control-Allow-Origin' header is present on the
    # requested resource" — which is technically true but
    # completely obscures the real bug. The fix is to override the
    # Exception handler so it sets the CORS headers itself before
    # returning the 500 JSON. Now the browser shows {"detail":
    # "internal_server_error"} and the FE can render a useful toast
    # instead of pretending the server is unreachable.
    @app.exception_handler(Exception)
    async def _cors_aware_500(request, exc):  # noqa: ANN001
        import logging
        from fastapi.responses import JSONResponse
        logging.getLogger("backend").exception(
            "unhandled exception on %s %s: %s",
            request.method, request.url.path, exc,
        )
        origin = request.headers.get("origin", "")
        headers: dict[str, str] = {}
        # Only echo the Origin back if it's in the allowlist —
        # don't reflect arbitrary origins on error responses.
        if origin and origin in allowed_origins:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
            headers["Vary"] = "Origin"
        # Surface the exception class so we can grep logs, but
        # the user-visible body stays generic — we don't want to
        # leak stack-trace fragments to the browser.
        detail = (
            f"server_error: {type(exc).__name__}"
            if settings.app_env.lower() in ("dev", "test", "local")
            else "internal_server_error"
        )
        return JSONResponse(
            status_code=500,
            content={"detail": detail},
            headers=headers,
        )

    @app.middleware("http")
    async def user_context_boundary(request, call_next):  # noqa: ANN001
        token = set_current_user_id(None)
        try:
            return await call_next(request)
        finally:
            reset_current_user_id(token)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/db")
    async def health_db() -> dict[str, str]:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "reachable"}

    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/auth/jwt",
        tags=["auth"],
    )
    # Cookie-based login/logout for browsers (HttpOnly + Secure +
    # SameSite=Lax). Mounted alongside the Bearer JWT routes so
    # programmatic callers keep working; the browser frontend uses
    # /auth/cookie/login. See backend/auth/users.py for cookie attrs.
    app.include_router(
        fastapi_users.get_auth_router(cookie_auth_backend),
        prefix="/auth/cookie",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/auth",
        tags=["auth"],
    )
    # Phase 13 (C6) — forgot-password + email verification flows. These
    # mount POST /auth/forgot-password, POST /auth/reset-password,
    # POST /auth/request-verify-token, POST /auth/verify. UserManager
    # callbacks dispatch the actual transactional mails.
    app.include_router(
        fastapi_users.get_reset_password_router(),
        prefix="/auth",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_verify_router(UserRead),
        prefix="/auth",
        tags=["auth"],
    )
    # Google Sign-In: /auth/google/login → consent screen,
    # /auth/google/callback → JWT in URL fragment.
    app.include_router(google_sso_router)
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )
    app.include_router(images_router)
    app.include_router(shares_router)
    app.include_router(folders_router)
    app.include_router(search_router)
    app.include_router(storage_router)
    app.include_router(consent_router)
    app.include_router(people_router)
    app.include_router(faces_router)
    app.include_router(account_router)
    app.include_router(two_factor_router)
    app.include_router(two_factor_auth_router)
    # Passwordless email-link sign-in: /auth/email-link/request +
    # /auth/email-link/consume. Same anti-enumeration posture as
    # /auth/forgot-password; 2FA-aware on consume.
    app.include_router(email_link_router)
    app.include_router(admin_router)
    app.include_router(admin_dashboard_router)
    app.include_router(cloud_router)
    app.include_router(feedback_router)
    app.include_router(billing_router)
    # §C1.6 — tags CRUD + image/folder attach.
    app.include_router(tags_router)
    app.include_router(tag_image_attach_router)
    app.include_router(tag_folder_attach_router)
    # §G2 — comments on any file (owner or active share recipient).
    app.include_router(comments_router)

    return app


app = create_app()
