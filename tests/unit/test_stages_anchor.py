"""generate_anchor / lock_generation / keyframes executors with the mock backend, fed by the fake
Blender compiler (anchors per shot per anchor frame) and by the builtin MotionPlan path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from content_factory.config import get_settings
from content_factory.controls import blender as blender_mod
from content_factory.schemas.dag import Stage
from content_factory.schemas.fixtures import sample_campaign, sample_shot_plan
from content_factory.schemas.sequences import GenerationLock
from content_factory.workflows.stages import (
    STAGE_EXECUTORS,
    StageContext,
    stage_compile_controls,
    stage_drift_qc,
    stage_generate_anchor,
    stage_generate_keyframes,
    stage_lock_generation,
    stage_package_sequence,
    stage_plan_shots,
)
from tests.helpers.fake_blender import fake_subprocess_run


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
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def _set(**kw: str) -> None:
        for k, v in kw.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()  # type: ignore[attr-defined]

    # no turnaround assets in the test environment -> no identity references
    _set(CF__CONTROLS__ASSETS_ROOT=str(tmp_path / "no-assets"))
    yield _set
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _log(ctx: StageContext) -> list[str]:
    log = ctx.project_dir / ".stages" / "executions.log"
    return log.read_text().splitlines() if log.exists() else []


def test_all_sequence_stages_have_executors() -> None:
    for stage in (
        Stage.generate_anchor,
        Stage.lock_generation,
        Stage.generate_keyframes,
        Stage.drift_qc,
        Stage.package_sequence,
    ):
        assert stage in STAGE_EXECUTORS


def test_anchors_per_shot_from_blender_controls(ctx: StageContext, env, monkeypatch) -> None:
    env(CF__CONTROLS__COMPILER="blender", CF__SHOTS__PLANNER="fixture")
    monkeypatch.setattr(blender_mod, "SUBPROCESS_RUN", fake_subprocess_run)
    stage_plan_shots(ctx)
    stage_compile_controls(ctx)
    out = stage_generate_anchor(ctx)
    plan = sample_shot_plan()
    assert out.facts == {
        "anchors": 4,
        "backend": "mock-reference-edit",
        "shots": 2,
        "cache_hits": 0,
        # One attempt each. Above the anchor count means the deterministic blocker checks sent
        # frames back, which is GPU time spent on frames that arrived unusable.
        "attempts": 4,
    }
    manifest = json.loads((ctx.ddir() / "anchors" / "manifest.json").read_text())
    assert [s["shot_id"] for s in manifest["shots"]] == [s.shot_id for s in plan.shots]
    for shot_entry, shot in zip(manifest["shots"], plan.shots, strict=True):
        assert [f["frame_index"] for f in shot_entry["frames"]] == list(shot.anchor_frames)
        for f in shot_entry["frames"]:
            png = ctx.ddir() / f["path"]
            assert Image.open(png).size == (shot.width, shot.height)
            marker = json.loads(png.with_suffix(".done.json").read_text())
            # Two references by default: the OpenPose skeleton for the pose and depth for the
            # figure's shape. The skeleton alone left the frame review's midtone check failing at
            # 0.293 of the frame; depth took it to 0.460 and kept the staged stride. What is *not*
            # here is an identity reference — the model reads every reference as subject material,
            # so sending the Blender clay render makes it draw clay people. See
            # ImageSequenceSettings.anchor_references.
            assert marker["references"] == 2 and marker["layout_boxes"] == 0
            assert marker["reference_kinds"] == ["pose_skeleton", "depth"]
    assert sum(1 for line in _log(ctx) if line.startswith("anchor:")) == 4

    # cache: identical inputs -> no new generations, same hash
    again = stage_generate_anchor(ctx)
    assert again.outputs_hash == out.outputs_hash and again.facts["cache_hits"] == 4
    assert sum(1 for line in _log(ctx) if line.startswith("anchor:")) == 4

    lock_out = stage_lock_generation(ctx)
    lock = GenerationLock.model_validate_json((ctx.ddir() / "sequence" / "lock.json").read_text())
    assert lock.reference_asset_sha256 == manifest["shots"][0]["frames"][0]["sha256"]
    assert lock.width == plan.shots[0].width and lock_out.outputs_hash == lock.content_hash()
    # blender-compiled shots keyframe through anchors: the MotionPlan stages step aside honestly
    assert "skipped" in stage_generate_keyframes(ctx).facts
    assert "skipped" in stage_drift_qc(ctx).facts
    assert "skipped" in stage_package_sequence(ctx).facts


def test_anchor_prompt_and_conditioning_change_the_cache_key(
    ctx: StageContext, env, monkeypatch
) -> None:
    env(CF__CONTROLS__COMPILER="blender", CF__SHOTS__PLANNER="fixture")
    monkeypatch.setattr(blender_mod, "SUBPROCESS_RUN", fake_subprocess_run)
    stage_plan_shots(ctx)
    stage_compile_controls(ctx)
    first = stage_generate_anchor(ctx)
    env(CF__IMAGE_SEQUENCES__ANCHOR_STYLE_PROMPT="charcoal storyboard sketch on rough grey paper")
    second = stage_generate_anchor(ctx)
    # the mock backend ignores the prompt, so the pixels match; the cache key must not
    assert second.facts["cache_hits"] == 0 and first.facts["cache_hits"] == 0
    shot_id = sample_shot_plan().shots[0].shot_id
    marker = json.loads((ctx.ddir() / "anchors" / shot_id / "0000.done.json").read_text())
    assert marker["cache_hit"] is False
    third = stage_generate_anchor(ctx)
    assert third.facts["cache_hits"] == 4  # stable again under the new prompt


def test_single_anchor_and_motion_plan_keyframes_without_controls(ctx: StageContext, env) -> None:
    out = stage_generate_anchor(ctx)
    assert out.facts["anchors"] == 1 and (ctx.ddir() / "anchors" / "anchor.png").exists()
    manifest = json.loads((ctx.ddir() / "anchors" / "manifest.json").read_text())
    assert manifest["shots"][0]["shot_id"] is None
    stage_lock_generation(ctx)
    # builtin controls + keyframes off the single anchor
    stage_compile_controls(ctx)
    kf = stage_generate_keyframes(ctx)
    assert kf.facts["frames"] == 8 and (ctx.ddir() / "sequence" / "frames" / "0007.png").exists()
    qc = stage_drift_qc(ctx)
    assert qc.facts["frames"] == 8 and qc.facts["worst_locked_similarity"] >= 0.92
    pkg = stage_package_sequence(ctx)
    assert pkg.facts["frames"] == 8 and "contact_sheet" in pkg.facts["outputs"]
    # the anchor written by generate_anchor is the hub every keyframe was edited from
    anchor_sha = manifest["shots"][0]["frames"][0]["sha256"]
    marker = json.loads((ctx.ddir() / "sequence" / "frames" / "0000.done.json").read_text())
    assert marker["anchor_sha256"] == anchor_sha


def test_each_uncached_generation_records_what_it_cost(ctx: StageContext, env, monkeypatch) -> None:
    """The measurement that was missing from every OOM and every timing surprise in this repo.

    A marker said which frame was made and never what was on the card when it started, so "Ollama
    was still holding the text model" stayed a hypothesis for weeks (STATUS 1213, 1361, 1380,
    1657). And the HiDream server has reported `elapsed_s` since it was written with nothing
    reading it, so a 6 min 25 s per-anchor figure went unchecked against the 114 s the server
    reported for the same request (STATUS 3238).
    """
    from content_factory.workflows import stages as st

    # nvidia-smi is not on every machine and must not be required; a fixed reading here keeps the
    # assertion about the plumbing rather than about this host's card.
    readings = iter([21000, 19850])
    monkeypatch.setattr(st, "gpu_memory_used_mib", lambda: next(readings, 19850))

    out = stage_generate_anchor(ctx)
    assert out.facts["anchors"] == 1
    marker = json.loads((ctx.ddir() / "anchors" / "anchor.done.json").read_text())
    telemetry = marker["telemetry"]
    assert telemetry["vram_before_mib"] == 21000 and telemetry["vram_after_mib"] == 19850
    assert telemetry["seconds"] >= 0 and telemetry["attempt"] == 1
    # The mock reports no server timing, so the key is absent rather than null.
    assert "server_elapsed_s" not in telemetry

    # And the same numbers are on the executions.log line, tab-separated after the unit name, so
    # the log stays greppable by prefix.
    line = next(x for x in _log(ctx) if x.startswith("anchor:single"))
    unit, _, detail = line.partition("\t")
    assert unit == "anchor:single"
    assert "vram_before_mib=21000" in detail and "seconds=" in detail


def test_the_server_s_own_timing_reaches_the_marker(ctx: StageContext, env) -> None:
    """Both timings are kept on purpose: they disagreed by a factor of three on the thirty-anchor
    run and the gap was recorded as unexplained because nothing held them side by side."""
    from content_factory.sequences.engine import MockReferenceEditBackend
    from content_factory.workflows import stages as st

    class Timed(MockReferenceEditBackend):
        """A backend that reports its own elapsed time, the way the HiDream server does."""

        def __init__(self) -> None:
            super().__init__()
            self.last_facts = {"elapsed_s": 114.0, "refs": 2}

    # The seam under test is the fact plumbing, not the backend selection.
    telemetry = st._generation_telemetry(before=None, seconds=385.0, backend=Timed())
    assert telemetry["seconds"] == 385.0 and telemetry["server_elapsed_s"] == 114.0
    assert "vram_before_mib" not in telemetry  # None is dropped, not written as null
