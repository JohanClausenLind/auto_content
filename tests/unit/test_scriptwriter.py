"""The script writer: one call, typed scenes, and nothing unsupported reaching generation.

`plan_story` loaded a hand-written fixture or the demo's, so a topic an operator actually has
became a film only if somebody wrote the twenty-one-kind scene grammar, the claim links and the
beat timings by hand. This is the largest genuine gap in the pipeline.

Most of what is tested here is the *refusals*, because that is the part that decides whether a
number a viewer reads is real. Four validators run before a plan exists, and each has a test that
proves a bad beat is dropped into `story/gaps.json` with its reason rather than rendered:

* a claim id the writer was never shown (a model cannot cite a claim into being);
* a figure in no dataset row and no cited claim;
* a scene kind with no renderer (today a placeholder card passes QC);
* an asset, source or dataset id that does not exist.
"""

from __future__ import annotations

import json
from pathlib import Path

from content_factory.config import get_settings
from content_factory.deliverables.documentary import episode_outline
from content_factory.models.scriptwriter import (
    NUMBER_TOLERANCE,
    WRITER_KINDS,
    DraftBeat,
    DraftPlan,
    claim_cards,
    draft_story_plan,
    validate_beat,
)
from content_factory.models.scriptwriter_eval import SCRIPTWRITER_FLOOR, score_draft
from content_factory.runners.demo import fixture_research
from content_factory.scenes.kinds import IMPLEMENTED_KINDS, PLACEHOLDER_KINDS
from content_factory.schemas.fixtures import sample_campaign, sample_dataset
from content_factory.schemas.research import VerificationStatus

OUTLINE = episode_outline(deliverable_id="dlv_short0000001", target_duration_s=600)
DATASET = sample_dataset()
_SOURCES, _EVIDENCE, CLAIMS = fixture_research("ws_demo00000001")
CARDS = claim_cards(CLAIMS)
CARD_IDS = {c.claim_id: c for c in CARDS}


def _beat(**kw) -> DraftBeat:
    base = {
        "section": "cold_open",
        "display_text": "Wind is now a fifth of the grid.",
        "scene_kind": "title",
        "headline": "A fifth of the grid",
    }
    return DraftBeat.model_validate({**base, **kw})


def _validate(beat: DraftBeat, **over) -> str:
    kwargs = {
        "cards": CARD_IDS,
        "datasets": {DATASET.dataset_id: DATASET},
        "dataset_values": {21.0, 34.9, 11.0},
        "source_ids": {"src_energimynd01", "src_svk00000001"},
        "asset_ids": set(),
        "allowed_kinds": set(WRITER_KINDS),
    }
    kwargs.update(over)
    return validate_beat(beat, **kwargs)  # type: ignore[arg-type]


# --- what the writer is shown -----------------------------------------------------------------


def test_only_usable_claims_become_cards() -> None:
    """A model given a fact and told not to use it uses it. What it cannot see, it cannot cite."""
    assert [c.claim_id for c in CARDS] == ["clm_wind0000001", "clm_wind0000002"]
    assert all(c.status in ("supported", "supported_with_caveat") for c in CARDS)
    unsupported = [
        c.model_copy(update={"status": VerificationStatus.unsupported, "evidence_ids": ()})
        for c in CLAIMS
    ]
    assert claim_cards(unsupported) == []
    assert len(claim_cards(unsupported, include_unsupported=True)) == 2
    # The verified figure travels with the card, so the writer can state it.
    assert any("21" in n for c in CARDS for n in c.numbers)


# --- the four refusals ------------------------------------------------------------------------


def test_a_claim_the_writer_was_never_shown_is_refused() -> None:
    assert _validate(_beat(claim_ids=("clm_wind0000001",))) == ""
    reason = _validate(_beat(claim_ids=("clm_invented001",)))
    assert "cites claims that were not offered" in reason and "clm_invented001" in reason


def test_a_figure_in_no_dataset_and_no_cited_claim_is_refused() -> None:
    """The one refusal that decides whether a number a viewer reads is real."""
    # In a dataset row.
    assert _validate(_beat(display_text="Wind reached 34.9 TWh.")) == ""
    # In a claim the beat cites (the fixture claim says 21%).
    assert (
        _validate(_beat(display_text="Wind is 21% of the grid.", claim_ids=("clm_wind0000001",)))
        == ""
    )
    # Rounding is editorial: 34.9 may be said as "about 35".
    assert _validate(_beat(display_text="Wind reached about 35 TWh.")) == ""
    # Invention is not.
    reason = _validate(_beat(display_text="Wind reached 62 TWh."))
    assert "is in no dataset row and in no claim" in reason
    # And a figure in the SCENE's own words is checked too, not only in the narration.
    assert "no dataset row" in _validate(_beat(headline="62 TWh of wind"))
    # A bare year is a period, not a quantity, so it is never asked to be in a dataset.
    assert _validate(_beat(display_text="In 2025 the grid changed.")) == ""


