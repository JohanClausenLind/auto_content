"""ProPainter: inpaint the masked region of every frame (nonzero mask = repaint).
params: {"mask_dir": path (one PNG per frame or a single PNG), "mask_dilation": 4, "fp16": true,
         "timeout_s": 3600}. S-Lab License 1.0 — non-commercial (operator-accepted for local use)."""

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


def run(job: Job, started: float) -> dict:
    mask_dir = Path(job.params.get("mask_dir", ""))
    if not mask_dir.exists():
        raise ToolError(f"propainter: mask_dir missing: {mask_dir}")
    frames = list_frames(job.frames_dir)
    repo = repo_dir("propainter")
    weights = Path(job.params.get("weights_dir", MODELS / "propainter"))
    for name in ("ProPainter.pth", "raft-things.pth", "recurrent_flow_completion.pth"):
        if not (weights / name).exists():
            raise ToolError(f"propainter: weights missing at {weights / name}")
    # upstream loads from <repo>/weights/; link our store there once
    link = repo / "weights"
    if not link.exists():
        link.symlink_to(weights)
    results = job.out_dir / "raw"
    if results.exists():
        shutil.rmtree(results)
    from PIL import Image

    with Image.open(frames[0]) as im:
        w, h = im.size
    cmd = [
        str(interpreter("propainter")),
        "inference_propainter.py",
        "--video",
        str(job.frames_dir),
        "--mask",
        str(mask_dir),
        "--output",
        str(results),
        "--width",
        str(w),
        "--height",
        str(h),
        "--mask_dilation",
        str(int(job.params.get("mask_dilation", 4))),
        "--save_frames",
    ]
    if job.params.get("fp16", True):
        cmd.append("--fp16")
    run_cmd(
        cmd,
        cwd=repo,
        timeout_s=int(job.params.get("timeout_s", 3600)),
        log=job.out_dir / "logs" / "propainter.log",
    )
    produced = sorted(results.rglob("frames/*.png")) or sorted(results.rglob("*.png"))
    produced = [p for p in produced if "mask" not in p.parent.name.lower()]
    if len(produced) != len(frames):
        raise ToolError(f"propainter produced {len(produced)} frames for {len(frames)} inputs")
    out = normalise_frames(produced, job.out_dir)
    return summary(job, out, started, kind="inpainted")
