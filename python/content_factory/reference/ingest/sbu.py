"""SBU Kinect ingest: 282 two-person gestures, 15 Kinect joints each, named as OpenPose-18.

SBU is the only source on disk that gives a labelled skeleton for *both* people in the same frame,
which is why it earns a real pose pass here rather than an index row. Everything else about it is
small: 21 pairs of students in one corridor, eight staged actions, sequences of 10 to 46 frames.

Four things in this module are measured rather than assumed, and each one changes the output.

**The normalisation.** ``skeleton_pos.txt`` holds 91 comma-separated values per line: a frame index
plus two people times fifteen joints times (x, y, z). The dataset's own README documents the
conversion as ``x = 1280 - 2560 * x_file``, ``y = 960 - 1920 * y_file`` and
``z = z_file * 10000 / 7.8125``; the copy of that formula on disk is
``external/ISTA-Net/feeders/feeder_sbu.py``, which cites the README URL. Read literally, the first
two put most joints far outside a 640x480 frame and invert the body vertically, so the formula only
makes sense as a coordinate centred on the image with y pointing up, over a 2560x1920 space: that
reading gives ``x_fraction = 0.5 + x_px / 2560 = 1 - x_file`` and
``y_fraction = 0.5 - y_px / 1920 = y_file``, i.e. unit gain and a horizontal flip. The unit gain is
what the disk says too: fitting the head joint of 584 separated frames against the head of the
matching blob in ``depth_*.png`` gives ``u = -633 * x_file + 633`` and ``v = 475 * y_file - 26``,
against 640 and 480 for a frame-sized gain. Applied this way, 13.75 % of all 204660 joint
observations fall outside 0..1, which is the number the contract's ``ReferenceJoint`` docstring
carries, and they are left outside rather than clamped.

**The flip belongs to the stored images, not to the geometry.** The RGB and depth PNGs are mirrored
with respect to the skeleton: over all 282 sequences, projecting with ``1 - x_file`` lands a median
14.6 px from the depth foreground centroid while projecting with ``x_file`` lands 100.6 px away.
The skeleton is the physical view, and only the physical view is self-consistent: SBU subjects shake
hands with the joint the README names ``r_hand`` in 16 of the 18 handshake runs, and that joint is
on the anatomical right only when x is read unmirrored. Read that way, the shoulder that is nearer
the lens is the one the naming predicts from which way the subject faces in 85.2 % of 5742 profile
frames; read mirrored, in 14.8 %, i.e. left-handed handshakes throughout. So this module emits
``x = x_file``, keeping
(x, y, z) a right-handed camera frame in which the left and right joint names are the subject's own,
and a consumer who wants to overlay a key pose on the frame it names must flip it: ``u = (1 - x) *
width``. Chirality is worth more than pixel alignment here, because the usage class is
``pose_derivable``: these 640x480 corridor frames are not a look reference, the poses are the point.

**Depth is millimetres, not metres.** ``z_file * 10000 / 7.8125`` is 1280 * z_file, i.e. the Kinect
depth in mm. Taking it as metres shrinks the subjects: the median head-to-lowest-foot extent comes
out 1.15 m, a child. With the factor applied it is 1.47 m, which is an adult head-centre to a foot
joint that is often extrapolated below the frame. Every metric number here also needs a transverse
scale, and no calibration ships with the dataset, so the standard Kinect v1 640x480 intrinsic
``_FOCAL_PX`` is used and named: anything in ``measured`` inherits it, while ``z`` does not.

**Contact is measured per class, not read off the label.** ``class_maps.json`` was verified by eye
and fixes the label and the affection of each action id, and this module refuses to emit a class
whose label or affection it disagrees with. What the label does not say is whether the two bodies
touch, so that comes from the geometry: at the closest frame a hand reaches the other's chest to
within a median 0.19 m when hugging and 0.24 m when pushing, but stops 0.41 m away when punching,
and a kicking foot stops 0.66 m away. Punching and kicking therefore carry ``ContactTag.none``, and
say so in the caption. The vocabulary also has no foot or leg contact, so the kick could not have
been described accurately even if it had landed.

The Kinect 15-joint set has no nose, eyes or ears. ``head`` fills the ``nose`` slot because it is
the only head point there is, and ``l_eye``, ``r_eye``, ``l_ear`` and ``r_ear`` are simply absent
from every emitted pose: an invented eye would be indistinguishable from a measured one downstream.
``torso`` is the other side of the same coin, a real Kinect joint with no OpenPose-18 slot to go in,
so it drives the hug contact measurement and is then dropped.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Final, NamedTuple

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
SOURCE = ReferenceSource.sbu_kinect

_CLEAN_DIR: Final = "SBU-Kinect/clean"
_INDEX_FILES: Final = ("class_maps.json", "sbu.json")
_CLASS_MAP_KEY: Final = "sbu_kinect"

_PEOPLE: Final = 2
_JOINTS: Final = 15
_COLUMNS: Final = 1 + _PEOPLE * _JOINTS * 3

# 15 fps is the rate the SBU authors state for the capture and nothing on disk timestamps a frame,
# so it is the dataset's number rather than a measured one. It is at least the only rate that makes
# the frame counts behave: the frame indices in skeleton_pos.txt step by exactly 1 in all 6540
# consecutive pairs, and the mean 24-frame sequence is a 1.6 s gesture at 15 fps and a 0.8 s twitch
# at 30.
_NATIVE_FPS: Final = 15.0

# Documented: z_file * 10000 / 7.8125 is the Kinect depth in mm, so this is the same factor in m.
_Z_METRES: Final = 10000.0 / 7.8125 / 1000.0

# The Kinect v1 640x480 focal length in pixels. Not on disk: no calibration ships with SBU. Only
# the two transverse axes of a measured distance depend on it; z is metric on its own.
_FOCAL_PX: Final = 525.0

# Fallback frame size, used only when a sequence's first RGB frame is missing. Every PNG in the
# 282 sequences that were checked is 640x480; the size is still read per sequence rather than
# assumed, because width and height are emitted on the clip.
_FRAME_SIZE: Final = (640, 480)

_MEASURED_DP: Final = 4
_JOINT_DP: Final = 6

# The dataset's joint order, and the OpenPose-18 slot each Kinect joint fills. This tuple is the
# mapping table: the left column is what column triple 0..14 of a person means, the right column is
# the name the joint is emitted under, and None means the joint has no OpenPose-18 slot at all.
#   head       -> nose      the Kinect head point is the head centre, not the nose. It is the only
#                           head joint SBU has, and the nose slot is the only slot it can fill.
#   neck       -> neck      one to one.
#   torso      -> None      OpenPose-18 has no mid-spine joint. Used for measurement, then dropped.
#   l/r hand   -> l/r wrist the Kinect hand point sits a hand's length past the wrist.
#   l/r foot   -> l/r ankle the Kinect foot point sits a foot's length past the ankle.
# Left and right are the subject's own, which holds in the unmirrored frame this module emits.
_KINECT15_TO_OPENPOSE18: Final = (
    ("head", "nose"),
    ("neck", "neck"),
    ("torso", None),
    ("l_shoulder", "l_shoulder"),
    ("l_elbow", "l_elbow"),
    ("l_hand", "l_wrist"),
    ("r_shoulder", "r_shoulder"),
    ("r_elbow", "r_elbow"),
    ("r_hand", "r_wrist"),
    ("l_hip", "l_hip"),
    ("l_knee", "l_knee"),
    ("l_foot", "l_ankle"),
    ("r_hip", "r_hip"),
    ("r_knee", "r_knee"),
    ("r_foot", "r_ankle"),
)

OPENPOSE18_WITHOUT_SOURCE: Final = ("l_ear", "l_eye", "r_ear", "r_eye")
"""The OpenPose-18 joints no Kinect 15-joint skeleton can fill. Named, never fabricated."""

KINECT15_WITHOUT_SLOT: Final = ("torso",)
"""Kinect joints with no OpenPose-18 slot. Measured from, then dropped from the emitted pose."""

_ROW_TORSO: Final = 2
_ROWS_HAND: Final = (5, 8)
_ROWS_FOOT: Final = (11, 14)
_ROW_NECK: Final = 1


class _Joint(NamedTuple):
    """One joint after normalisation: x and y are image fractions, z is metres."""

    x: float
    y: float
    z: float


class _Frame(NamedTuple):
    """One skeleton row: the Kinect frame number it names, and both people's joints."""

    number: int
    people: tuple[tuple[_Joint, ...], ...]


