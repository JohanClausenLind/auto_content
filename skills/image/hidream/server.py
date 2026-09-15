"""HiDream-O1-Image local server: loads the 8B model once, serves loopback-only generation."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

REPO_ROOT = Path(__file__).resolve().parents[3]  # skills/image/hidream/server.py -> repo root
REPO = Path(
    os.environ.get("CF_HIDREAM_REPO", REPO_ROOT / "external" / "hidream-o1-code")
).expanduser()
MODEL_PATH = Path(
    os.environ.get(
        "CF_HIDREAM_MODEL_PATH", REPO_ROOT / "models" / "image_generation" / "HiDream-O1-Image-Dev"
    )
).expanduser()
MODEL_TYPE = os.environ.get(
    "CF_HIDREAM_MODEL_TYPE", "full"
)  # full: 50 steps, cfg 5; dev: 28, cfg 0
LORA_PATH = os.environ.get("CF_HIDREAM_LORA", "").strip()
"""A musubi-tuner HiDream-O1 adapter to merge at startup, or "" for the base model.

Merged once into the loaded weights rather than attached per request, so generation costs nothing
extra and every frame of a sequence sees the same model. It cannot be unloaded — a different
adapter is a server restart, which `ensure()` already performs when it switches GPU tenants.

The adapter's own metadata says which base it was trained against and `_load` refuses a mismatch,
because a LoRA merged onto the wrong weights produces plausible nonsense rather than an error."""
LORA_MULTIPLIER = float(os.environ.get("CF_HIDREAM_LORA_MULTIPLIER", "1.0"))
PORT = int(os.environ.get("CF_HIDREAM_PORT", "8801"))
HOST = os.environ.get("CF_HIDREAM_HOST", "127.0.0.1")
"""Loopback by default: this server has no auth and hands a whole GPU to whoever asks.

Set it to the host's *tailnet* address to let a second machine's control plane send it frames --
never to 0.0.0.0, which would also publish it to the LAN. The factory reaching it from another box
is the whole point of ``image_sequences.hidream_endpoints``."""
MAX_LAYOUT_BOXES = 5  # upstream models/utils.py MAX_BOX

sys.path.insert(0, str(REPO))

app = Flask(__name__)
_state: dict = {"model": None, "processor": None, "lora": None, "lock": threading.Lock()}


def _load() -> None:
    import torch
    from models.qwen3_vl_transformers import Qwen3VLForConditionalGeneration
    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(str(MODEL_PATH))
    tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
    tokenizer.boi_token = "<|boi_token|>"
    tokenizer.bor_token = "<|bor_token|>"
    tokenizer.eor_token = "<|eor_token|>"
    tokenizer.bot_token = "<|bot_token|>"
    tokenizer.tms_token = "<|tms_token|>"
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        str(MODEL_PATH), torch_dtype=torch.bfloat16, device_map="cuda"
    ).eval()
    _state["model"] = model
    _state["processor"] = processor
    print(f"[hidream-server] {MODEL_TYPE} model loaded from {MODEL_PATH}", flush=True)
    if LORA_PATH:
        _state["lora"] = _merge_lora(model)
        print(f"[hidream-server] lora {_state['lora']}", flush=True)


def _merge_lora(model) -> dict:
    """Fold CF_HIDREAM_LORA into the loaded weights, refusing an adapter for a different base."""
    from lora import merge, read_metadata

    path = Path(LORA_PATH).expanduser()
    if not path.exists():
        msg = f"CF_HIDREAM_LORA={path} does not exist"
        raise SystemExit(msg)
    meta = read_metadata(path)
    trained_for = meta.get("ss_base_model_version", "")
    if (
        trained_for
        and trained_for != f"hidream_o1_{'image' if MODEL_TYPE == 'full' else MODEL_TYPE}"
    ):
        msg = (
            f"{path.name} was trained against {trained_for!r} but this server is serving"
            f" MODEL_TYPE={MODEL_TYPE!r} from {MODEL_PATH}. Merging it would produce plausible"
            " nonsense rather than an error. Set CF_HIDREAM_MODEL_TYPE/CF_HIDREAM_MODEL_PATH to the"
            " weights it was trained on, or point CF_HIDREAM_LORA at an adapter for these."
        )
        raise SystemExit(msg)
    report = merge(model, path, LORA_MULTIPLIER)
    report["trained_for"] = trained_for
    report["network_module"] = meta.get("ss_network_module", "")
    return report


def xywh_to_xxyy(boxes: list) -> list[list[float]]:
    """Our relative (x, y, w, h) -> upstream's relative [x1, x2, y1, y2], clamped to [0, 1]."""
    out: list[list[float]] = []
    for b in boxes[:MAX_LAYOUT_BOXES]:
        x, y, w, h = (float(v) for v in b)
        x1, y1 = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
        x2, y2 = max(0.0, min(1.0, x + w)), max(0.0, min(1.0, y + h))
        if x2 > x1 and y2 > y1:
            out.append([round(x1, 4), round(x2, 4), round(y1, 4), round(y2, 4)])
    return out


