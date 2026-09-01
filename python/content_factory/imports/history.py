"""Import/Migration toolkit (phase 12): historical posts from CSV into Channel Brain memory.
Dry-run first, deterministic dedup, full rollback, and a reconciliation report every time."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from content_factory.schemas.base import sha256_hex

REQUIRED_COLUMNS = ("published_at", "platform", "title", "url")


class ImportError_(Exception):
    pass


@dataclass(frozen=True)
class ImportReport:
    dry_run: bool
    total_rows: int
    valid: int
    invalid: tuple[tuple[int, str], ...]
    duplicates: int
    imported: int
    batch_id: str | None


@dataclass
class ContentMemoryStore:
    """File-backed archive of what a channel has published (feeds Radar + originality)."""

    root: Path
    entries: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "memory.json"
        if path.exists():
            self.entries = json.loads(path.read_text())

    def _save(self) -> None:
        tmp = self.root / "memory.json.tmp"
        tmp.write_text(json.dumps(self.entries, indent=1, sort_keys=True))
        tmp.replace(self.root / "memory.json")

    def add(self, key: str, record: dict) -> None:
        self.entries[key] = record
        self._save()

    def remove_batch(self, batch_id: str) -> int:
        doomed = [k for k, v in self.entries.items() if v.get("batch_id") == batch_id]
        for k in doomed:
            del self.entries[k]
        self._save()
        return len(doomed)


def import_history_csv(
    csv_path: Path, store: ContentMemoryStore, *, dry_run: bool = True
) -> ImportReport:
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or any(c not in reader.fieldnames for c in REQUIRED_COLUMNS):
            raise ImportError_(
                f"CSV must carry columns {REQUIRED_COLUMNS}; found {reader.fieldnames}"
            )
        rows = list(reader)
    invalid: list[tuple[int, str]] = []
    valid_rows: list[dict] = []
    for i, row in enumerate(rows, start=2):  # header is line 1
        problems = []
        if not (row.get("published_at") or "").strip():
            problems.append("published_at empty")
        if not (row.get("title") or "").strip():
            problems.append("title empty")
        if not (row.get("url") or "").startswith(("http://", "https://")):
            problems.append("url is not a link")
        if problems:
            invalid.append((i, "; ".join(problems)))
        else:
            valid_rows.append(row)
    batch_source = sha256_hex(csv_path.read_bytes())[:12]
    duplicates = 0
    to_import: list[tuple[str, dict]] = []
    for row in valid_rows:
        key = sha256_hex(f"{row['platform']}|{row['url']}".encode())[:24]
        if key in store.entries or any(k == key for k, _ in to_import):
            duplicates += 1
            continue
        to_import.append(
            (
                key,
                {
                    "published_at": row["published_at"],
                    "platform": row["platform"],
                    "title": row["title"],
                    "url": row["url"],
                    "batch_id": batch_source,
                },
            )
        )
    if dry_run:
        return ImportReport(True, len(rows), len(valid_rows), tuple(invalid), duplicates, 0, None)
    for key, record in to_import:
        store.add(key, record)
    return ImportReport(
        False, len(rows), len(valid_rows), tuple(invalid), duplicates, len(to_import), batch_source
    )


def reconcile(store: ContentMemoryStore, csv_path: Path) -> dict:
    """After an import: every valid CSV row is either present or accounted for as invalid/dup."""
    report = import_history_csv(csv_path, store, dry_run=True)
    return {
        "csv_rows": report.total_rows,
        "valid": report.valid,
        "already_present": report.duplicates,
        "invalid": len(report.invalid),
        "missing": report.valid
        - report.duplicates,  # rows that WOULD import (0 = fully reconciled)
    }
