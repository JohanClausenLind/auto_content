"""The explainer lane: its contract, its checker, the planner's repair loop, and the bridge.

The planner prompt and its schema were written by a review loop (docs/prompts/explainer-planner);
this is the pipeline half, and most of what is tested is that nothing unchecked gets through:

* the Pydantic contract and the schema the model reads accept and refuse the same plans;
* the checker's errors go back to the model as a repair turn, and a plan that never passes is a
  named failure rather than a film;
* the bridge keeps every sentence of narration exactly, because the voice is timed from it.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from content_factory.explainer.bridge import (
    SECTION_FOR,
    display_label,
    on_screen_by_beat,
    replay,
    story_plan_from_explainer,
)
from content_factory.explainer.check import check
from content_factory.models.explainer_planner import (
    PROMPT_DIR,
    campaign_request,
    draft_explainer_plan,
    prompt_version,
    repair_message,
    request_block,
)
from content_factory.schemas.explainer import ExplainerPlan
from content_factory.schemas.fixtures import sample_campaign
from content_factory.schemas.scenes import StoryPlan

REPO = Path(__file__).resolve().parents[2]
FIXTURES = sorted((REPO / "fixtures" / "explainer").glob("*.json"))
SCHEMA = json.loads((PROMPT_DIR / "schema.json").read_text())


def _load(name: str = "ssd_nearly_full") -> dict:
    return json.loads((REPO / "fixtures" / "explainer" / f"{name}.json").read_text())


# --- the contract -----------------------------------------------------------------------------


def test_every_fixture_passes_the_checker_with_zero_errors() -> None:
    assert FIXTURES, "fixtures/explainer is empty"
    for path in FIXTURES:
        report = check(json.loads(path.read_text()))
        assert report["ok"], (path.name, report["errors"])


def _mutations() -> list[tuple[str, dict]]:
    base = _load()
    out: list[tuple[str, dict]] = []

    def mutate(name: str, fn) -> None:
        plan = copy.deepcopy(base)
        fn(plan)
        out.append((name, plan))

    mutate("unknown top-level field", lambda p: p.update(extra=1))
    mutate("missing notes", lambda p: p.pop("notes"))
    mutate("duration past 600", lambda p: p.update(target_duration_s=900))
    mutate("hue instead of role", lambda p: p["objects"][0].update(color_role="green"))
    mutate("beat id shape", lambda p: p["beats"][0].update(id="beat1"))
    mutate("null instead of empty", lambda p: p["objects"][0].update(parent=None))
    mutate("cell not an index", lambda p: p["objects"][0].update(cell="first"))
    mutate("31 objects", lambda p: p.update(objects=p["objects"] * 4))
    mutate(
        "zoom as an update op", lambda p: p["beats"][2]["visual_updates"][0].update(op="zoom_in")
    )
    mutate("sentence below -1", lambda p: p["claims_to_verify"][0].update(sentence=-2))
    return out


def test_the_pydantic_contract_and_the_schema_the_model_reads_agree() -> None:
    """Two statements of one contract: the file the model is shown and the model the pipeline
    parses. They must accept and refuse the same plans, or a plan the model was told is valid
    fails in the pipeline (or the reverse)."""
    validator = Draft202012Validator(SCHEMA)
    for path in FIXTURES:
        plan = json.loads(path.read_text())
        assert not list(validator.iter_errors(plan)), path.name
        assert ExplainerPlan.model_validate(plan).model_dump(mode="json", by_alias=True) == plan
    for name, plan in _mutations():
        assert list(validator.iter_errors(plan)), f"schema.json accepts: {name}"
        with pytest.raises(ValidationError):
            ExplainerPlan.model_validate(plan)
        assert not check(plan)["ok"], f"check accepts: {name}"


# --- the checker ------------------------------------------------------------------------------


def test_the_checker_refuses_what_the_prompt_forbids() -> None:
    plan = _load()
    broken = copy.deepcopy(plan)
    broken["beats"][3]["transition_in"] = "zoom_out"  # breaks the chain with b03's transition_out
    assert any("transition_in" in e for e in check(broken)["errors"])

    faulted_early = copy.deepcopy(plan)
    target = faulted_early["objects"][-1]["id"]
    faulted_early["beats"][1]["visual_updates"] = [
        {"at_sentence": 0, "op": "fault", "targets": [target], "into": "", "value": "", "note": "x"}
    ]
    assert any("not on screen" in e or "not in view" in e for e in check(faulted_early)["errors"])


# --- the planner ------------------------------------------------------------------------------


def _gateway(replies: list[dict], seen: list[list[dict]]):
    """A gateway whose model returns these plans in order and records every conversation."""
    from content_factory.budgets.ledger import Cap, CostLedger, Scope
    from content_factory.hardware.probe import mock_inventory
    from content_factory.models.catalog import default_catalog, default_endpoints
    from content_factory.models.gateway import ModelGateway

    ledger = CostLedger()
    ledger.set_cap(Cap(scope=Scope.monthly, key="explainer_planner", limit_usd=1.0))
    queue = list(replies)

    def complete(**kw):
        seen.append(copy.deepcopy(kw["messages"]))
        return {
            "choices": [{"message": {"content": json.dumps(queue.pop(0))}}],
            "usage": {"prompt_tokens": 6000, "completion_tokens": 5000},
        }

    return ModelGateway(
        catalog=default_catalog(),
        endpoints=default_endpoints(),
        ledger=ledger,
        inventory=mock_inventory("rtx3090"),
        completion_fn=complete,
    )


def _failing() -> dict:
    plan = _load()
    plan["beats"][3]["transition_in"] = "zoom_out"
    return plan


def test_a_failing_draft_is_repaired_with_the_checker_errors() -> None:
    """The first draft misses a counted rule; the errors go back as a user turn, and the second
    draft is accepted. This is the loop the eight review rounds said the pipeline needs."""
    seen: list[list[dict]] = []
    draft = draft_explainer_plan(
        request_block(topic="Why does an SSD slow down?"),
        gateway=_gateway([_failing(), _load()], seen),
    )
    assert draft.plan is not None and draft.report["ok"]
    assert [a.errors > 0 for a in draft.attempts] == [True, False]
    second = seen[1]
    assert second[0]["role"] == "system" and "mechanism-first" in second[0]["content"]
    assert second[-2]["role"] == "assistant"
    assert second[-1]["role"] == "user" and "transition_in" in second[-1]["content"]
    assert draft.facts["repairs"] == 1 and draft.facts["prompt_version"] == prompt_version()


def test_a_plan_that_never_passes_is_returned_as_a_failure_not_a_film() -> None:
    seen: list[list[dict]] = []
    draft = draft_explainer_plan(
        request_block(topic="x"), max_repairs=1, gateway=_gateway([_failing()] * 2, seen)
    )
    assert draft.plan is None and not draft.report["ok"]
    assert len(draft.attempts) == 2 and draft.last is not None


def test_the_request_leaves_out_what_the_prompt_defaults() -> None:
    bare = request_block(topic="Why?")
    assert "<topic>Why?</topic>" in bare and "target_duration_s" not in bare
    assert "audience" not in bare and "source_notes" not in bare
    full = campaign_request(
        sample_campaign(), target_duration_s=240, claim_statements=["Flash erases in blocks."]
    )
    assert "<target_duration_s>240</target_duration_s>" in full
    assert "<source_notes>- Flash erases in blocks.</source_notes>" in full


def test_the_repair_turn_lists_at_most_the_first_errors() -> None:
    text = repair_message([f"error {i}" for i in range(40)])
    assert "- error 0" in text and "- error 24" in text and "- error 25" not in text
    assert "15 more" in text


# --- the bridge -------------------------------------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_the_bridge_keeps_every_sentence_and_one_scene_per_beat(path: Path) -> None:
    plan = ExplainerPlan.model_validate_json(path.read_text())
    story = story_plan_from_explainer(
        plan, deliverable_id="dlv_short0000001", width=1920, height=1080
    )
    StoryPlan.model_validate(story.model_dump(mode="json"))
    assert len(story.beats) == len(story.scenes) == len(plan.beats)
    for beat, visual in zip(plan.beats, story.beats, strict=True):
        assert visual.display_text == " ".join(beat.narration)
        assert visual.section == SECTION_FOR[beat.type]
        assert visual.planned_duration_ms == beat.est_seconds * 1000
    kinds = {b.type: s.kind for b, s in zip(plan.beats, story.scenes, strict=True)}
    assert kinds["question_hook"] == "title" and kinds["takeaway"] == "outro"
    assert kinds["common_assumption"] == "callout" and kinds["resolution"] == "callout"
    assert "flow_diagram" in {s.kind for s in story.scenes}


def test_the_bridge_draws_what_is_on_screen_inside_the_frame() -> None:
    plan = ExplainerPlan.model_validate(_load())
    screens = on_screen_by_beat(plan)
    story = story_plan_from_explainer(
        plan, deliverable_id="dlv_short0000001", width=1920, height=1080
    )
    for beat, screen, scene in zip(plan.beats, screens, story.scenes, strict=True):
        if scene.kind == "flow_diagram":
            assert {n.node_id for n in scene.nodes} <= screen, beat.id
            assert 2 <= len(scene.nodes) <= 12


# --- the stage --------------------------------------------------------------------------------


def test_plan_story_uses_a_named_explainer_plan_and_writes_what_it_checked(tmp_path: Path) -> None:
    from content_factory.runners.local import make_context
    from content_factory.workflows.stages import stage_plan_story

    ctx = replace(
        make_context(project_dir=tmp_path / "project"),
        params={"explainer": "fixtures/explainer/ssd_nearly_full.json"},
    )
    out = stage_plan_story(ctx)
    story = ctx.project_dir / "story"
    assert out.facts["planner"] == "explainer" and out.facts["explainer_beats"] == 10
    assert json.loads((story / "explainer-check.json").read_text())["ok"]
    assert json.loads((story / "explainer.json").read_text()) == _load()
    claims = json.loads((story / "claims-to-verify.json").read_text())
    assert len(claims) == len(_load()["claims_to_verify"])
    written = StoryPlan.model_validate_json((story / "plan.json").read_text())
    assert len(written.beats) == 10


def test_plan_story_refuses_an_explainer_plan_that_fails_its_checks(tmp_path: Path) -> None:
    from content_factory.runners.local import make_context
    from content_factory.workflows.stages import stage_plan_story

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_failing()))
    ctx = replace(make_context(project_dir=tmp_path / "project"), params={"explainer": str(bad)})
    with pytest.raises(RuntimeError, match=r"explainer-check\.json"):
        stage_plan_story(ctx)
    assert not (ctx.project_dir / "story" / "plan.json").exists()
    assert not json.loads((ctx.project_dir / "story" / "explainer-check.json").read_text())["ok"]


def test_the_bridge_draws_an_objects_current_text_not_its_first() -> None:
    """An update is how the plan shows state changing; drawing the declared label would show the
    first state for the whole film (the first render did exactly that)."""
    plan = ExplainerPlan.model_validate(_load())
    # The last update in a beat wins: the diagram shows the state the beat ends on.
    updated = {
        (i, t): u.value
        for i, b in enumerate(plan.beats)
        for u in b.visual_updates
        if u.op == "update"
        for t in u.targets
    }
    assert updated, "the fixture has no update to test with"
    states = replay(plan)
    for (i, target), value in updated.items():
        assert states[i][1][target] == value


def test_node_labels_read_as_text() -> None:
    assert display_label("block A: live | live | stale") == "block A: live · live · stale"
    long = display_label(" | ".join(["live"] * 30), limit=40)
    assert long.endswith("…") and len(long) <= 40 and not long.rstrip("…").endswith(("·", " "))
