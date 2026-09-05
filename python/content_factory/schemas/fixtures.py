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
    shots,
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


def sample_ltx_i2v_package() -> comfyui.ComfyWorkflowPackage:
    """Image-to-video on the local LTX-2.5 22B distilled GGUF stack (the operator's chosen video
    generator, 2026-09-05); mirrors the graph proven by projects/showcase/bin/showcase_i2v.py.
    Every tunable is a bound parameter. Text-to-video is deliberately absent: this GGUF packaging
    ships no gemma tokenizer, so the first frame must come from the image stack."""
    return comfyui.ComfyWorkflowPackage(
        package_id="ltx-2.5.i2v",
        version="0.1.0",
        status=comfyui.LifecycleStatus.canary,
        purpose="First frame + motion prompt to a short silent clip via LTX-2.5 image-to-video.",
        comfyui_min_version="0.33.0",
        api_workflow={
            "1": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {"unet_name": "ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf"},
            },
            "2": {
                "class_type": "CLIPLoaderGGUF",
                "inputs": {
                    "clip_name": "gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf",
                    "type": "ltxv",
                },
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": "ltx-2.5-video-vae-conv-bf16.safetensors"},
            },
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["2", 0],
                    "text": "blurry, distorted, static, still image, watermark, text",
                },
            },
            "6": {
                "class_type": "LTXVConditioning",
                "inputs": {"positive": ["4", 0], "negative": ["5", 0], "frame_rate": 24.0},
            },
            "7": {"class_type": "LoadImage", "inputs": {"image": ""}},
            "8": {
                "class_type": "LTXVImgToVideo",
                "inputs": {
                    "positive": ["6", 0],
                    "negative": ["6", 1],
                    "vae": ["3", 0],
                    "image": ["7", 0],
                    "width": 512,
                    "height": 896,
                    "length": 73,
                    "batch_size": 1,
                    "strength": 1.0,
                },
            },
            "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
            "10": {
                "class_type": "CFGGuider",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["8", 0],
                    "negative": ["8", 1],
                    "cfg": 1.0,
                },
            },
            "11": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_ancestral"}},
            "12": {
                "class_type": "ManualSigmas",
                "inputs": {
                    # official 8-step distilled schedule
                    "sigmas": "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
                },
            },
            "13": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["9", 0],
                    "guider": ["10", 0],
                    "sampler": ["11", 0],
                    "sigmas": ["12", 0],
                    "latent_image": ["8", 2],
                },
            },
            "14": {
                "class_type": "VAEDecodeTiled",
                "inputs": {
                    "samples": ["13", 0],
                    "vae": ["3", 0],
                    "tile_size": 512,
                    "overlap": 64,
                    "temporal_size": 128,
                    "temporal_overlap": 32,
                },
            },
            "15": {"class_type": "CreateVideo", "inputs": {"images": ["14", 0], "fps": 24.0}},
            "16": {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": ["15", 0],
                    "filename_prefix": "cf_ltx",
                    "format": "mp4",
                    "codec": "h264",
                },
            },
        },
        parameters=(
            comfyui.ParameterBinding(name="prompt", node_id="4", input_name="text", kind="string"),
            comfyui.ParameterBinding(
                name="first_frame", node_id="7", input_name="image", kind="image_ref"
            ),
            comfyui.ParameterBinding(
                name="width", node_id="8", input_name="width", kind="int", minimum=256, maximum=1344
            ),
            comfyui.ParameterBinding(
                name="height",
                node_id="8",
                input_name="height",
                kind="int",
                minimum=256,
                maximum=1344,
            ),
            comfyui.ParameterBinding(
                name="length",
                node_id="8",
                input_name="length",
                kind="int",
                minimum=9,
                maximum=257,
                description="frame count; LTX wants 8k+1 (73 = 3.0 s at 24 fps)",
            ),
            comfyui.ParameterBinding(
                name="seed", node_id="9", input_name="noise_seed", kind="seed"
            ),
        ),
        required_models=(
            comfyui.RequiredModel(
                filename="ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf",
                relative_path="models/diffusion_models",
                source_url="https://huggingface.co/elix3r/LTX-2.5-22b-distilled-GGUF",
                license="community quant of Lightricks/LTX-2.5 — see model card",
            ),
            comfyui.RequiredModel(
                filename="gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf",
                relative_path="models/text_encoders",
                source_url="https://huggingface.co/elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF",
                license="community quant — see model card (Gemma terms apply)",
            ),
            comfyui.RequiredModel(
                filename="ltx-2.5-video-vae-conv-bf16.safetensors",
                relative_path="models/vae",
                source_url="https://huggingface.co/Lightricks/LTX-2.5",
                license="Lightricks LTX-2.5 terms (gated) — see model card",
            ),
        ),
        custom_nodes=(
            comfyui.PinnedNode(
                registry_name="ComfyUI-GGUF",
                version="6ea2651",
                commit="6ea2651",
                license="Apache-2.0",
                # local checkout carries a gemma4-arch patch in loader.py (2026-09-05)
            ),
        ),
        capabilities=(
            comfyui.CapabilityFlag.image_to_video,
            comfyui.CapabilityFlag.deterministic_seed,
        ),
        max_resolution=(1344, 1344),
        expected_outputs=("16",),
    )


