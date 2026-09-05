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


PUBLIC_ASSETS = RENDERER_DIR / "public" / "assets"
"""Where `bundle.assets` files are staged so the renderer can serve them.

The renderer resolves an asset path through `staticFile()`, which is relative to the bundle's own
public directory — and Remotion copies that directory into the bundle at bundle time. An absolute
path to an operator's upload is therefore a 404, which is why `image`, `screenshot` and `map`
scenes were unreachable in practice even once `ingest` recorded the files: nothing put them where
the browser could read them.

Staged by content hash, and never cleaned: it makes the directory a content-addressed cache, so a
rerun with the same uploads reuses the cached webpack bundle instead of paying a rebundle for a
public directory that changed. It also makes the rendered bundle's own hash independent of where
the project lives on disk, which a render provenance record should be.
"""

ASSET_BYTES_MAX = 64 * 1024 * 1024
"""Refuse to stage anything larger. A still or a topojson is kilobytes to a few megabytes; a
64 MB "image" is a mistake, and copying it into the renderer's public directory would put it in
every webpack bundle from then on."""


def stage_assets(bundle: RenderBundle) -> RenderBundle:
    """Copy `bundle.assets` into the renderer's public directory, returning a bundle that names
    the staged copies. A path that is already a URL is left alone; a missing file is left alone
    too, because the scenes draw a labelled card for an asset they cannot resolve and that is a
    better failure than refusing to render the rest of the film."""
    if not bundle.assets:
        return bundle
    staged: dict[str, str] = {}
    for asset_id, raw in bundle.assets.items():
        if raw.startswith(("http:", "https:", "data:", "blob:", "file:")):
            staged[asset_id] = raw
            continue
        src = Path(raw)
        if not src.is_file():
            staged[asset_id] = raw
            continue
        size = src.stat().st_size
        if size > ASSET_BYTES_MAX:
            msg = f"asset {asset_id} is {size} bytes, over the {ASSET_BYTES_MAX} staging limit"
            raise RenderError(msg)
        digest = sha256_hex(src.read_bytes())
        name = f"{digest}{src.suffix.lower()}"
        dst = PUBLIC_ASSETS / name
        if not dst.exists():
            PUBLIC_ASSETS.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f".{name}.{os.getpid()}.tmp")
            tmp.write_bytes(src.read_bytes())
            tmp.replace(dst)
        # Public-relative and forward-slashed: this string is handed to `staticFile()`.
        staged[asset_id] = f"assets/{name}"
    return bundle.model_copy(update={"assets": staged})


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
    scale: int = 1,
) -> RenderOutcome:
    """``scale`` renders at a multiple of the artboard's pixel size (a retina export).

    The QC is told the scaled size rather than the artboard's, so a 2x render is checked against
    what was asked for instead of failing its own dimensions.
    """
    if bundle.kind != "artboard" or bundle.artboard is None:
        raise RenderError("bundle is not an artboard bundle")
    if scale < 1:
        raise RenderError(f"render scale must be 1 or more, got {scale}")
    bpath, bhash = _write_bundle(stage_assets(bundle), workdir)
    out = workdir / f"{bundle.bundle_id}.png"
    args = ["--bundle", str(bpath), "--out", str(out)]
    if scale != 1:
        args += ["--scale", str(scale)]
    result = _run_node("render-artboard.mjs", args, timeout_s=timeout_s)
    qc = check_still(
        out, width=bundle.artboard.width * scale, height=bundle.artboard.height * scale
    )
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
    bpath, bhash = _write_bundle(stage_assets(bundle), workdir)
    suffix = f".{scene_id}" if scene_id else ""
    out = workdir / f"{bundle.bundle_id}{suffix}.mp4"
    args = ["--bundle", str(bpath.resolve()), "--out", str(out.resolve())]
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
