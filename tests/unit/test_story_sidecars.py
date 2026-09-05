"""A hand-authored story fixture can bring its own datasets and source cards; the timeline bundle
then renders that story's numbers instead of the demo's."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from content_factory.runners.local import make_context
from content_factory.schemas.fixtures import sample_story_plan
from content_factory.schemas.render import RenderBundle
from content_factory.workflows import stages as st
from content_factory.workflows.stages import (
    StageContext,
    stage_align_words,
    stage_compile_timeline,
    stage_lock_script,
    stage_plan_story,
    stage_synthesize_narration,
)

REPO = Path(__file__).resolve().parents[2]


def _story_fixture(tmp_path: Path) -> Path:
    """The sample story rewritten as a fixture with sidecars, inside the repo (fixtures resolve
    relative to the repo root) under a scratch name."""
    plan = sample_story_plan()
    out_dir = REPO / "fixtures" / "story" / ".pytest-scratch"
    out_dir.mkdir(parents=True, exist_ok=True)
    story = out_dir / f"story_{tmp_path.name}.json"
    story.write_text(plan.model_dump_json(indent=1))
    story.with_suffix(".datasets.json").write_text(
        json.dumps(
            [
                {
                    "dataset_id": "ds_wind00000001",
                    "classification": "SOURCE_DATA",
                    "columns": ["year", "share_pct"],
                    "rows": [
                        {"year": "2018", "share_pct": 10.2},
                        {"year": "2025", "share_pct": 21},
                    ],
                    "unit": "%",
                    "source_ids": ["src_swea0000001"],
                    "label": "Wind share (fixture sidecar)",
                }
            ]
        )
    )
    story.with_suffix(".brand.json").write_text(
        json.dumps({"paper": "#0B0F14", "ink": "#F2F5F9", "accent": "#4CC3FF"})
    )
    story.with_suffix(".sources.json").write_text(
        json.dumps(
            [
                {
                    "source_id": "src_swea0000001",
                    "title": "Statistics and forecast Q4 2024",
                    "publisher": "Svensk Vindenergi",
                    "url": "https://swedishwindenergy.com/",
                    "accessed": "2026-09-06",
                }
            ]
        )
    )
    return story


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    return make_context(project_dir=tmp_path / "prj")


def test_story_sidecars_reach_the_render_bundle(ctx: StageContext, tmp_path: Path) -> None:
    story = _story_fixture(tmp_path)
    try:
        rel = str(story.relative_to(REPO))
        out = st.STAGE_EXECUTORS[st.Stage.plan_story](replace(ctx, params={"story": rel}))
        assert out.facts == {"beats": 4, "datasets": 1, "sources": 1, "brand": True}
        assert (ctx.project_dir / "data" / "ds_wind00000001.json").exists()
        cards = json.loads((ctx.project_dir / "research" / "source_cards.json").read_text())
        assert cards[0]["publisher"] == "Svensk Vindenergi"

        stage_lock_script(ctx)
        stage_synthesize_narration(ctx)
        stage_align_words(ctx)
        stage_compile_timeline(ctx)
        bundle = RenderBundle.model_validate_json(
            (ctx.ddir() / "timeline" / "bundle.json").read_text()
        )
        assert bundle.datasets["ds_wind00000001"].label == "Wind share (fixture sidecar)"
        assert set(bundle.sources) == {"src_swea0000001"}
        assert (bundle.brand.paper, bundle.brand.accent) == ("#0B0F14", "#4CC3FF")
    finally:
        for p in story.parent.glob(f"story_{tmp_path.name}*"):
            p.unlink()


def test_without_sidecars_the_demo_dataset_and_sources_are_used(ctx: StageContext) -> None:
    stage_plan_story(ctx)
    assert not (ctx.project_dir / "data").exists()
    stage_lock_script(ctx)
    stage_synthesize_narration(ctx)
    stage_align_words(ctx)
    stage_compile_timeline(ctx)
    bundle = RenderBundle.model_validate_json((ctx.ddir() / "timeline" / "bundle.json").read_text())
    assert "ds_wind00000001" in bundle.datasets and "src_energimynd01" in bundle.sources
    assert bundle.brand.paper is None  # the editorial default
