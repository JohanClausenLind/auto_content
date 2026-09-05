"""Shot plans and control bundles for the Blender scene-control layer.

A ``ShotPlan`` is the 3D-side twin of a ``MotionPlan``: per shot it fixes the camera path, the
characters and props with their poses and placements, the environment, the lighting and which
control passes to render. The Blender skill (``skills/video/blender_scene``) consumes one
``ShotSpec`` and the control plane wraps its output as a ``ControlBundle`` — the same contract the
2D ``MotionPlan`` compiler produces, so downstream stages see one shape whichever compiler ran.

Coordinates are metres, Blender Z-up, right-handed. Image coordinates are normalised with y down.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, SemVer, Sha256Hex, VersionedModel
from content_factory.schemas.sequences import Box, ControlAsset, ControlKind, Easing, SkeletonPose

# Human-readable ids used for Blender object names and JSON keys (not opaque ids).
SlugId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
AssetRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Rgb = tuple[float, float, float]

MAX_SEG_ID = 254  # 0 is background; 255 is reserved for "unassigned" in palette decoding


class CameraPreset(StrEnum):
    static = "static"
    slow_push_in = "slow_push_in"
    slow_pull_out = "slow_pull_out"
    pan_left = "pan_left"
    pan_right = "pan_right"
    orbit_left = "orbit_left"
    orbit_right = "orbit_right"
    crane_up = "crane_up"
    crane_down = "crane_down"


class RenderEngine(StrEnum):
    workbench = "workbench"
    eevee = "eevee"
    cycles_cpu = "cycles_cpu"


class Transform(SchemaModel):
    position: Vec3 = (0.0, 0.0, 0.0)
    yaw_deg: float = Field(default=0.0, ge=-360.0, le=360.0)
    scale: float = Field(default=1.0, gt=0.0, le=100.0)


class CameraKeyframe(SchemaModel):
    """Camera state at one frame. Exactly one of ``look_at`` / ``rotation_euler_deg`` is set."""

    frame_index: int = Field(ge=0)
    position: Vec3
    look_at: Vec3 | None = None
    rotation_euler_deg: Vec3 | None = None
    lens_mm: float = Field(default=35.0, ge=8.0, le=400.0)
    focus_distance: float = Field(default=5.0, gt=0.0)
    easing_to_next: Easing = Easing.ease_in_out

    @model_validator(mode="after")
    def _one_orientation(self) -> CameraKeyframe:
        if (self.look_at is None) == (self.rotation_euler_deg is None):
            msg = "camera keyframe needs exactly one of look_at or rotation_euler_deg"
            raise ValueError(msg)
        return self


class CameraSpec(SchemaModel):
    preset: CameraPreset = CameraPreset.static
    sensor_width_mm: float = Field(default=36.0, gt=0.0, le=100.0)
    clip_start: float = Field(default=0.05, gt=0.0)
    clip_end: float = Field(default=100.0, gt=0.0)
    keyframes: tuple[CameraKeyframe, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _keyframes_ordered(self) -> CameraSpec:
        idx = [k.frame_index for k in self.keyframes]
        if idx[0] != 0:
            msg = "camera keyframes must start at frame 0"
            raise ValueError(msg)
        if idx != sorted(set(idx)):
            msg = "camera keyframes must be strictly increasing by frame_index"
            raise ValueError(msg)
        if self.clip_end <= self.clip_start:
            msg = "clip_end must be greater than clip_start"
            raise ValueError(msg)
        return self


class LibraryPose(SchemaModel):
    kind: Literal["library"] = "library"
    name: SlugId = "idle"


class ClipPose(SchemaModel):
    kind: Literal["clip"] = "clip"
    name: SlugId
    speed: float = Field(default=1.0, gt=0.0, le=10.0)
    offset_frames: int = Field(default=0, ge=0)
    loop: bool = True


class SegmentClipPose(SchemaModel):
    """A ``cf.clip.v2`` motion clip: captured world segment directions, aimed onto whatever rig is
    loaded at render time.

    ``ClipPose`` stores a quaternion per bone, which makes a clip specific to the body it was
    solved on: MPFB fits its rig to each character mesh, so the same quaternions land up to 22.6
    degrees wrong on a different character (measured across the four built assets). Directions are
    a property of the motion, so one clip is exact on every body.

    ``actor`` selects which performer of a multi-person clip this character plays. A two-person
    capture is one clip with both actors on one timeline, so two ``CharacterSpec`` entries pointing
    at the same clip with different actors reproduce the captured contact instead of staging two
    solo clips near each other.
    """

    kind: Literal["segments"] = "segments"
    name: SlugId
    actor: SlugId = "a"
    speed: float = Field(default=1.0, gt=0.0, le=10.0)
    offset_frames: int = Field(default=0, ge=0)
    loop: bool = False
    """Off by default: a captured interaction has a beginning and an end, unlike a walk cycle."""


class BoneRotation(SchemaModel):
    rotation_quaternion: Quat = (1.0, 0.0, 0.0, 0.0)


class BonePose(SchemaModel):
    kind: Literal["bones"] = "bones"
    rig: str = Field(default="mpfb.default", min_length=1, max_length=64)
    bones: dict[str, BoneRotation] = Field(min_length=1)


PoseRef = Annotated[
    LibraryPose | ClipPose | SegmentClipPose | BonePose, Field(discriminator="kind")
]


class CharacterSpec(SchemaModel):
    id: SlugId
    asset: AssetRef
    """Character asset name: ``<assets>/characters/<asset>/<asset>.blend``."""
    transform: Transform = Transform()
    pose: PoseRef = LibraryPose()
    seg_id: int | None = Field(default=None, ge=1, le=MAX_SEG_ID)
    reference_image_sha256: tuple[Sha256Hex, ...] = ()
    """Identity reference images (turnaround renders or a HiDream character sheet)."""
    appearance: str | None = Field(default=None, min_length=1, max_length=400)
    """How this figure looks, in the words an image model reads: build, hair, clothing.

    The mesh carries the body and nothing else. Without this the prompt says "a figure" and the
    model dresses it differently in every frame — measured four outfit changes inside one
    thirty-anchor run (STATUS: image_sequences.anchor_references note)."""


class PrimitiveSource(SchemaModel):
    kind: Literal["primitive"] = "primitive"
    shape: Literal["cube", "plane", "sphere", "cylinder"] = "cube"
    size: Vec3 = (1.0, 1.0, 1.0)


class GlbSource(SchemaModel):
    kind: Literal["glb"] = "glb"
    path: str = Field(min_length=1, max_length=512)


class BlendSource(SchemaModel):
    kind: Literal["blend"] = "blend"
    path: str = Field(min_length=1, max_length=512)
    collection: str = Field(min_length=1, max_length=128)


PropSource = Annotated[PrimitiveSource | GlbSource | BlendSource, Field(discriminator="kind")]


class PropSpec(SchemaModel):
    id: SlugId
    source: PropSource = PrimitiveSource()
    transform: Transform = Transform()
    color: Rgb = (0.5, 0.5, 0.5)
    seg: bool = True
    seg_id: int | None = Field(default=None, ge=1, le=MAX_SEG_ID)


class GroundSpec(SchemaModel):
    enabled: bool = True
    size: float = Field(default=40.0, gt=0.0)
    color: Rgb = (0.35, 0.35, 0.35)
    seg: bool = False


class WallsSpec(SchemaModel):
    size: Vec3 = (8.0, 8.0, 3.0)
    color: Rgb = (0.6, 0.6, 0.6)


class EnvironmentSpec(SchemaModel):
    ground: GroundSpec = GroundSpec()
    walls: WallsSpec | None = None
    background_color: Rgb = (0.05, 0.05, 0.06)


class LightingSpec(SchemaModel):
    preset: Literal["studio", "exterior_day", "exterior_dusk", "interior_warm"] = "studio"
    key_azimuth_deg: float = Field(default=35.0, ge=-360.0, le=360.0)
    key_elevation_deg: float = Field(default=45.0, ge=-90.0, le=90.0)
    intensity: float = Field(default=1.0, ge=0.0, le=100.0)
    shadows: bool = True


class DepthRange(SchemaModel):
    near: float = Field(gt=0.0)
    far: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _far_beyond_near(self) -> DepthRange:
        if self.far <= self.near:
            msg = "far must be greater than near"
            raise ValueError(msg)
        return self


class CannySpec(SchemaModel):
    low: int = Field(default=100, ge=0, le=255)
    high: int = Field(default=200, ge=0, le=255)


DEFAULT_PASSES: tuple[ControlKind, ...] = (
    ControlKind.rough_rgb,
    ControlKind.depth,
    ControlKind.depth_exr,
    ControlKind.normals,
    ControlKind.segmentation,
    ControlKind.pose_skeleton,
    ControlKind.layout_boxes,
)


class RenderSpec(SchemaModel):
    engine: RenderEngine = RenderEngine.workbench
    passes: tuple[ControlKind, ...] = DEFAULT_PASSES
    depth_range: Literal["auto"] | DepthRange = "auto"
    canny: CannySpec = CannySpec()
    frames: tuple[int, ...] | None = None
    """Optional subset of frame indices to render (e.g. only the LTX keyframe indices)."""

    @model_validator(mode="after")
    def _passes_unique(self) -> RenderSpec:
        if len(set(self.passes)) != len(self.passes):
            msg = "render passes must be unique"
            raise ValueError(msg)
        if self.frames is not None and list(self.frames) != sorted(set(self.frames)):
            msg = "render.frames must be strictly increasing"
            raise ValueError(msg)
        return self


class ShotSpec(VersionedModel):
    """One shot: everything the Blender scene controller needs to render its control passes."""

    shot_id: OpaqueId
    order: int = Field(ge=0)
    beat_id: OpaqueId | None = None
    seed: int = Field(default=0, ge=0)
    frame_count: int = Field(ge=1, le=600)
    fps: Literal[24, 25, 30, 60] = 24
    width: int = Field(ge=64, le=8192, multiple_of=32)
    height: int = Field(ge=64, le=8192, multiple_of=32)
    camera: CameraSpec
    characters: tuple[CharacterSpec, ...] = ()
    props: tuple[PropSpec, ...] = ()
    environment: EnvironmentSpec = EnvironmentSpec()
    lighting: LightingSpec = LightingSpec()
    render: RenderSpec = RenderSpec()
    anchor_frames: tuple[int, ...] = (0,)
    """Frame indices that get a generated anchor image (LTX keyframe guides). Must include 0."""
    motion_prompt: str = Field(default="", max_length=2000)
    """The progression: what changes from the first frame to the last, camera included.

    Compiled by :mod:`content_factory.shots.prompt_compile` from ``action`` and the camera preset.
    It is never a beat's narration: an anchor prompt built from spoken words asks the image model
    to illustrate a sentence rather than to describe one frame."""
    description: str | None = Field(default=None, max_length=2000)
    """One instant: what the first anchor frame looks like, standing still. Framing, lighting,
    environment and the film's visual subject — no motion, no narration, no beat number."""
    action: str = Field(default="", max_length=600)
    """What the subject does during the shot, if anything. Empty means the pose is held."""
    end_state: str | None = Field(default=None, max_length=600)
    """What the last frame looks like, when it differs from ``description`` by more than the
    camera move. None lets the prompt compiler derive it from the camera preset."""
    staging_note: str | None = Field(default=None, max_length=600)
    """For the operator, never for a model: which clip the shot was staged from, how large the cast
    solved in frame, whether the camera tracks.

    It has its own field because it used to be appended to ``description``, and ``description`` is
    prompt text. An image model handed "staged from cmu_20_21_02, framed at 0.62 body height"
    draws whatever it makes of that. The measurement still has to live on the plan rather than only
    in a stage's facts, because what the plan claims is checked against what
    ``underframed_shots`` measures off the finished camera."""

    @model_validator(mode="after")
    def _consistent(self) -> ShotSpec:
        for k in self.camera.keyframes:
            if k.frame_index >= self.frame_count:
                msg = f"camera keyframe {k.frame_index} >= frame_count"
                raise ValueError(msg)
        anchors = list(self.anchor_frames)
        if anchors != sorted(set(anchors)) or not anchors or anchors[0] != 0:
            msg = "anchor_frames must be strictly increasing and start with 0"
            raise ValueError(msg)
        if anchors[-1] >= self.frame_count:
            msg = f"anchor frame {anchors[-1]} >= frame_count"
            raise ValueError(msg)
        if self.render.frames is not None:
            if self.render.frames[-1] >= self.frame_count:
                msg = "render.frames contains an index >= frame_count"
                raise ValueError(msg)
            missing = set(anchors) - set(self.render.frames)
            if missing:
                msg = f"render.frames must include every anchor frame (missing {sorted(missing)})"
                raise ValueError(msg)
        ids = [c.id for c in self.characters] + [p.id for p in self.props]
        if len(set(ids)) != len(ids):
            msg = "character and prop ids must be unique within a shot"
            raise ValueError(msg)
        seg_ids = [c.seg_id for c in self.characters if c.seg_id is not None]
        seg_ids += [p.seg_id for p in self.props if p.seg and p.seg_id is not None]
        if len(set(seg_ids)) != len(seg_ids):
            msg = "explicit seg_id values must be unique within a shot"
            raise ValueError(msg)
        return self


