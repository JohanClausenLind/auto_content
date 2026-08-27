"""Deterministic example instances of each contract, used by cross-language parity tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from content_factory.schemas import comfyui, editing, hardware, sequences

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
    }


def invalid_fixtures() -> dict[str, list[dict[str, Any]]]:
    """Instances that MUST be rejected by every validator (unknown field, bad enum, bad id)."""
    mp = sample_motion_plan().model_dump(mode="json")
    bad_extra = {**mp, "unexpected": 1}
    bad_id = {**mp, "plan_id": "not an id"}
    fp = sample_fix_plan().model_dump(mode="json")
    bad_op = json.loads(json.dumps(fp))
    bad_op["operations"][0]["op"] = "delete_everything"
    return {"MotionPlan": [bad_extra, bad_id], "FixPlan": [bad_op]}


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
