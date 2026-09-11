"""Build and verify the ``datasets/<category>/<Name>`` symlink tree.

The only thing allowed to act on :data:`content_factory.libraries.LIBRARIES`, the way
``weight_install`` is the only thing allowed to act on the weight registry.

Idempotent by construction: a link that already points where it should is left alone, a link that
points somewhere else is repointed, and a real directory sitting where a link belongs is refused
rather than replaced — that is data, and this module does not delete data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from content_factory.libraries.registry import DEFAULT_STORE, LIBRARIES, DataLibrary

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class LinkOutcome:
    """What happened to one library's link, and whether the data behind it is actually there."""

    key: str
    link: Path
    target: Path
    action: str
    """``linked`` (created), ``repointed``, ``ok`` (already correct), ``absent`` (no data on this
    host, so no link was made), or ``blocked`` (something real is in the way)."""
    present: bool
    detail: str = ""

    @property
    def changed(self) -> bool:
        return self.action in ("linked", "repointed")


def plan(store: str | Path = DEFAULT_STORE, repo_root: Path = REPO_ROOT) -> list[LinkOutcome]:
    """What :func:`apply` would do, without touching the filesystem."""
    return [_plan_one(d, store, repo_root) for d in LIBRARIES]


def _plan_one(library: DataLibrary, store: str | Path, repo_root: Path) -> LinkOutcome:
    target = library.path(store)
    link = library.link(repo_root)
    present = library.present(store)
    if not present:
        # An absent library is not an error. Every lane runs without any of these — the reference
        # library says so in its own settings docstring — so a machine that has not fetched one
        # gets a report, not a failure.
        return LinkOutcome(
            library.key, link, target, "absent", False, f"no {library.probe} under {target}"
        )
    if link.is_symlink():
        current = Path(link).readlink()
        if current == target:
            return LinkOutcome(library.key, link, target, "ok", True)
        return LinkOutcome(library.key, link, target, "repointed", True, f"was {current}")
    if link.exists():
        return LinkOutcome(
            library.key, link, target, "blocked", True, f"{link} exists and is not a symlink"
        )
    return LinkOutcome(library.key, link, target, "linked", True)


def apply(store: str | Path = DEFAULT_STORE, repo_root: Path = REPO_ROOT) -> list[LinkOutcome]:
    """Create the tree. Returns the same outcomes :func:`plan` would, after acting on them."""
    outcomes = plan(store, repo_root)
    for outcome in outcomes:
        if outcome.action in ("absent", "blocked", "ok"):
            continue
        outcome.link.parent.mkdir(parents=True, exist_ok=True)
        if outcome.link.is_symlink():
            outcome.link.unlink()
        outcome.link.symlink_to(outcome.target)
    return outcomes
