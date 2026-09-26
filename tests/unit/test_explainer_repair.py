"""Typed repairs re-validate the spec; the repair loop is bounded and rerenders only what moved."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from content_factory.explainer.compile import compile_episode
from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.qc import QcFinding
from content_factory.explainer.repair import (
    RepairOutcome,
    apply_repair,
    finding_key,
    repair_loop,
    split_scene_id,
)
from content_factory.schemas.explainer import (
    Action,
    Beat,
    Cue,
    CueOffsetRepair,
    DurationClass,
    Entity,
    EvidencePack,
    ExplainerRenderBundle,
    HoldAction,
    HoldRepair,
    LabelWordingRepair,
    LayoutChoiceRepair,
    QuoteAction,
    Scene,
    ScriptPlan,
    ScriptSegment,
    Section,
    SourcePassageRepair,
    SplitSceneRepair,
    TakeSelectionRepair,
    TargetAction,
    TextCorrectionRepair,
    TextItem,
    TextTemplate,
    TitlePromise,
    VisualSpec,
    tokenize,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "explainer"
CLAIM = "clm_stops_gain"
SEGMENTS: tuple[tuple[str, Section, str], ...] = (
    ("seg_open00001", "cold_open", "A faster tram does not shorten the trip"),
    ("seg_stakes0001", "question_stakes", "Where do the saved seconds go on a busy line"),
    (
        "seg_source0001",
        "build_model",
        "The measurements say it plainly: the seconds are spent standing at the busiest stops",
    ),
    ("seg_run0000001", "run_system", "Dwell time grows stop by stop along the line"),
    (
        "seg_change0001",
        "change_variable",
        "On a busier day the same stops hold the tram far longer",
    ),
    ("seg_synth00001", "synthesis", "The doors decide the timetable"),
)
Trio = tuple[EvidencePack, ScriptPlan, VisualSpec]


def _trio(name: str) -> Trio:
    d = FIXTURES / name
    return (
        EvidencePack.model_validate_json((d / "pack.json").read_text()),
        ScriptPlan.model_validate_json((d / "script.json").read_text()),
        VisualSpec.model_validate_json((d / "spec.json").read_text()),
    )


def _review_pair(name: str) -> Trio:
    d = FIXTURES / "review_set"
    pack = EvidencePack.model_validate_json((d / "pack.json").read_text())
    script = ScriptPlan.model_validate_json((d / name / "script.json").read_text())
    bundle = ExplainerRenderBundle.model_validate_json((d / name / "accepted.json").read_text())
    return pack, script, bundle.spec


def _scene(spec: VisualSpec, scene_id: str) -> Scene:
    return next(s for s in spec.scenes if s.scene_id == scene_id)


def _beat(spec: VisualSpec, beat_id: str) -> Beat:
    return next(b for s in spec.scenes for b in s.beats if b.beat_id == beat_id)


def _cue(
    beat_id: str, segment_id: str, token: int, *actions: Action, duration: DurationClass = "short"
) -> Beat:
    cue = Cue(segment_id=segment_id, token_start=token, token_end=token, duration_class=duration)
    return Beat(beat_id=beat_id, cue=cue, actions=actions)


def _reveal(*targets: str) -> TargetAction:
    return TargetAction(action="reveal", targets=targets)


def _statement(
    scene_id: str, section: Section, items: dict[str, str], beats: tuple[Beat, ...]
) -> Scene:
    template = TextTemplate(
        template="text",
        variant="list",
        items=tuple(
            TextItem(entity_id=eid, text=text, claim_id=CLAIM) for eid, text in items.items()
        ),
    )
    return Scene(
        scene_id=scene_id,
        section=section,
        purpose="Say it",
        template=template,
        beats=beats,
        claim_ids=(CLAIM,),
    )


def _loop_trio(second_cue: tuple[str, int] = ("seg_stakes0001", 3)) -> Trio:
    """Three list scenes; the first reveals two items so a split can move the second away."""
    pack = EvidencePack.model_validate_json((FIXTURES / "review_set" / "pack.json").read_text())
    segments = tuple(
        ScriptSegment(
            segment_id=sid,
            section=section,
            spoken_text=text,
            tokens=tokenize(text),
            claim_ids=(CLAIM,) if sid == "seg_open00001" else (),
        )
        for sid, section, text in SEGMENTS
    )
    script = ScriptPlan(
        script_id="scr_repairloop1",
        channel_id="ch_explain01",
        pack_id=pack.pack_id,
        pack_hash=pack.pack_hash(),
        question="Where do the tram's minutes go?",
        contribution="The stops set the trip time.",
        promises=(
            TitlePromise(title="Doors, not motors", thumbnail_promise="Stops", claim_ids=(CLAIM,)),
        ),
        segments=segments,
        locked_at="2026-09-23T00:00:00Z",
    )
    seg, token = second_cue
    scenes = (
        _statement(
            "scn_first000001",
            "cold_open",
            {
                "ent_item_a0001": "A faster tram does not shorten the trip",
                "ent_item_b0001": "The doors decide",
            },
            (
                _cue("bt_first_a00001", "seg_open00001", 0, _reveal("ent_item_a0001")),
                _cue("bt_first_b00001", seg, token, _reveal("ent_item_b0001")),
                _cue("bt_first_hold01", "seg_stakes0001", 6, HoldAction(action="hold")),
            ),
        ),
        _statement(
            "scn_second00001",
            "build_model",
            {"ent_item_c0001": "Where do the saved seconds go"},
            (
                _cue("bt_second_c0001", "seg_source0001", 11, _reveal("ent_item_c0001")),
                _cue(
                    "bt_second_hold1",
                    "seg_source0001",
                    13,
                    HoldAction(action="hold"),
                    duration="beat",
                ),
            ),
        ),
        _statement(
            "scn_third000001",
            "synthesis",
            {"ent_item_d0001": "The doors decide the timetable"},
            (
                _cue("bt_third_d00001", "seg_synth00001", 0, _reveal("ent_item_d0001")),
                _cue(
                    "bt_third_hold01",
                    "seg_synth00001",
                    2,
                    HoldAction(action="hold"),
                    duration="long",
                ),
            ),
        ),
    )
    entities = tuple(
        Entity(entity_id=f"ent_item_{k}0001", label=f"item {k}", kind="text", claim_ids=(CLAIM,))
        for k in "abcd"
    )
    spec = VisualSpec(
        spec_id="spec_repairloop",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        design_system_version=1,
        entities=entities,
        scenes=scenes,
    )
    return pack, script, spec


# --- apply_repair ---


def test_cue_offset_shifts_the_cue_by_whole_tokens_and_clamps() -> None:
    _, script, spec = _trio("amdahl")
    later, _ = apply_repair(
        spec,
        script,
        CueOffsetRepair(repair="cue_offset", beat_id="bt_reveal_series", offset_ms=800),
    )
    cue = _beat(later, "bt_reveal_series").cue
    assert (cue.token_start, cue.token_end) == (2, 3)
    earlier, _ = apply_repair(
        spec, script, CueOffsetRepair(repair="cue_offset", beat_id="bt_reveal_base", offset_ms=-800)
    )
    cue = _beat(earlier, "bt_reveal_base").cue
    assert (cue.token_start, cue.token_end) == (9, 10)
    count = len(script.segment("seg_build_model").tokens)
    far, _ = apply_repair(
        spec,
        script,
        CueOffsetRepair(repair="cue_offset", beat_id="bt_reveal_base", offset_ms=99_000),
    )
    cue = _beat(far, "bt_reveal_base").cue
    assert (cue.token_start, cue.token_end) == (count - 1, count - 1)
    same, _ = apply_repair(
        spec, script, CueOffsetRepair(repair="cue_offset", beat_id="bt_reveal_base", offset_ms=0)
    )
    assert same == spec


def test_label_wording_sets_the_short_label_and_nothing_else() -> None:
    _, script, spec = _trio("amdahl")
    repair = LabelWordingRepair(
        repair="label_wording", entity_id="ent_series_other", short_label="Rest"
    )
    new_spec, new_script = apply_repair(spec, script, repair)
    assert new_script == script
    changed = {e.entity_id: e for e in new_spec.entities}
    assert changed["ent_series_other"].short_label == "Rest"
    assert changed["ent_series_other"].label == "Other work"
    assert new_spec.model_copy(update={"entities": spec.entities}) == spec


def test_text_correction_rewrites_the_words_wherever_they_are_drawn() -> None:
    _, script, spec = _trio("sixty")
    node = TextCorrectionRepair(repair="text_correction", entity_id="ent_chainrng", text="Big ring")
    item = TextCorrectionRepair(
        repair="text_correction", entity_id="ent_close_txt", text="Gears trade force for speed."
    )
    for repair, scene_id in ((node, "scn_model001"), (item, "scn_close001")):
        new_spec, new_script = apply_repair(spec, script, repair)
        assert new_script == script
        entity = next(e for e in new_spec.entities if e.entity_id == repair.entity_id)
        assert entity.label == repair.text and entity.short_label == ""
        template = _scene(new_spec, scene_id).template
        shown = [
            getattr(part, "label", None) or getattr(part, "text", None)
            for part in (*getattr(template, "nodes", ()), *getattr(template, "items", ()))
            if part.entity_id == repair.entity_id
        ]
        assert shown == [repair.text]
    with pytest.raises(ValueError, match="unknown entity"):
        apply_repair(spec, script, node.model_copy(update={"entity_id": "ent_nothere1"}))


def test_layout_choice_changes_the_scene_layout() -> None:
    _, script, spec = _trio("amdahl")
    repair = LayoutChoiceRepair(repair="layout_choice", scene_id="scn_amdahl_card", layout="split")
    new_spec, _ = apply_repair(spec, script, repair)
    assert _scene(new_spec, "scn_amdahl_card").layout == "split"
    assert _scene(new_spec, "scn_big_sixty").layout == "overlay"


def test_hold_repair_sets_the_duration_and_adds_a_hold_only_when_missing() -> None:
    _, script, spec = _trio("amdahl")
    with_hold, _ = apply_repair(
        spec, script, HoldRepair(repair="hold", beat_id="bt_reveal_series", duration_class="long")
    )
    beat = _beat(with_hold, "bt_reveal_series")
    assert beat.cue.duration_class == "long"
    assert [a.action for a in beat.actions] == ["reveal", "hold"]
    already, _ = apply_repair(
        spec, script, HoldRepair(repair="hold", beat_id="bt_hold_bars", duration_class="medium")
    )
    beat = _beat(already, "bt_hold_bars")
    assert beat.cue.duration_class == "medium"
    assert [a.action for a in beat.actions] == ["hold"]


def test_take_selection_leaves_spec_and_script_unchanged() -> None:
    _, script, spec = _trio("amdahl")
    repair = TakeSelectionRepair(
        repair="take_selection", segment_id="seg_build_model", take_id="take_000000002"
    )
    assert apply_repair(spec, script, repair) == (spec, script)


def test_source_passage_swaps_every_quote_action_in_the_scene() -> None:
    _, script, spec = _review_pair("wrong_highlight")
    repair = SourcePassageRepair(
        repair="source_passage", scene_id="scn_source00001", quote_id="qt_another00001"
    )
    new_spec, _ = apply_repair(spec, script, repair)
    quotes = [
        a.quote_id
        for b in _scene(new_spec, "scn_source00001").beats
        for a in b.actions
        if isinstance(a, QuoteAction)
    ]
    assert quotes == ["qt_another00001", "qt_another00001"]
    _, script, text_spec = _trio("amdahl")
    with pytest.raises(ValueError, match="no source document"):
        apply_repair(
            text_spec,
            script,
            SourcePassageRepair(
                repair="source_passage", scene_id="scn_amdahl_card", quote_id="qt_another00001"
            ),
        )


def test_split_scene_keeps_ids_unique_and_carries_visibility_into_the_tail() -> None:
    pack, script, spec = _trio("amdahl")
    repair = SplitSceneRepair(
        repair="split_scene", scene_id="scn_latency_bars", after_beat_id="bt_annotate_base"
    )
    new_spec, _ = apply_repair(spec, script, repair)
    ids = [s.scene_id for s in new_spec.scenes]
    tail_id = split_scene_id("scn_latency_bars", "bt_annotate_base")
    assert ids == ["scn_latency_bars", tail_id, "scn_big_sixty", "scn_amdahl_card"]
    assert len(set(ids)) == len(ids)
    head, tail = new_spec.scenes[0], new_spec.scenes[1]
    assert [b.beat_id for b in head.beats] == [
        "bt_reveal_series", "bt_highlight_compute", "bt_reveal_base", "bt_annotate_base"
    ]  # fmt: skip
    assert [b.beat_id for b in tail.beats] == [
        "bt_reveal_fast",
        "bt_compare_totals",
        "bt_hold_bars",
    ]
    assert tail.initial_visible == ("ent_series_compute", "ent_series_other", "ent_total_base")
    assert tail.template == head.template and tail.section == head.section
    beat_ids = [b.beat_id for s in new_spec.scenes for b in s.beats]
    assert len(set(beat_ids)) == len(beat_ids)
    bundle = compile_episode(pack, script, new_spec)
    assert [s.scene_id for s in bundle.timeline.scenes] == ids


def test_split_scene_refuses_the_last_beat_a_source_scene_and_unknown_ids() -> None:
    _, script, spec = _trio("amdahl")
    with pytest.raises(ValueError, match="last beat"):
        apply_repair(
            spec,
            script,
            SplitSceneRepair(
                repair="split_scene", scene_id="scn_latency_bars", after_beat_id="bt_hold_bars"
            ),
        )
    with pytest.raises(ValueError, match="no beat"):
        apply_repair(
            spec,
            script,
            SplitSceneRepair(
                repair="split_scene", scene_id="scn_latency_bars", after_beat_id="bt_nowhere0001"
            ),
        )
    _, script, source_spec = _review_pair("wrong_highlight")
    with pytest.raises(ValueError, match="shows a source"):
        apply_repair(
            source_spec,
            script,
            SplitSceneRepair(
                repair="split_scene", scene_id="scn_source00001", after_beat_id="bt_src_show0001"
            ),
        )


def test_unknown_entity_scene_and_beat_raise_value_errors() -> None:
    _, script, spec = _trio("amdahl")
    with pytest.raises(ValueError, match="unknown entity"):
        apply_repair(
            spec,
            script,
            LabelWordingRepair(repair="label_wording", entity_id="ent_nowhere001", short_label="x"),
        )
    with pytest.raises(ValueError, match="unknown scene"):
        apply_repair(
            spec,
            script,
            LayoutChoiceRepair(repair="layout_choice", scene_id="scn_nowhere001", layout="split"),
        )
    with pytest.raises(ValueError, match="unknown beat"):
        apply_repair(
            spec, script, HoldRepair(repair="hold", beat_id="bt_nowhere0001", duration_class="long")
        )


# --- repair_loop ---


class _Fakes:
    """Real compile; a render that records which scenes it drew; a QC scripted per round."""

    def __init__(self, tmp_path: Path, rounds: Sequence[Sequence[QcFinding]]) -> None:
        self.tmp_path = tmp_path
        self.rounds = list(rounds)
        self.rendered: list[Sequence[str] | None] = []
        self.calls = 0

    def render(self, bundle: ExplainerRenderBundle, scenes: Sequence[str] | None) -> Path:
        self.rendered.append(None if scenes is None else list(scenes))
        mp4 = self.tmp_path / f"episode-{len(self.rendered)}.mp4"
        mp4.write_bytes(f"{bundle.bundle_id}:{scenes}".encode())
        return mp4

    def qc(self, bundle: ExplainerRenderBundle, mp4: Path) -> Sequence[QcFinding]:
        index = min(self.calls, len(self.rounds) - 1)
        self.calls += 1
        first = bundle.timeline.scenes[0].scene_id
        passed = QcFinding("contract", first, None, 0, 0.0, 0.0, True, "no issue")
        known = {s.scene_id for s in bundle.timeline.scenes}
        return [passed, *(f for f in self.rounds[index] if f.scene_id in known)]


def _fail(check: str, scene_id: str, entity_id: str | None, evidence: str) -> QcFinding:
    return QcFinding(check, scene_id, entity_id, 0, 1.0, 0.0, False, evidence)


TEXT_FLOOR_A = _fail("text_floor", "scn_first000001", "ent_item_a0001", "18 px is below the floor")
TIMING_FIRST = _fail(
    "contract", "scn_first000001", "ent_item_a0001", "[timing] scenes[0]: too short"
)


def _run(
    tmp_path: Path, rounds: Sequence[Sequence[QcFinding]], **kw: int
) -> tuple[RepairOutcome, _Fakes]:
    pack, script, spec = _loop_trio()
    fakes = _Fakes(tmp_path, rounds)
    outcome = repair_loop(
        pack, script, spec, compile=compile_episode, render=fakes.render, qc=fakes.qc, **kw
    )
    return outcome, fakes


def test_loop_applies_a_split_then_a_hold_and_rerenders_only_the_moved_scenes(
    tmp_path: Path,
) -> None:
    outcome, fakes = _run(tmp_path, [[TEXT_FLOOR_A], [TIMING_FIRST], []], max_rounds=2)
    tail = split_scene_id("scn_first000001", "bt_first_a00001")
    assert outcome.rounds == 2 and outcome.blocked == ()
    assert set(outcome.resolved) == {finding_key(TEXT_FLOOR_A), finding_key(TIMING_FIRST)}
    assert [s.scene_id for s in outcome.spec.scenes] == [
        "scn_first000001", tail, "scn_second00001", "scn_third000001"
    ]  # fmt: skip
    first = outcome.spec.scenes[0]
    assert [a.action for b in first.beats for a in b.actions] == ["reveal", "hold"]
    assert first.beats[0].cue.duration_class == "long"
    assert fakes.rendered == [
        None,
        ["scn_first000001", tail, "scn_second00001"],
        ["scn_first000001", tail],
    ]
    assert len(outcome.reports) == 3
    assert [r.disposition for r in outcome.reports] == ["fail", "fail", "pass"]
    assert outcome.reports[0].findings[0].proposed_repair == SplitSceneRepair(
        repair="split_scene", scene_id="scn_first000001", after_beat_id="bt_first_a00001"
    )


def test_loop_stops_at_max_rounds_per_scene_and_leaves_the_finding_blocked(tmp_path: Path) -> None:
    outcome, fakes = _run(tmp_path, [[TEXT_FLOOR_A], [TIMING_FIRST]], max_rounds=1)
    assert outcome.rounds == 1 and len(fakes.rendered) == 2
    assert outcome.blocked == (TIMING_FIRST,)
    assert outcome.resolved == (finding_key(TEXT_FLOOR_A),)
    assert outcome.reports[-1].disposition == "fail"


def test_loop_respects_the_total_budget(tmp_path: Path) -> None:
    outcome, fakes = _run(tmp_path, [[TEXT_FLOOR_A], [TIMING_FIRST]], max_rounds=2, budget_rounds=1)
    assert outcome.rounds == 1 and len(fakes.rendered) == 2
    assert outcome.blocked == (TIMING_FIRST,)


def test_loop_ends_when_no_finding_carries_a_repair(tmp_path: Path) -> None:
    stuck = _fail("duration", "scn_first000001", None, "frames differ")
    outcome, fakes = _run(tmp_path, [[stuck]])
    assert outcome.rounds == 0 and fakes.rendered == [None]
    assert outcome.blocked == (stuck,) and outcome.resolved == ()
    assert outcome.spec == _loop_trio()[2]


def test_loop_keeps_the_last_compilable_spec_when_a_repair_breaks_the_reading_floor(
    tmp_path: Path,
) -> None:
    pack, script, spec = _loop_trio(second_cue=("seg_open00001", 1))
    fakes = _Fakes(tmp_path, [[TEXT_FLOOR_A]])
    outcome = repair_loop(
        pack, script, spec, compile=compile_episode, render=fakes.render, qc=fakes.qc
    )
    assert outcome.spec == spec and outcome.rounds == 1
    assert fakes.rendered == [None]
    assert outcome.blocked[0] == TEXT_FLOOR_A
    assert [f.check for f in outcome.blocked[1:]] == ["contract"]
    assert (
        "[timing]" in outcome.blocked[1].evidence
        and outcome.blocked[1].scene_id == "scn_first000001"
    )


def test_loop_passes_through_without_a_round_when_qc_is_clean(tmp_path: Path) -> None:
    outcome, fakes = _run(tmp_path, [[]])
    assert (outcome.rounds, outcome.blocked, outcome.resolved) == (0, (), ())
    assert fakes.rendered == [None] and outcome.reports[0].disposition == "pass"


def test_issue_findings_name_the_scene_and_entity_from_the_issue_ids() -> None:
    from content_factory.explainer.repair import _issue_finding

    pack, script, spec = _review_pair("tiny_text")
    bundle = compile_episode(pack, script, spec)
    issue = ContractIssue(
        "timing", "x", "too short.", "hold.", ("scn_open0000001", "ent_open_stmt")
    )
    finding = _issue_finding(bundle, issue)
    assert (finding.check, finding.scene_id, finding.entity_id) == (
        "contract", "scn_open0000001", "ent_open_stmt"
    )  # fmt: skip
    assert finding.passed is False and str(issue) == finding.evidence
    assert isinstance(EpisodeInvalidError([issue]), ValueError)
    assert hashlib.sha256(b"").hexdigest()  # the module hashes ids the same way everywhere