_Point = tuple[float, float, float]
"""One joint in camera-space metres, for distances between the two bodies."""

_PeopleMetres = tuple[tuple[_Point, ...], ...]
"""Both people's joints in metres for one frame, in the dataset's joint order."""


class _Moment(NamedTuple):
    """Which measured distance picks a class's key frame, and what to call that moment."""

    rule: str
    label: KeyPoseLabel


class _ClassSpec(NamedTuple):
    """How one verified SBU action id lands in the contract's closed vocabularies."""

    label: str
    tag: InteractionTag
    contact: tuple[ContactTag, ...]
    affection: Affection
    posture: Posture
    moment: _Moment
    note: str


# Keyed by the action id in the directory name. Labels and affection must match class_maps.json,
# which was verified by looking at a frame per class; the tags, contact and key-pose rule are this
# module's, and the geometry behind each contact choice is in the module docstring.
_SPECS: dict[str, _ClassSpec] = {
    "01": _ClassSpec(
        label="approaching",
        tag=InteractionTag.approach,
        contact=(ContactTag.none,),
        affection=Affection.staging,
        posture=Posture.walking,
        moment=_Moment("torsos", "settle"),
        note="They close to a conversational distance and stop, so nothing touches.",
    ),
    "02": _ClassSpec(
        label="departing",
        tag=InteractionTag.depart,
        contact=(ContactTag.none,),
        affection=Affection.staging,
        posture=Posture.walking,
        moment=_Moment("torsos", "release"),
        note="They start apart and separate further, so nothing touches.",
    ),
    "03": _ClassSpec(
        label="kicking",
        tag=InteractionTag.kick,
        contact=(ContactTag.none,),
        affection=Affection.aggression,
        posture=Posture.standing,
        moment=_Moment("foot_chest", "peak"),
        note=(
            "The kick is a staged near miss: the foot stops well short of the other body, and the"
            " contact vocabulary has no foot or leg tag to describe it with if it had landed."
        ),
    ),
    "04": _ClassSpec(
        label="pushing",
        tag=InteractionTag.push,
        contact=(ContactTag.hands, ContactTag.torso),
        affection=Affection.aggression,
        posture=Posture.standing,
        moment=_Moment("hand_chest", "contact"),
        note="Both hands land on the other's chest.",
    ),
    "05": _ClassSpec(
        label="shaking_hands",
        tag=InteractionTag.handshake,
        contact=(ContactTag.hands,),
        affection=Affection.affection,
        posture=Posture.standing,
        moment=_Moment("hands", "contact"),
        note="The right hands clasp between them.",
    ),
    "06": _ClassSpec(
        label="hugging",
        tag=InteractionTag.hug,
        contact=(ContactTag.back, ContactTag.torso),
        affection=Affection.affection,
        posture=Posture.standing,
        moment=_Moment("torsos", "contact"),
        note="Chest to chest with an arm around the other's back.",
    ),
    "07": _ClassSpec(
        label="exchanging_objects",
        tag=InteractionTag.exchange_object,
        contact=(ContactTag.object,),
        affection=Affection.affection,
        posture=Posture.standing,
        moment=_Moment("hands", "contact"),
        note=(
            "A sheet of paper passes between them and the hands stay apart, so the only contact is"
            " through the object."
        ),
    ),
    "08": _ClassSpec(
        label="punching",
        tag=InteractionTag.punch,
        contact=(ContactTag.none,),
        affection=Affection.aggression,
        posture=Posture.standing,
        moment=_Moment("hand_chest", "peak"),
        note="The punch is a staged near miss: the fist stops short of the other's chest.",
    ),
}

