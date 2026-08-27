"""FastAPI application factory. Auth precedes business logic; routers are added per phase."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from content_factory import __version__
from content_factory.config import Settings, get_settings
from content_factory.logging import configure_logging, get_logger

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json=settings.environment == "production")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info(
            "api.start", environment=settings.environment, bind=settings.bind, port=settings.port
        )
        yield
        log.info("api.stop")

    app = FastAPI(
        title="Content Factory API",
        version=__version__,
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @app.get("/v1/meta")
    async def meta() -> dict[str, object]:
        return {
            "version": __version__,
            "environment": settings.environment,
            "distribution_enabled": settings.distribution.enabled,
            "kill_switch": settings.distribution.kill_switch,
        }

    return app


app = create_app()
