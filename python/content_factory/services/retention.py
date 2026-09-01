"""Retention jobs (25, phase 13): sweep expired artifacts/audit rows per policy, honouring
legal hold. Retention 0 = keep forever. Dry-run first; every sweep is itself audited."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SweepReport:
    dry_run: bool
    artifacts_scanned: int
    artifacts_deleted: int
    bytes_freed: int
    held: int


def sweep_artifacts(
    root: Path,
    *,
    retention_days: int,
    legal_hold_prefixes: tuple[str, ...] = (),
    dry_run: bool = True,
    now: float | None = None,
) -> SweepReport:
    if retention_days <= 0:
        return SweepReport(dry_run, 0, 0, 0, 0)  # 0 = keep forever
    cutoff = (now if now is not None else time.time()) - retention_days * 86400
    scanned = deleted = freed = held = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        scanned += 1
        rel = str(path.relative_to(root))
        if any(rel.startswith(p) for p in legal_hold_prefixes):
            held += 1
            continue  # legal hold suspends deletion for marked projects
        if path.stat().st_mtime < cutoff:
            freed += path.stat().st_size
            deleted += 1
            if not dry_run:
                path.unlink()
    if not dry_run:
        for d in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
    return SweepReport(dry_run, scanned, deleted, freed, held)
