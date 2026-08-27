"""Deterministic example instances of each contract, used by cross-language parity tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from content_factory.schemas import (
    artboards,
    comfyui,
    content,
    editing,
    hardware,
    render,
    scenes,
    sequences,
)

ZERO_HASH = "0" * 64
ANCHOR_HASH = "a" * 64


def sample_motion_plan() -> sequences.MotionPlan:
    hands_open = sequences.SkeletonPose(
        joints={
            "l_wrist": sequences.Point(x=0.30, y=0.60),
            "l_index": sequences.Point(x=0.33, y=0.50),
            "r_wrist": sequences.Point(x=0.70, y=0.60),
            "r_index": sequences.Point(x=0.67, y=0.50),
        },
        bones=(("l_wrist", "l_index"), ("r_wrist", "r_index")),
    )
    hands_together = sequences.SkeletonPose(
        joints={
            "l_wrist": sequences.Point(x=0.46, y=0.60),
            "l_index": sequences.Point(x=0.49, y=0.50),
            "r_wrist": sequences.Point(x=0.54, y=0.60),
            "r_index": sequences.Point(x=0.51, y=0.50),
        },
        bones=(("l_wrist", "l_index"), ("r_wrist", "r_index")),
    )
    return sequences.MotionPlan(
        plan_id="mp_fixture00001",
        sequence_id="seq_fixture0001",
        frame_count=8,
        canvas_width=1024,
        canvas_height=576,
        description="the hands slide toward each other over 8 frames until they hold",
        subjects=(
            sequences.TrackedSubject(
                subject_id="subj_hands0001",
                label="hands",
                keyframes=(
                    sequences.SubjectKeyframe(
                        frame_index=0,
                        layout=sequences.Box(x=0.25, y=0.40, w=0.50, h=0.30),
                        pose=hands_open,
                    ),
                    sequences.SubjectKeyframe(
                        frame_index=7,
                        layout=sequences.Box(x=0.40, y=0.40, w=0.20, h=0.30),
                        pose=hands_together,
                    ),
                ),
            ),
        ),
    )


def sample_fix_plan() -> editing.FixPlan:
    return editing.FixPlan(
        findings=(
            editing.CritiqueFinding(
                category=editing.CritiqueCategory.legibility,
                severity=editing.Severity.major,
                target_unit_ids=("scn_chart000001",),
                summary="Chart labels fall below the mobile minimum font size.",
            ),
        ),
        operations=(
            editing.UpdateChartEncoding(
                scene_id="scn_chart000001", encoding_patch={"label_size": "large"}
            ),
        ),
        impact=editing.DependencyImpact(
            affected_unit_ids=("scn_chart000001",),
            invalidates=(editing.InvalidationScope.layout, editing.InvalidationScope.render),
        ),
        plain_language="Enlarge the chart labels on the chart scene; nothing else changes.",
        estimated_cost_usd=0.0,
        estimated_seconds=20,
    )


def sample_edit_batch() -> editing.EditBatch:
    return editing.EditBatch(
        batch_id="eb_fixture000001",
        project_id="prj_fixture00001",
        base_revision_hash=ZERO_HASH,
        operations=(
            editing.ReplaceTextRange(
                unit_id="txt_hook0000001", start=0, end=5, replacement="Today"
            ),
            editing.ReorderCarouselCard(
                deliverable_id="dlv_carousel0001", card_id="card_000000003", to_index=0
            ),
            editing.RetimeBeat(scene_id="scn_intro000001", duration_frames=72),
        ),
    )


def sample_workflow_package() -> comfyui.ComfyWorkflowPackage:
    return comfyui.ComfyWorkflowPackage(
        package_id="fixture.empty-image",
        version="0.1.0",
        status=comfyui.LifecycleStatus.canary,
        purpose="Core-node-only smoke workflow: EmptyImage -> SaveImage (no models required).",
        comfyui_min_version="0.3.0",
        api_workflow={
            "1": {
                "class_type": "EmptyImage",
                "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0},
            },
            "2": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "cf_fixture", "images": ["1", 0]},
            },
        },
        parameters=(
            comfyui.ParameterBinding(
                name="width", node_id="1", input_name="width", kind="int", minimum=8, maximum=512
            ),
            comfyui.ParameterBinding(
                name="height", node_id="1", input_name="height", kind="int", minimum=8, maximum=512
            ),
            comfyui.ParameterBinding(
                name="color",
                node_id="1",
                input_name="color",
                kind="int",
                minimum=0,
                maximum=16777215,
            ),
        ),
        capabilities=(comfyui.CapabilityFlag.deterministic_seed,),
        expected_outputs=("2",),
    )


def sample_hardware() -> hardware.HardwareInventory:
    return hardware.HardwareInventory(
        os="Linux",
        os_version="7.0.0-29-generic",
        arch="x86_64",
        cpu_model="12th Gen Intel(R) Core(TM) i9-12900K",
        cpu_threads=24,
        ram_bytes=33480216576,
        disk_free_bytes=400 * 1024**3,
        gpus=(
            hardware.GPUInfo(
                vendor="nvidia",
                model="NVIDIA GeForce RTX 3090",
                vram_bytes=25769803776,
                driver_version="595.84",
            ),
        ),
        runtimes=(hardware.RuntimeInfo(name="ffmpeg", version="6.1.1"),),
        probed_at="2026-08-27T00:00:00Z",
    )


WS = "ws_demo00000001"


def sample_brief() -> content.ProjectBrief:
    return content.ProjectBrief(
        brief_id="brf_demo00000001",
        workspace_id=WS,
        input_mode=content.InputMode.brief_first,
        topic="How much of Sweden's electricity came from wind in 2025?",
        objective="Explain the share and the trend in plain language.",
        audience="Curious general public",
        operator_assertions=("We are not selling anything.",),
    )


def sample_campaign() -> content.ContentCampaign:
    export = content.DestinationBinding(
        destination=content.Destination(
            destination_id="dst_export000001", platform="export", capability_revision="2026-08-27"
        ),
        visibility="export_only",
    )
    return content.ContentCampaign(
        campaign_id="cmp_demo00000001",
        workspace_id=WS,
        brief=sample_brief(),
        deliverables=(
            content.SingleImagePostSpec(
                deliverable_id="dlv_image0000001",
                title="Wind share card",
                layout="number_led",
                destinations=(export,),
            ),
            content.ShortVideoSpec(
                deliverable_id="dlv_short0000001",
                title="Wind share in 20 seconds",
                target_duration_s=(15, 30),
                destinations=(export,),
            ),
        ),
        relationships=(
            content.DeliverableRelationship(
                from_id="dlv_short0000001", to_id="dlv_image0000001", kind="companion_to"
            ),
        ),
    )


def sample_story_plan() -> scenes.StoryPlan:
    beats = (
        scenes.VisualBeat(
            beat_id="beat_000000001",
            order=0,
            display_text="In 2025, wind supplied about a fifth of Sweden's electricity.",
            claim_ids=("clm_wind0000001",),
            planned_duration_ms=4200,
        ),
        scenes.VisualBeat(
            beat_id="beat_000000002",
            order=1,
            display_text="That share has roughly doubled since 2018.",
            claim_ids=("clm_wind0000002",),
            planned_duration_ms=3800,
        ),
        scenes.VisualBeat(
            beat_id="beat_000000003",
            order=2,
            display_text="Three things drove it: new turbines, better siting, and cheaper finance.",
            planned_duration_ms=6000,
        ),
        scenes.VisualBeat(
            beat_id="beat_000000004",
            order=3,
            display_text="Sources: Energimyndigheten, Svenska kraftnät.",
            planned_duration_ms=3000,
        ),
    )
    ds = scenes.DataRef(
        dataset_id="ds_wind00000001", claim_id="clm_wind0000001", column="share_pct", row_key="2025"
    )
    sc = (
        scenes.TitleScene(
            scene_id="scn_title000001",
            beat_id="beat_000000001",
            title=scenes.TextRef(text="Sweden's wind share, 2025"),
            subtitle=scenes.TextRef(text="Electricity generation"),
        ),
        scenes.BigNumberScene(
            scene_id="scn_number00001",
            beat_id="beat_000000002",
            value=ds,
            unit="%",
            label=scenes.TextRef(text="of electricity from wind", claim_ids=("clm_wind0000001",)),
        ),
        scenes.BulletSequenceScene(
            scene_id="scn_bullets0001",
            beat_id="beat_000000003",
            title=scenes.TextRef(text="What drove it"),
            bullets=(
                scenes.TextRef(text="New turbines"),
                scenes.TextRef(text="Better siting"),
                scenes.TextRef(text="Cheaper finance"),
            ),
        ),
        scenes.SourceCardScene(
            scene_id="scn_sources0001",
            beat_id="beat_000000004",
            source_ids=("src_energimynd01", "src_svk00000001"),
        ),
    )
    return scenes.StoryPlan(
        plan_id="plan_demo0000001",
        deliverable_id="dlv_short0000001",
        fps=30,
        width=1080,
        height=1920,
        beats=beats,
        scenes=sc,
    )


def sample_artboard() -> artboards.ArtboardSpec:
    ds = scenes.DataRef(
        dataset_id="ds_wind00000001", claim_id="clm_wind0000001", column="share_pct", row_key="2025"
    )
    return artboards.ArtboardSpec(
        artboard_id="art_demo00000001",
        deliverable_id="dlv_image0000001",
        width=1080,
        height=1080,
        alt_text="Data card: about a fifth of Sweden's electricity came from wind in 2025.",
        layers=(
            artboards.TextLayer(
                layer_id="lay_label0000001",
                frame=artboards.Rect(x=0.08, y=0.10, w=0.84, h=0.08),
                reading_order=0,
                text=scenes.TextRef(text="SWEDEN · ELECTRICITY · 2025"),
                role="label",
            ),
            artboards.NumberLayer(
                layer_id="lay_number000001",
                frame=artboards.Rect(x=0.08, y=0.26, w=0.84, h=0.30),
                reading_order=1,
                value=ds,
                unit="%",
                format="integer",
            ),
            artboards.TextLayer(
                layer_id="lay_headline0001",
                frame=artboards.Rect(x=0.08, y=0.58, w=0.84, h=0.20),
                reading_order=2,
                text=scenes.TextRef(
                    text="of electricity came from wind", claim_ids=("clm_wind0000001",)
                ),
                role="headline",
                max_lines=2,
            ),
            artboards.SourceLayer(
                layer_id="lay_source000001",
                frame=artboards.Rect(x=0.08, y=0.86, w=0.84, h=0.06),
                reading_order=3,
                source_ids=("src_energimynd01",),
            ),
        ),
    )


def sample_dataset() -> render.DatasetTable:
    return render.DatasetTable(
        dataset_id="ds_wind00000001",
        classification="SOURCE_DATA",
        columns=("year", "share_pct"),
        rows=(
            {"year": "2018", "share_pct": 11},
            {"year": "2021", "share_pct": 17},
            {"year": "2025", "share_pct": 21},
        ),
        unit="%",
        source_ids=("src_energimynd01",),
        label="Wind share of Swedish electricity generation",
    )


def sample_sources() -> dict[str, render.SourceCard]:
    return {
        "src_energimynd01": render.SourceCard(
            source_id="src_energimynd01",
            title="Energy in Sweden 2026 (fixture)",
            publisher="Energimyndigheten",
            url="https://www.energimyndigheten.se/",
            accessed="2026-08-27",
        ),
        "src_svk00000001": render.SourceCard(
            source_id="src_svk00000001",
            title="Grid statistics (fixture)",
            publisher="Svenska kraftnät",
            url="https://www.svk.se/",
            accessed="2026-08-27",
        ),
    }


def sample_artboard_bundle() -> render.RenderBundle:
    return render.RenderBundle(
        bundle_id="bnd_art000000001",
        kind="artboard",
        artboard=sample_artboard(),
        datasets={"ds_wind00000001": sample_dataset()},
        sources=sample_sources(),
    )


def sample_timeline_bundle() -> render.RenderBundle:
    from content_factory.timeline.compiler import compile_timeline

    plan = sample_story_plan()
    tl = compile_timeline(plan, timeline_id="tl_demo000000001", narrated=False)
    return render.RenderBundle(
        bundle_id="bnd_tl0000000001",
        kind="timeline",
        plan=plan,
        timeline=tl,
        datasets={"ds_wind00000001": sample_dataset()},
        sources=sample_sources(),
    )


def all_fixtures() -> dict[str, list[Any]]:
    mp = sample_motion_plan()
    return {
        "MotionPlan": [mp],
        "FixPlan": [sample_fix_plan()],
        "RevisionOutcome": [
            sample_fix_plan(),
            editing.ClarifyingQuestion(question="Which card do you mean — the first or the third?"),
            editing.Refusal(
                policy="citations_required",
                reason="Source citations are required by the research policy and cannot be removed.",  # noqa: E501
            ),
        ],
        "EditBatch": [sample_edit_batch()],
        "ComfyWorkflowPackage": [sample_workflow_package()],
        "HardwareInventory": [sample_hardware()],
        "ContentCampaign": [sample_campaign()],
        "StoryPlan": [sample_story_plan()],
        "ArtboardSpec": [sample_artboard()],
        "RenderBundle": [sample_artboard_bundle(), sample_timeline_bundle()],
        "DatasetTable": [sample_dataset()],
    }


def invalid_fixtures() -> dict[str, list[dict[str, Any]]]:
    """Instances that MUST be rejected by every validator (unknown field, bad enum, bad id)."""
    mp = sample_motion_plan().model_dump(mode="json")
    bad_extra = {**mp, "unexpected": 1}
    bad_id = {**mp, "plan_id": "not an id"}
    fp = sample_fix_plan().model_dump(mode="json")
    bad_op = json.loads(json.dumps(fp))
    bad_op["operations"][0]["op"] = "delete_everything"
    art = sample_artboard().model_dump(mode="json")
    bad_layer = json.loads(json.dumps(art))
    bad_layer["layers"][0]["kind"] = "arbitrary_css"
    camp = sample_campaign().model_dump(mode="json")
    bad_dest = json.loads(json.dumps(camp))
    bad_dest["deliverables"][0]["destinations"][0]["visibility"] = "everywhere"
    return {
        "MotionPlan": [bad_extra, bad_id],
        "FixPlan": [bad_op],
        "ArtboardSpec": [bad_layer],
        "ContentCampaign": [bad_dest],
    }


def export_fixtures(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    valid = {k: [m.model_dump(mode="json") for m in v] for k, v in all_fixtures().items()}
    (out_dir / "valid.json").write_text(
        json.dumps(valid, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "invalid.json").write_text(
        json.dumps(invalid_fixtures(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
