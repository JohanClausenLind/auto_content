"""SeedVR2: restore + upscale a frame directory; PNG out so interpolators can follow.

params: {"resolution": 1080, "batch_size": 5 (must be 4n+1), "dit_model": file, "model_dir": dir,
         "color_correction": "lab", "timeout_s": 7200}

**The `--model_dir` argument is not enough on its own**, and this tool had never run because of it.
`inference_cli.py` computes `--dit_model`'s argparse *choices* from its own registry plus whatever
it discovers under `<checkout>/models/SEEDVR2/` — before argparse parses anything, so a name that
is not in that set is rejected however `--model_dir` points. The old default,
`seedvr2_7b_int8_convrot.safetensors`, is the filename this repo's downloader saved and is in
neither place, so every invocation died with `invalid choice` (verified 2026-09-09).

Worse, the two are genuinely different weights and not one file under two names: the on-disk 3B
hashes to `98669fd2…` where the CLI's registry pins `seedvr2_ema_3b_fp16.safetensors` at
`2fd0e03a…`. So the answer is not to rename anything, it is to let the CLI *discover* what is
actually here — which it does, and then loads and runs it.

`_stage_model` therefore symlinks the requested DiT and the VAE into the checkout's discovery
directory before the call. Symlinks, not copies: these are 6-8 GB files.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from common import (
    MODELS,
    Job,
    ToolError,
    interpreter,
    list_frames,
    normalise_frames,
    repo_dir,
    summary,
)
from common import (
    run as run_cmd,
)

DEFAULT_DIT = "seedvr2_3b_fp16.safetensors"
"""The 3B fp16 build this repo actually downloads. Measured on a 3090, 2026-09-09: 9 frames of
704x384 to 1080 in **110 s** (~12 s a frame), edge energy 10.19 against bicubic's 4.85 at the same
size, and a structural similarity of 0.9991 to the bicubic — it reconstructs detail without moving
the composition, which is what an upscaler in this pipeline has to do (the composition was already
approved upstream)."""

DEFAULT_VAE = "seedvr2_ema_vae_fp16.safetensors"

DISCOVERY_DIR = "models/SEEDVR2"
"""Where `inference_cli.py` looks when ComfyUI is not importable — relative to the checkout, which
is the cwd this tool runs the CLI in. It is also where the argparse choices come from."""


def _stage_model(dit: str, model_dir: str) -> None:
    """Symlink the weights into the checkout's discovery directory.

    Required, not an optimisation: the CLI's `--dit_model` choices are computed from this directory
    (plus its own registry) before argparse runs, so a build that is not visible here cannot be
    named however `--model_dir` points. Symlinks because these are 6-8 GB files, and idempotent so
    a rerun costs nothing.
    """
    target = repo_dir("seedvr2") / DISCOVERY_DIR
    target.mkdir(parents=True, exist_ok=True)
    source_dir = Path(model_dir)
    for name in (dit, DEFAULT_VAE):
        found = next(
            (c for c in (source_dir / name, source_dir.parent / "vae" / name) if c.is_file()), None
        )
        if found is None:
            continue
        link = target / name
        if link.is_symlink() or link.exists():
            continue
        link.symlink_to(found.resolve())


def run(job: Job, started: float) -> dict:
    frames = list_frames(job.frames_dir)
    model_dir = str(job.params.get("model_dir", MODELS / "seedvr2-3b" / "diffusion_models"))
    dit = str(job.params.get("dit_model", DEFAULT_DIT))
    _stage_model(dit, model_dir)
    batch = int(job.params.get("batch_size", 5))
    if (batch - 1) % 4:
        raise ToolError("seedvr2: batch_size must be 4n+1")
    results = job.out_dir / "raw"
    if results.exists():
        shutil.rmtree(results)
    results.mkdir(parents=True)
    cmd = [
        str(interpreter("seedvr2")),
        "inference_cli.py",
        str(job.frames_dir),
        "--output",
        str(results),
        "--output_format",
        "png",
        "--model_dir",
        str(repo_dir("seedvr2") / DISCOVERY_DIR),
        "--dit_model",
        dit,
        "--resolution",
        str(int(job.params.get("resolution", 1080))),
        "--batch_size",
        str(batch),
        "--color_correction",
        str(job.params.get("color_correction", "lab")),
    ]
    for flag in ("vae_encode_tiled", "vae_decode_tiled"):
        if job.params.get(flag, True):
            cmd.append(f"--{flag}")
    run_cmd(
        cmd,
        cwd=repo_dir("seedvr2"),
        timeout_s=int(job.params.get("timeout_s", 7200)),
        log=job.out_dir / "logs" / "seedvr2.log",
    )
    produced = sorted(results.rglob("*.png"))
    if len(produced) != len(frames):
        raise ToolError(f"seedvr2 produced {len(produced)} frames for {len(frames)} inputs")
    out = normalise_frames(produced, job.out_dir)
    return summary(
        job, out, started, kind="upscaled", resolution=int(job.params.get("resolution", 1080))
    )
