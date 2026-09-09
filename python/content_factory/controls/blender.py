"""Runs the headless Blender skill (skills/video/blender_scene) as a subprocess, the way the
manim skill is run: ``uv run --project <skill> python <skill>/render.py <spec> <out>``."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from content_factory.skills.subprocess_json import last_json_line
from content_factory.video.render import REPO_ROOT

BUNDLE_BUILDER_VERSION = "0.1.0"
SKILL_DIR = Path("skills") / "video" / "blender_scene"

# Module-level seam so tests can substitute a fake Blender without touching subprocess globally.
SUBPROCESS_RUN: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run


@dataclass(frozen=True)
class BlenderRun:
    returncode: int
    summary: dict[str, Any] = field(default_factory=dict)
    stdout_tail: str = ""
    stderr_tail: str = ""


def skill_command(
    spec_path: Path,
    out_dir: Path,
    *,
    blender_bin: str,
    engine: str,
    assets_root: Path,
    timeout_s: int,
) -> list[str]:
    return [
        "uv",
        "run",
        "--project",
        str(SKILL_DIR),
        "python",
        str(SKILL_DIR / "render.py"),
        str(spec_path),
        str(out_dir),
        "--blender",
        blender_bin,
        "--engine",
        engine,
        "--assets",
        str(assets_root),
        "--timeout",
        str(timeout_s),
    ]


def run_blender_scene(
    spec_path: Path,
    out_dir: Path,
    *,
    blender_bin: str = "blender",
    engine: str = "workbench",
    assets_root: Path = Path("/mnt/fast/models/blender-assets"),
    timeout_s: int = 1800,
) -> BlenderRun:
    """Render one ShotSpec's passes into ``out_dir``. Raises ``RuntimeError`` with the skill's own
    error line when it fails; the skill's exit code is in the message."""
    cmd = skill_command(
        spec_path,
        out_dir,
        blender_bin=blender_bin,
        engine=engine,
        assets_root=assets_root,
        timeout_s=timeout_s,
    )
    try:
        proc = SUBPROCESS_RUN(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s + 120,
            cwd=REPO_ROOT,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"blender scene skill timed out after {timeout_s}s for {spec_path.name}"
        raise RuntimeError(msg) from exc
    summary = last_json_line(proc.stdout or "")
    run = BlenderRun(
        proc.returncode, summary, (proc.stdout or "")[-800:], (proc.stderr or "")[-800:]
    )
    if proc.returncode != 0 or not summary.get("ok"):
        detail = summary.get("error") or run.stderr_tail or run.stdout_tail
        msg = f"blender scene skill failed (exit {proc.returncode}): {detail}"
        raise RuntimeError(_diagnose(msg, blender_bin))
    return run


def _diagnose(message: str, blender_bin: str) -> str:
    """Name the one failure that is about *which* Blender ran rather than about the scene.

    The data passes are read back out of multilayer EXR with Blender's bundled OpenImageIO. The
    upstream builds carry it (verified against 5.2.1); the Ubuntu package does not, and
    ``blender_bin`` defaults to the bare name, so PATH decides — and on a host with both, a
    forty-line ModuleNotFoundError traceback out of Blender's own Python is the only thing that
    says so. One line, naming the setting that fixes it, is worth more than the traceback.
    """
    if "OpenImageIO" not in message:
        return message
    found = shutil.which(blender_bin) or blender_bin
    alternatives = [
        candidate
        for candidate in ("/snap/bin/blender", "/usr/local/bin/blender", "/opt/blender/blender")
        if candidate != found and Path(candidate).exists()
    ]
    hint = (
        f" Try CF__CONTROLS__BLENDER_BIN={alternatives[0]}."
        if alternatives
        else " Install an upstream Blender build (the distro package will not do)."
    )
    return (
        f"{message}\n  This is the Blender, not the scene: {found} has no bundled OpenImageIO,"
        f" which the skill needs to read its own multilayer EXR passes back.{hint}"
    )
