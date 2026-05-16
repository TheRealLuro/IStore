"""Shared test fixtures.

The Phase 4 acceptance tests are integration-style: they require a real
Postgres (the consent flow + face pipeline depend on pgvector / JSONB / async
SQLAlchemy semantics that are not faithfully simulated by SQLite).

To avoid clobbering the developer's `istore` dev database, these fixtures use
a dedicated `istore_phase4_test` database that is dropped + recreated +
migrated once per pytest session. Each test then truncates the user-scoped
tables for isolation. Tests that don't need the DB (test_health, test_codecs,
test_policy) ignore the `_test_db` fixture entirely.
"""
from __future__ import annotations

import os
import uuid

# Override DB env BEFORE backend modules import (Settings() reads env at
# instantiation time, and backend.db creates the engine at module load).
_TEST_DB_NAME = "istore_phase4_test"
os.environ["DATABASE_URL"] = (
    f"postgresql+asyncpg://istore:istore@localhost:5432/{_TEST_DB_NAME}"
)
os.environ["DATABASE_URL_SYNC"] = (
    f"postgresql+psycopg2://istore:istore@localhost:5432/{_TEST_DB_NAME}"
)
os.environ["APP_ENV"] = "test"
os.environ["VISION_ENABLED"] = "false"  # No CLIP / face models in unit tests.
os.environ["SECURITY_RATE_LIMITS_ENABLED"] = "false"

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402


def _install_nullpool_engine() -> None:
    """Replace the module-scoped engine with one that doesn't pool.

    Tests run on Windows ProactorEventLoop; pytest-asyncio creates a fresh
    loop per test function. Pooled asyncpg connections from a previous
    loop will fail with `'NoneType' object has no attribute 'send'` when
    the next test reuses them. NullPool side-steps this by opening a fresh
    connection per checkout and closing it on release.
    """
    from backend import db as db_mod
    from backend.config import settings

    new_engine = create_async_engine(
        settings.database_url, echo=False, poolclass=NullPool
    )
    db_mod.engine = new_engine
    db_mod.SessionLocal = async_sessionmaker(
        bind=new_engine, expire_on_commit=False
    )


_install_nullpool_engine()


