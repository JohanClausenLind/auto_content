"""HiDream-O1-Image local server: loads the 8B model once, serves loopback-only generation.

Run inside this skill's own environment:
    CF_HIDREAM_REPO=~/git/ai_models/image_generators/HiDream-O1-Image \
    CF_HIDREAM_MODEL_PATH=~/models/HiDream-O1-Image-Dev \
    uv run --project skills/image/hidream python skills/image/hidream/server.py

Endpoints (127.0.0.1:8801):
    GET  /healthz            -> {"status": "ok", "model": ..., "loaded": bool}
    POST /generate           -> {"png_b64": ..., "elapsed_s": ...}
        body: {"prompt": str, "ref_image_b64": str|null, "width": int, "height": int,
               "seed": int, "steps": int|null, "scheduler": "flow_match"|"flash"|null}

Not imported by the control plane; the factory talks to it over loopback HTTP only.
"""

from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

REPO = Path(
    os.environ.get("CF_HIDREAM_REPO", "~/git/ai_models/image_generators/HiDream-O1-Image")
).expanduser()
MODEL_PATH = Path(
    os.environ.get("CF_HIDREAM_MODEL_PATH", "~/models/HiDream-O1-Image-Dev")
).expanduser()
MODEL_TYPE = os.environ.get("CF_HIDREAM_MODEL_TYPE", "dev")  # dev: 28 steps; full: 50
PORT = int(os.environ.get("CF_HIDREAM_PORT", "8801"))

sys.path.insert(0, str(REPO))

app = Flask(__name__)
_state: dict = {"model": None, "processor": None, "lock": threading.Lock()}


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
    print(f"[hidream-server] model loaded from {MODEL_PATH}", flush=True)


@app.get("/healthz")
def healthz():
    return jsonify(
        {"status": "ok", "model": str(MODEL_PATH), "loaded": _state["model"] is not None}
    )


@app.post("/generate")
def generate():
    from models.pipeline import DEFAULT_TIMESTEPS, generate_image

    body = request.get_json(force=True)
    prompt = body["prompt"]
    width = int(body.get("width", 1024))
    height = int(body.get("height", 1024))
    seed = int(body.get("seed", 7))
    default_steps = 28 if MODEL_TYPE == "dev" else 50
    steps = int(body.get("steps") or default_steps)
    scheduler = body.get("scheduler") or "default"

    ref_paths: list[str] = []
    tmp: tempfile.NamedTemporaryFile | None = None
    if body.get("ref_image_b64"):
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(base64.b64decode(body["ref_image_b64"]))
        tmp.flush()
        ref_paths = [tmp.name]

    extra: dict = {}
    if MODEL_TYPE == "dev" and not ref_paths:
        extra["timesteps_list"] = DEFAULT_TIMESTEPS

    started = time.monotonic()
    with _state["lock"]:
        image = generate_image(
            _state["model"],
            _state["processor"],
            prompt,
            ref_image_paths=ref_paths,
            height=height,
            width=width,
            num_inference_steps=steps,
            seed=seed,
            scheduler_name=scheduler,
            **extra,
        )
    elapsed = time.monotonic() - started
    if tmp is not None:
        os.unlink(tmp.name)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return jsonify(
        {"png_b64": base64.b64encode(buf.getvalue()).decode(), "elapsed_s": round(elapsed, 1)}
    )


if __name__ == "__main__":
    _load()
    app.run(host="127.0.0.1", port=PORT, threaded=True)
