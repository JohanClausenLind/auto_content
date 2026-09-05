from __future__ import annotations

import math

import pytest
from camera import (
    CameraState,
    ease,
    euler_xyz_deg_to_quat,
    interpolate,
    intrinsics,
    look_at_quaternion,
    project_camera_point,
    quat_to_matrix,
    slerp,
)


def _rotate(q, v):
    m = quat_to_matrix(q)
    return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))


def test_ease_endpoints_and_monotonic() -> None:
    for kind in ("linear", "ease_in", "ease_out", "ease_in_out"):
        assert ease(0.0, kind) == 0.0 and ease(1.0, kind) == 1.0
        xs = [ease(i / 20, kind) for i in range(21)]
        assert xs == sorted(xs)
    assert ease(0.5, "ease_in_out") == 0.5


def test_look_at_points_camera_minus_z_at_target() -> None:
    for pos, target in [
        ((0, -6, 1.2), (0, 0, 0.5)),
        ((3, 2, 1), (-1, -1, 0)),
        ((0, 0, 5), (0, 0, 0)),
        ((0, 0, -5), (0, 0, 0)),
    ]:
        q = look_at_quaternion(pos, target)
        fwd = _rotate(q, (0.0, 0.0, -1.0))  # camera -Z in world space
        d = tuple(t - p for t, p in zip(target, pos, strict=True))
        n = math.sqrt(sum(c * c for c in d))
        assert all(abs(f - c / n) < 1e-9 for f, c in zip(fwd, d, strict=True))
        up = _rotate(q, (0.0, 1.0, 0.0))
        assert (
            up[2] >= -1e-9 or abs(d[0]) + abs(d[1]) < 1e-9
        )  # camera up never points down (except straight up/down)


def test_look_at_from_front_matches_known_blender_quaternion() -> None:
    # Camera on -Y looking at the origin, level: Blender gives a 90 degree rotation about X.
    q = look_at_quaternion((0, -5, 0), (0, 0, 0))
    assert q == pytest.approx((math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0), abs=1e-9)


def test_euler_matches_quaternion_semantics() -> None:
    q = euler_xyz_deg_to_quat((90.0, 0.0, 0.0))
    assert q == pytest.approx((math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0), abs=1e-9)
    assert _rotate(euler_xyz_deg_to_quat((0.0, 0.0, 90.0)), (1.0, 0.0, 0.0)) == pytest.approx(
        (0.0, 1.0, 0.0), abs=1e-9
    )


def test_slerp_endpoints_and_shortest_path() -> None:
    a = (1.0, 0.0, 0.0, 0.0)
    b = euler_xyz_deg_to_quat((0.0, 0.0, 90.0))
    assert slerp(a, b, 0.0) == pytest.approx(a) and slerp(a, b, 1.0) == pytest.approx(b, abs=1e-9)
    mid = slerp(a, b, 0.5)
    assert mid == pytest.approx(euler_xyz_deg_to_quat((0.0, 0.0, 45.0)), abs=1e-9)
    assert slerp(a, tuple(-c for c in b), 0.5) == pytest.approx(
        mid, abs=1e-9
    )  # negated quaternion, same rotation


def test_interpolate_holds_ends_and_eases_between() -> None:
    cam = {
        "keyframes": [
            {
                "frame_index": 0,
                "position": [0, -6, 1],
                "look_at": [0, 0, 0],
                "lens_mm": 24,
                "easing_to_next": "linear",
            },
            {"frame_index": 10, "position": [0, -2, 1], "look_at": [0, 0, 0], "lens_mm": 48},
        ]
    }
    assert interpolate(cam, -3).position == (0, -6, 1)
    assert interpolate(cam, 99).position == (0, -2, 1)
    mid = interpolate(cam, 5)
    assert (
        mid.position == (0.0, -4.0, 1.0) and mid.lens_mm == 36.0 and mid.look_at == (0.0, 0.0, 0.0)
    )
    assert isinstance(mid, CameraState)
    m = mid.world_to_camera()
    assert len(m) == 16
    # The look-at target maps onto the camera's -Z axis.
    x, y, z = (sum(m[r * 4 + c] * (0, 0, 0, 1)[c] for c in range(4)) for r in range(3))
    assert abs(x) < 1e-9 and abs(y) < 1e-9 and z < 0


def test_interpolate_slerps_when_orientations_are_explicit() -> None:
    cam = {
        "keyframes": [
            {
                "frame_index": 0,
                "position": [0, 0, 0],
                "rotation_euler_deg": [90, 0, 0],
                "easing_to_next": "linear",
            },
            {"frame_index": 2, "position": [0, 0, 0], "rotation_euler_deg": [90, 0, 90]},
        ]
    }
    mid = interpolate(cam, 1)
    assert mid.look_at is None
    assert mid.quaternion == pytest.approx(euler_xyz_deg_to_quat((90, 0, 45)), abs=1e-9)


def test_intrinsics_and_projection() -> None:
    k = intrinsics(35.0, 36.0, 1024, 576)
    assert k[0] == pytest.approx(35 / 36 * 1024) and k[2:] == (512.0, 288.0)
    u, v, depth = project_camera_point((0.0, 0.0, -5.0), k, 1024, 576)
    assert (u, v, depth) == pytest.approx((0.5, 0.5, 5.0))
    u, v, _ = project_camera_point((1.0, 1.0, -5.0), k, 1024, 576)
    assert u > 0.5 and v < 0.5  # right and up in the image
    assert math.isnan(project_camera_point((0.0, 0.0, 1.0), k, 1024, 576)[0])  # behind the camera
