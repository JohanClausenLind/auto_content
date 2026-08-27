"""Revision Box acceptance fixtures (16.5): complaint -> FixPlan on the right unit; ambiguity ->
question; policy-violating request -> refusal; factual change -> gate."""

from __future__ import annotations

from content_factory.editor.critique import ArtifactContext, ArtifactUnit, UnitKind, map_feedback
from content_factory.schemas.editing import ClarifyingQuestion, FixPlan, GateRequired, Refusal

CTX = ArtifactContext(
    artifact_id="art_video0000001",
    revision_hash="0" * 64,
    units=(
        ArtifactUnit("scn_intro000001", UnitKind.scene, "intro scene", 0, duration_frames=150),
        ArtifactUnit("scn_chart000001", UnitKind.chart_scene, "GDP chart", 1, duration_frames=240),
        ArtifactUnit("scn_outro000001", UnitKind.scene, "outro", 2, duration_frames=90),
        ArtifactUnit("txt_hook0000001", UnitKind.text, "hook copy", 0, claim_linked=False),
    ),
)


def test_chart_complaint_produces_fix_plan_touching_only_that_scene() -> None:
    out = map_feedback("the chart is unreadable on mobile", CTX)
    assert isinstance(out, FixPlan)
    assert out.impact.affected_unit_ids == ("scn_chart000001",)
    assert all(getattr(op, "scene_id", None) == "scn_chart000001" for op in out.operations)
    assert "evidence" not in out.impact.invalidates
    assert out.estimated_cost_usd == 0.0


def test_pacing_complaint_retimes_only_the_intro() -> None:
    out = map_feedback("intro too slow, get to the point", CTX)
    assert isinstance(out, FixPlan)
    assert out.impact.affected_unit_ids == ("scn_intro000001",)
    assert out.operations[0].op == "retime_beat"
    assert out.operations[0].duration_frames == 90  # type: ignore[union-attr]


def test_ambiguous_chart_reference_asks_one_question() -> None:
    two_charts = ArtifactContext(
        artifact_id=CTX.artifact_id,
        revision_hash=CTX.revision_hash,
        units=(*CTX.units, ArtifactUnit("scn_chart000002", UnitKind.chart_scene, "jobs chart", 3)),
    )
    out = map_feedback("the chart labels are too small", two_charts)
    assert isinstance(out, ClarifyingQuestion)
    assert set(out.candidate_unit_ids) == {"scn_chart000001", "scn_chart000002"}


def test_removing_citations_is_refused_with_policy_reason() -> None:
    out = map_feedback("remove the source citation at the bottom", CTX)
    assert isinstance(out, Refusal)
    assert out.policy == "citations_required"


def test_changing_a_figure_surfaces_the_evidence_gate() -> None:
    out = map_feedback("change the number to 3.2 percent", CTX)
    assert isinstance(out, GateRequired)
    assert out.gate == "evidence"


def test_adding_destinations_surfaces_publish_scope_gate() -> None:
    out = map_feedback("also post this to TikTok", CTX)
    assert isinstance(out, GateRequired)
    assert out.gate == "publish_scope"


def test_unknown_feedback_asks_rather_than_guessing() -> None:
    out = map_feedback("hmm, something feels off", CTX)
    assert isinstance(out, ClarifyingQuestion)
