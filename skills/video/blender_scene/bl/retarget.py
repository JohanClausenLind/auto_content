"""Apply a ``cf.clip.v2`` motion clip to an armature, inside Blender."""

from __future__ import annotations

from typing import Any

from mathutils import Matrix, Quaternion, Vector  # type: ignore[import-not-found]
from mocap.sample import sample_actor
from mocap.segments import bone_target_map, solve_aim


def rest_bones(rig: Any) -> list[dict[str, Any]]:
    """Rest matrices in armature space, parents before children."""
    by_name = {b.name: b for b in rig.data.bones}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(bone: Any) -> None:
        if bone.name in seen:
            return
        if bone.parent is not None:
            visit(bone.parent)
        seen.add(bone.name)
        out.append(
            {
                "name": bone.name,
                "parent": bone.parent.name if bone.parent else None,
                "matrix_local": [[float(v) for v in row] for row in bone.matrix_local],
            }
        )

    for bone in by_name.values():
        visit(bone)
    return out


def apply_segments(
    rig: Any,
    directions: dict[str, tuple[float, float, float]],
    root_translation: tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    """Aim every mapped bone along its captured direction."""
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))
        pb.location = Vector((0.0, 0.0, 0.0))

    bones = rest_bones(rig)
    targets = bone_target_map({k: list(v) for k, v in directions.items()})
    solved = solve_aim(bones, targets)

    applied = 0
    for name, quat in solved.items():
        pb = rig.pose.bones.get(name)
        if pb is None or quat == (1.0, 0.0, 0.0, 0.0):
            continue
        pb.rotation_quaternion = Quaternion(quat)
        applied += 1

    if root_translation is not None and rig.pose.bones:
        root = rig.pose.bones[0]
        rest = rig.data.bones[root.name].matrix_local.to_3x3()
        local = rest.inverted() @ Vector(root_translation)
        root.location = local

    return {"bones_targeted": len(targets), "bones_applied": applied, "rig_bones": len(bones)}


def apply_clip_frame(
    rig: Any,
    clip: dict[str, Any],
    actor_id: str,
    frame: int,
    *,
    fps: int,
    speed: float = 1.0,
    offset_frames: int = 0,
    loop: bool = False,
) -> dict[str, Any]:
    """Sample the clip for one actor at a render frame and apply it."""
    s = sample_actor(
        clip, actor_id, frame, fps=fps, speed=speed, offset_frames=offset_frames, loop=loop
    )
    report = apply_segments(rig, s["directions"], s["root_offset"])
    report |= {"clip": clip.get("name"), "actor": actor_id, "clip_frames": s["clip_frames"]}
    return report


def world_bone_direction(rig: Any, bone: str) -> tuple[float, float, float] | None:
    """Posed world direction of a bone, for verifying a retarget from inside Blender."""
    pb = rig.pose.bones.get(bone)
    if pb is None:
        return None
    m: Matrix = rig.matrix_world @ pb.matrix
    v = (m.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
    return (float(v.x), float(v.y), float(v.z))