def sample_shot_plan() -> shots.ShotPlan:
    """Two 4-second shots at 24 fps: a slow push-in on a standing man, then a pan past him."""
    man = shots.CharacterSpec(
        id="man",
        asset="man_01",
        transform=shots.Transform(position=(0.0, 0.0, 0.0), yaw_deg=0.0),
        pose=shots.LibraryPose(name="stand_relaxed"),
        appearance="a man in his forties, short dark hair, plain grey work coat",
    )
    bench = shots.PropSpec(
        id="bench",
        source=shots.PrimitiveSource(shape="cube", size=(1.6, 0.5, 0.45)),
        transform=shots.Transform(position=(0.0, -1.2, 0.225)),
        color=(0.4, 0.3, 0.2),
    )
    push_in = shots.CameraSpec(
        preset=shots.CameraPreset.slow_push_in,
        keyframes=(
            shots.CameraKeyframe(
                frame_index=0, position=(0.0, -6.0, 1.6), look_at=(0.0, 0.0, 1.0), lens_mm=24.0
            ),
            shots.CameraKeyframe(
                frame_index=96, position=(0.0, -3.2, 1.6), look_at=(0.0, 0.0, 1.2), lens_mm=35.0
            ),
        ),
    )
    pan_right = shots.CameraSpec(
        preset=shots.CameraPreset.pan_right,
        keyframes=(
            shots.CameraKeyframe(
                frame_index=0, position=(-2.5, -4.0, 1.5), look_at=(0.0, 0.0, 1.1), lens_mm=35.0
            ),
            shots.CameraKeyframe(
                frame_index=96, position=(2.5, -4.0, 1.5), look_at=(0.0, 0.0, 1.1), lens_mm=35.0
            ),
        ),
    )
    common = {
        "frame_count": 97,
        "fps": 24,
        "width": 1024,
        "height": 576,
        "characters": (man,),
        "props": (bench,),
        "anchor_frames": (0, 96),
    }
    return shots.ShotPlan(
        plan_id="shp_fixture00001",
        deliverable_id="dlv_fixture00001",
        planner="fixture",
        planner_version="0.1.0",
        shots=(
            shots.ShotSpec(
                shot_id="shot_fixture0001",
                order=0,
                camera=push_in,
                description=(
                    "a wide shot of one figure standing beside a bench, on open level ground,"
                    " even studio light from a single soft key"
                ),
                action="the man shifts his weight and looks off to the left",
                motion_prompt=(
                    "the man shifts his weight and looks off to the left;"
                    " the camera pushes slowly in"
                ),
                end_state="the framing has closed to a medium shot on his face",
                **common,
            ),
            shots.ShotSpec(
                shot_id="shot_fixture0002",
                order=1,
                camera=pan_right,
                description=(
                    "a medium shot of one figure from his left, on open level ground,"
                    " even studio light from a single soft key"
                ),
                motion_prompt=(
                    "the subject holds the pose without moving;"
                    " the camera pans steadily to the right"
                ),
                **common,
            ),
        ),
    )


def sample_shot_routing() -> shots.ShotRouting:
    """The hybrid workflow's routing for the sample story: the title beat is generated (Blender ->
    HiDream -> LTX), the data beats are rendered by Remotion."""
    story = sample_story_plan()
    plan = sample_shot_plan()
    kinds = {sc.beat_id: sc.kind for sc in story.scenes}
    beats = []
    for i, beat in enumerate(story.beats):
        generate = i == 0
        beats.append(
            shots.BeatRoute(
                beat_id=beat.beat_id,
                order=beat.order,
                scene_kind=kinds.get(beat.beat_id, "default"),
                route="generate" if generate else "render",
                shot_id=plan.shots[0].shot_id if generate else None,
                reason="scene kind title is in generate_kinds" if generate else "default route",
            )
        )
    return shots.ShotRouting(
        routing_id="route_demo00000001",
        deliverable_id=story.deliverable_id,
        story_plan_hash=story.content_hash(),
        shot_plan_hash=plan.content_hash(),
        router_version="0.1.0",
        beats=tuple(beats),
    )


