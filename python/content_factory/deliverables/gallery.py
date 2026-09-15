"""One flat directory of finished films, and the single rule for putting one there."""

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
    """Put one film in the gallery under ``name``; return its repo-relative path."""
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
