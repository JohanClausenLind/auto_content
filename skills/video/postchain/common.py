"""Shared plumbing for the post-chain runners: job files, frame directories, tool environments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]
EXTERNAL = Path(os.environ.get("CF_EXTERNAL_DIR", REPO_ROOT / "external"))
MODELS = Path(os.environ.get("AI_VIDEO_MODELS", "/mnt/fast/models"))

# tool -> (checkout dir under external/, env var overriding the interpreter)
TOOL_REPOS: dict[str, tuple[str, str]] = {
    "cutie": ("Cutie", "CF_CUTIE_PYTHON"),
    "propainter": ("ProPainter", "CF_PROPAINTER_PYTHON"),
    "seedvr2": ("seedvr2_videoupscaler", "CF_SEEDVR2_PYTHON"),
    "gimm_vfi": ("GIMM-VFI", "CF_GIMM_VFI_PYTHON"),
    "rife": ("Practical-RIFE", "CF_RIFE_PYTHON"),
}


class ToolError(RuntimeError):
    pass


@dataclass
class Job:
    tool: str
    frames_dir: Path
    out_dir: Path
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Job:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        tool = doc["tool"]
        if tool not in TOOL_REPOS:
            raise ToolError(f"unknown tool {tool!r}; known: {sorted(TOOL_REPOS)}")
        return cls(tool, Path(doc["frames_dir"]), Path(doc["out_dir"]), dict(doc.get("params", {})))


def repo_dir(tool: str) -> Path:
    return EXTERNAL / TOOL_REPOS[tool][0]


def interpreter(tool: str) -> Path:
    """The upstream checkout's own Python (torch + its pins), overridable per tool."""
    env = os.environ.get(TOOL_REPOS[tool][1])
    if env:
        return Path(env)
    venv = repo_dir(tool) / ".venv" / "bin" / "python"
    if venv.exists():
        return venv
    raise ToolError(
        f"{tool}: no interpreter — create {venv.parent.parent} (see skills/video/postchain/README.md)"
        f" or set {TOOL_REPOS[tool][1]}"
    )


def list_frames(frames_dir: Path) -> list[Path]:
    frames = sorted(
        p for p in frames_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")
    )
    if not frames:
        raise ToolError(f"no frames in {frames_dir}")
    return frames


def normalise_frames(src_files: list[Path], out_dir: Path) -> list[Path]:
    """Copy/rename any tool's output frames into ``out_dir/frames/%04d.png`` (PNG, sorted)."""
    from PIL import Image

    target = out_dir / "frames"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    out: list[Path] = []
    for i, src in enumerate(sorted(src_files)):
        dst = target / f"{i:04d}.png"
        if src.suffix.lower() == ".png":
            shutil.copyfile(src, dst)
        else:
            with Image.open(src) as im:
                im.convert("RGB").save(dst, format="PNG", optimize=False, compress_level=6)
        out.append(dst)
    return out


def frames_digest(frames: list[Path]) -> str:
    h = hashlib.sha256()
    for f in frames:
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def run(
    cmd: list[str], *, cwd: Path, timeout_s: int, log: Path, env: dict[str, str] | None = None
) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout_s, env=full_env
    )
    log.write_text(
        f"$ {' '.join(cmd)}\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise ToolError(f"{cmd[0]} exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}")


def summary(job: Job, frames: list[Path], started: float, **extra: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": job.tool,
        "frames": len(frames),
        "out_dir": str(job.out_dir),
        "frames_dir": str(job.out_dir / "frames"),
        "sha256": frames_digest(frames),
        "elapsed_s": round(time.time() - started, 2),
        **extra,
    }


def emit(doc: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(doc, sort_keys=True) + "\n")
    sys.stdout.flush()
