"""controls.compiler=blender: skill runner wrapper, bundle builder, stage caching — no Blender."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from content_factory.config import get_settings
from content_factory.controls import blender as blender_mod
from content_factory.controls.bundle import (
    BundleError,
    build_control_bundle,
    skeleton_frame_to_poses,
)
from content_factory.schemas.fixtures import sample_campaign, sample_shot_plan
from content_factory.schemas.sequences import ControlKind
from content_factory.schemas.shots import ControlBundle
from content_factory.workflows.stages import StageContext, stage_compile_controls, stage_plan_shots
from tests.helpers.fake_blender import (
    failing_subprocess_run,
    fake_subprocess_run,
    write_fake_outputs,
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
def blender_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CF__CONTROLS__COMPILER", "blender")
    monkeypatch.setenv("CF__SHOTS__PLANNER", "fixture")
    monkeypatch.setattr(blender_mod, "SUBPROCESS_RUN", fake_subprocess_run)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _log_lines(ctx: StageContext) -> list[str]:
    log = ctx.project_dir / ".stages" / "executions.log"
    return log.read_text().splitlines() if log.exists() else []


def test_blender_compiler_builds_one_bundle_per_shot(ctx: StageContext, blender_settings) -> None:
    stage_plan_shots(ctx)
    out = stage_compile_controls(ctx)
    manifest = json.loads((ctx.ddir() / "controls" / "manifest.json").read_text())
    assert manifest["compiler"] == "blender" and len(manifest["bundles"]) == 2
    assert out.facts["shots"] == 2 and out.facts["compiler"] == "blender"
    assert sum(1 for line in _log_lines(ctx) if line.startswith("blender_scene:")) == 2

    plan = sample_shot_plan()
    shot = plan.shots[0]
    bundle = ControlBundle.model_validate_json(
        (ctx.ddir() / manifest["bundles"][0]["path"]).read_text()
    )
    assert bundle.compiler == "blender" and bundle.shot_id == shot.shot_id
    assert bundle.plan_hash == shot.content_hash()
    assert {t.kind for t in bundle.tracks} == set(shot.render.passes)
    assert bundle.anchor_frames == (0, 96) and len(bundle.camera) == shot.frame_count
    by_kind = {t.kind: t for t in bundle.tracks}
    assert by_kind[ControlKind.depth_exr].encoding.value == "exr32"
    assert by_kind[ControlKind.segmentation].encoding.value == "index8"
    assert len(by_kind[ControlKind.pose_skeleton].frames) == shot.frame_count
    # subjects: the character and the bench prop, segmentation ids in spec order
    assert [(s.subject_id, s.segmentation_index) for s in bundle.subjects] == [
        ("man", 1),
        ("bench", 2),
    ]
    man = bundle.subjects[0]
    assert len(man.poses) == shot.frame_count and man.poses[0] is not None
    assert "l_ear" not in man.poses[0].joints  # off-screen joint dropped by the adapter
    assert ("nose", "l_ear") not in man.poses[0].bones and ("neck", "nose") in man.poses[0].bones
    assert man.layouts[0] is not None and man.layouts[0].w > 0

    # derived PNG tracks exist on disk and carry markers
    pose_png = ctx.ddir() / "controls" / shot.shot_id / "pose_skeleton" / "frames" / "0000.png"
    assert pose_png.exists() and Image.open(pose_png).size == (shot.width, shot.height)
    layout_marker = json.loads(
        (
            ctx.ddir() / "controls" / shot.shot_id / "layout_boxes" / "frames" / "0000.done.json"
        ).read_text()
    )
    assert layout_marker["pass"] == "layout_boxes" and len(layout_marker["png_sha256"]) == 64
    assert (ctx.ddir() / "controls" / shot.shot_id / "shot_spec.json").exists()


def test_second_run_is_a_cache_hit(ctx: StageContext, blender_settings) -> None:
    stage_plan_shots(ctx)
    first = stage_compile_controls(ctx)
    before = _log_lines(ctx)
    second = stage_compile_controls(ctx)
    assert second.outputs_hash == first.outputs_hash
    assert _log_lines(ctx) == before  # no new blender_scene: executions
    marker = json.loads(
        (ctx.ddir() / "controls" / sample_shot_plan().shots[0].shot_id / ".done.json").read_text()
    )
    assert marker["engines"] == {"data": "cycles_cpu", "rgb": "workbench"}


def test_engine_change_invalidates_cache(ctx: StageContext, blender_settings, monkeypatch) -> None:
    stage_plan_shots(ctx)
    stage_compile_controls(ctx)
    monkeypatch.setenv("CF__CONTROLS__ENGINE", "cycles_cpu")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    stage_compile_controls(ctx)
    assert sum(1 for line in _log_lines(ctx) if line.startswith("blender_scene:")) == 4


def test_missing_plan_and_failing_skill_raise(
    ctx: StageContext, blender_settings, monkeypatch
) -> None:
    with pytest.raises(RuntimeError, match="run plan_shots first"):
        stage_compile_controls(ctx)
    stage_plan_shots(ctx)
    monkeypatch.setattr(blender_mod, "SUBPROCESS_RUN", failing_subprocess_run)
    with pytest.raises(RuntimeError, match=r"exit 3.*simulated"):
        stage_compile_controls(ctx)


def test_bundle_builder_detects_tampered_pass(tmp_path: Path) -> None:
    shot = sample_shot_plan().shots[0]
    write_fake_outputs(shot.model_dump(mode="json"), tmp_path)
    (tmp_path / "depth" / "frames" / "0003.png").write_bytes(b"not the rendered bytes")
    with pytest.raises(BundleError, match="sha256 mismatch"):
        build_control_bundle(shot, tmp_path)


def test_bundle_builder_rejects_size_mismatch(tmp_path: Path) -> None:
    shot = sample_shot_plan().shots[0]
    write_fake_outputs({**shot.model_dump(mode="json"), "width": 512}, tmp_path)
    with pytest.raises(BundleError, match="spec says"):
        build_control_bundle(shot, tmp_path)


def test_bundle_is_deterministic_and_handles_frame_subsets(tmp_path: Path) -> None:
    shot = (
        sample_shot_plan()
        .shots[0]
        .model_copy(
            update={
                "render": sample_shot_plan()
                .shots[0]
                .render.model_copy(update={"frames": (0, 48, 96)})
            }
        )
    )
    hashes = []
    for name in ("a", "b"):
        d = tmp_path / name
        write_fake_outputs(shot.model_dump(mode="json"), d)
        bundle = build_control_bundle(shot, d)
        hashes.append(bundle.content_hash())
        assert [f.frame_index for f in bundle.tracks[0].frames] == [0, 48, 96]
        assert [c.frame_index for c in bundle.camera] == [0, 48, 96]
        assert len(bundle.subjects[0].layouts) == shot.frame_count
        assert bundle.subjects[0].layouts[1] is None and bundle.subjects[0].layouts[48] is not None
    assert hashes[0] == hashes[1]


def test_skeleton_adapter_drops_out_of_frame_joints_and_dangling_bones() -> None:
    doc = {
        "people": [
            {
                "character_id": "man",
                "joints": {
                    "nose": {"x": 0.5, "y": 0.1, "in_frame": True},
                    "neck": {"x": 0.5, "y": 0.2, "in_frame": True},
                    "l_ear": {"x": 1.2, "y": 0.1, "in_frame": False},
                },
                "bones": [["neck", "nose"], ["nose", "l_ear"]],
            },
            {
                "character_id": "ghost",
                "joints": {"nose": {"x": -1, "y": 0.5, "in_frame": False}},
                "bones": [],
            },
        ]
    }
    poses = skeleton_frame_to_poses(doc)
    assert set(poses) == {"man"}
    assert set(poses["man"].joints) == {"nose", "neck"} and poses["man"].bones == (
        ("neck", "nose"),
    )
