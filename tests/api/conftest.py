"""API tests run against the compose Postgres test database (integration-marked)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from content_factory.api.app import create_app
from content_factory.db.base import Base

pytestmark = pytest.mark.integration


def _test_url() -> str | None:
    return os.environ.get("DATABASE_URL_TEST")


@pytest.fixture
async def db_engine():
    url = _test_url()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    engine = create_async_engine(url, poolclass=None)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test database unavailable: {exc}")
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessionmaker(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def client(sessionmaker) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.state.sessionmaker = sessionmaker
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:3000") as c:
        yield c


@pytest.fixture
def make_client(sessionmaker):
    app = create_app()
    app.state.sessionmaker = sessionmaker

    def _mk() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
        )

    return _mk
