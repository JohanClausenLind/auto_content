"""Harmony4D: two calibrated 22-camera hug takes, ingested for their geometry.

Harmony4D holds the best two-person contact geometry on disk and the worst pixels. Every exo frame
has a tripod or a camera body between the lens and the subjects, plus a sofa, whiteboards and lab
clutter (``_index/measured/viewing_notes.md``), so the usage class is ``pose_derivable`` and never
``pixels_usable``: the way to use this take is to put its poses on our own characters in our own
set and re-render, not to look at its frames.

Two numbers in this module are derived rather than asserted, because asserting them would be a
guess dressed as data.

**The camera angles.** ``cameras.txt`` and ``images.txt`` give each exo camera in the COLMAP frame;
the poses are in the Aria world frame. ``aria_from_colmap_transforms.pkl`` ships the alignment
between the two, and this module picks the entry that actually works by reprojecting the frame's
SMPL joints into all 22 views and comparing against the dataset's own ``poses2d``. At the contact
frame the ``aria01`` entry lands at a median of 0.008 px (001_hugging) and 0.18 px (002_hugging)
on a 3840x2160 image, while the ``aria02`` entry lands at 746 px and 569 px, so the choice is
measured rather than read off a name. When no entry reprojects within
``_REPROJ_TOLERANCE_PX`` the clip is emitted with no ``camera_angles`` and no ``key_poses``, and the
reason goes in the skipped list, because a bucket derived from an unverified pose is fiction.

**The contact.** The embrace is found by tracking the distance between the two torso centres over
every frame, so the key poses sit on measured moments rather than on fractions of the duration.
"""

from __future__ import annotations

import pickle
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from content_factory.schemas.base import file_sha256
from content_factory.schemas.reference import (
    Affection,
    ContactTag,
    FileRole,
    InteractionTag,
    KeyPoseLabel,
    Modality,
    Posture,
    ReferenceClip,
    ReferenceFile,
    ReferenceJoint,
    ReferenceKeyPose,
    ReferencePersonPose,
    ReferenceSource,
    UsageClass,
)
from content_factory.sequences.control_compile import OPENPOSE18_LIMBS

INGESTER_VERSION = "0.1.0"
SOURCE = ReferenceSource.harmony4d

_DATASET_DIR: Final = "Harmony4D"

# 20 fps is not stated in any metadata file the dataset ships. The evidence on disk is the encoded
# exo videos: exo/cam01/images/rgb.mp4 in both takes and exo/cam04/images/rgb.mp4 in 001_hugging
# all read 3840x2160, r_frame_rate 20/1, 301 frames, 15.05 s, i.e. exactly the frame sequence at
# 20 fps. (Two other mp4s sit in the takes and are not evidence of a take's length: 002_hugging's
# exo/cam01/images/output.mp4 is a 5-frame stub, and 001_hugging's four ego rgb.mp4 are the Aria
# streams, not the exo rig.)
_NATIVE_FPS: Final = 20.0

# COCO-17 in the order poses3d stores it, named in the OpenPose-18 vocabulary. "neck" is not in
# COCO-17 at all: it is derived below as the midpoint of the two shoulders, in 3D before
# projection, because the fisheye projection is not linear and a 2D midpoint would bend the neck.
_COCO17_TO_OPENPOSE: Final = (
    "nose",
    "l_eye",
    "r_eye",
    "l_ear",
    "r_ear",
    "l_shoulder",
    "r_shoulder",
    "l_elbow",
    "r_elbow",
    "l_wrist",
    "r_wrist",
    "l_hip",
    "r_hip",
    "l_knee",
    "r_knee",
    "l_ankle",
    "r_ankle",
)
_NECK: Final = "neck"
_L_SHOULDER: Final = 5
_R_SHOULDER: Final = 6
_L_WRIST: Final = 9
_R_WRIST: Final = 10
_L_ELBOW: Final = 7
_R_ELBOW: Final = 8
_L_HIP: Final = 11
_R_HIP: Final = 12

_WORLD_UP: Final = (0.0, 0.0, 1.0)
"""The pose world frame is z-up: on both takes a nose sits ~1.5 m above the ankles in +z."""

# Azimuth 0 is the direction the first subject's chest faces, so a camera at 0 sees that subject's
# face and the other subject's back. The pair hugs chest to chest, which makes a single "pair
# facing" meaningless, so the first subject by name (aria01) is the reference and the buckets are
# symmetric: what is "front" for one is "back" for the other.
_BUCKET_EDGES: Final = (
    ("front", 22.5),
    ("front_3q", 67.5),
    ("side", 112.5),
    ("back_3q", 157.5),
    ("back", 180.0),
)
_BUCKET_CENTRES: Final = {
    "front": 0.0,
    "front_3q": 45.0,
    "side": 90.0,
    "back_3q": 135.0,
    "back": 180.0,
}

# Thresholds, each with the measurement that sets it. On both takes the torso gap runs 1.7-2.1 m
# apart, drops to 0.23-0.25 m at the closest moment, and comes back out; 0.60 m is inside the
# shoulder of that curve and is roughly two torso half-depths, i.e. chest against chest.
_EMBRACE_TORSO_M: Final = 0.60
# At the closest frame the elbows sit 0.26-0.41 m from the other's torso centre, so a forearm lies
# across the other body; over every frame whose torso gap exceeds 1.5 m the nearest elbow is never
# closer than 1.32 m, more than twice this threshold, so the two regimes do not overlap.
_ARM_TORSO_M: Final = 0.60
# A wrist that is both within 0.45 m of the other's torso centre and past it, along the line
# joining the two, has reached around the far side of their spine: a hand on the back.
_BACK_TORSO_M: Final = 0.45
# 25 px on a 3840x2160 frame is 0.65 % of the width. Measured on the two takes, the working
# alignment lands at 0.008 px and 0.177 px and the rejected one at 746 px and 569 px, so nothing
# real sits near this line.
_REPROJ_TOLERANCE_PX: Final = 25.0

