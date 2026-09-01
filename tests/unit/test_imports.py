from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.imports.history import (
    ContentMemoryStore,
    HistoryImportError,
    import_history_csv,
    reconcile,
)

CSV = """published_at,platform,title,url
2026-05-01,bluesky,Wind share hits 21%,https://bsky.app/p/1
2026-05-08,bluesky,Grid fees explained,https://bsky.app/p/2
,bluesky,Missing date,https://bsky.app/p/3
2026-05-15,mastodon,Wind share hits 21%,https://masto/p/9
2026-05-01,bluesky,Wind share hits 21%,https://bsky.app/p/1
"""


def test_dry_run_dedup_import_rollback_reconcile(tmp_path: Path) -> None:
    csv_path = tmp_path / "history.csv"
    csv_path.write_text(CSV)
    store = ContentMemoryStore(tmp_path / "memory")
    dry = import_history_csv(csv_path, store, dry_run=True)
    assert dry.dry_run and dry.total_rows == 5 and dry.valid == 4
    assert dry.invalid == ((4, "published_at empty"),)
    assert dry.duplicates == 1  # exact repeat of row 1 inside the file
    assert dry.imported == 0 and store.entries == {}

    done = import_history_csv(csv_path, store, dry_run=False)
    assert done.imported == 3 and len(store.entries) == 3
    again = import_history_csv(csv_path, store, dry_run=False)
    assert again.imported == 0 and again.duplicates == 4  # idempotent re-import

    recon = reconcile(store, csv_path)
    assert recon["missing"] == 0 and recon["invalid"] == 1

    assert done.batch_id is not None
    removed = store.remove_batch(done.batch_id)
    assert removed == 3 and store.entries == {}
    fresh = ContentMemoryStore(tmp_path / "memory")  # rollback persisted
    assert fresh.entries == {}

    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(HistoryImportError, match="must carry columns"):
        import_history_csv(bad, store)
