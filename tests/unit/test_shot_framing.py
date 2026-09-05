"""Solving a camera from a clip's own geometry.

This module exists because of two failures that each cost a render. Aiming at chest height instead
of the middle of the subject delivered a measured 0.50 body fraction where 0.78 was solved for, and
cropped the feet. Inheriting the preset planner's camera for a mocap-staged pair let a walking
character pass the near plane, which degenerated the depth pass and killed postprocess on a NaN.
Both are properties, so both are tests.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from content_factory.shots.framing import (
    BODY_WIDTH_MARGIN_M,
    FIGURE_HEIGHT_M,
    achieved_body_fraction,
    body_fraction_for,
    clip_extent,
    group_extent_at,
    solve_framing,
    solve_framing_tracking,
    standing_extent,
)

CLIPS = Path("/mnt/fast/models/blender-assets/clips")
WALKING = "cmu_20_21_02"  # link arms, walk: travels about 2.2 m
STILL = "cmu_22_23_07"  # a hand on a shoulder: barely moves
clips = pytest.mark.skipif(
    not (CLIPS / f"{WALKING}.json").is_file(), reason="clip library not baked on this host"
)


def _clip(actors: list[list[tuple[float, float, float]]], ground: float = 0.0) -> dict:
    """A minimal cf.clip.v2 document with the root path each actor takes."""
    origin = [0.0, 0.0, 0.0]
    return {
        "origin": origin,
        "ground_offset": ground,
        "actors": [
            {
                "actor_id": chr(ord("a") + i),
                "frames": [
                    {"root_translation": list(p), "directions": {}, "root_yaw_deg": 0.0}
                    for p in path
                ],
            }
            for i, path in enumerate(actors)
        ],
    }


def test_extent_of_a_standing_pair_is_their_separation() -> None:
    clip = _clip([[(-0.4, 0.0, 1.0)], [(0.4, 0.0, 1.0)]])
    (cx, cy), radius, top = clip_extent(clip)
    assert (cx, cy) == pytest.approx((0.0, 0.0))
    assert radius == pytest.approx(0.4)
    # A standing hip plus a standing head.
    assert top == pytest.approx(1.0 + FIGURE_HEIGHT_M - 0.98, abs=1e-6)


def test_extent_covers_the_whole_path_not_the_first_frame() -> None:
    """The bug this catches: framing a walking clip as if it stood still."""
    clip = _clip([[(0.0, 0.0, 1.0), (2.0, 0.0, 1.0), (4.0, 0.0, 1.0)]])
    (cx, _cy), radius, _top = clip_extent(clip)
    assert cx == pytest.approx(2.0)
    assert radius == pytest.approx(2.0)


def test_a_kneeling_clip_reports_a_shorter_subject() -> None:
    """Framing for a height nobody occupies pushes the camera too far back."""
    standing = clip_extent(_clip([[(0.0, 0.0, 1.0)]]))[2]
    kneeling = clip_extent(_clip([[(0.0, 0.0, 0.6)]]))[2]
    assert kneeling < standing
    assert standing - kneeling == pytest.approx(0.4, abs=1e-6)


def test_an_empty_clip_falls_back_rather_than_dividing_by_nothing() -> None:
    (cx, cy), radius, top = clip_extent({"actors": []})
    assert (cx, cy, radius) == (0.0, 0.0, 0.0)
    assert top == FIGURE_HEIGHT_M


def test_the_solved_distance_delivers_the_requested_body_fraction() -> None:
    clip = _clip([[(0.0, 0.0, 1.0)]])
    for wanted in (0.4, 0.62, 0.8):
        f = solve_framing(clip, width=1024, height=576, lens_mm=50.0, body_fraction=wanted)
        assert f.predicted_body_fraction == pytest.approx(wanted, abs=0.01)


def test_a_wide_pair_pushes_the_camera_back_instead_of_cropping() -> None:
    near = solve_framing(_clip([[(0.0, 0.0, 1.0)]]), width=1024, height=576, body_fraction=0.8)
    far = solve_framing(
        _clip([[(-3.0, 0.0, 1.0)], [(3.0, 0.0, 1.0)]]),
        width=1024,
        height=576,
        body_fraction=0.8,
    )
    assert far.distance_m > near.distance_m
    # Both people fit: the half-width the sensor sees covers the pair plus its margin. The
    # tolerance is 1 mm because distance_m is reported rounded to four decimals.
    half_width = far.distance_m * 18.0 / far.lens_mm
    assert half_width >= 3.0 + BODY_WIDTH_MARGIN_M - 1e-3
    # Paying for width means the subject is smaller than asked for, which is the honest trade.
    assert far.predicted_body_fraction < 0.8


def test_the_camera_aims_at_the_middle_of_the_subject() -> None:
    """Not at chest height. Aiming at 1.05 m needs 2.1 m of coverage to keep a 1.76 m figure
    whole, so the same distance that predicts 0.78 delivers 0.50 and crops the feet."""
    clip = _clip([[(0.0, 0.0, 1.0)]])
    f = solve_framing(clip, width=1024, height=576, elevation_deg=0.0)
    assert f.look_at[2] == pytest.approx(f.subject_height_m / 2.0, abs=1e-4)


def test_the_camera_sits_at_the_asked_for_angle_and_distance() -> None:
    clip = _clip([[(0.0, 0.0, 1.0)]])
    f = solve_framing(clip, width=1024, height=576, azimuth_deg=90.0, elevation_deg=0.0)
    assert f.position[0] == pytest.approx(0.0, abs=1e-3)
    assert f.position[1] == pytest.approx(f.distance_m, abs=1e-3)
    flat = math.dist(f.position[:2], f.look_at[:2])
    assert flat == pytest.approx(f.distance_m, abs=1e-3)


def test_elevation_raises_the_camera_without_moving_the_aim() -> None:
    clip = _clip([[(0.0, 0.0, 1.0)]])
    low = solve_framing(clip, width=1024, height=576, elevation_deg=0.0)
    high = solve_framing(clip, width=1024, height=576, elevation_deg=30.0)
    assert high.position[2] > low.position[2]
    assert high.look_at == low.look_at


def test_solving_is_pure() -> None:
    clip = _clip([[(0.0, 0.0, 1.0), (1.0, 0.5, 1.02)]])
    a = solve_framing(clip, width=1024, height=576)
    b = solve_framing(clip, width=1024, height=576)
    assert a == b


@clips
def test_a_real_walking_clip_is_framed_further_back_than_a_still_one() -> None:
    walk = json.loads((CLIPS / f"{WALKING}.json").read_text())
    still = json.loads((CLIPS / f"{STILL}.json").read_text())
    _c, walk_radius, _t = clip_extent(walk)
    _c, still_radius, _t = clip_extent(still)
    assert walk_radius > still_radius, "the walking clip should cover more ground"
    a = solve_framing(walk, width=1024, height=576, body_fraction=0.62)
    b = solve_framing(still, width=1024, height=576, body_fraction=0.62)
    assert a.distance_m > b.distance_m


@clips
def test_no_real_clip_puts_the_camera_inside_the_action() -> None:
    """The NaN this module was written for: a character passing the near plane. The camera has to
    stand outside the radius the actors cover, with room to spare."""
    for path in sorted(CLIPS.glob("cmu_*.json")):
        clip = json.loads(path.read_text())
        (cx, cy), radius, _top = clip_extent(clip)
        f = solve_framing(clip, width=1024, height=576, body_fraction=0.62)
        flat = math.dist(f.position[:2], (cx, cy))
        assert flat > radius + 0.5, (
            f"{path.stem}: camera {flat:.2f} m from centre, radius {radius:.2f}"
        )
        assert f.distance_m > 1.0, path.stem


def test_group_extent_at_is_one_instant_not_the_whole_path() -> None:
    """The distinction the tracking camera rests on: where they are now, not everywhere they go."""
    clip = _clip([[(0.0, 0.0, 1.0), (4.0, 0.0, 1.0)]])
    _c, path_radius, _t = clip_extent(clip)
    (cx, _cy), radius, _top = group_extent_at(clip, 0)
    assert path_radius == pytest.approx(2.0)
    assert (cx, radius) == pytest.approx((0.0, 0.0))


def test_group_extent_at_holds_two_bodies_apart() -> None:
    clip = _clip([[(-0.7, 0.0, 1.0)], [(0.7, 0.0, 1.0)]])
    (cx, cy), radius, top = group_extent_at(clip, 0)
    assert (cx, cy) == pytest.approx((0.0, 0.0))
    assert radius == pytest.approx(0.7)
    assert top == pytest.approx(1.0 + FIGURE_HEIGHT_M - 0.98, abs=1e-6)


def test_group_extent_at_can_be_narrowed_to_one_actor() -> None:
    clip = _clip([[(-0.7, 0.0, 1.0)], [(0.7, 0.0, 1.0)]])
    (cx, _cy), radius, _top = group_extent_at(clip, 0, actor_ids=("b",))
    assert (cx, radius) == pytest.approx((0.7, 0.0))


def test_one_actor_needs_no_width_of_their_own() -> None:
    """The invariant a tracking camera rests on: framing one body is a distance problem only, so
    the width push-back must not fire on a solo clip and pull the camera back for nothing."""
    clip = _clip([[(0.0, 0.0, 1.0), (1.0, 2.0, 1.1)], [(3.0, 0.0, 0.9)]])
    for frame in (0, 1, 9):
        centre, radius, top = group_extent_at(clip, frame, actor_ids=("a",))
        assert radius == 0.0
        assert top > 1.0
        assert centre == pytest.approx(
            (
                clip["actors"][0]["frames"][min(frame, 1)]["root_translation"][0],
                clip["actors"][0]["frames"][min(frame, 1)]["root_translation"][1],
            )
        )


def test_group_extent_at_clamps_past_the_end_rather_than_wrapping() -> None:
    clip = _clip([[(0.0, 0.0, 1.0), (5.0, 0.0, 1.0)]])
    (cx, _cy), _r, _t = group_extent_at(clip, 99)
    assert cx == pytest.approx(5.0), "a camera past the end holds on the last pose"


def test_group_extent_at_names_the_actor_a_clip_does_not_have() -> None:
    with pytest.raises(KeyError, match="zz"):
        group_extent_at(_clip([[(0.0, 0.0, 1.0)]]), 0, actor_ids=("zz",))


def test_tracking_returns_one_framing_per_frame_and_holds_the_target() -> None:
    """A travelling subject framed by one path-covering camera shrinks; tracked, it does not."""
    clip = _clip([[(0.0, 0.0, 1.0), (2.0, 0.0, 1.0), (4.0, 0.0, 1.0)]])
    whole = solve_framing(clip, width=576, height=1024, body_fraction=0.62)
    tracked = solve_framing_tracking(clip, (0, 1, 2), width=576, height=1024, body_fraction=0.62)
    assert len(tracked) == 3
    assert whole.predicted_body_fraction < 0.33, "the failure being fixed has to be present"
    for f in tracked:
        assert f.predicted_body_fraction == pytest.approx(0.62, abs=1e-3)
    # The camera moves with the body instead of retreating from all of it.
    assert tracked[0].position[0] < tracked[-1].position[0]
    assert tracked[-1].distance_m < whole.distance_m


def test_tracking_needs_a_frame() -> None:
    with pytest.raises(ValueError, match="at least one frame"):
        solve_framing_tracking(_clip([[(0.0, 0.0, 1.0)]]), (), width=576, height=1024)


def test_the_achieved_fraction_inverts_the_solve() -> None:
    """The verifier and the solver have to agree when handed the same instant."""
    clip = _clip([[(0.0, 0.0, 1.0)]])
    for w, h in ((1024, 576), (576, 1024), (768, 768)):
        f = solve_framing(clip, width=w, height=h, body_fraction=0.55)
        got = achieved_body_fraction(
            clip,
            0,
            lens_mm=f.lens_mm,
            camera_position=f.position,
            width=w,
            height=h,
        )
        assert got == pytest.approx(f.predicted_body_fraction, abs=1e-3)


def test_the_achieved_fraction_refuses_a_camera_inside_the_subject() -> None:
    """A camera standing on the aim point is a broken plan, not a subject filling the frame."""
    clip = _clip([[(0.0, 0.0, 1.0)]])
    aim = (0.0, 0.0, clip_extent(clip)[2] / 2.0)
    assert (
        achieved_body_fraction(clip, 0, lens_mm=50.0, camera_position=aim, width=1024, height=576)
        == 0.0
    )


def test_the_achieved_fraction_reads_elevation_off_the_camera() -> None:
    """A raised camera sees a shorter figure, and the measurement has to agree with the solve."""
    clip = _clip([[(0.0, 0.0, 1.0)]])
    for elevation in (0.0, 6.0, 38.0):
        f = solve_framing(clip, width=1024, height=576, body_fraction=0.55, elevation_deg=elevation)
        got = achieved_body_fraction(
            clip, 0, lens_mm=f.lens_mm, camera_position=f.position, width=1024, height=576
        )
        assert got == pytest.approx(f.predicted_body_fraction, abs=2e-3), elevation


@clips
def test_a_portrait_frame_cannot_hold_a_walking_pair_with_one_camera() -> None:
    """The measurement behind the tracking branch, on the real clip it was found on."""
    clip = json.loads((CLIPS / f"{WALKING}.json").read_text())
    tall = solve_framing(clip, width=576, height=1024, body_fraction=0.62)
    wide = solve_framing(clip, width=1024, height=576, body_fraction=0.62)
    assert wide.predicted_body_fraction == pytest.approx(0.62, abs=0.01)
    assert tall.predicted_body_fraction < 0.33
    mid = clip["frame_count"] // 2
    tracked = solve_framing_tracking(clip, (mid,), width=576, height=1024, body_fraction=0.62)
    assert tall.predicted_body_fraction < 0.33 < tracked[0].predicted_body_fraction


def test_standing_extent_places_a_figure_where_it_was_put() -> None:
    """The clip-free case: a preset shot has characters on the floor and no hip height to read."""
    (cx, cy), radius, top = standing_extent(((0.0, 0.0, 0.0),))
    assert (cx, cy, radius) == (0.0, 0.0, 0.0)
    assert top == pytest.approx(FIGURE_HEIGHT_M)


def test_standing_extent_covers_a_pair_and_a_raised_floor() -> None:
    (cx, cy), radius, top = standing_extent(((-0.8, 0.0, 0.0), (0.8, 0.0, 0.25)))
    assert (cx, cy) == pytest.approx((0.0, 0.0))
    assert radius == pytest.approx(0.8)
    assert top == pytest.approx(0.25 + FIGURE_HEIGHT_M), "a figure on a step is taller"


def test_standing_extent_of_nobody_falls_back_rather_than_dividing_by_nothing() -> None:
    (cx, cy), radius, top = standing_extent(())
    assert (cx, cy, radius) == (0.0, 0.0, 0.0)
    assert top == FIGURE_HEIGHT_M


def test_the_clip_measurement_is_the_clip_free_one_with_a_clip() -> None:
    """One measurement, two ways of getting the subject's extent. They must not drift."""
    clip = _clip([[(0.0, 0.0, 1.0)]])
    _centre, _radius, top = clip_extent(clip)
    camera = (0.0, -5.0, 0.9)
    from_clip = achieved_body_fraction(
        clip, 0, lens_mm=50.0, camera_position=camera, width=1024, height=576
    )
    direct = body_fraction_for(
        (0.0, 0.0), top, lens_mm=50.0, camera_position=camera, width=1024, height=576
    )
    assert from_clip == pytest.approx(direct)
