"""The CMU ingester: the two kinds of clip it makes, and the things it refuses to claim.

The synthetic tests build a whole fake reference root, so they run on a host with no data. The
tests marked with ``needs_reference`` are the ones that check the real 2514 trials and the 55
baked clips, including that a parsed frame count agrees with a full line count of the AMC.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from content_factory.reference.ingest import cmu
from content_factory.schemas.reference import (
    ABSENT_INTERACTIONS,
    Affection,
    ContactTag,
    InteractionTag,
    Modality,
    Posture,
    ReferenceSource,
    UsageClass,
)

INGESTED_AT = "2026-09-07T00:00:00Z"
REFERENCE_ROOT = Path("/mnt/fast/reference")

needs_reference = pytest.mark.skipif(
    not REFERENCE_ROOT.is_dir(), reason="reference library not on this host"
)


def write_amc(path: Path, frames: int) -> None:
    """A minimal AMC: the two header lines, then numbered frame blocks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#!OML:ASF test.asf", ":FULLY-SPECIFIED", ":DEGREES"]
    for index in range(1, frames + 1):
        lines.append(str(index))
        lines.append("root 1.0 2.0 3.0 4.0 5.0 6.0")
        lines.append("lowerback 0.1 0.2 0.3")
        lines.append("rfingers 7.12502")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_root(tmp_path: Path) -> Path:
    """A fake reference root with one baked clip and six trials, one of each interesting kind."""
    root = tmp_path / "reference"
    clips = tmp_path / "models" / "blender-assets" / "clips"
    clips.mkdir(parents=True)
    measured = root / "_index" / "measured"
    measured.mkdir(parents=True)

    for subject in ("01", "02", "03", "18", "19", "22", "117"):
        asf = root / "CMU-Mocap" / "all_asfamc" / "subjects" / subject / f"{subject}.asf"
        asf.parent.mkdir(parents=True, exist_ok=True)
        asf.write_text(f":version 1.10\n:name {subject}\n", encoding="utf-8")
    for subject, trial, frames in (
        ("01", "01", 240),
        ("02", "01", 60),
        ("03", "01", 30),
        ("18", "01", 300),
        ("19", "01", 300),
        ("22", "05", 120),
        ("117", "01", 90),
    ):
        write_amc(
            root / "CMU-Mocap" / "all_asfamc" / "subjects" / subject / f"{subject}_{trial}.amc",
            frames,
        )
    avi = root / "CMU-Mocap" / "avi" / "subjects" / "01" / "01_01.avi"
    avi.parent.mkdir(parents=True)
    avi.write_bytes(b"RIFF fake stick figure on black")

    def row(subject: str, trial_id: str, description: str, category: str, fps: int | None):
        return {
            "subject": subject,
            "trial": trial_id,
            "description": description,
            "category": category,
            "framerate": fps,
            "indexed": fps is not None,
            "amc": f"CMU-Mocap/all_asfamc/subjects/{subject}/{subject}_{trial_id}.amc",
            "asf": f"CMU-Mocap/all_asfamc/subjects/{subject}/{subject}.asf",
            "avi": (
                f"CMU-Mocap/avi/subjects/{subject}/{subject}_{trial_id}.avi"
                if (subject, trial_id) == ("01", "01")
                else None
            ),
            "amc_bytes": 1,
        }

    rows = [
        row("01", "01", "walk to the bench and sit down", "everyday activities", 120),
        row("02", "01", "", "Boxing", 60),
        row("03", "01", "climb ladder", "playground", 120),
        row("117", "01", "", "", None),
        row("22", "05", "comfort", "human interaction", 120),
        row("04", "01", "walk", "walking", 120),
        row("18", "01", "walk, shake hands (2 subjects - subject A)", "human interaction", 120),
        row("19", "01", "walk, shake hands (2 subjects - subject B)", "human interaction", 120),
    ]
    (measured / "cmu.json").write_text(json.dumps(rows), encoding="utf-8")
    (measured / "class_maps.json").write_text(
        json.dumps({"cmu_two_person_subjects": {"pairs": [["18", "19"], ["22", "23"]]}}),
        encoding="utf-8",
    )

    (clips / "cmu_18_19_01.json").write_text(
        json.dumps({"schema": "cf.clip.v2", "name": "cmu_18_19_01"}), encoding="utf-8"
    )
    (clips / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "cf.clip_library.v1",
                "dataset": "cmu-mocap",
                "fps": 24,
                "clips": [
                    {
                        "name": "cmu_18_19_01",
                        "subjects": ["18", "19"],
                        "trial": "01",
                        "description": "walk, shake hands",
                        "interaction": "handshake",
                        "affection": "affection",
                        "contact": ["hands"],
                        "posture": ["walking", "standing"],
                        "people": 2,
                        "flags": [],
                        "measured": {
                            "frames": 61,
                            "duration_s": 2.542,
                            "closest_wrists_m": 0.1763,
                            "ground_offset": 0.03548,
                            "root_gap_m": [0.738, 1.9803],
                            "travel_m": {"a": 0.583, "b": 0.716},
                            "yaw_range_deg": {"a": [-177.2, 176.9], "b": [-135.8, -82.8]},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_shape_and_ids(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    clips, skipped = cmu.ingest(root, ingested_at=INGESTED_AT)

    assert cmu.SOURCE is ReferenceSource.cmu_mocap
    assert [c.clip_id for c in clips] == ["cmu_01_01", "cmu_02_01", "cmu_18_19_01"]
    assert all(c.source is ReferenceSource.cmu_mocap for c in clips)
    assert all(c.ingested_at == INGESTED_AT for c in clips)
    assert all(c.ingester_version == cmu.INGESTER_VERSION for c in clips)
    assert all(c.usage is UsageClass.pose_derivable for c in clips)

    # Every skip names the trial and the reason, one line each.
    assert sorted(skipped) == [
        "cmu 03_01: 'climb ladder' names no posture the vocabulary can express (climbing,"
        " hanging, swimming, crawling, tumbling), and calling it standing would be a lie",
        "cmu 04_01: CMU-Mocap/all_asfamc/subjects/04/04_01.amc is not on disk",
        "cmu 117_01: CMU never indexed this trial, so there is no frame rate for it and the AMC"
        " does not carry one",
        "cmu 22_05: subject 22 is half of an A/B pair and no baked clip covers this trial, so"
        " there is no honest people_count for it",
    ]


def test_baked_clip_is_the_one_a_shot_can_name(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    clips, _ = cmu.ingest(root, ingested_at=INGESTED_AT)
    baked = next(c for c in clips if c.clip_id == "cmu_18_19_01")

    assert baked.modality is Modality.mocap_segments
    assert baked.pose_format == "cf_clip_v2"
    assert baked.retargeted_clip == "cmu_18_19_01"
    assert baked.people_count == 2
    assert baked.affection is Affection.affection
    assert baked.interaction_tags == (InteractionTag.handshake,)
    assert baked.contact_tags == (ContactTag.hands,)
    assert baked.postures == (Posture.standing, Posture.walking)
    assert (baked.frame_count, baked.native_fps, baked.duration_s) == (61, 24.0, 2.542)
    assert baked.caption == "walk, shake hands"
    assert baked.caption_source == "parsed_index"
    assert baked.source_ref == "subjects/18/18_01.amc+subjects/19/19_01.amc"
    # travel_m and yaw_range_deg are per actor in the manifest, so they are split, not averaged.
    assert baked.measured == {
        "closest_wrists_m": 0.1763,
        "ground_offset": 0.03548,
        "root_gap_m": [0.738, 1.9803],
        "travel_m_a": 0.583,
        "travel_m_b": 0.716,
        "yaw_range_deg_a": [-177.2, 176.9],
        "yaw_range_deg_b": [-135.8, -82.8],
    }

    (only,) = baked.files
    assert only.role == "clip_json"
    # The baked clips live beside the weights, so the path relative to root climbs out of it.
    assert only.path == "../models/blender-assets/clips/cmu_18_19_01.json"
    target = (root / only.path).resolve()
    assert target.is_file()
    assert only.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert only.size_bytes == target.stat().st_size


def test_trial_clip_claims_no_interaction_and_hashes_its_files(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    clips, _ = cmu.ingest(root, ingested_at=INGESTED_AT)
    solo = next(c for c in clips if c.clip_id == "cmu_01_01")

    assert solo.modality is Modality.mocap_skeleton
    assert solo.pose_format == "asf_amc"
    assert solo.pose_root == "CMU-Mocap/all_asfamc/subjects/01"
    assert solo.people_count == 1
    assert solo.affection is Affection.neutral
    assert solo.interaction_tags == (InteractionTag.no_contact,)
    assert solo.contact_tags == ()
    assert solo.postures == (Posture.sitting, Posture.walking)
    assert solo.frame_count == 240
    assert solo.native_fps == 120.0
    assert solo.duration_s == pytest.approx(2.0)
    assert solo.caption_source == "parsed_index"
    assert [f.role for f in solo.files] == ["skeleton", "skeleton_def", "video"]
    for file in solo.files:
        target = root / file.path
        assert file.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
        assert file.size_bytes == target.stat().st_size

    # An empty description cell falls back to the subject category, and says the caption is ours.
    empty = next(c for c in clips if c.clip_id == "cmu_02_01")
    assert (empty.caption, empty.caption_source) == ("Boxing", "derived")
    assert empty.postures == (cmu.FALLBACK_POSTURE,)
    assert [f.role for f in empty.files] == ["skeleton", "skeleton_def"]


def test_same_inputs_same_bytes(tmp_path: Path) -> None:
    root = build_root(tmp_path)
    first, first_skipped = cmu.ingest(root, ingested_at=INGESTED_AT)
    second, second_skipped = cmu.ingest(root, ingested_at=INGESTED_AT)
    assert [c.canonical_json() for c in first] == [c.canonical_json() for c in second]
    assert first_skipped == second_skipped


def test_missing_manifest_and_index_are_reported_not_guessed(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    root.mkdir()
    clips, skipped = cmu.ingest(root, ingested_at=INGESTED_AT)
    assert clips == []
    assert len(skipped) == 3
    assert any("class_maps.json" in line for line in skipped)
    assert any("manifest.json" in line for line in skipped)
    assert any("_index/measured/cmu.json" in line for line in skipped)


def test_frame_count_is_parsed_not_estimated(tmp_path: Path) -> None:
    path = tmp_path / "t.amc"
    write_amc(path, 7)
    assert cmu.amc_frame_count(path) == 7
    # A window smaller than one frame block still lands on the right answer, by widening.
    assert cmu.amc_frame_count(path, window=8) == 7
    headers = tmp_path / "empty.amc"
    headers.write_text("#!OML:ASF x.asf\n:FULLY-SPECIFIED\n:DEGREES\n", encoding="utf-8")
    assert cmu.amc_frame_count(headers) is None


def test_postures_from_text_is_conservative() -> None:
    assert cmu.postures_from_text("walk, shake hands") == (Posture.walking,)
    assert cmu.postures_from_text("sit down, stand up") == (Posture.sitting, Posture.standing)
    # Camel case is CMU's own spelling on 212 trials.
    assert cmu.postures_from_text("NormalWalk") == (Posture.walking,)
    assert cmu.postures_from_text("StartJog") == (Posture.running,)
    # Whole words only: no posture hides inside "clean" or "visit".
    assert cmu.postures_from_text("clean") == (cmu.FALLBACK_POSTURE,)
    assert cmu.postures_from_text("visit the store") == (cmu.FALLBACK_POSTURE,)
    assert cmu.postures_from_text("") == (cmu.FALLBACK_POSTURE,)
    assert cmu.posture_outside_vocabulary("playground - climb, hang, swing")
    assert not cmu.posture_outside_vocabulary("walk, shake hands")


@needs_reference
def test_real_library_two_kinds_of_clip() -> None:
    clips, skipped = cmu.ingest(REFERENCE_ROOT, ingested_at=INGESTED_AT)
    manifest = json.loads(
        (REFERENCE_ROOT / cmu.CLIP_LIBRARY / cmu.CLIP_MANIFEST).read_text(encoding="utf-8")
    )
    rows = json.loads((REFERENCE_ROOT / cmu.TRIALS_JSON).read_text(encoding="utf-8"))

    baked = [c for c in clips if c.modality is Modality.mocap_segments]
    solo = [c for c in clips if c.modality is Modality.mocap_skeleton]
    # Read from the manifest rather than pinned: the clip library grows, and another session
    # adding a clip should not read as a broken ingester. Twice already it has.
    assert len(baked) == manifest["counts"]["clips"]
    assert len(baked) >= 55
    assert {c.clip_id for c in baked} == {c["name"] for c in manifest["clips"]}
    assert all(c.retargeted_clip == c.clip_id for c in baked)
    assert all(c.pose_format == "cf_clip_v2" for c in baked)
    # 55 two-person takes and 2 solo running trials: the library holds no two-person run, so a
    # beat that needs somebody running reaches a single-subject sprint.
    assert sorted(c.people_count for c in baked) == [1, 1] + [2] * 55
    assert {c.clip_id for c in baked if c.people_count == 1} == {"cmu_16_36", "cmu_35_18"}
    assert all(Posture.running in c.postures for c in baked if c.people_count == 1)
    assert all((REFERENCE_ROOT / f.path).is_file() for c in clips for f in c.files)

    # Every trial is accounted for: baked, solo, or skipped by name. 112 of the 2514 are the two
    # halves of the 55 baked takes plus the 2 solo trials that were baked.
    covered = {(s, c["trial"]) for c in manifest["clips"] for s in c["subjects"]}
    assert len(covered) == 112
    assert len(solo) + len(skipped) == len(rows) - len(covered)
    no_framerate = [r for r in rows if r["framerate"] is None]
    assert len(no_framerate) == 113
    assert sum(1 for line in skipped if "no frame rate" in line) == 113
    # A covered trial is never ingested a second time as a raw AMC: the baked clip is the
    # stageable form of it, and a solo trial that was baked appears only as that.
    assert all(f"cmu_{s}_{t}" not in {c.clip_id for c in solo} for s, t in covered)

    assert all(c.usage is UsageClass.pose_derivable for c in clips)
    assert len({c.clip_id for c in clips}) == len(clips)
    assert [c.clip_id for c in clips] == sorted(c.clip_id for c in clips)
    assert not any(c.has_absent_tag for c in clips)
    assert all(c.interaction_tags == (InteractionTag.no_contact,) for c in solo)
    assert all(not c.contact_tags for c in solo)
    assert all(set(c.interaction_tags) - ABSENT_INTERACTIONS for c in baked)


@needs_reference
def test_real_frame_counts_agree_with_a_full_line_count() -> None:
    clips, _ = cmu.ingest(REFERENCE_ROOT, ingested_at=INGESTED_AT)
    solo = [c for c in clips if c.modality is Modality.mocap_skeleton]
    assert len(solo) > 2000
    # Full-counting all 2248 AMCs takes eleven seconds, so a fixed stride samples them instead.
    for clip in solo[::97]:
        amc = REFERENCE_ROOT / next(f.path for f in clip.files if f.role == "skeleton")
        counted = 0
        with amc.open("rb") as handle:
            for line in handle:
                if line.strip().isdigit():
                    counted += 1
        assert counted == clip.frame_count, clip.clip_id
        assert clip.duration_s == pytest.approx(clip.frame_count / clip.native_fps)
