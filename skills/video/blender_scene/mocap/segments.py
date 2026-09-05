"""Which rig bones each mocap segment aims, and the pure-maths half of the aim solve.

Kept out of ``bl/`` so it can be unit-tested without Blender: everything here works on plain 4x4
matrices, and ``bl/retarget.py`` is the thin layer that reads them off an armature and writes the
quaternions back.

Why aim instead of copying joint angles. MPFB fits its rig to each character mesh, so the rest
orientation of a given bone differs from body to body. A quaternion solved on one character is
therefore wrong on another - measured at a mean of 11.4 degrees and a maximum of 22.6 degrees,
with a whole leg 18-21 degrees out. A world direction is a property of the motion rather than of
the performer, so aiming each bone along the captured direction is correct on every rig by
construction.

MPFB splits limbs where CMU does not: one femur becomes ``upperleg01`` plus ``upperleg02``. Both
halves are aimed along the same segment direction, which keeps the limb straight and its length
intact.
"""

from __future__ import annotations

import math

import numpy as np

# CMU segment -> the MPFB "default" rig bones it aims, parent first.
SEGMENT_TO_BONES: dict[str, tuple[str, ...]] = {
    "lhipjoint": ("pelvis.L",),
    "rhipjoint": ("pelvis.R",),
    "lfemur": ("upperleg01.L", "upperleg02.L"),
    "rfemur": ("upperleg01.R", "upperleg02.R"),
    "ltibia": ("lowerleg01.L", "lowerleg02.L"),
    "rtibia": ("lowerleg01.R", "lowerleg02.R"),
    "lfoot": ("foot.L",),
    "rfoot": ("foot.R",),
    "lowerback": ("spine05", "spine04"),
    "upperback": ("spine03", "spine02"),
    "thorax": ("spine01",),
    "lowerneck": ("neck01", "neck02"),
    "upperneck": ("neck03",),
    "head": ("head",),
    "lclavicle": ("clavicle.L", "shoulder01.L"),
    "rclavicle": ("clavicle.R", "shoulder01.R"),
    "lhumerus": ("upperarm01.L", "upperarm02.L"),
    "rhumerus": ("upperarm01.R", "upperarm02.R"),
    "lradius": ("lowerarm01.L", "lowerarm02.L"),
    "rradius": ("lowerarm01.R", "lowerarm02.R"),
    "lwrist": ("wrist.L",),
    "rwrist": ("wrist.R",),
}

# CMU's hands are one rigid segment and the MPFB hand has twenty bones, so aiming fingers from
# this source would invent detail. Named here so the omission is a decision, not an oversight.
UNMAPPED_SEGMENTS: tuple[str, ...] = ("lhand", "rhand")

RIG_NAME = "mpfb.default"


def bone_target_map(directions: dict[str, list[float]]) -> dict[str, np.ndarray]:
    """Segment directions -> a unit target direction per rig bone."""
    out: dict[str, np.ndarray] = {}
    for segment, bones in SEGMENT_TO_BONES.items():
        d = directions.get(segment)
        if d is None:
            continue
        v = np.asarray(d, dtype=float)
        n = float(np.linalg.norm(v))
        if n < 1e-9:
            continue
        v = v / n
        for bone in bones:
            out[bone] = v
    return out


def minimal_rotation(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation taking unit vector ``a`` onto unit vector ``b``.

    Zero twist about the bone axis by construction, which is why forearm and palm roll are not
    reproduced. The elbow bend *plane* is still right, because the forearm is aimed on its own
    rather than inherited from the upper arm.
    """
    c = float(np.clip(np.dot(a, b), -1.0, 1.0))
    axis = np.cross(a, b)
    n = float(np.linalg.norm(axis))
    if n < 1e-8:
        if c > 0.0:
            return np.eye(3)
        # Antiparallel: any perpendicular axis is a valid 180 degree turn; pick a stable one.
        fallback = np.array([1.0, 0.0, 0.0])
        if abs(a[0]) > 0.9:
            fallback = np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, fallback)
        n = float(np.linalg.norm(axis))
    axis = axis / n
    angle = math.acos(c)
    k = np.array(
        [
            [0.0, -axis[2], axis[1]],
            [axis[2], 0.0, -axis[0]],
            [-axis[1], axis[0], 0.0],
        ]
    )
    return np.eye(3) + math.sin(angle) * k + (1.0 - math.cos(angle)) * (k @ k)


def matrix_to_quaternion(m: np.ndarray) -> tuple[float, float, float, float]:
    """Rotation matrix -> ``(w, x, y, z)``, Shepperd's method, so no branch loses precision."""
    t = float(m[0, 0] + m[1, 1] + m[2, 2])
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z])
    q = q / float(np.linalg.norm(q))
    if q[0] < 0.0:  # canonical sign, so equal rotations compare equal
        q = -q
    return (float(q[0]), float(q[1]), float(q[2]), float(q[3]))


