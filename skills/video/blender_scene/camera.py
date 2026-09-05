"""Camera path math (pure Python, no bpy): easing, look-at quaternions, interpolation, intrinsics.

Conventions: Blender Z-up right-handed world; the camera looks down its local -Z with +Y up.
Quaternions are (w, x, y, z). Image coordinates are normalised, y down.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Any

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


def ease(t: float, kind: str) -> float:
    t = min(max(t, 0.0), 1.0)
    if kind == "linear":
        return t
    if kind == "ease_in":
        return t * t
    if kind == "ease_out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    return t * t * (3.0 - 2.0 * t)  # ease_in_out (smoothstep)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp3(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t))


def _norm(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-12:
        raise ValueError("zero-length vector")
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _mat_to_quat(m: tuple[Vec3, Vec3, Vec3]) -> Quat:
    """Rotation matrix given as three COLUMN vectors (x, y, z axes) -> unit quaternion (w,x,y,z)."""
    cx, cy, cz = m
    r00, r01, r02 = cx[0], cy[0], cz[0]
    r10, r11, r12 = cx[1], cy[1], cz[1]
    r20, r21, r22 = cx[2], cy[2], cz[2]
    tr = r00 + r11 + r22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w, x, y, z = 0.25 * s, (r21 - r12) / s, (r02 - r20) / s, (r10 - r01) / s
    elif r00 > r11 and r00 > r22:
        s = math.sqrt(1.0 + r00 - r11 - r22) * 2.0
        w, x, y, z = (r21 - r12) / s, 0.25 * s, (r01 + r10) / s, (r02 + r20) / s
    elif r11 > r22:
        s = math.sqrt(1.0 + r11 - r00 - r22) * 2.0
        w, x, y, z = (r02 - r20) / s, (r01 + r10) / s, 0.25 * s, (r12 + r21) / s
    else:
        s = math.sqrt(1.0 + r22 - r00 - r11) * 2.0
        w, x, y, z = (r10 - r01) / s, (r02 + r20) / s, (r12 + r21) / s, 0.25 * s
    q = (w, x, y, z)
    return normalize_quat(q if w >= 0 else (-w, -x, -y, -z))


def normalize_quat(q: Quat) -> Quat:
    n = math.sqrt(sum(c * c for c in q))
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def look_at_quaternion(position: Vec3, target: Vec3, up: Vec3 = (0.0, 0.0, 1.0)) -> Quat:
    """Quaternion that points a Blender camera (local -Z forward, +Y up) from position at target."""
    f = _norm((target[0] - position[0], target[1] - position[1], target[2] - position[2]))
    side = _cross(f, up)
    if math.sqrt(_dot(side, side)) < 1e-6:
        side = _cross(f, (0.0, 1.0, 0.0))  # looking straight up/down: fall back to +Y as "up"
    r = _norm(side)
    u = _cross(r, f)
    return _mat_to_quat((r, u, (-f[0], -f[1], -f[2])))


def quat_mul(a: Quat, b: Quat) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def euler_xyz_deg_to_quat(e: Vec3) -> Quat:
    """Blender XYZ Euler (degrees) -> quaternion, matching mathutils.Euler(..., 'XYZ')."""
    x, y, z = (math.radians(v) for v in e)
    qx = (math.cos(x / 2), math.sin(x / 2), 0.0, 0.0)
    qy = (math.cos(y / 2), 0.0, math.sin(y / 2), 0.0)
    qz = (math.cos(z / 2), 0.0, 0.0, math.sin(z / 2))
    return normalize_quat(quat_mul(qz, quat_mul(qy, qx)))


def slerp(a: Quat, b: Quat, t: float) -> Quat:
    d = sum(x * y for x, y in zip(a, b, strict=True))
    if d < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
        d = -d
    if d > 0.9995:
        return normalize_quat(
            (lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t), lerp(a[3], b[3], t))
        )
    theta0 = math.acos(min(1.0, d))
    theta = theta0 * t
    s0 = math.cos(theta) - d * math.sin(theta) / math.sin(theta0)
    s1 = math.sin(theta) / math.sin(theta0)
    return normalize_quat(
        (s0 * a[0] + s1 * b[0], s0 * a[1] + s1 * b[1], s0 * a[2] + s1 * b[2], s0 * a[3] + s1 * b[3])
    )


def quat_to_matrix(q: Quat) -> list[list[float]]:
    """Row-major 3x3 rotation matrix (world-from-camera) for a unit quaternion."""
    w, x, y, z = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


@dataclass(frozen=True)
class CameraState:
    frame_index: int
    position: Vec3
    quaternion: Quat
    lens_mm: float
    focus_distance: float
    look_at: Vec3 | None

    def world_to_camera(self) -> list[float]:
        """Row-major 4x4 matrix mapping world points into camera space (Blender convention)."""
        r = quat_to_matrix(self.quaternion)
        rt = [[r[j][i] for j in range(3)] for i in range(3)]
        p = self.position
        t = [-(rt[i][0] * p[0] + rt[i][1] * p[1] + rt[i][2] * p[2]) for i in range(3)]
        rows = [rt[0] + [t[0]], rt[1] + [t[1]], rt[2] + [t[2]], [0.0, 0.0, 0.0, 1.0]]
        return [v for row in rows for v in row]


def _kf_quat(kf: dict[str, Any]) -> Quat:
    pos: Vec3 = tuple(kf["position"])  # type: ignore[assignment]
    if kf.get("look_at") is not None:
        return look_at_quaternion(pos, tuple(kf["look_at"]))  # type: ignore[arg-type]
    return euler_xyz_deg_to_quat(tuple(kf["rotation_euler_deg"]))  # type: ignore[arg-type]


def interpolate(camera: dict[str, Any], frame: int) -> CameraState:
    """Camera state at ``frame`` from the spec's keyframes (holds before/after the ends)."""
    kfs = camera["keyframes"]
    prev, nxt, t = kfs[0], kfs[0], 0.0
    if frame >= kfs[-1]["frame_index"]:
        prev = nxt = kfs[-1]
    elif frame > kfs[0]["frame_index"]:
        for a, b in itertools.pairwise(kfs):
            if a["frame_index"] <= frame < b["frame_index"]:
                span = b["frame_index"] - a["frame_index"]
                t = ease((frame - a["frame_index"]) / span, a.get("easing_to_next", "ease_in_out"))
                prev, nxt = a, b
                break
    pos = lerp3(tuple(prev["position"]), tuple(nxt["position"]), t)  # type: ignore[arg-type]
    lens = lerp(float(prev.get("lens_mm", 35.0)), float(nxt.get("lens_mm", 35.0)), t)
    focus = lerp(float(prev.get("focus_distance", 5.0)), float(nxt.get("focus_distance", 5.0)), t)
    look: Vec3 | None = None
    if prev.get("look_at") is not None and nxt.get("look_at") is not None:
        look = lerp3(tuple(prev["look_at"]), tuple(nxt["look_at"]), t)  # type: ignore[arg-type]
        quat = look_at_quaternion(pos, look)
    else:
        quat = slerp(_kf_quat(prev), _kf_quat(nxt), t)
    rounded: Vec3 = (round(pos[0], 6), round(pos[1], 6), round(pos[2], 6))
    return CameraState(frame, rounded, quat, lens, focus, look)


def intrinsics(
    lens_mm: float, sensor_width_mm: float, width: int, height: int
) -> tuple[float, float, float, float]:
    """(fx, fy, cx, cy) in pixels for sensor_fit=HORIZONTAL and square pixels."""
    fx = lens_mm / sensor_width_mm * width
    return (fx, fx, width / 2.0, height / 2.0)


def project_camera_point(
    p_cam: Vec3, k: tuple[float, float, float, float], width: int, height: int
) -> tuple[float, float, float]:
    """Camera-space point -> (u_norm, v_norm, depth); depth along the view axis, positive in front."""
    fx, fy, cx, cy = k
    depth = -p_cam[2]
    if depth <= 1e-9:
        return (float("nan"), float("nan"), depth)
    u = fx * p_cam[0] / depth + cx
    v = cy - fy * p_cam[1] / depth
    return (u / width, v / height, depth)
