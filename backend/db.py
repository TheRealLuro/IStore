from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Session as SyncSession

from backend.config import settings
from backend.context import get_current_user_id


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    # The default 5 + 10 overflow = 15 caps starve under v2 load: every
    # upload chains face_scan → summarize, each holding a session while a
    # model loads (10-30 s on first call). Combined with the frontend's
    # polling of /images/{id} for pending_summary every 4 s, 15 slots
    # disappear fast. 20 + 30 overflow gives headroom without bloating
    # the connection count on the Postgres side (default Postgres allows
    # ~100 connections; a single dev machine sits well under that).
    pool_size=20,
    max_overflow=30,
    pool_recycle=300,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


@event.listens_for(SyncSession, "after_begin")
def _set_rls_context(session, transaction, connection) -> None:  # noqa: ARG001
    user_id = get_current_user_id()
    if user_id:
        connection.exec_driver_sql(
            "SELECT set_config('app.current_user_id', %s, true)",
            (user_id,),
        )
        return
    if settings.app_env.lower() in {"dev", "test", "local"}:
        connection.exec_driver_sql("SELECT set_config('app.rls_bypass', 'on', true)")


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