def sample_control_bundle() -> shots.ControlBundle:
    """The MotionPlan fixture compiled into the shared bundle shape (hashes are placeholders)."""
    mp = sample_motion_plan()
    plan_hash = mp.content_hash()

    def _track(kind: sequences.ControlKind) -> shots.ControlTrack:
        return shots.ControlTrack(
            kind=kind,
            encoding=shots.ControlEncoding.rgb8,
            frames=tuple(
                sequences.ControlAsset(
                    kind=kind,
                    frame_index=i,
                    width=mp.canvas_width,
                    height=mp.canvas_height,
                    png_sha256=ZERO_HASH,
                    motion_plan_hash=plan_hash,
                    compiler_version="0.1.0",
                    compiler="motion_plan",
                    shot_id=mp.sequence_id,
                )
                for i in range(mp.frame_count)
            ),
        )

    subject = mp.subjects[0]
    first, last = subject.keyframes[0], subject.keyframes[-1]
    layouts: list[sequences.Box | None] = [None] * mp.frame_count
    poses: list[sequences.SkeletonPose | None] = [None] * mp.frame_count
    layouts[0], layouts[-1] = first.layout, last.layout
    poses[0], poses[-1] = first.pose, last.pose
    return shots.ControlBundle(
        bundle_id="cbd_fixture00001",
        shot_id=mp.sequence_id,
        plan_hash=plan_hash,
        compiler="motion_plan",
        compiler_version="0.1.0",
        width=mp.canvas_width,
        height=mp.canvas_height,
        frame_count=mp.frame_count,
        fps=24,
        anchor_frames=(0,),
        tracks=(
            _track(sequences.ControlKind.pose_skeleton),
            _track(sequences.ControlKind.layout_boxes),
        ),
        subjects=(
            shots.SubjectTrack(
                subject_id="hands",
                label=subject.label,
                segmentation_index=1,
                layouts=tuple(layouts),
                poses=tuple(poses),
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
        # What the film looks like, for the image and video models. The beats above are narration
        # and must never reach one: see shots/prompt_compile.py.
        visual_subject="a Swedish coastal wind farm under a flat overcast sky",
    )


def sample_new_kinds_plan() -> scenes.StoryPlan:
    """A plan exercising the scene kinds and fields that were contract-only until now.

    A second StoryPlan fixture rather than an extension of :func:`sample_story_plan`, which the
    offline demo renders and asserts against beat by beat. What this one is for is the validators:
    every generated Ajv validator sees it, so `image`, `comparison`, `flow_diagram`, the `step`
    chart kind, `ChartScene.source_ids` and `background_asset_id` all have a valid instance to be
    checked against instead of only a schema.
    """
    table = scenes.DataRef(dataset_id="ds_wind00000001", column="share_pct")
    beats = tuple(
        scenes.VisualBeat(
            beat_id=f"beat_{i + 1:09d}", order=i, display_text=text, planned_duration_ms=3000
        )
        for i, text in enumerate(
            [
                "A title over the shot it is about.",
                "The shot itself, held while the narrator speaks.",
                "Then against now, side by side.",
                "What actually caused it.",
                "The value held, and then it changed.",
            ]
        )
    )
    return scenes.StoryPlan(
        plan_id="plan_kinds000001",
        deliverable_id="dlv_short0000001",
        fps=30,
        width=1080,
        height=1920,
        beats=beats,
        scenes=(
            scenes.TitleScene(
                scene_id="scn_title000002",
                beat_id="beat_000000001",
                title=scenes.TextRef(text="Sweden's wind share"),
                subtitle=scenes.TextRef(text="Over a still from the shot"),
                background_asset_id="ast_anchor000001",
            ),
            scenes.ImageScene(
                scene_id="scn_image000001",
                beat_id="beat_000000002",
                asset_id="ast_anchor000001",
                alt_text="A coastal wind farm under a flat overcast sky.",
                caption=scenes.TextRef(text="Kriegers Flak, looking south."),
                motion="slow_push",
            ),
            scenes.ComparisonScene(
                scene_id="scn_compare0001",
                beat_id="beat_000000003",
                title=scenes.TextRef(text="2018 against 2025"),
                left=scenes.TextRef(text="2018"),
                right=scenes.TextRef(text="2025"),
                left_value=scenes.DataRef(
                    dataset_id="ds_wind00000001", column="share_pct", row_key="2018"
                ),
                right_value=scenes.DataRef(
                    dataset_id="ds_wind00000001", column="share_pct", row_key="2025"
                ),
            ),
            scenes.FlowDiagramScene(
                scene_id="scn_flow0000001",
                beat_id="beat_000000004",
                title=scenes.TextRef(text="What drove it"),
                nodes=(
                    scenes.DiagramNode(
                        node_id="permits", label=scenes.TextRef(text="Faster permits")
                    ),
                    scenes.DiagramNode(
                        node_id="finance", label=scenes.TextRef(text="Cheaper finance")
                    ),
                    scenes.DiagramNode(
                        node_id="build", label=scenes.TextRef(text="More turbines built")
                    ),
                    scenes.DiagramNode(
                        node_id="share", label=scenes.TextRef(text="A higher wind share")
                    ),
                ),
                edges=(
                    scenes.DiagramEdge(from_id="permits", to_id="build"),
                    scenes.DiagramEdge(from_id="finance", to_id="build"),
                    scenes.DiagramEdge(from_id="build", to_id="share", label="measured"),
                ),
            ),
            scenes.ChartScene(
                scene_id="scn_step00000001",
                beat_id="beat_000000005",
                chart=scenes.ChartKind.step,
                data=table,
                x="year",
                y=("share_pct",),
                title=scenes.TextRef(text="Wind share, by year"),
                source_ids=("src_energimynd01",),
            ),
        ),
        visual_subject="a Swedish coastal wind farm under a flat overcast sky",
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
    # Local imports: the documentary builders live above the schemas layer.
    from content_factory.deliverables.documentary import (
        documentary_campaign,
        episode_metadata,
        episode_outline,
        plan_shorts,
    )
    from content_factory.style.editorial import EDITORIAL_KIT

    doc = documentary_campaign(
        workspace_id=WS, topic="How undersea cables carry the internet", shorts=2
    )
    outline = episode_outline(
        deliverable_id=doc.deliverables[0].deliverable_id, target_duration_s=600
    )
    from content_factory.placement import choose_placement
    from content_factory.schemas.energy import EnergyReport, EnergyShare
    from content_factory.schemas.nodes import EncoderCapability, NodeCapabilityReport, VolumeInfo
    from content_factory.schemas.offers import ComputeOffer, OfferPrice
    from content_factory.schemas.placement import PlacementCandidate, PlacementRequest
    from content_factory.schemas.storage_plan import RoleAssignment, StoragePlan, StorageRole

    node_report = NodeCapabilityReport(
        node_id="nde_fixture0001",
        hostname="fixture-node",
        discovered_at="2026-09-05T12:00:00+00:00",
        inventory=sample_hardware(),
        volumes=(
            VolumeInfo(
                volume_id="uuid:fixture-a",
                mount_path="/mnt/fast",
                filesystem="ext4",
                media_class="nvme",
                total_bytes=2 << 40,
                free_bytes=1 << 40,
                writable=True,
            ),
        ),
        encoders=(
            EncoderCapability(name="h264_nvenc", status="verified", detail="test encode ok"),
        ),
        admitted_roles=("cpu", "gpu"),
    )
    offer = ComputeOffer(
        offer_id="fixture/rtx4090/1",
        source_api="fixture",
        provider="fixture-cloud",
        retrieved_at="2026-09-05T12:00:00+00:00",
        availability="available",
        gpu_model="RTX 4090",
        gpu_count=1,
        vram_gb_per_gpu=24,
        price=OfferPrice(currency="USD", usd_per_hour=0.5, original_amount_per_hour=0.5),
    )
    decision = choose_placement(
        PlacementRequest(task_fingerprint="fixture-task"),
        [
            PlacementCandidate(
                candidate_id="fixture/rtx4090/1",
                kind="cloud_offer",
                vram_gb=24,
                rate_usd_per_hour=0.5,
                expected_useful_execution_s=1800,
            )
        ],
        decided_at="2026-09-05T12:00:00+00:00",
    )
    storage_plan = StoragePlan(
        node_id="nde_fixture0001",
        assignments=(
            RoleAssignment(
                role=StorageRole.scratch, volume_id="uuid:fixture-a", root_path="/mnt/fast/scratch"
            ),
            RoleAssignment(
                role=StorageRole.backup, volume_id="uuid:fixture-b", root_path="/mnt/usb/backup"
            ),
        ),
    )
    energy_report = EnergyReport(
        node_id="nde_fixture0001",
        source="wall_meter",
        window_start="2026-09-05T00:00:00+00:00",
        window_end="2026-09-05T04:00:00+00:00",
        kwh_measured=4.0,
        attribution=(
            EnergyShare(key="job-1", kwh=3.0),
            EnergyShare(key="idle", kwh=1.0),
        ),
    )
    mp = sample_motion_plan()
    return {
        "NodeCapabilityReport": [node_report],
        "ComputeOffer": [offer],
        "PlacementDecision": [decision],
        "StoragePlan": [storage_plan],
        "EnergyReport": [energy_report],
        "EpisodeOutline": [outline],
        "ShortsPlan": [
            plan_shorts(sample_story_plan(), [d.deliverable_id for d in doc.deliverables[1:]])
        ],
        "EpisodeMetadata": [episode_metadata(doc.brief, outline)],
        "EditorialStyleKit": [EDITORIAL_KIT],
        "MotionPlan": [mp],
        "ShotPlan": [sample_shot_plan()],
        "ShotSpec": list(sample_shot_plan().shots),
        "ControlBundle": [sample_control_bundle()],
        "ShotRouting": [sample_shot_routing()],
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
        "StoryPlan": [sample_story_plan(), sample_new_kinds_plan()],
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
    outline = all_fixtures()["EpisodeOutline"][0].model_dump(mode="json")
    bad_section_kind = json.loads(json.dumps(outline))
    bad_section_kind["sections"][0]["kind"] = "intro"
    bad_outline_extra = {**outline, "unexpected": 1}
    offer = all_fixtures()["ComputeOffer"][0].model_dump(mode="json")
    bad_offer_mode = json.loads(json.dumps(offer))
    bad_offer_mode["purchase_mode"] = "handshake_deal"
    sp = sample_shot_plan().model_dump(mode="json")
    bad_planner = json.loads(json.dumps(sp))
    bad_planner["planner"] = "vibes"
    bad_shot_pass = json.loads(json.dumps(sp))
    bad_shot_pass["shots"][0]["render"]["passes"] = ["hologram"]
    # An appearance and an action are prompt text, so "" is not a permissive default but a shot
    # that silently tells the image model nothing. Both are bounded on the low side too.
    bad_appearance = json.loads(json.dumps(sp))
    bad_appearance["shots"][0]["characters"][0]["appearance"] = ""
    bad_end_state = json.loads(json.dumps(sp))
    bad_end_state["shots"][0]["end_state"] = "x" * 601
    story = sample_story_plan().model_dump(mode="json")
    bad_visual_subject = {**story, "visual_subject": ""}
    kinds = sample_new_kinds_plan().model_dump(mode="json")
    # "step_chart" is the obvious wrong spelling of the kind added with the step renderer, and an
    # enum is the only thing standing between a typo and a chart drawn as bars without saying so.
    bad_chart_kind = json.loads(json.dumps(kinds))
    bad_chart_kind["scenes"][4]["chart"] = "step_chart"
    # A comparison's two figures are optional; a third one is not a field at all.
    bad_comparison_field = json.loads(json.dumps(kinds))
    bad_comparison_field["scenes"][2]["middle_value"] = {"dataset_id": "ds_wind00000001"}
    # An asset id is an opaque id, not a path: accepting one would put a filesystem path from a
    # plan straight into the renderer's `staticFile()`.
    bad_asset_id = json.loads(json.dumps(kinds))
    bad_asset_id["scenes"][1]["asset_id"] = "../../etc/passwd"
    routing = sample_shot_routing().model_dump(mode="json")
    bad_route = json.loads(json.dumps(routing))
    bad_route["beats"][1]["route"] = "hand_drawn"
    return {
        "MotionPlan": [bad_extra, bad_id],
        "ShotPlan": [
            {**sp, "unexpected": 1},
            bad_planner,
            bad_shot_pass,
            bad_appearance,
            bad_end_state,
        ],
        "ShotSpec": [{**sp["shots"][0], "camera_speed": 3}],
        "StoryPlan": [
            {**story, "unexpected": 1},
            bad_visual_subject,
            bad_chart_kind,
            bad_comparison_field,
            bad_asset_id,
        ],
        "ControlBundle": [{**sample_control_bundle().model_dump(mode="json"), "compiler": "hand"}],
        # A generate-routed beat with no shot_id is rejected by BeatRoute's model_validator, not
        # by the JSON Schema: it is a cross-field rule, which JSON Schema cannot express. It lives
        # in tests/unit/test_shot_router.py, where the Pydantic validator is the thing under test.
        "ShotRouting": [
            {**sample_shot_routing().model_dump(mode="json"), "unexpected": 1},
            bad_route,
        ],
        "FixPlan": [bad_op],
        "ArtboardSpec": [bad_layer],
        "ContentCampaign": [bad_dest],
        "EpisodeOutline": [bad_section_kind, bad_outline_extra],
        "ComputeOffer": [{**offer, "unexpected": 1}, bad_offer_mode],
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
