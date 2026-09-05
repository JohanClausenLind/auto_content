"""GIMM-VFI: quality-tier interpolation. params: {"factor": 2 (N frames between = factor-1 -> N=factor),
"model": "gimmvfi_r_arb", "ds_factor": 1.0, "timeout_s": 3600}. Reads a frame dir, writes frames."""

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
    frames = list_frames(job.frames_dir)
    factor = int(job.params.get("factor", 2))
    if factor not in (2, 4, 8):
        raise ToolError("gimm_vfi: factor must be 2, 4 or 8")
    model = str(job.params.get("model", "gimmvfi_r_arb"))
    ckpt = Path(job.params.get("checkpoint", MODELS / "gimm-vfi" / f"{model}.pt"))
    if not ckpt.exists():
        raise ToolError(f"gimm_vfi: weights missing at {ckpt}")
    repo = repo_dir("gimm_vfi")
    # upstream expects ./pretrained_ckpt/* for the flow estimators
    pretrained = repo / "pretrained_ckpt"
    if not pretrained.exists():
        pretrained.symlink_to(MODELS / "gimm-vfi")
    results = job.out_dir / "raw"
    if results.exists():
        shutil.rmtree(results)
    cmd = [
        str(interpreter("gimm_vfi")),
        "src/video_Nx.py",
        "--source-path",
        str(job.frames_dir),
        "--output-path",
        str(results),
        "--N",
        str(factor),
        "--ds-factor",
        str(float(job.params.get("ds_factor", 1.0))),
        "-m",
        f"configs/gimmvfi/{model}.yaml",
        "-l",
        str(ckpt),
        "--eval",
    ]
    run_cmd(
        cmd,
        cwd=repo,
        timeout_s=int(job.params.get("timeout_s", 3600)),
        log=job.out_dir / "logs" / "gimm_vfi.log",
    )
    produced = (
        sorted((results / "frames").glob("*.png"))
        if (results / "frames").exists()
        else sorted(results.rglob("*.png"))
    )
    expected = (len(frames) - 1) * factor + 1
    if len(produced) < expected - factor:  # the last segment may be dropped by upstream
        raise ToolError(f"gimm_vfi produced {len(produced)} frames, expected about {expected}")
    out = normalise_frames(produced, job.out_dir)
    return summary(job, out, started, kind="interpolated", factor=factor, model=model)