# A root that covers more than a metre over the take walked; the embrace itself never moves the
# roots more than a few centimetres.
_WALKING_TRAVEL_M: Final = 1.0

_MEASURED_DP: Final = 4
_JOINT_DP: Final = 6


@dataclass(frozen=True)
class _Camera:
    """One COLMAP camera model: intrinsics plus the image size they are expressed in."""

    model: str
    width: int
    height: int
    params: tuple[float, ...]


@dataclass(frozen=True)
class _View:
    """One registered exo view: rotation and centre in the COLMAP frame."""

    name: str
    camera_id: int
    rotation: np.ndarray
    centre: np.ndarray


@dataclass(frozen=True)
class _Calibration:
    """The exo rig in the pose world frame, with the reprojection error that proved it."""

    views: dict[str, _View]
    cameras: dict[int, _Camera]
    world_rotation: np.ndarray
    """Rotation only, COLMAP axes to world axes; the pickled matrix carries a uniform scale too."""
    world_transform: np.ndarray
    """The full 3x4 similarity, applied to a COLMAP point to get a world point in metres."""
    alignment_key: str
    reproj_px: float


@dataclass(frozen=True)
class _Sequence:
    """One take on disk, addressed relative to the reference root."""

    rel: str
    split: str
    name: str

    @property
    def clip_id(self) -> str:
        return f"h4d_{self.split}_{self.name}"


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """Every clip Harmony4D contributes, plus one string per thing skipped and why.

    Guarantees: one ``ReferenceClip`` per take found under ``Harmony4D/<split>/<activity>/<take>``,
    sorted by ``clip_id``; every path relative to ``root``; every digest computed from the bytes on
    disk; identical output bytes for identical inputs, since nothing here reads a clock or a random
    source and every collection is walked in sorted order.
    """
    skipped: list[str] = []
    dataset = root / _DATASET_DIR
    if not dataset.is_dir():
        skipped.append(f"harmony4d: {_DATASET_DIR}/ is not on this host, nothing ingested")
        return [], skipped

    # INVENTORY.json: only the 01_hugging subset was downloaded, 2 of 208 sequences, because the
    # full dataset is 352 GB and the rest of it is combat.
    skipped.append(
        "harmony4d: 206 of 208 dataset sequences are not on disk, only the 01_hugging subset was"
        " downloaded (the full dataset is 352 GB and the remainder is combat)"
    )

    clips: list[ReferenceClip] = []
    for sequence in _discover(dataset):
        clip = _ingest_sequence(root, sequence, ingested_at=ingested_at, skipped=skipped)
        if clip is not None:
            clips.append(clip)
    clips.sort(key=lambda clip: clip.clip_id)
    return clips, skipped


def _discover(dataset: Path) -> list[_Sequence]:
    """Every take directory under the dataset root, in sorted order."""
    found: list[_Sequence] = []
    for split in sorted(p for p in dataset.iterdir() if p.is_dir()):
        for activity in sorted(p for p in split.iterdir() if p.is_dir()):
            for take in sorted(p for p in activity.iterdir() if p.is_dir()):
                found.append(
                    _Sequence(
                        rel=f"{_DATASET_DIR}/{split.name}/{activity.name}/{take.name}",
                        split=split.name,
                        name=take.name,
                    )
                )
    return found


