"""The retrieval stage and the shot planner that consumes it.

Two properties matter more than the happy path. A machine with no reference library must still run
every lane, because the library is 19 GB of host-specific data and a missing one is a normal state,
not an error. And a beat with no usable match must fall back to preset staging rather than have a
two-person staging invented for it, because inventing one is what produced the interpenetrating
hands the whole mocap path exists to avoid.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from content_factory.runners.local import make_context, run_plan
from content_factory.schemas.dag import Executor, Stage
from content_factory.schemas.fixtures import sample_story_plan
from content_factory.schemas.shots import SegmentClipPose
from content_factory.shots.framing import group_extent_at
from content_factory.shots.planner import (
    ESTIMATE_OPTIMISM,
    POSE_CLIFF_FRACTION,
    plan_shots_from_reference,
    underframed_shots,
)
from content_factory.workflows.stages import STAGE_EXECUTORS

CLIPS = Path("/mnt/fast/models/blender-assets/clips")
BAKED = "cmu_22_23_04"
WALKING = "cmu_20_21_02"
"""Link arms and walk: travels 2.2 m, which is what makes a narrow frame retreat."""
SOLO = "cmu_35_18"
"""A solo running trial. The two-person library holds no sustained run, so a run is one body."""
baked = pytest.mark.skipif(
    not (CLIPS / f"{BAKED}.json").is_file(), reason="clip library not baked on this host"
)
walking = pytest.mark.skipif(
    not (CLIPS / f"{WALKING}.json").is_file(), reason="clip library not baked on this host"
)
solo = pytest.mark.skipif(
    not (CLIPS / f"{SOLO}.json").is_file(), reason="solo run clips not baked on this host"
)


def _selection(beat_id: str, clip_id: str) -> dict:
    return {
        "beats": [
            {
                "beat_id": beat_id,
                "order": 0,
                "match_set": {"matches": [{"clip_id": clip_id, "rank": 0, "score": 1.0}]},
            }
        ]
    }


def test_the_stage_is_registered_and_deterministic() -> None:
    assert Stage.find_reference in STAGE_EXECUTORS
    from content_factory.deliverables.dag_compiler import stage_defaults

    assert stage_defaults()[Stage.find_reference] == ("control", Executor.deterministic)


def test_no_library_selects_nothing_and_says_so(tmp_path: Path, monkeypatch) -> None:
    """The behaviour that keeps every lane runnable on a machine without the data."""
    monkeypatch.setenv("CF__REFERENCE__INDEX_PATH", str(tmp_path / "nope.sqlite"))
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        ctx = make_context(project_dir=tmp_path / "prj")
        report = run_plan(
            [("story", Stage.plan_story, {}), ("ref", Stage.find_reference, {})],
            ctx,
            log=lambda _m: None,
        )
        assert report["passed"]
        facts = report["stages"][-1]["facts"]
        assert facts["library"] == "absent"
        assert facts["selected"] == 0
        doc = json.loads((ctx.ddir() / "reference" / "selection.json").read_text())
        assert doc["library"] is None
        assert "no reference library" in doc["note"]
        # The note names the command that would build one, so the run is self-explaining.
        assert "content_factory.reference.build" in doc["note"]
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_the_stage_output_hash_is_stable_across_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CF__REFERENCE__INDEX_PATH", str(tmp_path / "nope.sqlite"))
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        hashes = []
        for i in range(2):
            ctx = make_context(project_dir=tmp_path / f"prj{i}")
            report = run_plan(
                [("story", Stage.plan_story, {}), ("ref", Stage.find_reference, {})],
                ctx,
                log=lambda _m: None,
            )
            hashes.append(report["stages"][-1]["outputs_hash"])
        assert hashes[0] == hashes[1], "an idempotent stage must hash the same twice"
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_the_planner_falls_back_with_no_selection() -> None:
    story = sample_story_plan()
    plan = plan_shots_from_reference(story, {}, width=1024, height=576, fps=24)
    assert plan.planner == "reference"
    assert len(plan.shots) == len(story.beats)
    # One idle character, not an invented pair.
    for shot in plan.shots:
        assert len(shot.characters) == 1
        assert shot.characters[0].pose.kind == "library"


def test_the_planner_ignores_a_match_whose_clip_is_not_on_disk() -> None:
    """A retrieved clip that cannot drive a rig must not be staged from. Half the library is
    footage and skeletons that no rig can be aimed by."""
    story = sample_story_plan()
    plan = plan_shots_from_reference(
        story,
        _selection(story.beats[0].beat_id, "tvhi_hug_0001"),
        width=1024,
        height=576,
        fps=24,
    )
    assert all(c.pose.kind == "library" for sh in plan.shots for c in sh.characters)


@baked
def test_a_matched_mocap_clip_stages_both_actors() -> None:
    """The point of the whole path: two characters on one captured take, so the contact on screen
    is the contact that was recorded."""
    story = sample_story_plan()
    beat = story.beats[0].beat_id
    plan = plan_shots_from_reference(story, _selection(beat, BAKED), width=1024, height=576, fps=24)
    staged = next(sh for sh in plan.shots if sh.beat_id == beat)
    assert len(staged.characters) == 2
    poses = [c.pose for c in staged.characters]
    assert all(isinstance(p, SegmentClipPose) for p in poses)
    segments = [p for p in poses if isinstance(p, SegmentClipPose)]
    assert {p.name for p in segments} == {BAKED}
    # Different actors of the same clip, which is what preserves the captured geometry.
    assert sorted(p.actor for p in segments) == ["a", "b"]
    assert {c.seg_id for c in staged.characters} == {1, 2}
    # The clip id is a measurement for the operator, not prompt text: it lives on staging_note,
    # and the description is a picture description. See shots/prompt_compile.py.
    assert BAKED in (staged.staging_note or "")
    assert BAKED not in (staged.description or "") + staged.motion_prompt
    # What the clip shows reaches the models as a word, through the lexicon that retrieved it.
    assert staged.action and "captured take" in staged.action

    # Beats with no match keep the preset staging rather than borrowing this one.
    others = [sh for sh in plan.shots if sh.beat_id != beat]
    assert others and all(len(sh.characters) == 1 for sh in others)


@solo
def test_a_solo_clip_stages_one_character_and_not_a_phantom_second() -> None:
    """A one-actor clip can answer for actor "a" and nothing else.

    The cast is two characters, so the tempting thing is to give the second one actor "b" - which
    the clip does not carry, and which fails inside Blender at render time instead of here.
    """
    story = sample_story_plan()
    beat = story.beats[0].beat_id
    plan = plan_shots_from_reference(story, _selection(beat, SOLO), width=1024, height=576, fps=24)
    staged = next(sh for sh in plan.shots if sh.beat_id == beat)
    assert len(staged.characters) == 1
    pose = staged.characters[0].pose
    assert isinstance(pose, SegmentClipPose)
    assert (pose.name, pose.actor) == (SOLO, "a")
    doc = json.loads((CLIPS / f"{SOLO}.json").read_text())
    assert [a["actor_id"] for a in doc["actors"]] == ["a"]


@baked
def test_the_planner_is_pure() -> None:
    story = sample_story_plan()
    sel = _selection(story.beats[0].beat_id, BAKED)
    a = plan_shots_from_reference(story, sel, width=1024, height=576, fps=24)
    b = plan_shots_from_reference(story, sel, width=1024, height=576, fps=24)
    assert a.content_hash() == b.content_hash()


@baked
def test_the_stage_reports_what_it_staged_from(tmp_path: Path) -> None:
    """A run's facts should say whether retrieval changed anything, without opening a file."""
    ctx = make_context(project_dir=tmp_path / "prj")
    run_plan([("story", Stage.plan_story, {})], ctx, log=lambda _m: None)
    story = sample_story_plan()
    (ctx.ddir() / "reference").mkdir(parents=True, exist_ok=True)
    (ctx.ddir() / "reference" / "selection.json").write_text(
        json.dumps(_selection(story.beats[0].beat_id, BAKED))
    )
    report = run_plan(
        [("shots", Stage.plan_shots, {"planner": "reference"})], ctx, log=lambda _m: None
    )
    facts = report["stages"][-1]["facts"]
    assert facts["planner"] == "reference"
    assert facts["staged_from"] == [BAKED]


