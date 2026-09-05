"""The sqlite reference index: tokenizer, determinism, and the honesty of a NULL."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from content_factory.reference.index import (
    FTS_COLUMNS,
    SCHEMA,
    build,
    closest_contact_m,
    create,
    fts5_available,
    insert_clip,
    manifest_sha256,
    open_index,
    read_library,
)
from content_factory.schemas.reference import (
    Affection,
    ContactTag,
    InteractionTag,
    Modality,
    Posture,
    ReferenceClip,
    ReferenceFile,
    ReferenceSource,
    UsageClass,
)

INGESTED_AT = "2026-09-07T00:00:00Z"


def make_clip(
    clip_id: str,
    *,
    contact: tuple[ContactTag, ...] = (ContactTag.hands,),
    interaction: tuple[InteractionTag, ...] = (InteractionTag.handshake,),
    caption: str = "two people meet and shake hands",
    measured: dict[str, float | list[float]] | None = None,
) -> ReferenceClip:
    """A minimal valid clip. Only the fields a test varies are arguments."""
    return ReferenceClip(
        clip_id=clip_id,
        source=ReferenceSource.cmu_mocap,
        source_ref=f"subjects/18/{clip_id}.amc",
        modality=Modality.mocap_segments,
        usage=UsageClass.pose_derivable,
        people_count=2,
        affection=Affection.affection,
        interaction_tags=interaction,
        contact_tags=contact,
        postures=(Posture.standing, Posture.walking),
        setting="studio",
        camera_angles=("front", "side"),
        frame_count=61,
        native_fps=120.0,
        duration_s=2.542,
        pose_format="cf_clip_v2",
        pose_root=f"clips/{clip_id}.json",
        caption=caption,
        caption_source="dataset",
        measured={} if measured is None else measured,
        files=(
            ReferenceFile(
                role="clip_json",
                path=f"clips/{clip_id}.json",
                sha256="0" * 64,
                size_bytes=1024,
            ),
        ),
        ingested_at=INGESTED_AT,
        ingester_version="0.1.0",
    )


def test_fts5_is_available() -> None:
    assert fts5_available()
    conn = sqlite3.connect(":memory:")
    try:
        assert (
            conn.execute(
                "SELECT count(*) FROM pragma_compile_options WHERE compile_options = 'ENABLE_FTS5'"
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()


def test_schema_declares_the_underscore_tokenizer() -> None:
    assert "USING fts5(" in SCHEMA
    # The full tokenizer, not just the underscore half. remove_diacritics 2 has to match the query
    # side, whose normalize() folds NFKD and drops combining marks: without it an accented caption
    # word can never match the folded query word.
    assert """tokenize = "unicode61 remove_diacritics 2 tokenchars '_'\"""" in SCHEMA
    assert "prefix = '2 3'" in SCHEMA
    for column in FTS_COLUMNS:
        assert f"\n    {column},\n" in SCHEMA or f"\n    {column}\n" in SCHEMA


def test_create_sets_the_pragmas(tmp_path: Path) -> None:
    conn = create(tmp_path / "ref.sqlite3")
    try:
        assert conn.execute("PRAGMA page_size").fetchone()[0] == 4096
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 0
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"clip", "clip_fts", "library"} <= tables
    finally:
        conn.close()


def test_create_replaces_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "ref.sqlite3"
    first = create(path)
    insert_clip(first, make_clip("cmu_18_19_01"), 0)
    first.commit()
    first.close()
    second = create(path)
    try:
        assert second.execute("SELECT count(*) FROM clip").fetchone()[0] == 0
    finally:
        second.close()


