"""Tests for the MotionHub and stock reference ingesters.

The synthetic tests build a root in ``tmp_path`` so the shape of the contract is checked on every
host. The tests marked with ``REQUIRES_REFERENCE`` copy small slices of the real library into
``tmp_path`` and run the ingesters over real bytes, which is the only way to catch a change in the
data rather than a change in the code. One of them is not really a test of this module at all: it
re-measures the two EgoBody bodies whose independent world frames are the reason every MotionHub
clip declares ``people_count`` 1, so that claim fails loudly if the data is ever reconverted.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from content_factory.reference.ingest import motionhub, stock
from content_factory.schemas.base import file_sha256
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

REFERENCE_ROOT = Path("/mnt/fast/reference")

REQUIRES_REFERENCE = pytest.mark.skipif(
    not REFERENCE_ROOT.is_dir(), reason="reference library not on this host"
)

INGESTED_AT = "2026-09-07T12:00:00Z"


# --------------------------------------------------------------------------- synthetic MotionHub


def _write_motion(
    path: Path, *, frames: int, fps: int = 30, height: float = 1.15, step: float = 0.0
) -> None:
    """A minimal MotionHub-shaped ``.npz``: only the three arrays the ingester reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    transl = np.zeros((frames, 3), dtype=np.float32)
    transl[:, 0] = np.arange(frames, dtype=np.float32) * step
    transl[:, 1] = height
    np.savez(
        path,
        transl=transl,
        num_frames=np.int64(frames),
        mocap_framerate=np.int32(fps),
    )


def _write_caption(
    path: Path,
    *,
    action: str,
    macro: list[str] | None = None,
    meso: list[str] | None = None,
    category: str = "daily activity",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "category": category,
                "action": action,
                "complexity": "simple",
                "macro": macro or [],
                "meso": meso or [],
                "micro": [],
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """A reference root holding one usable clip in each of the three MotionHub subsets."""
    base = tmp_path / motionhub.MOTIONHUB_ROOT

    ego = base / "EgoBody"
    _write_motion(
        ego / "smplh_52" / "recording_20210929_S15_S11_03" / "body_idx_0" / "000.npz", frames=60
    )
    _write_caption(
        ego / "hierarchical_caption" / "recording_20210929_S15_S11_03" / "body_idx_0" / "000.json",
        action="sit on sofa, chat",
        macro=["A person sits on a sofa and chats."],
    )
    # A motion whose caption never arrived: kept out, and named in the skipped lines.
    _write_motion(
        ego / "smplh_52" / "recording_20210929_S15_S11_03" / "body_idx_1" / "000.npz", frames=60
    )

    grab = base / "GRAB"
    _write_motion(grab / "smplh_52" / "s1" / "airplane_fly_1.npz", frames=90, height=1.14)
    _write_caption(
        grab / "hierarchical_caption" / "s1" / "airplane_fly_1.json",
        action="pick up a toy airplane, fly it in hand",
        macro=["A person picks up a toy airplane and flies it in their hand."],
    )

    hml = base / "HumanML3D_AMASS"
    _write_motion(hml / "smplh_52" / "0000" / "000000.npz", frames=120)
    _write_caption(
        hml / "hierarchical_caption" / "0000" / "000000.json",
        action="stand, perform a flying kick with the left leg",
        macro=["A person performs a flying kick with their left leg."],
        category="martial arts / combat",
    )
    # A caption whose motion has not been downloaded yet.
    _write_caption(
        hml / "hierarchical_caption" / "0000" / "000001.json",
        action="walk forward",
        macro=["A person walks forward."],
    )
    return tmp_path


def test_motionhub_ingests_all_three_subsets(fake_root: Path) -> None:
    clips, _ = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)

    assert [clip.clip_id for clip in clips] == [
        "mh_ego_20210929_s15_s11_03_b0_000",
        "mh_grab_s1_airplane_fly_1",
        "mh_hml_0000_000000",
    ]
    assert [clip.source for clip in clips] == [
        ReferenceSource.motionhub_egobody,
        ReferenceSource.motionhub_grab,
        ReferenceSource.motionhub_humanml3d,
    ]
    for clip in clips:
        # Parameters only, no imagery anywhere in MotionHub.
        assert clip.modality is Modality.mocap_smplh
        assert clip.usage is UsageClass.pose_derivable
        assert clip.pose_format == "smplh"
        assert clip.pose_root is not None and clip.pose_root.endswith(".npz")
        assert clip.caption_source == "dataset"
        assert clip.width is None and clip.height is None


