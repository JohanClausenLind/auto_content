"""FLUX.2-dev multi-reference image workflow packages (ComfyUI API graphs)."""

from __future__ import annotations

from typing import Any

from content_factory.schemas import comfyui

PACKAGE_VERSION = "0.1.0"

TRANSFORMER = "flux2-dev-Q4_K_M.gguf"
TEXT_ENCODER = "mistral_3_small_flux2_fp8.safetensors"
VAE = "flux2-vae.safetensors"
TURBO_LORA = "Flux_2-Turbo-LoRA_comfyui.safetensors"

MAX_REFERENCES = 6
"""How many ``ReferenceLatent`` links the package will build. Six covers the roles the operator
named — character, clothes, scene, style, props, pose — and the cap is here so a caller cannot
grow the graph without a version bump."""

TURBO_STEPS = 8
"""With the Turbo LoRA. Without it FLUX.2-dev wants tens of steps, which at 4 MP on a 24 GB card
is the difference between a usable lane and an overnight one."""

FULL_STEPS = 28
GUIDANCE = 4.0
"""FLUX.2-dev is a guidance-distilled model that still takes a guidance embedding; 4.0 is the
value its own model card documents. Not to be confused with CFG, which stays at 1.0 — running both
would double the work per step for nothing."""

TURBO_GUIDANCE = 1.0
"""The Turbo LoRA is trained at low guidance; leaving it at 4.0 over-drives the few steps it has."""

# Node ids are stable strings so a caller can target them by id.
N_UNET, N_LORA, N_CLIP, N_VAE = "1", "2", "3", "4"
N_POS, N_LATENT, N_GUIDANCE = "5", "6", "7"
N_NOISE, N_GUIDER, N_SAMPLER_SEL, N_SIGMAS = "8", "9", "10", "11"
N_SAMPLER, N_DECODE, N_SAVE = "12", "13", "14"
REFERENCE_NODE_BASE = 20
"""Reference ``i`` (1-based) uses LoadImage id 20+3i-2, VAEEncode id 20+3i-1, ReferenceLatent
id 20+3i. Kept clear of the base graph's ids so adding a reference never renumbers it."""


def reference_node_ids(i: int) -> tuple[str, str, str]:
    """``(load_image_id, vae_encode_id, reference_latent_id)`` for 1-based reference ``i``."""
    base = REFERENCE_NODE_BASE + 3 * i
    return str(base - 2), str(base - 1), str(base)


def reference_param_name(i: int) -> str:
    """The public parameter carrying reference ``i``'s uploaded filename."""
    return f"reference_{i}"


