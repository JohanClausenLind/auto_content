"""Ideogram 4 text-to-image workflow package (ComfyUI API graph).

The operator's chosen main model for pure text-to-image, and the graph is unlike the other two
here in one way that decides how it must be called: there is no negative *prompt*. Ideogram 4
ships as **two** transformers -- a conditional and an unconditional -- and ``DualModelGuider``
runs the pair, taking the positive conditioning through one and a zeroed copy of it through the
other. The negative branch is therefore a model, not a text box, and ``ConditioningZeroOut`` is
what feeds it. ``CFGOverride`` then raises CFG only over the last 30 % of the schedule, which is
where this model's own recipe puts the detail.

**The caption must be structured JSON, never prose, and that is not a style preference.** The
text encoder is Qwen3-VL-8B: prose goes through a chat template to an encoder that also
*generates*, and its refusals are lettered onto the canvas as pixels. Measured 2026-09-12 over the
same subject, 16 of 17 prose generations refused against 3 of 3 clean as JSON. The caption shape
(``high_level_description`` / ``style_description`` / ``compositional_deconstruction`` with a
``bounding_box`` per element) is in docs/research/2026-09-12-ideogram-4.md, and it is also where
this repo's ``layout_boxes`` finally have a native home instead of being rastered into a rectangle.

What the model will not do reliably is **count**. Measured twice over two sittings, it drew three
people for a caption and a box that said two, and four for another: a caller that needs an exact
number of figures must check the result, which is why ``qc.frame_review`` carries both a refusal
detector and a subject count. Node signatures were read from a running ComfyUI ``/object_info``,
not from memory.
"""

from __future__ import annotations

from typing import Any

from content_factory.schemas import comfyui

PACKAGE_VERSION = "0.1.0"

TRANSFORMER = "ideogram4_fp8_scaled.safetensors"
TRANSFORMER_UNCOND = "ideogram4_unconditional_fp8_scaled.safetensors"
TEXT_ENCODER = "qwen3vl_8b_fp8_scaled.safetensors"
VAE = "flux2-vae.safetensors"

# Ideogram's own three published recipes: (steps, mu, std). `mu` and `std` shape the sigma
# schedule -- Turbo shifts mu positive to spend its twelve steps lower down the curve.
PRESETS: dict[str, tuple[int, float, float]] = {
    "quality": (48, 0.0, 1.5),
    "default": (20, 0.0, 1.75),
    "turbo": (12, 0.5, 1.75),
}
DEFAULT_PRESET = "default"

# CFG 7 over the whole schedule, lifted to 3.0 over the last 30 %. Both come from the model card.
CFG = 7.0
CFG_LATE = 3.0
CFG_LATE_START = 0.7

N_UNET = "1"
N_UNET_UNCOND = "2"
N_CLIP = "3"
N_VAE = "4"
N_POS = "5"
N_NEG = "6"
N_CFG_OVERRIDE = "7"
N_GUIDER = "8"
N_SAMPLER_SEL = "9"
N_SIGMAS = "10"
N_NOISE = "11"
N_LATENT = "12"
N_SAMPLER = "13"
N_DECODE = "14"
N_SAVE = "15"


