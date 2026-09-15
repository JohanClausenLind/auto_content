"""The scenario generator: every film it emits is a valid contract that frames its own subjects."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

from content_factory.schemas.scenes import StoryPlan
from content_factory.schemas.shots import ShotPlan

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "make_story_fixtures", REPO_ROOT / "scripts" / "make_story_fixtures.py"
)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
sys.modules["make_story_fixtures"] = gen
_spec.loader.exec_module(gen)

SCENARIOS = {s.slug: s for s in gen.SCENARIOS}
# The two-handers stage a pair facing each other under an overhead camera; the runner stages one
# figure under an orbiting camera.
TWO_HANDERS = {slug: s for slug, s in SCENARIOS.items() if s.camera_program == "topdown"}
ORBITERS = {slug: s for slug, s in SCENARIOS.items() if s.camera_program == "orbit"}


@pytest.mark.parametrize("slug", sorted(SCENARIOS))
def test_every_scenario_builds_valid_contracts(slug: str) -> None:
    scenario = SCENARIOS[slug]
    plan = gen.build_shots(scenario)
    story = gen.build_story(scenario)
    assert len(plan.shots) == scenario.shots
    assert len(story.beats) == len(scenario.lines)
    # Round-tripping proves it is the contract, not just a dataclass that looks like one.
    assert ShotPlan.model_validate_json(plan.model_dump_json()).shots
    assert StoryPlan.model_validate_json(story.model_dump_json()).beats
    # One drawing per shot, and Blender renders only the frame that gets drawn.
    assert all(s.anchor_frames == (0,) and s.render.frames == (0,) for s in plan.shots)
    expected = len(SCENARIOS[slug].characters) if slug in ORBITERS else 2
    assert all(len(s.characters) == (1 if slug in ORBITERS else expected) for s in plan.shots)


@pytest.mark.parametrize("slug", sorted(TWO_HANDERS))
def test_both_people_stay_inside_the_frame(slug: str) -> None:
    """The camera looks straight down from ``height``."""
    plan = gen.build_shots(SCENARIOS[slug])
    for shot in plan.shots:
        key = shot.camera.keyframes[0]
        across = gen.SENSOR_MM * key.position[2] / key.lens_mm
        down = across * shot.height / shot.width
        xs = [c.transform.position[0] for c in shot.characters]
        span = abs(xs[1] - xs[0]) + gen.BODY_MARGIN_M
        assert span <= across, f"{shot.shot_id}: pair spans {span:.2f} m in a {across:.2f} m frame"
        assert gen.BODY_PLAN_M <= down, f"{shot.shot_id}: body taller than the {down:.2f} m frame"


def test_poses_swing_about_the_axis_a_profile_render_confirmed() -> None:
    """Every limb swings about the bone's local X, and nothing else."""
    for pose in (gen.standing(), gen.stride(0.25), gen.reaching(1.0)):
        for name, rotation in pose.items():
            q = rotation.rotation_quaternion
            assert math.isclose(math.sqrt(sum(c * c for c in q)), 1.0, abs_tol=1e-4), name
            # A pure X rotation leaves the j and k components zero. Anything else is a lost axis.
            assert abs(q[2]) < 1e-9 and abs(q[3]) < 1e-9, f"{name} rotates off local X"
    # Reaching swings the arms FORWARD (positive local X); standing leaves them at rest.
    assert gen.reaching(1.0)["upperarm01.L"].rotation_quaternion[1] > 0.1
    assert abs(gen.standing()["upperarm01.L"].rotation_quaternion[1]) < 1e-6
    # Walking is antiphase: when one leg swings forward the other swings back.
    walking = gen.stride(0.25)
    assert walking["upperleg01.L"].rotation_quaternion[1] > 0
    assert walking["upperleg01.R"].rotation_quaternion[1] < 0


def test_the_pair_face_each_other() -> None:
    """yaw -90 faces -X, verified in a profile render."""
    for scenario in TWO_HANDERS.values():
        for shot in gen.build_shots(scenario).shots:
            left, right = sorted(shot.characters, key=lambda c: c.transform.position[0])
            assert left.transform.yaw_deg == 90.0, f"{shot.shot_id}: {left.id} faces away"
            assert right.transform.yaw_deg == -90.0, f"{shot.shot_id}: {right.id} faces away"


def test_hands_can_actually_reach_each_other() -> None:
    """Two facing people with ~0.62 m arms cannot touch fingertips closer than about 1.2 m."""
    arm_span = 2 * 0.62
    for scenario in TWO_HANDERS.values():
        reaching = [a for a in scenario.acts if a.kind == "reach"]
        for act in reaching:
            for gap in (act.gap_from, act.gap_to):
                assert gap <= arm_span + 0.3, f"{scenario.slug}: {gap} m reach would interpenetrate"
                assert gap >= arm_span - 0.3, f"{scenario.slug}: {gap} m is out of arm's reach"


