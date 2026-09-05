"""Hybrid workflow end to end with mocks: route_shots -> only routed shots reach Blender ->
compose_video splices Remotion cuts and generated clips in beat order on one narrated timeline."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from content_factory.config import get_settings
from content_factory.controls import blender as blender_mod
from content_factory.qc.media import check_video, ffprobe
from content_factory.schemas.fixtures import sample_campaign
from content_factory.schemas.scenes import CompiledTimeline
from content_factory.workflows.stages import (
    StageContext,
    stage_align_words,
    stage_compile_captions,
    stage_compile_controls,
    stage_compile_timeline,
    stage_compose_video,
    stage_generate_anchor,
    stage_generate_video,
    stage_lock_script,
    stage_mix_audio,
    stage_plan_shots,
    stage_route_shots,
    stage_select_music,
    stage_synthesize_narration,
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
        deliverable_id="dlv_testvideo001",
        quality="smoke",
        dep_outputs={},
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def _set(**kw: str) -> None:
        for k, v in kw.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()  # type: ignore[attr-defined]

    _set(
        CF__SHOTS__PLANNER="story_presets",
        CF__CONTROLS__COMPILER="blender",
        CF__CONTROLS__ASSETS_ROOT=str(tmp_path / "no-assets"),
        # The demo fixture has no `image` scene, which is the only kind the shipped default routes
        # to the generative chain (ADR-0012, 2026-09-08 amendment). This suite's subject is the
        # SPLICE, so it needs at least one generated beat: the title beat is routed here, in the
        # test, rather than by the setting the whole channel runs on.
        CF__ROUTING__GENERATE_KINDS='["title"]',
    )
    monkeypatch.setattr(blender_mod, "SUBPROCESS_RUN", fake_subprocess_run)
    yield _set
    get_settings.cache_clear()  # type: ignore[attr-defined]


def _fake_remotion_render(ctx: StageContext) -> CompiledTimeline:
    """Stand-in for render_scenes: a flat clip with the compiled timeline's exact shape."""
    tl = CompiledTimeline.model_validate_json(
        (ctx.ddir() / "timeline" / "compiled.json").read_text()
    )
    out = ctx.ddir() / "exports" / "bnd_run000000001.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x2040A0:s={tl.width}x{tl.height}:r={tl.fps}",
            "-frames:v",
            str(tl.total_frames),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out),
        ],
        check=True,
    )
    return tl


def _narration(ctx: StageContext) -> None:
    stage_lock_script(ctx)
    stage_synthesize_narration(ctx)
    stage_align_words(ctx)
    stage_compile_captions(ctx)
    stage_select_music(ctx)
    stage_mix_audio(ctx)


def test_hybrid_workflow_splices_both_branches_in_beat_order(ctx: StageContext, env) -> None:
    _narration(ctx)
    stage_compile_timeline(ctx)
    tl = _fake_remotion_render(ctx)

    stage_plan_shots(ctx)
    routing_out = stage_route_shots(ctx)
    assert routing_out.facts == {
        "beats": 4,
        "generate": 1,
        "render": 3,
        "default_route": "render",
        "generate_kinds": ["title"],
    }

    controls = stage_compile_controls(ctx)
    manifest = json.loads((ctx.ddir() / "controls" / "manifest.json").read_text())
    assert controls.facts["shots"] == 1 and len(manifest["bundles"]) == 1  # only the title beat
    stage_generate_anchor(ctx)
    video = stage_generate_video(ctx)
    assert video.facts["clips"] == 1 if "clips" in video.facts else True
    clips = sorted(p.parent.name for p in (ctx.ddir() / "video").glob("*/clip.mp4"))
    assert clips == [manifest["bundles"][0]["shot_id"]]

    out = stage_compose_video(ctx)
    compose = json.loads((ctx.ddir() / "exports" / "compose.json").read_text())
    assert [s["route"] for s in compose["segments"]] == ["generate", "render", "render", "render"]
    assert [s["beat_id"] for s in compose["segments"]] == [s.beat_id for s in tl.scenes]
    assert sum(s["frames"] for s in compose["segments"]) == tl.total_frames
    assert compose["segments"][0]["shot_id"] == clips[0]
    assert compose["segments"][0]["source"].startswith("video/") and compose["narrated"] is True
    assert compose["captions"] == "captions/captions.ass"  # word-highlight track, burned by default
    assert compose["pacing"]["scenes"] == 4 and compose["pacing"]["longest_scene_s"] > 0
    assert out.facts["longest_scene_s"] == compose["pacing"]["longest_scene_s"]
    assert (ctx.ddir() / "exports" / "composed.captioned.mp4").exists()
    assert out.facts["segments"] == 4 and out.facts["generated"] == 1
    assert out.facts["rendered"] == 3 and out.facts["frames"] == tl.total_frames

    final = ctx.ddir() / "exports" / "final.mp4"
    qc = check_video(final, width=tl.width, height=tl.height, fps=tl.fps, frames=tl.total_frames)
    assert qc.passed, qc.findings
    streams = {s["codec_type"] for s in ffprobe(final)["streams"]}
    assert streams == {"video", "audio"}  # narration muxed onto the spliced picture

    # Every segment is exactly its beat's length, so the narration stays in sync.
    for seg, scene in zip(compose["segments"], tl.scenes, strict=True):
        path = ctx.ddir() / "exports" / "segments" / f"{seg['index']:03d}_{scene.beat_id}.mp4"
        info = check_video(
            path, width=tl.width, height=tl.height, fps=tl.fps, frames=scene.duration_frames
        )
        assert info.passed, (scene.beat_id, info.findings)

    # Idempotent: a rerun re-uses every segment (markers match) and produces the same hash.
    markers = sorted((ctx.ddir() / "exports" / "segments").glob("*.done.json"))
    before = [m.stat().st_mtime_ns for m in markers]
    again = stage_compose_video(ctx)
    assert again.outputs_hash == out.outputs_hash
    assert [m.stat().st_mtime_ns for m in markers] == before