UPSTREAM_FLASH_NOISE_SCALE = 7.5
"""``inference.py``'s argparse default for ``--noise_scale_start`` and ``--noise_scale_end``, which
it passes only on the flash branch. The pipeline's own ``NOISE_SCALE`` is 8.0; both are the scale of
an initial *pixel-space* latent, since this model diffuses pixels rather than a VAE code."""


def generation_settings(model_type: str, n_refs: int, body: dict) -> dict:
    """Upstream inference.py defaults per model type, with per-request overrides."""
    if model_type == "full":
        settings = {
            "num_inference_steps": 50,
            "guidance_scale": 5.0,
            "shift": 1.0 if n_refs >= 2 else 3.0,  # multi-reference (subject) mode uses shift 1
            "scheduler_name": "default",
            "timesteps_list": None,
        }
    else:
        from models.pipeline import DEFAULT_TIMESTEPS

        # Upstream inference.py splits the dev recipe on `is_editing = len(ref_images) == 1`:
        # exactly one reference runs flow_match on the distilled schedule with no noise arguments.
        scheduler = body.get("scheduler") or ("flow_match" if n_refs == 1 else "flash")
        settings = {
            "num_inference_steps": 28,
            "guidance_scale": 0.0,
            "shift": 1.0,
            "scheduler_name": scheduler,
            "timesteps_list": DEFAULT_TIMESTEPS,
        }
        if scheduler == "flash":
            settings |= {
                "noise_scale_start": UPSTREAM_FLASH_NOISE_SCALE,
                "noise_scale_end": UPSTREAM_FLASH_NOISE_SCALE,
                "noise_clip_std": 0.0,
            }
    if body.get("steps"):
        settings["num_inference_steps"] = int(body["steps"])
        # The dev recipe pins an explicit 28-entry timestep schedule, and upstream's build_scheduler
        # *overwrites* sched.timesteps with it after set_timesteps.
        settings["timesteps_list"] = None
    if body.get("guidance_scale") is not None:
        settings["guidance_scale"] = float(body["guidance_scale"])
    if body.get("shift") is not None:
        settings["shift"] = float(body["shift"])
    for key in ("noise_scale_start", "noise_scale_end", "noise_clip_std"):
        if body.get(key) is not None:
            settings[key] = float(body[key])
    return settings


@app.get("/healthz")
def healthz():
    return jsonify(
        {
            "status": "ok",
            "model": str(MODEL_PATH),
            "model_type": MODEL_TYPE,
            "loaded": _state["model"] is not None,
            # Which adapter, if any, is baked into the weights being served.
            "lora": _state["lora"],
        }
    )


@app.post("/generate")
def generate():
    from models.pipeline import generate_image

    body = request.get_json(force=True)
    allowed = {
        "prompt",
        "ref_images_b64",
        "ref_image_b64",
        "layout_bboxes",
        "width",
        "height",
        "seed",
        "steps",
        "guidance_scale",
        "shift",
        "scheduler",
        "noise_scale_start",
        "noise_scale_end",
        "noise_clip_std",
    }
    unknown = sorted(set(body) - allowed)
    if unknown:
        return jsonify({"error": f"unknown fields {unknown}"}), 400
    prompt = body["prompt"]
    width = int(body.get("width", 1024))
    height = int(body.get("height", 1024))
    seed = int(body.get("seed", 7))
    refs_b64: list[str] = list(body.get("ref_images_b64") or [])
    if body.get("ref_image_b64"):
        refs_b64.insert(0, body["ref_image_b64"])
    boxes = xywh_to_xxyy(body.get("layout_bboxes") or [])
    if boxes and len(boxes) > len(refs_b64):
        return jsonify({"error": "more layout boxes than reference images"}), 400
    settings = generation_settings(MODEL_TYPE, len(refs_b64), body)

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hidream-refs-") as tmp:
        ref_paths: list[str] = []
        for i, b64 in enumerate(refs_b64):
            path = Path(tmp) / f"ref_{i:02d}.png"
            path.write_bytes(base64.b64decode(b64))
            ref_paths.append(str(path))
        extra: dict = {}
        if settings["timesteps_list"] is not None:
            extra["timesteps_list"] = settings["timesteps_list"]
        for key in ("noise_scale_start", "noise_scale_end", "noise_clip_std"):
            if settings.get(key) is not None:
                extra[key] = settings[key]
        if boxes:
            extra["layout_bboxes"] = json.dumps(boxes)
        with _state["lock"]:
            image = generate_image(
                _state["model"],
                _state["processor"],
                prompt,
                ref_image_paths=ref_paths,
                height=height,
                width=width,
                num_inference_steps=settings["num_inference_steps"],
                guidance_scale=settings["guidance_scale"],
                shift=settings["shift"],
                seed=seed,
                scheduler_name=settings["scheduler_name"],
                **extra,
            )
    elapsed = time.monotonic() - started
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return jsonify(
        {
            "png_b64": base64.b64encode(buf.getvalue()).decode(),
            "elapsed_s": round(elapsed, 1),
            "refs": len(ref_paths),
            "layout_boxes": len(boxes),
            "model_type": MODEL_TYPE,
            "lora": (_state["lora"] or {}).get("adapter"),
        }
    )


if __name__ == "__main__":
    _load()
    app.run(host=HOST, port=PORT, threaded=True)
