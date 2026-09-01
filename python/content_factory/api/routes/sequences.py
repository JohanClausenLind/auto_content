"""/v1/sequences: review surface for image-sequence runs (anchor, frames, drift, packaged video).

Read-only. Files are served ONLY from inside the configured output root, with the resolved path
checked against traversal and an extension allowlist — never arbitrary filesystem access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from content_factory.api.deps import Principal, require_role
from content_factory.config import Settings, get_settings
from content_factory.db.models import Role

router = APIRouter(prefix="/v1/sequences", tags=["sequences"])
VIEWER = require_role(Role.viewer)

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".json": "application/json",
}


def _root(request: Request) -> Path:
    stored = getattr(request.app.state, "settings", None)
    settings = stored if isinstance(stored, Settings) else get_settings()
    return Path(settings.image_sequences.output_root).resolve()


def _summarize(seq_dir: Path) -> dict[str, Any]:
    frames: list[dict[str, Any]] = []
    frames_dir = seq_dir / "frames"
    if frames_dir.is_dir():
        for marker in sorted(frames_dir.glob("*.done.json")):
            record = json.loads(marker.read_text())
            index = record["frame_index"]
            frames.append(
                {
                    "index": index,
                    "file": f"frames/{index:04d}.png",
                    "attempts": record.get("attempts"),
                    "cache_hit": record.get("cache_hit", False),
                    "drift": record.get("drift"),
                }
            )
    videos = sorted(p.name for p in seq_dir.glob("*.mp4"))
    mtimes = [p.stat().st_mtime for p in seq_dir.rglob("*") if p.is_file()]
    return {
        "name": seq_dir.name,
        "anchor": (seq_dir / "anchor.png").exists(),
        "frames": frames,
        "videos": videos,
        "contact_sheet": (seq_dir / "contact-sheet.png").exists(),
        "flipbook": (seq_dir / "flipbook.pdf").exists(),
        "updated_at": max(mtimes) if mtimes else None,
    }


@router.get("")
async def list_sequences(request: Request, p: Principal = Depends(VIEWER)) -> list[dict[str, Any]]:
    root = _root(request)
    if not root.is_dir():
        return []
    out = [
        _summarize(d)
        for d in sorted(root.iterdir())
        if d.is_dir() and ((d / "anchor.png").exists() or (d / "frames").is_dir())
    ]
    out.sort(key=lambda s: s["updated_at"] or 0, reverse=True)
    return out


@router.get("/{name}/files/{file_path:path}")
async def get_sequence_file(
    name: str, file_path: str, request: Request, p: Principal = Depends(VIEWER)
) -> FileResponse:
    root = _root(request)
    if "/" in name or name.startswith("."):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    target = (root / name / file_path).resolve()
    # The resolved path must stay inside the sequence's own directory (no traversal, no links out).
    seq_dir = (root / name).resolve()
    if not target.is_relative_to(seq_dir) or not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    media_type = _MEDIA_TYPES.get(target.suffix.lower())
    if media_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return FileResponse(target, media_type=media_type)
