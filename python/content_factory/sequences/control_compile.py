"""Deterministic control-image compiler (16.6): MotionPlan -> per-frame ControlAssets.

Code — never a model — interpolates keyframes into pose skeletons and layout maps. The output is
byte-identical for identical plans: fixed canvas, fixed palette, integer geometry, no antialiasing
randomness, deterministic PNG encoding (no timestamps, fixed compression level).
"""

from __future__ import annotations

import io
import itertools
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from content_factory.schemas.base import canonical_dumps, sha256_hex
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
from content_factory.schemas.shots import ControlBundle, ControlEncoding, ControlTrack, SubjectTrack

COMPILER_VERSION = "0.1.0"

# The kinds this code compiler can render from a MotionPlan. Every other ControlKind comes from the
# Blender scene controller (skills/video/blender_scene) and is refused here on purpose.
BUILTIN_KINDS: frozenset[ControlKind] = frozenset(
    {ControlKind.pose_skeleton, ControlKind.layout_boxes}
)

# OpenPose BODY_18 (COCO) joint order. Pose-conditioned video models (Wan-Animate, DWPose-trained
# ControlNets) expect this order and the limb colours below, so the Blender skeleton export and
# ``render_openpose_pose`` both use these names.
OPENPOSE18: tuple[str, ...] = (
    "nose",
    "neck",
    "r_shoulder",
    "r_elbow",
    "r_wrist",
    "l_shoulder",
    "l_elbow",
    "l_wrist",
    "r_hip",
    "r_knee",
    "r_ankle",
    "l_hip",
    "l_knee",
    "l_ankle",
    "r_eye",
    "l_eye",
    "r_ear",
    "l_ear",
)
# Limb sequence in the canonical OpenPose drawing order; limb i is drawn in _PALETTE[i].
OPENPOSE18_LIMBS: tuple[tuple[str, str], ...] = (
    ("neck", "r_shoulder"),
    ("neck", "l_shoulder"),
    ("r_shoulder", "r_elbow"),
    ("r_elbow", "r_wrist"),
    ("l_shoulder", "l_elbow"),
    ("l_elbow", "l_wrist"),
    ("neck", "r_hip"),
    ("r_hip", "r_knee"),
    ("r_knee", "r_ankle"),
    ("neck", "l_hip"),
    ("l_hip", "l_knee"),
    ("l_knee", "l_ankle"),
    ("neck", "nose"),
    ("nose", "r_eye"),
    ("r_eye", "r_ear"),
    ("nose", "l_eye"),
    ("l_eye", "l_ear"),
)

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


def encode_png(img: Image.Image) -> bytes:
    """Public alias of the canonical encoder for other compilers (byte-identical output)."""
    return _encode_png(img)


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


def _pose_ink(points, width: int, height: int) -> tuple[int, int]:
    """(stroke, joint radius) in pixels for one figure, proportional to how big it is drawn.

    OpenPose's own renderer sizes its ink to the detected person; ours sized it to the frame, so
    the same skeleton read as a pose in a close shot and as a scatter of baubles in a wide one.
    """
    xs = [p.x * width for p in points]
    ys = [p.y * height for p in points]
    if len(xs) < 2:
        return max(2, height // 144), max(3, height // 96)
    extent = max(max(xs) - min(xs), max(ys) - min(ys))
    if extent < 8:  # degenerate projection: a figure seen end-on, or a bad frame
        return 2, 2
    return max(2, round(extent * 0.022)), max(2, round(extent * 0.032))


def render_openpose_pose(pose: SkeletonPose, width: int, height: int) -> Image.Image:
    """Draw one pose with the canonical OpenPose-18 joint colours and limb order.

    Joints named outside OPENPOSE18 are skipped; ``pose.bones`` that are not canonical limbs (e.g.
    hand bones) are drawn in the colour of their first joint when both ends are known joints.
    Points are normalised with y down. Output is byte-identical for identical input.
    """
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    joint_colour = {name: _PALETTE[i] for i, name in enumerate(OPENPOSE18)}
    known = {n: p for n, p in pose.joints.items() if n in joint_colour}
    # Scale the ink to the *person*, not the canvas. A fixed canvas-relative stroke puts 12 px
    # joint dots on a figure 110 px tall — proportionally a head-sized blob at every joint — and
    # the image model draws them as physical objects: coloured baubles beside a small figure,
    # curved tubes beside a larger one. Sized against the pose's own extent they read as a pose.
    stroke, radius = _pose_ink(known.values(), width, height)

    def _line(a: str, b: str, colour: tuple[int, int, int]) -> None:
        pa, pb = known[a], known[b]
        draw.line(
            [(_px(pa.x, width), _px(pa.y, height)), (_px(pb.x, width), _px(pb.y, height))],
            fill=colour,
            width=stroke,
        )

    for i, (a, b) in enumerate(OPENPOSE18_LIMBS):
        if a in known and b in known:
            _line(a, b, _PALETTE[i])
    canonical = set(OPENPOSE18_LIMBS)
    for a, b in pose.bones:
        if (a, b) in canonical or (b, a) in canonical:
            continue
        if a in known and b in known:
            _line(a, b, joint_colour[a])
    for name in OPENPOSE18:
        p = known.get(name)
        if p is None:
            continue
        cx, cy = _px(p.x, width), _px(p.y, height)
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=joint_colour[name])
    return img