def test_motionhub_declares_one_person_and_no_interpersonal_contact(fake_root: Path) -> None:
    clips, _ = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)

    for clip in clips:
        assert clip.people_count == 1
        assert clip.interaction_tags == (InteractionTag.no_contact,)
        assert not clip.has_absent_tag

    by_source = {clip.source: clip for clip in clips}
    # Only GRAB has a contact that is a fact of the subset: a hand on an object.
    assert by_source[ReferenceSource.motionhub_grab].contact_tags == (ContactTag.object,)
    assert by_source[ReferenceSource.motionhub_egobody].contact_tags == ()
    assert by_source[ReferenceSource.motionhub_humanml3d].contact_tags == ()


def test_motionhub_affection_is_never_affection(fake_root: Path) -> None:
    clips, _ = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)
    by_id = {clip.clip_id: clip for clip in clips}

    assert by_id["mh_hml_0000_000000"].affection is Affection.aggression
    assert by_id["mh_grab_s1_airplane_fly_1"].affection is Affection.neutral
    assert all(clip.affection is not Affection.affection for clip in clips)


def test_motionhub_caption_joins_action_and_sentences(fake_root: Path) -> None:
    clips, _ = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)
    by_id = {clip.clip_id: clip for clip in clips}

    assert by_id["mh_ego_20210929_s15_s11_03_b0_000"].caption == (
        "sit on sofa, chat A person sits on a sofa and chats."
    )


def test_motionhub_caption_drops_a_part_that_would_overflow() -> None:
    long_sentence = "x" * 590
    text = motionhub._caption_text(
        {"action": "wave", "macro": [long_sentence], "meso": ["this one cannot fit"]}
    )

    assert text == f"wave {long_sentence}"
    assert len(text) <= 600


def test_motionhub_posture_comes_from_the_caption_words(fake_root: Path) -> None:
    clips, _ = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)
    by_id = {clip.clip_id: clip for clip in clips}

    # The caption says "sit" even though this synthetic root is at standing height.
    assert by_id["mh_ego_20210929_s15_s11_03_b0_000"].postures == (Posture.sitting,)
    assert by_id["mh_hml_0000_000000"].postures == (Posture.standing,)
    # GRAB captions never mention posture, so the measured root height decides.
    assert by_id["mh_grab_s1_airplane_fly_1"].postures == (Posture.standing,)


def test_motionhub_posture_words_are_whole_tokens() -> None:
    motion = motionhub._Motion(
        frame_count=100,
        native_fps=30.0,
        root_height_median_m=1.13,
        root_height_min_m=1.1,
        root_height_max_m=1.15,
        travel_m=0.1,
        speed_mps=0.03,
    )

    # "transition" contains "sit" and "outstanding" contains "stand": neither is a posture.
    assert motionhub._postures("a transition of outstanding quality", motion) == (Posture.standing,)
    assert motionhub._postures("the person sits down", motion) == (Posture.sitting,)


def test_motionhub_measured_posture_fallback_reads_height_and_speed() -> None:
    def motion(height: float, speed: float) -> motionhub._Motion:
        return motionhub._Motion(
            frame_count=100,
            native_fps=30.0,
            root_height_median_m=height,
            root_height_min_m=height,
            root_height_max_m=height,
            travel_m=speed * 100 / 30.0,
            speed_mps=speed,
        )

    assert motionhub._measured_postures(motion(0.32, 0.0)) == (Posture.lying,)
    assert motionhub._measured_postures(motion(0.70, 0.0)) == (Posture.sitting,)
    assert motionhub._measured_postures(motion(1.13, 0.0)) == (Posture.standing,)
    assert motionhub._measured_postures(motion(1.13, 0.6)) == (Posture.standing, Posture.walking)
    assert motionhub._measured_postures(motion(1.13, 1.9)) == (Posture.running, Posture.standing)


