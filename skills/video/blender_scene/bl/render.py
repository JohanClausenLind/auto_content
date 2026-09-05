"""Render passes. Data passes (depth, normals, object index) come from Cycles on the CPU at one
sample per pixel: exact, GPU-free and deterministic. The rough RGB comes from Workbench (studio
clay look) when a GPU context exists, else from a Cycles CPU fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np
from mathutils import Vector  # type: ignore[import-not-found]

from bl.compat import find_channel, light_direction, read_multilayer_exr, set_if_has

BACKGROUND_DEPTH = 1e9


def _view_layer(scene: Any) -> Any:
    return scene.view_layers[0]


def configure_data_pass(scene: Any, spec: dict[str, Any]) -> None:
    scene.render.engine = "CYCLES"
    cy = scene.cycles
    cy.device = "CPU"
    cy.samples = 1
    set_if_has(cy, "use_adaptive_sampling", False)
    set_if_has(cy, "use_denoising", False)
    set_if_has(cy, "seed", int(spec.get("seed", 0)))
    set_if_has(cy, "use_animated_seed", False)
    set_if_has(cy, "pixel_filter_type", "BOX")
    set_if_has(cy, "filter_width", 1.0)
    set_if_has(cy, "max_bounces", 0)
    set_if_has(scene.render, "use_persistent_data", False)
    vl = _view_layer(scene)
    vl.use_pass_combined = True
    vl.use_pass_z = True
    vl.use_pass_normal = True
    vl.use_pass_object_index = True
    if hasattr(vl, "cycles"):
        set_if_has(vl.cycles, "denoising_store_passes", False)
    ims = scene.render.image_settings
    # Blender 5.x: multilayer EXR is a media type; the format enum only offers it once set.
    set_if_has(ims, "media_type", "MULTI_LAYER_IMAGE")
    ims.file_format = "OPEN_EXR_MULTILAYER"
    ims.color_mode = "RGBA"
    ims.color_depth = "32"
    set_if_has(ims, "exr_codec", "ZIP")


def render_data(scene: Any, out_stem: Path) -> Path:
    scene.render.filepath = str(out_stem)
    bpy.ops.render.render(write_still=True)
    path = out_stem.with_suffix(".exr")
    if not path.exists():
        raise RuntimeError(f"Cycles data render produced no file at {path}")
    return path


def decode_data(
    path: Path, world_to_camera: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """-> (depth float32 [H,W], normals_cam float32 [H,W,3] in [-1,1], index uint8 [H,W], channel names)."""
    ch = read_multilayer_exr(path)
    depth = find_channel(ch, "Depth.Z", ".Z")
    nx = find_channel(ch, "Normal.X")
    ny = find_channel(ch, "Normal.Y")
    nz = find_channel(ch, "Normal.Z")
    idx = find_channel(ch, "IndexOB.X", "Object Index.X", "IndexOB")
    if depth is None or nx is None or ny is None or nz is None or idx is None:
        raise RuntimeError(f"missing passes in {path.name}; channels: {sorted(ch)}")
    hit = depth < BACKGROUND_DEPTH
    n_world = np.stack([nx, ny, nz], axis=-1).astype(np.float64)
    r = world_to_camera[:3, :3]
    n_cam = n_world @ r.T
    norm = np.linalg.norm(n_cam, axis=-1, keepdims=True)
    n_cam = np.where(norm > 1e-6, n_cam / np.maximum(norm, 1e-6), 0.0)
    n_cam[~hit] = 0.0
    index = np.rint(np.where(hit, idx, 0.0)).clip(0, 255).astype(np.uint8)
    return depth.astype(np.float32), n_cam.astype(np.float32), index, sorted(ch)


def _sun(scene: Any, spec: dict[str, Any]) -> Any:
    light = bpy.data.lights.get("cf:key") or bpy.data.lights.new("cf:key", "SUN")
    light.energy = 3.0 * float(spec["lighting"].get("intensity", 1.0))
    light.angle = 0.0
    obj = bpy.data.objects.get("cf:key") or bpy.data.objects.new("cf:key", light)
    if obj.name not in scene.collection.objects:
        scene.collection.objects.link(obj)
    d = light_direction(
        float(spec["lighting"].get("key_azimuth_deg", 35.0)),
        float(spec["lighting"].get("key_elevation_deg", 45.0)),
    )
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector(d).to_track_quat("-Z", "Y")
    obj.location = (0.0, 0.0, 10.0)
    return obj


def _png(scene: Any) -> None:
    ims = scene.render.image_settings
    set_if_has(ims, "media_type", "IMAGE")
    ims.file_format = "PNG"
    ims.color_mode = "RGB"
    ims.color_depth = "8"
    ims.compression = 15


def configure_rgb_workbench(scene: Any, spec: dict[str, Any]) -> None:
    scene.render.engine = "BLENDER_WORKBENCH"
    sh = scene.display.shading
    sh.light = "STUDIO"
    sh.color_type = "MATERIAL"
    sh.show_shadows = bool(spec["lighting"].get("shadows", True))
    sh.shadow_intensity = 0.5
    sh.show_cavity = False
    sh.show_object_outline = True
    sh.show_specular_highlight = False
    set_if_has(sh, "use_dof", False)
    # Camera-relative studio lighting, not world-space. Measured across thirty runner stills that
    # orbit one figure: with world-space lighting the studio light stays put, so body brightness
    # swung from luma 39 to 116 depending only on where the camera stood, and twenty of the thirty
    # left the figure darker than luma 60 with less than 12 luma between it and the ground. The
    # pose signal is the whole reason this pass exists and a near-silhouette does not carry it.
    # Camera-relative lighting lifted the worst of those to luma 85-87 and left the ones that were
    # already lit alone. studiolight_rotate_z only applies to world-space lighting, so it is gone:
    # the light now follows the camera and there is no rotation to compute. Shadow direction still
    # comes from the shot's key azimuth below, in world space, where a shadow belongs.
    set_if_has(sh, "use_world_space_lighting", False)
    set_if_has(sh, "background_type", "WORLD")
    set_if_has(scene.display, "render_aa", "5")
    scene.display.light_direction = light_direction(
        float(spec["lighting"].get("key_azimuth_deg", 35.0)),
        float(spec["lighting"].get("key_elevation_deg", 45.0)),
    )
    _png(scene)


def configure_rgb_cycles(scene: Any, spec: dict[str, Any]) -> None:
    scene.render.engine = "CYCLES"
    cy = scene.cycles
    cy.device = "CPU"
    cy.samples = 16
    set_if_has(cy, "use_adaptive_sampling", False)
    set_if_has(cy, "use_denoising", False)
    set_if_has(cy, "seed", int(spec.get("seed", 0)))
    set_if_has(cy, "use_animated_seed", False)
    set_if_has(cy, "pixel_filter_type", "BLACKMAN_HARRIS")
    set_if_has(cy, "max_bounces", 2)
    vl = _view_layer(scene)
    vl.use_pass_z = vl.use_pass_normal = vl.use_pass_object_index = False
    _sun(scene, spec)
    _png(scene)


def render_rgb(scene: Any, out_stem: Path) -> Path:
    scene.render.filepath = str(out_stem)
    bpy.ops.render.render(write_still=True)
    path = out_stem.with_suffix(".png")
    if not path.exists():
        raise RuntimeError(f"RGB render produced no file at {path}")
    return path


def gpu_context_available() -> bool:
    """Workbench/EEVEE create their own offscreen GPU context in --background; the ``gpu`` module
    cannot be queried before that happens, so the only reliable probe is to try the render (the
    caller falls back to Cycles CPU on failure)."""
    return True