# Verified by eye in _index/sheets/sbu_actions.png and written down in
# _index/measured/viewing_notes.md: one corridor with a wooden door, two students, flat indoor
# light. Both subjects stand in profile facing each other in front of a fixed Kinect, so the pair
# is seen from the side and from nowhere else.
SETTING: Final = "indoor corridor, flat overhead light"
CAMERA_ANGLES: Final = ("side",)
USAGE: Final = UsageClass.pose_derivable
"""Kinect-era 640x480 in one corridor: the geometry is real, the pixels are a laboratory."""

_AFFECTION_BY_MAP_VALUE: Final = {
    "staging": Affection.staging,
    "true": Affection.affection,
    "false": Affection.aggression,
}


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """Every SBU sequence this source contributes, plus one line per thing skipped and why.

    Guarantees: clips are sorted by ``clip_id``; the same ``root`` and ``ingested_at`` produce
    byte-identical clips, because every number is computed from the bytes on disk in a fixed order
    and nothing is read from the clock; no clip is emitted whose label or affection disagrees with
    ``class_maps.json``; every emitted key pose carries both people, OpenPose-18 joint names, and
    no joint the Kinect 15-joint skeleton cannot supply.
    """
    index, failure = _load_index(root / "_index" / "measured")
    if index is None:
        return [], [failure or "sbu: the measured index could not be read"]

    specs, skipped, reported = _verified_specs(index["class_maps.json"])
    if not specs:
        return [], skipped
    clips: list[ReferenceClip] = []
    for entry in sorted(index["sbu.json"], key=lambda row: str(row.get("path", ""))):
        clip = _clip(root, entry, specs, reported, ingested_at, skipped)
        if clip is not None:
            clips.append(clip)
    clips.sort(key=lambda clip: clip.clip_id)
    return clips, skipped


