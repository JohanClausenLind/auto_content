"""The reference library: real human interaction, queryable in words.

Seven sources of two-person material live outside the repo under ``/mnt/fast/reference``: mocap,
multi-camera capture, labelled skeleton sets, broadcast footage. This module is the contract for one
queryable unit of it, for the library manifest, and for a query and its answer, so a retrieval
stage's output is a contract rather than a dict.

Three decisions are worth reading before the field lists.

**Vocabularies are closed.** ``InteractionTag``, ``ContactTag``, ``Posture`` and ``Affection`` are
StrEnums, not free text, because retrieval depends on the values matching. ``Affection`` is the
field that keeps a shove out of a tender scene: half of the labelled material on disk is aggression.

**Four tags name things the library does not have.** ``head_on_shoulder``, ``cuddle``,
``slow_dance`` and ``carry_child`` are declared and match nothing. That is deliberate. A query for
them comes back empty with the term listed in ``expanded_terms``, so the caller sees the library's
gap instead of being handed a near-miss. The gap is the shooting list.

**Usage class is about the material, not permission.** ``reference_only`` means the pixels cannot be
used as a visual reference no matter who owns them - every Harmony4D frame has a camera tripod
between the lens and the subjects. ``pose_derivable`` means poses can be read off it;
``pixels_usable`` means the image itself is worth looking at. Licences are deliberately not modelled
here at all: they gate nothing in this project.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from content_factory.schemas.base import SchemaModel, Sha256Hex, VersionedModel
from content_factory.schemas.sequences import Point, SkeletonPose

ClipId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_]{2,63}$")]
"""Readable on purpose, e.g. ``cmu_22_23_08``: these ids appear in logs and shot plans."""

SemVerStr = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]


class ReferenceSource(StrEnum):
    cmu_mocap = "cmu_mocap"
    harmony4d = "harmony4d"
    sbu_kinect = "sbu_kinect"
    ut_interaction = "ut_interaction"
    tv_human_interactions = "tv_human_interactions"
    motionhub_egobody = "motionhub_egobody"
    motionhub_grab = "motionhub_grab"
    motionhub_humanml3d = "motionhub_humanml3d"
    pexels = "pexels"
    operator_recording = "operator_recording"


class UsageClass(StrEnum):
    """What the material is good for, measured rather than assumed."""

    reference_only = "reference_only"
    """Neither the pixels nor the poses can drive a shot. Indexed so the gap is visible."""
    pose_derivable = "pose_derivable"
    """Poses can be read off it. Harmony4D and SBU are here: the geometry is real, the pixels are
    a laboratory."""
    pixels_usable = "pixels_usable"
    """The image itself is worth looking at as a reference."""


class Modality(StrEnum):
    mocap_segments = "mocap_segments"
    """cf.clip.v2: world segment directions, rig-independent."""
    mocap_skeleton = "mocap_skeleton"
    mocap_smpl = "mocap_smpl"
    mocap_smplh = "mocap_smplh"
    multiview_rgb = "multiview_rgb"
    rgbd_skeleton = "rgbd_skeleton"
    video_rgb = "video_rgb"
    still_image = "still_image"


class Affection(StrEnum):
    affection = "affection"
    neutral = "neutral"
    aggression = "aggression"
    staging = "staging"
    """Approaching and departing: not affection, but the shots either side of it."""


class InteractionTag(StrEnum):
    # Measured on disk, from the baked clip library and the verified class maps.
    handshake = "handshake"
    hug = "hug"
    kiss = "kiss"
    hold_hands_walk = "hold_hands_walk"
    hand_on_shoulder = "hand_on_shoulder"
    hands_on_shoulders = "hands_on_shoulders"
    comfort_kneeling = "comfort_kneeling"
    shoulder_rub = "shoulder_rub"
    shelter_child = "shelter_child"
    link_arms_walk = "link_arms_walk"
    lead_by_arm = "lead_by_arm"
    help_up = "help_up"
    meet_and_sit = "meet_and_sit"
    walk_together = "walk_together"
    conversation = "conversation"
    swing_partner = "swing_partner"
    dance_novelty = "dance_novelty"
    ring_around = "ring_around"
    high_five = "high_five"
    low_five = "low_five"
    pass_object = "pass_object"  # noqa: S105 - an interaction, not a credential
    exchange_object = "exchange_object"
    throw_catch = "throw_catch"
    exercise_together = "exercise_together"
    march = "march"
    blindfold_tag = "blindfold_tag"
    race_for_seat = "race_for_seat"
    stumble_into = "stumble_into"
    approach = "approach"
    depart = "depart"
    no_contact = "no_contact"
    # Aggression, kept so a query for it is answerable and a query for tenderness can exclude it.
    pull_resist = "pull_resist"
    pull_by_elbow = "pull_by_elbow"
    arm_wrestle = "arm_wrestle"
    quarrel = "quarrel"
    stare_down = "stare_down"
    threaten = "threaten"
    kick = "kick"
    punch = "punch"
    push = "push"
    point = "point"
    # Declared and empty. A query naming one of these returns nothing and says so, which is the
    # honest answer and also the operator's shooting list.
    head_on_shoulder = "head_on_shoulder"
    cuddle = "cuddle"
    slow_dance = "slow_dance"
    carry_child = "carry_child"
    stroke_hair = "stroke_hair"
    hold_face = "hold_face"


ABSENT_INTERACTIONS: frozenset[InteractionTag] = frozenset(
    {
        InteractionTag.head_on_shoulder,
        InteractionTag.cuddle,
        InteractionTag.slow_dance,
        InteractionTag.carry_child,
        InteractionTag.stroke_hair,
        InteractionTag.hold_face,
    }
)
"""Tags no source on disk covers. Named here so a retriever can tell a caller the difference
between "nothing matched your words" and "we have nothing like this at all"."""


class ContactTag(StrEnum):
    none = "none"
    hands = "hands"
    arm = "arm"
    shoulder = "shoulder"
    torso = "torso"
    back = "back"
    head_shoulder = "head_shoulder"
    head_chest = "head_chest"
    face = "face"
    lap = "lap"
    object = "object"


class Posture(StrEnum):
    standing = "standing"
    sitting = "sitting"
    kneeling = "kneeling"
    walking = "walking"
    running = "running"
    crouching = "crouching"
    lying = "lying"
    dancing = "dancing"
    leaning = "leaning"


PoseFormat = Literal[
    "none", "cf_clip_v2", "asf_amc", "smpl", "smplh", "kinect15", "coco17_3d", "bbox_only"
]

FileRole = Literal[
    "clip_json",
    "skeleton",
    "skeleton_def",
    "smpl",
    "poses3d",
    "poses2d",
    "bbox",
    "annotation",
    "calibration",
    "video",
    "thumbnail",
]

KeyPoseLabel = Literal["start", "contact", "peak", "settle", "release", "end"]


class ReferenceFile(SchemaModel):
    """One file on disk that belongs to a clip, addressed relative to the reference root."""

    role: FileRole
    path: str = Field(min_length=1, max_length=400)
    sha256: Sha256Hex
    size_bytes: int = Field(ge=0)
    person_index: int | None = Field(default=None, ge=0, le=15)
    camera: str | None = Field(default=None, max_length=32)


class ReferenceJoint(SchemaModel):
    """One joint. ``x`` and ``y`` are normalised but deliberately NOT clamped.

    Joints leave the frame: 3.55 % of the Harmony4D joints and 13.75 % of the SBU joints fall
    outside it. Clamping would move them to the edge and invent a pose; dropping them at ingest
    would throw away the fact that the capture saw them. They are kept as measured, and the
    conversion to a ``SkeletonPose`` is where the drop rule applies.
    """

    x: float
    y: float
    z: float | None = None
    """Metres, where the source has depth."""
    visible: bool = True
    in_frame: bool = True

    def to_point(self) -> Point:
        """The pipeline's own normalised point. Raises when this joint is outside the frame."""
        return Point(x=self.x, y=self.y)

    @property
    def usable(self) -> bool:
        return self.in_frame and 0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0


