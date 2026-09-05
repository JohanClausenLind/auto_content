"""Phase-0 spike: MotionPlan -> per-frame control images, byte-identical on rerun."""

from __future__ import annotations

import io
from typing import cast

import pytest
from PIL import Image

from content_factory.schemas.fixtures import sample_motion_plan
from content_factory.schemas.sequences import ControlKind, Point, SkeletonPose
from content_factory.sequences.control_compile import (
    BUILTIN_KINDS,
    OPENPOSE18,
    OPENPOSE18_LIMBS,
    _encode_png,
    compile_control_assets,
    render_openpose_pose,
)


def _all(kind: ControlKind) -> list[tuple[str, bytes]]:
    return [(f.asset.png_sha256, f.png) for f in compile_control_assets(sample_motion_plan(), kind)]


def test_pose_and_layout_are_byte_identical_on_rerun() -> None:
    for kind in sorted(BUILTIN_KINDS):
        first = _all(kind)
        second = _all(kind)
        assert [h for h, _ in first] == [h for h, _ in second]
        assert [p for _, p in first] == [p for _, p in second]
        assert len(first) == 8


def test_frames_move_between_keyframes_and_hold_at_ends() -> None:
    frames = list(compile_control_assets(sample_motion_plan(), ControlKind.pose_skeleton))
    hashes = [f.asset.png_sha256 for f in frames]
    # Every in-between frame differs from its neighbours (the hands move each frame).
    assert len(set(hashes)) == len(hashes)
    # Geometry check: the left wrist x moves monotonically toward the centre.
    xs = []
    for f in frames:
        img = Image.open(io.BytesIO(f.png)).convert("RGB")
        w, h = img.size
        row = int(0.60 * (h - 1) + 0.5)
        lit = [x for x in range(w // 2) if img.getpixel((x, row)) != (0, 0, 0)]
        xs.append(min(lit))
    assert xs == sorted(xs)
    assert xs[0] < xs[-1]


def test_control_asset_records_plan_hash_and_compiler_version() -> None:
    plan = sample_motion_plan()
    frame = next(compile_control_assets(plan, ControlKind.layout_boxes))
    assert frame.asset.motion_plan_hash == plan.content_hash()
    assert frame.asset.compiler_version == "0.1.0"
    assert frame.asset.width == 1024 and frame.asset.height == 576


def test_builtin_compiler_refuses_blender_only_kinds() -> None:
    assert BUILTIN_KINDS == {ControlKind.pose_skeleton, ControlKind.layout_boxes}
    for kind in ControlKind:
        if kind in BUILTIN_KINDS:
            continue
        with pytest.raises(ValueError, match=r"controls\.compiler=blender"):
            next(compile_control_assets(sample_motion_plan(), kind))


def _standing_pose() -> SkeletonPose:
    # A front-facing figure: image x grows to the right, so the right side of the body is on the
    # viewer's left (x < 0.5), as in OpenPose annotations.
    j = {
        "nose": (0.50, 0.10),
        "neck": (0.50, 0.20),
        "r_shoulder": (0.42, 0.21),
        "r_elbow": (0.38, 0.35),
        "r_wrist": (0.36, 0.48),
        "l_shoulder": (0.58, 0.21),
        "l_elbow": (0.62, 0.35),
        "l_wrist": (0.64, 0.48),
        "r_hip": (0.45, 0.50),
        "r_knee": (0.45, 0.70),
        "r_ankle": (0.45, 0.90),
        "l_hip": (0.55, 0.50),
        "l_knee": (0.55, 0.70),
        "l_ankle": (0.55, 0.90),
        "r_eye": (0.48, 0.08),
        "l_eye": (0.52, 0.08),
        "r_ear": (0.46, 0.09),
        "l_ear": (0.54, 0.09),
        "l_thumb": (0.66, 0.50),  # not an OpenPose-18 joint: must be ignored
    }
    return SkeletonPose(
        joints={k: Point(x=x, y=y) for k, (x, y) in j.items()},
        bones=(*OPENPOSE18_LIMBS, ("l_wrist", "l_thumb")),
    )


def test_openpose_render_is_byte_identical_and_uses_limb_colours() -> None:
    pose = _standing_pose()
    a = _encode_png(render_openpose_pose(pose, 256, 512))
    b = _encode_png(render_openpose_pose(pose, 256, 512))
    assert a == b
    img = Image.open(io.BytesIO(a)).convert("RGB")
    assert img.size == (256, 512)

    def _colours_around(x: int, y: int, reach: int = 4) -> set[tuple[int, int, int]]:
        """Colours in a small neighbourhood. The ink is now proportional to the figure's size, so
        probing one exact pixel would assert a stroke width rather than a colour."""
        return {
            cast("tuple[int, int, int]", img.getpixel((x + dx, y + dy)))
            for dx in range(-reach, reach + 1)
            for dy in range(-reach, reach + 1)
        }

    # Joint 0 (nose) is drawn in the first palette colour, pure red.
    assert (255, 0, 0) in _colours_around(int(0.50 * 255 + 0.5), int(0.10 * 511 + 0.5))
    # Limb 8 (r_knee -> r_ankle) is drawn in the ninth palette colour. Sampled in a small
    # neighbourhood of the midpoint rather than one exact pixel: the stroke is now proportional to
    # the figure's size, so a single-pixel probe tests the stroke width instead of the colour.
    assert (0, 255, 170) in _colours_around(int(0.45 * 255 + 0.5), int(0.80 * 511 + 0.5))
    assert len(OPENPOSE18) == 18 and len(OPENPOSE18_LIMBS) == 17


def test_openpose_render_skips_missing_joints() -> None:
    partial = SkeletonPose(
        joints={"neck": Point(x=0.5, y=0.2), "l_shoulder": Point(x=0.6, y=0.2)},
        bones=(("neck", "l_shoulder"),),
    )
    img = render_openpose_pose(partial, 128, 128)
    assert img.getpixel((int(0.55 * 127 + 0.5), int(0.2 * 127 + 0.5))) == (255, 85, 0)
    assert img.getpixel((5, 120)) == (0, 0, 0)
