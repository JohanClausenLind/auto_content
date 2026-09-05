"""The aim retarget: segment directions -> per-bone quaternions on whatever rig is loaded.

The test that justifies the whole cf.clip.v2 format is
``test_directions_are_exact_on_every_character`` together with
``test_baked_quaternions_are_wrong_on_other_characters``. The first shows a direction clip is
exact on all four MPFB characters; the second shows that a quaternion clip baked on one of them is
up to 19 degrees wrong on another. That is the difference between a clip and a clip-for-one-body.

The rest matrices come from ``tests/data/rig_*.json``, dumped by ``mocap/dump_rig.py`` inside
Blender, so these tests need neither Blender nor a GPU.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import numpy as np
import pytest
from mocap.segments import (
    SEGMENT_TO_BONES,
    UNMAPPED_SEGMENTS,
    aim_residual_deg,
    bone_target_map,
    matrix_to_quaternion,
    minimal_rotation,
    solve_aim,
)

DATA = Path(__file__).parent / "data"
CHARACTERS = ("man_01", "man_02", "woman_01", "woman_02")
CLIPS = Path("/mnt/fast/models/blender-assets/clips")
clips = pytest.mark.skipif(
    not (CLIPS / "cmu_22_23_04.json").is_file(), reason="clip library not baked on this host"
)


def rig(name: str) -> list[dict]:
    rigs = json.loads((DATA / f"rig_{name}.json").read_text())
    bones = rigs[f"{name}:rig"]["bones"]
    return [
        {"name": b["name"], "parent": b["parent"], "matrix_local": b["matrix_local"]} for b in bones
    ]


@pytest.fixture(scope="module")
def targets() -> dict[str, np.ndarray]:
    clip = json.loads((CLIPS / "cmu_22_23_04.json").read_text())
    frames = clip["actors"][1]["frames"]
    return bone_target_map(frames[len(frames) // 2]["directions"])


def test_minimal_rotation_takes_a_onto_b() -> None:
    a = np.array([0.0, 1.0, 0.0])
    for b in (
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
        np.array([0.577, 0.577, 0.577]) / np.linalg.norm([0.577, 0.577, 0.577]),
    ):
        r = minimal_rotation(a, b / np.linalg.norm(b))
        assert r @ a == pytest.approx(b / np.linalg.norm(b), abs=1e-9)
        assert float(np.linalg.det(r)) == pytest.approx(1.0)


def test_minimal_rotation_handles_parallel_and_antiparallel() -> None:
    a = np.array([0.0, 1.0, 0.0])
    assert minimal_rotation(a, a) == pytest.approx(np.eye(3))
    r = minimal_rotation(a, -a)
    assert r @ a == pytest.approx(-a, abs=1e-9)
    assert float(np.linalg.det(r)) == pytest.approx(1.0)
    b = np.array([1.0, 0.0, 0.0])
    r = minimal_rotation(b, -b)
    assert r @ b == pytest.approx(-b, abs=1e-9)


def test_matrix_to_quaternion_round_trips_through_every_branch() -> None:
    for angles in ((10, 20, 30), (170, 5, 5), (5, 170, 5), (5, 5, 170), (0, 0, 0)):
        ax, ay, az = (math.radians(v) for v in angles)
        rx = minimal_rotation(
            np.array([0.0, 1.0, 0.0]), np.array([0.0, math.cos(ax), math.sin(ax)])
        )
        r = rx @ minimal_rotation(
            np.array([1.0, 0.0, 0.0]), np.array([math.cos(ay), math.sin(ay), 0.0])
        )
        w, x, y, z = matrix_to_quaternion(r)
        assert w * w + x * x + y * y + z * z == pytest.approx(1.0)
        assert w >= 0.0
        back = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        assert back == pytest.approx(r, abs=1e-9)
        _ = az


def test_segment_map_covers_the_clip_vocabulary() -> None:
    from mocap.clip import SEGMENTS

    mapped = set(SEGMENT_TO_BONES)
    assert mapped | set(UNMAPPED_SEGMENTS) == set(SEGMENTS)
    assert mapped.isdisjoint(UNMAPPED_SEGMENTS)


def test_split_limbs_are_both_aimed_along_one_segment() -> None:
    d = {"lfemur": [0.0, 0.0, -1.0]}
    t = bone_target_map(d)
    assert set(t) == {"upperleg01.L", "upperleg02.L"}
    assert t["upperleg01.L"] == pytest.approx(t["upperleg02.L"])


def test_zero_and_missing_directions_are_skipped() -> None:
    assert bone_target_map({"lfemur": [0.0, 0.0, 0.0]}) == {}
    assert bone_target_map({}) == {}


def test_targets_are_normalised_even_if_the_input_is_not() -> None:
    t = bone_target_map({"lfemur": [0.0, 0.0, -3.0]})
    assert float(np.linalg.norm(t["upperleg01.L"])) == pytest.approx(1.0)


@clips
@pytest.mark.parametrize("who", CHARACTERS)
def test_directions_are_exact_on_every_character(who: str, targets) -> None:
    """The v2 promise: one clip, every body, no per-character bake."""
    bones = rig(who)
    solved = solve_aim(bones, targets)
    residual = aim_residual_deg(bones, targets, solved)
    assert residual, "no bone was aimed"
    assert max(residual.values()) < 0.01, (who, max(residual.items(), key=lambda kv: kv[1]))


@clips
def test_baked_quaternions_are_wrong_on_other_characters(targets) -> None:
    """Why v1 could not carry this. If MPFB ever normalises rest orientations across characters,
    this test fails and the format could be simplified - which is worth knowing."""
    baked = solve_aim(rig("man_01"), targets)
    worst = 0.0
    for who in ("man_02", "woman_01", "woman_02"):
        residual = aim_residual_deg(rig(who), targets, baked)
        worst = max(worst, max(residual.values()))
        assert statistics.mean(residual.values()) > 1.0, who
    assert worst > 10.0, worst


@clips
def test_every_targeted_bone_exists_on_the_rig(targets) -> None:
    names = {b["name"] for b in rig("man_01")}
    for bones in SEGMENT_TO_BONES.values():
        for bone in bones:
            assert bone in names, bone
    assert set(targets) <= names


@clips
def test_untargeted_bones_are_left_at_identity(targets) -> None:
    bones = rig("man_01")
    solved = solve_aim(bones, targets)
    assert len(solved) == len(bones)
    identity = [n for n, q in solved.items() if q == (1.0, 0.0, 0.0, 0.0)]
    assert len(identity) == len(bones) - len(targets)
    assert "jaw" in identity


@clips
def test_the_whole_clip_solves_without_a_singularity() -> None:
    """Every frame of a real clip, both actors: no NaN, no residual blow-up."""
    clip = json.loads((CLIPS / "cmu_22_23_08.json").read_text())
    bones = rig("woman_01")
    worst = 0.0
    for actor in clip["actors"]:
        for frame in actor["frames"]:
            t = bone_target_map(frame["directions"])
            solved = solve_aim(bones, t)
            assert all(math.isfinite(v) for q in solved.values() for v in q)
            worst = max(worst, max(aim_residual_deg(bones, t, solved).values()))
    assert worst < 0.01, worst