def test_all_render_routing_takes_the_plain_mux_path(ctx: StageContext, env) -> None:
    env(CF__ROUTING__DEFAULT_ROUTE="render", CF__ROUTING__GENERATE_KINDS="[]")
    _narration(ctx)
    stage_compile_timeline(ctx)
    tl = _fake_remotion_render(ctx)
    stage_plan_shots(ctx)
    assert stage_route_shots(ctx).facts["generate"] == 0
    assert stage_compile_controls(ctx).facts["shots"] == 0  # nothing for Blender to do
    out = stage_compose_video(ctx)
    manifest = json.loads((ctx.ddir() / "exports" / "compose.json").read_text())
    assert "segments" not in manifest and manifest["picture"] == "exports/bnd_run000000001.mp4"
    final = ctx.ddir() / "exports" / "final.mp4"
    assert check_video(
        final, width=tl.width, height=tl.height, fps=tl.fps, frames=tl.total_frames
    ).passed
    assert out.facts["artifact_key"]


def test_missing_generated_clip_is_a_clear_error(ctx: StageContext, env) -> None:
    _narration(ctx)
    stage_compile_timeline(ctx)
    _fake_remotion_render(ctx)
    stage_plan_shots(ctx)
    stage_route_shots(ctx)  # title beat -> generate, but generate_video never ran
    with pytest.raises(RuntimeError, match="run generate_video first"):
        stage_compose_video(ctx)


def test_caption_burning_can_be_turned_off(ctx: StageContext, env) -> None:
    env(CF__COMPOSE__BURN_CAPTIONS="false", CF__ROUTING__GENERATE_KINDS="[]")
    _narration(ctx)
    stage_compile_timeline(ctx)
    _fake_remotion_render(ctx)
    stage_plan_shots(ctx)
    stage_route_shots(ctx)
    stage_compose_video(ctx)
    exports = ctx.ddir() / "exports"
    assert not list(exports.glob("*.captioned.mp4"))
    env(CF__COMPOSE__BURN_CAPTIONS="true")
    stage_compose_video(ctx)
    assert list(exports.glob("*.captioned.mp4"))  # the picture was re-encoded with the .srt


def test_plain_path_reruns_from_the_pristine_picture(ctx: StageContext, env) -> None:
    env(CF__ROUTING__GENERATE_KINDS="[]")
    _narration(ctx)
    stage_compile_timeline(ctx)
    tl = _fake_remotion_render(ctx)
    stage_plan_shots(ctx)
    stage_route_shots(ctx)
    first = stage_compose_video(ctx)
    manifest = json.loads((ctx.ddir() / "exports" / "compose.json").read_text())
    assert manifest["picture"] == "exports/bnd_run000000001.mp4"
    assert manifest["captions"] == "captions/captions.ass"
    second = stage_compose_video(ctx)  # would have captioned final.mp4 again before the manifest
    again = json.loads((ctx.ddir() / "exports" / "compose.json").read_text())
    assert again["picture"] == "exports/bnd_run000000001.mp4"
    assert not (ctx.ddir() / "exports" / "picture.mp4").exists()
    assert second.outputs_hash == first.outputs_hash
    final = ctx.ddir() / "exports" / "final.mp4"
    assert check_video(
        final, width=tl.width, height=tl.height, fps=tl.fps, frames=tl.total_frames
    ).passed


def test_srt_burn_is_the_fallback_when_highlighting_is_off(ctx: StageContext, env) -> None:
    env(CF__COMPOSE__CAPTION_HIGHLIGHT="false", CF__ROUTING__GENERATE_KINDS="[]")
    _narration(ctx)
    stage_compile_timeline(ctx)
    _fake_remotion_render(ctx)
    stage_plan_shots(ctx)
    stage_route_shots(ctx)
    stage_compose_video(ctx)
    manifest = json.loads((ctx.ddir() / "exports" / "compose.json").read_text())
    assert manifest["captions"] == "captions/captions.srt"
