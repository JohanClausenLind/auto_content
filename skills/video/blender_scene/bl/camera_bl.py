"""Camera object creation and per-frame state application."""

from __future__ import annotations

from typing import Any

import bpy  # type: ignore[import-not-found]
from camera import CameraState, interpolate, intrinsics
from mathutils import Quaternion, Vector  # type: ignore[import-not-found]


def create(scene: Any, spec: dict[str, Any]) -> Any:
    cam_data = bpy.data.cameras.new("Camera")
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    c = spec["camera"]
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.sensor_width = float(c.get("sensor_width_mm", 36.0))
    cam_data.clip_start = float(c.get("clip_start", 0.05))
    cam_data.clip_end = float(c.get("clip_end", 100.0))
    cam_data.dof.use_dof = False
    cam_data.shift_x = cam_data.shift_y = 0.0
    cam.rotation_mode = "QUATERNION"
    return cam


def apply(cam: Any, state: CameraState) -> None:
    cam.location = Vector(state.position)
    cam.rotation_quaternion = Quaternion(state.quaternion)
    cam.data.lens = float(state.lens_mm)
    cam.data.dof.focus_distance = float(state.focus_distance)


def state_for_frame(spec: dict[str, Any], frame: int) -> CameraState:
    return interpolate(spec["camera"], frame)


def track_quat_check(state: CameraState) -> list[float]:
    """Blender's own look-at quaternion for the same state (cross-check of camera.py)."""
    if state.look_at is None:
        return list(state.quaternion)
    d = Vector(state.look_at) - Vector(state.position)
    q = d.to_track_quat("-Z", "Y")
    return [q.w, q.x, q.y, q.z]


def camera_record(spec: dict[str, Any], cam: Any, state: CameraState) -> dict[str, Any]:
    w, h = spec["width"], spec["height"]
    sensor = float(spec["camera"].get("sensor_width_mm", 36.0))
    k = intrinsics(state.lens_mm, sensor, w, h)
    m = cam.matrix_world.inverted()
    return {
        "frame_index": state.frame_index,
        "position": [round(v, 6) for v in state.position],
        "rotation_quat_wxyz": [round(v, 9) for v in state.quaternion],
        "look_at": [round(v, 6) for v in state.look_at] if state.look_at is not None else None,
        "lens_mm": round(state.lens_mm, 6),
        "sensor_width_mm": sensor,
        "focus_distance": round(state.focus_distance, 6),
        "intrinsics": [round(v, 6) for v in k],
        "world_to_camera": [round(float(m[r][c]), 9) for r in range(4) for c in range(4)],
        "track_quat_check": [round(v, 9) for v in track_quat_check(state)],
    }
