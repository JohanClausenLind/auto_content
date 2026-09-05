"""The Harmony4D ingester: the arithmetic offline, the two real takes when the library is mounted.

The pure-geometry tests run anywhere. The ones that touch ``/mnt/fast/reference`` are skipped on a
host without the data, and they assert the numbers this ingester claims rather than only its shape:
a wrong handedness or a wrong camera alignment would still produce a valid ``ReferenceClip``, so
the facing, the azimuth ring and the reprojection error are all checked against measurements.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from content_factory.reference.ingest import harmony4d
from content_factory.schemas.reference import (
    Affection,
    ContactTag,
    InteractionTag,
    Modality,
    Posture,
    ReferenceSource,
    UsageClass,
)
from content_factory.sequences.control_compile import OPENPOSE18

REFERENCE_ROOT = Path("/mnt/fast/reference")
requires_library = pytest.mark.skipif(
    not Path("/mnt/fast/reference").is_dir(), reason="reference library not on this host"
)
INGESTED_AT = "2026-09-07T00:00:00Z"


# --- offline: the arithmetic ------------------------------------------------------------------


def test_module_shape() -> None:
    assert harmony4d.SOURCE is ReferenceSource.harmony4d
    assert harmony4d.INGESTER_VERSION == "0.1.0"


def test_missing_dataset_is_reported_not_crashed(tmp_path: Path) -> None:
    clips, skipped = harmony4d.ingest(tmp_path, ingested_at=INGESTED_AT)
    assert clips == []
    assert any("not on this host" in line for line in skipped)


@pytest.mark.parametrize(
    ("azimuth", "bucket"),
    [
        (0.0, "front"),
        (22.5, "front"),
        (-22.5, "front"),
        (22.6, "front_3q"),
        (-45.0, "front_3q"),
        (67.5, "front_3q"),
        (90.0, "side"),
        (-112.5, "side"),
        (135.0, "back_3q"),
        (157.5, "back_3q"),
        (-179.0, "back"),
        (180.0, "back"),
    ],
)
def test_bucket_edges(azimuth: float, bucket: str) -> None:
    assert harmony4d._bucket(azimuth) == bucket


def test_quaternion_to_rotation_is_orthonormal() -> None:
    quat = np.array([0.5, -0.5, 0.5, 0.5])
    rotation = harmony4d._quaternion_to_rotation(quat)
    assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)
    assert np.isclose(np.linalg.det(rotation), 1.0)
    # A 90 degree turn about +z sends +x to +y.
    turn = harmony4d._quaternion_to_rotation(
        np.array([np.cos(np.pi / 4), 0.0, 0.0, np.sin(np.pi / 4)])
    )
    assert np.allclose(turn @ np.array([1.0, 0.0, 0.0]), [0.0, 1.0, 0.0], atol=1e-12)


def test_project_matches_a_pinhole_when_undistorted() -> None:
    camera = harmony4d._Camera(
        "OPENCV_FISHEYE", 400, 200, (100.0, 100.0, 200.0, 100.0, 0.0, 0.0, 0.0, 0.0)
    )
    points = np.array([[0.0, 0.0, 2.0], [1.0, 0.0, 2.0]])
    uv = harmony4d._project(points, camera)
    assert np.allclose(uv[0], [0.5, 0.5])
    # theta = atan(0.5) rather than 0.5: the fisheye model is not a pinhole even with zero k.
    expected_x = (100.0 * np.arctan(0.5) / 0.5 * 0.5 + 200.0) / 400.0
    assert np.isclose(uv[1, 0], expected_x)


def test_project_does_not_clamp_joints_that_leave_the_frame() -> None:
    """A joint outside the frame keeps the coordinate the capture implies, as the contract wants."""
    camera = harmony4d._Camera(
        "OPENCV_FISHEYE", 400, 200, (300.0, 300.0, 200.0, 100.0, 0.0, 0.0, 0.0, 0.0)
    )
    uv = harmony4d._project(np.array([[-1.0, 0.0, 1.0], [1.0, 0.0, 1.0]]), camera)
    assert uv[0, 0] < 0.0
    assert uv[1, 0] > 1.0


def test_openpose18_derives_the_neck_from_the_shoulders() -> None:
    pose = np.zeros((17, 3))
    pose[harmony4d._L_SHOULDER] = [0.0, 0.2, 1.4]
    pose[harmony4d._R_SHOULDER] = [0.0, -0.2, 1.4]
    names, world = harmony4d._openpose18_world(pose)
    assert set(names) == set(OPENPOSE18)
    assert len(names) == 18
    assert names[-1] == "neck"
    assert np.allclose(world[-1], [0.0, 0.0, 1.4])


def test_facing_comes_out_of_the_chest() -> None:
    pose = np.zeros((17, 3))
    # Shoulders on the y axis: a z-up right-handed frame then puts the chest along +x.
    pose[harmony4d._L_SHOULDER] = [0.0, 0.2, 1.4]
    pose[harmony4d._R_SHOULDER] = [0.0, -0.2, 1.4]
    assert np.allclose(harmony4d._facing(pose), [1.0, 0.0, 0.0])


def _synthetic_pair(separation: float, wrap: bool) -> np.ndarray:
    """Two people facing each other ``separation`` apart, arms wrapped or hanging."""
    poses = np.zeros((1, 2, 17, 3))
    for index, sign in ((0, -1.0), (1, 1.0)):
        x = sign * separation / 2.0
        pose = poses[0, index]
        pose[harmony4d._L_SHOULDER] = [x, -0.2 * sign, 1.4]
        pose[harmony4d._R_SHOULDER] = [x, 0.2 * sign, 1.4]
        pose[harmony4d._L_HIP] = [x, -0.1 * sign, 1.0]
        pose[harmony4d._R_HIP] = [x, 0.1 * sign, 1.0]
        reach = -sign * (separation / 2.0 + 0.2) if wrap else 0.0
        pose[harmony4d._L_ELBOW] = [x + reach * 0.5, -0.25 * sign, 1.2]
        pose[harmony4d._R_ELBOW] = [x + reach * 0.5, 0.25 * sign, 1.2]
        pose[harmony4d._L_WRIST] = [x + reach, -0.15 * sign, 1.2]
        pose[harmony4d._R_WRIST] = [x + reach, 0.15 * sign, 1.2]
    return poses


def test_contact_tags_are_derived_from_the_geometry() -> None:
    close = _synthetic_pair(0.3, wrap=True)
    centres = harmony4d._torso_centres(close)
    assert harmony4d._contact_tags(close, centres, 0) == (
        ContactTag.arm,
        ContactTag.back,
        ContactTag.torso,
    )
    apart = _synthetic_pair(2.0, wrap=False)
    assert harmony4d._contact_tags(apart, harmony4d._torso_centres(apart), 0) == ()


def test_azimuth_gaps_close_the_circle() -> None:
    gaps = harmony4d._azimuth_gaps({"a": -170.0, "b": -10.0, "c": 100.0})
    assert np.isclose(sum(gaps), 360.0)
    assert np.isclose(max(gaps), 160.0)


def test_representatives_pick_the_camera_nearest_each_bucket_centre() -> None:
    azimuths = {"cam01": 2.0, "cam02": -40.0, "cam03": 46.0, "cam04": 178.0}
    buckets = {view: harmony4d._bucket(value) for view, value in azimuths.items()}
    chosen = harmony4d._representatives(azimuths, buckets)
    assert chosen["front"] == "cam01"
    assert chosen["front_3q"] == "cam03"
    assert chosen["back"] == "cam04"
    assert "side" not in chosen


def test_key_frames_are_the_measured_moments() -> None:
    gap = np.concatenate([np.full(10, 1.5), np.full(5, 0.3), np.full(10, 1.5)])
    geometry = harmony4d._Geometry(
        torso_centres=np.zeros((25, 2, 3)),
        torso_gap=gap,
        closest_torso_m=0.3,
        contact_frame=int(np.argmin(gap)),
        embrace_first=10,
        embrace_last=14,
        closest_wrists_m=0.2,
        root_gap_min=0.3,
        root_gap_max=1.6,
        travel_m=(1.0, 1.0),
    )
    assert harmony4d._key_frames(geometry) == (
        (0, "start"),
        (10, "contact"),
        (14, "release"),
        (24, "end"),
    )


# --- the two real takes ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ingested() -> tuple[list, list[str]]:
    return harmony4d.ingest(REFERENCE_ROOT, ingested_at=INGESTED_AT)


@requires_library
def test_both_takes_are_ingested(ingested: tuple[list, list[str]]) -> None:
    clips, _ = ingested
    assert [clip.clip_id for clip in clips] == ["h4d_test_002_hugging", "h4d_train_001_hugging"]
    assert [clip.clip_id for clip in clips] == sorted(clip.clip_id for clip in clips)


@requires_library
def test_the_clip_says_what_the_material_is(ingested: tuple[list, list[str]]) -> None:
    clips, _ = ingested
    for clip in clips:
        assert clip.source is ReferenceSource.harmony4d
        assert clip.modality is Modality.multiview_rgb
        # Every frame has a tripod between the lens and the subjects, so never pixels_usable.
        assert clip.usage is UsageClass.pose_derivable
        assert clip.affection is Affection.affection
        assert clip.interaction_tags == (InteractionTag.hug,)
        assert clip.contact_tags == (ContactTag.arm, ContactTag.back, ContactTag.torso)
        # Both takes walk in and out around a stationary embrace, so both postures are earned.
        assert clip.postures == (Posture.standing, Posture.walking)
        travel = clip.measured["travel_m"]
        assert isinstance(travel, list)
        assert min(travel) > 1.0
        assert clip.people_count == 2
        assert clip.view_count == 22
        assert clip.frame_count == 301
        assert clip.native_fps == 20.0
        assert clip.duration_s == pytest.approx(15.05)
        assert (clip.width, clip.height) == (3840, 2160)
        assert clip.pose_format == "coco17_3d"
        assert clip.pose_root == f"{clip.source_ref}/processed_data/poses3d"
        assert clip.ingested_at == INGESTED_AT
        assert not clip.has_absent_tag


@requires_library
def test_the_camera_ring_is_derived_and_complete(ingested: tuple[list, list[str]]) -> None:
    clips, _ = ingested
    for clip in clips:
        assert clip.camera_angles == ("front", "front_3q", "side", "back_3q", "back")
        azimuths = clip.measured["camera_azimuth_deg"]
        assert isinstance(azimuths, list)
        assert len(azimuths) == 22
        assert azimuths == sorted(azimuths)
        assert min(azimuths) > -180.0 and max(azimuths) <= 180.0
        smallest, largest = clip.measured["camera_azimuth_gap_deg"]  # type: ignore[misc]
        assert largest < 30.0, "a gap this wide would mean the ring is not complete"
        assert smallest > 5.0
        near, far = clip.measured["camera_radius_m"]  # type: ignore[misc]
        assert 1.0 < near <= far < 3.0, "the rig stands within a few metres of the pair"


@requires_library
def test_the_calibration_was_verified_not_assumed(ingested: tuple[list, list[str]]) -> None:
    clips, _ = ingested
    for clip in clips:
        error = clip.measured["calibration_reproj_px"]
        assert isinstance(error, float)
        # Reprojecting the take's own SMPL joints through the chosen alignment lands on the
        # dataset's own poses2d to well under a pixel of a 3840 wide frame.
        assert error < 1.0


@requires_library
def test_the_subjects_face_each_other_at_the_contact_frame(
    ingested: tuple[list, list[str]],
) -> None:
    """The handedness claim the azimuth zero rests on, checked against the poses themselves."""
    clips, _ = ingested
    for clip in clips:
        frame = int(clip.measured["contact_frame"])  # type: ignore[arg-type]
        path = REFERENCE_ROOT / clip.source_ref / f"processed_data/poses3d/{frame + 1:05d}.npy"
        loaded = harmony4d._load_pickled_npy(path)
        assert isinstance(loaded, dict)
        poses = np.stack([np.asarray(loaded[key])[:, :3] for key in sorted(loaded)])[None, :]
        centres = harmony4d._torso_centres(poses)
        toward = centres[0, 1] - centres[0, 0]
        toward[2] = 0.0
        toward /= np.linalg.norm(toward)
        assert harmony4d._facing(poses[0, 0]) @ toward > 0.5
        assert harmony4d._facing(poses[0, 1]) @ -toward > 0.5


@requires_library
def test_the_embrace_is_measured(ingested: tuple[list, list[str]]) -> None:
    clips, _ = ingested
    expected = {
        "h4d_train_001_hugging": (0.2251, 34.0),
        "h4d_test_002_hugging": (0.2498, 155.0),
    }
    for clip in clips:
        closest, contact_frame = expected[clip.clip_id]
        assert clip.measured["closest_torso_m"] == pytest.approx(closest, abs=1e-3)
        assert clip.measured["contact_frame"] == contact_frame
        wrists = clip.measured["closest_wrists_m"]
        assert isinstance(wrists, float)
        assert 0.1 < wrists < 0.25
        first, last = clip.measured["embrace_frames"]  # type: ignore[misc]
        assert first < contact_frame <= last


@requires_library
def test_key_poses_are_openpose18_in_one_camera(ingested: tuple[list, list[str]]) -> None:
    clips, _ = ingested
    for clip in clips:
        assert [pose.label for pose in clip.key_poses] == ["start", "contact", "release", "end"]
        frames = [pose.frame_index for pose in clip.key_poses]
        assert frames == sorted(set(frames))
        cameras = {pose.camera for pose in clip.key_poses}
        assert len(cameras) == 1 and next(iter(cameras)).startswith("cam")
        for pose in clip.key_poses:
            assert [person.person_index for person in pose.people] == [0, 1]
            for person in pose.people:
                assert set(person.joints) == set(OPENPOSE18)
                assert person.bones
                depths = [joint.z for joint in person.joints.values()]
                # Metric depth along the camera axis: the pair moves between 0.9 m and 3.3 m
                # from the front camera over the take, and never behind it.
                assert all(depth is not None and 0.5 < depth < 4.0 for depth in depths)
                assert person.to_skeleton_pose() is not None
        # Ankles drop below the bottom edge in several frames. They are kept as measured, not
        # clamped, which is the whole point of ReferenceJoint.in_frame.
        outside = [
            joint
            for pose in clip.key_poses
            for person in pose.people
            for joint in person.joints.values()
            if not joint.in_frame
        ]
        assert outside
        assert all(
            joint.y > 1.0 or joint.y < 0.0 or joint.x > 1.0 or joint.x < 0.0 for joint in outside
        )
        contact = next(pose for pose in clip.key_poses if pose.label == "contact")
        assert contact.contact == (ContactTag.arm, ContactTag.back, ContactTag.torso)
        assert next(pose for pose in clip.key_poses if pose.label == "start").contact == ()


@requires_library
def test_the_neck_is_the_projected_shoulder_midpoint(ingested: tuple[list, list[str]]) -> None:
    """Derived in 3D then projected, so the 2D neck lands between the two 2D shoulders."""
    clips, _ = ingested
    for clip in clips:
        for pose in clip.key_poses:
            for person in pose.people:
                neck = person.joints["neck"]
                left = person.joints["l_shoulder"]
                right = person.joints["r_shoulder"]
                assert min(left.x, right.x) - 1e-6 <= neck.x <= max(left.x, right.x) + 1e-6
                assert min(left.y, right.y) - 1e-6 <= neck.y <= max(left.y, right.y) + 1e-6


@requires_library
def test_files_are_the_ones_a_consumer_needs_and_digested_for_real(
    ingested: tuple[list, list[str]],
) -> None:
    from content_factory.schemas.base import file_sha256

    clips, _ = ingested
    for clip in clips:
        roles = {file.role for file in clip.files}
        assert {"calibration", "poses3d", "smpl", "poses2d", "bbox", "thumbnail"} <= roles
        # One representative image per bucket, not 6622 frames.
        thumbnails = [file for file in clip.files if file.role == "thumbnail"]
        assert len(thumbnails) == len(clip.camera_angles)
        assert len(clip.files) < 40
        for file in clip.files:
            assert not file.path.startswith("/")
            path = REFERENCE_ROOT / file.path
            assert path.is_file()
            assert file.size_bytes == path.stat().st_size
        # Digest one of the small ones for real rather than re-hashing 50 MB.
        smallest = min(clip.files, key=lambda file: file.size_bytes)
        assert smallest.sha256 == file_sha256(REFERENCE_ROOT / smallest.path)


@requires_library
def test_what_was_left_out_is_named(ingested: tuple[list, list[str]]) -> None:
    _, skipped = ingested
    assert skipped
    assert all(line.startswith("harmony4d") for line in skipped)
    joined = "\n".join(skipped)
    assert "206 of 208" in joined
    assert "ego views are not counted" in joined
    assert "points3D.txt" in joined


@requires_library
def test_the_same_inputs_give_the_same_bytes(ingested: tuple[list, list[str]]) -> None:
    clips, skipped = ingested
    again, again_skipped = harmony4d.ingest(REFERENCE_ROOT, ingested_at=INGESTED_AT)
    assert [clip.canonical_json() for clip in clips] == [clip.canonical_json() for clip in again]
    assert skipped == again_skipped


# --- offline: a synthetic take, for the paths the real data never takes -------------------------


def _write_synthetic_take(root: Path, *, with_calibration: bool) -> Path:
    """A three-frame take with two exo camera directories and no COLMAP calibration."""
    take = root / "Harmony4D/train/01_hugging/900_hugging"
    poses3d = take / "processed_data/poses3d"
    smpl = take / "processed_data/smpl"
    poses3d.mkdir(parents=True)
    smpl.mkdir(parents=True)
    for view in ("cam01", "cam02"):
        (take / f"exo/{view}/images").mkdir(parents=True)
    if with_calibration:
        (take / "colmap/workplace").mkdir(parents=True)
    for index, separation in enumerate((2.0, 0.3, 2.0)):
        poses = _synthetic_pair(separation, wrap=separation < 1.0)[0]
        frame = {
            name: np.concatenate([poses[person], np.ones((17, 1))], axis=1)
            for person, name in ((0, "aria01"), (1, "aria02"))
        }
        # The dataset stores one pickled dict per frame, which numpy holds as a 0-d object array.
        np.save(poses3d / f"{index + 1:05d}.npy", np.array(frame, dtype=object), allow_pickle=True)
        np.save(
            smpl / f"{index + 1:05d}.npy",
            np.array({"aria01": {}, "aria02": {}}, dtype=object),
            allow_pickle=True,
        )
    return take


def test_a_take_without_calibration_emits_no_angles_and_no_key_poses(tmp_path: Path) -> None:
    """The honest failure: a clip that cannot place its cameras claims no angle at all."""
    _write_synthetic_take(tmp_path, with_calibration=False)
    clips, skipped = harmony4d.ingest(tmp_path, ingested_at=INGESTED_AT)
    assert len(clips) == 1
    clip = clips[0]
    assert clip.clip_id == "h4d_train_900_hugging"
    assert clip.camera_angles == ()
    assert clip.key_poses == ()
    assert (clip.width, clip.height) == (None, None)
    assert "camera_azimuth_deg" not in clip.measured
    # The geometry still measures the embrace, which is what the pose track is for.
    assert clip.contact_tags == (ContactTag.arm, ContactTag.back, ContactTag.torso)
    assert clip.measured["contact_frame"] == 1.0
    assert clip.view_count == 2
    assert any("cameras.txt is missing" in line for line in skipped)


def test_a_take_whose_frames_do_not_line_up_is_skipped_with_a_reason(tmp_path: Path) -> None:
    take = _write_synthetic_take(tmp_path, with_calibration=False)
    (take / "processed_data/smpl/00003.npy").unlink()
    clips, skipped = harmony4d.ingest(tmp_path, ingested_at=INGESTED_AT)
    assert clips == []
    assert any("do not describe the same take" in line for line in skipped)
