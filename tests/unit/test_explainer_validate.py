"""Gate B3: the compiler boundary names what is wrong, where, and the change that fixes it."""

from __future__ import annotations

import hashlib

import pytest

from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.validate import (
    check_bindings,
    check_narration_manifest,
    check_references,
    check_state,
    load_or_explain,
    validate_episode,
)
from content_factory.schemas.explainer import (
    Action,
    AssetRef,
    Beat,
    ChartTemplate,
    Claim,
    Cue,
    DatasetColumn,
    DatasetRow,
    Entity,
    EvidenceDataset,
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    FieldEncoding,
    NarrationManifest,
    NarrationTake,
    Quantity,
    QuoteAction,
    Scene,
    ScriptPlan,
    ScriptSegment,
    SeriesBinding,
    ShowSourceAction,
    SourceDocumentTemplate,
    TargetAction,
    TitlePromise,
    VisualSpec,
    VoiceSpec,
    tokenize,
)
from content_factory.schemas.research import EvidenceLocator

SHA = hashlib.sha256(b"fixture").hexdigest()
OTHER_SHA = hashlib.sha256(b"something else").hexdigest()
SPOKEN = "Solar capacity grew twenty four percent in one year"
SEGMENT = "seg_coldopen"
SOLAR = "ent_solar001"
SCENE = "scn_chart001"


def _claim(claim_id: str, statement: str, magnitude: float, unit: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=statement,
        epistemic_class="observation",
        evidence_ids=("evi_growth01",),
        value=Quantity(magnitude=magnitude, unit=unit),
        rationale="stated in the source table",
        checked_at="2026-09-15",
    )


def _pack(*, frozen: bool = True) -> EvidencePack:
    source = EvidenceSource(
        source_id="src_iea2025a",
        url="https://example.org/solar",
        canonical_url="https://example.org/solar",
        accessed_at="2026-09-15",
        content_sha256=SHA,
    )
    item = EvidenceItem(
        item_id="evi_growth01",
        source_id="src_iea2025a",
        passage="Capacity rose from 100 GW to 124 GW.",
        locator=EvidenceLocator(kind="char_range", start=0, end=36),
        quoted_at="2026-09-15",
    )
    dataset = EvidenceDataset(
        dataset_id="ds_capacity",
        columns=(
            DatasetColumn(name="year", kind="temporal"),
            DatasetColumn(name="gw", kind="quantitative", unit="GW"),
            DatasetColumn(name="tech", kind="nominal"),
        ),
        rows=(
            DatasetRow(key="2020", values=("2020", 100.0, "solar"), claim_ids=("clm_gw2020a1",)),
            DatasetRow(key="2021", values=("2021", 124.0, "solar"), claim_ids=("clm_gw2021a1",)),
        ),
    )
    pack = EvidencePack(
        pack_id="pack_solar2026",
        topic="Why solar capacity grew",
        sources=(source,),
        items=(item,),
        claims=(
            _claim("clm_growth24", "Solar capacity grew 24 percent in a year", 24, "percent"),
            _claim("clm_gw2020a1", "Solar capacity was 100 GW in 2020", 100, "GW"),
            _claim("clm_gw2021a1", "Solar capacity was 124 GW in 2021", 124, "GW"),
        ),
        datasets=(dataset,),
    )
    return pack.frozen("2026-09-15T00:00:00Z") if frozen else pack


def _script(pack: EvidencePack) -> ScriptPlan:
    segment = ScriptSegment(
        segment_id=SEGMENT,
        section="cold_open",
        spoken_text=SPOKEN,
        tokens=tokenize(SPOKEN),
        claim_ids=("clm_growth24",),
    )
    promise = TitlePromise(
        title="Solar's quiet leap", thumbnail_promise="+24% in a year", claim_ids=("clm_growth24",)
    )
    return ScriptPlan(
        script_id="scr_solar001",
        channel_id="ch_explain01",
        pack_id=pack.pack_id,
        pack_hash=pack.pack_hash(),
        question="Why did solar capacity grow so fast?",
        contribution="A model of the growth from one year of data.",
        promises=(promise,),
        segments=(segment,),
    )


def _beat(beat_id: str, start: int, end: int, *actions: Action) -> Beat:
    cue = Cue(segment_id=SEGMENT, token_start=start, token_end=end)
    return Beat(beat_id=beat_id, cue=cue, actions=actions)


