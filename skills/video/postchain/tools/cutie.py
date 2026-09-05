"""Cutie: propagate the Blender segmentation seed (indexed PNG, 0 = background) through a clip.
params: {"seed_mask": path, "max_internal_size": 480, "mem_every": 5, "timeout_s": 1800}"""

from __future__ import annotations

from pathlib import Path

from common import (
    MODELS,
    SKILL_DIR,
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
    seed = Path(job.params.get("seed_mask", ""))
    if not seed.exists():
        raise ToolError(f"cutie: seed_mask missing: {seed}")
    weights = Path(job.params.get("weights", MODELS / "cutie" / "cutie-base-mega.pth"))
    if not weights.exists():
        raise ToolError(f"cutie: weights missing at {weights}")
    frames = list_frames(job.frames_dir)
    raw = job.out_dir / "raw_masks"
    py = interpreter("cutie")
    cmd = [
        str(py),
        str(SKILL_DIR / "tools" / "cutie_driver.py"),
        str(job.frames_dir),
        str(seed),
        str(raw),
        "--weights",
        str(weights),
        "--max-internal-size",
        str(int(job.params.get("max_internal_size", 480))),
        "--mem-every",
        str(int(job.params.get("mem_every", 5))),
    ]
    run_cmd(
        cmd,
        cwd=repo_dir("cutie"),
        timeout_s=int(job.params.get("timeout_s", 1800)),
        log=job.out_dir / "logs" / "cutie.log",
    )
    masks = sorted(raw.glob("*.png"))
    if len(masks) != len(frames):
        raise ToolError(f"cutie produced {len(masks)} masks for {len(frames)} frames")
    out = normalise_frames(masks, job.out_dir)
    return summary(job, out, started, kind="masks", seed_mask=str(seed))
