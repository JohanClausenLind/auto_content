"""/v1/run-history: the runs this machine has made, and the files they produced.

Read-only, and the counterpart to ``/v1/runs``: that route reads the ``production_runs`` table and
sees durable Temporal runs, which on this machine were mostly started by the integration suite.
Everything actually produced here — every film in ``videos/``, every anchor drawing, every
narration — came from a local run (``content-factory make``) that writes no database row at all,
so none of the day's work was reachable from the product that made it.

Files are served only from inside the output root, resolved and extension-checked by
``api/files.contained_file`` — the same rule ``/v1/sequences`` uses, in one place.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from content_factory.api.deps import Principal, require_role
from content_factory.api.files import serve_contained
from content_factory.db.models import Role
from content_factory.services import run_history as history

router = APIRouter(prefix="/v1/run-history", tags=["history"])
VIEWER = require_role(Role.viewer)

_FILE_TYPES = {ext: media for ext, (_kind, media) in history.MEDIA_TYPES.items()}


@router.get("")
async def list_history(
    limit: int = Query(default=100, ge=1, le=500), p: Principal = Depends(VIEWER)
) -> list[dict[str, Any]]:
    """Newest first, without outputs.

    Scanning outputs for every run means walking the whole tree — 226 runs, tens of thousands of
    files — to draw one list, so a row carries its counts and the detail view pays for the files
    of the run somebody opened.
    """
    return [
        {
            k: v
            for k, v in record.as_dict().items()
            if k not in {"outputs", "nodes", "film", "poster", "unattributed"}
        }
        for record in history.list_runs(limit=limit)
    ]


@router.get("/{run_id}")
async def get_history_run(run_id: str, p: Principal = Depends(VIEWER)) -> dict[str, Any]:
    """One run with everything it produced, each file addressable by the path in this response."""
    record = history.get_run(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return record.as_dict()


@router.get("/{run_id}/files/{file_path:path}")
async def get_history_file(
    run_id: str, file_path: str, p: Principal = Depends(VIEWER)
) -> FileResponse:
    run_dir = history.decode_run_id(run_id, history.history_root())
    if run_dir is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    # Immutable: a finished run does not rewrite its own frames, so a browser scrolling a grid of
    # 80 drawings should fetch each one once.
    return serve_contained(run_dir, file_path, _FILE_TYPES, immutable=True)