def _ingest_sequence(
    root: Path, sequence: _Sequence, *, ingested_at: str, skipped: list[str]
) -> ReferenceClip | None:
    """One take as a clip, or None with a reason in ``skipped`` when it cannot be read."""
    base = root / sequence.rel
    poses3d_dir = base / "processed_data/poses3d"
    smpl_dir = base / "processed_data/smpl"
    if not poses3d_dir.is_dir() or not smpl_dir.is_dir():
        skipped.append(
            f"harmony4d {sequence.rel}: no processed_data/poses3d or /smpl, there is no pose to"
            " index"
        )
        return None

    frame_files = sorted(poses3d_dir.glob("*.npy"))
    if not frame_files:
        skipped.append(f"harmony4d {sequence.rel}: processed_data/poses3d holds no .npy frames")
        return None
    stems = [path.stem for path in frame_files]
    if stems != [f"{i + 1:05d}" for i in range(len(stems))]:
        skipped.append(
            f"harmony4d {sequence.rel}: poses3d frame numbering is not 00001..{len(stems):05d},"
            " so frame_index cannot be tied to a filename"
        )
        return None
    smpl_count = len(sorted(smpl_dir.glob("*.npy")))
    if smpl_count != len(frame_files):
        skipped.append(
            f"harmony4d {sequence.rel}: {len(frame_files)} poses3d frames but {smpl_count} smpl"
            " frames, the two do not describe the same take"
        )
        return None

    exo_views = sorted(path.name for path in base.glob("exo/cam*") if path.is_dir())
    if len(exo_views) < 2:
        skipped.append(
            f"harmony4d {sequence.rel}: {len(exo_views)} exo camera directories, this ingester only"
            " describes the multi-view rig"
        )
        return None
    exo_view_count = len(exo_views)

    people = _load_poses(frame_files)
    if people is None:
        skipped.append(
            f"harmony4d {sequence.rel}: poses3d frames are not a dict of (17,4) arrays for exactly"
            " two people"
        )
        return None
    names, poses = people

    geometry = _measure(poses)
    calibration = _calibrate(base, geometry.contact_frame, names, skipped, sequence.rel)

    frame_count = len(frame_files)
    key_frames = _key_frames(geometry)
    camera_angles: tuple[str, ...] = ()
    key_poses: tuple[ReferenceKeyPose, ...] = ()
    representatives: dict[str, str] = {}
    azimuths: dict[str, float] = {}
    width: int | None = None
    height: int | None = None

    if calibration is not None:
        azimuths = _azimuths(calibration, poses, geometry.contact_frame)
        buckets = {view: _bucket(azimuth) for view, azimuth in sorted(azimuths.items())}
        camera_angles = tuple(name for name, _ in _BUCKET_EDGES if name in set(buckets.values()))
        representatives = _representatives(azimuths, buckets)
        front = representatives.get("front") or min(
            sorted(azimuths), key=lambda v: abs(azimuths[v])
        )
        front_camera = calibration.cameras[calibration.views[front].camera_id]
        width, height = front_camera.width, front_camera.height
        key_poses = _key_poses(calibration, front, poses, names, key_frames)

    contact_tags = _clip_contact_tags(poses, geometry.torso_centres, key_frames)
    if not contact_tags:
        skipped.append(
            f"harmony4d {sequence.rel}: the two people never come within"
            f" {_EMBRACE_TORSO_M:.2f} m of each other, so this take is not the embrace the tags"
            " would claim"
        )
        return None

    files = _files(root, sequence, calibration, representatives, key_frames, skipped)
    if not files:
        skipped.append(f"harmony4d {sequence.rel}: not one listable file could be digested")
        return None

    _report_unlisted(sequence, base, frame_count, skipped)

    duration_s = frame_count / _NATIVE_FPS
    embrace_s = (geometry.embrace_last - geometry.embrace_first + 1) / _NATIVE_FPS
    gaps = _azimuth_gaps(azimuths)
    measured: dict[str, float | list[float]] = {
        "closest_torso_m": _round(geometry.closest_torso_m),
        "closest_wrists_m": _round(geometry.closest_wrists_m),
        "root_gap_m": [_round(geometry.root_gap_min), _round(geometry.root_gap_max)],
        "travel_m": [_round(value) for value in geometry.travel_m],
        "contact_frame": float(geometry.contact_frame),
        "embrace_frames": [float(geometry.embrace_first), float(geometry.embrace_last)],
        "embrace_duration_s": _round(embrace_s),
    }
    if calibration is not None:
        measured["camera_azimuth_deg"] = [_round(value) for value in sorted(azimuths.values())]
        measured["camera_azimuth_gap_deg"] = [_round(min(gaps)), _round(max(gaps))]
        measured["camera_radius_m"] = [
            _round(value) for value in _camera_radii(calibration, poses, geometry.contact_frame)
        ]
        measured["calibration_reproj_px"] = _round(calibration.reproj_px)

    return ReferenceClip(
        clip_id=sequence.clip_id,
        source=SOURCE,
        source_ref=sequence.rel,
        modality=Modality.multiview_rgb,
        # pose_derivable, never pixels_usable: a tripod or a camera body stands between the lens
        # and the subjects in most of the 22 views, and the room is full of lab clutter.
        usage=UsageClass.pose_derivable,
        people_count=len(names),
        # class_maps.json marks "hug" affectionate in both maps that were verified by eye
        # (ut_interaction class 1, sbu_kinect class 06); this take is the same embrace.
        affection=Affection.affection,
        interaction_tags=(InteractionTag.hug,),
        contact_tags=contact_tags,
        postures=_postures(geometry),
        setting="university lab, capture rig in frame",
        camera_angles=camera_angles,
        view_count=exo_view_count,
        frame_count=frame_count,
        native_fps=_NATIVE_FPS,
        duration_s=duration_s,
        width=width,
        height=height,
        pose_format="coco17_3d",
        pose_root=f"{sequence.rel}/processed_data/poses3d",
        key_poses=key_poses,
        caption=_caption(geometry, azimuths, embrace_s, len(names), contact_tags),
        caption_source="derived",
        measured=measured,
        files=files,
        ingested_at=ingested_at,
        ingester_version=INGESTER_VERSION,
    )


# --- pose reading and measurement -------------------------------------------------------------