def _base_workflow(
    *, width: int, height: int, steps: int, guidance: float, turbo: bool
) -> dict[str, dict[str, Any]]:
    model_source: list[Any] = [N_LORA if turbo else N_UNET, 0]
    workflow: dict[str, dict[str, Any]] = {
        N_UNET: {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": TRANSFORMER}},
        N_CLIP: {
            "class_type": "CLIPLoader",
            # The transformer needs the whole card, and these two never run at the same moment:
            # the prompt is encoded once, then the encoder is dead weight for every step after.
            "inputs": {"clip_name": TEXT_ENCODER, "type": "flux2", "device": "cpu"},
        },
        N_VAE: {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        N_POS: {"class_type": "CLIPTextEncode", "inputs": {"clip": [N_CLIP, 0], "text": ""}},
        N_LATENT: {
            "class_type": "EmptyFlux2LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        N_GUIDANCE: {
            "class_type": "FluxGuidance",
            # Rewired below once the reference chain is built, so guidance always sits at the end
            # of the conditioning and applies to the composed result rather than to bare text.
            "inputs": {"conditioning": [N_POS, 0], "guidance": guidance},
        },
        N_NOISE: {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        N_GUIDER: {
            "class_type": "BasicGuider",
            # BasicGuider, not CFGGuider: FLUX.2-dev is guidance-distilled, so the negative branch
            # a CFG guider needs would cost a second forward pass per step and buy nothing.
            "inputs": {"model": model_source, "conditioning": [N_GUIDANCE, 0]},
        },
        N_SAMPLER_SEL: {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        N_SIGMAS: {
            "class_type": "Flux2Scheduler",
            # FLUX.2's own schedule, which shifts with resolution — the reason it takes width and
            # height at all, and the reason a generic scheduler is the wrong choice here.
            "inputs": {"steps": steps, "width": width, "height": height},
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
            "inputs": {"images": [N_DECODE, 0], "filename_prefix": "cf_flux2"},
        },
    }
    if turbo:
        workflow[N_LORA] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": [N_UNET, 0], "lora_name": TURBO_LORA, "strength_model": 1.0},
        }
    return workflow


def _base_parameters(*, width: int, height: int) -> list[comfyui.ParameterBinding]:
    return [
        comfyui.ParameterBinding(
            name="prompt",
            node_id=N_POS,
            input_name="text",
            kind="string",
            description="what to draw; FLUX.2 takes long natural-language prompts",
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
            name="steps",
            node_id=N_SIGMAS,
            input_name="steps",
            kind="int",
            minimum=1,
            maximum=100,
            description="8 with the Turbo LoRA, 28 without",
        ),
        comfyui.ParameterBinding(
            name="guidance",
            node_id=N_GUIDANCE,
            input_name="guidance",
            kind="float",
            minimum=0.0,
            maximum=20.0,
        ),
        # The scheduler shifts its sigmas by resolution: bind width and height alongside the
        # latent's, or the schedule is solved for a size the graph is not rendering.
        comfyui.ParameterBinding(
            name="sigma_width",
            node_id=N_SIGMAS,
            input_name="width",
            kind="int",
            minimum=256,
            maximum=4096,
            description=f"keep equal to width (default {width})",
        ),
        comfyui.ParameterBinding(
            name="sigma_height",
            node_id=N_SIGMAS,
            input_name="height",
            kind="int",
            minimum=256,
            maximum=4096,
            description=f"keep equal to height (default {height})",
        ),
    ]


def _required_models(*, turbo: bool) -> tuple[comfyui.RequiredModel, ...]:
    models = [
        comfyui.RequiredModel(
            filename=TRANSFORMER,
            relative_path="models/diffusion_models",
            source_url="https://huggingface.co/city96/FLUX.2-dev-gguf",
            license="FLUX.2-dev non-commercial licence — see model card",
            size_bytes=20_146_000_000,
        ),
        comfyui.RequiredModel(
            filename=TEXT_ENCODER,
            relative_path="models/text_encoders",
            source_url="https://huggingface.co/Comfy-Org/flux2-dev",
            license="Mistral-Small-3.2 terms (Apache-2.0) — see model card",
            size_bytes=18_040_000_000,
        ),
        comfyui.RequiredModel(
            filename=VAE,
            relative_path="models/vae",
            source_url="https://huggingface.co/Comfy-Org/flux2-dev",
            license="FLUX.2-dev non-commercial licence — see model card",
            size_bytes=322_000_000,
        ),
    ]
    if turbo:
        models.append(
            comfyui.RequiredModel(
                filename=TURBO_LORA,
                relative_path="models/loras",
                source_url="https://huggingface.co/ByteZSzn/Flux.2-Turbo-ComfyUI",
                license="community LoRA — see model card",
                size_bytes=2_792_000_000,
            )
        )
    return tuple(models)


def _custom_nodes() -> tuple[comfyui.PinnedNode, ...]:
    return (
        comfyui.PinnedNode(
            registry_name="ComfyUI-GGUF", version="6ea2651", commit="6ea2651", license="Apache-2.0"
        ),
    )


def flux2_reference_package(
    references: int = 0,
    *,
    width: int = 1024,
    height: int = 576,
    turbo: bool = True,
    steps: int | None = None,
    guidance: float | None = None,
) -> comfyui.ComfyWorkflowPackage:
    """Text plus ``references`` reference images to one composed frame."""
    if not 0 <= references <= MAX_REFERENCES:
        msg = f"references must be between 0 and {MAX_REFERENCES}, got {references}"
        raise ValueError(msg)
    resolved_steps = steps if steps is not None else (TURBO_STEPS if turbo else FULL_STEPS)
    resolved_guidance = (
        guidance if guidance is not None else (TURBO_GUIDANCE if turbo else GUIDANCE)
    )
    workflow = _base_workflow(
        width=width,
        height=height,
        steps=resolved_steps,
        guidance=resolved_guidance,
        turbo=turbo,
    )
    parameters = _base_parameters(width=width, height=height)

    conditioning: list[Any] = [N_POS, 0]
    for i in range(1, references + 1):
        load_id, encode_id, ref_id = reference_node_ids(i)
        workflow[load_id] = {"class_type": "LoadImage", "inputs": {"image": ""}}
        workflow[encode_id] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": [load_id, 0], "vae": [N_VAE, 0]},
        }
        workflow[ref_id] = {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": conditioning, "latent": [encode_id, 0]},
        }
        conditioning = [ref_id, 0]
        parameters.append(
            comfyui.ParameterBinding(
                name=reference_param_name(i),
                node_id=load_id,
                input_name="image",
                kind="image_ref",
                description=f"reference {i}, an uploaded filename",
            )
        )
    workflow[N_GUIDANCE]["inputs"]["conditioning"] = conditioning

    capabilities = [
        comfyui.CapabilityFlag.text_to_image,
        comfyui.CapabilityFlag.deterministic_seed,
    ]
    if references:
        # Every reference goes through ReferenceLatent, so the caller's ordering, not the graph,
        # says which is pose and which identity; these flags advertise what can be conditioned on.
        capabilities += [
            comfyui.CapabilityFlag.reference_image_edit,
            comfyui.CapabilityFlag.control_pose,
            comfyui.CapabilityFlag.control_depth,
        ]
    return comfyui.ComfyWorkflowPackage(
        package_id=f"flux2-dev.reference-{references}" if references else "flux2-dev.t2i",
        version=PACKAGE_VERSION,
        status=comfyui.LifecycleStatus.draft,
        purpose=(
            f"Text plus {references} reference image(s) to one composed frame via FLUX.2-dev"
            " (Q4_K_M GGUF), chained through ReferenceLatent."
            if references
            else "Text to one frame via FLUX.2-dev (Q4_K_M GGUF)."
        ),
        comfyui_min_version="0.33.0",
        api_workflow=workflow,
        parameters=tuple(parameters),
        required_models=_required_models(turbo=turbo),
        custom_nodes=_custom_nodes(),
        capabilities=tuple(capabilities),
        max_resolution=(4096, 4096),
        expected_outputs=(N_SAVE,),
    )
