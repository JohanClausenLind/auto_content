"""Serving a file off local disk, once, so the containment rule has one implementation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse

# One year, immutable: every path served here is content a finished run wrote and will not rewrite
# — a frame, a film, a narration take. The run directory is the version.
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def contained_file(base: Path, relative: str, allowed: Mapping[str, str]) -> tuple[Path, str]:
    """The file ``relative`` names inside ``base``, and its media type."""
    base = base.resolve()
    if not relative or relative.startswith("/") or "\x00" in relative:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    target = (base / relative).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    media_type = allowed.get(target.suffix.lower())
    if media_type is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return target, media_type


def serve_contained(
    base: Path, relative: str, allowed: Mapping[str, str], *, immutable: bool = False
) -> FileResponse:
    """`contained_file`, as a response. ``immutable`` is for output a run will never rewrite."""
    target, media_type = contained_file(base, relative, allowed)
    headers = {"Cache-Control": IMMUTABLE_CACHE} if immutable else None
    return FileResponse(target, media_type=media_type, headers=headers)
