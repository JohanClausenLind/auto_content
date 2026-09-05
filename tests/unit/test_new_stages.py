"""New production stages: music selection + music-bedded mix, generated video, animation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.media.video_generate import (
    MockVideoBackend,
    VideoGenerationError,
    VideoGenerationRequest,
    run_video_skill,
)
from content_factory.schemas.comfyui import CapabilityFlag
from content_factory.schemas.fixtures import sample_campaign, sample_ltx_i2v_package
from content_factory.workflows.stages import (
    StageContext,
    stage_align_words,
    stage_generate_video,
    stage_lock_script,
    stage_mix_audio,
    stage_render_animation,
    stage_select_music,
    stage_synthesize_narration,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    campaign = sample_campaign()
    return StageContext(
        workspace_id=campaign.workspace_id,
        project_dir=tmp_path / "project",
        artifacts_dir=tmp_path / "artifacts",
        campaign=campaign,
        deliverable_id="dlv_testvideo001",
        quality="smoke",
        dep_outputs={},
    )


def test_select_music_is_deterministic_and_verified(ctx: StageContext) -> None:
    out1 = stage_select_music(ctx)
    out2 = stage_select_music(ctx)
    assert out1.outputs_hash == out2.outputs_hash
    selection = json.loads((ctx.ddir() / "audio" / "music-selection.json").read_text())
    assert selection["track_id"] == "calm_bed_a"
    assert selection["attribution"]


def test_select_music_survives_missing_library(ctx: StageContext, monkeypatch) -> None:
    monkeypatch.setenv("CF__MEDIA_LIBRARY__MUSIC_DIR", str(ctx.project_dir / "no-library"))
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        out = stage_select_music(ctx)
        assert out.facts["track"] is None
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_mix_audio_includes_the_selected_bed(ctx: StageContext) -> None:
    stage_lock_script(ctx)
    stage_synthesize_narration(ctx)
    stage_align_words(ctx)
    stage_select_music(ctx)
    out = stage_mix_audio(ctx)
    assert (ctx.ddir() / "audio" / "narration-with-music.wav").exists()
    assert (ctx.ddir() / "audio" / "narration-mastered.wav").exists()
    assert -15.5 <= out.facts["lufs"] <= -12.5  # still masters to target with the bed underneath


def test_generate_video_stage_produces_verified_clip(ctx: StageContext) -> None:
    # A lane with no shot plan has to say what is moving: the stage no longer falls back to the
    # campaign brief's topic, which is a research question rather than a shot description.
    object.__setattr__(ctx, "params", {"subject": "a paper lantern drifting over still water"})
    out = stage_generate_video(ctx)
    final = ctx.ddir() / "exports" / "generated.mp4"
    assert final.exists() and final.stat().st_size > 0
    assert out.facts["backend"] == "mock"
    prov = json.loads((ctx.ddir() / "generated" / "provenance.json").read_text())
    assert prov["has_audio"] is True
    assert "paper lantern" in prov["provenance"]["prompt"]


def test_render_animation_builtin(ctx: StageContext) -> None:
    out = stage_render_animation(ctx)
    frames = sorted((ctx.ddir() / "animation" / "frames").glob("*.png"))
    assert len(frames) == out.facts["frames"] and out.facts["renderer"] == "builtin"
    assert (ctx.ddir() / "animation" / "preview.mp4").exists()
    # deterministic: rerunning produces the same output hash
    assert stage_render_animation(ctx).outputs_hash == out.outputs_hash


def test_video_skill_verifies_dimensions_and_duration(tmp_path: Path) -> None:
    request = VideoGenerationRequest(
        request_id="vid_skilltest001",
        purpose="test",
        prompt="a calm sky",
        width=320,
        height=176,
        duration_s=2.0,
        seed=7,
    )
    out = run_video_skill(request, MockVideoBackend(), workdir=tmp_path)
    assert out.result.has_audio and out.result.backend == "mock"

    class LyingBackend(MockVideoBackend):
        def generate(self, request, first_frame_png, *, workdir, **kw):
            produced = super().generate(request, first_frame_png, workdir=workdir, **kw)
            wrong = produced.result.model_copy(update={"has_audio": False})
            return type(produced)(result=wrong, mp4=produced.mp4)

    with pytest.raises(VideoGenerationError, match="audio flag"):
        run_video_skill(request, LyingBackend(), workdir=tmp_path / "lie")


def test_ltx_package_fixture_is_valid_and_bound() -> None:
    package = sample_ltx_i2v_package()
    names = {p.name for p in package.parameters}
    assert {"prompt", "first_frame", "width", "height", "length", "seed"} <= names
    assert package.expected_outputs == ("16",)
    assert len(package.required_models) == 3
    assert {m.filename.rsplit(".", 1)[-1] for m in package.required_models} == {
        "gguf",
        "safetensors",
    }
    assert package.custom_nodes[0].registry_name == "ComfyUI-GGUF"
    assert CapabilityFlag.image_to_video in package.capabilities


def test_video_skill_verifies_guide_frame_digests(tmp_path: Path) -> None:
    from content_factory.media.video_generate import GuideFrame

    request = VideoGenerationRequest(
        request_id="vid_guidetest001",
        purpose="test",
        prompt="a calm sky",
        width=320,
        height=176,
        duration_s=2.0,
        seed=7,
        guides=(GuideFrame(frame_index=47, png_sha256="a" * 64),),
    )
    with pytest.raises(VideoGenerationError, match="no image bytes"):
        run_video_skill(request, MockVideoBackend(), workdir=tmp_path, first_frame_png=b"x")
    with pytest.raises(VideoGenerationError, match="do not match its digest"):
        run_video_skill(
            request,
            MockVideoBackend(),
            workdir=tmp_path,
            first_frame_png=b"x",
            guide_pngs={47: b"wrong"},
        )


def test_generate_video_stage_animates_each_shot_with_anchor_guides(
    ctx: StageContext, monkeypatch, tmp_path: Path
) -> None:
    from content_factory.config import get_settings
    from content_factory.controls import blender as blender_mod
    from content_factory.schemas.fixtures import sample_shot_plan
    from content_factory.workflows.stages import (
        stage_compile_controls,
        stage_generate_anchor,
        stage_plan_shots,
    )
    from tests.helpers.fake_blender import fake_subprocess_run

    monkeypatch.setenv("CF__CONTROLS__COMPILER", "blender")
    monkeypatch.setenv("CF__SHOTS__PLANNER", "fixture")
    monkeypatch.setenv("CF__CONTROLS__ASSETS_ROOT", str(tmp_path / "no-assets"))
    monkeypatch.setattr(blender_mod, "SUBPROCESS_RUN", fake_subprocess_run)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        stage_plan_shots(ctx)
        stage_compile_controls(ctx)
        stage_generate_anchor(ctx)
        out = stage_generate_video(ctx)
        plan = sample_shot_plan()
        assert (
            out.facts["shots"] == 2 and out.facts["guides"] == 2 and out.facts["backend"] == "mock"
        )
        assert out.facts["cache_hits"] == 0
        for shot in plan.shots:
            clip = ctx.ddir() / "video" / shot.shot_id / "clip.mp4"
            prov = json.loads((ctx.ddir() / "video" / shot.shot_id / "provenance.json").read_text())
            assert clip.exists() and prov["width"] == shot.width and prov["has_audio"] is False
            assert [g["frame_index"] for g in prov["provenance"]["guides"]] == [96]
            assert prov["provenance"]["first_frame"] is True
        final = ctx.ddir() / "exports" / "generated.mp4"
        assert final.exists() and final.stat().st_size > 0
        assert abs(out.facts["duration_s"] - 2 * (97 / 24)) < 0.5
        again = stage_generate_video(ctx)
        assert again.outputs_hash == out.outputs_hash and again.facts["cache_hits"] == 2
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]
