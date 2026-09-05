"""HiDream-O1-Image local server: loads the 8B model once, serves loopback-only generation.

Run inside this skill's own environment:
    uv run --project skills/image/hidream python skills/image/hidream/server.py
Defaults resolve inside this repo (override with the env vars):
    CF_HIDREAM_REPO        <repo>/external/hidream-o1-code
    CF_HIDREAM_MODEL_PATH  <repo>/models/image_generation/HiDream-O1-Image-Dev
    CF_HIDREAM_MODEL_TYPE  full | dev   Selects the inference *recipe*, and it must match the
                           weights: full is 50 steps at guidance 5, dev is 28 steps at guidance 0
                           with the distilled timestep schedule. The two upstream repos ship
                           different shards, so the recipe cannot be inferred from the directory
                           name — verify with content_factory.models.hidream_identity.

Endpoints (127.0.0.1:8801):
    GET  /healthz            -> {"status": "ok", "model": ..., "model_type": ..., "loaded": bool}
    POST /generate           -> {"png_b64": ..., "elapsed_s": ..., "refs": n, "layout_boxes": n}
        body: {"prompt": str,
               "ref_images_b64": [str, ...]      # ordered reference images: identity refs first
                                                 # (one per layout box), then structural refs
                                                 # (rough render, OpenPose skeleton) — upstream
                                                 # README §4 "IP_skeleton" pattern
               "ref_image_b64": str|null,        # legacy single reference (still accepted)
               "layout_bboxes": [[x, y, w, h], ...] | null,   # relative, our convention; converted
                                                 # to upstream's [x1, x2, y1, y2]; box i colours
                                                 # reference image i; at most 5
               "width": int, "height": int,      # advisory in size, exact in shape: upstream
                                                 # snaps to one of eleven predefined ~4 MP
                                                 # resolutions by minimising the *ratio*
                                                 # difference, so 1024x576 and 2560x1440 both
                                                 # return 2560x1440, 576x1024 returns 1440x2560
                                                 # and 768x768 returns 2048x2048 — 0.000 % aspect
                                                 # error. A vertical ShotSpec does get a vertical
                                                 # anchor; only the pixel count is not the
                                                 # caller's to choose
               "seed": int,
               "steps": int|null,               # overrides the recipe's step count, and on the
                                                 # dev recipe also drops its pinned 28-entry
                                                 # timestep schedule, which would otherwise
                                                 # decide the step count on its own
               "guidance_scale": float|null, "shift": float|null,
               "scheduler": "flow_match"|"flash"|null,   # dev: flow_match is upstream's
                                                 # single-reference editing branch, flash is the
                                                 # branch it uses for everything else
               "noise_scale_start": float|null,  # initial pixel-space latent scale; the pipeline
               "noise_scale_end": float|null,    # defaults to 8.0 and upstream passes 7.5 on the
               "noise_clip_std": float|null}     # flash branch, where s_noise is actually used

Not imported by the control plane; the factory talks to it over loopback HTTP only.
"""

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
PORT = int(os.environ.get("CF_HIDREAM_PORT", "8801"))
HOST = os.environ.get("CF_HIDREAM_HOST", "127.0.0.1")
"""Loopback by default: this server has no auth and hands a whole GPU to whoever asks.

Set it to the host's *tailnet* address to let a second machine's control plane send it frames --
never to 0.0.0.0, which would also publish it to the LAN. The factory reaching it from another box
is the whole point of ``image_sequences.hidream_endpoints``."""
MAX_LAYOUT_BOXES = 5  # upstream models/utils.py MAX_BOX

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
    print(f"[hidream-server] {MODEL_TYPE} model loaded from {MODEL_PATH}", flush=True)


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
        # exactly one reference runs flow_match on the distilled schedule with no noise arguments,
        # so the pipeline's NOISE_SCALE default stands, and everything else runs `flash` with
        # noise_scale_start, noise_scale_end and noise_clip_std passed explicitly. That second
        # half is reachable now (it never was — this sent everything but one reference to
        # `default`, which upstream never selects for dev) and it matters because flash is the one
        # scheduler that actually consumes s_noise; diffusers' FlowMatchEulerDiscreteScheduler
        # ignores the argument entirely.
        #
        # Upstream's rule is the right one, and it took a misread to establish that. Measured on
        # one staged shot, same seed, skeleton + depth as the references: flow_match keeps the
        # staged stride but carries the speckle at 0.175 of pixels above a luma gradient of 60,
        # while flash comes back at 0.0004 — a clean photographic frame, correct side-on stride,
        # coherent track. Its 0.990 midtone fraction reads as a failure against a threshold built
        # to catch *missing* midtones in crushed illustration, and is simply what a flat overcast
        # scene on grey concrete looks like when it is well exposed. With one reference flash does
        # fail, fusing two bodies and abandoning the brief — which is exactly the `is_editing`
        # split, so the count is what decides and upstream decides it correctly.
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
        # The dev recipe pins an explicit 28-entry timestep schedule, and upstream's
        # build_scheduler *overwrites* sched.timesteps with it after set_timesteps, so the loop
        # takes its length from the list and num_inference_steps is discarded. Measured: 28 and 50
        # steps at the same seed returned a byte-identical PNG (sha256 497564af99cb), so a caller
        # asking for more steps silently got the distilled schedule. An explicit step count means
        # "a schedule of this length", so it drops the pinned list rather than being ignored.
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
        }
    )


if __name__ == "__main__":
    _load()
    app.run(host=HOST, port=PORT, threaded=True)
