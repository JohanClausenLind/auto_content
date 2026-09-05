"""Sampling cf.clip.v2 at a render frame.

The properties that matter: an interpolated direction is still a unit vector (otherwise a limb
changes length mid-clip), yaw takes the short way round, the ground offset is applied once, and
two actors sampled at the same render frame keep the contact the capture had.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from mocap.sample import _lerp_angle, _nlerp, actor_ids, clip_position, sample_actor

CLIPS = Path("/mnt/fast/models/blender-assets/clips")
clips = pytest.mark.skipif(
    not (CLIPS / "cmu_22_23_08.json").is_file(), reason="clip library not baked on this host"
)

SYNTHETIC = {
    "schema": "cf.clip.v2",
    "name": "synthetic",
    "fps": 24,
    "loop": False,
    "frame_count": 3,
    "ground_offset": 0.25,
    "origin": [0.5, 0.0, 0.0],
    "up_axis": "z",
    "units": "m",
    "segments": ["lfemur"],
    "actors": [
        {
            "actor_id": "a",
            "subject": "x",
            "trial": "01",
            "frames": [
                {
                    "source_frame": 0,
                    "directions": {"lfemur": [0.0, 0.0, -1.0]},
                    "root_translation": [0.0, 0.0, 1.0],
                    "root_yaw_deg": -170.0,
                },
                {
                    "source_frame": 5,
                    "directions": {"lfemur": [1.0, 0.0, 0.0]},
                    "root_translation": [1.0, 0.0, 1.0],
                    "root_yaw_deg": 170.0,
                },
                {
                    "source_frame": 10,
                    "directions": {"lfemur": [0.0, 0.0, 1.0]},
                    "root_translation": [2.0, 0.0, 1.0],
                    "root_yaw_deg": 0.0,
                },
            ],
        }
    ],
}


def test_nlerp_stays_on_the_unit_sphere() -> None:
    a, b = [0.0, 0.0, -1.0], [1.0, 0.0, 0.0]
    for t in (0.0, 0.1, 0.25, 0.5, 0.9, 1.0):
        v = _nlerp(a, b, t)
        assert float(np.linalg.norm(v)) == pytest.approx(1.0, abs=1e-12)
    mid = _nlerp(a, b, 0.5)
    # Halfway along the arc between two perpendicular vectors is 45 degrees from each.
    assert math.degrees(math.acos(np.dot(mid, a))) == pytest.approx(45.0, abs=1e-6)
    assert math.degrees(math.acos(np.dot(mid, b))) == pytest.approx(45.0, abs=1e-6)


def test_nlerp_handles_near_parallel_and_near_opposite() -> None:
    a = [0.0, 0.0, 1.0]
    assert _nlerp(a, a, 0.5) == pytest.approx(a)
    almost = [1e-9, 0.0, 1.0]
    v = _nlerp(a, almost, 0.5)
    assert float(np.linalg.norm(v)) == pytest.approx(1.0, abs=1e-12)
    v = _nlerp(a, [0.0, 0.0, -1.0], 0.5)
    assert float(np.linalg.norm(v)) == pytest.approx(1.0, abs=1e-12)


def test_lerp_angle_takes_the_short_way() -> None:
    assert _lerp_angle(-179.0, 179.0, 0.5) == pytest.approx(-180.0)
    assert _lerp_angle(10.0, 20.0, 0.5) == pytest.approx(15.0)
    assert _lerp_angle(350.0, 10.0, 0.5) == pytest.approx(360.0)
    assert _lerp_angle(0.0, 90.0, 0.0) == pytest.approx(0.0)


def test_clip_position_holds_the_last_frame_without_loop() -> None:
    assert clip_position(SYNTHETIC, 0, fps=24) == (0, 1, 0.0)
    assert clip_position(SYNTHETIC, 2, fps=24) == (2, 2, 0.0)
    assert clip_position(SYNTHETIC, 99, fps=24) == (2, 2, 0.0)


def test_clip_position_wraps_with_loop() -> None:
    assert clip_position(SYNTHETIC, 3, fps=24, loop=True) == (0, 1, 0.0)
    assert clip_position(SYNTHETIC, 4, fps=24, loop=True) == (1, 2, 0.0)
    i0, i1, _blend = clip_position(SYNTHETIC, 2, fps=24, loop=True)
    assert (i0, i1) == (2, 0)


def test_render_fps_above_clip_fps_interpolates() -> None:
    i0, i1, blend = clip_position(SYNTHETIC, 1, fps=48)
    assert (i0, i1) == (0, 1)
    assert blend == pytest.approx(0.5)


def test_speed_and_offset_shift_the_read_head() -> None:
    assert clip_position(SYNTHETIC, 1, fps=24, speed=2.0) == (2, 2, 0.0)
    assert clip_position(SYNTHETIC, 0, fps=24, offset_frames=1) == (1, 2, 0.0)


def test_root_offset_is_measured_from_the_clip_origin() -> None:
    s = sample_actor(SYNTHETIC, "a", 0, fps=24)
    assert s["root_translation"][0] == pytest.approx(0.0)
    assert s["root_offset"][0] == pytest.approx(-0.5)
    assert s["root_offset"][2] == pytest.approx(1.25)


def test_ground_offset_is_applied_once() -> None:
    s = sample_actor(SYNTHETIC, "a", 0, fps=24)
    assert s["root_translation"][2] == pytest.approx(1.0 + 0.25)
    s = sample_actor(SYNTHETIC, "a", 1, fps=48)
    assert s["root_translation"][2] == pytest.approx(1.0 + 0.25)


def test_interpolated_sample_is_unit_and_between() -> None:
    s = sample_actor(SYNTHETIC, "a", 1, fps=48)
    assert s["clip_frames"][2] == pytest.approx(0.5)
    v = s["directions"]["lfemur"]
    assert float(np.linalg.norm(v)) == pytest.approx(1.0, abs=1e-12)
    assert s["root_translation"][0] == pytest.approx(0.5)
    assert s["root_yaw_deg"] == pytest.approx(-180.0)


def test_unknown_actor_is_a_clear_error() -> None:
    with pytest.raises(KeyError, match="has no actor"):
        sample_actor(SYNTHETIC, "nobody", 0, fps=24)


@clips
def test_real_clip_directions_stay_unit_at_every_render_frame() -> None:
    clip = json.loads((CLIPS / "cmu_22_23_08.json").read_text())
    worst = 0.0
    for actor in actor_ids(clip):
        for frame in range(0, clip["frame_count"] * 2):
            s = sample_actor(clip, actor, frame, fps=48)
            for v in s["directions"].values():
                worst = max(worst, abs(float(np.linalg.norm(v)) - 1.0))
    assert worst < 1e-4, worst


@clips
def test_actors_sampled_at_the_same_frame_keep_their_contact() -> None:
    clip = json.loads((CLIPS / "cmu_22_23_08.json").read_text())
    gaps = []
    for frame in range(clip["frame_count"]):
        a = sample_actor(clip, "a", frame, fps=24)
        b = sample_actor(clip, "b", frame, fps=24)
        gaps.append(
            float(np.linalg.norm(np.array(a["root_translation"]) - np.array(b["root_translation"])))
        )
    assert 0.3 < min(gaps), min(gaps)
    assert max(gaps) < 1.5, max(gaps)
    assert max(gaps) - min(gaps) < 0.5, "separation should be steady while holding hands"


@clips
def test_both_actors_share_one_ground_plane() -> None:
    clip = json.loads((CLIPS / "cmu_22_23_04.json").read_text())
    a = sample_actor(clip, "a", 0, fps=24)
    b = sample_actor(clip, "b", 0, fps=24)
    assert abs(a["root_translation"][2] - b["root_translation"][2]) < 0.35


@clips
def test_the_offset_form_preserves_the_distance_between_actors() -> None:
    """Both actors subtract the same origin, so their separation is untouched. If the origin were
    per-actor, a two-person clip would silently change how far apart the pair stands."""
    clip = json.loads((CLIPS / "cmu_22_23_08.json").read_text())
    worst = 0.0
    for frame in range(clip["frame_count"]):
        a = sample_actor(clip, "a", frame, fps=24)
        b = sample_actor(clip, "b", frame, fps=24)
        absolute = float(
            np.linalg.norm(np.array(a["root_translation"]) - np.array(b["root_translation"]))
        )
        relative = float(np.linalg.norm(np.array(a["root_offset"]) - np.array(b["root_offset"])))
        worst = max(worst, abs(absolute - relative))
    assert worst < 1e-9, worst


@clips
def test_the_pair_starts_centred_on_the_staging_point() -> None:
    clip = json.loads((CLIPS / "cmu_22_23_08.json").read_text())
    a = sample_actor(clip, "a", 0, fps=24)["root_offset"]
    b = sample_actor(clip, "b", 0, fps=24)["root_offset"]
    assert a[0] + b[0] == pytest.approx(0.0, abs=1e-4)
    assert a[1] + b[1] == pytest.approx(0.0, abs=1e-4)


@clips
def test_locomotion_survives_as_displacement() -> None:
    clip = json.loads((CLIPS / "cmu_22_23_08.json").read_text())
    first = np.array(sample_actor(clip, "a", 0, fps=24)["root_offset"][:2])
    last = np.array(sample_actor(clip, "a", clip["frame_count"] - 1, fps=24)["root_offset"][:2])
    assert float(np.linalg.norm(last - first)) > 1.5, "a walking clip must actually travel"


@clips
def test_a_stationary_clip_barely_travels() -> None:
    clip = json.loads((CLIPS / "cmu_22_23_07.json").read_text())
    xy = [
        np.array(sample_actor(clip, "a", f, fps=24)["root_offset"][:2])
        for f in range(clip["frame_count"])
    ]
    span = float(np.linalg.norm(np.max(xy, axis=0) - np.min(xy, axis=0)))
    assert span < 0.5, span
