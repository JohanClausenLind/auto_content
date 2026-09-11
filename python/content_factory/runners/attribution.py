"""Which node produced which file.

A run's report said what each stage *did* — its hash, its facts, its seconds — and never which
files it left behind. So the history could show a run's 12,019 files as five groups by file type
and could not answer the question an operator actually has in front of a bad picture: *which step
made this, and what else did that step make?* ComfyUI answers it by hanging every output off the
node that emitted it, which is why a bad image there is one click from the sampler that drew it.

Nothing in a stage executor knows its own file list — :class:`StageOutput` carries a hash and a
dict of facts — and forty-odd executors are not going to start agreeing about how to report one.
So this observes instead: snapshot the run directory, run the step, snapshot again, and the files
that appeared or changed are that node's output. It is the same trick ``make`` uses, and it needs
no cooperation from the thing being measured.

Measured on this machine (2026-09-11) before it was wired in, because a per-step directory walk
sounds expensive and the whole design rests on it not being: **20.8 ms for the 12,019 files of
`m04-picture-story-24`**, against stages that cost 48 s to 608 s. A 24-step lane pays half a
second in total.

Two honesty rules the readers depend on:

* **A snapshot only claims what it saw.** A file a stage *read* is not its output; a file two
  stages both touch is attributed to the second one, because that is what the mtime says. Where
  that is wrong, it is wrong in a way somebody can see, which is why the record says
  ``recorded`` and the path-based guess says ``inferred``.
* **It never fails a run.** An unreadable directory, a file deleted mid-walk, a permission error:
  all of it is caught and reported as "nothing observed". A run that produced a film must not be
  lost because a bookkeeping walk tripped.
"""

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
    """Every file under ``root`` with its size and mtime. Empty when the directory is unreadable.

    Iterative rather than ``rglob``: this runs between every pair of steps, and ``os.scandir``
    carries the stat data from the directory entry instead of paying a second syscall per file.
    """
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
    """Paths that appeared or changed between two snapshots, sorted.

    Deletions are deliberately not reported: a node that removed a file did not produce one, and a
    history panel offering a link to something that is gone is worse than saying nothing.
    """
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
    """``paths`` as a capped record, the files a person would look at first.

    Sorted rather than left in walk order, so a report is stable between runs: a diff of two
    run.json files should show what the run did differently, not what order a directory walk
    happened to return. Within that, media before bookkeeping — see :data:`_REVIEWABLE`.
    """
    ordered = sorted(paths, key=lambda p: (0 if _suffix(p) in _REVIEWABLE else 1, p))
    return NodeFiles(paths=tuple(ordered[:limit]), total=len(ordered))


def _suffix(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    _, dot, ext = name.rpartition(".")
    return f".{ext.lower()}" if dot else ""
