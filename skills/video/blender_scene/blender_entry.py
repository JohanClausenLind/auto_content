"""Blender-side entry point. Run inside Blender's Python:

    blender --background --factory-startup --python-exit-code 3 \
        --python blender_entry.py -- <spec.json> <raw_dir> [--assets=<root>]

Writes raw per-frame outputs into <raw_dir> (the venv-side postprocess turns them into the final
pass directories): data_NNNN.exr (multilayer), depth_NNNN.npy, normals_NNNN.npy, index_NNNN.npy,
depth_NNNN.exr, rgb_NNNN.png, skeleton_NNNN.json, layout_NNNN.json, camera.json, manifest.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def _args() -> tuple[Path, Path, Path]:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    if len(argv) < 2:
        raise SystemExit("usage: blender_entry.py -- <spec.json> <raw_dir> [--assets=<root>]")
    assets = os.environ.get("CF_BLENDER_ASSETS", "/mnt/fast/models/blender-assets")
    for a in argv[2:]:
        if a.startswith("--assets="):
            assets = a.split("=", 1)[1]
    return Path(argv[0]), Path(argv[1]), Path(assets).expanduser()


def _dump(path: Path, doc: object) -> None:
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    import bpy  # type: ignore[import-not-found]
    import numpy as np
    from bl import assets as bl_assets
    from bl import camera_bl, ids, keypoints, posing
    from bl import render as bl_render
    from bl import scene as bl_scene
    from bl.compat import blender_version, build_hash, write_single_channel_exr
    from spec import SKILL_VERSION, frames_to_render, load_spec, seg_assignments, spec_sha256

    spec_path, raw_dir, assets_root = _args()
    t0 = time.time()
    spec = load_spec(spec_path)
    raw_dir.mkdir(parents=True, exist_ok=True)
    passes = set(spec["render"]["passes"])
    frames = frames_to_render(spec)

    bl_scene.reset()
    scene = bpy.context.scene
    bl_scene.configure(scene, spec)
    entities = bl_assets.build(scene, spec, assets_root)
    seg_map = ids.assign(entities, seg_assignments(spec))
    cam = camera_bl.create(scene, spec)
    fps = int(spec["fps"])

    pose_reports: list[dict] = []

    def apply_frame(frame: int) -> camera_bl.CameraState:
        scene.frame_set(frame)
        state = camera_bl.state_for_frame(spec, frame)
        camera_bl.apply(cam, state)
        for ent in entities:
            if ent.kind == "character" and ent.rig is not None:
                report = posing.apply_for_frame(ent.rig, ent.pose, assets_root, frame, fps)
                if report is not None:
                    pose_reports.append({"entity": ent.entity_id, "frame": frame, **report})
        bpy.context.view_layer.update()
        return state

    camera_records: list[dict] = []
    extents: list[tuple[float, float]] = []
    rendered_depth_min, rendered_depth_max = float("inf"), float("-inf")
    channels_seen: list[str] = []

    need_data = passes & {"depth", "depth16", "depth_exr", "normals", "segmentation"}
    if need_data:
        bl_render.configure_data_pass(scene, spec)
    for frame in frames:
        state = apply_frame(frame)
        dg = bpy.context.evaluated_depsgraph_get()
        rec = camera_rec = camera_bl.camera_record(spec, cam, state)
        camera_records.append(rec)
        layout_doc, extent = keypoints.layout_frame(spec, entities, cam, state, dg)
        if extent is not None:
            extents.append(extent)
        _dump(raw_dir / f"layout_{frame:04d}.json", layout_doc)
        _dump(
            raw_dir / f"skeleton_{frame:04d}.json",
            keypoints.skeleton_frame(spec, entities, cam, state, dg, scene),
        )
        if need_data:
            exr = bl_render.render_data(scene, raw_dir / f"data_{frame:04d}")
            m = np.array(camera_rec["world_to_camera"], dtype=np.float64).reshape(4, 4)
            depth, normals, index, channels_seen = bl_render.decode_data(exr, m)
            hit = depth < bl_render.BACKGROUND_DEPTH
            if hit.any():
                rendered_depth_min = min(rendered_depth_min, float(depth[hit].min()))
                rendered_depth_max = max(rendered_depth_max, float(depth[hit].max()))
            np.save(raw_dir / f"depth_{frame:04d}.npy", depth)
            np.save(raw_dir / f"normals_{frame:04d}.npy", normals)
            np.save(raw_dir / f"index_{frame:04d}.npy", index)
            if "depth_exr" in passes:
                write_single_channel_exr(raw_dir / f"depth_{frame:04d}.exr", depth)
            if not os.environ.get("CF_BLENDER_KEEP_EXR"):
                exr.unlink()

    rgb_engine = None
    if passes & {"rough_rgb", "canny"}:
        want = spec["render"].get("engine", "workbench")
        chain = [want] if want == "cycles_cpu" else [want, "cycles_cpu"]
        for engine in chain:
            try:
                if engine in ("workbench", "eevee"):
                    if not bl_render.gpu_context_available():
                        raise RuntimeError("no GPU context for Workbench/EEVEE in --background")
                    bl_render.configure_rgb_workbench(scene, spec)
                    if engine == "eevee":
                        scene.render.engine = "BLENDER_EEVEE"
                else:
                    bl_render.configure_rgb_cycles(scene, spec)
                for frame in frames:
                    apply_frame(frame)
                    bl_render.render_rgb(scene, raw_dir / f"rgb_{frame:04d}")
                rgb_engine = engine
                break
            except Exception as exc:
                sys.stderr.write(f"[blender_scene] rgb engine {engine} failed: {exc}\n")
                rgb_engine = None
        if rgb_engine is None:
            raise RuntimeError("no RGB render engine succeeded")

    # Depth normalisation range: characters + props across all frames (padded), else what was hit.
    dr = spec["render"].get("depth_range", "auto")
    if isinstance(dr, dict):
        near, far, source = float(dr["near"]), float(dr["far"]), "explicit"
    elif extents:
        near = min(e[0] for e in extents)
        far = max(e[1] for e in extents)
        pad = max(0.05 * (far - near), 0.05)
        near, far, source = max(0.01, near - pad), far + pad, "auto:entities"
    elif rendered_depth_min < float("inf"):
        near, far, source = (
            max(0.01, rendered_depth_min * 0.95),
            rendered_depth_max * 1.05,
            "auto:rendered",
        )
    else:
        near, far, source = 0.1, 10.0, "default"
    if far <= near:
        far = near + 1.0

    _dump(
        raw_dir / "camera.json",
        {
            "schema": "cf.blender_scene.camera.v1",
            "width": spec["width"],
            "height": spec["height"],
            "fps": fps,
            "frame_count": spec["frame_count"],
            "sensor_width_mm": float(spec["camera"].get("sensor_width_mm", 36.0)),
            "sensor_fit": "HORIZONTAL",
            "convention": {
                "blender": "-Z forward, +Y up",
                "quaternion": "wxyz",
                "image": "normalised, y down",
            },
            "frames": camera_records,
        },
    )
    _dump(
        raw_dir / "manifest.json",
        {
            "schema": "cf.blender_scene.manifest.v1",
            "skill_version": SKILL_VERSION,
            "blender_version": blender_version(),
            "blender_build_hash": build_hash(),
            "spec_sha256": spec_sha256(spec),
            "shot_id": spec["shot_id"],
            "width": spec["width"],
            "height": spec["height"],
            "fps": fps,
            "frame_count": spec["frame_count"],
            "frames_rendered": frames,
            "seed": int(spec.get("seed", 0)),
            "passes": sorted(passes),
            "engines": {"data": "cycles_cpu" if need_data else None, "rgb": rgb_engine},
            "depth": {"near": round(near, 6), "far": round(far, 6), "source": source},
            "seg": {"objects": seg_map, "background": 0},
            "characters": {
                e.entity_id: {
                    "asset": e.asset,
                    "rig_type": e.rig_type,
                    "kp_joints": len(e.kp_anchors),
                }
                for e in entities
                if e.kind == "character"
            },
            "exr_channels": channels_seen,
            # One row per posed character per frame. For a cf.clip.v2 clip it carries how many
            # bones the aim solve actually aimed, which is the number that says the retarget ran
            # rather than silently doing nothing.
            "poses": pose_reports,
            "elapsed_s": round(time.time() - t0, 3),
        },
    )
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        raise
    sys.exit(code)
