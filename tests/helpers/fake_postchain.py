"""Stand-in for ``uv run … skills/video/postchain/run.py``: reads the job file and writes plausible
frames so the chain stages can be tested without any of the five tools."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def _frames(frames_dir: Path) -> list[Path]:
    return sorted(frames_dir.glob("*.png"))


def _write(job: dict[str, Any]) -> dict[str, Any]:
    tool = job["tool"]
    src = _frames(Path(job["frames_dir"]))
    out = Path(job["out_dir"]) / "frames"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    params = job.get("params", {})
    produced: list[Path] = []
    if tool == "cutie":
        seed = Image.open(params["seed_mask"])
        for i, _ in enumerate(src):
            p = out / f"{i:04d}.png"
            seed.save(p)
            produced.append(p)
    elif tool in ("rife", "gimm_vfi"):
        factor = int(params.get("factor", 2))
        idx = 0
        for a, b in zip(src, [*src[1:], src[-1]], strict=True):
            ia = np.asarray(Image.open(a).convert("RGB")).astype(np.float32)
            ib = np.asarray(Image.open(b).convert("RGB")).astype(np.float32)
            for k in range(factor):
                t = k / factor
                blend = ((1 - t) * ia + t * ib).astype(np.uint8)
                p = out / f"{idx:04d}.png"
                Image.fromarray(blend).save(p)
                produced.append(p)
                idx += 1
    else:  # propainter / seedvr2: same count, deterministic pixel change (upscale doubles size)
        for i, f in enumerate(src):
            im = Image.open(f).convert("RGB")
            if tool == "seedvr2":
                im = im.resize((im.width * 2, im.height * 2))
            arr = np.asarray(im).copy()
            arr[..., 1] = np.minimum(255, arr[..., 1].astype(int) + 10).astype(np.uint8)
            p = out / f"{i:04d}.png"
            Image.fromarray(arr).save(p)
            produced.append(p)
    digest = hashlib.sha256(
        b"".join(hashlib.sha256(p.read_bytes()).digest() for p in produced)
    ).hexdigest()
    return {
        "ok": True,
        "tool": tool,
        "frames": len(produced),
        "out_dir": job["out_dir"],
        "frames_dir": str(out),
        "sha256": digest,
        "elapsed_s": 0.01,
    }


def fake_postchain_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
    job = json.loads(Path(cmd[-1]).read_text())
    summary = _write(job)
    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(summary) + "\n", stderr="")


def failing_postchain_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        cmd,
        3,
        stdout=json.dumps({"ok": False, "exit": 3, "error": "simulated tool failure"}) + "\n",
        stderr="",
    )