class ReferencePersonPose(SchemaModel):
    person_index: int = Field(ge=0, le=15)
    joints: dict[str, ReferenceJoint] = Field(min_length=1)
    """Keyed by OpenPose-18 joint name, so a pose here can drive the existing control passes."""
    bones: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def _bones_reference_joints(self) -> ReferencePersonPose:
        for a, b in self.bones:
            if a not in self.joints or b not in self.joints:
                msg = f"bone ({a}, {b}) references a joint this person does not have"
                raise ValueError(msg)
        return self

    def to_skeleton_pose(self) -> SkeletonPose | None:
        """The pipeline's ``SkeletonPose``, dropping joints outside the frame.

        Same rule the Blender control bundle already applies, so a reference pose and a rendered
        pose are filtered identically. Returns None when nothing survives.
        """
        kept = {name: joint.to_point() for name, joint in self.joints.items() if joint.usable}
        if not kept:
            return None
        bones = tuple((a, b) for a, b in self.bones if a in kept and b in kept)
        return SkeletonPose(joints=kept, bones=bones)


class ReferenceKeyPose(SchemaModel):
    """One moment worth sampling: where the hands meet, where the embrace settles."""

    frame_index: int = Field(ge=0)
    label: KeyPoseLabel
    camera: str | None = Field(default=None, max_length=32)
    people: tuple[ReferencePersonPose, ...] = Field(min_length=1)
    contact: tuple[ContactTag, ...] = ()


