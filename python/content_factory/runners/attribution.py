"""Which node produced which file."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

Snapshot = dict[str, tuple[int, int]]
"""Path relative to the run directory -> (size, mtime_ns). Both, because a regenerated frame of
the same length is a change and a truncated one is too."""

MAX_FILES_PER_NODE = 200
"""How many paths one node's record carries.

`generate_keyframes` on a 24-beat story touches 2,904 files in one step, and a report listing them
all would be a 400 KB JSON document nobody can read, rewritten after every step. The count is
recorded separately, so a truncated list says so rather than looking complete — the same bargain
``run_history.MAX_OUTPUTS`` makes for the same reason.
"""

_REVIEWABLE = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".webp", ".gif",
        ".mp4", ".webm",
        ".wav", ".mp3", ".m4a", ".flac", ".ogg",
        ".pdf",
    }
)  # fmt: skip
"""The kinds a person opens and looks at or listens to.

They sort first inside a node's record, and that ordering is load-bearing rather than cosmetic.
The cap is reached only by the picture stages, and their file lists are mostly `.done.json`
markers and per-attempt JSON — a plain alphabetical cut on the 2,904 files of one
`generate_keyframes` step spends all 200 slots on `audio/` bookkeeping and records not one of the
frames the step exists to make. Deliberately not shared with
``run_history.MEDIA_TYPES``: that list decides what an HTTP route may serve, and a runner must not
be able to widen it by editing a sort order.
"""

_SKIP_DIRS = frozenset({".stages", "__pycache__", ".git"})
"""Runner bookkeeping, not output. `.stages` is the cache that makes `--from` a resume."""


def snapshot(root: Path) -> Snapshot:
    """Every file under ``root`` with its size and mtime."""
    out: Snapshot = {}
    root = root.resolve()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in _SKIP_DIRS:
                                stack.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue  # deleted or replaced while we walked; not this node's problem
                    relative = Path(entry.path).relative_to(root).as_posix()
                    out[relative] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue
    return out


def changed(before: Snapshot, after: Snapshot) -> list[str]:
    """Paths that appeared or changed between two snapshots, sorted."""
    return sorted(path for path, stamp in after.items() if before.get(path) != stamp)


@dataclass(frozen=True)
class NodeFiles:
    """What one step left behind, as the report records it."""

    paths: tuple[str, ...]
    """Relative to the run directory, capped at :data:`MAX_FILES_PER_NODE`."""
    total: int
    """How many files changed, which is not ``len(paths)`` once the cap bites."""

    def as_record(self) -> dict[str, object]:
        return {"paths": list(self.paths), "total": self.total}


def node_files(paths: Iterable[str], *, limit: int = MAX_FILES_PER_NODE) -> NodeFiles:
    """``paths`` as a capped record, the files a person would look at first."""
    ordered = sorted(paths, key=lambda p: (0 if _suffix(p) in _REVIEWABLE else 1, p))
    return NodeFiles(paths=tuple(ordered[:limit]), total=len(ordered))


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    _, dot, ext = name.rpartition(".")
    return f".{ext.lower()}" if dot else ""
