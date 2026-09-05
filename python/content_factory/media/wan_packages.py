"""Wan-Animate-2 pose-driven package (ComfyUI API graph): the HiDream anchor is the reference
character, the Blender OpenPose skeleton video drives the motion. Node inputs verified in ComfyUI
0.33.0 ``comfy_extras/nodes_wan.py`` (WanAnimate2ToVideo) and ``nodes_video.py`` (LoadVideo,
GetVideoComponents). Two weights are not on this host yet and are declared as RequiredModels:
``clip_vision_h.safetensors`` and ``wan_2.1_vae.safetensors`` (Comfy-Org repackaged, Apache-2.0).
"""

from __future__ import annotations

from content_factory.schemas import comfyui

PACKAGE_VERSION = "0.1.0"
WAN_LENGTH_MIN, WAN_LENGTH_MAX = 5, 257

TRANSFORMER = "wan_animate_2-Q5_K_M.gguf"
TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE = "wan_2.1_vae.safetensors"
CLIP_VISION = "clip_vision_h.safetensors"


def snap_length_4k1(frames: int) -> int:
    """Wan generates 4k+1 frames."""
    k = max(1, round((frames - 1) / 4))
    return min(max(4 * k + 1, WAN_LENGTH_MIN), WAN_LENGTH_MAX)


def wan_animate2_pose_package(
    *, width: int = 832, height: int = 480, length: int = 81, fps: float = 16.0
) -> comfyui.ComfyWorkflowPackage:
    wf = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": TRANSFORMER}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "wan"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["2", 0],
                "text": "blurry, distorted, static, watermark, text, extra limbs",
            },
        },
        "6": {"class_type": "LoadImage", "inputs": {"image": ""}},
        "7": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": CLIP_VISION}},
        "8": {
            "class_type": "CLIPVisionEncode",
            "inputs": {"clip_vision": ["7", 0], "image": ["6", 0], "crop": "none"},
        },
        "9": {"class_type": "LoadVideo", "inputs": {"file": ""}},
        "10": {"class_type": "GetVideoComponents", "inputs": {"video": ["9", 0]}},
        "11": {
            "class_type": "WanAnimate2ToVideo",
            "inputs": {
                "positive": ["4", 0],
                "negative": ["5", 0],
                "vae": ["3", 0],
                "width": width,
                "height": height,
                "length": length,
                "batch_size": 1,
                "reference_image": ["6", 0],
                "pose_video": ["10", 0],
                "clip_vision_output": ["8", 0],
                "video_frame_offset": 0,
                "pose_strength": 1.0,
                "pose_start_percent": 0.0,
                "pose_end_percent": 1.0,
                "reference_image_strength": 1.0,
            },
        },
        "12": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": 0,
                "steps": 20,
                "cfg": 4.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["11", 0],
                "negative": ["11", 1],
                "latent_image": ["11", 2],
                "denoise": 1.0,
            },
        },
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": float(fps)}},
        "15": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["14", 0],
                "filename_prefix": "cf_wan_animate",
                "format": "mp4",
                "codec": "h264",
            },
        },
    }
    params = (
        comfyui.ParameterBinding(name="prompt", node_id="4", input_name="text", kind="string"),
        comfyui.ParameterBinding(
            name="first_frame",
            node_id="6",
            input_name="image",
            kind="image_ref",
            description="the reference character (HiDream anchor)",
        ),
        comfyui.ParameterBinding(
            name="pose_video",
            node_id="9",
            input_name="file",
            kind="image_ref",
            description="uploaded mp4 of the OpenPose skeleton track",
        ),
        comfyui.ParameterBinding(
            name="width", node_id="11", input_name="width", kind="int", minimum=256, maximum=1280
        ),
        comfyui.ParameterBinding(
            name="height", node_id="11", input_name="height", kind="int", minimum=256, maximum=1280
        ),
        comfyui.ParameterBinding(
            name="length",
            node_id="11",
            input_name="length",
            kind="int",
            minimum=WAN_LENGTH_MIN,
            maximum=WAN_LENGTH_MAX,
            description="4k+1 frames",
        ),
        comfyui.ParameterBinding(
            name="pose_strength",
            node_id="11",
            input_name="pose_strength",
            kind="float",
            minimum=0.0,
            maximum=10.0,
        ),
        comfyui.ParameterBinding(name="seed", node_id="12", input_name="seed", kind="seed"),
    )
    return comfyui.ComfyWorkflowPackage(
        package_id="wan-animate-2.pose",
        version=PACKAGE_VERSION,
        status=comfyui.LifecycleStatus.draft,
        purpose=(
            "Animate the anchor character with the Blender OpenPose skeleton video"
            " (Wan-Animate-2 14B GGUF)."
        ),
        comfyui_min_version="0.33.0",
        api_workflow=wf,
        parameters=params,
        required_models=(
            comfyui.RequiredModel(
                filename=TRANSFORMER,
                relative_path="models/diffusion_models",
                source_url="https://huggingface.co/karcsiha/wan_animate_2_gguf",
                license="Apache-2.0 (Wan) — community quant, see model card",
            ),
            comfyui.RequiredModel(
                filename=TEXT_ENCODER,
                relative_path="models/text_encoders",
                source_url="https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                license="Apache-2.0",
            ),
            comfyui.RequiredModel(
                filename=VAE,
                relative_path="models/vae",
                source_url="https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors",
                license="Apache-2.0",
            ),
            comfyui.RequiredModel(
                filename=CLIP_VISION,
                relative_path="models/clip_vision",
                source_url="https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors",
                license="Apache-2.0",
            ),
        ),
        custom_nodes=(
            comfyui.PinnedNode(
                registry_name="ComfyUI-GGUF",
                version="6ea2651",
                commit="6ea2651",
                license="Apache-2.0",
            ),
        ),
        capabilities=(
            comfyui.CapabilityFlag.image_to_video,
            comfyui.CapabilityFlag.pose_video,
            comfyui.CapabilityFlag.deterministic_seed,
        ),
        max_resolution=(1280, 1280),
        expected_outputs=("15",),
    )
