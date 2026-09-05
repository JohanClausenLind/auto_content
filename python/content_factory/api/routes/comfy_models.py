"""/v1/comfy/*: local model inventory and one-click downloads.

The inventory answers "which checkpoints/loras/VAEs/text encoders are already on this machine";
downloads run only through the allowlisted `comfy model download` path (https + approved hosts,
destination pinned inside the workspace models/ tree) in comfy-cli's background worker. Inventory
roots are configuration-derived; download requests carry a URL and a models/<kind> folder, never
an arbitrary path."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from content_factory.api.deps import Principal, app_settings, require_role
from content_factory.comfyui.downloads import (
    DownloadManager,
    DownloadValidationError,
    ModelDownloadRequest,
    download_workspace,
)
from content_factory.comfyui.inventory import resolve_roots, scan_models
from content_factory.db.models import Role

router = APIRouter(prefix="/v1/comfy", tags=["comfy"])
VIEWER = require_role(Role.viewer)
EDITOR = require_role(Role.editor)


def _downloads(request: Request) -> DownloadManager:
    manager = getattr(request.app.state, "comfy_downloads", None)
    if not isinstance(manager, DownloadManager):
        manager = DownloadManager()
        request.app.state.comfy_downloads = manager
    return manager


# Sync handlers on purpose: they scan disk and shell out to comfy-cli, which must run in the
# threadpool, never on the event loop.
@router.get("/models")
def list_models(request: Request, p: Principal = Depends(VIEWER)) -> dict[str, Any]:
    roots = resolve_roots(app_settings(request))
    models = scan_models(roots)
    return {
        "roots": [{"path": str(r.path), "source": r.source, "exists": r.exists} for r in roots],
        "models": [asdict(f) for f in models],
    }


@router.post("/models/download", status_code=status.HTTP_202_ACCEPTED)
def start_download(
    body: ModelDownloadRequest, request: Request, p: Principal = Depends(EDITOR)
) -> dict[str, Any]:
    try:
        job = _downloads(request).start(body, download_workspace(app_settings(request)))
    except DownloadValidationError as err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err)) from err
    return job.as_dict()


@router.get("/models/downloads")
def list_downloads(request: Request, p: Principal = Depends(VIEWER)) -> list[dict[str, Any]]:
    return [job.as_dict() for job in _downloads(request).jobs()]
