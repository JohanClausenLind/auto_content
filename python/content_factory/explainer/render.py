"""Subprocess wrappers around the Node renderer scripts; argument arrays, stderr surfaced."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from content_factory.schemas.base import canonical_dumps, file_sha256
from content_factory.schemas.explainer import (
    CaptureTile,
    DiagramLayout,
    ExplainerRenderBundle,
    PixelBox,
    VisualSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "apps" / "renderer" / "scripts"
# The timeline renderer's staging directory (video/render.py): what staticFile() can reach.
PUBLIC_ASSETS = REPO_ROOT / "apps" / "renderer" / "public" / "assets"
URL_PREFIXES = ("http:", "https:", "data:", "blob:", "file:", "assets/")
FrameMode = Literal["sequential", "stills"]


@dataclass(frozen=True)
class RenderResult:
    out: Path
    frames: int
    fps: int
    width: int
    height: int
    sha256: str


def layout_diagrams(spec: VisualSpec, regions: dict[str, PixelBox]) -> tuple[DiagramLayout, ...]:
    """ELK layouts for the diagram scenes, computed by apps/renderer/scripts/layout-diagrams.mjs."""
    script = script_path("layout-diagrams.mjs")
    with tempfile.TemporaryDirectory(prefix="explainer-layout-") as tmp:
        folder = Path(tmp)
        spec_path = folder / "spec.json"
        regions_path = folder / "regions.json"
        out_path = folder / "layouts.json"
        spec_path.write_text(spec.canonical_json(), encoding="utf-8")
        boxes = {sid: box.model_dump(mode="json") for sid, box in regions.items()}
        regions_path.write_text(canonical_dumps(boxes), encoding="utf-8")
        args = ["--spec", str(spec_path), "--regions", str(regions_path), "--out", str(out_path)]
        run_script(script, args)
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    return tuple(DiagramLayout.model_validate(item) for item in payload)


def render_bundle(
    bundle_path: Path,
    out_mp4: Path,
    *,
    scene: str | None = None,
    crf: int | None = None,
    concurrency: int | None = None,
) -> RenderResult:
    script = script_path("render-explainer.mjs")
    args = ["--bundle", str(bundle_path), "--out", str(out_mp4)]
    if scene is not None:
        args += ["--scene", scene]
    if concurrency is not None:
        args += ["--concurrency", str(concurrency)]
    if crf is not None:
        args += ["--crf", str(crf)]
    report = _json_line(run_script(script, args), script)
    return RenderResult(
        out=Path(report["out"]),
        frames=int(report["frames"]),
        fps=int(report["fps"]),
        width=int(report["width"]),
        height=int(report["height"]),
        sha256=str(report["sha256"]),
    )


def render_frames(
    bundle_path: Path, out_dir: Path, frames: Sequence[int], mode: FrameMode = "sequential"
) -> list[Path]:
    script = script_path("render-frames.mjs")
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_list = ",".join(str(f) for f in frames)
    args = ["--bundle", str(bundle_path), "--out-dir", str(out_dir), "--frames", frame_list]
    run_script(script, [*args, "--mode", mode])
    paths = [out_dir / f"f{frame:06d}.png" for frame in frames]
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        msg = f"{script.name} wrote no {', '.join(missing)} in {out_dir}"
        raise RuntimeError(msg)
    return paths


def stage_captures(bundle: ExplainerRenderBundle) -> ExplainerRenderBundle:
    """Copy every tile into public/assets by content hash, as the timeline stages its images."""
    if not bundle.captures:
        return bundle
    captures = tuple(
        c.model_copy(update={"tiles": tuple(_stage_tile(t) for t in c.tiles)})
        for c in bundle.captures
    )
    return bundle.model_copy(update={"captures": captures})


def _stage_tile(tile: CaptureTile) -> CaptureTile:
    if tile.path.startswith(URL_PREFIXES):
        return tile
    src = Path(tile.path)
    if not src.is_file():
        msg = f"capture tile {tile.path} is missing"
        raise FileNotFoundError(msg)
    digest = file_sha256(src)
    if digest != tile.sha256:
        msg = (
            f"capture tile {tile.path} hashes to {digest[:12]}, the bundle says {tile.sha256[:12]}"
        )
        raise ValueError(msg)
    name = f"{digest}{src.suffix.lower()}"
    dst = PUBLIC_ASSETS / name
    if not dst.exists():
        PUBLIC_ASSETS.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{name}.{os.getpid()}.tmp")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
    return tile.model_copy(update={"path": f"assets/{name}"})


def script_path(name: str) -> Path:
    path = SCRIPTS_DIR / name
    if not path.exists():
        msg = f"renderer script {path} is missing; the TS renderer must provide it"
        raise FileNotFoundError(msg)
    return path


def run_script(script: Path, args: Sequence[str]) -> str:
    node = shutil.which("node")
    if node is None:
        msg = "node is not on PATH; run ./setup.sh"
        raise FileNotFoundError(msg)
    completed = subprocess.run(  # noqa: S603  argument array, no shell, script path is ours
        [node, str(script), *args], capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    if completed.returncode != 0:
        msg = f"{script.name} exited {completed.returncode}:\n{completed.stderr.strip()}"
        raise RuntimeError(msg)
    return completed.stdout


def _json_line(stdout: str, script: Path) -> dict[str, Any]:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line)
            if isinstance(payload, dict):
                return payload
    msg = f"{script.name} printed no JSON report on stdout:\n{stdout.strip()}"
    raise RuntimeError(msg)
