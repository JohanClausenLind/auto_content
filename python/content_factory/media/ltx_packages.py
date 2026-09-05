"""LTX-2.5 image-to-video workflow packages (ComfyUI API graphs as typed ``ComfyWorkflowPackage``).

``ltx_i2v_package`` is the production twin of the ``sample_ltx_i2v_package`` fixture: the 16-node
graph proven by ``projects/showcase/bin/showcase_i2v.py`` (GGUF transformer + gemma GGUF text
encoder, ``LTXVImgToVideo`` from the first frame, the official 8-step distilled sigma schedule).

``ltx_i2v_guided_package`` adds keyframe guidance: for each guide a ``LoadImage`` +
``LTXVAddGuide`` pair is chained after ``LTXVImgToVideo`` (node signature verified in ComfyUI
0.33.0 ``comfy_extras/nodes_lt.py``: positive, negative, vae, latent, image, frame_idx, strength)
and the last guide feeds ``CFGGuider`` and ``SamplerCustomAdvanced``. This is how the Blender
scene-control layer pins the shot's camera move: HiDream anchors rendered at Blender-chosen frame
indices become guides, and LTX interpolates the motion between them. No control LoRA is needed.
"""

from __future__ import annotations

from typing import Any

from content_factory.schemas import comfyui

PACKAGE_VERSION = "0.2.0"
LTX_FRAME_MIN, LTX_FRAME_MAX = 9, 257
NEGATIVE_PROMPT = "blurry, distorted, static, still image, watermark, text"
DISTILLED_SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"

TRANSFORMER = "ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf"
TEXT_ENCODER = "gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf"
VAE = "ltx-2.5-video-vae-conv-bf16.safetensors"

# Node ids are stable strings so callers (and the guided builder) can target them by id.
N_UNET, N_CLIP, N_VAE, N_POS, N_NEG, N_COND = "1", "2", "3", "4", "5", "6"
N_FIRST, N_I2V, N_NOISE, N_GUIDER, N_SAMPLER_SEL, N_SIGMAS = "7", "8", "9", "10", "11", "12"
N_SAMPLER, N_DECODE, N_VIDEO, N_SAVE = "13", "14", "15", "16"
GUIDE_NODE_BASE = 16  # guide i (1-based) uses LoadImage id 16+2i-1 and LTXVAddGuide id 16+2i


def snap_length(frames: int) -> int:
    """Nearest 8k+1 in [9, 257] (LTX-2.5 generates 8k+1 frames)."""
    k = max(1, round((frames - 1) / 8))
    return min(max(8 * k + 1, LTX_FRAME_MIN), LTX_FRAME_MAX)