def test_motionhub_measured_numbers_are_real(fake_root: Path) -> None:
    ego = fake_root / motionhub.MOTIONHUB_ROOT / "EgoBody" / "smplh_52"
    _write_motion(
        ego / "recording_20210929_S15_S11_03" / "body_idx_0" / "000.npz",
        frames=31,
        fps=30,
        height=1.2,
        step=0.1,
    )
    clips, _ = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)
    clip = next(c for c in clips if c.clip_id == "mh_ego_20210929_s15_s11_03_b0_000")

    assert clip.frame_count == 31
    assert clip.native_fps == 30.0
    assert clip.duration_s == pytest.approx(31 / 30)
    assert clip.measured["root_height_median_m"] == pytest.approx(1.2, abs=1e-3)
    # 30 steps of 0.1 m along x.
    assert clip.measured["travel_m"] == pytest.approx(3.0, abs=1e-3)
    assert clip.measured["horizontal_speed_mps"] == pytest.approx(3.0 / (31 / 30), abs=1e-3)


def test_motionhub_files_are_relative_and_really_digested(fake_root: Path) -> None:
    clips, _ = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)
    clip = clips[0]

    assert [f.role for f in clip.files] == ["smpl", "annotation"]
    for entry in clip.files:
        assert not entry.path.startswith("/")
        absolute = fake_root / entry.path
        assert absolute.is_file()
        assert entry.sha256 == file_sha256(absolute)
        assert entry.size_bytes == absolute.stat().st_size


def test_motionhub_names_a_missing_caption_and_a_missing_motion(fake_root: Path) -> None:
    _, skipped = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)
    joined = "\n".join(skipped)

    assert "recording_20210929_S15_S11_03/body_idx_1/000" in joined
    assert "no hierarchical_caption .json" in joined
    assert "0000/000001" in joined
    assert "no smplh_52 .npz on disk" in joined


def test_motionhub_always_says_humanml3d_is_incomplete(fake_root: Path) -> None:
    _, skipped = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)

    incomplete = [line for line in skipped if "INCOMPLETE" in line]
    assert len(incomplete) == 1
    assert "humanml3d" in incomplete[0]
    assert str(motionhub.HUMANML3D_REMOTE_FILES) in incomplete[0]


def test_motionhub_groups_a_large_gap_instead_of_naming_every_file(tmp_path: Path) -> None:
    base = tmp_path / motionhub.MOTIONHUB_ROOT / "HumanML3D_AMASS"
    for index in range(motionhub._NAMED_ABSENCE_LIMIT + 3):
        _write_caption(
            base / "hierarchical_caption" / "0000" / f"00000{index}.json", action="walk forward"
        )

    _, skipped = motionhub.ingest(tmp_path, ingested_at=INGESTED_AT)
    grouped = [line for line in skipped if line.startswith("motionhub humanml3d 0000:")]

    assert len(grouped) == 1
    assert "11 captioned motions in this directory" in grouped[0]
    assert "000000" not in grouped[0]


def test_motionhub_missing_root_is_reported_not_raised(tmp_path: Path) -> None:
    clips, skipped = motionhub.ingest(tmp_path, ingested_at=INGESTED_AT)

    assert clips == []
    assert skipped == [
        "motionhub: MotionHub/raw is not on disk, no MotionHub clip was ingested",
    ]


def test_motionhub_skips_an_unreadable_npz(fake_root: Path) -> None:
    truncated = (
        fake_root
        / motionhub.MOTIONHUB_ROOT
        / "EgoBody"
        / "smplh_52"
        / "recording_20210929_S15_S11_03"
        / "body_idx_0"
        / "000.npz"
    )
    truncated.write_bytes(b"PK\x03\x04 half a download")

    clips, skipped = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)

    assert all(clip.source is not ReferenceSource.motionhub_egobody for clip in clips)
    assert any("unreadable .npz" in line and "still downloading" in line for line in skipped)


def test_motionhub_skips_a_frame_count_that_disagrees_with_the_data(fake_root: Path) -> None:
    path = fake_root / motionhub.MOTIONHUB_ROOT / "GRAB" / "smplh_52" / "s1" / "airplane_fly_1.npz"
    np.savez(
        path,
        transl=np.zeros((10, 3), dtype=np.float32),
        num_frames=np.int64(90),
        mocap_framerate=np.int32(30),
    )

    clips, skipped = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)

    assert all(clip.source is not ReferenceSource.motionhub_grab for clip in clips)
    assert any("num_frames says 90 but transl holds 10 rows" in line for line in skipped)


def test_motionhub_is_deterministic(fake_root: Path) -> None:
    first_clips, first_skipped = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)
    second_clips, second_skipped = motionhub.ingest(fake_root, ingested_at=INGESTED_AT)

    assert [c.canonical_json() for c in first_clips] == [c.canonical_json() for c in second_clips]
    assert first_skipped == second_skipped
    assert first_skipped == sorted(first_skipped)


