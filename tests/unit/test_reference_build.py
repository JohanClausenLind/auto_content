"""The build driver, exercised through a hand-made ingester rather than a real one.

The ingest package is synthesised into ``sys.modules`` with its ``__path__`` pointing at a
temporary directory, so discovery, import and the uniform call shape are all the real code paths
while nothing here depends on which of the seven real ingesters has been written yet.
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from content_factory.reference import build, index
from content_factory.schemas.reference import ReferenceClip, ReferenceLibrary

AT = "2026-09-07T00:00:00Z"

FAKE_INGESTER = '''
"""Two hand-made clips, so the driver can be tested without a dataset."""

from __future__ import annotations

import hashlib
from pathlib import Path

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

SOURCE = ReferenceSource.cmu_mocap
INGESTER_VERSION = "1.0.0"


def _file(relpath: str) -> ReferenceFile:
    return ReferenceFile(
        role="clip_json",
        path=relpath,
        sha256=hashlib.sha256(relpath.encode("utf-8")).hexdigest(),
        size_bytes=len(relpath),
    )


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """Two clips and one skip. Returned out of clip_id order on purpose."""
    hug = ReferenceClip(
        clip_id="fake_hug_two",
        source=SOURCE,
        source_ref="subjects/22/22_08.amc",
        modality=Modality.mocap_segments,
        usage=UsageClass.pose_derivable,
        people_count=2,
        affection=Affection.affection,
        interaction_tags=(InteractionTag.hand_on_shoulder, InteractionTag.hug),
        contact_tags=(ContactTag.shoulder,),
        postures=(Posture.standing,),
        setting="studio",
        frame_count=240,
        native_fps=120.0,
        duration_s=2.0,
        pose_format="cf_clip_v2",
        pose_root="clips/fake_hug_two.json",
        retargeted_clip="fake_hug_two",
        caption="B comforts A, puts one hand on A's shoulder",
        caption_source="parsed_index",
        measured={"closest_wrists_m": 0.12},
        files=(_file("CMU-Mocap/all_asfamc/subjects/22/22_08.amc"),),
        ingested_at=ingested_at,
        ingester_version=INGESTER_VERSION,
    )
    walk = ReferenceClip(
        clip_id="fake_walk_two",
        source=SOURCE,
        source_ref="subjects/22/22_09.amc",
        modality=Modality.mocap_segments,
        usage=UsageClass.pose_derivable,
        people_count=2,
        affection=Affection.neutral,
        interaction_tags=(InteractionTag.walk_together,),
        contact_tags=(ContactTag.hands,),
        postures=(Posture.walking,),
        setting="studio",
        frame_count=360,
        native_fps=120.0,
        duration_s=3.0,
        pose_format="cf_clip_v2",
        pose_root="clips/fake_walk_two.json",
        retargeted_clip="fake_walk_two",
        caption="A and B walk together holding hands",
        caption_source="parsed_index",
        files=(_file("CMU-Mocap/all_asfamc/subjects/22/22_09.amc"),),
        ingested_at=ingested_at,
        ingester_version=INGESTER_VERSION,
    )
    return [walk, hug], ["cmu: 22_10.amc has no description"]
'''

MULTI_SOURCE_INGESTER = '''
"""One module, three ReferenceSource values, the way MotionHub really is."""

from __future__ import annotations

import hashlib
from pathlib import Path

from content_factory.schemas.reference import (
    Affection,
    InteractionTag,
    Modality,
    Posture,
    ReferenceClip,
    ReferenceFile,
    ReferenceSource,
    UsageClass,
)

SOURCE = ReferenceSource.motionhub_egobody
SOURCES = (
    ReferenceSource.motionhub_egobody,
    ReferenceSource.motionhub_grab,
    ReferenceSource.motionhub_humanml3d,
)
INGESTER_VERSION = "1.0.0"


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """One clip per subset, so a module owning three sources is exercised end to end."""
    clips = []
    for subset in SOURCES:
        relpath = f"MotionHub/raw/{subset.value}/a.npz"
        clips.append(
            ReferenceClip(
                clip_id=f"mh_{subset.value}_one",
                source=subset,
                source_ref=relpath,
                modality=Modality.mocap_segments,
                usage=UsageClass.pose_derivable,
                people_count=1,
                affection=Affection.neutral,
                interaction_tags=(InteractionTag.no_contact,),
                postures=(Posture.standing,),
                frame_count=120,
                native_fps=30.0,
                duration_s=4.0,
                pose_format="cf_clip_v2",
                pose_root=f"clips/mh_{subset.value}_one.json",
                caption=f"a person moves, {subset.value}",
                caption_source="dataset",
                files=(
                    ReferenceFile(
                        role="clip_json",
                        path=relpath,
                        sha256=hashlib.sha256(relpath.encode("utf-8")).hexdigest(),
                        size_bytes=len(relpath),
                    ),
                ),
                ingested_at=ingested_at,
                ingester_version=INGESTER_VERSION,
            )
        )
    return clips, []
'''

HALF_WRITTEN = '''
"""An ingester someone started: no ingest(), no SOURCE, so the driver must not call it."""

VERSION = "0.0.1"
'''


@pytest.fixture
def fake_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Put a fake ingest package in front of the real one, and take it away again."""
    pkg_dir = tmp_path / "ingesters"
    pkg_dir.mkdir()
    (pkg_dir / "cmu.py").write_text(FAKE_INGESTER, encoding="utf-8")
    (pkg_dir / "half_written.py").write_text(HALF_WRITTEN, encoding="utf-8")
    package = types.ModuleType(build.INGEST_PACKAGE)
    package.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, build.INGEST_PACKAGE, package)
    prefix = f"{build.INGEST_PACKAGE}."
    for name in [key for key in sys.modules if key.startswith(prefix)]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield pkg_dir
    for name in [key for key in list(sys.modules) if key.startswith(prefix)]:
        sys.modules.pop(name, None)