def test_an_unsupported_fps_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported fps"):
        plan_shots_from_reference(sample_story_plan(), {}, width=1024, height=576, fps=23)


@walking
def test_a_narrow_frame_gets_a_tracking_camera_and_a_wide_one_does_not() -> None:
    """The measured reason this branch exists: no clip in the library needs tracking in 16:9, and
    30 of 57 do in 9:16, because a portrait frame is too narrow to hold two bodies apart and the
    width push-back retreats the camera under the cliff where the pose skeleton stops being read.
    """
    story = sample_story_plan()
    beat = story.beats[0].beat_id
    sel = _selection(beat, WALKING)

    wide = plan_shots_from_reference(story, sel, width=1024, height=576, fps=24)
    tall = plan_shots_from_reference(story, sel, width=576, height=1024, fps=24)
    w_shot = next(sh for sh in wide.shots if sh.beat_id == beat)
    t_shot = next(sh for sh in tall.shots if sh.beat_id == beat)

    assert len(w_shot.camera.keyframes) == 1, "a wide frame needs no help"
    assert "tracking" not in (w_shot.staging_note or "")
    assert len(t_shot.camera.keyframes) > 1
    assert "tracking" in (t_shot.staging_note or "")
    # And it has to have actually worked, not just moved the camera about. Only this shot: the
    # film's preset shots are under the cliff in a vertical frame for their own reasons.
    assert t_shot.shot_id not in dict(underframed_shots(tall)), (
        "tracking that leaves the shot under the cliff is churn"
    )


