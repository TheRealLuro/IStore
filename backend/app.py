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
from backend.api.two_factor import auth_router as two_factor_auth_router
from backend.api.two_factor import router as two_factor_router
from backend.auth.google_sso import router as google_sso_router
from backend.auth.users import auth_backend, fastapi_users
from backend.config import settings
from backend.consent import router as consent_router
from backend.context import reset_current_user_id, set_current_user_id
from backend.db import engine
from backend.schemas import UserCreate, UserRead, UserUpdate
from backend.security import SecurityControlsMiddleware, validate_production_settings
from backend.storage import storage


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

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="neuthek",
        version="0.1.0",
        description="AI-driven personal storage with privacy-compliant face recognition.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "tauri://localhost",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Original-Expired"],
    )
    app.add_middleware(SecurityControlsMiddleware)

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
