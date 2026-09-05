"""API tests run against the compose Postgres test database (integration-marked)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from content_factory.api.app import create_app
from content_factory.db.base import Base

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _test_url() -> str | None:
    return os.environ.get("DATABASE_URL_TEST")


@contextmanager
def _env(name: str, value: str) -> Iterator[None]:
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


@pytest.fixture(scope="session")
def migrated_test_db() -> str:
    """Bring the test database to head with Alembic, once per session.

    Tests must never build the schema from ``Base.metadata``: that creates tables behind
    Alembic's back and leaves ``alembic_version`` on an older revision, so the next
    ``alembic upgrade head`` (setup.sh, `just migrate`) dies on an already-existing table.
    Migrations are the single source of truth for this database.
    """
    url = _test_url()
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    try:
        with _env("ALEMBIC_DATABASE_URL", url):
            command.upgrade(config, "head")
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test database unavailable: {exc}")
    return url


@pytest.fixture
async def db_engine(migrated_test_db: str):
    engine = create_async_engine(migrated_test_db, poolclass=None)
    async with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
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