def test_motionhub_declares_its_three_sources() -> None:
    assert motionhub.SOURCE in motionhub.SOURCES
    assert set(motionhub.SOURCES) == {
        ReferenceSource.motionhub_egobody,
        ReferenceSource.motionhub_grab,
        ReferenceSource.motionhub_humanml3d,
    }
    assert {subset.source for subset in motionhub.SUBSETS} == set(motionhub.SOURCES)


# ------------------------------------------------------------------------------- synthetic stock


def test_stock_absent_directory_returns_no_clips(tmp_path: Path) -> None:
    clips, skipped = stock.ingest(tmp_path, ingested_at=INGESTED_AT)

    assert clips == []
    assert skipped == ["pexels Stock/pexels: directory is not on disk, stock is fetched per shot"]


def test_stock_empty_directory_is_silent(tmp_path: Path) -> None:
    (tmp_path / stock.PEXELS_DIR).mkdir(parents=True)

    assert stock.ingest(tmp_path, ingested_at=INGESTED_AT) == ([], [])


def test_stock_skips_a_file_nobody_has_looked_at(tmp_path: Path) -> None:
    directory = tmp_path / stock.PEXELS_DIR
    directory.mkdir(parents=True)
    (directory / "9999999.mp4").write_bytes(b"not really an mp4")
    (directory / "notes.txt").write_text("fetched for shot 3", encoding="utf-8")

    clips, skipped = stock.ingest(tmp_path, ingested_at=INGESTED_AT)

    assert clips == []
    assert skipped == [
        "pexels 9999999.mp4: no verified annotation, and a stock clip is tagged only after a "
        "frame has been looked at",
        "pexels notes.txt: not a video file",
    ]


def test_stock_reports_a_file_ffprobe_cannot_read(tmp_path: Path) -> None:
    directory = tmp_path / stock.PEXELS_DIR
    directory.mkdir(parents=True)
    known = next(iter(stock.VERIFIED_CLIPS))
    (directory / f"{known}.mp4").write_bytes(b"\x00" * 64)

    clips, skipped = stock.ingest(tmp_path, ingested_at=INGESTED_AT)

    assert clips == []
    assert len(skipped) == 1
    assert skipped[0].startswith(f"pexels {known}.mp4: ")


def test_stock_verified_annotations_satisfy_the_contract() -> None:
    """Every hand-written annotation must build a legal clip, tags sorted and coherent."""
    for pexels_id, verified in stock.VERIFIED_CLIPS.items():
        clip = ReferenceClip(
            clip_id=f"pexels_{pexels_id}",
            source=stock.SOURCE,
            source_ref=f"pexels/{pexels_id}",
            modality=Modality.video_rgb,
            usage=UsageClass.pixels_usable,
            people_count=verified.people_count,
            affection=verified.affection,
            interaction_tags=verified.interaction_tags,
            contact_tags=verified.contact_tags,
            postures=verified.postures,
            setting=verified.setting,
            camera_angles=verified.camera_angles,
            frame_count=1,
            native_fps=25.0,
            duration_s=0.04,
            width=16,
            height=16,
            pose_format="none",
            caption=verified.caption,
            caption_source="operator",
            files=(ReferenceFile(role="video", path="x.mp4", sha256="0" * 64, size_bytes=1),),
            ingested_at=INGESTED_AT,
            ingester_version=stock.INGESTER_VERSION,
        )
        assert not clip.has_absent_tag
        # hold_hands_walk is the tempting tag and it is wrong for a seated hand-hold.
        assert InteractionTag.hold_hands_walk not in clip.interaction_tags


def test_stock_frame_rate_and_frame_count_helpers() -> None:
    assert stock._frame_rate("25/1") == 25.0
    assert stock._frame_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert stock._frame_rate("0/0") is None
    assert stock._frame_rate(None) is None

    assert stock._frame_count({"nb_frames": "150"}, {}, 25.0) == 150
    # No nb_frames: the duration and the frame rate answer instead.
    assert stock._frame_count({"duration": "6.0"}, {}, 25.0) == 150
    assert stock._frame_count({}, {"duration": "6.0"}, 25.0) == 150
    assert stock._frame_count({}, {}, 25.0) is None


# ----------------------------------------------------------------------------------- real data