def _postgres_reachable() -> bool:
    try:
        import psycopg2

        c = psycopg2.connect(
            dbname="postgres",
            user="istore",
            password="istore",
            host="localhost",
            port=5432,
            connect_timeout=2,
        )
        c.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def _test_db():
    """Drop, recreate, and migrate the dedicated test database."""
    if not _postgres_reachable():
        pytest.skip("Postgres not reachable on localhost:5432 — start docker compose.")

    import psycopg2

    admin = psycopg2.connect(
        dbname="postgres",
        user="istore",
        password="istore",
        host="localhost",
        port=5432,
    )
    admin.autocommit = True
    cur = admin.cursor()
    # Drop any sessions still attached, then drop+create the DB.
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (_TEST_DB_NAME,),
    )
    cur.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"')
    cur.execute(f'CREATE DATABASE "{_TEST_DB_NAME}" OWNER istore')
    admin.close()

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL_SYNC"])
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
async def client() -> AsyncClient:
    """Lightweight client for tests that don't need DB or auth."""
    from backend.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db_client(_test_db, monkeypatch) -> AsyncClient:
    """Authenticated-capable client backed by the test DB with MinIO stubbed."""
    # Stub MinIO so tests don't need a live blob store.
    from backend import storage as storage_mod

    blobs: dict[tuple[str, str], bytes] = {}

    def _put(bucket, key, data, content_type, **kwargs):  # noqa: ARG001
        blobs[(bucket, key)] = bytes(data)

    def _get(bucket, key):
        return blobs[(bucket, key)]

    def _delete(bucket, key):
        blobs.pop((bucket, key), None)

    monkeypatch.setattr(storage_mod.storage, "put", _put)
    monkeypatch.setattr(storage_mod.storage, "get", _get)
    monkeypatch.setattr(storage_mod.storage, "delete", _delete)
    monkeypatch.setattr(storage_mod.storage, "ensure_buckets", lambda: None)

    # Per-test truncate for isolation. CASCADE handles FK chains; RESTART IDENTITY
    # resets sequence counters so person_id / face_id are stable across tests.
    from sqlalchemy import text

    from backend.db import engine

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                TRUNCATE TABLE
                    audit_log, cloud_files, cloud_links, feedback_events,
                    bandit_state, recovery_codes, image_geo, share_grants,
                    face_detections, faces, persons, consent_records,
                    folder_tags, image_tags, tags,
                    folders, images, stripe_events,
                    subscriptions, users
                RESTART IDENTITY CASCADE
                """
            )
        )

    from backend.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------- helpers used by Phase 4 tests ----------


async def register_and_login(
    ac: AsyncClient, email: str | None = None, password: str = "Aa1!aaaaaa"
) -> tuple[str, dict]:
    """Register a user, log in, return (jwt, auth_headers)."""
    email = email or f"u{uuid.uuid4().hex[:10]}@example.com"
    r = await ac.post(
        "/auth/register",
        json={"email": email, "password": password, "age_confirmed": True},
    )
    assert r.status_code in (200, 201), r.text
    r = await ac.post(
        "/auth/jwt/login",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


async def grant_consent(ac: AsyncClient, headers: dict) -> dict:
    r = await ac.post(
        "/consent/face-recognition/grant",
        json={
            "signature_text": "Test User",
            "consent_collection": True,
            "consent_retention": True,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def insert_face(
    user_id: uuid.UUID,
    person_name: str | None = None,
    cluster_id: int = 1,
    *,
    image_id: uuid.UUID | None = None,
) -> dict:
    """Insert a face row (and optional named person + face_detection + image)
    directly via the ORM — bypasses insightface so tests don't need GPU
    weights. Returns IDs for assertions."""
    from backend.db import SessionLocal
    from backend.models import (
        Face,
        FaceDetection,
        Image,
        Person,
    )

    async with SessionLocal() as session:
        person_id = None
        if person_name is not None:
            p = Person(user_id=user_id, display_name=person_name, face_count=0)
            session.add(p)
            await session.flush()
            person_id = p.id

        # Synthetic 512-d embedding; values irrelevant for the leak/lifecycle
        # tests — what matters is that the row is per-user-scoped.
        embedding = [0.001 * (i % 17) for i in range(512)]
        face = Face(
            user_id=user_id,
            embedding=embedding,
            person_id=person_id,
            cluster_id=cluster_id,
            quality_score=0.95,
        )
        session.add(face)
        await session.flush()

        # Need an image to attach face_detection to (FK).
        img = None
        if image_id is None:
            img = Image(
                user_id=user_id,
                category="image",
                served_blob_key=f"users/{user_id}/served/test-{uuid.uuid4().hex}.webp",
                original_blob_key=f"users/{user_id}/originals/test-{uuid.uuid4().hex}",
                byte_size_original=1000,
                byte_size_served=500,
                pending_face_scan=False,
            )
            session.add(img)
            await session.flush()
            image_id = img.id

        det = FaceDetection(
            image_id=image_id,
            user_id=user_id,
            bbox_x=10,
            bbox_y=10,
            bbox_w=50,
            bbox_h=50,
            detection_confidence=0.99,
            face_id=face.id,
            crop_blob_key=f"users/{user_id}/faces/test-{uuid.uuid4().hex}.jpg",
        )
        session.add(det)

        if person_id is not None:
            from sqlalchemy import update as sa_update

            await session.execute(
                sa_update(Person)
                .where(Person.id == person_id)
                .values(face_count=Person.face_count + 1)
            )

        await session.commit()
        return {
            "face_id": face.id,
            "person_id": person_id,
            "cluster_id": cluster_id,
            "image_id": image_id,
            "detection_id": det.id,
            "crop_blob_key": det.crop_blob_key,
        }


async def fetch_user_id(email: str) -> uuid.UUID:
    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import User

    async with SessionLocal() as session:
        u = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        return u.id


async def count_rows(table: str, user_id: uuid.UUID) -> int:
    from sqlalchemy import text

    from backend.db import engine

    async with engine.connect() as conn:
        r = await conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE user_id = :uid"),
            {"uid": user_id},
        )
        return int(r.scalar_one() or 0)
