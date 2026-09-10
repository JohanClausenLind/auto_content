"""A scene that names a source the bundle does not carry prints the raw id on screen.

Measured on `f02-penny` (2026-09-10), the first film this repo made with a quote card in it: the
card shipped reading **"Source: src_authored000001"** under the quotation, and all seven checks
in `qc_deliverable` passed. The renderer is doing what it was told — `bundle.sources[id]` misses,
so it falls back to printing the id — and nothing upstream of it said no.

`scenes/kinds.py` already names the stake: *"a quote attributed to a source that does not exist
is a fabricated citation."* The script writer is held to that. A hand-authored story goes round
the writer, which is exactly how the placeholder films got out, and there was a check for the
other kind of dangling reference (`scene_assets_present`) but not for this one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

STORY = {
    "schema_version": 1,
    "plan_id": "plan_srcref0001",
    "deliverable_id": "dlv_short0000001",
    "fps": 30,
    "width": 1920,
    "height": 1080,
    "beats": [
        {
            "beat_id": "bea_one00000",
            "order": 0,
            "display_text": "A bookseller put it plainly.",
            "spoken_text": None,
            "claim_ids": [],
            "measured_start_ms": None,
            "measured_end_ms": None,
            "words": [],
            "planned_duration_ms": 4000,
            "section": None,
        }
    ],
    "scenes": [
        {
            "scene_id": "scn_quote000001",
            "beat_id": "bea_one00000",
            "kind": "quote",
            "variant": "default",
            "emphasis": [],
            "quote": {"text": "It is the literature of the poor.", "claim_ids": []},
            "attribution": {"text": "A Victorian bookseller, 1863", "claim_ids": []},
            "source_id": "src_authored000001",
        }
    ],
    "handle_ms": 250,
    "min_scene_ms": 1200,
    "hook_text": None,
}


def _run_qc(tmp_path: Path, bundle_sources: dict):
    from content_factory.runners.local import make_context
    from content_factory.workflows.stages import stage_qc_deliverable

    ctx = make_context(project_dir=tmp_path / "project", brief={"topic": "penny dreadfuls"})
    story = ctx.project_dir / "story"
    story.mkdir(parents=True, exist_ok=True)
    (story / "plan.json").write_text(json.dumps(STORY))
    timeline = ctx.ddir() / "timeline"
    timeline.mkdir(parents=True, exist_ok=True)
    (timeline / "bundle.json").write_text(json.dumps({"assets": {}, "sources": bundle_sources}))
    try:
        stage_qc_deliverable(ctx)
        raised = False
    except RuntimeError:
        raised = True
    report = json.loads((ctx.ddir() / "qc" / "report.json").read_text())
    return raised, report["checks"]["scene_sources_present"]


def test_a_citation_to_a_source_that_does_not_exist_fails_the_deliverable(tmp_path: Path) -> None:
    raised, check = _run_qc(tmp_path, {})
    assert raised
    assert check["passed"] is False
    assert check["facts"]["dangling"] == [
        {"scene_id": "scn_quote000001", "source_id": "src_authored000001"}
    ]
    assert check["facts"]["scenes_naming_a_source"] == 1


def test_the_same_citation_passes_once_the_source_is_in_the_bundle(tmp_path: Path) -> None:
    card = {
        "source_id": "src_authored000001",
        "title": "Invented for a layout test",
        "publisher": "Content Factory fixtures",
        "url": "https://example.invalid/fixture",
        "accessed": "2026-09-10",
    }
    raised, check = _run_qc(tmp_path, {"src_authored000001": card})
    assert not raised
    assert check["passed"] is True
    assert check["facts"] == {
        "scenes_naming_a_source": 1,
        "in_the_bundle": 1,
        "dangling": [],
    }


def test_source_ids_plural_is_checked_too(tmp_path: Path) -> None:
    """`source_id` is a quote or a screenshot; `source_ids` is a source card or a chart. Both
    dangle the same way and both print the raw id."""
    story = dict(STORY)
    story["scenes"] = [
        {
            "scene_id": "scn_sources00001",
            "beat_id": "bea_one00000",
            "kind": "source_card",
            "variant": "default",
            "emphasis": [],
            "source_ids": ["src_realone00001", "src_ghost000001"],
        }
    ]
    from content_factory.runners.local import make_context
    from content_factory.workflows.stages import stage_qc_deliverable

    ctx = make_context(project_dir=tmp_path / "p2", brief={"topic": "sources"})
    (ctx.project_dir / "story").mkdir(parents=True, exist_ok=True)
    (ctx.project_dir / "story" / "plan.json").write_text(json.dumps(story))
    (ctx.ddir() / "timeline").mkdir(parents=True, exist_ok=True)
    (ctx.ddir() / "timeline" / "bundle.json").write_text(
        json.dumps({"assets": {}, "sources": {"src_realone00001": {}}})
    )
    with pytest.raises(RuntimeError):
        stage_qc_deliverable(ctx)
    check = json.loads((ctx.ddir() / "qc" / "report.json").read_text())["checks"][
        "scene_sources_present"
    ]
    assert check["facts"]["scenes_naming_a_source"] == 2
    assert check["facts"]["dangling"] == [
        {"scene_id": "scn_sources00001", "source_id": "src_ghost000001"}
    ]


def test_a_lane_with_no_bundle_is_not_asked(tmp_path: Path) -> None:
    """A generative lane cuts its own frames and writes no timeline bundle. There is nothing to
    check against, and inventing a failure there would be worse than staying quiet."""
    from content_factory.runners.local import make_context
    from content_factory.workflows.stages import stage_qc_deliverable

    ctx = make_context(project_dir=tmp_path / "p3", brief={"topic": "no bundle"})
    (ctx.project_dir / "story").mkdir(parents=True, exist_ok=True)
    (ctx.project_dir / "story" / "plan.json").write_text(json.dumps(STORY))
    stage_qc_deliverable(ctx)
    checks = json.loads((ctx.ddir() / "qc" / "report.json").read_text())["checks"]
    assert "scene_sources_present" not in checks