def _base_workflow(
    *,
    width: int,
    height: int,
    length: int,
    fps: float,
    unet_name: str = TRANSFORMER,
    clip_name: str = TEXT_ENCODER,
    vae_name: str = VAE,
) -> dict[str, dict[str, Any]]:
    """The 16-node graph. The three weight names are arguments because the ``generate_video`` node
    declares them as widgets: a machine with a different quantisation on disk must be able to name
    it without editing this module. They default to the pins the showcase lane was proven on."""
    return {
        N_UNET: {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": unet_name}},
        N_CLIP: {
            "class_type": "CLIPLoaderGGUF",
            "inputs": {"clip_name": clip_name, "type": "ltxv"},
        },
        N_VAE: {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        N_POS: {"class_type": "CLIPTextEncode", "inputs": {"clip": [N_CLIP, 0], "text": ""}},
        N_NEG: {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": [N_CLIP, 0], "text": NEGATIVE_PROMPT},
        },
        N_COND: {
            "class_type": "LTXVConditioning",
            "inputs": {"positive": [N_POS, 0], "negative": [N_NEG, 0], "frame_rate": float(fps)},
        },
        N_FIRST: {"class_type": "LoadImage", "inputs": {"image": ""}},
        N_I2V: {
            "class_type": "LTXVImgToVideo",
            "inputs": {
                "positive": [N_COND, 0],
                "negative": [N_COND, 1],
                "vae": [N_VAE, 0],
                "image": [N_FIRST, 0],
                "width": width,
                "height": height,
                "length": length,
                "batch_size": 1,
                "strength": 1.0,
            },
        },
        N_NOISE: {"class_type": "RandomNoise", "inputs": {"noise_seed": 0}},
        N_GUIDER: {
            "class_type": "CFGGuider",
            "inputs": {
                "model": [N_UNET, 0],
                "positive": [N_I2V, 0],
                "negative": [N_I2V, 1],
                "cfg": 1.0,
            },
        },
        N_SAMPLER_SEL: {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler_ancestral"},
        },
        N_SIGMAS: {"class_type": "ManualSigmas", "inputs": {"sigmas": DISTILLED_SIGMAS}},
        N_SAMPLER: {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": [N_NOISE, 0],
                "guider": [N_GUIDER, 0],
                "sampler": [N_SAMPLER_SEL, 0],
                "sigmas": [N_SIGMAS, 0],
                "latent_image": [N_I2V, 2],
            },
        },
        N_DECODE: {
            "class_type": "VAEDecodeTiled",
            "inputs": {
                "samples": [N_SAMPLER, 0],
                "vae": [N_VAE, 0],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 128,
                "temporal_overlap": 32,
            },
        },
        N_VIDEO: {
            "class_type": "CreateVideo",
            "inputs": {"images": [N_DECODE, 0], "fps": float(fps)},
        },
        N_SAVE: {
            "class_type": "SaveVideo",
            "inputs": {
                "video": [N_VIDEO, 0],
                "filename_prefix": "cf_ltx",
                "format": "mp4",
                "codec": "h264",
            },
        },
    }


def _base_parameters() -> tuple[comfyui.ParameterBinding, ...]:
    return (
        comfyui.ParameterBinding(name="prompt", node_id=N_POS, input_name="text", kind="string"),
        comfyui.ParameterBinding(
            name="first_frame", node_id=N_FIRST, input_name="image", kind="image_ref"
        ),
        comfyui.ParameterBinding(
            name="width", node_id=N_I2V, input_name="width", kind="int", minimum=256, maximum=1344
        ),
        comfyui.ParameterBinding(
            name="height", node_id=N_I2V, input_name="height", kind="int", minimum=256, maximum=1344
        ),
        comfyui.ParameterBinding(
            name="length",
            node_id=N_I2V,
            input_name="length",
            kind="int",
            minimum=LTX_FRAME_MIN,
            maximum=LTX_FRAME_MAX,
            description="frame count; LTX wants 8k+1 (73 = 3.0 s at 24 fps)",
        ),
        comfyui.ParameterBinding(
            name="seed", node_id=N_NOISE, input_name="noise_seed", kind="seed"
        ),
    )


def _required_models(
    *, unet_name: str = TRANSFORMER, clip_name: str = TEXT_ENCODER, vae_name: str = VAE
) -> tuple[comfyui.RequiredModel, ...]:
    """What the graph loads, so a doctor check looks for the files this package actually names
    rather than for the module's default pins."""
    return (
        comfyui.RequiredModel(
            filename=unet_name,
            relative_path="models/diffusion_models",
            source_url="https://huggingface.co/elix3r/LTX-2.5-22b-distilled-GGUF",
            license="community quant of Lightricks/LTX-2.5 — see model card",
        ),
        comfyui.RequiredModel(
            filename=clip_name,
            relative_path="models/text_encoders",
            source_url="https://huggingface.co/elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF",
            license="community quant — see model card (Gemma terms apply)",
        ),
        comfyui.RequiredModel(
            filename=vae_name,
            relative_path="models/vae",
            source_url="https://huggingface.co/Lightricks/LTX-2.5",
            license="Lightricks LTX-2.5 terms (gated) — see model card",
        ),
    )


def _custom_nodes() -> tuple[comfyui.PinnedNode, ...]:
    return (
        comfyui.PinnedNode(
            registry_name="ComfyUI-GGUF", version="6ea2651", commit="6ea2651", license="Apache-2.0"
        ),
    )


def ltx_i2v_package(
    *,
    width: int = 512,
    height: int = 896,
    length: int = 73,
    fps: float = 24.0,
    unet_name: str = TRANSFORMER,
    clip_name: str = TEXT_ENCODER,
    vae_name: str = VAE,
) -> comfyui.ComfyWorkflowPackage:
    """First frame + motion prompt -> short silent clip (LTX-2.5 22B distilled GGUF)."""
    return comfyui.ComfyWorkflowPackage(
        package_id="ltx-2.5.i2v",
        version=PACKAGE_VERSION,
        status=comfyui.LifecycleStatus.canary,
        purpose="First frame + motion prompt to a short silent clip via LTX-2.5 image-to-video.",
        comfyui_min_version="0.33.0",
        api_workflow=_base_workflow(
            width=width,
            height=height,
            length=length,
            fps=fps,
            unet_name=unet_name,
            clip_name=clip_name,
            vae_name=vae_name,
        ),
        parameters=_base_parameters(),
        required_models=_required_models(
            unet_name=unet_name, clip_name=clip_name, vae_name=vae_name
        ),
        custom_nodes=_custom_nodes(),
        capabilities=(
            comfyui.CapabilityFlag.image_to_video,
            comfyui.CapabilityFlag.deterministic_seed,
        ),
        max_resolution=(1344, 1344),
        expected_outputs=(N_SAVE,),
    )


def guide_param_names(i: int) -> tuple[str, str, str]:
    """Parameter names for guide ``i`` (1-based): image, frame index, strength."""
    return (f"guide_{i}", f"guide_{i}_frame_idx", f"guide_{i}_strength")


def ltx_i2v_guided_package(
    guides: int,
    *,
    width: int = 512,
    height: int = 896,
    length: int = 73,
    fps: float = 24.0,
    unet_name: str = TRANSFORMER,
    clip_name: str = TEXT_ENCODER,
    vae_name: str = VAE,
) -> comfyui.ComfyWorkflowPackage:
    """The i2v graph plus ``guides`` keyframe guides (1..4). Guide i is a ``LoadImage`` +
    ``LTXVAddGuide`` pair; the chain runs LTXVImgToVideo -> guide_1 -> ... -> guide_n, and the
    last guide's positive/negative/latent feed CFGGuider and SamplerCustomAdvanced."""
    if not 1 <= guides <= 4:
        msg = "guides must be between 1 and 4"
        raise ValueError(msg)
    wf = _base_workflow(
        width=width,
        height=height,
        length=length,
        fps=fps,
        unet_name=unet_name,
        clip_name=clip_name,
        vae_name=vae_name,
    )
    params = list(_base_parameters())
    prev_pos, prev_neg, prev_latent = [N_I2V, 0], [N_I2V, 1], [N_I2V, 2]
    for i in range(1, guides + 1):
        load_id = str(GUIDE_NODE_BASE + 2 * i - 1)
        guide_id = str(GUIDE_NODE_BASE + 2 * i)
        wf[load_id] = {"class_type": "LoadImage", "inputs": {"image": ""}}
        wf[guide_id] = {
            "class_type": "LTXVAddGuide",
            "inputs": {
                "positive": prev_pos,
                "negative": prev_neg,
                "vae": [N_VAE, 0],
                "latent": prev_latent,
                "image": [load_id, 0],
                "frame_idx": length - 1,
                "strength": 1.0,
            },
        }
        img, idx, strength = guide_param_names(i)
        params += [
            comfyui.ParameterBinding(
                name=img, node_id=load_id, input_name="image", kind="image_ref"
            ),
            comfyui.ParameterBinding(
                name=idx,
                node_id=guide_id,
                input_name="frame_idx",
                kind="int",
                minimum=-9999,
                maximum=9999,
                description="frame index the guide image pins (negative counts from the end)",
            ),
            comfyui.ParameterBinding(
                name=strength,
                node_id=guide_id,
                input_name="strength",
                kind="float",
                minimum=0.0,
                maximum=10.0,
            ),
        ]
        prev_pos, prev_neg, prev_latent = [guide_id, 0], [guide_id, 1], [guide_id, 2]
    wf[N_GUIDER]["inputs"]["positive"] = prev_pos
    wf[N_GUIDER]["inputs"]["negative"] = prev_neg
    wf[N_SAMPLER]["inputs"]["latent_image"] = prev_latent
    return comfyui.ComfyWorkflowPackage(
        package_id=f"ltx-2.5.i2v-guided{guides}",
        version=PACKAGE_VERSION,
        status=comfyui.LifecycleStatus.canary,
        purpose=(
            f"First frame + {guides} keyframe guide(s) at chosen frame indices + motion prompt"
            " to a short silent clip via LTX-2.5 (deterministic cinematography from the"
            " scene-control layer)."
        ),
        comfyui_min_version="0.33.0",
        api_workflow=wf,
        parameters=tuple(params),
        required_models=_required_models(
            unet_name=unet_name, clip_name=clip_name, vae_name=vae_name
        ),
        custom_nodes=_custom_nodes(),
        capabilities=(
            comfyui.CapabilityFlag.image_to_video,
            comfyui.CapabilityFlag.keyframe_guide,
            comfyui.CapabilityFlag.deterministic_seed,
        ),
        max_resolution=(1344, 1344),
        expected_outputs=(N_SAVE,),
    )
