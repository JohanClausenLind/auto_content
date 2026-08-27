"""Async engine/session factory (psycopg 3). One engine per process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from content_factory.config import get_settings


def _normalize(url: str) -> str:
    # Accept plain postgresql:// and force the psycopg (v3) driver, which supports asyncio.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    resolved = _normalize(url or settings.database.url().get_secret_value())
    return create_async_engine(resolved, pool_size=settings.database.pool_size, pool_pre_ping=True)


@lru_cache(maxsize=4)
def get_sessionmaker(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(url), expire_on_commit=False)


@asynccontextmanager
async def session_scope(url: str | None = None) -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker(url)() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