def test_the_shipped_fixtures_match_what_the_generator_produces() -> None:
    """The fixtures are generated output kept in the repo; drift means someone hand-edited one."""
    for scenario in gen.SCENARIOS:
        on_disk = (REPO_ROOT / "fixtures" / "shots" / f"{scenario.shots_name}.json").read_text()
        assert ShotPlan.model_validate_json(on_disk) == gen.build_shots(scenario)
        story_disk = (REPO_ROOT / "fixtures" / "story" / f"{scenario.story_name}.json").read_text()
        assert StoryPlan.model_validate_json(story_disk) == gen.build_story(scenario)


@pytest.mark.parametrize("slug", sorted(TWO_HANDERS))
def test_nobody_is_drawn_too_small_for_the_skeleton_to_be_read(slug: str) -> None:
    """Measured floor, not taste."""
    plan = gen.build_shots(SCENARIOS[slug])
    for shot in plan.shots:
        key = shot.camera.keyframes[0]
        frame_height = (gen.SENSOR_MM * key.position[2] / key.lens_mm) * shot.height / shot.width
        fraction = gen.BODY_PLAN_M / frame_height
        assert fraction >= gen.MIN_BODY_FRACTION - 1e-6, (
            f"{shot.shot_id}: a person is {fraction:.0%} of the frame"
        )


def test_the_generator_refuses_a_gap_it_cannot_frame_readably() -> None:
    """A scenario that stages people too far apart fails here, naming the shot."""
    from dataclasses import replace

    scenario = SCENARIOS["love_story"]
    too_wide = replace(scenario, acts=(replace(scenario.acts[0], gap_from=9.0),))
    with pytest.raises(ValueError, match="below the 22% the image model needs"):
        gen.build_shots(too_wide)


@pytest.mark.parametrize("slug", sorted(ORBITERS))
def test_an_orbiting_camera_actually_moves(slug: str) -> None:
    """Thirty near-identical cameras is a slideshow with extra steps."""
    import math
    from itertools import pairwise
    from statistics import median

    plan = gen.build_shots(SCENARIOS[slug])
    positions = [s.camera.keyframes[0].position for s in plan.shots]
    deltas = [math.dist(a, b) for a, b in pairwise(positions)]
    assert median(deltas) > 0.2, "consecutive shots must read as different shots"
    assert median(deltas) < 6.0, "and still read as one place"
    lenses = {s.camera.keyframes[0].lens_mm for s in plan.shots}
    assert len(lenses) > 5, "the lens should move too, not just the position"
    # Every camera stays pointed at the subject: thirty views of one thing, not thirty pictures.
    assert all(s.camera.keyframes[0].look_at == (0.0, 0.0, 0.95) for s in plan.shots)


def test_the_rubric_scores_measurements_and_cannot_be_re_based() -> None:
    """The rubric's whole value is that raising a score means changing the work."""
    from content_factory.controls import rubric

    assert rubric.TARGET == 8.5
    assert rubric.RUBRIC_VERSION == "1.0.0"
    report = rubric.score_report(
        [rubric.Score(c.key, 9.0, c.weight, c.why) for c in rubric.CRITERIA]
    )
    assert report["meets_target"] is True and report["rubric_version"] == "1.0.0"
    # No per-shot rows unless asked for, so an existing caller sees the same document.
    assert "per_shot" not in report and "worst_shots" not in report
    # The identical measurements must score identically every time.
    assert rubric.score_readability(0.19) == rubric.score_readability(0.19)
    # A solo scene is not penalised on a two-character criterion.
    assert rubric.score_identity_distinctness(0.0, characters=1) == 10.0
    assert rubric.score_identity_distinctness(0.004, characters=2) < 1.0


def test_the_film_score_and_the_per_shot_scores_answer_different_questions() -> None:
    """A film scored on its worst shot per criterion cannot tell you whether a change worked."""
    from content_factory.controls import rubric

    def shot(value: float) -> list[rubric.Score]:
        return [rubric.Score(c.key, value, c.weight, c.why) for c in rubric.CRITERIA]

    film = shot(9.0)
    report = rubric.score_report(film, {"sht_good": shot(9.4), "sht_bad": shot(3.1)})
    assert report["overall"] == 9.0 and report["meets_target"] is True
    assert report["per_shot"]["sht_good"]["meets_target"] is True
    assert report["per_shot"]["sht_bad"]["meets_target"] is False
    assert report["per_shot"]["sht_bad"]["overall"] == 3.1
    # A film can meet the target with an unusable shot in it, and this is where that shows.
    assert report["worst_shots"] == ["sht_bad"]
    assert set(report["per_shot"]["sht_good"]["criteria"]) == {c.key for c in rubric.CRITERIA}
    # An empty shot contributes nothing rather than dividing by zero.
    assert "sht_empty" not in rubric.score_report(film, {"sht_empty": []})["per_shot"]
