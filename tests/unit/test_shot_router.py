"""Shot router (hybrid workflow): deterministic per-beat render|generate decisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from content_factory.config import get_settings
from content_factory.schemas.fixtures import sample_campaign, sample_shot_routing, sample_story_plan
from content_factory.schemas.shots import ShotRouting
from content_factory.shots import route_shots
from content_factory.shots.planner import plan_shots_from_story
from content_factory.workflows.stages import StageContext, stage_plan_shots, stage_route_shots


def _plans():
    story = sample_story_plan()
    shots = plan_shots_from_story(story, width=1024, height=576, fps=24, snap_to_ltx_length=True)
    return story, shots


def test_kind_table_routes_title_to_generate_and_data_beats_to_render() -> None:
    story, shots = _plans()
    routing = route_shots(story, shots, generate_kinds=("title", "image"))
    by_kind = {b.scene_kind: b.route for b in routing.beats}
    assert by_kind == {
        "title": "generate",
        "big_number": "render",
        "bullet_sequence": "render",
        "source_card": "render",
    }
    generated = [b for b in routing.beats if b.route == "generate"]
    assert generated[0].shot_id == shots.shots[0].shot_id  # the planner's shot for that beat
    assert all(b.shot_id is None for b in routing.beats if b.route == "render")
    assert routing.generated_shot_ids() == {shots.shots[0].shot_id}
    assert routing.route_for("beat_000000002") == "render"
    assert routing.route_for("beat_unknown0001") == routing.default_route


def test_overrides_win_and_default_route_applies_to_unlisted_kinds() -> None:
    story, shots = _plans()
    routing = route_shots(
        story,
        shots,
        default_route="generate",
        generate_kinds=(),
        overrides={"beat_000000003": "render"},
    )
    assert [b.route for b in routing.beats] == ["generate", "generate", "render", "generate"]
    assert "override" in routing.beats[2].reason


def test_generate_without_a_planned_shot_falls_back_to_render() -> None:
    story, shots = _plans()
    fewer = shots.model_copy(update={"shots": shots.shots[1:]})  # no shot for the title beat
    routing = route_shots(story, fewer, generate_kinds=("title",))
    first = routing.beats[0]
    assert first.route == "render" and first.shot_id is None
    assert "no shot planned" in first.reason


def test_routing_is_deterministic_and_keyed_by_both_plans() -> None:
    story, shots = _plans()
    a = route_shots(story, shots, generate_kinds=("title",))
    b = route_shots(story, shots, generate_kinds=("title",))
    assert a.content_hash() == b.content_hash() and a.routing_id == b.routing_id
    assert a.story_plan_hash == story.content_hash()
    assert a.shot_plan_hash == shots.content_hash()


def test_contract_rejects_generate_without_shot_and_out_of_order_beats() -> None:
    routing = sample_shot_routing().model_dump(mode="json")
    bad = json.loads(json.dumps(routing))
    bad["beats"][0]["shot_id"] = None
    with pytest.raises(ValidationError, match="shot_id"):
        ShotRouting.model_validate(bad)
    swapped = json.loads(json.dumps(routing))
    swapped["beats"][0], swapped["beats"][1] = swapped["beats"][1], swapped["beats"][0]
    with pytest.raises(ValidationError, match="story order"):
        ShotRouting.model_validate(swapped)


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    campaign = sample_campaign()
    return StageContext(
        workspace_id=campaign.workspace_id,
        project_dir=tmp_path / "project",
        artifacts_dir=tmp_path / "artifacts",
        campaign=campaign,
        deliverable_id="dlv_testvideo001",
        quality="smoke",
        dep_outputs={},
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    def _set(**kw: str) -> None:
        for k, v in kw.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()  # type: ignore[attr-defined]

    _set(CF__SHOTS__PLANNER="story_presets")
    yield _set
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_stage_requires_plan_shots(ctx: StageContext, env) -> None:
    with pytest.raises(RuntimeError, match="plan_shots first"):
        stage_route_shots(ctx)


def test_stage_writes_routing_and_honours_settings(ctx: StageContext, env) -> None:
    stage_plan_shots(ctx)
    out = stage_route_shots(ctx)
    routing = ShotRouting.model_validate_json((ctx.ddir() / "shots" / "routing.json").read_text())
    assert out.outputs_hash == routing.content_hash()
    # Nothing, and that is correct: the default generate_kinds is ("image",) and the demo fixture
    # has no image scene. Its four beats are a title, a big number, a bullet list and a source
    # card — all text, all typeset. This used to route the title beat to the image model, which is
    # how the wind shorts got drawn typography where a title belonged.
    assert out.facts == {
        "beats": 4,
        "generate": 0,
        "render": 4,
        "default_route": "render",
        "generate_kinds": ["image"],
    }
    env(CF__ROUTING__OVERRIDES=json.dumps({"beat_000000002": "generate"}))
    again = stage_route_shots(ctx)
    assert again.facts["generate"] == 1 and again.outputs_hash != out.outputs_hash
    env(CF__ROUTING__OVERRIDES="{}", CF__ROUTING__DEFAULT_ROUTE="generate")
    assert stage_route_shots(ctx).facts["generate"] == 4


def test_the_node_widgets_decide_the_routing_not_only_the_settings(ctx: StageContext, env) -> None:
    """Both were declared on the canvas and read by nothing, so a lane could not differ from the
    host's settings. hybrid-video.yaml now states generate_kinds: [image] in the definition."""
    stage_plan_shots(ctx)
    object.__setattr__(ctx, "params", {"generate_kinds": "[title, source_card]"})
    out = stage_route_shots(ctx)
    assert out.facts["generate"] == 2 and out.facts["generate_kinds"] == ["title", "source_card"]
    # CSV is accepted too: the canvas has no list control, so a list arrives as text.
    object.__setattr__(ctx, "params", {"generate_kinds": "title, big_number"})
    assert stage_route_shots(ctx).facts["generate"] == 2
    object.__setattr__(ctx, "params", {"default_route": "generate", "generate_kinds": "[]"})
    assert stage_route_shots(ctx).facts["generate"] == 4
    object.__setattr__(ctx, "params", {"default_route": "sometimes"})
    with pytest.raises(RuntimeError, match="default_route"):
        stage_route_shots(ctx)


def test_the_committed_default_is_the_one_kind_with_no_text_in_it() -> None:
    """Recorded as a test because it is a documented decision (ADR-0012, 2026-09-08 amendment),
    not a tuning value: every other scene kind's content is words a card renderer typesets."""
    from content_factory.config import get_settings

    assert get_settings().routing.generate_kinds == ("image",)


def test_generate_routed_beat_must_name_its_shot() -> None:
    """The cross-field rule the JSON Schema cannot express: a beat sent to the generative branch
    without a shot_id would leave compose_video with nothing to splice."""
    routing = sample_shot_routing().model_dump(mode="json")
    routing["beats"][0]["route"] = "generate"
    routing["beats"][0]["shot_id"] = None
    with pytest.raises(ValidationError, match="generate-routed beats need the shot_id"):
        ShotRouting.model_validate(routing)
