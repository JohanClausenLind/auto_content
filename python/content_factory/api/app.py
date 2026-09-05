"""FastAPI application factory. Auth precedes business logic; routers are added per phase."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from content_factory import __version__
from content_factory.api.routes import brands as brand_routes
from content_factory.api.routes import campaigns as campaign_routes
from content_factory.api.routes import comfy_models as comfy_model_routes
from content_factory.api.routes import distribution as distribution_routes
from content_factory.api.routes import engagement as engagement_routes
from content_factory.api.routes import graphs as graph_routes
from content_factory.api.routes import model_store as model_store_routes
from content_factory.api.routes import notifications as notification_routes
from content_factory.api.routes import operations as operations_routes
from content_factory.api.routes import personas as persona_routes
from content_factory.api.routes import portal as portal_routes
from content_factory.api.routes import revisions as revision_routes
from content_factory.api.routes import runs as run_routes
from content_factory.api.routes import sequences as sequence_routes
from content_factory.api.routes import session as session_routes
from content_factory.api.routes import uploads as upload_routes
from content_factory.api.routes import workspaces as workspace_routes
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
    app.state.settings = settings

    app.include_router(session_routes.router)
    app.include_router(run_routes.router)
    app.include_router(revision_routes.router)
    app.include_router(notification_routes.router)
    app.include_router(campaign_routes.router)
    app.include_router(operations_routes.router)
    app.include_router(distribution_routes.router)
    app.include_router(workspace_routes.router)
    app.include_router(persona_routes.router)
    app.include_router(brand_routes.router)
    app.include_router(portal_routes.router)
    app.include_router(engagement_routes.router)
    app.include_router(sequence_routes.router)
    app.include_router(comfy_model_routes.router)
    app.include_router(model_store_routes.router)
    app.include_router(upload_routes.router)
    app.include_router(graph_routes.router)

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