def _load_pickled_npy(path: Path) -> object:
    """One ``.npy`` that holds a python dict, which is how this dataset stores every frame.

    The dataset pickles its frames, so ``allow_pickle`` is required to read them at all. These are
    local files inside the reference library, never anything fetched. The warning filter is for
    numpy's own legacy ``numpy.core`` alias inside a 2023-era pickle: the suite promotes
    DeprecationWarnings raised inside ``content_factory`` to errors, and this one says nothing
    about the data.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return np.load(path, allow_pickle=True).item()


def _load_poses(frame_files: list[Path]) -> tuple[tuple[str, ...], np.ndarray] | None:
    """Person names and a (frames, people, 17, 3) array of world metres, or None if malformed."""
    names: tuple[str, ...] | None = None
    stacked: list[np.ndarray] = []
    for path in frame_files:
        frame = _load_pickled_npy(path)
        if not isinstance(frame, dict):
            return None
        keys = tuple(sorted(str(key) for key in frame))
        if names is None:
            names = keys
        if keys != names or len(keys) != 2:
            return None
        arrays = []
        for key in keys:
            array = np.asarray(frame[key], dtype=np.float64)
            if array.shape != (len(_COCO17_TO_OPENPOSE), 4):
                return None
            arrays.append(array[:, :3])
        stacked.append(np.stack(arrays))
    if names is None:
        return None
    return names, np.stack(stacked)


@dataclass(frozen=True)
class _Geometry:
    """What the pose track measures about the embrace, all in metres and frame indices."""

    torso_centres: np.ndarray
    torso_gap: np.ndarray
    closest_torso_m: float
    contact_frame: int
    embrace_first: int
    embrace_last: int
    closest_wrists_m: float
    root_gap_min: float
    root_gap_max: float
    travel_m: tuple[float, ...]


def _torso_centres(poses: np.ndarray) -> np.ndarray:
    """Midway between the shoulder midpoint and the hip midpoint, per person per frame."""
    shoulders = poses[:, :, [_L_SHOULDER, _R_SHOULDER], :].mean(axis=2)
    hips = poses[:, :, [_L_HIP, _R_HIP], :].mean(axis=2)
    return (shoulders + hips) / 2.0


def _measure(poses: np.ndarray) -> _Geometry:
    """Every number the clip reports about the embrace, read off the pose track.

    Guarantees: the contact frame is the frame whose two torso centres are closest, and the
    embrace window runs from the first to the last frame whose torso gap is under
    ``_EMBRACE_TORSO_M``. On both takes on disk every frame in that span is under the threshold, so
    the span is the embrace and not two embraces with a gap in the middle.
    """
    centres = _torso_centres(poses)
    gap = np.linalg.norm(centres[:, 0] - centres[:, 1], axis=1)
    contact_frame = int(np.argmin(gap))
    close = np.flatnonzero(gap < _EMBRACE_TORSO_M)
    embrace_first = int(close.min()) if close.size else contact_frame
    embrace_last = int(close.max()) if close.size else contact_frame

    wrists_a = poses[:, 0][:, [_L_WRIST, _R_WRIST], :]
    wrists_b = poses[:, 1][:, [_L_WRIST, _R_WRIST], :]
    pairwise = np.linalg.norm(wrists_a[:, :, None, :] - wrists_b[:, None, :, :], axis=3)

    roots = poses[:, :, [_L_HIP, _R_HIP], :].mean(axis=2)
    root_gap = np.linalg.norm(roots[:, 0] - roots[:, 1], axis=1)
    steps = np.linalg.norm(np.diff(roots[:, :, :2], axis=0), axis=2)

    return _Geometry(
        torso_centres=centres,
        torso_gap=gap,
        closest_torso_m=float(gap[contact_frame]),
        contact_frame=contact_frame,
        embrace_first=embrace_first,
        embrace_last=embrace_last,
        closest_wrists_m=float(pairwise.min()),
        root_gap_min=float(root_gap.min()),
        root_gap_max=float(root_gap.max()),
        travel_m=tuple(float(value) for value in steps.sum(axis=0)),
    )


def _key_frames(geometry: _Geometry) -> tuple[tuple[int, KeyPoseLabel], ...]:
    """The frames worth sampling, strictly increasing, each defined by a measurement.

    ``contact`` is the frame where the two torsos are closest. ``release`` is the last frame of the
    embrace window. ``start`` and ``end`` bracket the take, where the pair is apart.
    """
    last = geometry.torso_gap.shape[0] - 1
    wanted: list[tuple[int, KeyPoseLabel]] = [
        (0, "start"),
        (geometry.contact_frame, "contact"),
        (geometry.embrace_last, "release"),
        (last, "end"),
    ]
    seen: set[int] = set()
    ordered: list[tuple[int, KeyPoseLabel]] = []
    for frame, label in sorted(wanted):
        if frame not in seen:
            seen.add(frame)
            ordered.append((frame, label))
    return tuple(ordered)


def _contact_tags(poses: np.ndarray, centres: np.ndarray, frame: int) -> tuple[ContactTag, ...]:
    """Contact tags the geometry of one frame supports, sorted as the contract requires."""
    tags: set[ContactTag] = set()
    gap = float(np.linalg.norm(centres[frame, 0] - centres[frame, 1]))
    if gap < _EMBRACE_TORSO_M:
        tags.add(ContactTag.torso)
    for me, other in ((0, 1), (1, 0)):
        mine = poses[frame, me]
        their_centre = centres[frame, other]
        toward = their_centre - centres[frame, me]
        toward[2] = 0.0
        norm = float(np.linalg.norm(toward))
        if norm < 1e-9:
            continue
        toward = toward / norm
        for elbow in (_L_ELBOW, _R_ELBOW):
            if float(np.linalg.norm(mine[elbow] - their_centre)) < _ARM_TORSO_M:
                tags.add(ContactTag.arm)
        for wrist in (_L_WRIST, _R_WRIST):
            offset = mine[wrist] - their_centre
            reach = float(np.linalg.norm(offset))
            if reach < _BACK_TORSO_M and float(offset @ toward) > 0.0:
                tags.add(ContactTag.back)
    return tuple(sorted(tags, key=lambda tag: tag.value))


def _postures(geometry: _Geometry) -> tuple[Posture, ...]:
    """Postures the root motion supports, sorted as the contract requires.

    Standing is always there: the embrace itself is stationary. Walking is added only when a root
    actually covers ground, which on both takes on disk it does, 3.3 m and 4.9 m of it.
    """
    postures = [Posture.standing]
    if max(geometry.travel_m) > _WALKING_TRAVEL_M:
        postures.append(Posture.walking)
    return tuple(sorted(postures, key=lambda posture: posture.value))


def _clip_contact_tags(
    poses: np.ndarray, centres: np.ndarray, key_frames: tuple[tuple[int, KeyPoseLabel], ...]
) -> tuple[ContactTag, ...]:
    """The union of what the key frames' geometry supports, sorted and unique.

    Derived, never asserted: if a take never closed to an embrace, nothing here would claim one.
    """
    union: set[ContactTag] = set()
    for frame, _label in key_frames:
        union.update(_contact_tags(poses, centres, frame))
    return tuple(sorted(union, key=lambda tag: tag.value))


# --- calibration ------------------------------------------------------------------------------


def _quaternion_to_rotation(quat: np.ndarray) -> np.ndarray:
    """The rotation matrix for a COLMAP (qw, qx, qy, qz), normalised first."""
    w, x, y, z = quat / float(np.linalg.norm(quat))
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _parse_cameras(path: Path) -> dict[int, _Camera]:
    """Every camera model in a COLMAP ``cameras.txt``, keyed by camera id."""
    cameras: dict[int, _Camera] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.split()
            if len(fields) < 5:
                continue
            try:
                camera_id = int(fields[0])
                width, height = int(fields[2]), int(fields[3])
                params = tuple(float(value) for value in fields[4:])
            except ValueError:
                continue
            cameras[camera_id] = _Camera(fields[1], width, height, params)
    return cameras


def _parse_images(path: Path) -> dict[str, _View]:
    """The exo views in a COLMAP ``images.txt``, one entry per ``cam*`` directory.

    Each exo camera is registered on 4 frames of a static rig. Both takes ship the same
    images.txt, and within a camera those 4 centres agree to 3.6 mm at worst (1.3 mm mean over the
    22 cameras) against a rig radius of 1.4-2.2 m, so the first by sorted image name is taken and
    the rest ignored; the ego (aria*) entries are dropped because a head-mounted camera has no
    fixed angle.
    """
    best: dict[str, tuple[str, _View]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) != 10 or not fields[9].endswith(".jpg") or "/" not in fields[9]:
                continue
            view = fields[9].split("/", 1)[0]
            if not view.startswith("cam"):
                continue
            try:
                rotation = _quaternion_to_rotation(
                    np.array([float(value) for value in fields[1:5]], dtype=np.float64)
                )
                translation = np.array([float(value) for value in fields[5:8]], dtype=np.float64)
                camera_id = int(fields[8])
            except ValueError:
                continue
            entry = _View(view, camera_id, rotation, -rotation.T @ translation)
            current = best.get(view)
            if current is None or fields[9] < current[0]:
                best[view] = (fields[9], entry)
    return {view: entry for view, (_, entry) in sorted(best.items())}


def _load_transforms(path: Path) -> dict[str, np.ndarray]:
    """The COLMAP-to-world similarity transforms the dataset ships, keyed by ego camera.

    The dataset ships them as a pickle, so a pickle is what this reads: a local file inside the
    reference library, never anything from the network. Which entry is the one the poses live in is
    not documented, so the caller decides by reprojection rather than by name.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with path.open("rb") as handle:
            raw = pickle.load(handle)  # noqa: S301
    if not isinstance(raw, dict):
        return {}
    transforms: dict[str, np.ndarray] = {}
    for key, value in raw.items():
        matrix = np.asarray(value, dtype=np.float64)
        if matrix.shape in {(4, 4), (3, 4)}:
            transforms[str(key)] = matrix[:3, :4]
    return transforms


