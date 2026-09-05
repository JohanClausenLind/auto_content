"""Practical-RIFE 4.25: fast interpolation. params: {"factor": 2|4|8, "scale": 1.0, "fp16": false,
"timeout_s": 1800}. Weights live in the checkout (train_log/flownet.pkl, manual fetch)."""

from __future__ import annotations

import shutil

from common import (
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
    frames = list_frames(job.frames_dir)
    factor = int(job.params.get("factor", 2))
    if factor not in (2, 4, 8):
        raise ToolError("rife: factor must be 2, 4 or 8")
    repo = repo_dir("rife")
    if not (repo / "train_log" / "flownet.pkl").exists():
        raise ToolError(
            f"rife: weights missing at {repo / 'train_log' / 'flownet.pkl'} (manual Google Drive step)"
        )
    results = job.out_dir / "raw"
    if results.exists():
        shutil.rmtree(results)
    results.mkdir(parents=True)
    exp = {2: 1, 4: 2, 8: 3}[factor]
    # --png writes to ./vid_out relative to the working directory (the --output name is ignored),
    # so run inside our results dir and point --model at the checkout's train_log absolutely.
    cmd = [
        str(interpreter("rife")),
        str(repo / "inference_video.py"),
        "--img",
        str(job.frames_dir),
        "--exp",
        str(exp),
        "--png",
        "--model",
        str(repo / "train_log"),
        "--scale",
        str(float(job.params.get("scale", 1.0))),
    ]
    if job.params.get("fp16", False):
        cmd.append("--fp16")
    env_note = {"PYTHONPATH": str(repo)}
    run_cmd(
        cmd,
        cwd=results,
        timeout_s=int(job.params.get("timeout_s", 1800)),
        log=job.out_dir / "logs" / "rife.log",
        env=env_note,
    )
    produced = sorted((results / "vid_out").glob("*.png")) or sorted(results.rglob("*.png"))
    if len(produced) < len(frames):
        raise ToolError(f"rife produced {len(produced)} frames for {len(frames)} inputs")
    out = normalise_frames(produced, job.out_dir)
    return summary(job, out, started, kind="interpolated", factor=factor)