def test_a_scene_kind_with_no_renderer_is_refused_by_name() -> None:
    """Today a placeholder card passes QC, so a plan naming `ranking` ships a grey card where a
    chart was meant to be."""
    for kind in sorted(PLACEHOLDER_KINDS):
        reason = _validate(_beat(scene_kind=kind))
        assert "has no renderer" in reason, kind
    assert "not available in this run" in _validate(_beat(scene_kind="image"))
    assert _validate(_beat(scene_kind="image"), allowed_kinds={"image"}, asset_ids={"ast_1"}) != ""
    assert (
        _validate(
            _beat(scene_kind="image", asset_id="ast_1", alt_text="a turbine"),
            allowed_kinds={"image"},
            asset_ids={"ast_1"},
        )
        == ""
    )


def test_an_id_this_run_does_not_have_is_refused() -> None:
    # A quote must name the source it is attributed to: a quote attributed to a source that does
    # not exist is a fabricated citation.
    assert "must name the source" in _validate(_beat(scene_kind="quote", quote="x"))
    assert "names sources this run does not have" in _validate(
        _beat(scene_kind="quote", quote="x", source_ids=("src_madeup000001",))
    )
    assert _validate(_beat(scene_kind="quote", quote="x", source_ids=("src_svk00000001",))) == ""
    # A data-backed scene needs a dataset that exists.
    assert "needs a dataset_id" in _validate(_beat(scene_kind="big_number"))
    assert "which this run does not have" in _validate(
        _beat(scene_kind="big_number", dataset_id="ds_madeup000001")
    )
    assert _validate(_beat(scene_kind="big_number", dataset_id=DATASET.dataset_id)) == ""


def test_the_rounding_tolerance_is_the_line_between_editing_and_inventing() -> None:
    assert NUMBER_TOLERANCE == 0.02
    within = 34.9 * (1 + NUMBER_TOLERANCE * 0.9)
    beyond = 34.9 * (1 + NUMBER_TOLERANCE * 3)
    assert _validate(_beat(display_text=f"Wind reached {within:.2f} TWh.")) == ""
    assert "no dataset row" in _validate(_beat(display_text=f"Wind reached {beyond:.2f} TWh."))


# --- the deterministic scene builder ----------------------------------------------------------


