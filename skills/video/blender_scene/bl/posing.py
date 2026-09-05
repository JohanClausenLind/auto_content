"""Apply cf.pose.v1 / cf.clip.v1 / cf.clip.v2 poses to an armature."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mathutils import Quaternion, Vector  # type: ignore[import-not-found]
from posing import bone_rotations, sample_clip


def load_pose_doc(assets_root: Path, kind: str, name: str) -> dict[str, Any]:
    sub = "poses" if kind == "library" else "clips"
    path = assets_root / sub / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"{kind} pose missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def apply_rotations(
    rig: Any,
    rotations: dict[str, tuple[float, float, float, float]],
    root_location: tuple[float, float, float] | None = None,
) -> None:
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))
        pb.location = Vector((0.0, 0.0, 0.0))
    for name, q in rotations.items():
        pb = rig.pose.bones.get(name)
        if pb is None:
            continue
        pb.rotation_quaternion = Quaternion(q)
    if root_location is not None and rig.pose.bones:
        rig.pose.bones[0].location = Vector(root_location)


def pose_for_frame(
    pose_ref: dict[str, Any] | None, assets_root: Path, frame: int, fps: int
) -> dict[str, tuple[float, float, float, float]] | None:
    if not pose_ref:
        return None
    kind = pose_ref.get("kind", "library")
    if kind == "bones":
        return {
            n: tuple(b.get("rotation_quaternion", (1, 0, 0, 0)))
            for n, b in pose_ref["bones"].items()
        }  # type: ignore[misc]
    if kind == "library":
        return bone_rotations(load_pose_doc(assets_root, "library", pose_ref.get("name", "idle")))
    if kind == "clip":
        doc = load_pose_doc(assets_root, "clip", pose_ref["name"])
        return sample_clip(
            doc,
            frame,
            fps=fps,
            speed=float(pose_ref.get("speed", 1.0)),
            offset_frames=int(pose_ref.get("offset_frames", 0)),
            loop=bool(pose_ref.get("loop", True)),
        )
    if kind == "segments":
        # Solved against the loaded rig by apply_for_frame, not here: a cf.clip.v2 clip stores
        # world directions, and turning those into quaternions needs the armature.
        return None
    raise ValueError(f"unknown pose kind {kind}")


def apply_for_frame(
    rig: Any, pose_ref: dict[str, Any] | None, assets_root: Path, frame: int, fps: int
) -> dict[str, Any] | None:
    """Pose one character for one frame, whichever kind of pose reference it carries.

    ``cf.clip.v2`` ("segments") is the one kind that cannot be reduced to a bone->quaternion dict
    ahead of time, because the solve depends on the rig's own rest orientations. Returning the
    retarget report lets the runner put the numbers in its summary.
    """
    if not pose_ref:
        return None
    if pose_ref.get("kind") == "segments":
        from bl import retarget

        doc = load_pose_doc(assets_root, "clip", pose_ref["name"])
        return retarget.apply_clip_frame(
            rig,
            doc,
            str(pose_ref.get("actor", "a")),
            frame,
            fps=fps,
            speed=float(pose_ref.get("speed", 1.0)),
            offset_frames=int(pose_ref.get("offset_frames", 0)),
            loop=bool(pose_ref.get("loop", False)),
        )
    rotations = pose_for_frame(pose_ref, assets_root, frame, fps)
    if rotations is None:
        return None
    apply_rotations(rig, rotations)
    return {"bones_applied": len(rotations), "kind": pose_ref.get("kind", "library")}
