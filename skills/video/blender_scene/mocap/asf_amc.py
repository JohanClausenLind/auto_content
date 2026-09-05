"""Read CMU ASF skeletons and AMC motion, and run forward kinematics on them.

Pure Python and numpy, no ``bpy``: the point is that the format and the kinematics can be tested in
the normal pytest run rather than only inside Blender. The retarget onto a Blender rig lives in
``bl/retarget.py`` and consumes what this module produces.

The kinematics convention, which is the part that is easy to get wrong:

    M(bone)       = C @ Rxyz(theta) @ C.T          C = Rz(az) @ Ry(ay) @ Rx(ax) from ``axis``
    R_global(b)   = R_global(parent) @ M(b)
    head(b)       = tail(parent)
    tail(b)       = head(b) + R_global(b) @ (direction * length * SCALE)

``theta`` is filled from the AMC value list **in the order the bone's own ``dof`` line gives**, and
is zero on the axes that bone does not have. A parser that assumes three angles per bone
desynchronises the whole frame: root carries six values, ``rclavicle`` two, ``rradius`` one.

``SCALE`` converts ASF length units to metres. The ASF declares ``length 0.45``, meaning 0.45
inches per unit, so a unit is ``(1 / 0.45) * 0.0254`` m. Two independent checks say this is right:
femur and tibia come out at 0.42 m and 0.43 m for an adult subject, and over the 17.4 s of trial
18_08 the minimum ankle height holds at 0.0879 m with a standard deviation of 2.9 mm. A wrong
convention drifts or explodes instead of standing still on the floor.

CMU is Y-up. Blender is Z-up. This module stays in CMU's frame and ``to_blender`` does the one
conversion, so nothing downstream has to guess which frame it is holding.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# (1 / 0.45) inches per ASF length unit, in metres.
SCALE = (1.0 / 0.45) * 0.0254

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def rot_xyz(degrees: tuple[float, float, float] | list[float]) -> np.ndarray:
    """Rotation for CMU's ``XYZ`` axis order: apply X, then Y, then Z, so ``Rz @ Ry @ Rx``."""
    ax, ay, az = (math.radians(float(d)) for d in degrees)
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return rz @ ry @ rx


@dataclass(frozen=True)
class Bone:
    """One ASF ``:bonedata`` block, plus the parent the ``:hierarchy`` block gives it."""

    name: str
    direction: np.ndarray
    """Unit vector, in the skeleton's rest frame, from head to tail."""
    length: float
    """In ASF units. Multiply by :data:`SCALE` for metres."""
    axis: tuple[float, float, float]
    dof: tuple[str, ...]
    """Subset of ("rx", "ry", "rz"), in the order the AMC lists this bone's values."""
    parent: str | None = None
    limits: tuple[tuple[float, float], ...] = ()

    @property
    def c(self) -> np.ndarray:
        return rot_xyz(self.axis)

    def offset(self) -> np.ndarray:
        """Rest-frame head-to-tail vector in metres."""
        return self.direction * self.length * SCALE


@dataclass(frozen=True)
class Skeleton:
    name: str
    bones: dict[str, Bone]
    length_unit: float
    """The ASF ``:units length`` value, kept so a non-0.45 file is visible rather than silent."""
    angle_unit: str = "deg"
    root_order: tuple[str, ...] = ("TX", "TY", "TZ", "RX", "RY", "RZ")
    _order: tuple[str, ...] = field(default=(), repr=False)

    def topological(self) -> tuple[str, ...]:
        """Parents before children, so forward kinematics is a single pass."""
        if self._order:
            return self._order
        out: list[str] = []
        seen: set[str] = set()

        def visit(name: str) -> None:
            if name in seen:
                return
            parent = self.bones[name].parent
            if parent is not None:
                visit(parent)
            seen.add(name)
            out.append(name)

        for name in self.bones:
            visit(name)
        object.__setattr__(self, "_order", tuple(out))
        return self._order


class MocapError(ValueError):
    """The file is not the format we think it is. Never raised for a merely unusual skeleton."""