def _fake_gateway(draft: DraftPlan):
    """A gateway whose model returns exactly this draft, so the builder is what is under test."""
    from content_factory.budgets.ledger import Cap, CostLedger, Scope
    from content_factory.hardware.probe import mock_inventory
    from content_factory.models.catalog import default_catalog, default_endpoints
    from content_factory.models.gateway import ModelGateway

    ledger = CostLedger()
    ledger.set_cap(Cap(scope=Scope.monthly, key="scriptwriter", limit_usd=1.0))
    return ModelGateway(
        catalog=default_catalog(),
        endpoints=default_endpoints(),
        ledger=ledger,
        inventory=mock_inventory("rtx3090"),
        completion_fn=lambda **kw: {
            "choices": [{"message": {"content": draft.model_dump_json()}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 400},
        },
    )


def _draft(*beats: DraftBeat):
    plan = DraftPlan(beats=beats)
    return draft_story_plan(
        sample_campaign(),
        OUTLINE,
        deliverable_id="dlv_short0000001",
        claims=CLAIMS,
        datasets={DATASET.dataset_id: DATASET},
        source_ids=("src_energimynd01", "src_svk00000001"),
        gateway=_fake_gateway(plan),
    )


def test_one_call_returns_beats_and_typed_scenes_that_validate() -> None:
    """A beat and its scene are one object in one response. Asking twice resends every beat as
    context and lets the second answer disagree with the first."""
    result = _draft(
        _beat(section="cold_open", scene_kind="title", headline="A fifth of the grid"),
        _beat(
            section="question_stakes",
            scene_kind="big_number",
            display_text="Wind supplied 21% of the grid.",
            claim_ids=("clm_wind0000001",),
            dataset_id=DATASET.dataset_id,
            column="share_pct",
            row_key="2025",
            unit="%",
            headline="of electricity from wind",
        ),
        _beat(
            section="build_model",
            scene_kind="bullet_sequence",
            display_text="Three things drove it.",
            headline="What drove it",
            bullets=("New turbines", "Better siting", "Cheaper finance"),
        ),
        _beat(
            section="synthesis",
            scene_kind="source_card",
            display_text="The numbers come from the grid operator.",
            source_ids=("src_energimynd01", "src_svk00000001"),
        ),
    )
    assert result.plan is not None and result.gaps == []
    plan = result.plan
    assert [s.kind for s in plan.scenes] == [
        "title",
        "big_number",
        "bullet_sequence",
        "source_card",
    ]
    # Every scene anchors its own beat, which StoryPlan's validator enforces — so this having
    # constructed at all is the proof.
    assert {s.beat_id for s in plan.scenes} == {b.beat_id for b in plan.beats}
    assert [b.order for b in plan.beats] == [0, 1, 2, 3]
    # The section rides on the beat, so a later pass can ask which beat is the cold open.
    assert [b.section for b in plan.beats] == [
        "cold_open",
        "question_stakes",
        "build_model",
        "synthesis",
    ]
    # Durations come from the outline's own word budgets at its words-per-minute.
    assert all(b.planned_duration_ms and b.planned_duration_ms > 200 for b in plan.beats)
    big_number = plan.scenes[1]
    assert big_number.value.dataset_id == DATASET.dataset_id  # type: ignore[union-attr]
    assert big_number.value.claim_id == "clm_wind0000001"  # type: ignore[union-attr]
    assert result.facts["beats"] == 4 and result.facts["gaps"] == 0
    assert result.facts["schema_enforced"] is True


def test_every_writer_kind_builds_a_valid_scene() -> None:
    """A kind the writer may ask for and the builder cannot build would be a gap on every run."""
    payloads = {
        "title": {},
        "section_intro": {"secondary": "Chapter one"},
        "big_number": {"dataset_id": DATASET.dataset_id, "column": "share_pct", "unit": "%"},
        "chart": {
            "dataset_id": DATASET.dataset_id,
            "chart": "bar",
            "x": "year",
            "y": ("share_pct",),
        },
        "bullet_sequence": {"bullets": ("One", "Two")},
        "quote": {
            "quote": "It changed fast.",
            "attribution": "the operator",
            "source_ids": ("src_svk00000001",),
        },
        "definition": {"secondary": "The share of generation from one source."},
        "callout": {"tone": "warning"},
        "comparison": {"left": "2018", "right": "2025"},
        "chapter_transition": {},
        "timeline": {"bullets": ("2018: eleven per cent", "2025: a fifth")},
        "source_card": {"source_ids": ("src_svk00000001",)},
        "outro": {"secondary": "Subscribe"},
    }
    assert set(payloads) == set(WRITER_KINDS)
    for kind, extra in payloads.items():
        result = _draft(_beat(scene_kind=kind, **extra))
        assert result.plan is not None, (kind, [g.reason for g in result.gaps])
        assert result.plan.scenes[0].kind == kind


def test_a_scene_the_builder_cannot_fill_becomes_a_gap_not_a_crash() -> None:
    result = _draft(
        _beat(scene_kind="bullet_sequence", bullets=()),
        _beat(scene_kind="timeline", bullets=("no colon here",)),
        _beat(scene_kind="definition", secondary=""),
    )
    assert result.plan is None  # nothing survived
    reasons = [g.reason for g in result.gaps]
    assert any("bullet_sequence with no bullets" in r for r in reasons)
    assert any("timeline needs at least two" in r for r in reasons)
    assert any("definition with no definition body" in r for r in reasons)
    assert all(g.section and g.scene_kind for g in result.gaps)


def test_the_surviving_beats_are_the_film_and_the_rest_are_the_operators_next_task() -> None:
    result = _draft(
        _beat(section="cold_open", scene_kind="title"),
        _beat(section="question_stakes", scene_kind="ranking", dataset_id=DATASET.dataset_id),
        _beat(section="build_model", scene_kind="callout", display_text="Wind hit 62 TWh."),
    )
    assert result.plan is not None
    assert len(result.plan.beats) == 1 and len(result.gaps) == 2
    assert result.facts["requested"] == 3 and result.facts["beats"] == 1
    # Renumbered contiguously: a plan with a hole in its beat order does not validate.
    assert [b.order for b in result.plan.beats] == [0]


# --- the evaluation pack ----------------------------------------------------------------------


def test_the_scorers_grade_the_model_and_not_the_validators() -> None:
    """Scoring the surviving plan would report a perfect "no invented numbers" by construction:
    the beats that invented one are exactly the beats that are no longer there."""
    good = _draft(
        _beat(section="cold_open", scene_kind="title", display_text="A fifth of the grid."),
        _beat(
            section="question_stakes",
            scene_kind="callout",
            display_text="Wind supplied 21% of it.",
            spoken_text="Wind supplied twenty-one per cent of it.",
            claim_ids=("clm_wind0000001",),
        ),
    )
    scores = score_draft(good, OUTLINE, datasets={DATASET.dataset_id: DATASET}, cards=CARDS)
    assert scores.usable == 1.0 and scores.grounded == 1.0 and scores.cited == 1.0
    assert scores.drawable == 1.0
    # A numeral in the display line and a spoken line supplied for it.
    assert scores.spoken == 1.0
    # Two of seven sections covered, and the beats are far under a 600 s episode's budgets.
    assert 0 < scores.arc < 1 and scores.budget < 1
    # The floor is the WORST axis, not the mean: a total failure on one must not hide behind the
    # others.
    assert scores.overall == min(scores.as_dict().values())

    bad = _draft(
        _beat(scene_kind="ranking", dataset_id=DATASET.dataset_id),
        _beat(scene_kind="callout", display_text="Wind hit 62 TWh.", claim_ids=("clm_nope000001",)),
    )
    bad_scores = score_draft(bad, OUTLINE, datasets={DATASET.dataset_id: DATASET}, cards=CARDS)
    assert bad_scores.usable == 0.0  # nothing survived
    assert bad_scores.grounded == 0.0  # the one figure is invented
    assert bad_scores.cited == 0.0  # the one citation does not exist
    assert bad_scores.drawable == 0.5  # one of two kinds has a renderer
    assert bad_scores.overall == 0.0


def test_the_floor_is_a_number_and_the_writer_is_off_until_it_is_cleared() -> None:
    assert SCRIPTWRITER_FLOOR.minimum == 0.8 and SCRIPTWRITER_FLOOR.higher_is_better
    assert SCRIPTWRITER_FLOOR.metric == "min_axis_score"
    # And nothing has cleared it, so the flag is off.
    assert get_settings().execution.local_scriptwriter is False


# --- the stage --------------------------------------------------------------------------------


def test_plan_story_uses_the_writer_behind_the_flag_and_writes_the_gaps(
    tmp_path: Path, monkeypatch
) -> None:
    """A run with the writer on must never silently fall back to the demo film: an operator who
    turned it on and got Swedish wind power would have no way to tell."""
    from content_factory.models import scriptwriter
    from content_factory.runners.local import make_context
    from content_factory.schemas.scenes import StoryPlan
    from content_factory.workflows.stages import stage_plan_story

    monkeypatch.setenv("CF__EXECUTION__LOCAL_SCRIPTWRITER", "true")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    ctx = make_context(project_dir=tmp_path / "project", brief={"topic": "wind in Sweden"})
    plan = DraftPlan(
        beats=(
            _beat(section="cold_open", scene_kind="title"),
            _beat(section="question_stakes", scene_kind="ranking", dataset_id="ds_x"),
        )
    )
    # The stage imports the writer inside the function, so patching the module attribute is what
    # it picks up. The fake gateway returns this exact draft, so what is under test is the stage's
    # own wiring: the outline it builds, the gaps it writes, the facts it reports.
    monkeypatch.setattr(scriptwriter, "draft_story_plan", lambda *a, **kw: _draft(*plan.beats))
    try:
        out = stage_plan_story(ctx)
        assert out.facts["writer_beats"] == 1 and out.facts["writer_gaps"] == 1
        written = StoryPlan.model_validate_json(
            (ctx.project_dir / "story" / "plan.json").read_text()
        )
        assert len(written.beats) == 1
        gaps = json.loads((ctx.project_dir / "story" / "gaps.json").read_text())
        assert len(gaps["gaps"]) == 1 and "has no renderer" in gaps["gaps"][0]["reason"]
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_the_implemented_kinds_list_covers_every_kind_the_writer_may_ask_for() -> None:
    assert set(WRITER_KINDS) <= IMPLEMENTED_KINDS
    assert not (set(WRITER_KINDS) & PLACEHOLDER_KINDS)
    # And the two lists together are the whole grammar: a kind in neither is a kind nobody decided
    # about, which is how `ranking` came to ship as a grey card.
    from content_factory.schemas.scenes import SceneSpec

    declared = {
        branch.model_fields["kind"].default
        for branch in SceneSpec.__origin__.__args__  # type: ignore[attr-defined]
    }
    assert declared == IMPLEMENTED_KINDS | PLACEHOLDER_KINDS