@walking
def test_tracking_keyframes_land_on_the_frames_the_image_model_sees() -> None:
    """The anchors are the frames handed to the image model, so they are the ones that must be
    legible. The ends are included too, or a static camera holds while the bodies walk into the
    near plane - the NaN that framing exists to prevent."""
    story = sample_story_plan()
    beat = story.beats[0].beat_id
    plan = plan_shots_from_reference(
        story, _selection(beat, WALKING), width=576, height=1024, fps=24
    )
    shot = next(sh for sh in plan.shots if sh.beat_id == beat)
    at = [k.frame_index for k in shot.camera.keyframes]
    assert at == sorted(set(at))
    assert set(shot.anchor_frames) <= set(at)
    assert {0, shot.frame_count - 1} <= set(at)


@walking
def test_the_whole_cast_is_tracked_not_half_of_a_pair() -> None:
    """Framing one half of a pair is worse than framing both small: the partner leaves frame."""
    story = sample_story_plan()
    beat = story.beats[0].beat_id
    plan = plan_shots_from_reference(
        story, _selection(beat, WALKING), width=576, height=1024, fps=24
    )
    shot = next(sh for sh in plan.shots if sh.beat_id == beat)
    doc = json.loads((CLIPS / f"{WALKING}.json").read_text())
    ids = tuple(str(a["actor_id"]) for a in doc["actors"])
    assert len(ids) == 2
    # Both actors are posed from the clip, and the aim point sits between them at every keyframe.
    posed = [c.pose for c in shot.characters if isinstance(c.pose, SegmentClipPose)]
    assert tuple(pose.actor for pose in posed) == ids
    for kf in shot.camera.keyframes:
        assert kf.look_at is not None
        cf = round(kf.frame_index * doc["fps"] / shot.fps)
        both = group_extent_at(doc, cf, actor_ids=ids)[0]
        one = group_extent_at(doc, cf, actor_ids=(ids[0],))[0]
        assert abs(kf.look_at[0] - both[0]) <= abs(kf.look_at[0] - one[0]) + 1e-6


@baked
def test_what_the_plan_claims_is_what_the_verifier_measures() -> None:
    """A plan that reports a legible shot and stages an illegible one is the failure mode here, so
    the description and the stage fact come from one measurement of the finished camera."""
    story = sample_story_plan()
    beat = story.beats[0].beat_id
    for width, height in ((1024, 576), (576, 1024), (768, 768)):
        plan = plan_shots_from_reference(
            story, _selection(beat, BAKED), width=width, height=height, fps=24
        )
        shot = next(sh for sh in plan.shots if sh.beat_id == beat)
        claim = re.search(r"framed at ([0-9.]+)", shot.staging_note or "")
        assert claim is not None, "a staged shot has to say how it was framed"
        said = float(claim.group(1))
        reported = dict(underframed_shots(plan))
        if shot.shot_id in reported:
            assert reported[shot.shot_id] == pytest.approx(said, abs=0.01)
        else:
            assert said >= POSE_CLIFF_FRACTION + ESTIMATE_OPTIMISM - 0.005