def _project(points_camera: np.ndarray, camera: _Camera) -> np.ndarray:
    """Normalised image coordinates for camera-space points, OpenCV fisheye model.

    Guarantees: one (x, y) per input point, in fractions of image width and height, and never
    clamped, so a joint that leaves the frame keeps the coordinate the capture implies. Verified
    against the dataset's own ``poses2d`` at a median of 0.01 px.
    """
    fx, fy, cx, cy = camera.params[:4]
    k1, k2, k3, k4 = camera.params[4:8]
    depth = points_camera[:, 2]
    safe = np.where(np.abs(depth) < 1e-9, 1e-9, depth)
    x = points_camera[:, 0] / safe
    y = points_camera[:, 1] / safe
    radius = np.sqrt(x * x + y * y)
    theta = np.arctan(radius)
    distorted = theta * (1.0 + k1 * theta**2 + k2 * theta**4 + k3 * theta**6 + k4 * theta**8)
    scale = np.where(radius > 1e-9, distorted / np.maximum(radius, 1e-12), 1.0)
    return np.stack(
        [(fx * scale * x + cx) / camera.width, (fy * scale * y + cy) / camera.height], axis=1
    )


def _to_camera(
    view: _View,
    calibration_rotation: np.ndarray,
    centre_world: np.ndarray,
    points_world: np.ndarray,
) -> np.ndarray:
    """World-metre points in a view's camera frame, still in metres.

    The shipped alignment carries a uniform scale; taking the rotation out of it and subtracting
    the camera centre keeps the camera-space depth metric, which is what ``ReferenceJoint.z`` wants.
    """
    rotation = view.rotation @ calibration_rotation.T
    return (points_world - centre_world) @ rotation.T


def _camera_centre(view: _View, transform: np.ndarray) -> np.ndarray:
    """A view's centre in the pose world frame, in metres."""
    return transform[:3, :3] @ view.centre + transform[:3, 3]


