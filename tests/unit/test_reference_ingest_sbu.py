"""SBU Kinect ingester: the joint mapping, the normalisation, and the real 282-sequence tree.

The synthetic half builds a miniature reference root, so the shape of the output, the skip lines
and the determinism are testable on a host with no data. The measured half runs against
``/mnt/fast/reference`` and is skipped when it is not mounted; it is the half that checks the
numbers the module claims, including the 13.75 % of joints that fall outside the frame.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from content_factory.reference.ingest import sbu
from content_factory.schemas.reference import (
    Affection,
    ContactTag,
    ReferenceClip,
    ReferenceSource,
)
from content_factory.sequences.control_compile import OPENPOSE18, OPENPOSE18_LIMBS

REFERENCE_ROOT = Path("/mnt/fast/reference")
needs_reference = pytest.mark.skipif(
    not REFERENCE_ROOT.is_dir(), reason="reference library not on this host"
)

INGESTED_AT = "2026-09-07T12:00:00Z"

_CLASS_MAPS = {
    "sbu_kinect": {
        "classes": {
            "01": {"label": "approaching", "affection": "staging"},
            "02": {"label": "departing", "affection": "staging"},
            "03": {"label": "kicking", "affection": False},
            "04": {"label": "pushing", "affection": False},
            "05": {"label": "shaking_hands", "affection": True},
            "06": {"label": "hugging", "affection": True},
            "07": {"label": "exchanging_objects", "affection": True},
            "08": {"label": "punching", "affection": False},
        }
    }
}


def _png(width: int, height: int) -> bytes:
    """A valid, all-black PNG of the given size, so _png_size has a real header to read."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _number(value: float | list[float]) -> float:
    """One measured scalar, so a list-valued key fails the test instead of being coerced."""
    assert isinstance(value, float)
    return value


def _skeleton_row(number: int, hand_gap: float, out_of_frame: bool = False) -> str:
    """One 91-value row: two people a fixed distance apart, right hands ``hand_gap`` apart in x."""
    values: list[float] = [float(number)]
    for person in range(2):
        base_x = 0.40 if person == 0 else 0.40 + hand_gap
        for joint in range(15):
            x, y, z = base_x, 0.20 + 0.05 * joint, 2.0
            if joint == 8:  # r_hand, the joint the two people reach with
                x = base_x + (0.05 if person == 0 else -0.05)
                y = 0.50
            if out_of_frame and joint == 14:  # r_foot below the frame
                y = 1.30
            values.extend((x, y, z))
    return ",".join(f"{value:.5f}" for value in values)


