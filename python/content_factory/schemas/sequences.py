"""Consistent image sequences (16.6): MotionPlan, ControlAsset, GenerationLock."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, SemVer, Sha256Hex, VersionedModel


class Easing(StrEnum):
    linear = "linear"
    ease_in = "ease_in"
    ease_out = "ease_out"
    ease_in_out = "ease_in_out"


class Point(SchemaModel):
    """Normalized coordinates in [0, 1] relative to the frame."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class Box(SchemaModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class SkeletonPose(SchemaModel):
    """A named subset of an OpenPose-style body/hand rig, all points normalized."""

    joints: dict[str, Point]
    bones: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def _bones_reference_joints(self) -> SkeletonPose:
        for a, b in self.bones:
            if a not in self.joints or b not in self.joints:
                msg = f"bone ({a}, {b}) references an unknown joint"
                raise ValueError(msg)
        return self


class SubjectKeyframe(SchemaModel):
    frame_index: int = Field(ge=0)
    layout: Box | None = None
    pose: SkeletonPose | None = None
    easing_to_next: Easing = Easing.ease_in_out


class TrackedSubject(SchemaModel):
    subject_id: OpaqueId
    label: str = Field(min_length=1, max_length=80)
    keyframes: tuple[SubjectKeyframe, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _monotonic_keyframes(self) -> TrackedSubject:
        idx = [k.frame_index for k in self.keyframes]
        if idx != sorted(set(idx)):
            msg = "keyframes must be strictly increasing by frame_index"
            raise ValueError(msg)
        return self


class MotionPlan(VersionedModel):
    plan_id: OpaqueId
    sequence_id: OpaqueId
    frame_count: int = Field(ge=1, le=600)
    canvas_width: int = Field(ge=64, le=8192)
    canvas_height: int = Field(ge=64, le=8192)
    subjects: tuple[TrackedSubject, ...] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _keyframes_within_range(self) -> MotionPlan:
        for s in self.subjects:
            for k in s.keyframes:
                if k.frame_index >= self.frame_count:
                    msg = f"subject {s.subject_id} keyframe {k.frame_index} >= frame_count"
                    raise ValueError(msg)
        return self


class ControlKind(StrEnum):
    """Per-frame control signals. The first two are compiled by code from a MotionPlan; the rest
    are rendered by the Blender scene controller (skills/video/blender_scene) from a ShotSpec."""

    pose_skeleton = "pose_skeleton"
    layout_boxes = "layout_boxes"
    rough_rgb = "rough_rgb"
    depth = "depth"
    depth16 = "depth16"
    depth_exr = "depth_exr"
    normals = "normals"
    segmentation = "segmentation"
    canny = "canny"


class ControlAsset(SchemaModel):
    """One compiled per-frame control image plus provenance (deterministic: same plan, same bytes)."""  # noqa: E501

    kind: ControlKind
    frame_index: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    png_sha256: Sha256Hex
    motion_plan_hash: Sha256Hex
    """Content hash of the plan that produced the asset: a MotionPlan or a ShotSpec."""
    compiler_version: SemVer
    compiler: Literal["motion_plan", "blender"] = "motion_plan"
    shot_id: OpaqueId | None = None


class GenerationLock(SchemaModel):
    """Everything held constant across frames of a sequence; one declared delta per frame."""

    workflow_package_id: str = Field(min_length=1)
    workflow_package_version: SemVer
    model_revision: str = Field(min_length=1)
    width: int = Field(ge=64)
    height: int = Field(ge=64)
    seed: int = Field(ge=0)
    sampler: str = Field(min_length=1)
    steps: int = Field(ge=1, le=200)
    guidance: float = Field(ge=0)
    style_prompt: str
    camera_prompt: str
    lighting_prompt: str
    background_prompt: str
    reference_asset_sha256: Sha256Hex


class FrameDelta(SchemaModel):
    """The single allowed change for a frame, expressed for the edit-instruction compiler."""

    kind: Literal["move_subject", "pose_change", "expression_change", "none"]
    subject_label: str | None = None
    instruction: str = Field(min_length=1, max_length=400)


class FrameSpec(SchemaModel):
    frame_index: int = Field(ge=0)
    lock: GenerationLock
    control: ControlAsset | None = None
    delta: FrameDelta
    reference: Literal["anchor"] | OpaqueId = "anchor"  # hub-and-spoke: default anchor