def _calibrate(
    base: Path, frame: int, names: tuple[str, ...], skipped: list[str], rel: str
) -> _Calibration | None:
    """The exo rig in the pose world frame, or None with a reason in ``skipped``.

    Guarantees: the returned alignment reprojects the frame's own SMPL joints into every exo view
    within ``_REPROJ_TOLERANCE_PX`` of the dataset's own 2D annotation. Nothing is returned on a
    guess: an unreadable or unverifiable calibration yields None, and the clip then carries no
    camera angles.
    """
    workplace = base / "colmap/workplace"
    cameras_txt = workplace / "cameras.txt"
    images_txt = workplace / "images.txt"
    transforms_pkl = workplace / "aria_from_colmap_transforms.pkl"
    for path in (cameras_txt, images_txt, transforms_pkl):
        if not path.is_file():
            skipped.append(
                f"harmony4d {rel}: colmap/workplace/{path.name} is missing, so no camera_angles and"
                " no key_poses are emitted rather than guessed"
            )
            return None

    cameras = _parse_cameras(cameras_txt)
    views = _parse_images(images_txt)
    if not views:
        skipped.append(
            f"harmony4d {rel}: images.txt registers no cam* view, so no camera_angles are emitted"
        )
        return None
    used = [cameras[view.camera_id] for view in views.values() if view.camera_id in cameras]
    models = {camera.model for camera in used}
    if models != {"OPENCV_FISHEYE"} or any(len(camera.params) < 8 for camera in used):
        skipped.append(
            f"harmony4d {rel}: exo cameras use models {sorted(models)}, only OPENCV_FISHEYE with"
            " its 8 parameters is implemented here, so no camera_angles are emitted"
        )
        return None

    smpl = _load_pickled_npy(base / f"processed_data/smpl/{frame + 1:05d}.npy")
    if not isinstance(smpl, dict):
        skipped.append(f"harmony4d {rel}: smpl frame {frame + 1:05d} is not a dict, cannot verify")
        return None
    joints = {}
    for name in names:
        person = smpl.get(name)
        if not isinstance(person, dict) or "joints" not in person:
            skipped.append(f"harmony4d {rel}: smpl frame {frame + 1:05d} has no joints for {name}")
            return None
        joints[name] = np.asarray(person["joints"], dtype=np.float64)

    best: _Calibration | None = None
    for key, transform in sorted(_load_transforms(transforms_pkl).items()):
        linear = transform[:3, :3]
        scale = float(np.mean(np.linalg.norm(linear, axis=1)))
        if scale <= 0.0:
            continue
        rotation = linear / scale
        error = _reprojection_error(base, frame, views, cameras, rotation, transform, joints)
        if error is None:
            continue
        candidate = _Calibration(views, cameras, rotation, transform, key, error)
        if best is None or candidate.reproj_px < best.reproj_px:
            best = candidate
    if best is None or best.reproj_px > _REPROJ_TOLERANCE_PX:
        worst = "none readable" if best is None else f"best {best.reproj_px:.1f} px"
        skipped.append(
            f"harmony4d {rel}: no colmap-to-world alignment reprojects the smpl joints within"
            f" {_REPROJ_TOLERANCE_PX:.0f} px ({worst}), so no camera_angles and no key_poses are"
            " emitted rather than guessed"
        )
        return None
    return best


def _reprojection_error(
    base: Path,
    frame: int,
    views: dict[str, _View],
    cameras: dict[int, _Camera],
    rotation: np.ndarray,
    transform: np.ndarray,
    joints: dict[str, np.ndarray],
) -> float | None:
    """Median pixel error between projected SMPL joints and the dataset's own ``poses2d``.

    None when no view ships a 2D annotation to compare against, which is the honest answer: an
    unverifiable alignment is not a verified one.
    """
    errors: list[float] = []
    for view_name, view in sorted(views.items()):
        camera = cameras.get(view.camera_id)
        path = base / f"processed_data/poses2d/{view_name}/{frame + 1:05d}.npy"
        if camera is None or not path.is_file():
            continue
        annotated = _load_pickled_npy(path)
        if not isinstance(annotated, dict):
            continue
        centre = _camera_centre(view, transform)
        for person in sorted(joints):
            truth = annotated.get(person)
            if truth is None:
                continue
            expected = np.asarray(truth, dtype=np.float64)
            world = joints[person]
            if expected.shape != (world.shape[0], 2):
                continue
            uv = _project(_to_camera(view, rotation, centre, world), camera)
            pixels = uv * np.array([camera.width, camera.height], dtype=np.float64)
            errors.append(float(np.median(np.linalg.norm(pixels - expected, axis=1))))
    if not errors:
        return None
    return float(np.median(errors))


# --- angles -----------------------------------------------------------------------------------


def _facing(pose: np.ndarray) -> np.ndarray:
    """The horizontal unit vector this person's chest points along.

    The shoulder line crossed with world up. The pose world frame is z-up and right-handed, so
    ``cross(l_shoulder - r_shoulder, up)`` comes out of the chest. Measured on both takes: at the
    closest-torso frame each person's facing has a positive dot product with the direction to the
    other (0.93 and 0.87 on 001_hugging, 0.86 and 0.79 on 002_hugging), which is what an embrace
    means and would be negative if the handedness or the shoulder order were the other way round.
    """
    shoulder = pose[_L_SHOULDER] - pose[_R_SHOULDER]
    facing = np.cross(shoulder, np.array(_WORLD_UP, dtype=np.float64))
    facing[2] = 0.0
    norm = float(np.linalg.norm(facing))
    if norm < 1e-9:
        return np.array([1.0, 0.0, 0.0])
    return facing / norm