class ReferenceClip(VersionedModel):
    """One queryable sequence: what it is, what it can drive, and where its files are."""

    clip_id: ClipId
    source: ReferenceSource
    source_ref: str = Field(min_length=1, max_length=200)
    """How to find it in the original dataset, e.g. ``subjects/22/22_08.amc`` or ``hug_0001``."""
    modality: Modality
    usage: UsageClass
    people_count: int = Field(ge=1, le=8)
    affection: Affection
    interaction_tags: tuple[InteractionTag, ...] = Field(min_length=1)
    contact_tags: tuple[ContactTag, ...] = ()
    postures: tuple[Posture, ...] = Field(min_length=1)
    setting: str = Field(default="unknown", max_length=64)
    camera_angles: tuple[str, ...] = ()
    """Angle buckets this clip can be seen from, e.g. ``front``, ``side``, ``back_3q``."""
    view_count: int = Field(default=1, ge=1, le=64)
    frame_count: int = Field(ge=1)
    native_fps: float = Field(gt=0.0)
    sampled_fps: float | None = Field(default=None, gt=0.0)
    duration_s: float = Field(gt=0.0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    pose_format: PoseFormat
    pose_root: str | None = Field(default=None, max_length=400)
    retargeted_clip: str | None = Field(default=None, max_length=64)
    """The ``clips/<name>.json`` a ShotSpec can name directly, when one has been baked."""
    key_poses: tuple[ReferenceKeyPose, ...] = ()
    caption: str = Field(default="", max_length=600)
    caption_source: Literal["dataset", "parsed_index", "operator", "derived"]
    measured: dict[str, float | list[float]] = Field(default_factory=dict)
    """The baker's own numbers: ``closest_wrists_m``, ``root_gap_m``, ``travel_m``. Measured, so a
    clip whose tags disagree with its geometry can be found."""
    files: tuple[ReferenceFile, ...] = Field(min_length=1)
    ingested_at: str = Field(min_length=10, max_length=32)
    ingester_version: SemVerStr

    @model_validator(mode="after")
    def _coherent(self) -> ReferenceClip:
        for name, values in (
            ("interaction_tags", self.interaction_tags),
            ("contact_tags", self.contact_tags),
            ("postures", self.postures),
        ):
            listed = [v.value for v in values]
            if listed != sorted(set(listed)):
                msg = f"{name} must be sorted and unique, got {listed}"
                raise ValueError(msg)

        if ContactTag.none in self.contact_tags and len(self.contact_tags) > 1:
            msg = "contact_tags: 'none' cannot appear alongside a real contact"
            raise ValueError(msg)
        if not self.contact_tags and tuple(self.interaction_tags) != (InteractionTag.no_contact,):
            msg = "contact_tags is required unless the only interaction tag is 'no_contact'"
            raise ValueError(msg)

        frames = [k.frame_index for k in self.key_poses]
        if frames != sorted(set(frames)):
            msg = "key_poses must be strictly increasing by frame_index"
            raise ValueError(msg)
        for pose in self.key_poses:
            if pose.frame_index >= self.frame_count:
                msg = f"key pose at frame {pose.frame_index} >= frame_count {self.frame_count}"
                raise ValueError(msg)
            if len(pose.people) > self.people_count:
                msg = (
                    f"key pose at frame {pose.frame_index} has {len(pose.people)} people but the"
                    f" clip declares {self.people_count}"
                )
                raise ValueError(msg)
            indices = [p.person_index for p in pose.people]
            if len(set(indices)) != len(indices):
                msg = f"key pose at frame {pose.frame_index} repeats a person_index"
                raise ValueError(msg)

        if self.pose_format != "none" and not self.pose_root:
            msg = f"pose_format {self.pose_format} needs a pose_root"
            raise ValueError(msg)
        if self.modality == Modality.multiview_rgb and self.view_count < 2:
            msg = "multiview_rgb needs view_count >= 2"
            raise ValueError(msg)
        if not self.caption and self.caption_source != "derived":
            msg = "an empty caption must declare caption_source='derived'"
            raise ValueError(msg)
        if self.retargeted_clip and self.pose_format != "cf_clip_v2":
            msg = "retargeted_clip names a cf.clip.v2 file, so pose_format must be cf_clip_v2"
            raise ValueError(msg)
        return self

    @property
    def has_absent_tag(self) -> bool:
        """True when this clip claims a tag nothing on disk should have. A guard, not a feature."""
        return bool(set(self.interaction_tags) & ABSENT_INTERACTIONS)

    def search_text(self) -> dict[str, str]:
        """The five FTS columns, built one way so the builder and a test cannot disagree."""
        return {
            "interaction": " ".join(t.value for t in self.interaction_tags),
            "contact": " ".join(t.value for t in self.contact_tags),
            "posture": " ".join(p.value for p in self.postures),
            "setting": self.setting,
            "caption": self.caption,
        }


class ReferenceLibrary(VersionedModel):
    """The manifest for one built index: what went in, and what to rebuild it from."""

    library_id: str = Field(min_length=3, max_length=64)
    root: str = Field(min_length=1, max_length=400)
    index_path: str = Field(min_length=1, max_length=400)
    builder_version: SemVerStr
    built_at: str = Field(min_length=10, max_length=32)
    lexicon_sha256: Sha256Hex
    sources: tuple[ReferenceSource, ...] = ()
    clip_count: int = Field(ge=0)
    manifest_sha256: Sha256Hex
    """Over the ordered clip documents, never over the sqlite bytes: the sqlite file depends on the
    host's libsqlite3 build, which cannot be pinned, so its digest is not reproducible."""
    skipped: tuple[str, ...] = ()
    """What was on disk and not ingested, with the reason, so an absence is never silent."""


class ReferenceQuery(SchemaModel):
    """A question in words plus the constraints that are not words."""

    text: str = Field(min_length=1, max_length=500)
    people_count: int | None = Field(default=None, ge=1, le=8)
    require_interaction: tuple[InteractionTag, ...] = ()
    require_contact: tuple[ContactTag, ...] = ()
    require_posture: tuple[Posture, ...] = ()
    require_affection: Affection | None = None
    require_usage: UsageClass | None = None
    require_pose: bool = False
    """Only clips something can actually be driven from."""
    sources: tuple[ReferenceSource, ...] = ()
    min_duration_s: float | None = Field(default=None, gt=0.0)
    max_duration_s: float | None = Field(default=None, gt=0.0)
    limit: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def _durations_make_sense(self) -> ReferenceQuery:
        if (
            self.min_duration_s is not None
            and self.max_duration_s is not None
            and self.min_duration_s > self.max_duration_s
        ):
            msg = "min_duration_s is greater than max_duration_s"
            raise ValueError(msg)
        return self


class ReferenceMatch(SchemaModel):
    clip_id: ClipId
    rank: int = Field(ge=0)
    score: float
    matched_interaction: tuple[str, ...] = ()
    matched_contact: tuple[str, ...] = ()
    snippet: str = Field(default="", max_length=400)


class ReferenceMatchSet(VersionedModel):
    """The answer, including what the query was understood to mean.

    ``expanded_terms`` and ``unmatched_words`` are not diagnostics, they are the answer's honesty:
    a caller can see that "head on shoulder" was understood, searched for, and found nothing.
    """

    library_id: str = Field(min_length=3, max_length=64)
    query: ReferenceQuery
    match_expression: str = Field(default="", max_length=4000)
    expanded_terms: tuple[str, ...] = ()
    unmatched_words: tuple[str, ...] = ()
    absent_terms: tuple[str, ...] = ()
    """Terms understood, searched for, and known to be missing from every source on disk."""
    matches: tuple[ReferenceMatch, ...] = ()
    retriever: Literal["fts5_bm25"] = "fts5_bm25"
    retriever_version: SemVerStr
    lexicon_sha256: Sha256Hex

    @model_validator(mode="after")
    def _ranked(self) -> ReferenceMatchSet:
        ids = [m.clip_id for m in self.matches]
        if len(set(ids)) != len(ids):
            msg = "a clip appears twice in the matches"
            raise ValueError(msg)
        for i, match in enumerate(self.matches):
            if match.rank != i:
                msg = f"match {match.clip_id} has rank {match.rank} at position {i}"
                raise ValueError(msg)
        scores = [m.score for m in self.matches]
        if scores != sorted(scores, reverse=True):
            msg = "matches must be ordered by non-increasing score"
            raise ValueError(msg)
        if len(self.matches) > self.query.limit:
            msg = f"{len(self.matches)} matches exceeds the query limit {self.query.limit}"
            raise ValueError(msg)
        return self
