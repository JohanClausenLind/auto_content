"""Scene reset and render configuration shared by every pass."""

from __future__ import annotations

from typing import Any

import bpy  # type: ignore[import-not-found]

from bl.compat import set_if_has

STAMP_FLAGS = (
    "use_stamp_date",
    "use_stamp_time",
    "use_stamp_render_time",
    "use_stamp_frame",
    "use_stamp_frame_range",
    "use_stamp_memory",
    "use_stamp_hostname",
    "use_stamp_camera",
    "use_stamp_lens",
    "use_stamp_scene",
    "use_stamp_marker",
    "use_stamp_filename",
    "use_stamp_sequencer_strip",
    "use_stamp_note",
)


def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def configure(scene: Any, spec: dict[str, Any]) -> None:
    rd = scene.render
    rd.resolution_x = spec["width"]
    rd.resolution_y = spec["height"]
    rd.resolution_percentage = 100
    rd.pixel_aspect_x = rd.pixel_aspect_y = 1.0
    rd.fps = int(spec["fps"])
    rd.fps_base = 1.0
    rd.use_border = False
    rd.use_motion_blur = False
    rd.film_transparent = False
    rd.dither_intensity = 0.0
    rd.use_compositing = False
    rd.use_sequencer = False
    rd.use_file_extension = True
    rd.use_overwrite = True
    rd.use_placeholder = False
    rd.use_stamp = False
    for flag in STAMP_FLAGS:
        set_if_has(rd, flag, False)
    scene.frame_start = 0
    scene.frame_end = max(0, spec["frame_count"] - 1)
    scene.display_settings.display_device = "sRGB"
    vs = scene.view_settings
    vs.view_transform = "Standard"
    set_if_has(vs, "look", "None")
    vs.exposure = 0.0
    vs.gamma = 1.0
    set_if_has(vs, "use_curve_mapping", False)
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    bg = tuple(spec["environment"].get("background_color", (0.05, 0.05, 0.06)))
    world.use_nodes = True
    node = world.node_tree.nodes.get("Background")
    if node is not None:
        node.inputs["Color"].default_value = (*bg, 1.0)
        node.inputs["Strength"].default_value = 1.0
    set_if_has(world, "color", bg)
