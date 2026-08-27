"""RenderBackend: local_process (ADR 0003). Calls the Node renderer with argument arrays, never
shell strings; stores outputs in the ArtifactStore with QC facts and a provenance record."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from content_factory.artifacts import ArtifactRef, ArtifactStore
from content_factory.qc.media import QCResult, check_still, check_video
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.render import RenderBundle

REPO_ROOT = Path(__file__).resolve().parents[3]
RENDERER_DIR = REPO_ROOT / "apps" / "renderer"


class RenderError(Exception):
    pass


@dataclass(frozen=True)
class RenderOutcome:
    artifact: ArtifactRef
    qc: QCResult
    renderer_stdout: dict
    bundle_sha256: str


def _run_node(script: str, args: list[str], *, timeout_s: int) -> dict:
    cmd = ["node", str(RENDERER_DIR / "scripts" / script), *args]
    env = {**os.environ, "CI": "1"}
    proc = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        cwd=RENDERER_DIR,
        env=env,
    )
    if proc.returncode != 0:
        raise RenderError(f"{script} failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}")
    last = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")]
    if not last:
        raise RenderError(f"{script} produced no JSON result: {proc.stdout[-500:]}")
    return json.loads(last[-1])


def _write_bundle(bundle: RenderBundle, workdir: Path) -> tuple[Path, str]:
    workdir.mkdir(parents=True, exist_ok=True)
    text = bundle.canonical_json()
    path = workdir / f"{bundle.bundle_id}.json"
    path.write_text(text, encoding="utf-8")
    return path, sha256_hex(text.encode("utf-8"))


def render_artboard(
    bundle: RenderBundle,
    *,
    workspace_id: str,
    store: ArtifactStore,
    workdir: Path,
    timeout_s: int = 600,
) -> RenderOutcome:
    if bundle.kind != "artboard" or bundle.artboard is None:
        raise RenderError("bundle is not an artboard bundle")
    bpath, bhash = _write_bundle(bundle, workdir)
    out = workdir / f"{bundle.bundle_id}.png"
    result = _run_node(
        "render-artboard.mjs", ["--bundle", str(bpath), "--out", str(out)], timeout_s=timeout_s
    )
    qc = check_still(out, width=bundle.artboard.width, height=bundle.artboard.height)
    ref = store.put_file(workspace_id, "renders", out)
    return RenderOutcome(ref, qc, result, bhash)


def render_timeline(
    bundle: RenderBundle,
    *,
    workspace_id: str,
    store: ArtifactStore,
    workdir: Path,
    scene_id: str | None = None,
    timeout_s: int = 1800,
) -> RenderOutcome:
    if bundle.kind != "timeline" or bundle.timeline is None or bundle.plan is None:
        raise RenderError("bundle is not a timeline bundle")
    bpath, bhash = _write_bundle(bundle, workdir)
    suffix = f".{scene_id}" if scene_id else ""
    out = workdir / f"{bundle.bundle_id}{suffix}.mp4"
    args = ["--bundle", str(bpath), "--out", str(out)]
    if scene_id:
        args += ["--scene", scene_id]
    result = _run_node("render-timeline.mjs", args, timeout_s=timeout_s)
    tl = bundle.timeline
    frames = tl.total_frames
    if scene_id:
        frames = next(s.duration_frames for s in tl.scenes if s.scene_id == scene_id)
    qc = check_video(out, width=tl.width, height=tl.height, fps=tl.fps, frames=frames)
    ref = store.put_file(workspace_id, "renders", out)
    return RenderOutcome(ref, qc, result, bhash)