def render_openpose_frame(poses: Iterable[SkeletonPose], width: int, height: int) -> Image.Image:
    """All people of one frame on a single canvas (OpenPose-18 colours), for pose videos."""
    img = Image.new("RGB", (width, height), (0, 0, 0))
    for pose in poses:
        layer = render_openpose_pose(pose, width, height)
        mask = layer.convert("L").point(lambda v: 255 if v else 0)
        img.paste(layer, (0, 0), mask)
    return img


def render_layout_boxes(boxes: Iterable[Box | None], width: int, height: int) -> Image.Image:
    """Filled layout rectangles, one palette colour per subject index (same look as the MotionPlan
    layout pass), for the Blender compiler's ``layout_boxes`` track."""
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, box in enumerate(boxes):
        if box is None:
            continue
        colour = _PALETTE[(i * 5) % len(_PALETTE)]
        x0, y0 = _px(box.x, width), _px(box.y, height)
        x1, y1 = _px(box.x + box.w, width), _px(box.y + box.h, height)
        draw.rectangle([x0, y0, min(x1, width - 1), min(y1, height - 1)], fill=colour)
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
    if kind not in BUILTIN_KINDS:
        msg = f"the builtin compiler cannot render {kind.value}; use controls.compiler=blender"
        raise ValueError(msg)
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


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _short(*parts: str) -> str:
    import hashlib

    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def compile_bundle_from_motion_plan(
    plan: MotionPlan,
    out_dir: Path,
    *,
    kinds: Iterable[ControlKind] = BUILTIN_KINDS,
    shot_id: str | None = None,
    anchor_frames: tuple[int, ...] | None = None,
) -> ControlBundle:
    """Compile a MotionPlan into the shared ``ControlBundle`` layout on disk:

    ``<out_dir>/<kind>/frames/NNNN.png`` + ``NNNN.done.json`` per frame, plus ``bundle.json``.
    Byte-identical on rerun (same PNG encoder, canonical JSON, sorted kinds).

    ``shot_id`` stands the bundle in for a planned shot (the hybrid workflow's offline path: the
    2D plan supplies the passes, the shot supplies the identity downstream stages key on);
    ``anchor_frames`` overrides the plan's keyframe indices (clamped to the plan's frame range)."""
    plan_hash = plan.content_hash()
    bundle_shot_id = shot_id or plan.sequence_id
    tracks: list[ControlTrack] = []
    for kind in sorted(kinds, key=lambda k: k.value):
        assets: list[ControlAsset] = []
        for frame in compile_control_assets(plan, kind):
            asset = ControlAsset(
                **{
                    **frame.asset.model_dump(),
                    "compiler": "motion_plan",
                    "shot_id": bundle_shot_id,
                }
            )
            stem = out_dir / kind.value / "frames" / f"{asset.frame_index:04d}"
            _write_bytes(stem.with_suffix(".png"), frame.png)
            marker = {
                "frame_index": asset.frame_index,
                "input_hash": plan_hash,
                "kind": kind.value,
                "png_sha256": asset.png_sha256,
            }
            _write_bytes(stem.with_suffix(".done.json"), (canonical_dumps(marker) + "\n").encode())
            assets.append(asset)
        tracks.append(ControlTrack(kind=kind, encoding=ControlEncoding.rgb8, frames=tuple(assets)))

    subjects: list[SubjectTrack] = []
    for i, subject in enumerate(plan.subjects):
        layouts: list[Box | None] = []
        poses: list[SkeletonPose | None] = []
        for frame in range(plan.frame_count):
            prev, nxt, t = _bracket(subject, frame)
            layouts.append(interpolate_box(prev.layout, nxt.layout, t))
            poses.append(interpolate_pose(prev.pose, nxt.pose, t))
        subjects.append(
            SubjectTrack(
                subject_id=subject.subject_id,
                label=subject.label,
                segmentation_index=i + 1,
                layouts=tuple(layouts),
                poses=tuple(poses),
            )
        )

    bundle = ControlBundle(
        bundle_id=f"cbd_{plan_hash[:12]}"
        if shot_id is None
        else f"cbd_{_short(plan_hash, shot_id)}",
        shot_id=bundle_shot_id,
        plan_hash=plan_hash,
        compiler="motion_plan",
        compiler_version=COMPILER_VERSION,
        width=plan.canvas_width,
        height=plan.canvas_height,
        frame_count=plan.frame_count,
        fps=24,
        anchor_frames=(
            (tuple(f for f in anchor_frames if 0 <= f < plan.frame_count) or (0,))
            if anchor_frames is not None
            else (0,)
        ),
        tracks=tuple(tracks),
        subjects=tuple(subjects),
    )
    _write_bytes(
        out_dir / "bundle.json",
        (json.dumps(bundle.model_dump(mode="json"), indent=1, sort_keys=True) + "\n").encode(),
    )
    return bundle
