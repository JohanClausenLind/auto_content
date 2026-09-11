"""Ideogram 4's graph: the dual-transformer guider, the late CFG lift, and the three recipes.

The shape worth pinning is the one that makes this model unlike the other two image packages here.
There is no negative *prompt*: the negative branch is a second transformer, and what feeds it is a
zeroed copy of the positive conditioning. Wire that to a `CLIPTextEncode` of its own and the graph
still runs -- it just stops being the recipe the model was published with.
"""

from __future__ import annotations

import pytest

from content_factory.comfyui.client import inject_parameters
from content_factory.media.ideogram_packages import (
    CFG,
    CFG_LATE,
    CFG_LATE_START,
    N_CFG_OVERRIDE,
    N_GUIDER,
    N_LATENT,
    N_NEG,
    N_NOISE,
    N_POS,
    N_SIGMAS,
    N_UNET,
    N_UNET_UNCOND,
    PRESETS,
    TEXT_ENCODER,
    TRANSFORMER,
    TRANSFORMER_UNCOND,
    ideogram4_package,
)
from content_factory.schemas.comfyui import CapabilityFlag


def test_the_negative_branch_is_a_zeroed_positive_through_the_second_transformer() -> None:
    wf = ideogram4_package().api_workflow
    # Not a second prompt: the negative conditioning is the positive one, zeroed.
    assert wf[N_NEG]["class_type"] == "ConditioningZeroOut"
    assert wf[N_NEG]["inputs"]["conditioning"] == [N_POS, 0]
    guider = wf[N_GUIDER]
    assert guider["class_type"] == "DualModelGuider"
    assert guider["inputs"]["positive"] == [N_POS, 0]
    assert guider["inputs"]["negative"] == [N_NEG, 0]
    # The two transformers are distinct weights, and the unconditional one is the negative model.
    assert wf[N_UNET]["inputs"]["unet_name"] == TRANSFORMER
    assert wf[N_UNET_UNCOND]["inputs"]["unet_name"] == TRANSFORMER_UNCOND
    assert TRANSFORMER != TRANSFORMER_UNCOND
    assert guider["inputs"]["model_negative"] == [N_UNET_UNCOND, 0]


def test_cfg_is_lifted_only_over_the_tail_of_the_schedule() -> None:
    wf = ideogram4_package().api_workflow
    override = wf[N_CFG_OVERRIDE]
    assert override["class_type"] == "CFGOverride"
    # The override sits between the conditional transformer and the guider, so the guider's own
    # CFG is what runs early and the override is what runs late.
    assert override["inputs"]["model"] == [N_UNET, 0]
    assert wf[N_GUIDER]["inputs"]["model"] == [N_CFG_OVERRIDE, 0]
    assert wf[N_GUIDER]["inputs"]["cfg"] == CFG
    assert override["inputs"]["cfg"] == CFG_LATE
    assert 0.0 < CFG_LATE_START < 1.0
    assert override["inputs"]["start_percent"] == CFG_LATE_START
    assert override["inputs"]["end_percent"] == 1.0


def test_each_recipe_carries_its_own_steps_and_sigma_shape() -> None:
    seen = set()
    for preset, (steps, mu, std) in PRESETS.items():
        pkg = ideogram4_package(preset=preset)
        assert pkg.package_id == f"ideogram4.t2i-{preset}"
        sigmas = pkg.api_workflow[N_SIGMAS]
        assert sigmas["class_type"] == "Ideogram4Scheduler"
        assert (sigmas["inputs"]["steps"], sigmas["inputs"]["mu"], sigmas["inputs"]["std"]) == (
            steps,
            mu,
            std,
        )
        seen.add((steps, mu, std))
    assert len(seen) == len(PRESETS), "two recipes must not compile to the same schedule"


def test_an_unknown_recipe_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(ValueError, match="preset must be one of"):
        ideogram4_package(preset="cinematic")


def test_the_scheduler_is_solved_for_the_resolution_actually_rendered() -> None:
    pkg = ideogram4_package(width=768, height=1344)
    assert pkg.api_workflow[N_SIGMAS]["inputs"]["width"] == 768
    assert pkg.api_workflow[N_SIGMAS]["inputs"]["height"] == 1344
    assert pkg.api_workflow[N_LATENT]["inputs"]["width"] == 768


def test_only_declared_parameters_reach_the_graph() -> None:
    pkg = ideogram4_package()
    wf = inject_parameters(pkg, {"prompt": '{"high_level_description": "x"}', "seed": 99})
    assert wf[N_POS]["inputs"]["text"] == '{"high_level_description": "x"}'
    assert wf[N_NOISE]["inputs"]["noise_seed"] == 99
    # The caption binding says JSON, because prose is what gets refused.
    prompt_binding = next(p for p in pkg.parameters if p.name == "prompt")
    assert "JSON" in (prompt_binding.description or "")


def test_it_advertises_text_to_image_only() -> None:
    pkg = ideogram4_package()
    assert CapabilityFlag.text_to_image in pkg.capabilities
    # There is no image path into the conditioning, so it must not claim one.
    assert CapabilityFlag.reference_image_edit not in pkg.capabilities
    assert CapabilityFlag.control_pose not in pkg.capabilities
    assert not any(p.kind == "image_ref" for p in pkg.parameters)


def test_every_required_weight_the_graph_names_is_declared() -> None:
    pkg = ideogram4_package()
    declared = {m.filename for m in pkg.required_models}
    named = {
        node["inputs"][key]
        for node in pkg.api_workflow.values()
        for key in ("unet_name", "clip_name", "vae_name")
        if key in node.get("inputs", {})
    }
    assert named <= declared, f"graph names weights nothing declares: {sorted(named - declared)}"
    assert TEXT_ENCODER in declared
