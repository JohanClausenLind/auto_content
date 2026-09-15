"""Ideogram 4 (SDNQ 4-bit) local server: text-to-image and Differential Diffusion inpainting."""

from __future__ import annotations

import base64
import io
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

REPO_ROOT = Path(__file__).resolve().parents[3]  # skills/image/ideogram4/server.py -> repo root

MODEL_PATH = Path(
    os.environ.get(
        "CF_IDEOGRAM4_MODEL_PATH", REPO_ROOT / "models" / "image_generation" / "Ideogram-4-SDNQ"
    )
).expanduser()
"""The SDNQ 4-bit repack: Disty0/Ideogram-4-SDNQ-4bit-dynamic-hadamard, pinned in weights.py."""

BLOCKS_PATH = Path(
    os.environ.get(
        "CF_IDEOGRAM4_BLOCKS_PATH",
        REPO_ROOT / "models" / "image_generation" / "Ideogram-4-Blocks" / "custom_blocks",
    )
).expanduser()
"""OzzyGT/ideogram4_custom_blocks, downloaded to the store rather than fetched at import.

``trust_remote_code=True`` executes this directory's Python. Pointing it at a pinned local copy is
the difference between running code reviewed once at a known revision and running whatever the Hub
serves at start-up, which for a ``trust_remote_code`` path is the whole security boundary."""

PORT = int(os.environ.get("CF_IDEOGRAM4_PORT", "8802"))
HOST = os.environ.get("CF_IDEOGRAM4_HOST", "127.0.0.1")
"""Loopback by default. As with HiDream: set it to the *tailnet* address to serve another box,
never 0.0.0.0, which would publish an unauthenticated whole-GPU endpoint to the LAN."""

OFFLOAD = os.environ.get("CF_IDEOGRAM4_OFFLOAD", "1") != "0"
"""Leaf-level group offloading. On by default because it is the only reason this fits a 24 GB
card: the two transformers plus the encoder do not co-reside otherwise."""

LOW_CPU_MEM = os.environ.get("CF_IDEOGRAM4_LOW_CPU_MEM", "1") != "0"
"""Upstream's comment is "set to False if you have ~40GB of free RAM". vegaserv has 31 GB total,
so the default here is True; on nova (76 GB) exporting ``CF_IDEOGRAM4_LOW_CPU_MEM=0`` is faster."""

QUANTIZED_MATMUL = os.environ.get("CF_IDEOGRAM4_QUANTIZED_MATMUL", "0") != "0"
"""Off by default: sdnq 0.2.6's quantized-matmul kernel calls `triton.language.mul`, which the
triton 3.4.0 bundled with torch 2.8 does not have, and it fails mid-denoise (journal 2026-09-15)."""

# Ideogram's own published recipes, the same three media/ideogram_packages.py carries:
# (steps, mu, std). The diffusers block's own defaults are 48 / 0.0 / 1.5, i.e. "quality".
PRESETS: dict[str, tuple[int, float, float]] = {
    "quality": (48, 0.0, 1.5),
    "default": (20, 0.0, 1.75),
    "turbo": (12, 0.5, 1.75),
}
DEFAULT_PRESET = os.environ.get("CF_IDEOGRAM4_PRESET", "default")

MAX_PIXELS = 4096 * 4096

OFFLOADED = ("transformer", "unconditional_transformer")
"""The components that stream on and off the card; everything else stays resident."""

NEEDED = (
    "text_encoder",
    "tokenizer",
    "transformer",
    "unconditional_transformer",
    "vae",
    "scheduler",
)

app = Flask(__name__)
_state: dict = {"pipe": None, "error": None, "lock": threading.Lock()}


def _load() -> None:
    import torch
    from diffusers import ModularPipeline
    from diffusers.hooks import apply_group_offloading

    # Import sdnq BEFORE diffusers loads anything: it registers its quantizer, and without it
    # load_components dies with "Unknown quantization type, got sdnq".
    from sdnq import SDNQConfig  # noqa: F401

    for path, what in ((MODEL_PATH, "weights"), (BLOCKS_PATH, "custom blocks")):
        if not path.exists():
            msg = (
                f"ideogram4 {what} missing at {path}. Install with `uv run content-factory "
                "weights install ideogram-4-sdnq`, which also creates the models/ index link."
            )
            raise FileNotFoundError(msg)

    started = time.monotonic()
    pipe = ModularPipeline.from_pretrained(str(BLOCKS_PATH), trust_remote_code=True)
    pipe.load_components(
        names=list(NEEDED),
        pretrained_model_name_or_path=str(MODEL_PATH),
        torch_dtype=torch.bfloat16,
    )

    missing = [n for n in NEEDED if pipe.components.get(n) is None]
    if missing:
        msg = f"components failed to load: {missing} -- see the traceback above this line"
        raise RuntimeError(msg)

    if QUANTIZED_MATMUL and torch.cuda.is_available():
        try:
            from sdnq.common import use_torch_compile as triton_is_available
            from sdnq.loader import apply_sdnq_options_to_model

            if triton_is_available:
                for name in ("transformer", "unconditional_transformer", "text_encoder"):
                    apply_sdnq_options_to_model(pipe.components[name], use_quantized_matmul=True)
        except ImportError:  # sdnq present but without the triton extras: slower, not broken
            pass

    if OFFLOAD:
        # Only the two transformers stream.
        onload_device = torch.device("cuda")
        for name in OFFLOADED:
            apply_group_offloading(
                pipe.components[name],
                onload_device=onload_device,
                offload_type="leaf_level",
                use_stream=True,
                low_cpu_mem_usage=LOW_CPU_MEM,
            )
        for name in ("text_encoder", "vae"):
            pipe.components[name].to("cuda")
    else:
        pipe.to("cuda")

    _state["pipe"] = pipe
    print(
        f"[ideogram4-server] loaded from {MODEL_PATH} in {time.monotonic() - started:.1f}s "
        f"(offload={OFFLOAD}, low_cpu_mem={LOW_CPU_MEM})",
        flush=True,
    )


