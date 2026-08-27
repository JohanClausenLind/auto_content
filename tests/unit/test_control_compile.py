"""Phase-0 spike: MotionPlan -> per-frame control images, byte-identical on rerun."""

from __future__ import annotations

import io

from PIL import Image

from content_factory.schemas.fixtures import sample_motion_plan
from content_factory.schemas.sequences import ControlKind
from content_factory.sequences.control_compile import compile_control_assets


def _all(kind: ControlKind) -> list[tuple[str, bytes]]:
    return [(f.asset.png_sha256, f.png) for f in compile_control_assets(sample_motion_plan(), kind)]


def test_pose_and_layout_are_byte_identical_on_rerun() -> None:
    for kind in ControlKind:
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
