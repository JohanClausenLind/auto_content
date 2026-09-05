"""Subprocess seam for the post-chain skill plus the two ffmpeg ends of the chain."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from content_factory.audio.mix import ffmpeg
from content_factory.skills.subprocess_json import last_json_line
from content_factory.video.render import REPO_ROOT

POSTCHAIN_VERSION = "0.1.0"
SKILL_DIR = Path("skills") / "video" / "postchain"
SUBPROCESS_RUN: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run


class PostChainError(RuntimeError):
    pass


def run_tool(
    tool: str, frames_dir: Path, out_dir: Path, params: dict[str, Any], *, timeout_s: int = 3600
) -> dict[str, Any]:
    """Run one tool through the skill; returns its JSON summary (frames under out_dir/frames)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    job = out_dir / "job.json"
    job.write_text(
        json.dumps(
            {
                "tool": tool,
                "frames_dir": str(frames_dir),
                "out_dir": str(out_dir),
                "params": params,
            },
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    cmd = ["uv", "run", "--project", str(SKILL_DIR), "python", str(SKILL_DIR / "run.py"), str(job)]
    try:
        proc = SUBPROCESS_RUN(
            cmd, capture_output=True, text=True, check=False, timeout=timeout_s + 120, cwd=REPO_ROOT
        )
    except subprocess.TimeoutExpired as exc:
        raise PostChainError(f"{tool} timed out after {timeout_s}s") from exc
    summary = last_json_line(proc.stdout or "")
    if proc.returncode != 0 or not summary.get("ok"):
        detail = summary.get("error") or (proc.stderr or proc.stdout or "")[-800:]
        raise PostChainError(f"{tool} failed (exit {proc.returncode}): {detail}")
    return summary


def list_frames(frames_dir: Path) -> list[Path]:
    return sorted(p for p in frames_dir.glob("*.png"))


def frames_digest(frames_dir: Path) -> str:
    h = hashlib.sha256()
    for f in list_frames(frames_dir):
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def explode_video(mp4: Path, frames_dir: Path) -> list[Path]:
    """Lossless-ish frame dump: ``frames/%04d.png`` starting at 0."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("*.png"):
        old.unlink()
    ffmpeg(["-i", str(mp4), "-start_number", "0", str(frames_dir / "%04d.png")], timeout=600)
    frames = list_frames(frames_dir)
    if not frames:
        raise PostChainError(f"no frames extracted from {mp4}")
    return frames


def mux_frames(frames_dir: Path, mp4: Path, *, fps: float) -> Path:
    mp4.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-framerate",
            str(fps),
            "-start_number",
            "0",
            "-i",
            str(frames_dir / "%04d.png"),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            str(mp4),
        ],
        timeout=1200,
    )
    return mp4