def _load_index(measured: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The measured JSON this ingester reads, or a reason it could not be read.

    A host without ``/mnt/fast/reference`` is a normal condition for this repo, so a missing index
    is one readable line in the library manifest rather than a traceback in the build driver.
    """
    out: dict[str, Any] = {}
    for name in _INDEX_FILES:
        path = measured / name
        if not path.is_file():
            return None, f"sbu: {path} is not on disk, so no SBU sequence was read"
        try:
            out[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return None, f"sbu: {path} could not be parsed ({exc}), so no SBU sequence was read"
    return out, None


def _verified_specs(class_maps: Any) -> tuple[dict[str, _ClassSpec], list[str], frozenset[str]]:
    """The action ids whose label and affection agree with the verified class map.

    Guarantees: an id this module has a spec for but the map disagrees with is left out and
    reported, so a relabelled dataset can never quietly retag a clip; an id in the map with no spec
    here is also reported, because a new SBU action is a decision for a person to make. The third
    return value is every id these lines already account for, so a sequence whose action id appears
    in neither the map nor the specs can still be reported once per sequence rather than dropped.
    """
    classes = {}
    if isinstance(class_maps, dict):
        section = class_maps.get(_CLASS_MAP_KEY)
        if isinstance(section, dict) and isinstance(section.get("classes"), dict):
            classes = section["classes"]
    if not classes:
        return (
            {},
            [f"sbu: class_maps.json has no verified {_CLASS_MAP_KEY} class map, so no clip"],
            frozenset(),
        )

    verified: dict[str, _ClassSpec] = {}
    notes: list[str] = []
    for action_id in sorted(set(classes) | set(_SPECS)):
        entry = classes.get(action_id)
        spec = _SPECS.get(action_id)
        if spec is None:
            label = entry.get("label") if isinstance(entry, dict) else entry
            notes.append(
                f"sbu action {action_id}: class_maps.json calls it {label!r} and this ingester has"
                " no tag for it, so its sequences were not read"
            )
            continue
        if not isinstance(entry, dict):
            notes.append(
                f"sbu action {action_id}: not in the verified class map, so its sequences were not"
                " read even though this ingester has a spec for it"
            )
            continue
        mapped = _AFFECTION_BY_MAP_VALUE.get(str(entry.get("affection")).lower())
        if entry.get("label") != spec.label or mapped is not spec.affection:
            notes.append(
                f"sbu action {action_id}: the verified map says"
                f" {entry.get('label')!r}/{entry.get('affection')!r} and this ingester says"
                f" {spec.label!r}/{spec.affection.value!r}, so its sequences were not read"
            )
            continue
        verified[action_id] = spec
    return verified, notes, frozenset(classes) | frozenset(_SPECS)


def _clip(
    root: Path,
    entry: Any,
    specs: dict[str, _ClassSpec],
    reported: frozenset[str],
    ingested_at: str,
    skipped: list[str],
) -> ReferenceClip | None:
    """One clip per sequence directory, or None with a line in ``skipped`` saying why not."""
    if not isinstance(entry, dict):
        skipped.append(f"sbu: {entry!r} is not a sequence row in sbu.json")
        return None
    pair = str(entry.get("pair", ""))
    action_id = str(entry.get("action_id", ""))
    run = str(entry.get("run", ""))
    rel = str(entry.get("path", ""))
    where = f"sbu {pair}/{action_id}/{run}"
    spec = specs.get(action_id)
    if spec is None:
        if action_id not in reported:
            skipped.append(
                f"{where}: action id {action_id!r} is in neither the verified class map nor this"
                " ingester's specs, so the sequence was not read"
            )
        return None  # a known class is reported once for the whole class by _verified_specs
    if not rel.startswith(_CLEAN_DIR):
        skipped.append(f"{where}: sbu.json points at {rel!r}, which is not under {_CLEAN_DIR}")
        return None

    skeleton_rel = f"{rel}/skeleton_pos.txt"
    frames, reason = _read_frames(root / skeleton_rel)
    if frames is None:
        skipped.append(f"{where}: {reason}")
        return None
    if entry.get("frames") not in (None, len(frames)):
        skipped.append(
            f"{where}: sbu.json counts {entry.get('frames')} skeleton rows and the file holds"
            f" {len(frames)}, so the file's own count was used"
        )

    width, height = _sequence_frame_size(root, rel, frames, where, skipped)
    metres = tuple(_metric_people(frame, width, height) for frame in frames)
    gaps = _distances(metres)
    key_row = _key_row(spec.moment.rule, gaps)

    files = [_digested(root, skeleton_rel, "skeleton")]
    thumbnail = _rgb_file(root, rel, frames[key_row].number, where, skipped)
    if thumbnail is not None:
        files.append(thumbnail)

    measured = _measured(frames, metres, gaps, key_row, spec.moment.rule)
    return ReferenceClip(
        clip_id=f"sbu_{pair}_{action_id}_{run}".lower(),
        source=SOURCE,
        source_ref=f"{pair}/{action_id}/{run}",
        modality=Modality.rgbd_skeleton,
        # pose_derivable, never pixels_usable: see USAGE.
        usage=USAGE,
        people_count=_PEOPLE,
        affection=spec.affection,
        interaction_tags=(spec.tag,),
        contact_tags=spec.contact,
        postures=(spec.posture,),
        setting=SETTING,
        camera_angles=CAMERA_ANGLES,
        frame_count=len(frames),
        native_fps=_NATIVE_FPS,
        duration_s=round(len(frames) / _NATIVE_FPS, 4),
        width=width,
        height=height,
        pose_format="kinect15",
        pose_root=rel,
        key_poses=(_key_pose(frames, key_row, spec),),
        caption=_caption(pair, run, spec, len(frames), measured),
        caption_source="derived",
        measured=measured,
        files=tuple(files),
        ingested_at=ingested_at,
        ingester_version=INGESTER_VERSION,
    )


# --- reading and normalising -------------------------------------------------------------------


def _read_frames(path: Path) -> tuple[tuple[_Frame, ...] | None, str | None]:
    """Every normalised skeleton row of one sequence, or None and a reason it was unusable.

    Guarantees: x and y are image fractions of the physical view and are not clamped, z is metres,
    joint order is the dataset's own, and a row with anything other than 91 values fails the whole
    sequence rather than being padded, because a short row would silently move a joint.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"{path.name} could not be read ({exc})"
    frames: list[_Frame] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split(",")
        if len(parts) != _COLUMNS:
            return None, f"{path.name} line {number} has {len(parts)} values, expected {_COLUMNS}"
        try:
            values = [float(part) for part in parts]
        except ValueError:
            return None, f"{path.name} line {number} holds a value that is not a number"
        people = tuple(
            tuple(
                _Joint(
                    # The documented normalisation, read as a centred 2560x1920 coordinate: unit
                    # gain, and the stored PNGs are the mirror of this frame, not the other way
                    # round. See the module docstring.
                    x=values[1 + person * _JOINTS * 3 + joint * 3],
                    y=values[2 + person * _JOINTS * 3 + joint * 3],
                    z=values[3 + person * _JOINTS * 3 + joint * 3] * _Z_METRES,
                )
                for joint in range(_JOINTS)
            )
            for person in range(_PEOPLE)
        )
        frames.append(_Frame(number=int(values[0]), people=people))
    if not frames:
        return None, f"{path.name} holds no skeleton rows"
    return tuple(frames), None


def _sequence_frame_size(
    root: Path,
    rel: str,
    frames: tuple[_Frame, ...],
    where: str,
    skipped: list[str],
) -> tuple[int, int]:
    """The RGB frame size of one sequence, read from a PNG header rather than assumed."""
    path = root / rel / f"rgb_{frames[0].number:06d}.png"
    size = _png_size(path)
    if size is None:
        skipped.append(
            f"{where}: {path.name} is missing or not a PNG, so the documented"
            f" {_FRAME_SIZE[0]}x{_FRAME_SIZE[1]} Kinect frame size was used"
        )
        return _FRAME_SIZE
    return size


def _png_size(path: Path) -> tuple[int, int] | None:
    """Width and height from a PNG's IHDR, or None when the file is missing or not a PNG.

    Reads 24 bytes: a frame is only needed for its size here, and decoding the 13644 PNGs these
    sequences hold would make an ingest pass cost minutes for two integers.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


# --- measuring ---------------------------------------------------------------------------------


def _metric_people(frame: _Frame, width: int, height: int) -> _PeopleMetres:
    """One frame's joints as camera-space metres, for distances between the two bodies.

    Guarantees: z is the source's own metric depth; x and y are scaled by ``_FOCAL_PX``, so every
    distance derived from this inherits that one assumed intrinsic and nothing else.
    """
    return tuple(
        tuple(
            (
                (joint.x * width - width / 2.0) * joint.z / _FOCAL_PX,
                (joint.y * height - height / 2.0) * joint.z / _FOCAL_PX,
                joint.z,
            )
            for joint in person
        )
        for person in frame.people
    )


def _chest(person: tuple[_Point, ...]) -> _Point:
    """The midpoint of neck and torso: the closest thing to a sternum a Kinect 15 skeleton has."""
    neck, torso = person[_ROW_NECK], person[_ROW_TORSO]
    return ((neck[0] + torso[0]) / 2.0, (neck[1] + torso[1]) / 2.0, (neck[2] + torso[2]) / 2.0)


def _distances(metres: tuple[_PeopleMetres, ...]) -> dict[str, list[float]]:
    """Per-frame inter-person distances in metres, one list per rule a key frame can be picked by.

    Guarantees: every list has one entry per frame, in frame order, so an index into any of them
    means the same frame; ``joints`` is the closest joint pair of any kind, which is the measure
    that says whether the two bodies met at all.
    """
    out: dict[str, list[float]] = {
        "hands": [],
        "torsos": [],
        "hand_chest": [],
        "foot_chest": [],
        "joints": [],
    }
    for people in metres:
        first, second = people
        out["hands"].append(
            min(math.dist(first[a], second[b]) for a in _ROWS_HAND for b in _ROWS_HAND)
        )
        out["torsos"].append(math.dist(first[_ROW_TORSO], second[_ROW_TORSO]))
        out["hand_chest"].append(
            min(
                math.dist(actor[row], _chest(other))
                for actor, other in ((first, second), (second, first))
                for row in _ROWS_HAND
            )
        )
        out["foot_chest"].append(
            min(
                math.dist(actor[row], _chest(other))
                for actor, other in ((first, second), (second, first))
                for row in _ROWS_FOOT
            )
        )
        out["joints"].append(min(math.dist(a, b) for a in first for b in second))
    return out


def _key_row(rule: str, gaps: dict[str, list[float]]) -> int:
    """The row index of the closest-contact frame under one rule.

    Guarantees: ties go to the earliest frame, so the choice does not depend on iteration order.
    """
    series = gaps[rule]
    best = 0
    for index in range(1, len(series)):
        if series[index] < series[best]:
            best = index
    return best


def _travel_m(metres: tuple[_PeopleMetres, ...]) -> float:
    """The largest distance either person's torso covers between any two frames of the sequence."""
    if len(metres) < 2:
        return 0.0
    out = 0.0
    for person in range(_PEOPLE):
        track = [frame[person][_ROW_TORSO] for frame in metres]
        for index, point in enumerate(track):
            for other in track[index + 1 :]:
                out = max(out, math.dist(point, other))
    return out


def _out_of_frame_fraction(frames: tuple[_Frame, ...]) -> float:
    """The share of this sequence's joint observations that fall outside the frame.

    The contract keeps such joints as measured rather than clamping them, so the share is recorded
    per clip: a clip whose skeletons mostly left the frame is then findable instead of surprising.
    """
    total = len(frames) * _PEOPLE * _JOINTS
    outside = sum(
        1
        for frame in frames
        for person in frame.people
        for joint in person
        if not (0.0 <= joint.x <= 1.0 and 0.0 <= joint.y <= 1.0)
    )
    return outside / total if total else 0.0


def _measured(
    frames: tuple[_Frame, ...],
    metres: tuple[_PeopleMetres, ...],
    gaps: dict[str, list[float]],
    key_row: int,
    rule: str,
) -> dict[str, float | list[float]]:
    """The clip's own numbers, in the baker's vocabulary where one already exists.

    Guarantees: every distance is metres and every key is present on every SBU clip, so a query can
    compare clips and find one whose tags disagree with its geometry. ``key_pose_source_frame`` is
    the Kinect frame number, because ``ReferenceKeyPose.frame_index`` is a row index and the PNGs
    on disk are named by the other one.
    """
    return {
        "closest_wrists_m": round(min(gaps["hands"]), _MEASURED_DP),
        "root_gap_m": round(min(gaps["torsos"]), _MEASURED_DP),
        "closest_joint_gap_m": round(min(gaps["joints"]), _MEASURED_DP),
        "hand_to_other_chest_m": round(min(gaps["hand_chest"]), _MEASURED_DP),
        "foot_to_other_chest_m": round(min(gaps["foot_chest"]), _MEASURED_DP),
        "travel_m": round(_travel_m(metres), _MEASURED_DP),
        "out_of_frame_joint_fraction": round(_out_of_frame_fraction(frames), _MEASURED_DP),
        "key_pose_gap_m": round(gaps[rule][key_row], _MEASURED_DP),
        "key_pose_source_frame": float(frames[key_row].number),
    }


# --- key poses ---------------------------------------------------------------------------------


def _key_pose(frames: tuple[_Frame, ...], key_row: int, spec: _ClassSpec) -> ReferenceKeyPose:
    """The one moment of a sequence worth sampling, as an OpenPose-18 pose for both people.

    Guarantees: ``frame_index`` is the row index, so it indexes ``frame_count`` as the contract
    requires; joint names are OpenPose-18 and only the fourteen a Kinect 15-joint skeleton can
    fill are present, with no eye, ear or mid-spine joint invented; x and y are unclamped image
    fractions and ``in_frame`` records whether each one landed inside the frame; ``bones`` is the
    OpenPose-18 limb list restricted to the joints that exist.
    """
    people = []
    for index, person in enumerate(frames[key_row].people):
        joints = {}
        for row, (_kinect, slot) in enumerate(_KINECT15_TO_OPENPOSE18):
            if slot is None:
                continue
            joint = person[row]
            joints[slot] = ReferenceJoint(
                x=round(joint.x, _JOINT_DP),
                y=round(joint.y, _JOINT_DP),
                z=round(joint.z, _JOINT_DP),
                # visible stays at the contract default: the clean SBU files carry no per-joint
                # confidence, so occlusion here would be a guess.
                in_frame=bool(0.0 <= joint.x <= 1.0 and 0.0 <= joint.y <= 1.0),
            )
        bones = tuple((a, b) for a, b in OPENPOSE18_LIMBS if a in joints and b in joints)
        people.append(ReferencePersonPose(person_index=index, joints=joints, bones=bones))
    return ReferenceKeyPose(
        frame_index=key_row,
        label=spec.moment.label,
        people=tuple(people),
        contact=spec.contact,
    )


# --- files and prose ---------------------------------------------------------------------------


def _digested(root: Path, rel: str, role: FileRole) -> ReferenceFile:
    """One file with its real sha256 and size, addressed relative to the reference root."""
    path = root / rel
    return ReferenceFile(
        role=role,
        path=rel,
        sha256=file_sha256(path),
        size_bytes=path.stat().st_size,
    )


def _rgb_file(
    root: Path, rel: str, number: int, where: str, skipped: list[str]
) -> ReferenceFile | None:
    """The one RGB frame the key pose was read from, digested, or None with a reason.

    Only this frame is listed and digested. The 282 sequences hold 6822 RGB and 6822 depth PNGs,
    one of each per skeleton row, and a consumer who wants the rest can name them from
    ``pose_root`` and the frame numbers in ``skeleton_pos.txt``.
    """
    frame_rel = f"{rel}/rgb_{number:06d}.png"
    if not (root / frame_rel).is_file():
        skipped.append(
            f"{where}: {frame_rel} is not on disk, so the clip lists no frame for its key pose"
        )
        return None
    return _digested(root, frame_rel, "thumbnail")


def _caption(
    pair: str,
    run: str,
    spec: _ClassSpec,
    frame_count: int,
    measured: dict[str, float | list[float]],
) -> str:
    """One sentence a search can read, built from the verified label and the measured geometry."""
    action = spec.label.replace("_", " ")
    closest = measured["closest_joint_gap_m"]
    return (
        f"Two students in a corridor, {action}, seen from the side by a fixed Kinect."
        f" Pair {pair}, run {run}: {frame_count} frames of 15-joint skeletons for both people at"
        f" {_NATIVE_FPS:.0f} fps, closest joint gap {closest} m. {spec.note}"
    )