@pytest.fixture
def multi_source_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fake ingest package holding one module that owns three sources."""
    pkg_dir = tmp_path / "multi"
    pkg_dir.mkdir()
    (pkg_dir / "motionhub.py").write_text(MULTI_SOURCE_INGESTER, encoding="utf-8")
    package = types.ModuleType(build.INGEST_PACKAGE)
    package.__path__ = [str(pkg_dir)]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, build.INGEST_PACKAGE, package)
    prefix = f"{build.INGEST_PACKAGE}."
    for name in [key for key in sys.modules if key.startswith(prefix)]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield pkg_dir
    for name in [key for key in list(sys.modules) if key.startswith(prefix)]:
        sys.modules.pop(name, None)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A reference tree with nothing in it but the directory itself."""
    tree = tmp_path / "reference"
    tree.mkdir()
    return tree


def test_discovery_finds_the_conforming_module_and_names_the_other(fake_ingest: Path) -> None:
    ingesters, nonconforming = build.discover_ingesters()
    assert [(i.name, i.source.value) for i in ingesters] == [("cmu", "cmu_mocap")]
    assert nonconforming == ("half_written",)


def test_only_selects_by_module_name_or_source_value(fake_ingest: Path) -> None:
    assert [i.name for i in build.discover_ingesters(("cmu",))[0]] == ["cmu"]
    assert [i.name for i in build.discover_ingesters(("cmu_mocap",))[0]] == ["cmu"]


def test_only_naming_an_unknown_source_fails_loudly(fake_ingest: Path) -> None:
    with pytest.raises(build.BuildError, match="not an ingester"):
        build.discover_ingesters(("sbu",))


def test_documents_land_on_disk_as_canonical_json(fake_ingest: Path, root: Path) -> None:
    report = build.build_library(root, ingested_at=AT, built_at=AT)

    clips_dir = root / "_index" / "clips"
    assert sorted(p.name for p in clips_dir.glob("*.json")) == [
        "fake_hug_two.json",
        "fake_walk_two.json",
    ]
    assert report.documents_written == 2
    assert report.documents_unchanged == 0
    assert report.clip_count == 2
    assert [(s.name, s.clips, s.skips, s.unstamped) for s in report.sources] == [("cmu", 2, 1, 0)]
    assert report.skipped == ("cmu: 22_10.amc has no description",)

    doc = clips_dir / "fake_hug_two.json"
    clip = ReferenceClip.model_validate_json(doc.read_bytes())
    assert doc.read_bytes() == clip.canonical_json().encode("utf-8")
    assert clip.ingested_at == AT


