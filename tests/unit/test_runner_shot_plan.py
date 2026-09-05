"""The runner staging: thirty stills of one captured sprint, a different camera on each.

Three properties here each cost a render to learn, so each is a test rather than a comment. The
motion is captured, so the plan must sample thirty distinct clip frames rather than pose one figure
thirty times. The cameras must come all the way round the body and still leave consecutive stills
close enough to read as one place - the first version walked the azimuth by the golden angle, which
covers a circle beautifully and put consecutive cameras a median 13.1 m apart, scoring 0.0 on the
rubric's camera-variety criterion. And the second framing pass must move a camera in the direction
the measurement asks for, because the analytic solve models a standing figure while a sprinter
throws its limbs out.
"""

from __future__ import annotations

import importlib.util
import json
import math
from itertools import pairwise
from pathlib import Path
from statistics import median

import pytest

from content_factory.schemas.shots import SegmentClipPose, ShotPlan

REPO = Path(__file__).resolve().parents[2]
CLIPS = Path("/mnt/fast/models/blender-assets/clips")
CLIP = "cmu_35_18"
clip_baked = pytest.mark.skipif(
    not (CLIPS / f"{CLIP}.json").is_file(), reason="solo run clips not baked on this host"
)


def _generator():
    spec = importlib.util.spec_from_file_location(
        "make_runner_shot_plan", REPO / "scripts" / "make_runner_shot_plan.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def plan() -> dict:
    return _generator().build(CLIP, 30, actor="a", asset="man_01", beat_id="bea_run_take00")


@clip_baked
def test_the_plan_is_a_valid_shot_plan(plan: dict) -> None:
    parsed = ShotPlan.model_validate(plan)
    assert len(parsed.shots) == 30
    assert [s.order for s in parsed.shots] == list(range(30))


@clip_baked
def test_every_still_is_a_different_frame_of_one_captured_run(plan: dict) -> None:
    """Thirty poses of one sprint, not one pose thirty times and not thirty separate takes."""
    poses = [ShotPlan.model_validate(plan).shots[i].characters[0].pose for i in range(30)]
    assert all(isinstance(p, SegmentClipPose) for p in poses)
    segments = [p for p in poses if isinstance(p, SegmentClipPose)]
    assert {p.name for p in segments} == {CLIP}
    assert {p.actor for p in segments} == {"a"}
    assert [p.offset_frames for p in segments] == list(range(30))


@clip_baked
def test_one_character_because_the_clip_has_one_performer(plan: dict) -> None:
    parsed = ShotPlan.model_validate(plan)
    assert {len(s.characters) for s in parsed.shots} == {1}
    doc = json.loads((CLIPS / f"{CLIP}.json").read_text(encoding="utf-8"))
    assert [a["actor_id"] for a in doc["actors"]] == ["a"]


@clip_baked
def test_a_still_is_asked_for_more_frames_than_it_renders(plan: dict) -> None:
    """One rendered frame per shot, held for a second by the hold cut. The extra frame_count is
    what the cut reads as a duration, so it must not become extra renders."""
    parsed = ShotPlan.model_validate(plan)
    for shot in parsed.shots:
        assert shot.anchor_frames == (0,)
        assert shot.render.frames == (0,)
        assert shot.frame_count > 1


@clip_baked
def test_the_cameras_come_all_the_way_round(plan: dict) -> None:
    module = _generator()
    azimuths = sorted((i * module.AZIMUTH_STEP_DEG) % 360.0 for i in range(len(plan["shots"])))
    assert azimuths[0] < 15.0 and azimuths[-1] > 345.0
    quadrants = {int(a // 90) for a in azimuths}
    assert quadrants == {0, 1, 2, 3}


@clip_baked
def test_consecutive_stills_still_read_as_one_place(plan: dict) -> None:
    """The criterion the golden-angle version failed: the rubric plateaus to zero above 6 m."""
    positions = [tuple(s["camera"]["keyframes"][0]["position"]) for s in plan["shots"]]
    deltas = [math.dist(a, b) for a, b in pairwise(positions)]
    assert median(deltas) > 0.2, "and still be different shots"
    assert median(deltas) < 6.0, "the rubric's ceiling"
    assert max(deltas) < 6.0, "no single jump across the set either"


@clip_baked
def test_the_lens_and_the_height_both_move(plan: dict) -> None:
    lenses = {s["camera"]["keyframes"][0]["lens_mm"] for s in plan["shots"]}
    heights = {s["camera"]["keyframes"][0]["position"][2] for s in plan["shots"]}
    assert len(lenses) >= 5
    assert len(heights) > 20


def _measured(tmp_path: Path, shot_id: str, height: float, top: float) -> None:
    layout = tmp_path / shot_id / "layout" / "frames"
    layout.mkdir(parents=True, exist_ok=True)
    (layout / "0000.json").write_text(
        json.dumps({"objects": [{"kind": "character", "box": {"h": height, "y": top}}]})
    )


@clip_baked
def test_the_correction_pass_moves_the_camera_the_way_the_measurement_asks(
    plan: dict, tmp_path: Path
) -> None:
    """A figure measured larger than the target means the camera is too close, so it must move
    away from the aim point, and vice versa. The bug this guards is a sign error, which would
    double the framing error instead of removing it."""
    module = _generator()
    shot = json.loads(json.dumps(plan["shots"][0]))
    target = float(shot["description"].split("framed at ")[1].split(" ")[0])
    keyframe = shot["camera"]["keyframes"][0]
    before = math.dist(keyframe["position"], keyframe["look_at"])

    for measured, expected in ((target * 1.2, "farther"), (target * 0.8, "closer")):
        one = {"shots": [json.loads(json.dumps(shot))]}
        # Centred vertically, so only the size correction is exercised here.
        _measured(tmp_path, one["shots"][0]["shot_id"], measured, (1.0 - measured) / 2.0)
        corrected, stats = module.correct(one, tmp_path)
        after_kf = corrected["shots"][0]["camera"]["keyframes"][0]
        after = math.dist(after_kf["position"], after_kf["look_at"])
        assert stats["corrected"] == 1 and stats["unmeasured"] == 0
        if expected == "farther":
            assert after > before * 1.1
        else:
            assert after < before * 0.9
        assert "corrected from a measured" in corrected["shots"][0]["description"]


@clip_baked
def test_a_subject_sitting_high_in_frame_raises_the_aim(plan: dict, tmp_path: Path) -> None:
    """Image y runs down, so a box whose centre is above the middle needs the aim raised to bring
    it down. Getting this the wrong way round is what clipped a head: the solve already put every
    runner high, and a sign error here would push them out of frame instead of down into it."""
    module = _generator()
    shot = json.loads(json.dumps(plan["shots"][0]))
    target = float(shot["description"].split("framed at ")[1].split(" ")[0])
    before_z = shot["camera"]["keyframes"][0]["look_at"][2]

    high = {"shots": [json.loads(json.dumps(shot))]}
    _measured(tmp_path, high["shots"][0]["shot_id"], target, 0.0)  # jammed against the top
    corrected, stats = module.correct(high, tmp_path)
    assert corrected["shots"][0]["camera"]["keyframes"][0]["look_at"][2] > before_z
    # Box top at 0 with height `target` puts its centre at target/2, so it sits that far above
    # the middle of the frame.
    assert stats["worst_offset_before"] == pytest.approx(0.5 - target / 2.0, abs=1e-6)

    low = {"shots": [json.loads(json.dumps(shot))]}
    _measured(tmp_path, low["shots"][0]["shot_id"], target, 1.0 - target)  # against the bottom
    corrected, _ = module.correct(low, tmp_path)
    assert corrected["shots"][0]["camera"]["keyframes"][0]["look_at"][2] < before_z


@clip_baked
def test_a_second_correction_composes_onto_the_first(plan: dict, tmp_path: Path) -> None:
    """Raising the camera to centre the subject also raises the elevation, which shrinks it again,
    so the correction is a fixed-point iteration. A second pass that rebuilt from the analytic
    camera would throw the first pass away."""
    module = _generator()
    first = {"shots": [json.loads(json.dumps(plan["shots"][0]))]}
    shot_id = first["shots"][0]["shot_id"]
    target = float(first["shots"][0]["description"].split("framed at ")[1].split(" ")[0])
    _measured(tmp_path, shot_id, target * 1.2, (1.0 - target * 1.2) / 2.0)
    once, _ = module.correct(first, tmp_path)
    after_once = list(once["shots"][0]["camera"]["keyframes"][0]["position"])

    _measured(tmp_path, shot_id, target * 1.1, (1.0 - target * 1.1) / 2.0)
    twice, _ = module.correct(json.loads(json.dumps(once)), tmp_path)
    after_twice = twice["shots"][0]["camera"]["keyframes"][0]["position"]
    look = twice["shots"][0]["camera"]["keyframes"][0]["look_at"]
    assert math.dist(after_twice, look) > math.dist(after_once, look)
    # One record of where it started, not a growing history.
    assert twice["shots"][0]["description"].count("[corrected") == 1


@clip_baked
def test_an_unmeasured_shot_keeps_its_analytic_camera(plan: dict, tmp_path: Path) -> None:
    one = {"shots": [json.loads(json.dumps(plan["shots"][0]))]}
    before = list(one["shots"][0]["camera"]["keyframes"][0]["position"])
    corrected, stats = _generator().correct(one, tmp_path)
    assert stats == {
        "corrected": 0,
        "unmeasured": 1,
        "worst_before": 0.0,
        "worst_after": 0.0,
    }
    assert corrected["shots"][0]["camera"]["keyframes"][0]["position"] == before


@clip_baked
def test_asking_for_more_stills_than_the_clip_has_frames_is_refused() -> None:
    """Sampling a 35-frame clip 60 times would repeat poses and call them different frames."""
    with pytest.raises(SystemExit, match="Sampling it"):
        _generator().build(CLIP, 60, actor="a", asset="man_01", beat_id="bea_run_take00")


@clip_baked
def test_the_shipped_fixture_is_the_generator_plus_its_correction_pass(plan: dict) -> None:
    """A full comparison is the wrong assertion: the shipped plan is the output of two passes, and
    the second one needs a rendered controls directory the test does not have. So this pins
    everything the correction does not touch - which shots there are and what drives them - and
    checks the cameras were in fact corrected rather than shipped straight from the solve."""
    shipped = json.loads((REPO / "fixtures" / "shots" / "runner_mocap.json").read_text("utf-8"))
    parsed = ShotPlan.model_validate(shipped)
    assert [s["shot_id"] for s in shipped["shots"]] == [s["shot_id"] for s in plan["shots"]]
    poses = [s.characters[0].pose for s in parsed.shots]
    assert all(isinstance(p, SegmentClipPose) for p in poses)
    assert [p.offset_frames for p in poses if isinstance(p, SegmentClipPose)] == list(range(30))
    assert all("corrected from a measured" in (s.description or "") for s in parsed.shots)
    positions = [tuple(s["camera"]["keyframes"][0]["position"]) for s in shipped["shots"]]
    assert median(math.dist(a, b) for a, b in pairwise(positions)) < 6.0