def _decode(b64: str) -> object:
    from PIL import Image

    return Image.open(io.BytesIO(base64.b64decode(b64)))


def _recipe(body: dict) -> tuple[int, float, float]:
    """(steps, mu, std) for this request: the preset, with an explicit `steps` overriding it."""
    preset = str(body.get("preset") or DEFAULT_PRESET)
    if preset not in PRESETS:
        msg = f"preset must be one of {sorted(PRESETS)}, got {preset!r}"
        raise ValueError(msg)
    steps, mu, std = PRESETS[preset]
    if body.get("steps"):
        steps = int(body["steps"])
    if steps < 1:
        msg = f"steps must be positive, got {steps}"
        raise ValueError(msg)
    return steps, mu, std


def guidance_schedule(steps: int) -> list[float]:
    """The model's 48-step CFG curve resampled to `steps`, which the block requires to match."""
    from diffusers.modular_pipelines.ideogram4.before_denoise import DEFAULT_GUIDANCE_SCHEDULE

    base = list(DEFAULT_GUIDANCE_SCHEDULE)
    if steps == len(base):
        return base
    return [float(base[min(len(base) - 1, int(i * len(base) / steps))]) for i in range(steps)]


def _size(body: dict) -> tuple[int, int]:
    width = int(body.get("width", 1024))
    height = int(body.get("height", 1024))
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        msg = f"unusable size {width}x{height}"
        raise ValueError(msg)
    # The change map is decided on a //16 token grid; a size off that stride silently truncates.
    if width % 16 or height % 16:
        msg = f"width and height must be multiples of 16, got {width}x{height}"
        raise ValueError(msg)
    return width, height


def _generator(seed: int):
    import torch

    return torch.Generator(device="cuda").manual_seed(int(seed))


def _png(image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@app.get("/healthz")
def healthz():
    return jsonify(
        {
            "status": "ok",
            "model": str(MODEL_PATH),
            "blocks": str(BLOCKS_PATH),
            "loaded": _state["pipe"] is not None,
            "offload": OFFLOAD,
            "low_cpu_mem": LOW_CPU_MEM,
            "preset": DEFAULT_PRESET,
            "presets": PRESETS,
            "capabilities": ["text_to_image", "inpaint"],
            "error": _state["error"],
        }
    )


@app.post("/generate")
def generate():
    """Text to image. The prompt must be a structured JSON caption, never prose."""
    body = request.get_json(force=True)
    allowed = {"prompt", "width", "height", "seed", "steps", "preset"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        return jsonify({"error": f"unknown fields {unknown}"}), 400
    try:
        width, height = _size(body)
        steps, mu, std = _recipe(body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    started = time.monotonic()
    with _state["lock"]:
        image = _state["pipe"](
            prompt=body["prompt"],
            height=height,
            width=width,
            num_inference_steps=steps,
            mu=mu,
            std=std,
            guidance_schedule=guidance_schedule(steps),
            generator=_generator(int(body.get("seed", 7))),
            output="images",
        )[0]
    return jsonify(
        {
            "png_b64": _png(image),
            "elapsed_s": round(time.monotonic() - started, 1),
            "steps": steps,
            "mode": "text_to_image",
        }
    )


@app.post("/inpaint")
def inpaint():
    """Differential Diffusion: repaint where the change map is dark, keep where it is light."""
    body = request.get_json(force=True)
    allowed = {
        "prompt",
        "image_b64",
        "diffdiff_map_b64",
        "width",
        "height",
        "seed",
        "steps",
        "preset",
        "denoising_start",
    }
    unknown = sorted(set(body) - allowed)
    if unknown:
        return jsonify({"error": f"unknown fields {unknown}"}), 400
    for required in ("prompt", "image_b64", "diffdiff_map_b64"):
        if not body.get(required):
            return jsonify({"error": f"{required} is required for inpainting"}), 400
    try:
        width, height = _size(body)
        steps, mu, std = _recipe(body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    image = _decode(body["image_b64"]).convert("RGB")
    change_map = _decode(body["diffdiff_map_b64"]).convert("L")
    if change_map.size != image.size:
        return jsonify({"error": f"map {change_map.size} does not match image {image.size}"}), 400
    if image.size != (width, height):
        image = image.resize((width, height))
        change_map = change_map.resize((width, height))

    extra: dict = {}
    if body.get("denoising_start") is not None:
        extra["denoising_start"] = float(body["denoising_start"])

    started = time.monotonic()
    with _state["lock"]:
        out = _state["pipe"](
            prompt=body["prompt"],
            image=image,
            diffdiff_map=change_map,
            height=height,
            width=width,
            num_inference_steps=steps,
            mu=mu,
            std=std,
            guidance_schedule=guidance_schedule(steps),
            generator=_generator(int(body.get("seed", 7))),
            output="images",
            **extra,
        )[0]
    return jsonify(
        {
            "png_b64": _png(out),
            "elapsed_s": round(time.monotonic() - started, 1),
            "steps": steps,
            "mode": "inpaint",
        }
    )


if __name__ == "__main__":
    try:
        _load()
    except Exception as exc:  # reported through /healthz instead of dying silently in a log
        _state["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[ideogram4-server] load failed: {_state['error']}", flush=True)
        raise
    app.run(host=HOST, port=PORT, threaded=True)
