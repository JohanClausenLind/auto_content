"""Perf pass at realistic scale: hundreds of runs in the API, thousands of pieces in the
content memory. Budgets are deliberately generous — these catch O(n²) regressions and missing
indexes, not micro-variance."""

from __future__ import annotations

import csv
import time

from content_factory.imports.history import ContentMemoryStore, import_history_csv
from content_factory.originality.fingerprint import fingerprint_script

TITLES = [
    "Why wind beat coal in {n} charts",
    "The {n}-minute guide to heat pumps",
    "How Sweden stores {n} GWh of water",
    "Grid batteries explained, part {n}",
    "Nuclear vs solar: the {n} numbers that matter",
]


def test_content_memory_import_and_compare_at_scale(tmp_path) -> None:
    """2000-piece import writes once, and originality comparison stays interactive."""
    rows = [
        {
            "published_at": f"2024-{1 + i % 12:02d}-01",
            "platform": "youtube",
            "title": TITLES[i % len(TITLES)].format(n=i),
            "url": f"https://youtube.example/v/{i}",
        }
        for i in range(2000)
    ]
    csv_path = tmp_path / "history.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["published_at", "platform", "title", "url"])
        writer.writeheader()
        writer.writerows(rows)

    store = ContentMemoryStore(tmp_path / "memory")
    started = time.perf_counter()
    report = import_history_csv(csv_path, store, dry_run=False)
    import_seconds = time.perf_counter() - started
    assert report.imported == 2000
    assert import_seconds < 5, f"bulk import took {import_seconds:.1f}s — did per-row saves return?"

    # Comparing one new piece against the full archive stays comfortably interactive.
    candidate = fingerprint_script(
        ["The 7-minute guide to heat pumps for old stone houses"], ["hook"]
    )
    archived = [fingerprint_script([v["title"]], ["hook"]) for v in store.entries.values()]
    started = time.perf_counter()
    hits = 0
    for fp in archived:
        if candidate.text_shingles & fp.text_shingles:
            hits += 1
    compare_seconds = time.perf_counter() - started
    assert hits > 0  # the corpus genuinely overlaps
    assert compare_seconds < 2, (
        f"comparison took {compare_seconds:.1f}s over {len(archived)} pieces"
    )
