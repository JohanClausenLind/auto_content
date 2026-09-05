"""Per-frame geometry exports: layout boxes for every entity, OpenPose-18 skeletons for characters."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from camera import CameraState, intrinsics
from layout import bbox_from_points, hidream_boxes, visible_fraction, xxyy
from mathutils import Vector  # type: ignore[import-not-found]

from bl.assets import Entity

OPENPOSE18 = (
    "nose",
    "neck",
    "r_shoulder",
    "r_elbow",
    "r_wrist",
    "l_shoulder",
    "l_elbow",
    "l_wrist",
    "r_hip",
    "r_knee",
    "r_ankle",
    "l_hip",
    "l_knee",
    "l_ankle",
    "r_eye",
    "l_eye",
    "r_ear",
    "l_ear",
)
OPENPOSE18_LIMBS = (
    ("neck", "r_shoulder"),
    ("neck", "l_shoulder"),
    ("r_shoulder", "r_elbow"),
    ("r_elbow", "r_wrist"),
    ("l_shoulder", "l_elbow"),
    ("l_elbow", "l_wrist"),
    ("neck", "r_hip"),
    ("r_hip", "r_knee"),
    ("r_knee", "r_ankle"),
    ("neck", "l_hip"),
    ("l_hip", "l_knee"),
    ("l_knee", "l_ankle"),
    ("neck", "nose"),
    ("nose", "r_eye"),
    ("r_eye", "r_ear"),
    ("nose", "l_eye"),
    ("l_eye", "l_ear"),
)
# Occlusion tolerance per joint (metres): how far behind the first surface a joint may sit and
# still count as visible (joints are inside the body).
_TOL = {
    "nose": 0.04,
    "neck": 0.12,
    "r_hip": 0.14,
    "l_hip": 0.14,
    "r_eye": 0.04,
    "l_eye": 0.04,
    "r_ear": 0.04,
    "l_ear": 0.04,
}


def world_vertices(obj: Any, depsgraph: Any) -> np.ndarray:
    """Evaluated (posed, modified) mesh vertices in world space, shape (N, 3)."""
    ob_eval = obj.evaluated_get(depsgraph)
    me = ob_eval.to_mesh()
    try:
        n = len(me.vertices)
        buf = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get("co", buf)
        local = buf.reshape(n, 3)
    finally:
        ob_eval.to_mesh_clear()
    m = np.array(obj.matrix_world, dtype=np.float64)
    pts = np.concatenate([local, np.ones((local.shape[0], 1))], axis=1) @ m.T
    return pts[:, :3]


def project(
    points_world: np.ndarray,
    world_to_camera: np.ndarray,
    k: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """-> (u_norm, v_norm, depth) arrays; depth along the view axis, positive in front."""
    fx, fy, cx, cy = k
    hom = np.concatenate([points_world, np.ones((points_world.shape[0], 1))], axis=1)
    cam = hom @ world_to_camera.T
    depth = -cam[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = np.where(depth > 1e-9, fx * cam[:, 0] / depth + cx, np.nan) / width
        v = np.where(depth > 1e-9, cy - fy * cam[:, 1] / depth, np.nan) / height
    return u, v, depth


def _k(spec: dict[str, Any], state: CameraState) -> tuple[float, float, float, float]:
    return intrinsics(
        state.lens_mm,
        float(spec["camera"].get("sensor_width_mm", 36.0)),
        spec["width"],
        spec["height"],
    )


def layout_frame(
    spec: dict[str, Any], entities: list[Entity], cam: Any, state: CameraState, depsgraph: Any
) -> tuple[dict[str, Any], tuple[float, float] | None]:
    """Layout JSON for one frame plus the (near, far) depth extent of characters + props."""
    w, h = spec["width"], spec["height"]
    m = np.array(cam.matrix_world.inverted(), dtype=np.float64)
    k = _k(spec, state)
    objects: list[dict[str, Any]] = []
    near, far = math.inf, -math.inf
    for ent in entities:
        if ent.kind == "environment" or not ent.objects:
            continue
        pts = np.concatenate([world_vertices(o, depsgraph) for o in ent.objects], axis=0)
        u, v, depth = project(pts, m, k, w, h)
        front = depth > 1e-9
        if front.any():
            near, far = min(near, float(depth[front].min())), max(far, float(depth[front].max()))
        uv = [(float(a), float(b)) for a, b in zip(u, v, strict=True)]
        box = bbox_from_points(uv)
        entry: dict[str, Any] = {
            "object_id": ent.entity_id,
            "kind": ent.kind,
            "seg_id": ent.seg_id,
            "box": None if box is None else {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
            "xxyy": None if box is None else xxyy(box),
            "depth_min": round(float(depth[front].min()), 4) if front.any() else None,
            "depth_max": round(float(depth[front].max()), 4) if front.any() else None,
            "visible_fraction": visible_fraction(uv),
        }
        objects.append(entry)
    doc = {
        "schema": "cf.blender_scene.layout.v1",
        "frame_index": state.frame_index,
        "width": w,
        "height": h,
        "objects": objects,
        "hidream_layout_bboxes": hidream_boxes(objects),
    }
    extent = (near, far) if near < math.inf else None
    return doc, extent


def _visible(
    scene: Any, depsgraph: Any, cam_pos: Vector, joint: Vector, own: set[str], tol: float
) -> bool:
    d = joint - cam_pos
    dist = d.length
    if dist < 1e-6:
        return True
    hit, loc, _n, _i, obj, _m = scene.ray_cast(depsgraph, cam_pos, d.normalized())
    if not hit:
        return True
    if obj is not None and obj.name in own:
        return True
    return (loc - cam_pos).length >= dist - tol


def skeleton_frame(
    spec: dict[str, Any],
    entities: list[Entity],
    cam: Any,
    state: CameraState,
    depsgraph: Any,
    scene: Any,
) -> dict[str, Any]:
    w, h = spec["width"], spec["height"]
    m = np.array(cam.matrix_world.inverted(), dtype=np.float64)
    k = _k(spec, state)
    cam_pos = Vector(state.position)
    people: list[dict[str, Any]] = []
    for ent in entities:
        if ent.kind != "character" or not ent.kp_anchors:
            continue
        src = ent.kp_proxy if ent.kp_proxy is not None else ent.body
        if src is None:
            continue
        verts = world_vertices(src, depsgraph)
        own = {o.name for o in ent.objects} | ({src.name} if src is not None else set())
        joints: dict[str, dict[str, Any]] = {}
        flat: list[float] = []
        for name in OPENPOSE18:
            idx = ent.kp_anchors.get(name)
            if not idx:
                flat.extend([0.0, 0.0, 0.0])
                continue
            p = verts[np.asarray(idx, dtype=np.int64)].mean(axis=0)
            u, v, depth = project(p[None, :], m, k, w, h)
            uu, vv, dd = float(u[0]), float(v[0]), float(depth[0])
            in_frame = dd > 0 and 0.0 <= uu <= 1.0 and 0.0 <= vv <= 1.0
            visible = in_frame and _visible(
                scene, depsgraph, cam_pos, Vector(p), own, _TOL.get(name, 0.06)
            )
            joints[name] = {
                "x": round(uu, 6),
                "y": round(vv, 6),
                "z": round(dd, 4),
                "visible": bool(visible),
                "in_frame": bool(in_frame),
            }
            conf = 1.0 if visible else (0.3 if in_frame else 0.0)
            flat.extend([round(uu * w, 2), round(vv * h, 2), conf] if in_frame else [0.0, 0.0, 0.0])
        people.append(
            {
                "character_id": ent.entity_id,
                "seg_id": ent.seg_id,
                "joints": joints,
                "bones": [list(b) for b in OPENPOSE18_LIMBS if b[0] in joints and b[1] in joints],
                "openpose18": flat,
            }
        )
    return {
        "schema": "cf.blender_scene.skeleton.v1",
        "frame_index": state.frame_index,
        "width": w,
        "height": h,
        "people": people,
    }