def _chart_scene(
    *,
    claim_ids: tuple[str, ...] = ("clm_growth24",),
    beats: tuple[Beat, ...] | None = None,
    y_field: str = "gw",
) -> Scene:
    if beats is None:
        beats = (
            _beat("bt_reveal01", 0, 2, TargetAction(action="reveal", targets=(SOLAR,))),
            _beat("bt_hilite01", 3, 5, TargetAction(action="highlight", targets=(SOLAR,))),
        )
    template = ChartTemplate(
        template="chart",
        chart_kind="line",
        dataset_asset_id="ast_capacity",
        x=FieldEncoding(field="year", kind="temporal"),
        y=FieldEncoding(field=y_field, kind="quantitative", unit="GW"),
        series_field="tech",
        series=(SeriesBinding(value="solar", entity_id=SOLAR),),
    )
    return Scene(
        scene_id=SCENE,
        section="cold_open",
        purpose="Show the one-year jump",
        template=template,
        beats=beats,
        claim_ids=claim_ids,
    )


def _source_scene(*beats: Beat) -> Scene:
    template = SourceDocumentTemplate(
        template="source_document",
        capture_asset_id="ast_capture1",
        initial_section_id="sec_intro001",
    )
    return Scene(
        scene_id="scn_source01",
        section="build_model",
        purpose="Read the passage in its source",
        template=template,
        beats=beats,
    )


def _spec(script: ScriptPlan, pack: EvidencePack, *scenes: Scene) -> VisualSpec:
    scenes = scenes or (_chart_scene(),)
    assets = [
        AssetRef(asset_id="ast_capacity", kind="dataset", sha256=SHA, dataset_id="ds_capacity")
    ]
    if any(isinstance(s.template, SourceDocumentTemplate) for s in scenes):
        capture = AssetRef(
            asset_id="ast_capture1", kind="capture", sha256=SHA, capture_id="cap_iea2025a"
        )
        assets.append(capture)
    return VisualSpec(
        spec_id="spec_solar001",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        design_system_version=1,
        entities=(
            Entity(entity_id=SOLAR, label="Solar", kind="series", claim_ids=("clm_growth24",)),
        ),
        assets=tuple(assets),
        scenes=scenes,
    )


def _trio() -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    pack = _pack()
    script = _script(pack)
    return pack, script, _spec(script, pack)


def _only(issues: list[ContractIssue]) -> ContractIssue:
    assert len(issues) == 1, [str(i) for i in issues]
    return issues[0]


def test_valid_trio_yields_no_issues() -> None:
    pack, script, spec = _trio()
    assert check_bindings(pack, script, spec) == []
    assert check_references(pack, script, spec) == []
    assert check_state(spec) == []


def test_load_or_explain_returns_the_model_for_a_valid_payload() -> None:
    pack = _pack()
    loaded = load_or_explain(EvidencePack, pack.model_dump(mode="json"))
    assert isinstance(loaded, EvidencePack)
    assert loaded.pack_hash() == pack.pack_hash()


def test_scene_citing_an_unknown_claim_is_one_invalid_reference() -> None:
    pack, script, _ = _trio()
    spec = _spec(script, pack, _chart_scene(claim_ids=("clm_does_not_exist",)))
    issue = _only(check_references(pack, script, spec))
    assert issue.kind == "invalid_reference"
    assert SCENE in issue.message and "clm_does_not_exist" in issue.message
    assert "scenes[0]" in issue.where
    assert issue.fix


def test_cue_past_the_segment_names_the_token_count() -> None:
    pack, script, _ = _trio()
    late = _beat("bt_reveal01", 0, 20, TargetAction(action="reveal", targets=(SOLAR,)))
    spec = _spec(script, pack, _chart_scene(beats=(late,)))
    issue = _only(check_references(pack, script, spec))
    assert issue.kind == "invalid_reference"
    assert f"has {len(tokenize(SPOKEN))} tokens" in issue.message
    assert SEGMENT in issue.message and "bt_reveal01" in issue.message
    assert "0..8" in issue.fix


def test_chart_field_outside_the_dataset_names_the_columns() -> None:
    pack, script, _ = _trio()
    spec = _spec(script, pack, _chart_scene(y_field="capacity_gw"))
    issue = _only(check_references(pack, script, spec))
    assert issue.kind == "invalid_reference"
    assert "'capacity_gw'" in issue.message
    assert "columns: year, gw, tech" in issue.message
    assert issue.where.endswith("template.y.field")


def test_unknown_scene_field_lists_the_allowed_fields() -> None:
    _, _, spec = _trio()
    payload = spec.model_dump(mode="json")
    payload["scenes"][0]["transition"] = "fade"
    issues = load_or_explain(VisualSpec, payload)
    assert isinstance(issues, list)
    issue = _only(issues)
    assert issue.kind == "unknown_field"
    assert "scenes[0]" in issue.where
    assert "'transition'" in issue.message
    assert "beats" in issue.fix and "template" in issue.fix


