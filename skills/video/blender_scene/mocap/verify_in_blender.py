"""Apply a real clip to a real character inside Blender and measure whether it landed.

    blender --background --factory-startup --python mocap/verify_in_blender.py -- \
        <character.blend> <clip.json> <actor_id> <frame> [out.json]

Reports Blender's own view of each aimed bone's world direction against the clip's target, so the
number is not the solver grading its own homework: if the maths and Blender's pose evaluation
disagree, this is where it shows.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bpy  # type: ignore[import-not-found]


def main(blend: str, clip_path: str, actor: str, frame: int, out: str | None) -> int:
    from bl.retarget import apply_clip_frame, world_bone_direction

    from mocap.sample import sample_actor
    from mocap.segments import bone_target_map

    bpy.ops.wm.open_mainfile(filepath=blend)
    rig = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
    if rig is None:
        print(json.dumps({"error": f"no armature in {blend}"}))
        return 2

    clip = json.loads(Path(clip_path).read_text())
    fps = int(clip.get("fps", 24))
    report = apply_clip_frame(rig, clip, actor, frame, fps=fps)
    bpy.context.view_layer.update()

    sampled = sample_actor(clip, actor, frame, fps=fps)
    targets = bone_target_map({k: list(v) for k, v in sampled["directions"].items()})
    residuals: dict[str, float] = {}
    for bone, target in targets.items():
        d = world_bone_direction(rig, bone)
        if d is None:
            continue
        dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(d, target, strict=True))))
        residuals[bone] = round(math.degrees(math.acos(dot)), 4)

    values = sorted(residuals.values())
    summary = {
        "blend": Path(blend).name,
        "clip": clip.get("name"),
        "actor": actor,
        "frame": frame,
        **report,
        "residual_deg": {
            "max": values[-1] if values else None,
            "median": values[len(values) // 2] if values else None,
            "count": len(values),
        },
        "worst": sorted(residuals.items(), key=lambda kv: -kv[1])[:5],
        "root_location_world": [round(v, 5) for v in sampled["root_translation"]],
    }
    print(json.dumps(summary))
    if out:
        Path(out).write_text(
            json.dumps({"summary": summary, "residuals": residuals}, indent=1) + "\n"
        )
    return 0 if values and values[-1] < 0.5 else 3


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(argv) < 4:
        raise SystemExit("usage: ... -- <blend> <clip.json> <actor> <frame> [out.json]")
    raise SystemExit(
        main(argv[0], argv[1], argv[2], int(argv[3]), argv[4] if len(argv) > 4 else None)
    )