def _workflow(*, width: int, height: int, steps: int, mu: float, std: float) -> dict[str, Any]:
    return {
        N_UNET: {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": TRANSFORMER, "weight_dtype": "default"},
        },
        N_UNET_UNCOND: {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": TRANSFORMER_UNCOND, "weight_dtype": "default"},
        },
        N_CLIP: {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": TEXT_ENCODER, "type": "ideogram4", "device": "default"},
        },
        N_VAE: {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        N_POS: {"class_type": "CLIPTextEncode", "inputs": {"clip": [N_CLIP, 0], "text": ""}},
        N_NEG: {
            # The negative branch is a zeroed copy of the positive, not a second prompt: the
            # unconditional transformer is what makes it negative.
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": [N_POS, 0]},
        },
        N_CFG_OVERRIDE: {
            "class_type": "CFGOverride",
            "inputs": {
                "model": [N_UNET, 0],
                "cfg": CFG_LATE,
                "start_percent": CFG_LATE_START,
                "end_percent": 1.0,
            },
        },
        N_GUIDER: {
            "class_type": "DualModelGuider",
            "inputs": {
                "model": [N_CFG_OVERRIDE, 0],
                "positive": [N_POS, 0],
                "cfg": CFG,
                "model_negative": [N_UNET_UNCOND, 0],
                "negative": [N_NEG, 0],
            },
        },
        N_SAMPLER_SEL: {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        N_SIGMAS: {
            "class_type": "Ideogram4Scheduler",
            "inputs": {"steps": steps, "width": width, "height": height, "mu": mu, "std": std},
        },
        N_NOISE: {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        N_LATENT: {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        N_SAMPLER: {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": [N_NOISE, 0],
                "guider": [N_GUIDER, 0],
                "sampler": [N_SAMPLER_SEL, 0],
                "sigmas": [N_SIGMAS, 0],
                "latent_image": [N_LATENT, 0],
            },
        },
        N_DECODE: {
            "class_type": "VAEDecode",
            "inputs": {"samples": [N_SAMPLER, 0], "vae": [N_VAE, 0]},
        },
        N_SAVE: {
            "class_type": "SaveImage",
            "inputs": {"images": [N_DECODE, 0], "filename_prefix": "cf_ideogram4"},
        },
    }


def _parameters() -> tuple[comfyui.ParameterBinding, ...]:
    return (
        comfyui.ParameterBinding(
            name="prompt",
            node_id=N_POS,
            input_name="text",
            kind="string",
            description=(
                "a STRUCTURED JSON caption, never prose: prose reaches a generating encoder and"
                " comes back as a refusal painted into the frame"
            ),
        ),
        comfyui.ParameterBinding(
            name="width",
            node_id=N_LATENT,
            input_name="width",
            kind="int",
            minimum=256,
            maximum=4096,
        ),
        comfyui.ParameterBinding(
            name="height",
            node_id=N_LATENT,
            input_name="height",
            kind="int",
            minimum=256,
            maximum=4096,
        ),
        comfyui.ParameterBinding(
            name="seed", node_id=N_NOISE, input_name="noise_seed", kind="seed"
        ),
        comfyui.ParameterBinding(
            name="steps", node_id=N_SIGMAS, input_name="steps", kind="int", minimum=1, maximum=100
        ),
    )


def _required_models() -> tuple[comfyui.RequiredModel, ...]:
    return (
        comfyui.RequiredModel(
            filename=TRANSFORMER,
            relative_path="models/diffusion_models",
            source_url="https://huggingface.co/Comfy-Org/ideogram4",
            license="Ideogram 4 model terms — see model card",
            size_bytes=9_280_741_285,
        ),
        comfyui.RequiredModel(
            filename=TRANSFORMER_UNCOND,
            relative_path="models/diffusion_models",
            source_url="https://huggingface.co/Comfy-Org/ideogram4",
            license="Ideogram 4 model terms — see model card",
            size_bytes=9_280_741_293,
        ),
        comfyui.RequiredModel(
            filename=TEXT_ENCODER,
            relative_path="models/text_encoders",
            source_url="https://huggingface.co/Comfy-Org/ideogram4",
            license="Qwen3-VL-8B terms (Apache-2.0) — see model card",
            size_bytes=10_588_637_512,
        ),
        comfyui.RequiredModel(
            filename=VAE,
            relative_path="models/vae",
            source_url="https://huggingface.co/Comfy-Org/flux2-dev",
            license="FLUX.2-dev non-commercial licence — see model card",
            size_bytes=336_211_292,
        ),
    )


def ideogram4_package(
    *,
    width: int = 1024,
    height: int = 576,
    preset: str = DEFAULT_PRESET,
) -> comfyui.ComfyWorkflowPackage:
    """Structured JSON caption to one frame via Ideogram 4's dual-transformer guider."""
    if preset not in PRESETS:
        msg = f"preset must be one of {sorted(PRESETS)}, got {preset!r}"
        raise ValueError(msg)
    steps, mu, std = PRESETS[preset]
    return comfyui.ComfyWorkflowPackage(
        package_id=f"ideogram4.t2i-{preset}",
        version=PACKAGE_VERSION,
        status=comfyui.LifecycleStatus.draft,
        purpose=(
            f"Structured JSON caption to one frame via Ideogram 4 ({preset} recipe:"
            f" {steps} steps, mu {mu}, std {std}), dual conditional/unconditional transformers."
        ),
        comfyui_min_version="0.33.0",
        api_workflow=_workflow(width=width, height=height, steps=steps, mu=mu, std=std),
        parameters=_parameters(),
        required_models=_required_models(),
        custom_nodes=(),
        capabilities=(
            comfyui.CapabilityFlag.text_to_image,
            comfyui.CapabilityFlag.deterministic_seed,
        ),
        max_resolution=(4096, 4096),
        expected_outputs=(N_SAVE,),
    )
