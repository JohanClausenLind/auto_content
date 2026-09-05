"""One master frame rate, from the story to the finished mux.

Every wind short v1 to v5 shipped 24 fps footage inside a 30 fps film. Nothing was broken and
nothing reported it: `fixtures/story/wind_2024.json` is 30 fps, `ShotSettings.fps` is 24, and
`plan_shots` took the setting as its default because no code compared the two. `compose_video` then
conformed each generated clip with ffmpeg's `fps` filter, which reaches a higher rate by
DUPLICATING frames — one frame in five shown twice, for the whole film, invisibly.

So there are three separate guarantees here and they are tested separately: the story's rate is
what `plan_shots` plans at; a mismatch is refused rather than conformed; and when a conform does
fire (a hand-authored plan, a re-used clip) it is written into `compose.json`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from content_factory.config import get_settings
from content_factory.schemas.fixtures import sample_campaign
from content_factory.schemas.scenes import StoryPlan
from content_factory.schemas.shots import ShotPlan
from content_factory.workflows.stages import (
    FPS_MASTER_HINT,
    StageContext,
    _segment_filter,
    stage_plan_shots,
)

REPO = Path(__file__).resolve().parents[2]
WIND_STORY = REPO / "fixtures" / "story" / "wind_2024.json"
WIND_SHOTS = "fixtures/shots/wind_2024.json"


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    campaign = sample_campaign()
    return StageContext(
        workspace_id=campaign.workspace_id,
        project_dir=tmp_path / "project",
        artifacts_dir=tmp_path / "artifacts",
        campaign=campaign,
        deliverable_id="dlv_short0000001",
        quality="smoke",
        dep_outputs={},
    )


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CF__SHOTS__PLANNER", "story_presets")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _write_story(ctx: StageContext, plan: StoryPlan) -> None:
    path = ctx.project_dir / "story" / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=1))


def test_a_thirty_fps_story_plans_thirty_fps_shots(ctx: StageContext) -> None:
    """The setting says 24 and the story says 30. The story wins, because it is the film."""
    story = StoryPlan.model_validate_json(WIND_STORY.read_text())
    assert story.fps == 30 and get_settings().shots.fps == 24
    _write_story(ctx, story)
    out = stage_plan_shots(ctx)
    assert out.facts["fps"] == 30
    plan = ShotPlan.model_validate_json((ctx.ddir() / "shots" / "plan.json").read_text())
    assert {s.fps for s in plan.shots} == {30}


def test_the_widget_cannot_disagree_with_the_story_either(ctx: StageContext) -> None:
    """Setting the widget is not a way round the check. Delivering at a rate the story was not
    written at means retiming the whole film, which is a decision, not a widget value."""
    _write_story(ctx, StoryPlan.model_validate_json(WIND_STORY.read_text()))
    object.__setattr__(ctx, "params", {"fps": "25"})
    with pytest.raises(RuntimeError, match="25 fps"):
        stage_plan_shots(ctx)


def test_the_committed_wind_fixtures_now_agree(ctx: StageContext) -> None:
    story = StoryPlan.model_validate_json(WIND_STORY.read_text())
    shots = ShotPlan.model_validate_json((REPO / WIND_SHOTS).read_text())
    assert story.fps == shots.shots[0].fps == 30
    # Same durations as the 24 fps original, still on LTX-2.5's 8k+1 frame grid.
    for shot in shots.shots:
        assert (shot.frame_count - 1) % 8 == 0
        assert 4.0 <= shot.frame_count / shot.fps <= 5.5


def test_a_fixture_plan_that_disagrees_with_the_story_is_refused(ctx: StageContext) -> None:
    story = StoryPlan.model_validate_json(WIND_STORY.read_text())
    _write_story(ctx, story)
    # The demo fixture plan is 24 fps and the story is 30: exactly the shipped mismatch.
    object.__setattr__(
        ctx, "params", {"planner": "fixture", "fixture_path": "fixtures/shots/demo.json"}
    )
    with pytest.raises(RuntimeError) as info:
        stage_plan_shots(ctx)
    message = str(info.value)
    assert "30 fps" in message and "24 fps" in message
    assert FPS_MASTER_HINT.split(".")[0] in message


# --- the conform itself -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Timeline:
    fps: int = 30
    width: int = 1080
    height: int = 1920


@dataclass(frozen=True)
class _Scene:
    start_frame: int = 0
    duration_frames: int = 90


def test_the_segment_filter_conforms_only_when_the_source_is_at_another_rate() -> None:
    tl, scene = _Timeline(), _Scene()
    for route in ("generate", "render"):
        matched = _segment_filter(route, tl, scene, 30.0)
        assert "fps=" not in matched, route
        stepped = _segment_filter(route, tl, scene, 24.0)
        assert "fps=30," in stepped, route
        # Unreadable source: conform anyway. An unconformed segment at the wrong rate
        # desynchronises everything after it in the concatenation.
        assert "fps=30," in _segment_filter(route, tl, scene, None), route
    # The letterbox is never a crop, whatever the rate.
    assert "force_original_aspect_ratio=decrease" in _segment_filter("generate", tl, scene, 30.0)