@baked
def test_the_planner_is_pure_with_a_tracking_camera_too() -> None:
    story = sample_story_plan()
    sel = _selection(story.beats[0].beat_id, BAKED)
    a = plan_shots_from_reference(story, sel, width=576, height=1024, fps=24)
    b = plan_shots_from_reference(story, sel, width=576, height=1024, fps=24)
    assert a.content_hash() == b.content_hash()


def test_a_plan_with_no_staged_shots_is_still_checked_and_comes_back_clean() -> None:
    """A lane on a host with no clip library must not grow a warning it cannot act on, and must
    not escape the check either. With nothing staged every shot is measured the clip-free way, and
    since the preset cameras are aspect-corrected that comes back clean on every aspect."""
    story = sample_story_plan()
    for width, height in ((1024, 576), (576, 1024), (1024, 1024)):
        plan = plan_shots_from_reference(story, {}, width=width, height=height, fps=24)
        assert underframed_shots(plan) == (), (width, height)


def test_the_plan_shots_stage_reports_underframing(tmp_path: Path, monkeypatch) -> None:
    """The fact exists even with nothing to report, so an operator can tell "checked, fine" from
    "never looked". A shot under the cliff renders its control passes and then has them ignored by
    the image model, and the fix - a wider frame, or a clip whose cast stands closer - is a choice
    the planner should surface rather than make."""
    monkeypatch.setenv("CF__REFERENCE__INDEX_PATH", str(tmp_path / "nope.sqlite"))
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        ctx = make_context(project_dir=tmp_path / "prj")
        report = run_plan(
            [
                ("story", Stage.plan_story, {}),
                ("ref", Stage.find_reference, {}),
                ("shots", Stage.plan_shots, {"planner": "reference"}),
            ],
            ctx,
            log=lambda _m: None,
        )
        assert report["passed"]
        facts = report["stages"][-1]["facts"]
        assert facts["planner"] == "reference"
        assert facts["underframed"] == [], "nothing was staged, so nothing can be underframed"
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_the_cliff_is_tested_with_the_estimate_s_own_bias_added() -> None:
    """The estimate models a hip plus a standing head; the render measures the mesh's bounding
    box, and came in 0.016 and 0.022 lower on the two shots checked frame by frame. Testing the
    raw cliff would call a shot estimated at 0.34 legible when it renders at 0.318."""
    from content_factory.shots.planner import _under_cliff

    assert ESTIMATE_OPTIMISM > 0.0
    assert _under_cliff(0.34), "the case measured on a real render"
    assert not _under_cliff(0.40)
    assert _under_cliff(POSE_CLIFF_FRACTION)
    assert not _under_cliff(POSE_CLIFF_FRACTION + ESTIMATE_OPTIMISM)


def test_preset_shots_are_checked_too_and_the_check_has_teeth() -> None:
    """A shot with a ``LibraryPose`` has no clip to read a hip height out of, and used to be
    skipped entirely, so the one defect nobody could see was the preset planner's own: before the
    cameras were aspect-corrected every preset shot in a vertical film opened at a measured 0.216
    of frame height. The corrected presets are clean, so this also pulls a camera back by hand to
    prove the check is measuring rather than agreeing."""
    from content_factory.shots import plan_shots_from_story

    story = sample_story_plan()
    for width, height in ((1024, 576), (576, 1024)):
        plan = plan_shots_from_story(story, width=width, height=height, fps=24)
        assert underframed_shots(plan) == (), (width, height)

        far = plan.shots[0]
        pulled = far.camera.model_copy(
            update={
                "keyframes": tuple(
                    k.model_copy(update={"position": (k.position[0], -40.0, k.position[2])})
                    for k in far.camera.keyframes
                )
            }
        )
        moved = plan.model_copy(
            update={"shots": (far.model_copy(update={"camera": pulled}), *plan.shots[1:])}
        )
        flagged = dict(underframed_shots(moved))
        assert far.shot_id in flagged, (width, height)
        assert flagged[far.shot_id] < POSE_CLIFF_FRACTION