def test_tokenizer_keeps_underscores(tmp_path: Path) -> None:
    """head_shoulder is one token, so the tag matches itself and 'head' matches nothing."""
    conn = create(tmp_path / "ref.sqlite3")
    try:
        insert_clip(
            conn,
            make_clip(
                "sbu_head_shoulder_01",
                contact=(ContactTag.head_shoulder,),
                interaction=(InteractionTag.hug,),
                caption="an embrace",
            ),
            0,
        )
        conn.commit()
        hit = conn.execute(
            "SELECT clip_id FROM clip_fts WHERE clip_fts MATCH 'head_shoulder'"
        ).fetchall()
        assert [row[0] for row in hit] == ["sbu_head_shoulder_01"]
        assert (
            conn.execute("SELECT count(*) FROM clip_fts WHERE clip_fts MATCH 'head'").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM clip_fts WHERE clip_fts MATCH 'shoulder'"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_fts_text_comes_from_the_contract(tmp_path: Path) -> None:
    clip = make_clip("cmu_18_19_02")
    conn = create(tmp_path / "ref.sqlite3")
    try:
        insert_clip(conn, clip, 7)
        conn.commit()
        row = conn.execute(
            "SELECT rowid, interaction, contact, posture, setting, caption FROM clip_fts"
        ).fetchone()
    finally:
        conn.close()
    expected = clip.search_text()
    assert row[0] == 7
    assert list(row[1:]) == [expected[name] for name in FTS_COLUMNS]


def test_doc_sha256_is_over_the_clip_document(tmp_path: Path) -> None:
    clip = make_clip("cmu_18_19_03")
    conn = create(tmp_path / "ref.sqlite3")
    try:
        insert_clip(conn, clip, 0)
        conn.commit()
        doc, digest = conn.execute("SELECT doc, doc_sha256 FROM clip").fetchone()
    finally:
        conn.close()
    assert doc == clip.canonical_json()
    assert digest == hashlib.sha256(clip.canonical_json().encode("utf-8")).hexdigest()
    assert ReferenceClip.model_validate_json(doc) == clip


def test_closest_contact_m_prefers_wrists_then_root_gap_then_null(tmp_path: Path) -> None:
    clips = [
        make_clip("cmu_wrists_01", measured={"closest_wrists_m": 0.1763, "root_gap_m": [0.7, 1.9]}),
        make_clip("cmu_gap_01", measured={"root_gap_m": [0.652, 1.8618]}),
        make_clip("cmu_neither_01", measured={"travel_m": 0.5}),
    ]
    conn = create(tmp_path / "ref.sqlite3")
    try:
        for order, clip in enumerate(clips):
            insert_clip(conn, clip, order)
        conn.commit()
        got = dict(conn.execute("SELECT clip_id, closest_contact_m FROM clip").fetchall())
    finally:
        conn.close()
    assert got["cmu_wrists_01"] == pytest.approx(0.1763)
    assert got["cmu_gap_01"] == pytest.approx(0.652)
    # NULL, not 0.0: an unmeasured clip must not sort as the closest thing in the library.
    assert got["cmu_neither_01"] is None
    assert closest_contact_m({}) is None


def test_build_is_ordered_and_repeatable(tmp_path: Path) -> None:
    clips = [make_clip(name) for name in ("cmu_c_01", "cmu_a_01", "cmu_b_01")]
    kwargs = {
        "lexicon_sha256": "a" * 64,
        "built_at": INGESTED_AT,
        "builder_version": "0.1.0",
        "sources": (ReferenceSource.cmu_mocap,),
        "skipped": ("cmu_18_19_99: no two-person overlap",),
    }
    first = build(clips, tmp_path / "one.sqlite3", **kwargs)
    second = build(list(reversed(clips)), tmp_path / "two.sqlite3", **kwargs)

    assert first.manifest_sha256 == second.manifest_sha256
    assert first.manifest_sha256 == manifest_sha256(sorted(clips, key=lambda c: c.clip_id))
    assert first.clip_count == 3
    assert first.skipped == ("cmu_18_19_99: no two-person overlap",)

    conn = open_index(tmp_path / "one.sqlite3")
    try:
        rows = conn.execute("SELECT id, clip_id FROM clip ORDER BY id").fetchall()
        assert [row["clip_id"] for row in rows] == ["cmu_a_01", "cmu_b_01", "cmu_c_01"]
        assert [row["id"] for row in rows] == [0, 1, 2]
        assert conn.execute("SELECT count(*) FROM clip").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM clip_fts").fetchone()[0] == 3
        stored = read_library(conn)
    finally:
        conn.close()
    assert stored == first
    assert stored.index_path == str(tmp_path / "one.sqlite3")
    assert stored.library_id == "one"


def test_manifest_digest_ignores_everything_but_the_clips(tmp_path: Path) -> None:
    """The digest answers "which clips", not "which file" or "when", so it is comparable."""
    clips = [make_clip("cmu_a_01"), make_clip("cmu_b_01")]
    one = build(
        clips,
        tmp_path / "one.sqlite3",
        lexicon_sha256="a" * 64,
        built_at=INGESTED_AT,
        builder_version="0.1.0",
    )
    two = build(
        clips,
        tmp_path / "elsewhere.sqlite3",
        lexicon_sha256="f" * 64,
        built_at="2027-01-01T00:00:00Z",
        builder_version="9.9.9",
    )
    assert one.manifest_sha256 == two.manifest_sha256
    assert one.manifest_sha256 != manifest_sha256(clips[:1])


def test_clip_and_fts_rows_join_on_rowid(tmp_path: Path) -> None:
    """A query gets the whole clip from an FTS hit with one join, which is the schema's contract."""
    path = tmp_path / "ref.sqlite3"
    build(
        [make_clip("cmu_a_01"), make_clip("sbu_hug_01", interaction=(InteractionTag.hug,))],
        path,
        lexicon_sha256="a" * 64,
        built_at=INGESTED_AT,
        builder_version="0.1.0",
    )
    conn = open_index(path)
    try:
        rows = conn.execute(
            """
            SELECT c.clip_id, c.duration_s, bm25(clip_fts, 0.0, 4.0, 2.0, 1.0, 1.0, 1.0) AS score
            FROM clip_fts f JOIN clip c ON c.id = f.rowid
            WHERE clip_fts MATCH 'hug' ORDER BY score
            """
        ).fetchall()
    finally:
        conn.close()
    assert [row["clip_id"] for row in rows] == ["sbu_hug_01"]
    assert rows[0]["duration_s"] == pytest.approx(2.542)


def test_build_leaves_no_staging_file_and_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "ref.sqlite3"
    build(
        [make_clip("cmu_18_19_01")],
        path,
        lexicon_sha256="b" * 64,
        built_at=INGESTED_AT,
        builder_version="0.1.0",
    )
    assert path.is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ref.sqlite3"]


def test_build_rejects_a_duplicate_clip_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="appears twice"):
        build(
            [make_clip("cmu_18_19_01"), make_clip("cmu_18_19_01")],
            tmp_path / "ref.sqlite3",
            lexicon_sha256="c" * 64,
            built_at=INGESTED_AT,
            builder_version="0.1.0",
        )