def test_the_index_has_the_rows(fake_ingest: Path, root: Path) -> None:
    report = build.build_library(root, ingested_at=AT, built_at=AT)
    assert report.index_path.is_file()

    conn = index.open_index(report.index_path)
    try:
        rows = conn.execute(
            "SELECT clip_id, people_count, doc_sha256 FROM clip ORDER BY id"
        ).fetchall()
        matched = conn.execute("SELECT clip_id FROM clip_fts WHERE clip_fts MATCH 'hug'").fetchall()
        library = index.read_library(conn)
    finally:
        conn.close()

    assert [row["clip_id"] for row in rows] == ["fake_hug_two", "fake_walk_two"]
    assert [row["people_count"] for row in rows] == [2, 2]
    assert [row["clip_id"] for row in matched] == ["fake_hug_two"]
    assert library.clip_count == 2
    assert library.built_at == AT
    assert library.sources == ("cmu_mocap",)
    assert library.manifest_sha256 == report.manifest_sha256


def test_the_manifest_is_written_beside_the_index(fake_ingest: Path, root: Path) -> None:
    report = build.build_library(root, ingested_at=AT, built_at=AT)
    written = ReferenceLibrary.model_validate_json((root / "_index" / "library.json").read_bytes())
    assert written == report.library


def test_building_twice_gives_the_same_manifest_and_touches_nothing(
    fake_ingest: Path, root: Path
) -> None:
    first = build.build_library(root, ingested_at=AT, built_at=AT)
    index_mtime = first.index_path.stat().st_mtime_ns

    second = build.build_library(root, ingested_at=AT, built_at=AT)

    assert second.manifest_sha256 == first.manifest_sha256
    assert first.rebuilt is True
    assert second.rebuilt is False
    assert second.documents_written == 0
    assert second.documents_unchanged == 2
    assert second.index_path.stat().st_mtime_ns == index_mtime


def test_a_document_no_ingester_produces_any_more_is_pruned(fake_ingest: Path, root: Path) -> None:
    build.build_library(root, ingested_at=AT, built_at=AT)
    clips_dir = root / "_index" / "clips"
    stale = clips_dir / "fake_gone_two.json"
    stale.write_bytes(
        (clips_dir / "fake_hug_two.json").read_bytes().replace(b"fake_hug_two", b"fake_gone_two")
    )

    report = build.build_library(root, ingested_at=AT, built_at=AT)

    assert not stale.exists()
    assert report.documents_pruned == 1
    assert report.clip_count == 2


def test_a_hand_edited_document_fails_the_build_instead_of_being_dropped(
    fake_ingest: Path, root: Path
) -> None:
    build.build_library(root, ingested_at=AT, built_at=AT)
    (root / "_index" / "clips" / "fake_broken_two.json").write_text("{}", encoding="utf-8")
    with pytest.raises(build.BuildError, match="not a valid ReferenceClip"):
        build.build_library(root, ingested_at=AT, built_at=AT)