def _azimuths(calibration: _Calibration, poses: np.ndarray, frame: int) -> dict[str, float]:
    """Each exo camera's azimuth in degrees around the pair, zero at the first subject's facing.

    Guarantees: signed degrees in (-180, 180], measured in the horizontal plane about the midpoint
    of the two torso centres at ``frame``, one entry per registered exo view.
    """
    centres = _torso_centres(poses)
    middle = (centres[frame, 0] + centres[frame, 1]) / 2.0
    facing = _facing(poses[frame, 0])
    left = np.cross(np.array(_WORLD_UP, dtype=np.float64), facing)
    result: dict[str, float] = {}
    for view_name, view in sorted(calibration.views.items()):
        offset = _camera_centre(view, calibration.world_transform) - middle
        offset[2] = 0.0
        norm = float(np.linalg.norm(offset))
        if norm < 1e-9:
            continue
        direction = offset / norm
        result[view_name] = float(
            np.degrees(np.arctan2(float(direction @ left), float(direction @ facing)))
        )
    return result


def _bucket(azimuth_deg: float) -> str:
    """The angle bucket an azimuth falls in; the buckets are symmetric about the facing axis."""
    magnitude = abs(azimuth_deg)
    for name, edge in _BUCKET_EDGES:
        if magnitude <= edge:
            return name
    return "back"


def _representatives(azimuths: dict[str, float], buckets: dict[str, str]) -> dict[str, str]:
    """One exo view per occupied bucket: the one nearest that bucket's centre angle."""
    chosen: dict[str, str] = {}
    for bucket, centre in _BUCKET_CENTRES.items():
        candidates = sorted(view for view, name in buckets.items() if name == bucket)
        if candidates:
            chosen[bucket] = min(candidates, key=lambda view: abs(abs(azimuths[view]) - centre))
    return chosen


def _azimuth_gaps(azimuths: dict[str, float]) -> list[float]:
    """The gaps in degrees between neighbouring cameras around the full circle."""
    if len(azimuths) < 2:
        return [360.0]
    ordered = sorted(azimuths.values())
    return [(b - a) % 360.0 for a, b in zip(ordered, ordered[1:] + ordered[:1], strict=True)]


def _camera_radii(calibration: _Calibration, poses: np.ndarray, frame: int) -> list[float]:
    """Smallest and largest horizontal distance from the pair's midpoint to a camera, in metres."""
    centres = _torso_centres(poses)
    middle = (centres[frame, 0] + centres[frame, 1]) / 2.0
    radii = []
    for view in calibration.views.values():
        offset = _camera_centre(view, calibration.world_transform) - middle
        radii.append(float(np.linalg.norm(offset[:2])))
    return [min(radii), max(radii)]


# --- key poses --------------------------------------------------------------------------------


def _openpose18_world(pose: np.ndarray) -> tuple[tuple[str, ...], np.ndarray]:
    """COCO-17 world joints as OpenPose-18, with the derived neck, in the source's own metres.

    Guarantees: 18 named joints in a fixed order. Every COCO-17 joint maps one to one; ``neck`` is
    derived, as the midpoint of the two shoulders, because COCO-17 does not have one.
    """
    neck = (pose[_L_SHOULDER] + pose[_R_SHOULDER]) / 2.0
    names = (*_COCO17_TO_OPENPOSE, _NECK)
    return names, np.vstack([pose, neck[None, :]])


def _key_poses(
    calibration: _Calibration,
    front: str,
    poses: np.ndarray,
    names: tuple[str, ...],
    key_frames: tuple[tuple[int, KeyPoseLabel], ...],
) -> tuple[ReferenceKeyPose, ...]:
    """One key pose per measured moment, projected into the front-bucket camera.

    Guarantees: ``x`` and ``y`` are that camera's normalised image coordinates and are never
    clamped, ``z`` is metric depth along the camera axis, ``in_frame`` says whether the joint landed
    inside the frame, and the joint names are OpenPose-18 so a reference pose can drive the same
    control passes a rendered pose does.
    """
    view = calibration.views[front]
    camera = calibration.cameras[view.camera_id]
    centre = _camera_centre(view, calibration.world_transform)
    centres = _torso_centres(poses)
    out: list[ReferenceKeyPose] = []
    for frame, label in key_frames:
        people: list[ReferencePersonPose] = []
        for index, _name in enumerate(names):
            joint_names, world = _openpose18_world(poses[frame, index])
            in_camera = _to_camera(view, calibration.world_rotation, centre, world)
            uv = _project(in_camera, camera)
            joints = {}
            for slot, joint_name in enumerate(joint_names):
                x, y = float(uv[slot, 0]), float(uv[slot, 1])
                depth = float(in_camera[slot, 2])
                joints[joint_name] = ReferenceJoint(
                    x=round(x, _JOINT_DP),
                    y=round(y, _JOINT_DP),
                    z=round(depth, _JOINT_DP),
                    # visible stays at the contract default: nothing here measures occlusion.
                    # Reading it off the SMPL meshes needs the per-joint tolerance table the
                    # Blender keypoint pass owns, and half-measuring it would put an unearned
                    # flag in the index.
                    in_frame=bool(depth > 0.0 and 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0),
                )
            bones = tuple((a, b) for a, b in OPENPOSE18_LIMBS if a in joints and b in joints)
            people.append(ReferencePersonPose(person_index=index, joints=joints, bones=bones))
        out.append(
            ReferenceKeyPose(
                frame_index=frame,
                label=label,
                camera=front,
                people=tuple(people),
                contact=_contact_tags(poses, centres, frame),
            )
        )
    return tuple(out)


# --- files ------------------------------------------------------------------------------------


