from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.api.images import router as images_router
from backend.api.search import router as search_router
from backend.api.storage import router as storage_router
from backend.auth.users import auth_backend, fastapi_users
from backend.db import engine
from backend.schemas import UserCreate, UserRead, UserUpdate
from backend.storage import storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.ensure_buckets()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="IStore",
        version="0.1.0",
        description="AI-driven image storage with privacy-compliant face recognition.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "tauri://localhost",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Original-Expired"],
    )

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
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )
    app.include_router(images_router)
    app.include_router(search_router)
    app.include_router(storage_router)

    return app


app = create_app()
