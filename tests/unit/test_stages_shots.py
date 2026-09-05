"""plan_shots and compile_controls stage executors: artifacts, determinism, settings switches."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.config import get_settings
from content_factory.schemas.dag import Stage
from content_factory.schemas.fixtures import sample_campaign, sample_shot_plan
from content_factory.schemas.shots import ControlBundle, ShotPlan
from content_factory.workflows.stages import (
    STAGE_EXECUTORS,
    StageContext,
    stage_compile_controls,
    stage_plan_shots,
)


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    campaign = sample_campaign()
    return StageContext(
        workspace_id=campaign.workspace_id,
        project_dir=tmp_path / "project",
        artifacts_dir=tmp_path / "artifacts",
        campaign=campaign,
        deliverable_id="dlv_image0000001",
        quality="smoke",
        dep_outputs={},
    )


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch):
    def _set(**env: str) -> None:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()  # type: ignore[attr-defined]

    yield _set
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_executors_registered() -> None:
    assert STAGE_EXECUTORS[Stage.plan_shots] is stage_plan_shots
    assert STAGE_EXECUTORS[Stage.compile_controls] is stage_compile_controls


def test_plan_shots_writes_plan_and_is_idempotent(ctx: StageContext) -> None:
    first = stage_plan_shots(ctx)
    second = stage_plan_shots(ctx)
    assert first.outputs_hash == second.outputs_hash
    plan = ShotPlan.model_validate_json((ctx.ddir() / "shots" / "plan.json").read_text())
    assert plan.content_hash() == first.outputs_hash
    assert first.facts["shots"] == len(plan.shots) > 0
    assert first.facts["planner"] == "story_presets"
    assert all((s.frame_count - 1) % 8 == 0 for s in plan.shots)


def test_plan_shots_fixture_planner(ctx: StageContext, settings_env) -> None:
    settings_env(CF__SHOTS__PLANNER="fixture")
    out = stage_plan_shots(ctx)
    assert out.outputs_hash == sample_shot_plan().content_hash()
    # staged_from lists the mocap clips retrieval put on characters. Empty here, because the
    # fixture planner stages by hand and does not consult the reference library.
    # A fixture plan is checked like any other, and this one has something to report: its opening
    # push-in starts far enough back that the figure measures 0.3427 of frame height even in 16:9,
    # and the estimate runs about 0.02 optimistic against rendered boxes. A hand-authored plan does
    # not get the benefit of the doubt.
    assert out.facts == {
        "shots": 2,
        "planner": "fixture",
        # The plan's own rate, reported so a mismatch with the story is visible in the run log as
        # well as raised. No story is planned in this test, so nothing to disagree with.
        "fps": 24,
        "frames": 194,
        "staged_from": [],
        "underframed": [{"shot_id": "shot_fixture0001", "body_fraction": 0.3427}],
        # No styled identity sheet has been built for this asset in this test's style, so the
        # plan names none. Whether a film needs one is anchor_references' business, and
        # review_assets is the gate — a plan is not the place to decide it.
        "identity_sheets": [],
    }


def test_compile_controls_motion_plan_is_byte_identical(ctx: StageContext) -> None:
    first = stage_compile_controls(ctx)
    controls = ctx.ddir() / "controls"
    manifest = json.loads((controls / "manifest.json").read_text())
    assert manifest["compiler"] == "motion_plan" and len(manifest["bundles"]) == 1
    bundle_path = ctx.ddir() / manifest["bundles"][0]["path"]
    bundle = ControlBundle.model_validate_json(bundle_path.read_text())
    assert bundle.content_hash() == manifest["bundles"][0]["bundle_hash"]
    assert {t.kind.value for t in bundle.tracks} == {"layout_boxes", "pose_skeleton"}
    assert bundle.subjects[0].segmentation_index == 1
    assert len(bundle.subjects[0].poses) == bundle.frame_count

    pngs = sorted(p for p in controls.rglob("*.png"))
    markers = sorted(p for p in controls.rglob("*.done.json"))
    assert len(pngs) == 2 * bundle.frame_count == len(markers)
    before = {p: p.read_bytes() for p in pngs + markers + [bundle_path]}

    second = stage_compile_controls(ctx)
    assert second.outputs_hash == first.outputs_hash
    assert {p: p.read_bytes() for p in before} == before
    assert first.facts["kinds"] == ["layout_boxes", "pose_skeleton"]

    marker = json.loads(markers[0].read_text())
    assert marker["input_hash"] == bundle.plan_hash
    assert len(marker["png_sha256"]) == 64


def test_compile_controls_blender_requires_a_shot_plan(ctx: StageContext, settings_env) -> None:
    settings_env(CF__CONTROLS__COMPILER="blender")
    with pytest.raises(RuntimeError, match="run plan_shots first"):
        stage_compile_controls(ctx)
