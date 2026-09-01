"""Revision Box apply/undo on a project directory: overlay revisions are append-only and undo
restores the exact previous overlay hash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.editor.apply import ApplyError, apply_fix_plan, current_overlay_hash, undo_last
from content_factory.editor.critique import map_feedback
from content_factory.editor.project_context import load_context
from content_factory.schemas.editing import FixPlan


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "prj_test000000001"
    (root / "deliverables" / "dlv_carousel0001").mkdir(parents=True)
    (root / "story").mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "project_id": "prj_test000000001",
                "workspace_id": "ws_demo00000001",
                "campaign_id": "cmp_x",
                "quality": "demo",
            }
        )
    )
    (root / "deliverables" / "dlv_carousel0001" / "copy.json").write_text(
        json.dumps(
            {
                "cards": [
                    {"card_id": "card_000000000001", "text": "one"},
                    {"card_id": "card_000000000002", "text": "two"},
                    {"card_id": "card_000000000003", "text": "three"},
                ]
            }
        )
    )
    return root


def test_feedback_to_apply_to_undo_roundtrip(project: Path) -> None:
    ctx = load_context(project)
    baseline = current_overlay_hash(project)
    outcome = map_feedback('card 2 should say "Better siting doubled output"', ctx)
    assert isinstance(outcome, FixPlan)
    assert outcome.impact.affected_unit_ids == ("card_000000000002",)
    applied = apply_fix_plan(project, outcome)
    assert applied.revision == 1
    overlay = json.loads((project / "edits" / "overlay.json").read_text())
    assert (
        overlay["copy"]["dlv_carousel0001"]["card_000000000002"] == "Better siting doubled output"
    )
    # Second edit stacks; history is append-only.
    outcome2 = map_feedback('card 3 should say "Sources at the end"', ctx)
    assert isinstance(outcome2, FixPlan)
    apply_fix_plan(project, outcome2)
    assert len(list((project / "edits" / "history").glob("*.json"))) == 2
    hash_after_two = current_overlay_hash(project)
    # Undo removes only the last edit.
    undone = undo_last(project)
    assert undone.revision == 3
    overlay = json.loads((project / "edits" / "overlay.json").read_text())
    assert "card_000000000003" not in overlay["copy"]["dlv_carousel0001"]
    assert (
        overlay["copy"]["dlv_carousel0001"]["card_000000000002"] == "Better siting doubled output"
    )
    assert current_overlay_hash(project) != hash_after_two
    # Undo to the beginning restores the empty overlay; further undo refuses.
    undo_last(project)
    assert json.loads((project / "edits" / "overlay.json").read_text()) == {}
    del baseline


def test_refusal_and_gates_never_apply(project: Path) -> None:
    ctx = load_context(project)
    refusal = map_feedback("remove the source citation", ctx)
    assert refusal.kind == "refusal"
    gate = map_feedback("also post this to TikTok please", ctx)
    assert gate.kind == "gate_required"
    with pytest.raises(ApplyError):
        apply_fix_plan(
            project,
            FixPlan.model_validate(
                {
                    "kind": "fix_plan",
                    "findings": [
                        {
                            "category": "tone",
                            "severity": "minor",
                            "target_unit_ids": ["card_000000000009"],
                            "summary": "x",
                        }
                    ],
                    "operations": [
                        {
                            "op": "replace_text_range",
                            "unit_id": "card_000000000009",
                            "start": 0,
                            "end": 0,
                            "replacement": "x",
                            "expected_before": None,
                        }
                    ],
                    "impact": {
                        "affected_unit_ids": ["card_000000000009"],
                        "invalidates": ["render"],
                        "reopens_evidence_validation": False,
                        "reopens_preflight_gate": False,
                    },
                    "plain_language": "x",
                    "estimated_cost_usd": 0,
                    "estimated_seconds": 1,
                }
            ),
        )
