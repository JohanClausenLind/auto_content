"""Serving a file off local disk, once, so the containment rule has one implementation.

Two routes now hand generated media to the browser — ``/v1/sequences`` for a sequence workdir and
``/v1/run-history`` for everything a run produced — and the rule they need is identical and
unforgiving: resolve the path, refuse anything that leaves the base directory, refuse anything
whose extension is not on an allowlist. A second copy of that is how the two drift, and the one
that drifts is the one that serves ``/etc/shadow``.

Every check is on the **resolved** path, which is what catches the case a string comparison misses:
a symlink inside the base directory pointing outside it. ``output/`` is full of hard links and the
gallery makes more, so this is not hypothetical.

An allowlist rather than a denylist, because "everything except the dangerous extensions" is a
list nobody finishes.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse

# One year, immutable: every path served here is content a finished run wrote and will not rewrite
# — a frame, a film, a narration take. The run directory is the version.
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def contained_file(base: Path, relative: str, allowed: Mapping[str, str]) -> tuple[Path, str]:
    """The file ``relative`` names inside ``base``, and its media type.

    Raises ``404`` — never ``403`` — for a path that escapes, is missing, or is of a type not
    served: telling a caller apart "exists but forbidden" from "does not exist" hands them a
    filesystem oracle for free.
    """
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