def _write_sequence(
    root: Path, pair: str, action: str, run: str, rows: list[str]
) -> dict[str, object]:
    """One sequence directory on disk plus the sbu.json row that points at it."""
    rel = f"SBU-Kinect/clean/{pair}/{action}/{run}"
    seq = root / rel
    seq.mkdir(parents=True)
    (seq / "skeleton_pos.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    for row in rows:
        number = int(float(row.split(",")[0]))
        (seq / f"rgb_{number:06d}.png").write_bytes(_png(640, 480))
    return {
        "pair": pair,
        "action_id": action,
        "run": run,
        "frames": len(rows),
        "skeleton_columns": 91,
        "people": 2,
        "joints_per_person": 15,
        "rgb_frames": len(rows),
        "depth_frames": len(rows),
        "path": rel,
    }


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """A miniature reference root: two good sequences and one with a short skeleton row."""
    rows = [
        _skeleton_row(100, 0.30),
        _skeleton_row(101, 0.10, out_of_frame=True),  # the closest frame, and one joint outside
        _skeleton_row(102, 0.25),
    ]
    index = [
        _write_sequence(tmp_path, "s01s02", "05", "001", rows),
        _write_sequence(tmp_path, "s01s02", "06", "001", rows),
        _write_sequence(tmp_path, "s09s09", "03", "001", [_skeleton_row(1, 0.4)[:-8]]),
    ]
    measured = tmp_path / "_index" / "measured"
    measured.mkdir(parents=True)
    (measured / "class_maps.json").write_text(json.dumps(_CLASS_MAPS), encoding="utf-8")
    (measured / "sbu.json").write_text(json.dumps(index), encoding="utf-8")
    return tmp_path


# --- the mapping table -------------------------------------------------------------------------


def test_every_mapped_slot_is_an_openpose18_joint() -> None:
    mapped = [slot for _kinect, slot in sbu._KINECT15_TO_OPENPOSE18 if slot is not None]
    assert len(sbu._KINECT15_TO_OPENPOSE18) == 15
    assert len(mapped) == 14
    assert len(set(mapped)) == 14
    assert set(mapped) <= set(OPENPOSE18)


def test_the_joints_with_no_source_are_named_and_are_exactly_the_gap() -> None:
    mapped = {slot for _kinect, slot in sbu._KINECT15_TO_OPENPOSE18 if slot is not None}
    assert set(sbu.OPENPOSE18_WITHOUT_SOURCE) == set(OPENPOSE18) - mapped
    assert sbu.OPENPOSE18_WITHOUT_SOURCE == ("l_ear", "l_eye", "r_ear", "r_eye")
    unslotted = tuple(k for k, slot in sbu._KINECT15_TO_OPENPOSE18 if slot is None)
    assert unslotted == sbu.KINECT15_WITHOUT_SLOT == ("torso",)


def test_class_specs_cover_the_eight_action_ids_with_legal_vocabulary() -> None:
    assert sorted(sbu._SPECS) == ["01", "02", "03", "04", "05", "06", "07", "08"]
    for action_id, spec in sorted(sbu._SPECS.items()):
        contact = [tag.value for tag in spec.contact]
        assert contact == sorted(set(contact)), action_id
        if ContactTag.none in spec.contact:
            assert len(spec.contact) == 1, action_id
        assert spec.affection in tuple(Affection)
        assert spec.moment.rule in ("hands", "torsos", "hand_chest", "foot_chest")


# --- the synthetic root ------------------------------------------------------------------------


def test_ingest_emits_one_clip_per_good_sequence_sorted_by_id(fake_root: Path) -> None:
    clips, skipped = sbu.ingest(fake_root, ingested_at=INGESTED_AT)
    assert [clip.clip_id for clip in clips] == ["sbu_s01s02_05_001", "sbu_s01s02_06_001"]
    assert [clip.clip_id for clip in clips] == sorted(clip.clip_id for clip in clips)
    assert all(clip.source is ReferenceSource.sbu_kinect for clip in clips)
    assert all(clip.ingested_at == INGESTED_AT for clip in clips)
    assert any("s09s09/03/001" in line and "90 values" in line for line in skipped), skipped


def test_clip_carries_the_shape_the_task_specifies(fake_root: Path) -> None:
    clip = sbu.ingest(fake_root, ingested_at=INGESTED_AT)[0][0]
    assert clip.modality.value == "rgbd_skeleton"
    assert clip.usage.value == "pose_derivable"
    assert clip.pose_format == "kinect15"
    assert clip.people_count == 2
    assert clip.native_fps == 15.0
    assert clip.frame_count == 3
    assert clip.duration_s == pytest.approx(0.2)
    assert (clip.width, clip.height) == (640, 480)
    assert clip.pose_root == "SBU-Kinect/clean/s01s02/05/001"
    assert clip.source_ref == "s01s02/05/001"
    assert clip.affection is Affection.affection
    assert clip.contact_tags == (ContactTag.hands,)


def test_key_pose_is_the_closest_contact_frame_with_openpose_names(fake_root: Path) -> None:
    clip = sbu.ingest(fake_root, ingested_at=INGESTED_AT)[0][0]
    (pose,) = clip.key_poses
    assert pose.frame_index == 1  # the row where the two right hands are closest
    assert pose.label == "contact"
    assert clip.measured["key_pose_source_frame"] == 101.0
    assert [person.person_index for person in pose.people] == [0, 1]
    for person in pose.people:
        assert set(person.joints) <= set(OPENPOSE18)
        assert len(person.joints) == 14
        assert not set(person.joints) & set(sbu.OPENPOSE18_WITHOUT_SOURCE)
        assert set(person.bones) <= set(OPENPOSE18_LIMBS)
        for a, b in person.bones:
            assert a in person.joints and b in person.joints


def test_joints_outside_the_frame_are_kept_and_flagged(fake_root: Path) -> None:
    clip = sbu.ingest(fake_root, ingested_at=INGESTED_AT)[0][0]
    (pose,) = clip.key_poses
    ankle = pose.people[0].joints["r_ankle"]
    assert ankle.y == pytest.approx(1.30)  # not clamped
    assert ankle.in_frame is False
    assert ankle.usable is False
    assert pose.people[0].joints["neck"].in_frame is True
    # One joint per person on the middle row only: 2 of the sequence's 90 observations.
    assert clip.measured["out_of_frame_joint_fraction"] == pytest.approx(2 / 90, abs=1e-4)


def test_depth_is_converted_to_metres_by_the_documented_factor(fake_root: Path) -> None:
    clip = sbu.ingest(fake_root, ingested_at=INGESTED_AT)[0][0]
    (pose,) = clip.key_poses
    assert pose.people[0].joints["neck"].z == pytest.approx(2.0 * 10000 / 7.8125 / 1000)


def test_files_are_digested_for_real(fake_root: Path) -> None:
    clip = sbu.ingest(fake_root, ingested_at=INGESTED_AT)[0][0]
    assert [file.role for file in clip.files] == ["skeleton", "thumbnail"]
    for file in clip.files:
        path = fake_root / file.path
        assert file.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert file.size_bytes == path.stat().st_size
    assert clip.files[1].path.endswith("rgb_000101.png")


def test_ingest_is_deterministic(fake_root: Path) -> None:
    first = sbu.ingest(fake_root, ingested_at=INGESTED_AT)
    second = sbu.ingest(fake_root, ingested_at=INGESTED_AT)
    assert [clip.model_dump_json() for clip in first[0]] == [
        clip.model_dump_json() for clip in second[0]
    ]
    assert first[1] == second[1]


def test_clips_round_trip_through_the_contract(fake_root: Path) -> None:
    for clip in sbu.ingest(fake_root, ingested_at=INGESTED_AT)[0]:
        assert ReferenceClip.model_validate_json(clip.model_dump_json()) == clip


def test_a_missing_index_is_one_readable_line(tmp_path: Path) -> None:
    clips, skipped = sbu.ingest(tmp_path, ingested_at=INGESTED_AT)
    assert clips == []
    assert len(skipped) == 1
    assert "class_maps.json" in skipped[0]


def test_a_relabelled_class_is_refused_rather_than_retagged(fake_root: Path) -> None:
    maps = json.loads((fake_root / "_index/measured/class_maps.json").read_text(encoding="utf-8"))
    maps["sbu_kinect"]["classes"]["05"]["label"] = "high_fiving"
    (fake_root / "_index/measured/class_maps.json").write_text(json.dumps(maps), encoding="utf-8")
    clips, skipped = sbu.ingest(fake_root, ingested_at=INGESTED_AT)
    assert [clip.clip_id for clip in clips] == ["sbu_s01s02_06_001"]
    assert any("sbu action 05" in line and "high_fiving" in line for line in skipped), skipped


def test_an_unknown_action_id_is_reported_rather_than_dropped(fake_root: Path) -> None:
    index = json.loads((fake_root / "_index/measured/sbu.json").read_text(encoding="utf-8"))
    rows = [_skeleton_row(200, 0.2), _skeleton_row(201, 0.1)]
    index.append(_write_sequence(fake_root, "s01s02", "09", "001", rows))
    (fake_root / "_index/measured/sbu.json").write_text(json.dumps(index), encoding="utf-8")
    clips, skipped = sbu.ingest(fake_root, ingested_at=INGESTED_AT)
    assert [clip.clip_id for clip in clips] == ["sbu_s01s02_05_001", "sbu_s01s02_06_001"]
    assert any("s01s02/09/001" in line and "'09'" in line for line in skipped), skipped


def test_an_empty_class_map_stops_the_pass_with_one_line(fake_root: Path) -> None:
    (fake_root / "_index/measured/class_maps.json").write_text("{}", encoding="utf-8")
    clips, skipped = sbu.ingest(fake_root, ingested_at=INGESTED_AT)
    assert clips == []
    assert len(skipped) == 1 and "no verified sbu_kinect class map" in skipped[0]


# --- the library on disk -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_clips() -> tuple[list[ReferenceClip], list[str]]:
    return sbu.ingest(REFERENCE_ROOT, ingested_at=INGESTED_AT)


@needs_reference
def test_all_282_sequences_become_clips(real_clips: tuple[list[ReferenceClip], list[str]]) -> None:
    clips, skipped = real_clips
    assert len(clips) == 282
    assert skipped == []
    assert len({clip.clip_id for clip in clips}) == 282
    assert [clip.clip_id for clip in clips] == sorted(clip.clip_id for clip in clips)
    assert len({clip.source_ref.split("/")[0] for clip in clips}) == 21


@needs_reference
def test_every_class_is_present_with_the_verified_affection(
    real_clips: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = real_clips
    maps = json.loads(
        (REFERENCE_ROOT / "_index/measured/class_maps.json").read_text(encoding="utf-8")
    )
    classes = maps["sbu_kinect"]["classes"]
    seen = set()
    for clip in clips:
        action_id = clip.source_ref.split("/")[1]
        seen.add(action_id)
        expected = sbu._AFFECTION_BY_MAP_VALUE[str(classes[action_id]["affection"]).lower()]
        assert clip.affection is expected, clip.clip_id
        assert clip.interaction_tags == (sbu._SPECS[action_id].tag,)
    assert seen == set(classes)


@needs_reference
def test_the_library_reproduces_the_contract_s_out_of_frame_share(
    real_clips: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = real_clips
    frames = sum(clip.frame_count for clip in clips)
    outside = sum(
        _number(clip.measured["out_of_frame_joint_fraction"]) * clip.frame_count for clip in clips
    )
    assert frames == 6822
    # The contract's ReferenceJoint docstring says 13.75 % of the SBU joints leave the frame.
    assert outside / frames == pytest.approx(0.1375, abs=0.0005)


@needs_reference
def test_key_poses_sit_on_real_frames_with_both_people(
    real_clips: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = real_clips
    for clip in clips:
        (pose,) = clip.key_poses
        assert 0 <= pose.frame_index < clip.frame_count
        assert len(pose.people) == 2
        assert all(len(person.joints) == 14 for person in pose.people)
        source = int(_number(clip.measured["key_pose_source_frame"]))
        assert (REFERENCE_ROOT / f"{clip.pose_root}/rgb_{source:06d}.png").is_file()


@needs_reference
def test_the_contact_classes_touch_and_the_strikes_do_not(
    real_clips: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = real_clips
    by_class: dict[str, list[float]] = {}
    for clip in clips:
        action_id = clip.source_ref.split("/")[1]
        gap = _number(clip.measured["hand_to_other_chest_m"])
        by_class.setdefault(action_id, []).append(gap)
    median = {key: sorted(values)[len(values) // 2] for key, values in by_class.items()}
    # Hugging and pushing reach the chest; punching stops short, which is why 08 carries no
    # contact tag. The numbers are the ones the module docstring quotes.
    assert median["06"] < 0.25
    assert median["04"] < 0.30
    assert median["08"] > 0.35
    hugs = [clip for clip in clips if clip.source_ref.split("/")[1] == "06"]
    assert all(clip.contact_tags == (ContactTag.back, ContactTag.torso) for clip in hugs)
    punches = [clip for clip in clips if clip.source_ref.split("/")[1] == "08"]
    assert all(clip.contact_tags == (ContactTag.none,) for clip in punches)


@needs_reference
def test_real_files_exist_and_are_digested(
    real_clips: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = real_clips
    for clip in clips:
        assert [file.role for file in clip.files] == ["skeleton", "thumbnail"]
        for file in clip.files:
            assert (REFERENCE_ROOT / file.path).is_file()
    sample = clips[0].files[0]
    assert sample.sha256 == hashlib.sha256((REFERENCE_ROOT / sample.path).read_bytes()).hexdigest()


@needs_reference
def test_real_ingest_is_deterministic() -> None:
    first, first_skipped = sbu.ingest(REFERENCE_ROOT, ingested_at=INGESTED_AT)
    second, second_skipped = sbu.ingest(REFERENCE_ROOT, ingested_at=INGESTED_AT)
    assert [clip.model_dump_json() for clip in first] == [clip.model_dump_json() for clip in second]
    assert first_skipped == second_skipped