def test_check_is_quiet_when_the_manifest_holds_and_loud_when_it_moves(
    fake_ingest: Path, root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert build.main(["--root", str(root), "--at", AT]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    totals = json.loads(lines[-1])
    assert totals["clips"] == 2
    assert totals["ingesters"] == 1
    assert totals["nonconforming"] == ["half_written"]
    assert totals["rebuilt"] is True

    assert build.main(["--root", str(root), "--check", "--at", AT]) == 0
    same = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert same["check"] == "same"
    assert same["manifest_sha256"] == totals["manifest_sha256"]

    assert build.main(["--root", str(root), "--check", "--at", "2020-01-01T00:00:00Z"]) == 1
    moved = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert moved["check"] == "differs"
    assert moved["manifest_sha256"] != totals["manifest_sha256"]


def test_check_leaves_the_tree_alone(fake_ingest: Path, root: Path) -> None:
    same, live, fresh, report = build.check(root, ingested_at=AT, built_at=AT)
    assert same is True
    assert live == fresh == report.manifest_sha256
    assert not (root / "_index").exists()


def test_a_missing_tree_is_an_error_not_an_empty_library(
    fake_ingest: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert build.main(["--root", str(tmp_path / "nope")]) == 2
    assert "no reference tree" in capsys.readouterr().out


def test_a_tree_with_no_ingesters_builds_an_empty_index(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, build.INGEST_PACKAGE, None)  # type: ignore[arg-type]
    monkeypatch.delitem(sys.modules, build.INGEST_PACKAGE)
    monkeypatch.setattr(build, "INGEST_PACKAGE", "content_factory.reference.no_such_ingest")
    report = build.build_library(root, ingested_at=AT, built_at=AT)
    assert report.sources == ()
    assert report.clip_count == 0
    assert report.index_path.is_file()
    assert "no ingesters found" in "\n".join(build.report_lines(report))


def test_lexicon_digest_of_a_missing_file_is_the_digest_of_nothing(tmp_path: Path) -> None:
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert build.lexicon_digest(tmp_path / "absent.json") == empty
    present = tmp_path / "lexicon.v1.json"
    present.write_bytes(b"{}")
    assert build.lexicon_digest(present) != empty


@pytest.mark.skipif(
    not Path("/mnt/fast/reference").is_dir(), reason="reference library not on this host"
)
def test_the_documents_already_in_the_real_tree_are_valid_and_stable() -> None:
    """Whatever the real ingesters have written so far must read back as the contract."""
    clips_dir = build.DEFAULT_ROOT / "_index" / "clips"
    if not clips_dir.is_dir():
        pytest.skip("no clip documents built on this host yet")
    clips = build.read_documents(clips_dir)
    assert index.manifest_sha256(clips) == index.manifest_sha256(build.read_documents(clips_dir))


def test_a_module_declaring_a_sources_tuple_may_emit_all_of_them(
    multi_source_ingest: Path, root: Path
) -> None:
    """MotionHub is one module covering three sources; SOURCE alone must not gate its clips."""
    ingesters, _ = build.discover_ingesters()
    assert [s.value for s in ingesters[0].sources] == [
        "motionhub_egobody",
        "motionhub_grab",
        "motionhub_humanml3d",
    ]

    report = build.build_library(root, ingested_at=AT, built_at=AT)

    assert report.clip_count == 3
    assert report.library is not None
    assert report.library.sources == (
        "motionhub_egobody",
        "motionhub_grab",
        "motionhub_humanml3d",
    )
    assert report.totals()["sources"] == [
        "motionhub_egobody",
        "motionhub_grab",
        "motionhub_humanml3d",
    ]


def test_the_printed_line_stays_aligned_for_a_multi_source_module(
    multi_source_ingest: Path, root: Path
) -> None:
    report = build.build_library(root, ingested_at=AT, built_at=AT)
    line = build.report_lines(report)[0]
    assert line.startswith("motionhub  motionhub_egobody+2 ")
    assert "clips=    3" in line


def test_only_selects_a_multi_source_module_by_any_of_its_sources(
    multi_source_ingest: Path,
) -> None:
    for name in ("motionhub", "motionhub_egobody", "motionhub_humanml3d"):
        assert [i.name for i in build.discover_ingesters((name,))[0]] == ["motionhub"], name


def test_a_run_prunes_every_source_the_module_owns(multi_source_ingest: Path, root: Path) -> None:
    """A subset's stale document is the owning module's to delete, not another module's."""
    build.build_library(root, ingested_at=AT, built_at=AT)
    clips_dir = root / "_index" / "clips"
    stale = clips_dir / "mh_motionhub_grab_gone.json"
    stale.write_bytes(
        (clips_dir / "mh_motionhub_grab_one.json")
        .read_bytes()
        .replace(b"mh_motionhub_grab_one", b"mh_motionhub_grab_gone")
    )

    report = build.build_library(root, ingested_at=AT, built_at=AT)

    assert not stale.exists()
    assert report.documents_pruned == 1