def test_open_index_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "ref.sqlite3"
    build(
        [make_clip("cmu_18_19_01")],
        path,
        lexicon_sha256="d" * 64,
        built_at=INGESTED_AT,
        builder_version="0.1.0",
    )
    conn = open_index(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM clip")
    finally:
        conn.close()
    with pytest.raises(FileNotFoundError):
        open_index(tmp_path / "missing.sqlite3")


def test_read_library_needs_a_built_index(tmp_path: Path) -> None:
    from content_factory.reference.index import IndexUnavailableError

    conn = create(tmp_path / "ref.sqlite3")
    try:
        with pytest.raises(IndexUnavailableError):
            read_library(conn)
    finally:
        conn.close()


@pytest.mark.skipif(
    not Path("/mnt/fast/reference").is_dir(), reason="reference library not on this host"
)
def test_baked_clip_measurements_survive_the_index(tmp_path: Path) -> None:
    """A two-person baked clip carries closest_wrists_m, so the column is never NULL for one.

    A solo clip has nobody to measure a gap to, and NULL is the honest value there rather than a
    zero that would read as touching.
    """
    import json

    manifest = Path("/mnt/fast/models/blender-assets/clips/manifest.json")
    if not manifest.is_file():
        pytest.skip("baked clip manifest not on this host")
    baked = json.loads(manifest.read_text(encoding="utf-8"))["clips"]
    clips = [
        make_clip(
            entry["name"],
            measured={
                key: value
                for key, value in entry["measured"].items()
                if key in {"closest_wrists_m", "root_gap_m"}
            },
        )
        for entry in baked
    ]
    library = build(
        clips,
        tmp_path / "baked.sqlite3",
        lexicon_sha256="e" * 64,
        built_at=INGESTED_AT,
        builder_version="0.1.0",
        sources=(ReferenceSource.cmu_mocap,),
    )
    assert library.clip_count == len(baked)
    solo = [e for e in baked if "closest_wrists_m" not in e["measured"]]
    assert [e["name"] for e in solo] == ["cmu_35_18", "cmu_16_36"]
    conn = open_index(tmp_path / "baked.sqlite3")
    try:
        missing = {
            row[0]
            for row in conn.execute(
                "SELECT clip_id FROM clip WHERE closest_contact_m IS NULL"
            ).fetchall()
        }
    finally:
        conn.close()
    assert missing == {e["name"] for e in solo}
