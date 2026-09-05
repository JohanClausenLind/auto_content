from __future__ import annotations

import math

import pytest
from posing import IDENTITY, bone_rotations, sample_clip


def _q(deg: float) -> tuple[float, float, float, float]:
    return (math.cos(math.radians(deg) / 2), 0.0, 0.0, math.sin(math.radians(deg) / 2))


def test_bone_rotations_defaults_to_identity() -> None:
    assert bone_rotations({"bones": {"a": {}, "b": {"rotation_quaternion": [0, 1, 0, 0]}}}) == {
        "a": IDENTITY,
        "b": (0, 1, 0, 0),
    }


def test_clip_sampling_speed_offset_loop() -> None:
    clip = {
        "fps": 24,
        "frames": [
            {"bones": {"arm": {"rotation_quaternion": list(_q(d))}}} for d in (0, 30, 60, 90)
        ],
    }
    assert sample_clip(clip, 0, fps=24)["arm"] == pytest.approx(_q(0))
    assert sample_clip(clip, 2, fps=24)["arm"] == pytest.approx(_q(60), abs=1e-9)
    assert sample_clip(clip, 4, fps=24)["arm"] == pytest.approx(_q(0), abs=1e-9)  # loops
    assert sample_clip(clip, 9, fps=24, loop=False)["arm"] == pytest.approx(
        _q(90), abs=1e-9
    )  # holds
    assert sample_clip(clip, 1, fps=24, speed=0.5)["arm"] == pytest.approx(
        _q(15), abs=1e-9
    )  # slerp half-way
    assert sample_clip(clip, 0, fps=24, offset_frames=3)["arm"] == pytest.approx(_q(90), abs=1e-9)
    assert sample_clip(clip, 1, fps=48)["arm"] == pytest.approx(
        _q(15), abs=1e-9
    )  # 48 fps output samples half a clip frame
    assert sample_clip({"frames": []}, 0, fps=24) == {}
