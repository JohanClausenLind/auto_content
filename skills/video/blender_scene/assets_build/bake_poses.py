"""Bake pose documents for the default MPFB rig (runs inside Blender with MPFB2 enabled):

    assets_build/run_in_blender.sh assets_build/bake_poses.py <assets_root>

Writes <assets_root>/poses/{idle,stand_relaxed,t_pose}.json (cf.pose.v1: bone -> quaternion) and
<assets_root>/clips/walk_cycle.json (cf.clip.v1: per-frame bone quaternions) from MPFB's own data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy  # type: ignore[import-not-found]
from _mpfb import Mpfb, args_after_dashes


def _quats(rig) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for pb in rig.pose.bones:
        q = pb.matrix_basis.to_quaternion()
        if abs(q.w - 1.0) > 1e-6 or any(abs(c) > 1e-6 for c in (q.x, q.y, q.z)):
            out[pb.name] = [round(q.w, 6), round(q.x, 6), round(q.y, 6), round(q.z, 6)]
    return out


def _pose_doc(rig_type: str, bones: dict[str, list[float]], source: str) -> dict:
    return {
        "schema": "cf.pose.v1",
        "rig": rig_type,
        "bones": {k: {"rotation_quaternion": v} for k, v in sorted(bones.items())},
        "source": source,
    }


def _reset(rig) -> None:
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.location = (0.0, 0.0, 0.0)


def main() -> int:
    argv = args_after_dashes()
    if not argv:
        raise SystemExit("usage: run_in_blender.sh bake_poses.py <assets_root>")
    assets_root = Path(argv[0])
    poses_dir, clips_dir = assets_root / "poses", assets_root / "clips"
    poses_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    m = Mpfb()
    body = m.create_human()
    rig = m.add_rig(body, "default")
    rig_type = "mpfb.default"
    written: list[str] = []

    def write(path: Path, doc: dict) -> None:
        path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        written.append(str(path))

    # idle / stand_relaxed: MakeHuman's rest pose (relaxed A-stance) — identity rotations.
    write(poses_dir / "idle.json", _pose_doc(rig_type, {}, "mpfb rest pose"))
    write(poses_dir / "stand_relaxed.json", _pose_doc(rig_type, {}, "mpfb rest pose"))

    # t_pose from MPFB's default_fk pose set.
    tpose = json.loads(m.data_path("poses", "default_fk", "t-pose.json").read_text())
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    m.RigService.set_pose_from_dict(rig, tpose)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()
    write(
        poses_dir / "t_pose.json",
        _pose_doc(rig_type, _quats(rig), "mpfb data/poses/default_fk/t-pose.json"),
    )
    _reset(rig)

    # walk_cycle from MPFB's walkcycle keyframes, sampled per frame.
    walk_path = m.data_path("walkcycles", "crappy_experimental_female.json")
    walk = json.loads(walk_path.read_text())
    # The clip was authored on a rig with IK helper bones; keep only bones this rig has (the FK
    # channels) and record what was dropped, so the clip is honest about being partial.
    present = {pb.name for pb in rig.pose.bones}
    dropped = sorted(b for b in walk["animation_data"] if b not in present)
    walk["animation_data"] = {b: v for b, v in walk["animation_data"].items() if b in present}
    bpy.context.view_layer.objects.active = rig
    m.AnimationService.set_key_frames_from_dict(rig, walk)  # expects the full document
    frames: list[dict] = []
    if rig.animation_data is not None and rig.animation_data.action is not None:
        last = m.AnimationService.get_max_keyframe(rig) or 0
        for f in range(int(last) + 1):
            bpy.context.scene.frame_set(f)
            bpy.context.view_layer.update()
            frames.append(
                {"bones": {k: {"rotation_quaternion": v} for k, v in sorted(_quats(rig).items())}}
            )
    if len(frames) >= 2:
        write(
            clips_dir / "walk_cycle.json",
            {
                "schema": "cf.clip.v1",
                "rig": rig_type,
                "fps": 24,
                "loop": True,
                "frames": frames,
                "source": "mpfb data/walkcycles/crappy_experimental_female.json",
                "metadata": walk.get("metadata", {}),
                "dropped_bones": dropped,
            },
        )
    else:
        # MPFB's clip targets IK helper bones the plain default rig lacks; without them no keyframes
        # land. Recorded, not fatal: the pipeline needs library poses first, clips are optional.
        print(
            json.dumps(
                {"warning": "walk_cycle skipped: clip needs IK bones", "dropped_bones": dropped}
            )
        )
    print(json.dumps({"ok": True, "written": written, "walk_frames": len(frames)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