def test_unknown_pack_field_is_reported_at_the_top_level() -> None:
    payload = _pack().model_dump(mode="json")
    payload["frozen"] = True
    issues = load_or_explain(EvidencePack, payload)
    assert isinstance(issues, list)
    issue = _only(issues)
    assert issue.kind == "unknown_field"
    assert issue.where == "EvidencePack.frozen"
    assert "frozen_at" in issue.fix


def test_hide_before_reveal_says_reveal_it_first() -> None:
    pack, script, _ = _trio()
    hide = _beat("bt_hide0001", 0, 2, TargetAction(action="hide", targets=(SOLAR,)))
    spec = _spec(script, pack, _chart_scene(beats=(hide,)))
    issue = _only(check_state(spec))
    assert issue.kind == "conflicting_state"
    text = str(issue)
    assert SCENE in text and "bt_hide0001" in text and SOLAR in text
    assert "reveal it first" in issue.fix


def test_second_highlight_quote_without_clear_names_both_quotes() -> None:
    pack, script, _ = _trio()
    scene = _source_scene(
        _beat("bt_show0001", 0, 1, ShowSourceAction(action="show_source")),
        _beat("bt_quote001", 2, 3, QuoteAction(action="highlight_quote", quote_id="qt_first001")),
        _beat("bt_quote002", 4, 5, QuoteAction(action="highlight_quote", quote_id="qt_second01")),
    )
    issue = _only(check_state(_spec(script, pack, scene)))
    assert issue.kind == "conflicting_state"
    assert "qt_first001" in issue.message and "qt_second01" in issue.message
    assert "bt_quote002" in issue.message
    assert "clear_highlight" in issue.fix


def test_quote_actions_before_show_source_are_flagged() -> None:
    pack, script, _ = _trio()
    scene = _source_scene(
        _beat("bt_quote001", 0, 1, QuoteAction(action="focus_passage", quote_id="qt_first001")),
    )
    issue = _only(check_state(_spec(script, pack, scene)))
    assert "qt_first001" in issue.message
    assert issue.fix == "show_source first."


def test_reveal_and_hide_in_one_beat_names_both_indexes() -> None:
    pack, script, _ = _trio()
    both = _beat(
        "bt_flip0001",
        0,
        2,
        TargetAction(action="reveal", targets=(SOLAR,)),
        TargetAction(action="hide", targets=(SOLAR,)),
    )
    spec = _spec(script, pack, _chart_scene(beats=(both,)))
    issue = _only(check_state(spec))
    assert issue.kind == "conflicting_state"
    assert "action 0" in issue.message and "action 1" in issue.message
    assert issue.where.endswith("actions[1]")


def test_spec_bound_to_a_stale_script_names_both_hashes() -> None:
    pack, script, spec = _trio()
    stale = spec.model_copy(update={"script_hash": OTHER_SHA})
    issue = _only(check_bindings(pack, script, stale))
    assert issue.kind == "stale_binding"
    assert OTHER_SHA[:12] in issue.message and script.script_hash()[:12] in issue.message
    assert issue.where == "VisualSpec.script_hash"
    assert "re-plan against the current script" in issue.fix


def test_unfrozen_pack_asks_to_freeze_it() -> None:
    pack = _pack(frozen=False)
    script = _script(pack)
    spec = _spec(script, pack)
    issue = _only(check_bindings(pack, script, spec))
    assert issue.kind == "stale_binding"
    assert "EvidencePack.frozen(at)" in issue.fix


def test_narration_manifest_missing_a_take_names_the_segment() -> None:
    _, script, _ = _trio()
    take = NarrationTake(
        take_id="tk_take00001",
        segment_ids=("seg_missing1",),
        kind="synthesized",
        audio_sha256=SHA,
        duration_ms=4000,
        sample_rate_hz=48000,
        transcript_similarity=0.99,
    )
    narration = NarrationManifest(
        manifest_id="nar_take0001",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        voice=VoiceSpec(kind="preset", voice_id="calm"),
        takes=(take,),
        total_duration_ms=4000,
        created_at="2026-09-15",
    )
    issues = check_narration_manifest(script, narration)
    assert [i.kind for i in issues] == ["invalid_reference", "invalid_reference"]
    assert issues[0].fix == f"record or synthesize segment {SEGMENT}."
    assert "seg_missing1" in issues[1].message


def test_validate_episode_lists_every_issue() -> None:
    pytest.importorskip("content_factory.explainer.evidence")
    pack, script, _ = _trio()
    hide = _beat("bt_hide0001", 0, 2, TargetAction(action="hide", targets=(SOLAR,)))
    spec = _spec(script, pack, _chart_scene(claim_ids=("clm_does_not_exist",), beats=(hide,)))
    with pytest.raises(EpisodeInvalidError) as info:
        validate_episode(pack, script, spec)
    text = str(info.value)
    assert "clm_does_not_exist" in text
    assert "reveal it first" in text
    assert len(info.value.issues) >= 2
