"""Deterministic control-image compiler (16.6): MotionPlan -> per-frame ControlAssets.

Code — never a model — interpolates keyframes into pose skeletons and layout maps. The output is
byte-identical for identical plans: fixed canvas, fixed palette, integer geometry, no antialiasing
randomness, deterministic PNG encoding (no timestamps, fixed compression level).
"""

from __future__ import annotations

import io
import itertools
from collections.abc import Iterator
from dataclasses import dataclass

from PIL import Image, ImageDraw

from content_factory.schemas.base import sha256_hex
from content_factory.schemas.sequences import (
    Box,
    ControlAsset,
    ControlKind,
    Easing,
    MotionPlan,
    Point,
    SkeletonPose,
    SubjectKeyframe,
    TrackedSubject,
)

COMPILER_VERSION = "0.1.0"

# OpenPose-style limb colours would go here for full rigs; the spike uses a fixed palette keyed by
# sorted joint names so colour assignment is stable across runs and machines.
_PALETTE = (
    (255, 0, 0),
    (255, 85, 0),
    (255, 170, 0),
    (255, 255, 0),
    (170, 255, 0),
    (85, 255, 0),
    (0, 255, 0),
    (0, 255, 85),
    (0, 255, 170),
    (0, 255, 255),
    (0, 170, 255),
    (0, 85, 255),
    (0, 0, 255),
    (85, 0, 255),
    (170, 0, 255),
    (255, 0, 255),
    (255, 0, 170),
    (255, 0, 85),
)


@dataclass(frozen=True)
class CompiledFrame:
    asset: ControlAsset
    png: bytes


def ease(t: float, kind: Easing) -> float:
    t = min(max(t, 0.0), 1.0)
    if kind == Easing.linear:
        return t
    if kind == Easing.ease_in:
        return t * t
    if kind == Easing.ease_out:
        return 1.0 - (1.0 - t) * (1.0 - t)
    # ease_in_out (smoothstep)
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _bracket(subject: TrackedSubject, frame: int) -> tuple[SubjectKeyframe, SubjectKeyframe, float]:
    """Return (previous keyframe, next keyframe, eased t) for a frame index."""
    kfs = subject.keyframes
    if frame <= kfs[0].frame_index:
        return kfs[0], kfs[0], 0.0
    if frame >= kfs[-1].frame_index:
        return kfs[-1], kfs[-1], 0.0
    for prev, nxt in itertools.pairwise(kfs):
        if prev.frame_index <= frame < nxt.frame_index:
            span = nxt.frame_index - prev.frame_index
            raw = (frame - prev.frame_index) / span
            return prev, nxt, ease(raw, prev.easing_to_next)
    msg = "unreachable: keyframes are validated as strictly increasing"
    raise AssertionError(msg)


def interpolate_box(a: Box | None, b: Box | None, t: float) -> Box | None:
    if a is None or b is None:
        return a or b
    return Box(
        x=_lerp(a.x, b.x, t), y=_lerp(a.y, b.y, t), w=_lerp(a.w, b.w, t), h=_lerp(a.h, b.h, t)
    )


def interpolate_pose(
    a: SkeletonPose | None, b: SkeletonPose | None, t: float
) -> SkeletonPose | None:
    if a is None or b is None:
        return a or b
    joints: dict[str, Point] = {}
    for name in sorted(set(a.joints) | set(b.joints)):
        pa, pb = a.joints.get(name), b.joints.get(name)
        if pa is None or pb is None:
            joints[name] = pa or pb  # type: ignore[assignment]
        else:
            joints[name] = Point(x=_lerp(pa.x, pb.x, t), y=_lerp(pa.y, pb.y, t))
    bones = tuple(sorted(set(a.bones) | set(b.bones)))
    return SkeletonPose(joints=joints, bones=bones)


def _px(v: float, size: int) -> int:
    # Round half-up on a fixed grid: identical on every platform (no float formatting involved).
    return int(v * (size - 1) + 0.5)


def _encode_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    # No metadata chunks (no tIME/tEXt), fixed compression: byte-identical output for equal pixels.
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def render_pose_frame(plan: MotionPlan, frame: int) -> Image.Image:
    img = Image.new("RGB", (plan.canvas_width, plan.canvas_height), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    stroke = max(2, plan.canvas_height // 144)
    radius = max(3, plan.canvas_height // 96)
    for subject in plan.subjects:
        prev, nxt, t = _bracket(subject, frame)
        pose = interpolate_pose(prev.pose, nxt.pose, t)
        if pose is None:
            continue
        names = sorted(pose.joints)
        colour_of = {n: _PALETTE[i % len(_PALETTE)] for i, n in enumerate(names)}
        for a, b in pose.bones:
            pa, pb = pose.joints[a], pose.joints[b]
            draw.line(
                [
                    (_px(pa.x, plan.canvas_width), _px(pa.y, plan.canvas_height)),
                    (_px(pb.x, plan.canvas_width), _px(pb.y, plan.canvas_height)),
                ],
                fill=colour_of[a],
                width=stroke,
            )
        for name in names:
            p = pose.joints[name]
            cx, cy = _px(p.x, plan.canvas_width), _px(p.y, plan.canvas_height)
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=colour_of[name])
    return img


def render_layout_frame(plan: MotionPlan, frame: int) -> Image.Image:
    img = Image.new("RGB", (plan.canvas_width, plan.canvas_height), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, subject in enumerate(plan.subjects):
        prev, nxt, t = _bracket(subject, frame)
        box = interpolate_box(prev.layout, nxt.layout, t)
        if box is None:
            continue
        colour = _PALETTE[(i * 5) % len(_PALETTE)]
        x0, y0 = _px(box.x, plan.canvas_width), _px(box.y, plan.canvas_height)
        x1, y1 = _px(box.x + box.w, plan.canvas_width), _px(box.y + box.h, plan.canvas_height)
        draw.rectangle(
            [x0, y0, min(x1, plan.canvas_width - 1), min(y1, plan.canvas_height - 1)], fill=colour
        )
    return img


def compile_control_assets(plan: MotionPlan, kind: ControlKind) -> Iterator[CompiledFrame]:
    plan_hash = plan.content_hash()
    renderer = render_pose_frame if kind == ControlKind.pose_skeleton else render_layout_frame
    for frame in range(plan.frame_count):
        png = _encode_png(renderer(plan, frame))
        yield CompiledFrame(
            asset=ControlAsset(
                kind=kind,
                frame_index=frame,
                width=plan.canvas_width,
                height=plan.canvas_height,
                png_sha256=sha256_hex(png),
                motion_plan_hash=plan_hash,
                compiler_version=COMPILER_VERSION,
            ),
            png=png,
        )
