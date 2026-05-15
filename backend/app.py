from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.api.account import admin_router as admin_router
from backend.api.account import router as account_router
from backend.api.admin import router as admin_dashboard_router
from backend.api.cloud import router as cloud_router
from backend.api.faces import router as faces_router
from backend.api.feedback import router as feedback_router
from backend.api.folders import router as folders_router
from backend.api.images import router as images_router
from backend.api.people import router as people_router
from backend.api.search import router as search_router
from backend.api.shares import router as shares_router
from backend.api.storage import router as storage_router
from backend.api.two_factor import auth_router as two_factor_auth_router
from backend.api.two_factor import router as two_factor_router
from backend.auth.users import auth_backend, fastapi_users
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
    yield


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

    return app


app = create_app()
