"""The prompt path: what the image model is told, what the video model is told, and which of the
two a change re-generates.

This is the most-measured defect in the repo (STATUS 1627, 1704). The planner used to copy a
beat's ``display_text`` — narration, written to be spoken over a picture — into ``motion_prompt``,
and the anchor prompt put that in front of the image model along with a "beat N" label. Two things
followed. The pictures were illustrations of an argument rather than descriptions of a frame, and
inserting a beat mid-story renumbered every later beat and re-generated a whole film's worth of
anchors that had not changed.

So the tests here are about *separation*: the still gets one instant, the clip gets the
progression, and each regenerates only for its own reasons.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.config import get_settings
from content_factory.schemas.fixtures import sample_campaign, sample_story_plan
from content_factory.schemas.scenes import StoryPlan, VisualBeat
from content_factory.schemas.shots import CameraPreset, ShotPlan
from content_factory.shots.planner import plan_shots_from_story
from content_factory.shots.prompt_compile import (
    PROMPT_COMPILER_VERSION,
    compile_video_prompt,
    progression_sentence,
    state_sentence,
)
from content_factory.workflows.stages import (
    StageContext,
    _anchor_lock,
    _anchor_prompt,
    stage_compile_controls,
    stage_generate_anchor,
    stage_generate_video,
    stage_plan_shots,
)


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    campaign = sample_campaign()
    return StageContext(
        workspace_id=campaign.workspace_id,
        project_dir=tmp_path / "project",
        artifacts_dir=tmp_path / "artifacts",
        campaign=campaign,
        deliverable_id="dlv_image0000001",
        quality="smoke",
        dep_outputs={},
    )


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CF__SHOTS__PLANNER", "story_presets")
    monkeypatch.setenv("CF__CONTROLS__COMPILER", "motion_plan")
    monkeypatch.setenv("CF__CONTROLS__ASSETS_ROOT", str(tmp_path / "no-assets"))
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _story(*, beats: int, visual_subject: str | None = "a windswept coastal headland") -> StoryPlan:
    """A story with ``beats`` image beats, ids stable across a change of length."""
    base = sample_story_plan()
    scene = next(s for s in base.scenes if s.kind == "title")
    return StoryPlan(
        plan_id="stp_prompts00001",
        deliverable_id="dlv_image0000001",
        fps=24,
        width=1024,
        height=576,
        visual_subject=visual_subject,
        beats=tuple(
            VisualBeat(
                beat_id=f"bet_{i:012d}",
                order=i,
                display_text=f"Narration sentence number {i} that must never reach a model.",
                planned_duration_ms=2000,
            )
            for i in range(beats)
        ),
        scenes=tuple(
            scene.model_copy(update={"scene_id": f"scn_{i:012d}", "beat_id": f"bet_{i:012d}"})
            for i in range(beats)
        ),
    )


def _write_story(ctx: StageContext, story: StoryPlan) -> None:
    path = ctx.project_dir / "story" / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(story.model_dump_json(indent=1))


def _anchor_markers(ctx: StageContext) -> dict[str, str]:
    """shot_id/frame -> input_hash, read off the markers on disk."""
    out: dict[str, str] = {}
    for marker in sorted((ctx.ddir() / "anchors").rglob("*.done.json")):
        record = json.loads(marker.read_text())
        out[f"{record['shot_id']}/{record['frame_index']}"] = record["input_hash"]
    return out


# --- the compiler itself ---------------------------------------------------------------------


def test_the_planner_never_copies_narration_or_a_beat_number() -> None:
    story = _story(beats=4)
    plan = plan_shots_from_story(story, width=1024, height=576)
    for shot in plan.shots:
        assert shot.description is not None
        text = f"{shot.motion_prompt} {shot.description}"
        assert "Narration sentence" not in text
        assert "beat" not in text.lower()
        # The one-instant sentence names the film's world and where the light comes from.
        assert "windswept coastal headland" in shot.description
        assert "one figure" in shot.description
        # The progression names the camera and says, honestly, that nothing else moves.
        assert "camera" in shot.motion_prompt
        assert shot.action == ""


def test_the_state_sentence_reads_the_start_of_the_move_not_the_preset_name() -> None:
    """A push-in opens wide and a pull-out opens close, and the anchor is generated at frame 0."""
    from content_factory.schemas.shots import EnvironmentSpec

    kw = {"lighting_preset": "exterior_day", "environment": EnvironmentSpec()}
    push = state_sentence(preset=CameraPreset.slow_push_in, **kw)  # type: ignore[arg-type]
    pull = state_sentence(preset=CameraPreset.slow_pull_out, **kw)  # type: ignore[arg-type]
    assert "wide shot" in push and "close shot" in pull
    assert "overcast daylight" in push
    # No verbs of motion in a sentence describing one frame.
    for text in (push, pull):
        assert "pushes" not in text and "pulls" not in text and "camera" not in text
    assert "camera pushes slowly in" in progression_sentence(preset=CameraPreset.slow_push_in)


def test_the_compiled_video_prompt_carries_every_component_and_the_shot_action() -> None:
    story = _story(beats=2)
    shot = plan_shots_from_story(story, width=1024, height=576).shots[0]
    staged = shot.model_copy(
        update={"action": "the pair hold hands exactly as in the captured take"}
    )
    prompt = compile_video_prompt(staged, story)
    assert prompt.startswith("the pair hold hands")  # the action leads, as the models expect
    assert "captured take" in prompt
    assert "windswept coastal headland" in prompt
    assert "camera pushes slowly in" in prompt
    assert "framing has closed" in prompt  # derived end state
    assert "nothing else in the frame moves" in prompt
    assert len(prompt.split()) <= 200
    # Pure: same shot, same story, same bytes.
    assert compile_video_prompt(staged, story) == prompt
    assert PROMPT_COMPILER_VERSION.count(".") == 2


def test_a_story_with_no_visual_subject_still_compiles_and_invents_no_setting() -> None:
    story = _story(beats=1, visual_subject=None)
    shot = plan_shots_from_story(story, width=1024, height=576).shots[0]
    assert shot.description is not None and "unadorned figure study" in shot.description
    assert "unadorned figure study" in compile_video_prompt(shot, story)


# --- what a change regenerates ----------------------------------------------------------------


def _run_to_anchors(ctx: StageContext, story: StoryPlan) -> None:
    _write_story(ctx, story)
    stage_plan_shots(ctx)
    stage_compile_controls(ctx)
    stage_generate_anchor(ctx)


def test_restaging_a_shot_leaves_every_anchor_cached_and_regenerates_the_clip(
    ctx: StageContext,
) -> None:
    """(a) The action and the progression belong to the clip alone.

    Rewriting what happens *during* a shot cannot change what its first frame looks like, so the
    anchors must all come back from cache. The clip must not: it is the thing that was told the
    old progression.
    """
    _run_to_anchors(ctx, _story(beats=2))
    before = stage_generate_video(ctx)
    plan_path = ctx.ddir() / "shots" / "plan.json"
    plan = ShotPlan.model_validate_json(plan_path.read_text())
    restaged = plan.model_copy(
        update={
            "shots": tuple(
                s.model_copy(
                    update={
                        "action": "the figure turns away and walks out of frame",
                        "motion_prompt": progression_sentence(
                            preset=s.camera.preset,
                            action="the figure turns away and walks out of frame",
                        ),
                    }
                )
                for s in plan.shots
            )
        }
    )
    plan_path.write_text(restaged.model_dump_json(indent=1))

    anchors = stage_generate_anchor(ctx)
    assert anchors.facts["cache_hits"] == anchors.facts["anchors"] > 0
    after = stage_generate_video(ctx)
    assert before.facts["cache_hits"] == 0  # nothing was cached before this run
    assert after.facts["cache_hits"] == 0 and after.facts["shots"] == before.facts["shots"]


def test_inserting_a_beat_mid_story_leaves_the_other_shots_anchors_untouched(
    ctx: StageContext,
) -> None:
    """(b) The regression this whole item exists for.

    The description used to say "title scene for beat 3". Insert a beat at the front and every
    later beat renumbers, so every later anchor prompt changes and a film's worth of GPU time is
    spent regenerating frames that are identical.
    """
    _run_to_anchors(ctx, _story(beats=3))
    before = _anchor_markers(ctx)
    assert len(before) == 3

    # One more beat, at the end of the id space but ordered into the middle of the story: the
    # existing beats keep their ids, which is what keeps their shot ids stable.
    grown = _story(beats=4)
    _run_to_anchors(ctx, grown)
    after = _anchor_markers(ctx)

    assert len(after) == 4
    shared = set(before) & set(after)
    assert len(shared) == 3
    assert {k: after[k] for k in shared} == before


def test_a_style_change_redraws_the_picture_and_leaves_the_words_alone(ctx: StageContext) -> None:
    """(c) The style belongs to the picture. Narration, captions and the timeline are text and
    timing, and no art direction may invalidate them."""
    from content_factory.workflows.stages import (
        stage_align_words,
        stage_compile_captions,
        stage_compile_timeline,
        stage_lock_script,
        stage_synthesize_narration,
    )

    story = _story(beats=2)
    _write_story(ctx, story)
    stage_lock_script(ctx)
    words = {
        "narration": stage_synthesize_narration(ctx).outputs_hash,
        "align": stage_align_words(ctx).outputs_hash,
        "captions": stage_compile_captions(ctx).outputs_hash,
        "timeline": stage_compile_timeline(ctx).outputs_hash,
    }
    stage_plan_shots(ctx)
    stage_compile_controls(ctx)
    first_anchor = stage_generate_anchor(ctx)
    first_clip = stage_generate_video(ctx)

    object.__setattr__(ctx, "params", {"style": "woodblock"})
    styled_anchor = stage_generate_anchor(ctx)
    styled_clip = stage_generate_video(ctx)
    object.__setattr__(ctx, "params", {})

    assert styled_anchor.facts["cache_hits"] == 0 and first_anchor.facts["cache_hits"] == 0
    # A new anchor is a new first frame, so the clips are re-generated off it.
    assert styled_clip.facts["cache_hits"] == 0 and first_clip.facts["shots"] > 0
    assert {
        "narration": stage_synthesize_narration(ctx).outputs_hash,
        "align": stage_align_words(ctx).outputs_hash,
        "captions": stage_compile_captions(ctx).outputs_hash,
        "timeline": stage_compile_timeline(ctx).outputs_hash,
    } == words


def test_the_anchor_marker_records_the_words_the_model_was_given(ctx: StageContext) -> None:
    _run_to_anchors(ctx, _story(beats=1))
    marker = next((ctx.ddir() / "anchors").rglob("*.done.json"))
    record = json.loads(marker.read_text())
    shot = ShotPlan.model_validate_json((ctx.ddir() / "shots" / "plan.json").read_text()).shots[0]
    lock = _anchor_lock(
        width=shot.width,
        height=shot.height,
        backend=_MockName(),  # type: ignore[arg-type]
        ctx=ctx,
    )
    assert record["prompt"].startswith(lock.style_prompt.rstrip("."))
    assert shot.description is not None and shot.description.rstrip(".") in record["prompt"]
    assert shot.motion_prompt not in record["prompt"]
    assert _anchor_prompt(ctx, shot, lock) == record["prompt"]


class _MockName:
    """The two fields ``_anchor_lock`` reads off a backend, without importing one."""

    name = "mock-reference-edit"
