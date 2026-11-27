"""Async SQLAlchemy engine and session factory.

The engine is created lazily (first access) so that importing the module
in tests doesn't immediately try to connect to a database.

Usage in service code:
    from app.core.database import get_db
    async with get_db() as db:
        result = await db.execute(select(Product)...)
"""

from contextlib import asynccontextmanager
from typing import Optional

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)

from app.core.config import settings

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker] = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=(settings.APP_ENV == "development"),
            pool_size=2,       # Render Free: low connection budget
            max_overflow=3,
            pool_pre_ping=True,
        )
    return _engine


def _get_session_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_db():
    """Context-manager session. Auto-commits on success, rolls back on error."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_dep():
    """FastAPI dependency injector (use with Depends)."""
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
