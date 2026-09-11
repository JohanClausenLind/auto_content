"""One flat directory of finished films, and the single rule for putting one there.

A deliverable lives at ``deliverables/<id>/exports/final.mp4`` — correct, addressable, and no use
to anybody browsing: measured 2026-09-10, 213 run directories held 34 films between them and
watching one meant knowing the path. So the last stage of a run also links its film into
``<gallery>/<run>.mp4``.

This module exists because there are now **two** callers — the packaging stage for a run made here,
and the harvest for a run made on another machine — and a second copy of "how a film gets into the
gallery" is how the two drift. The naming rule in particular is load-bearing and shared: a film is
named by its run, so two runs with the same name are a collision the caller has to resolve rather
than a silent overwrite.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def gallery_path(name: str, suffix: str, *, gallery_dir: str, repo_root: Path) -> Path | None:
    """Where a film called ``name`` belongs, or None when the gallery is switched off."""
    if not gallery_dir:
        return None
    return repo_root / gallery_dir / f"{name}{suffix}"


def publish_film(
    *,
    name: str,
    source: Path,
    gallery_dir: str,
    repo_root: Path,
    replace: bool = True,
    expect_sha256: str | None = None,
) -> str | None:
    """Put one film in the gallery under ``name``; return its repo-relative path.

    A hard link rather than a copy: the bytes exist once, the gallery costs nothing, and deleting
    it cannot lose a deliverable. Falls back to a copy when the two are on different filesystems —
    which they are not today (``output/`` and ``videos/`` share ``/home``), but a harvest directory
    moved to ``/mnt/fast`` would make them so, and a silent doubling of every film is worth the
    two-line fallback.

    ``replace=False`` refuses rather than overwrites when something is already there. That is what
    a harvest wants: a run name is only unique on the machine that made it, so a name that is
    already taken by different bytes is two different films arguing over one filename, and the
    operator should be told rather than have one of them disappear.
    """
    target = gallery_path(name, source.suffix, gallery_dir=gallery_dir, repo_root=repo_root)
    if target is None or not source.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not replace:
            # Digests, not sizes. Two different films can be the same number of bytes, and
            # "close enough" here means one of them silently never reaches the gallery.
            from content_factory.schemas.base import file_sha256

            want = expect_sha256 or file_sha256(source)
            if file_sha256(target) == want:
                return str(target.relative_to(repo_root))  # already this exact film
            msg = f"{target.name} is already in the gallery with different bytes"
            raise FileExistsError(msg)
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return str(target.relative_to(repo_root))
