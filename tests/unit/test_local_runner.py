"""Local workflow runner: it reads the definitions, and slicing a run behaves.

The old version of this file asserted that the runner's hand-written stage table agreed with the
canvas's. There is no second table any more, so what is worth testing is that the runner reads the
definitions faithfully, that --from and --until slice the real order, and that a lane's own values
survive the command-line overrides.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.runners.local import (
    LocalRunError,
    make_context,
    run_plan,
    run_stages,
    run_workflow,
    workflow_steps,
)
from content_factory.schemas.dag import Stage
from content_factory.workflows.catalog import load_definitions
from content_factory.workflows.stages import STAGE_EXECUTORS


def test_every_runnable_workflow_stage_has_an_executor_and_the_hybrid_order_is_right() -> None:
    for name, template in load_definitions().items():
        if template.caveat:
            # An unfinished lane names its gap in the caveat; test_workflow_definitions checks it.
            continue
        assert all(s in STAGE_EXECUTORS for s in template.stages()), name
    hybrid = [s.value for s in load_definitions()["hybrid-video"].stages()]
    assert hybrid.index("route_shots") > hybrid.index("plan_shots")
    assert hybrid.index("compile_controls") > hybrid.index("route_shots")
    assert hybrid.index("compose_video") > hybrid.index("generate_video")
    assert hybrid.index("compose_video") > hybrid.index("render_scenes")
    assert hybrid.index("compose_video") > hybrid.index("mix_audio")


def test_run_stages_writes_a_report_and_stops_at_the_first_failure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CF__SHOTS__PLANNER", "story_presets")
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    ctx = make_context(project_dir=tmp_path / "prj")
    logs: list[str] = []
    report = run_stages([Stage.plan_shots, Stage.route_shots], ctx, log=logs.append)
    assert report["passed"] and [s["stage"] for s in report["stages"]] == [
        "plan_shots",
        "route_shots",
    ]
    assert all(s["ok"] and s["outputs_hash"] for s in report["stages"])
    on_disk = json.loads((ctx.ddir() / "run.json").read_text())
    assert on_disk["passed"] is True and logs[0] == "==> plan_shots"

    # compose_video needs compile_timeline output: it fails, the report keeps the failure
    with pytest.raises(LocalRunError) as info:
        run_stages([Stage.plan_shots, Stage.compose_video], ctx, log=logs.append)
    assert info.value.stage is Stage.compose_video
    failed = json.loads((ctx.ddir() / "run.json").read_text())
    assert failed["passed"] is False and failed["stages"][-1]["ok"] is False
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_run_workflow_slices_by_stage_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown workflow"):
        run_workflow("nope", project_dir=tmp_path)
    report = run_workflow(
        "scene-controlled-video",
        project_dir=tmp_path / "prj",
        from_stage="plan_story",
        until_stage="plan_shots",
        log=lambda _m: None,
    )
    assert [s["stage"] for s in report["stages"]] == ["plan_story", "plan_shots"]


def test_run_workflow_resolves_relative_project_dirs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report = run_workflow(
        "scene-controlled-video",
        project_dir=Path("relative-run"),
        from_stage="plan_story",
        until_stage="plan_story",
        log=lambda _m: None,
    )
    assert Path(report["project_dir"]).is_absolute()
    assert (tmp_path / "relative-run" / "story" / "plan.json").exists()


def test_picture_story_orders_picture_before_sound_and_carries_its_parameters() -> None:
    """Sound design scores the cut, so it cannot run before there is one; the mix cannot run
    before the sound design it beds. Getting this order wrong fails only at run time, minutes in."""
    template = load_definitions()["picture-story"]
    lane = [s.value for s in template.stages()]
    assert lane.index("compile_controls") > lane.index("plan_shots")
    assert lane.index("generate_anchor") > lane.index("compile_controls")
    assert lane.index("generate_video") > lane.index("generate_anchor")
    assert lane.index("sound_design") > lane.index("interpolate")
    assert lane.index("mix_audio") > lane.index("sound_design")
    assert lane.index("mix_audio") > lane.index("voice_over")
    assert lane.index("compose_video") > lane.index("mix_audio")
    # The recorded voice replaces the TTS rather than joining it.
    assert "voice_over" in lane and "synthesize_narration" not in lane

    values = {stage.value: v for _key, stage, v in template.stage_order() if v}
    assert values["compile_controls"]["compiler"] == "blender"
    assert values["generate_anchor"]["model"] == "hidream-o1"
    assert "watercolour" in str(values["generate_anchor"]["style"])


def test_run_workflow_passes_the_chosen_film_to_the_stages_that_need_it(tmp_path: Path) -> None:
    """--story and --shots are what make one lane run three different films."""
    from content_factory.runners.local import run_workflow

    seen: dict[str, dict[str, str]] = {}

    def fake_run_plan(steps, ctx, *, report_path=None, workflow="", log=print):
        for _key, stage, values in steps:
            if values:
                seen.setdefault(stage.value, {}).update(values)
        return {"passed": True, "stages": [], "project_dir": str(ctx.project_dir)}

    import content_factory.runners.local as local

    original = local.run_plan
    local.run_plan = fake_run_plan  # type: ignore[assignment]
    try:
        run_workflow(
            "picture-story",
            project_dir=tmp_path,
            story="fixtures/story/last_train.json",
            shots="fixtures/shots/last_train_topdown.json",
            style="charcoal on grey paper",
            log=lambda *_: None,
        )
    finally:
        local.run_plan = original  # type: ignore[assignment]

    assert seen["plan_story"]["story"] == "fixtures/story/last_train.json"
    assert seen["plan_shots"]["planner"] == "fixture"
    assert seen["plan_shots"]["fixture_path"] == "fixtures/shots/last_train_topdown.json"
    # The lane's own framing survives an override that does not mention it.
    assert seen["plan_shots"]["size"] == "1024x576"
    assert seen["generate_anchor"]["style"] == "charcoal on grey paper"
    assert seen["compile_controls"]["compiler"] == "blender"  # lane default survives the override


def test_from_and_until_together_slice_to_the_stages_asked_for(tmp_path: Path) -> None:
    """--until has to be resolved against the original order. Resolving it after --from indexes
    the long list into the short one and quietly runs stages past the one you stopped at."""
    from content_factory.runners.local import run_workflow

    seen: list[str] = []

    def fake_run_plan(steps, ctx, *, report_path=None, workflow="", log=print):
        seen.extend(stage.value for _key, stage, _v in steps)
        return {"passed": True, "stages": [], "project_dir": str(ctx.project_dir)}

    import content_factory.runners.local as local

    original = local.run_plan
    local.run_plan = fake_run_plan  # type: ignore[assignment]
    try:
        run_workflow(
            "picture-story",
            project_dir=tmp_path,
            from_stage="generate_anchor",
            until_stage="generate_video",
            log=lambda *_: None,
        )
    finally:
        local.run_plan = original  # type: ignore[assignment]
    # review_frames sits between them: no drawing reaches the animator unreviewed, and the
    # slice has to carry the gate with it or --until would step over a blocking stage.
    assert seen == ["generate_anchor", "review_frames", "generate_video"]


def test_the_local_context_addresses_a_deliverable_the_campaign_has(tmp_path: Path) -> None:
    """qc_deliverable and the destination packages look their own spec up in the campaign; an
    invented deliverable id fails them with a bare StopIteration minutes into a run."""
    ctx = make_context(project_dir=tmp_path)
    ids = {d.deliverable_id for d in ctx.campaign.deliverables}
    assert ctx.deliverable_id in ids
    spec = next(d for d in ctx.campaign.deliverables if d.deliverable_id == ctx.deliverable_id)
    assert spec.type in ("short_video", "long_video")


def test_slicing_takes_a_node_key_and_refuses_an_ambiguous_stage_name(tmp_path: Path) -> None:
    """A stage that appears twice in a lane cannot be named by stage: the runner says so and names
    the nodes, instead of quietly picking the first."""
    steps = workflow_steps("picture-story")
    keys = [k for k, _s, _v in steps]
    # Named rather than positional: the lane gained a retrieval step between the story and the
    # shots, and a test that pins positions turns every editorial change into a test edit.
    assert {"story", "shots", "controls", "anchor", "frames_gate", "motion"} <= set(keys)
    assert keys.index("controls") < keys.index("anchor") < keys.index("motion")

    import content_factory.runners.local as local

    seen: list[str] = []
    original = local.run_plan
    local.run_plan = lambda steps, ctx, **kw: (  # type: ignore[assignment]
        seen.extend(k for k, _s, _v in steps),
        {"passed": True, "stages": [], "project_dir": str(ctx.project_dir)},
    )[1]
    try:
        run_workflow(
            "picture-story",
            project_dir=tmp_path,
            from_stage="controls",
            until_stage="motion",
            log=lambda *_: None,
        )
    finally:
        local.run_plan = original  # type: ignore[assignment]
    assert seen == ["controls", "anchor", "frames_gate", "motion"]

    with pytest.raises(ValueError, match="not in this workflow"):
        run_workflow(
            "picture-story", project_dir=tmp_path, until_stage="nonsense", log=lambda *_: None
        )


def test_run_plan_carries_the_node_key_into_the_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CF__SHOTS__PLANNER", "story_presets")
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    ctx = make_context(project_dir=tmp_path / "prj")
    report = run_plan([("my_shots", Stage.plan_shots, {})], ctx, log=lambda _m: None)
    assert report["stages"][0]["node"] == "my_shots"
    assert report["stages"][0]["stage"] == "plan_shots"
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_hybrid_workflow_runs_end_to_end_on_mock_backends(tmp_path: Path, monkeypatch) -> None:
    """The offline path: fixture story + fixture shots, mock TTS, 2D control compiler, mock video,
    Remotion replaced by an ffmpeg stand-in. Every stage of the template must complete."""
    import subprocess

    from content_factory.config import get_settings
    from content_factory.schemas.scenes import CompiledTimeline
    from content_factory.workflows import stages as st

    monkeypatch.setenv("CF__ROUTING__GENERATE_KINDS", '["image"]')
    get_settings.cache_clear()  # type: ignore[attr-defined]

    def fake_render(ctx: st.StageContext) -> st.StageOutput:
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
                f"color=c=0x101418:s={tl.width}x{tl.height}:r={tl.fps}",
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
        return st.StageOutput("0" * 64, {"fake": True})

    monkeypatch.setitem(st.STAGE_EXECUTORS, Stage.render_scenes, fake_render)
    try:
        report = run_workflow(
            "hybrid-video",
            project_dir=tmp_path / "prj",
            story="fixtures/story/wind_2024.json",
            shots="fixtures/shots/wind_2024.json",
            # The definition pins a real narrator and a real speech enhancer, which are right for
            # a film and wrong for an offline test. Both are overridden here rather than removed
            # from the definition: what belongs in a lane and what belongs in a test are different
            # questions, and this test's subject is the FFmpeg-only voice chain.
            params={
                Stage.synthesize_narration: {"voice": "mock"},
                Stage.restore_speech: {
                    "enhancer": "off",
                    "band_extension": "off",
                    "cleanup": "off",
                },
            },
            log=lambda _m: None,
        )
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]
    # The count tracks the definition rather than a literal: this lane gained research,
    # verify_claims, compile_datasets and write_copy when the catalogue decided that lanes whose
    # scripts are drafted from sources carry the research front end.
    expected = len(load_definitions()["hybrid-video"].stages())
    assert report["passed"] and len(report["stages"]) == expected
    by = {s["stage"]: s for s in report["stages"]}
    # The voice chain runs on every beat with the FFmpeg tail only, because this run pinned both
    # model steps off, and leaves the beat exactly as long as it found it.
    assert by["restore_speech"]["facts"]["steps"] == [
        "detect",
        "voice_chain:de_ess+eq+compress",
    ]
    assert by["route_shots"]["facts"]["generate"] == 2
    assert by["compile_controls"]["facts"]["shots"] == 2  # per-shot bundles from the 2D compiler
    assert by["generate_video"]["facts"]["shots"] == 2
    assert (
        by["compose_video"]["facts"]["generated"] == 2
        and by["compose_video"]["facts"]["rendered"] == 6
    )
    assert by["compile_captions"]["facts"]["hook"] is True
    assert by["qc_deliverable"]["facts"]["passed"] is True


# --- the brief a lane run actually carries ----------------------------------------------------


def test_the_lane_brief_replaces_the_fixture_and_subject_overrides_its_topic() -> None:
    """Every local run used to carry ``sample_campaign()`` unchanged, so a film about two people on
    a plaza was generated under "How much of Sweden's electricity came from wind in 2025?" — the
    demo fixture's brief topic, which reached the image model through ``_anchor_prompt`` and the
    video model through two fallbacks in ``generate_video`` (STATUS 1370, 1678)."""
    from content_factory.runners.local import _brief_for, make_context
    from content_factory.schemas.fixtures import sample_campaign

    fixture_topic = sample_campaign().brief.topic
    assert "wind" in fixture_topic  # the leak this test is about

    lane = _brief_for("image-to-video", None)
    assert lane["topic"] == "one image brought into motion by a slow camera push"
    assert _brief_for("image-to-video", "a paper lantern over still water")["topic"] == (
        "a paper lantern over still water"
    )

    ctx = make_context(project_dir=Path("/tmp/unused-brief-check"), brief=lane)
    assert ctx.campaign.brief.topic == lane["topic"] != fixture_topic
    # A caller that passes no brief still gets the fixture: that is what the tests and the MCP
    # tools want, and it is the one path that may.
    assert make_context(project_dir=Path("/tmp/unused-brief-check")).campaign.brief.topic == (
        fixture_topic
    )


def test_an_empty_brief_topic_is_named_rather_than_defaulted() -> None:
    from content_factory.runners.local import make_context
    from content_factory.schemas.fixtures import sample_campaign
    from content_factory.workspace.compile import campaign_with_brief

    with pytest.raises(ValueError, match="topic is empty"):
        campaign_with_brief(sample_campaign(), {"audience": "general"})
    with pytest.raises(ValueError, match="topic is empty"):
        make_context(project_dir=Path("/tmp/unused-brief-check"), brief={"topic": "   "})


def test_a_single_clip_lane_with_no_subject_fails_before_any_server_starts(
    tmp_path: Path, monkeypatch
) -> None:
    """The single-clip path has no ShotSpec to compile a prompt from, so it needs a subject stated
    for it. It used to take the brief topic — a research question — as the whole LTX prompt.

    The failure has to land before a GPU tenant is started: a run refused after ComfyUI has been
    warmed has already evicted whatever the other tenant on the card was doing.
    """
    from content_factory.runners.local import make_context
    from content_factory.workflows import stages as st

    warmed: list[str] = []
    monkeypatch.setattr(st, "_ensure_backend_ready", lambda b, w: warmed.append(str(b)))

    lane = {"topic": "one image brought into motion by a slow camera push"}
    ctx = make_context(project_dir=tmp_path / "prj", brief=lane)
    object.__setattr__(ctx, "params", {"prompt": "hold the framing of the first frame"})
    with pytest.raises(RuntimeError, match="no subject"):
        st.stage_generate_video(ctx)
    assert warmed == []

    object.__setattr__(
        ctx,
        "params",
        {
            "prompt": "hold the framing of the first frame",
            "subject": "a paper lantern drifting over still water",
        },
    )
    st.stage_generate_video(ctx)
    result = json.loads((ctx.ddir() / "generated" / "provenance.json").read_text())
    prompt = result["provenance"]["prompt"]
    assert "paper lantern drifting over still water" in prompt
    assert "electricity" not in prompt and "wind in 2025" not in prompt
