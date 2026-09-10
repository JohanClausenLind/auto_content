"""fix_video / upscale_video / interpolate over the per-shot clips with fake tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from content_factory.config import get_settings
from content_factory.controls import blender as blender_mod
from content_factory.postchain import runner as pc_runner
from content_factory.postchain.runner import PostChainError, run_tool
from content_factory.schemas.dag import Stage
from content_factory.schemas.fixtures import sample_campaign, sample_shot_plan
from content_factory.workflows.stages import (
    STAGE_EXECUTORS,
    StageContext,
    stage_compile_controls,
    stage_fix_video,
    stage_generate_anchor,
    stage_generate_video,
    stage_interpolate,
    stage_plan_shots,
    stage_upscale_video,
)
from tests.helpers.fake_blender import fake_subprocess_run
from tests.helpers.fake_postchain import failing_postchain_run, fake_postchain_run


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
def pipeline(ctx: StageContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Shots -> Blender (fake) -> anchors (mock) -> clips (mock), with the fake post-chain tools."""
    monkeypatch.setenv("CF__CONTROLS__COMPILER", "blender")
    monkeypatch.setenv("CF__SHOTS__PLANNER", "fixture")
    monkeypatch.setenv("CF__CONTROLS__ASSETS_ROOT", str(tmp_path / "no-assets"))
    monkeypatch.setattr(blender_mod, "SUBPROCESS_RUN", fake_subprocess_run)
    monkeypatch.setattr(pc_runner, "SUBPROCESS_RUN", fake_postchain_run)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    stage_plan_shots(ctx)
    stage_compile_controls(ctx)
    stage_generate_anchor(ctx)
    stage_generate_video(ctx)
    yield ctx
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _log(ctx: StageContext) -> list[str]:
    log = ctx.project_dir / ".stages" / "executions.log"
    return log.read_text().splitlines() if log.exists() else []


def test_post_chain_stages_registered() -> None:
    for stage in (Stage.fix_video, Stage.upscale_video, Stage.interpolate):
        assert stage in STAGE_EXECUTORS


def test_run_tool_reports_failures(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pc_runner, "SUBPROCESS_RUN", failing_postchain_run)
    with pytest.raises(PostChainError, match=r"exit 3.*simulated"):
        run_tool("rife", tmp_path, tmp_path / "out", {"factor": 2})


def test_fix_is_a_pass_through_until_ids_are_chosen(pipeline: StageContext) -> None:
    out = stage_fix_video(pipeline)
    assert all("skipped" in c for c in out.facts["clips"])
    assert not any(line.startswith("masks:") for line in _log(pipeline))


def test_fix_tracks_and_inpaints_chosen_ids(pipeline: StageContext, monkeypatch) -> None:
    monkeypatch.setenv("CF__POSTCHAIN__REMOVE_SEG_IDS", "[2]")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    out = stage_fix_video(pipeline)
    plan = sample_shot_plan()
    for shot, clip in zip(plan.shots, out.facts["clips"], strict=True):
        assert clip["clip"] == shot.shot_id and clip["cache_hit"] is False
        masks = sorted(
            (pipeline.ddir() / "video" / shot.shot_id / "masks" / "frames").glob("*.png")
        )
        fixed = sorted(
            (pipeline.ddir() / "video" / shot.shot_id / "fixed" / "frames").glob("*.png")
        )
        exploded = sorted((pipeline.ddir() / "video" / shot.shot_id / "frames").glob("*.png"))
        # the mock ffmpeg clip may carry one rounding frame more than frame_count; real LTX is exact
        assert len(exploded) >= shot.frame_count and len(masks) == len(fixed) == len(exploded)
        assert Image.open(masks[0]).mode == "P"
        state = json.loads((pipeline.ddir() / "video" / shot.shot_id / "chain.json").read_text())
        assert state["latest"].endswith("fixed/frames")
    again = stage_fix_video(pipeline)
    assert all(c["cache_hit"] for c in again.facts["clips"])


def test_upscale_then_interpolate_chain_and_final_mux(pipeline: StageContext) -> None:
    up = stage_upscale_video(pipeline)
    assert up.facts["clips"] == 2 and up.facts["resolution"] == 1080
    plan = sample_shot_plan()
    shot = plan.shots[0]
    upscaled = sorted(
        (pipeline.ddir() / "video" / shot.shot_id / "upscaled" / "frames").glob("*.png")
    )
    exploded = sorted((pipeline.ddir() / "video" / shot.shot_id / "frames").glob("*.png"))
    assert len(upscaled) == len(exploded) >= shot.frame_count
    assert Image.open(upscaled[0]).size == (
        shot.width * 2,
        shot.height * 2,
    )  # fake doubles; real = SeedVR2

    inter = stage_interpolate(pipeline)
    assert (
        inter.facts["engine"] == "rife" and inter.facts["factor"] == 2 and inter.facts["clips"] == 2
    )
    frames = sorted(
        (pipeline.ddir() / "video" / shot.shot_id / "interpolated" / "frames").glob("*.png")
    )
    assert len(frames) == len(exploded) * 2
    final = pipeline.ddir() / "exports" / "postchain.mp4"
    assert final.exists() and final.stat().st_size > 0
    state = json.loads((pipeline.ddir() / "video" / shot.shot_id / "chain.json").read_text())
    assert state["steps"] == ["upscaled", "interpolated"]
    # cached rerun: no new tool executions, same hash
    before = len(_log(pipeline))
    again = stage_interpolate(pipeline)
    assert again.outputs_hash == inter.outputs_hash and len(_log(pipeline)) == before


def test_interpolate_works_on_motion_plan_keyframes(
    ctx: StageContext, monkeypatch, tmp_path: Path
) -> None:
    from content_factory.workflows.stages import stage_generate_keyframes, stage_lock_generation

    monkeypatch.setenv("CF__CONTROLS__ASSETS_ROOT", str(tmp_path / "no-assets"))
    monkeypatch.setattr(pc_runner, "SUBPROCESS_RUN", fake_postchain_run)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        stage_generate_anchor(ctx)
        stage_lock_generation(ctx)
        stage_compile_controls(ctx)
        stage_generate_keyframes(ctx)
        out = stage_interpolate(ctx)
        assert out.facts["clips"] == 1
        frames = sorted((ctx.ddir() / "sequence" / "interpolated" / "frames").glob("*.png"))
        assert len(frames) == 16 and (ctx.ddir() / "exports" / "postchain.mp4").exists()
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]
