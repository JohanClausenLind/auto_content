"""Phase 6 planner: section-by-section planning against a fake model, validated into a spec."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from content_factory.explainer.errors import EpisodeInvalidError
from content_factory.explainer.planner import (
    PLANNER_PROMPT_PATH,
    CapabilitySet,
    FakePlanner,
    OpenAICompatPlanner,
    PlannerPromptError,
    PlannerUnavailableError,
    PlanRecord,
    plan_episode,
    planner_context,
    prompt_template,
    render_prompt,
    schema_slice,
    section_schema,
)
from content_factory.schemas.explainer import (
    CaptureQuote,
    CaptureSection,
    ChannelEpisodeFormat,
    ChannelNarrationPolicy,
    ChannelProfile,
    EvidencePack,
    PageRect,
    PdfLocator,
    ScriptPlan,
    SourceCaptureManifest,
    Viewport,
    VisualSpec,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "explainer"
SHA = hashlib.sha256(b"capture").hexdigest()
PROFILE = ChannelProfile(
    channel_id="ch_explainer_demo",
    name="Explainer demo",
    audience="Curious general public, adults, not specialists.",
    topic_domain=("how systems around us work",),
    episode_format=ChannelEpisodeFormat(
        min_length_s=480, max_length_s=720, width=1920, height=1080, fps=30
    ),
    narration=ChannelNarrationPolicy(
        recorded_seconds_min=60, recorded_seconds_max=90, voice_kind="creator_recorded_plus_clone"
    ),
)
MANIFEST = SourceCaptureManifest(
    capture_id="cap_amdahl001",
    source_id="src_amdahl_1967",
    url="https://doi.org/10.1145/1465482.1465560",
    captured_at="2026-09-16T00:00:00Z",
    capture_kind="pdf",
    artifact_sha256=SHA,
    viewport=Viewport(width=1280, height=800),
    page_height_px=2000.0,
    text_sha256=SHA,
    extractor="pdf",
    extractor_version="1",
    sections=(
        CaptureSection(section_id="sec_abstract1", heading="Abstract", order=0, scroll_y_px=0),
    ),
    quotes=(
        CaptureQuote(
            quote_id="qt_thesis0001",
            section_id="sec_abstract1",
            text="the effort expended on achieving high parallel processing rates is wasted",
            locator=PdfLocator(kind="pdf", page=1),
            occurrence_index=0,
            line_rects=(PageRect(x=10, y=10, width=300, height=20),),
        ),
    ),
)
SECTIONS = (
    "cold_open",
    "question_stakes",
    "build_model",
    "run_system",
    "change_variable",
    "show_limits",
    "synthesis",
)


def load(name: str) -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    folder = FIXTURES / name
    return (
        EvidencePack.model_validate_json((folder / "pack.json").read_text()),
        ScriptPlan.model_validate_json((folder / "script.json").read_text()),
        VisualSpec.model_validate_json((folder / "spec.json").read_text()),
    )


def section_answers(spec: VisualSpec) -> dict[str, dict[str, Any]]:
    """Split a hand-made spec into the per-section answers a model would give."""
    answers: dict[str, dict[str, Any]] = {}
    declared: set[str] = set()
    for scene in spec.scenes:
        answer = answers.setdefault(
            scene.section, {"entities": [], "assets": [], "scenes": [], "capability_requests": []}
        )
        dumped = scene.model_dump(mode="json")
        text = json.dumps(dumped)
        answer["scenes"].append(dumped)
        for entity in spec.entities:
            if f'"{entity.entity_id}"' in text and entity.entity_id not in declared:
                declared.add(entity.entity_id)
                answer["entities"].append(entity.model_dump(mode="json"))
        for asset in spec.assets:
            if f'"{asset.asset_id}"' in text and asset.asset_id not in declared:
                declared.add(asset.asset_id)
                answer["assets"].append(
                    {"asset_id": asset.asset_id, "kind": "dataset", "dataset_id": asset.dataset_id}
                )
    return answers


def queue(answers: dict[str, dict[str, Any]], *extra: dict[str, Any]) -> list[str]:
    return [json.dumps(answers[s]) for s in SECTIONS] + [json.dumps(a) for a in extra]


def with_missing_entity(answer: dict[str, Any]) -> dict[str, Any]:
    broken = copy.deepcopy(answer)
    broken["scenes"][0]["initial_visible"] = ["ent_missing01"]
    return broken


@pytest.fixture
def sixty() -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    return load("sixty")


def test_schema_slice_drops_source_document_and_its_actions_without_captures() -> None:
    sliced = schema_slice(CapabilitySet.for_episode())
    defs = sliced["$defs"]
    templates = defs["Scene"]["properties"]["template"]
    actions = defs["Beat"]["properties"]["actions"]["items"]
    assert set(templates["discriminator"]["mapping"]) == {"chart", "diagram", "text"}
    assert {"$ref": "#/$defs/SourceDocumentTemplate"} not in templates["oneOf"]
    source_only = {"show_source", "scroll_to", "focus_passage", "highlight_quote"}
    assert not source_only & set(actions["discriminator"]["mapping"])
    for gone in ("SourceDocumentTemplate", "ShowSourceAction", "ScrollToAction", "QuoteAction"):
        assert gone not in defs
    assert "clear_highlight" in defs["TargetAction"]["properties"]["action"]["enum"]


def test_schema_slice_keeps_source_document_when_captures_exist() -> None:
    sliced = schema_slice(CapabilitySet.for_episode([MANIFEST]))
    mapping = sliced["$defs"]["Beat"]["properties"]["actions"]["items"]["discriminator"]["mapping"]
    assert "SourceDocumentTemplate" in sliced["$defs"]
    assert {"show_source", "scroll_to", "focus_passage", "highlight_quote"} <= set(mapping)


def test_schema_slice_still_describes_the_amdahl_spec() -> None:
    _, _, spec = load("amdahl")
    sliced = schema_slice(CapabilitySet.for_episode())
    defs = sliced["$defs"]
    refs = {ref for ref in _all_refs(sliced)}
    assert refs <= {f"#/$defs/{name}" for name in defs}
    templates = set(defs["Scene"]["properties"]["template"]["discriminator"]["mapping"])
    actions = set(defs["Beat"]["properties"]["actions"]["items"]["discriminator"]["mapping"])
    assert {s.template.template for s in spec.scenes} <= templates
    assert {a.action for s in spec.scenes for b in s.beats for a in b.actions} <= actions
    assert set(sliced["properties"]) == set(VisualSpec.model_fields)
    assert VisualSpec.model_validate(spec.model_dump(mode="json")) == spec


def test_schema_slice_is_deterministic_and_never_shares_state() -> None:
    caps = CapabilitySet.for_episode()
    first = schema_slice(caps)
    first["$defs"].clear()
    assert schema_slice(caps) == schema_slice(caps)
    assert schema_slice(caps)["$defs"]


def test_section_schema_offers_only_pack_datasets_and_no_sha256(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, _ = sixty
    schema = section_schema(
        schema_slice(CapabilitySet.for_episode()), pack, script, section="run_system"
    )
    asset = schema["$defs"]["PlannedAsset"]["properties"]
    assert "sha256" not in asset
    assert asset["kind"]["enum"] == ["dataset"]
    assert asset["dataset_id"]["anyOf"][0]["enum"] == ["ds_gear_teeth", "ds_gear_ratios"]
    assert schema["$defs"]["Cue"]["properties"]["segment_id"]["enum"] == ["seg_run000001"]
    assert schema["$defs"]["Scene"]["properties"]["section"]["enum"] == ["run_system"]
    assert schema["required"] == ["entities", "assets", "scenes", "capability_requests"]


def test_planner_context_holds_one_section_and_nothing_else(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    context = planner_context(
        PROFILE, pack, script, [MANIFEST], section="run_system", entities=spec.entities[:2]
    )
    assert set(context) == {
        "channel",
        "section",
        "claims",
        "datasets",
        "segments",
        "captures",
        "entities",
        "capabilities",
    }
    assert [s["segment_id"] for s in context["segments"]] == ["seg_run000001"]
    tokens = context["segments"][0]["tokens"]
    assert tokens[0] == {"i": 0, "token": script.segment("seg_run000001").tokens[0]}
    assert context["entities"] == [
        {"entity_id": e.entity_id, "label": e.label, "kind": e.kind} for e in spec.entities[:2]
    ]
    assert set(context["claims"][0]) == {
        "claim_id",
        "statement",
        "short_label",
        "value",
        "unit",
        "epistemic_class",
    }
    assert context["captures"][0]["quotes"][0]["quote_id"] == "qt_thesis0001"
    assert [c["template"] for c in context["capabilities"]][-1] == "source_document"


def test_prompt_is_the_planner_doc_verbatim_with_the_scoped_context(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    fake = FakePlanner(queue(section_answers(spec)))
    plan_episode(PROFILE, pack, script, model=fake)
    system = fake.calls[0].system
    template = prompt_template()
    for chunk in template.replace("{{schema_slice}}", "{{context}}").split("{{context}}"):
        assert chunk.strip() in system
    assert "seg_coldopen1" in system
    assert "seg_question1" not in system
    assert script.question not in system
    assert pack.sources[0].url not in system
    assert "{{context}}" not in system and "{{schema_slice}}" not in system
    assert fake.calls[0].json_schema is not None
    assert json.dumps(fake.calls[0].json_schema, indent=2, ensure_ascii=False) in system


def test_render_prompt_refuses_a_doc_missing_a_placeholder(tmp_path: Path) -> None:
    doc = tmp_path / "prompt.md"
    doc.write_text(PLANNER_PROMPT_PATH.read_text().replace("{{schema_slice}}", "the schema"))
    with pytest.raises(PlannerPromptError, match="schema_slice"):
        render_prompt({}, {}, doc)


def test_render_prompt_never_substitutes_a_placeholder_inside_the_context() -> None:
    prompt = render_prompt({"claims": ["{{schema_slice}}"]}, {"title": "SLICE"})
    assert '"{{schema_slice}}"' in prompt
    assert prompt.count("SLICE") == 1


def test_plan_episode_assembles_sections_in_script_order(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    fake = FakePlanner(queue(section_answers(spec)))
    planned = plan_episode(PROFILE, pack, script, model=fake)
    assert len(fake.calls) == len(SECTIONS)
    assert planned.scenes == spec.scenes
    assert {e.entity_id: e for e in planned.entities} == {e.entity_id: e for e in spec.entities}
    assert planned.spec_id == f"vs_{script.script_hash()[:16]}"
    assert planned.script_hash == script.script_hash()
    assert planned.pack_hash == pack.pack_hash()


def test_later_sections_see_earlier_entities_in_the_registry(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    fake = FakePlanner(queue(section_answers(spec)))
    plan_episode(PROFILE, pack, script, model=fake)
    assert '"entity_id": "ent_open_txt"' not in fake.calls[0].system
    assert '"entity_id": "ent_open_txt"' in fake.calls[1].system
    assert '"entity_id": "ent_pedals_n"' in fake.calls[-1].system


def test_reemitted_identical_entity_is_kept_once_with_its_id(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    answers["synthesis"]["entities"].append(answers["cold_open"]["entities"][0])
    planned = plan_episode(PROFILE, pack, script, model=FakePlanner(queue(answers)))
    assert [e.entity_id for e in planned.entities].count("ent_open_txt") == 1


def test_redefined_entity_is_rejected_and_reasked(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    redefined = copy.deepcopy(answers["synthesis"])
    redefined["entities"].append({**answers["cold_open"]["entities"][0], "label": "Renamed"})
    fake = FakePlanner(
        [*queue({**answers, "synthesis": redefined}), json.dumps(answers["synthesis"])]
    )
    planned = plan_episode(PROFILE, pack, script, model=fake)
    assert "[conflicting_state]" in fake.calls[-1].user
    assert "ent_open_txt" in fake.calls[-1].user
    assert next(e for e in planned.entities if e.entity_id == "ent_open_txt").label != "Renamed"


def test_invalid_reference_triggers_exactly_one_retry_carrying_the_issue(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec], tmp_path: Path
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    bad = with_missing_entity(answers["cold_open"])
    fake = FakePlanner([*queue({**answers, "cold_open": bad}), json.dumps(answers["cold_open"])])
    record_path = tmp_path / "spec.plan.json"
    planned = plan_episode(PROFILE, pack, script, model=fake, record_path=record_path)
    record = PlanRecord.model_validate_json(record_path.read_text())
    assert len(fake.calls) == len(SECTIONS) + 1
    issue = record.calls[0].issues[0]
    assert issue.startswith("[invalid_reference]") and "ent_missing01" in issue
    assert issue in fake.calls[-1].user
    assert json.dumps(bad) in fake.calls[-1].user
    assert record.retries == 1 and record.spec_hash == planned.spec_hash()
    assert planned.scenes == spec.scenes


def test_failing_every_retry_round_raises_with_the_issues(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec], tmp_path: Path
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    bad = with_missing_entity(answers["cold_open"])
    fake = FakePlanner([*queue({**answers, "cold_open": bad}), json.dumps(bad), json.dumps(bad)])
    record_path = tmp_path / "spec.plan.json"
    with pytest.raises(EpisodeInvalidError) as raised:
        plan_episode(PROFILE, pack, script, model=fake, record_path=record_path)
    assert len(fake.calls) == len(SECTIONS) + 2
    assert raised.value.issues
    assert all("ent_missing01" in str(i) for i in raised.value.issues)
    record = PlanRecord.model_validate_json(record_path.read_text())
    assert record.spec_hash is None and record.retries == 2
    assert record.issues_seen == tuple(str(i) for i in raised.value.issues)


def test_answer_that_is_not_json_is_reasked(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    good = queue(answers)
    fake = FakePlanner(["Here is the plan: {", *good[1:], good[0]])
    plan_episode(PROFILE, pack, script, model=fake)
    assert "not one JSON object" in fake.calls[-1].user


def test_cue_outside_the_section_is_rejected(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    stray = copy.deepcopy(answers["question_stakes"])
    stray["scenes"][0]["beats"][0]["cue"]["segment_id"] = "seg_coldopen1"
    stray["scenes"][0]["beats"][0]["cue"]["token_start"] = 0
    stray["scenes"][0]["beats"][0]["cue"]["token_end"] = 0
    fake = FakePlanner(
        [*queue({**answers, "question_stakes": stray}), json.dumps(answers["question_stakes"])]
    )
    plan_episode(PROFILE, pack, script, model=fake)
    assert "not in section question_stakes" in fake.calls[-1].user


def test_asset_sha256_comes_from_the_dataset_not_the_model(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    forged = copy.deepcopy(answers["run_system"])
    forged["assets"][0]["sha256"] = "0" * 64
    fake = FakePlanner(
        [*queue({**answers, "run_system": forged}), json.dumps(answers["run_system"])]
    )
    planned = plan_episode(PROFILE, pack, script, model=fake)
    assert "[unknown_field]" in fake.calls[-1].user and "sha256" in fake.calls[-1].user
    datasets = {d.dataset_id: d for d in pack.datasets}
    for asset in planned.assets:
        assert asset.dataset_id is not None
        expected = hashlib.sha256(datasets[asset.dataset_id].canonical_json().encode()).hexdigest()
        assert asset.sha256 == expected
    assert {a.sha256 for a in planned.assets} == {a.sha256 for a in spec.assets}


def test_dataset_outside_the_pack_is_rejected(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    invented = copy.deepcopy(answers["run_system"])
    invented["assets"][0]["dataset_id"] = "ds_invented01"
    fake = FakePlanner(
        [*queue({**answers, "run_system": invented}), json.dumps(answers["run_system"])]
    )
    plan_episode(PROFILE, pack, script, model=fake)
    assert "ds_invented01, which the pack lacks" in fake.calls[-1].user


def test_capability_requests_are_kept_in_the_spec(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    answers = section_answers(spec)
    request = {
        "scene_id": "scn_model001",
        "intent": "Animate the chain turning both gears at their true ratio.",
        "fallback": "The static diagram with a trace along the chain edge.",
    }
    answers["build_model"]["capability_requests"] = [request]
    planned = plan_episode(PROFILE, pack, script, model=FakePlanner(queue(answers)))
    assert [r.model_dump(mode="json") for r in planned.capability_requests] == [request]
    assert planned.scenes == spec.scenes


def test_plan_record_replays_to_the_same_spec(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec], tmp_path: Path
) -> None:
    pack, script, spec = sixty
    record_path = tmp_path / "spec.plan.json"
    planned = plan_episode(
        PROFILE,
        pack,
        script,
        model=FakePlanner(queue(section_answers(spec))),
        record_path=record_path,
    )
    record = PlanRecord.model_validate_json(record_path.read_text())
    replay_path = tmp_path / "replay.plan.json"
    replayed = plan_episode(
        PROFILE, pack, script, model=FakePlanner.replaying(record), record_path=replay_path
    )
    assert replayed.spec_hash() == planned.spec_hash() == record.spec_hash
    again = PlanRecord.model_validate_json(replay_path.read_text())
    assert [c.prompt_sha256 for c in again.calls] == [c.prompt_sha256 for c in record.calls]
    assert [c.section for c in record.calls] == list(SECTIONS)
    assert record.model_id == "fake-planner" and record.retries == 0


def test_unfrozen_pack_fails_before_any_model_call(
    sixty: tuple[EvidencePack, ScriptPlan, VisualSpec],
) -> None:
    pack, script, spec = sixty
    fake = FakePlanner(queue(section_answers(spec)))
    with pytest.raises(EpisodeInvalidError, match="not frozen"):
        plan_episode(PROFILE, pack.model_copy(update={"frozen_at": None}), script, model=fake)
    assert fake.calls == []


def test_openai_compat_planner_asks_for_structured_json_without_thinking() -> None:
    seen: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})

    planner = OpenAICompatPlanner(
        "http://planner.test/v1/", "qwen-planner", transport=httpx.MockTransport(answer)
    )
    schema = {"type": "object"}
    assert planner.complete("sys", "usr", json_schema=schema, max_tokens=64) == '{"ok": 1}'
    body = seen[0]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "visual_plan", "schema": schema},
    }
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["model"] == "qwen-planner" and body["max_tokens"] == 64


def test_openai_compat_planner_reports_a_refusing_server() -> None:
    planner = OpenAICompatPlanner(
        "http://planner.test/v1",
        "qwen-planner",
        transport=httpx.MockTransport(lambda _: httpx.Response(503, text="loading")),
    )
    with pytest.raises(PlannerUnavailableError, match="503"):
        planner.complete("sys", "usr", json_schema=None, max_tokens=64)


def _all_refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return found + [r for value in node.values() for r in _all_refs(value)]
    if isinstance(node, list):
        return [r for value in node for r in _all_refs(value)]
    return []