def solve_aim(
    bones: list[dict],
    targets: dict[str, np.ndarray],
) -> dict[str, tuple[float, float, float, float]]:
    """Top-down aim solve over a rest-pose bone list.

    ``bones`` is ordered parents-before-children, each entry ``{"name", "parent", "matrix_local"}``
    with ``matrix_local`` a 4x4 rest matrix in armature space (Blender's ``Bone.matrix_local``).
    Returns one basis quaternion per bone; bones with no target get identity but still propagate
    their parent's solved rotation, which is what keeps a chain consistent.
    """
    parent_world: dict[str, np.ndarray] = {}
    rest: dict[str, np.ndarray] = {
        b["name"]: np.asarray(b["matrix_local"], dtype=float) for b in bones
    }
    out: dict[str, tuple[float, float, float, float]] = {}
    for entry in bones:
        name = entry["name"]
        parent = entry.get("parent")
        m_rest = rest[name]
        if parent is None:
            base = m_rest
        else:
            offset = np.linalg.inv(rest[parent]) @ m_rest
            base = parent_world[parent] @ offset
        r0 = base[:3, :3]
        basis = np.eye(4)
        quat = (1.0, 0.0, 0.0, 0.0)
        target = targets.get(name)
        if target is not None:
            # A Blender bone points along its own local +Y.
            y0 = r0 @ np.array([0.0, 1.0, 0.0])
            n = float(np.linalg.norm(y0))
            if n > 1e-9:
                y0 = y0 / n
                rm = minimal_rotation(y0, target)
                rb = np.linalg.inv(r0) @ rm @ r0
                basis[:3, :3] = rb
                quat = matrix_to_quaternion(rb)
        out[name] = quat
        parent_world[name] = base @ basis
    return out


def aim_residual_deg(
    bones: list[dict],
    targets: dict[str, np.ndarray],
    solved: dict[str, tuple[float, float, float, float]],
) -> dict[str, float]:
    """Angle between each aimed bone's posed direction and its target. The correctness check."""
    posed = _posed_directions(bones, solved)
    out: dict[str, float] = {}
    for name, target in targets.items():
        d = posed.get(name)
        if d is None:
            continue
        c = float(np.clip(np.dot(d, target), -1.0, 1.0))
        out[name] = round(math.degrees(math.acos(c)), 4)
    return out


def _posed_directions(
    bones: list[dict], solved: dict[str, tuple[float, float, float, float]]
) -> dict[str, np.ndarray]:
    """World +Y direction of every bone after applying ``solved``."""
    rest = {b["name"]: np.asarray(b["matrix_local"], dtype=float) for b in bones}
    parent_world: dict[str, np.ndarray] = {}
    out: dict[str, np.ndarray] = {}
    for entry in bones:
        name = entry["name"]
        parent = entry.get("parent")
        m_rest = rest[name]
        base = (
            m_rest
            if parent is None
            else parent_world[parent] @ (np.linalg.inv(rest[parent]) @ m_rest)
        )
        w, x, y, z = solved.get(name, (1.0, 0.0, 0.0, 0.0))
        rb = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        basis = np.eye(4)
        basis[:3, :3] = rb
        world = base @ basis
        parent_world[name] = world
        v = world[:3, :3] @ np.array([0.0, 1.0, 0.0])
        n = float(np.linalg.norm(v))
        out[name] = v / n if n > 1e-9 else v
    return out