def parse_asf(path: str | Path) -> Skeleton:
    text = Path(path).read_text(errors="replace")
    if ":bonedata" not in text or ":hierarchy" not in text:
        raise MocapError(f"{path}: not an ASF file (no :bonedata / :hierarchy)")

    units = text.split(":units")[1].split(":", 1)[0] if ":units" in text else ""
    length_unit = 0.45
    angle_unit = "deg"
    for line in units.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "length":
            length_unit = float(parts[1])
        elif len(parts) >= 2 and parts[0] == "angle":
            angle_unit = parts[1].lower()
    if angle_unit != "deg":
        raise MocapError(f"{path}: angle unit {angle_unit!r} is not supported (expected deg)")

    root_order: tuple[str, ...] = ("TX", "TY", "TZ", "RX", "RY", "RZ")
    if ":root" in text:
        for line in text.split(":root")[1].split(":", 1)[0].splitlines():
            parts = line.split()
            if parts and parts[0] == "order":
                root_order = tuple(p.upper() for p in parts[1:])

    bones: dict[str, Bone] = {
        "root": Bone(
            name="root",
            direction=np.zeros(3),
            length=0.0,
            axis=(0.0, 0.0, 0.0),
            dof=("rx", "ry", "rz"),
            parent=None,
        )
    }

    body = text.split(":bonedata", 1)[1].split(":hierarchy", 1)[0]
    for block in re.findall(r"begin(.*?)\bend\b", body, re.S):
        name: str | None = None
        direction = np.zeros(3)
        length = 0.0
        axis: tuple[float, float, float] = (0.0, 0.0, 0.0)
        dof: tuple[str, ...] = ()
        limits: list[tuple[float, float]] = []
        for raw in block.splitlines():
            parts = raw.split()
            if not parts:
                continue
            key = parts[0]
            if key == "name":
                name = parts[1]
            elif key == "direction":
                direction = np.array([float(v) for v in parts[1:4]])
            elif key == "length":
                length = float(parts[1])
            elif key == "axis":
                axis = (float(parts[1]), float(parts[2]), float(parts[3]))
                if len(parts) > 4 and parts[4].upper() != "XYZ":
                    raise MocapError(f"{path}: bone {name} axis order {parts[4]!r}, expected XYZ")
            elif key == "dof":
                dof = tuple(p.lower() for p in parts[1:])
            elif key in ("limits", "(") or raw.strip().startswith("("):
                for lo, hi in re.findall(r"\(\s*(-?[\d.eE+-]+)\s+(-?[\d.eE+-]+)\s*\)", raw):
                    limits.append((float(lo), float(hi)))
        if name is None:
            raise MocapError(f"{path}: a :bonedata block has no name")
        for axis_name in dof:
            if axis_name not in ("rx", "ry", "rz"):
                raise MocapError(f"{path}: bone {name} has unsupported dof {axis_name!r}")
        bones[name] = Bone(
            name=name,
            direction=direction,
            length=length,
            axis=axis,
            dof=dof,
            limits=tuple(limits),
        )

    parents: dict[str, str] = {}
    for raw in text.split(":hierarchy", 1)[1].splitlines():
        parts = raw.split()
        if len(parts) < 2 or parts[0] in ("begin", "end"):
            continue
        for child in parts[1:]:
            parents[child] = parts[0]
    unknown = set(parents) - set(bones)
    if unknown:
        raise MocapError(f"{path}: :hierarchy names bones with no :bonedata: {sorted(unknown)}")

    bones = {
        n: (b if n == "root" else Bone(**{**b.__dict__, "parent": parents.get(n)}))
        for n, b in bones.items()
    }
    orphans = [n for n, b in bones.items() if n != "root" and b.parent is None]
    if orphans:
        raise MocapError(f"{path}: bones with no parent in :hierarchy: {sorted(orphans)}")

    return Skeleton(
        name=Path(path).stem, bones=bones, length_unit=length_unit, root_order=root_order
    )


def parse_amc(path: str | Path) -> list[dict[str, list[float]]]:
    """One dict per frame: bone name -> its raw values, in the ASF's dof order for that bone."""
    frames: list[dict[str, list[float]]] = []
    current: dict[str, list[float]] | None = None
    for raw in Path(path).read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(":"):
            continue
        if re.fullmatch(r"\d+", line):
            current = {}
            frames.append(current)
            continue
        if current is None:
            raise MocapError(f"{path}: bone values before the first frame number")
        parts = line.split()
        current[parts[0]] = [float(v) for v in parts[1:]]
    if not frames:
        raise MocapError(f"{path}: no frames")
    return frames


@dataclass(frozen=True)
class Posed:
    """A skeleton evaluated at one frame. Positions are metres in CMU's Y-up frame."""

    rotation: dict[str, np.ndarray]
    head: dict[str, np.ndarray]
    tail: dict[str, np.ndarray]

    def direction(self, bone: str) -> np.ndarray:
        """Unit world direction of a bone, head to tail. Zero-length bones return zeros."""
        v = self.tail[bone] - self.head[bone]
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-12 else np.zeros(3)

    def lowest_foot(self, feet: tuple[str, ...] = ("lfoot", "rfoot", "ltoes", "rtoes")) -> float:
        vals = [self.tail[b][1] for b in feet if b in self.tail]
        return min(vals) if vals else 0.0


def fk(skeleton: Skeleton, frame: dict[str, list[float]]) -> Posed:
    """Forward kinematics for one AMC frame. See the module docstring for the convention."""
    rotation: dict[str, np.ndarray] = {}
    head: dict[str, np.ndarray] = {}
    tail: dict[str, np.ndarray] = {}
    for name in skeleton.topological():
        bone = skeleton.bones[name]
        values = frame.get(name, [])
        if name == "root":
            translation = np.zeros(3)
            angles = [0.0, 0.0, 0.0]
            for i, token in enumerate(skeleton.root_order):
                if i >= len(values):
                    break
                if token.startswith("T"):
                    translation[_AXIS_INDEX[token[1].lower()]] = values[i] * SCALE
                else:
                    angles[_AXIS_INDEX[token[1].lower()]] = values[i]
            rotation[name] = rot_xyz(angles)
            head[name] = translation
            tail[name] = translation
            continue
        angles = [0.0, 0.0, 0.0]
        for i, axis_name in enumerate(bone.dof):
            if i < len(values):
                angles[_AXIS_INDEX[axis_name[1]]] = values[i]
        parent = bone.parent
        assert parent is not None  # parse_asf rejects orphans
        m = bone.c @ rot_xyz(angles) @ bone.c.T
        rotation[name] = rotation[parent] @ m
        head[name] = tail[parent]
        tail[name] = head[name] + rotation[name] @ bone.offset()
    return Posed(rotation=rotation, head=head, tail=tail)


def to_blender(v: np.ndarray) -> np.ndarray:
    """CMU Y-up -> Blender Z-up. ``(x, y, z) -> (x, -z, y)``, which is a rotation, not a mirror."""
    return np.array([v[0], -v[2], v[1]])


def trial_paths(root: str | Path, subject: str, trial: str) -> tuple[Path, Path]:
    """``(asf, amc)`` for a CMU subject/trial under an ``all_asfamc`` root."""
    base = Path(root) / "subjects" / subject
    return base / f"{subject}.asf", base / f"{subject}_{trial}.amc"