class ShotPlan(VersionedModel):
    plan_id: OpaqueId
    deliverable_id: OpaqueId
    story_plan_hash: Sha256Hex | None = None
    planner: Literal["fixture", "story_presets", "reference", "llm"] = "story_presets"
    planner_version: SemVer
    shots: tuple[ShotSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _shots_consistent(self) -> ShotPlan:
        ids = [s.shot_id for s in self.shots]
        if len(set(ids)) != len(ids):
            msg = "shot ids must be unique"
            raise ValueError(msg)
        for i, s in enumerate(self.shots):
            if s.order != i:
                msg = f"shot {s.shot_id} has order {s.order}, expected {i}"
                raise ValueError(msg)
        first = self.shots[0]
        for s in self.shots[1:]:
            if (s.fps, s.width, s.height) != (first.fps, first.width, first.height):
                msg = "all shots in a plan must share fps, width and height"
                raise ValueError(msg)
        return self


# --------------------------------------------------------------------------------------------
# Compiled output, shared by the MotionPlan compiler and the Blender compiler.


class ControlEncoding(StrEnum):
    rgb8 = "rgb8"
    gray8 = "gray8"
    gray16 = "gray16"
    index8 = "index8"
    exr32 = "exr32"


class CameraFrame(SchemaModel):
    """Camera state for one frame. Rotation is a Blender camera quaternion (looks down local -Z,
    +Y up); ``intrinsics`` is (fx, fy, cx, cy) in pixels; ``world_to_camera`` is row-major 4x4."""

    frame_index: int = Field(ge=0)
    position: Vec3
    rotation_quat_wxyz: Quat
    lens_mm: float = Field(gt=0.0)
    sensor_width_mm: float = Field(gt=0.0)
    look_at: Vec3 | None = None
    intrinsics: tuple[float, float, float, float]
    world_to_camera: tuple[float, ...] = Field(min_length=16, max_length=16)


class ControlTrack(SchemaModel):
    kind: ControlKind
    encoding: ControlEncoding
    frames: tuple[ControlAsset, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _frames_consistent(self) -> ControlTrack:
        idx = [f.frame_index for f in self.frames]
        if idx != sorted(set(idx)):
            msg = "track frames must be strictly increasing by frame_index"
            raise ValueError(msg)
        for f in self.frames:
            if f.kind != self.kind:
                msg = f"track kind {self.kind} contains an asset of kind {f.kind}"
                raise ValueError(msg)
        return self


class SubjectTrack(SchemaModel):
    subject_id: SlugId
    label: str = Field(min_length=1, max_length=80)
    segmentation_index: int = Field(ge=1, le=MAX_SEG_ID)
    layouts: tuple[Box | None, ...] = ()
    poses: tuple[SkeletonPose | None, ...] = ()


class ControlBundle(VersionedModel):
    """Everything compiled for one shot (or one MotionPlan sequence): per-kind frame tracks,
    per-subject layout/pose tracks and, for 3D compilers, the camera per frame."""

    bundle_id: OpaqueId
    shot_id: OpaqueId
    plan_hash: Sha256Hex
    compiler: Literal["motion_plan", "blender"]
    compiler_version: SemVer
    blender_version: str | None = Field(default=None, max_length=64)
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)
    frame_count: int = Field(ge=1, le=600)
    fps: Literal[24, 25, 30, 60] = 24
    anchor_frames: tuple[int, ...] = (0,)
    tracks: tuple[ControlTrack, ...] = Field(min_length=1)
    subjects: tuple[SubjectTrack, ...] = ()
    camera: tuple[CameraFrame, ...] = ()

    @model_validator(mode="after")
    def _bundle_consistent(self) -> ControlBundle:
        kinds = [t.kind for t in self.tracks]
        if len(set(kinds)) != len(kinds):
            msg = "control bundle tracks must have unique kinds"
            raise ValueError(msg)
        for t in self.tracks:
            if t.frames[-1].frame_index >= self.frame_count:
                msg = f"track {t.kind} has a frame index >= frame_count"
                raise ValueError(msg)
        for s in self.subjects:
            for name, seq in (("layouts", s.layouts), ("poses", s.poses)):
                if seq and len(seq) != self.frame_count:
                    msg = f"subject {s.subject_id} {name} must have frame_count entries"
                    raise ValueError(msg)
        cam_idx = [c.frame_index for c in self.camera]
        if cam_idx != sorted(set(cam_idx)) or (cam_idx and cam_idx[-1] >= self.frame_count):
            msg = "camera frames must be strictly increasing and < frame_count"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------------------------
# Shot routing (hybrid workflow): which beats the deterministic renderer draws and which the
# generative chain (Blender controls → HiDream → LTX) produces. One decision per story beat.
# ---------------------------------------------------------------------------------------------

Route = Literal["render", "generate"]
"""``render``: Remotion (D3 / Vega-Lite / MapLibre / Manim asset scenes). ``generate``: the
scene-control chain. FFmpeg assembles both kinds in beat order in ``compose_video``."""


class BeatRoute(SchemaModel):
    beat_id: OpaqueId
    order: int = Field(ge=0)
    scene_kind: SlugId
    route: Route
    # The ShotSpec the generative branch renders for this beat; None for render-routed beats.
    shot_id: OpaqueId | None = None
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _shot_matches_route(self) -> BeatRoute:
        if self.route == "generate" and self.shot_id is None:
            msg = "generate-routed beats need the shot_id the generative branch renders"
            raise ValueError(msg)
        return self


class ShotRouting(VersionedModel):
    """Owned by the shot router; consumed by ``compile_controls`` (renders only generate-routed
    shots) and ``compose_video`` (interleaves Remotion segments and generated clips)."""

    routing_id: OpaqueId
    deliverable_id: OpaqueId
    story_plan_hash: Sha256Hex
    shot_plan_hash: Sha256Hex
    router: Literal["kind_table"] = "kind_table"
    router_version: str = Field(min_length=1, max_length=32)
    default_route: Route = "render"
    beats: tuple[BeatRoute, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _beats_ordered_and_unique(self) -> ShotRouting:
        orders = [b.order for b in self.beats]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            msg = "routed beats must be in strictly increasing story order"
            raise ValueError(msg)
        ids = [b.beat_id for b in self.beats]
        if len(set(ids)) != len(ids):
            msg = "each beat is routed once"
            raise ValueError(msg)
        return self

    def route_for(self, beat_id: str) -> Route:
        for b in self.beats:
            if b.beat_id == beat_id:
                return b.route
        return self.default_route

    def shot_for(self, beat_id: str) -> str | None:
        for b in self.beats:
            if b.beat_id == beat_id:
                return b.shot_id
        return None

    def generated_shot_ids(self) -> frozenset[str]:
        return frozenset(b.shot_id for b in self.beats if b.route == "generate" and b.shot_id)