def _reference_file(
    root: Path, rel: str, role: FileRole, camera: str | None, skipped: list[str]
) -> ReferenceFile | None:
    """One digested file, or None with a reason when it is not on disk."""
    path = root / rel
    if not path.is_file():
        skipped.append(f"harmony4d {rel}: expected a {role} file here and found none")
        return None
    return ReferenceFile(
        role=role,
        path=rel,
        sha256=file_sha256(path),
        size_bytes=path.stat().st_size,
        camera=camera,
    )


def _files(
    root: Path,
    sequence: _Sequence,
    calibration: _Calibration | None,
    representatives: dict[str, str],
    key_frames: tuple[tuple[int, KeyPoseLabel], ...],
    skipped: list[str],
) -> tuple[ReferenceFile, ...]:
    """What a consumer of this clip needs, digested for real.

    Not every frame: 13 244 exo jpgs and 1204 pose files across the two takes would be a digest
    marathon and nobody reads them through this index. The list is the calibration, the pose and
    SMPL frames the key poses name, the 2D pose and box for one camera per angle bucket, and one
    representative image per bucket so the operator can see what each angle looks like.
    """
    wanted: list[tuple[FileRole, str, str | None]] = []
    if calibration is not None:
        for name in ("cameras.txt", "images.txt", "aria_from_colmap_transforms.pkl"):
            wanted.append(("calibration", f"{sequence.rel}/colmap/workplace/{name}", None))
    for frame, _label in key_frames:
        wanted.append(
            ("poses3d", f"{sequence.rel}/processed_data/poses3d/{frame + 1:05d}.npy", None)
        )
        wanted.append(("smpl", f"{sequence.rel}/processed_data/smpl/{frame + 1:05d}.npy", None))
    contact = next((frame for frame, label in key_frames if label == "contact"), 0)
    for bucket, _centre in _BUCKET_CENTRES.items():
        view = representatives.get(bucket)
        if view is None:
            continue
        stem = f"{contact + 1:05d}"
        wanted.append(("poses2d", f"{sequence.rel}/processed_data/poses2d/{view}/{stem}.npy", view))
        wanted.append(("bbox", f"{sequence.rel}/processed_data/bbox/{view}/{stem}.npy", view))
        wanted.append(("thumbnail", f"{sequence.rel}/exo/{view}/images/{stem}.jpg", view))
    preview = f"{sequence.rel}/exo/cam01/images/rgb.mp4"
    if (root / preview).is_file():
        wanted.append(("video", preview, "cam01"))

    files: list[ReferenceFile] = []
    for role, rel, camera in sorted(wanted, key=lambda item: (item[0], item[1])):
        entry = _reference_file(root, rel, role, camera, skipped)
        if entry is not None:
            files.append(entry)
    return tuple(files)


def _report_unlisted(sequence: _Sequence, base: Path, frame_count: int, skipped: list[str]) -> None:
    """Say what is on disk and deliberately not in ``files``, so no absence is silent."""
    jpgs = sum(1 for _ in base.glob("exo/*/images/*.jpg"))
    skipped.append(
        f"harmony4d {sequence.rel}: {jpgs} exo jpgs on disk, only one per angle bucket at the"
        " contact frame is listed, because the pixels are unusable and digesting 3840x2160 frames"
        " buys nothing"
    )
    skipped.append(
        f"harmony4d {sequence.rel}: the 2 aria ego views are not counted in view_count and get no"
        " angle bucket, because a head-mounted fisheye moves with its wearer every frame"
    )
    skipped.append(
        f"harmony4d {sequence.rel}: {frame_count} poses3d and smpl frames on disk, only the key"
        " pose frames are listed; pose_root addresses the rest"
    )
    skipped.append(
        f"harmony4d {sequence.rel}: colmap points3D.txt and temp.db are not listed, a consumer"
        " needs cameras.txt, images.txt and the alignment only"
    )


# --- caption ----------------------------------------------------------------------------------


def _caption(
    geometry: _Geometry,
    azimuths: dict[str, float],
    embrace_s: float,
    people: int,
    contact_tags: tuple[ContactTag, ...],
) -> str:
    """A caption made of measurements, plus the things the closed vocabulary cannot say.

    The phrase describing the embrace comes from the contact tags the geometry supported, so the
    caption cannot claim arms around a back that the poses do not show. The rest is what the
    vocabulary has no field for: the Aria headsets, the ego views, and the tripods.
    """
    held = []
    if ContactTag.torso in contact_tags:
        held.append("chest to chest")
    if ContactTag.arm in contact_tags:
        held.append("arms wrapped round each other")
    if ContactTag.back in contact_tags:
        held.append("hands on each other's backs")
    embrace = ", ".join(held) if held else "in contact"
    ring = ""
    if azimuths:
        ring = (
            f" {len(azimuths)} static cameras ring the pair through 360 degrees, largest azimuth"
            f" gap {max(_azimuth_gaps(azimuths)):.0f} degrees."
        )
    return (
        f"{people} men walk in, embrace {embrace} for {embrace_s:.1f} s, then separate. Torso"
        f" centres close to {geometry.closest_torso_m:.2f} m at frame"
        f" {geometry.contact_frame}.{ring} Both subjects wear Project Aria glasses, two"
        " head-mounted ego views come with the take, and tripods and camera bodies stand between"
        " the lens and the subjects in most views, so the geometry is usable and the pixels are"
        " not."
    )


def _round(value: float) -> float:
    """Rounded to four decimals so the same inputs serialise to the same bytes."""
    return round(float(value), _MEASURED_DP)