@REQUIRES_REFERENCE
def test_egobody_bodies_do_not_share_a_world_frame() -> None:
    """The measurement behind ``people_count`` 1 for every MotionHub clip.

    Both bodies of one recording start at exactly the world origin in x and z, so a pair cannot be
    read as one two-person clip. If MotionHub is ever reconverted with a shared frame, this fails
    and the ingester's people_count has to be revisited.
    """
    recording = (
        REFERENCE_ROOT
        / motionhub.MOTIONHUB_ROOT
        / "EgoBody"
        / "smplh_52"
        / "recording_20210929_S15_S11_03"
    )
    if not recording.is_dir():
        pytest.skip("EgoBody recording_20210929_S15_S11_03 is not on this host")

    with np.load(recording / "body_idx_0" / "000.npz", allow_pickle=False) as data:
        first = np.asarray(data["transl"], dtype=np.float64)
    with np.load(recording / "body_idx_1" / "000.npz", allow_pickle=False) as data:
        second = np.asarray(data["transl"], dtype=np.float64)

    assert first[0, 0] == 0.0 and first[0, 2] == 0.0
    assert second[0, 0] == 0.0 and second[0, 2] == 0.0
    frames = min(len(first), len(second))
    gap = np.linalg.norm(first[:frames, [0, 2]] - second[:frames, [0, 2]], axis=1)
    # Two adults cannot occupy one point, so the shared origin is a re-origining artefact.
    assert gap[0] == 0.0
    assert gap.max() > 1.0


@REQUIRES_REFERENCE
def test_motionhub_ingests_a_real_slice(tmp_path: Path) -> None:
    """A copy of real EgoBody, GRAB and HumanML3D directories, ingested over real bytes."""
    source = REFERENCE_ROOT / motionhub.MOTIONHUB_ROOT
    destination = tmp_path / motionhub.MOTIONHUB_ROOT
    wanted = {
        "EgoBody": ["recording_20210929_S15_S11_03"],
        "GRAB": ["s1"],
        "HumanML3D_AMASS": ["0000"],
    }
    copied = 0
    for subset, names in wanted.items():
        for kind in ("smplh_52", "hierarchical_caption"):
            for name in names:
                original = source / subset / kind / name
                if original.is_dir():
                    shutil.copytree(original, destination / subset / kind / name)
                    copied += 1
    if copied == 0:
        pytest.skip("none of the sampled MotionHub directories are on this host")

    clips, skipped = motionhub.ingest(tmp_path, ingested_at=INGESTED_AT)

    assert clips
    assert [clip.clip_id for clip in clips] == sorted(clip.clip_id for clip in clips)
    assert {clip.people_count for clip in clips} == {1}
    for clip in clips:
        assert clip.caption
        assert clip.frame_count >= 1
        assert clip.native_fps > 0
        for entry in clip.files:
            assert (tmp_path / entry.path).is_file()
    # Real captions and real measured numbers, quoted from the files on disk.
    by_id = {clip.clip_id: clip for clip in clips}
    known = by_id.get("mh_ego_20210929_s15_s11_03_b0_000")
    if known is not None:
        assert known.caption.startswith("catch a pen, toss it into the air")
        assert (known.frame_count, known.native_fps) == (542, 30.0)
    grab = by_id.get("mh_grab_s1_airplane_fly_1")
    if grab is not None:
        assert "airplane" in grab.caption
        assert grab.contact_tags == (ContactTag.object,)
    assert any("INCOMPLETE" in line for line in skipped)


@REQUIRES_REFERENCE
def test_stock_ingests_the_real_pexels_clip() -> None:
    clip_path = REFERENCE_ROOT / stock.PEXELS_DIR / "4701507.mp4"
    if not clip_path.is_file():
        pytest.skip("the pexels clip is not on this host")

    clips, skipped = stock.ingest(REFERENCE_ROOT, ingested_at=INGESTED_AT)
    by_id = {clip.clip_id: clip for clip in clips}
    clip = by_id["pexels_4701507"]

    # ffprobe's numbers, not the filename's.
    assert (clip.width, clip.height) == (3840, 2160)
    assert clip.native_fps == 25.0
    assert clip.frame_count == 150
    assert clip.duration_s == pytest.approx(6.0)
    assert clip.people_count == 2
    assert clip.affection is Affection.affection
    assert clip.contact_tags == (ContactTag.hands,)
    assert clip.usage is UsageClass.pixels_usable
    assert clip.pose_format == "none" and clip.pose_root is None
    assert clip.files[0].sha256 == file_sha256(clip_path)
    assert skipped == []
