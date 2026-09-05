"""Dump a character's rest rig to JSON, for retarget tests that must not open Blender.

Run inside Blender:

    blender --background --factory-startup --python mocap/dump_rig.py -- \
        /mnt/fast/models/blender-assets/characters/man_01/man_01.blend out.json

The rest matrices are what the aim solve needs, and they differ per character because MPFB fits the
rig to each mesh. Dumping them means the cross-character correctness test can run in plain pytest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy  # type: ignore[import-not-found]


def dump(blend: str, out: str) -> None:
    bpy.ops.wm.open_mainfile(filepath=blend)
    rigs = {}
    for obj in bpy.data.objects:
        if obj.type != "ARMATURE":
            continue
        bones = [
            {
                "name": b.name,
                "parent": b.parent.name if b.parent else None,
                "length": round(b.length, 6),
                "matrix_local": [[round(v, 8) for v in row] for row in b.matrix_local],
            }
            for b in obj.data.bones
        ]
        rigs[obj.name] = {
            "count": len(bones),
            "rotation_modes": sorted({pb.rotation_mode for pb in obj.pose.bones}),
            "constraints": sum(len(pb.constraints) for pb in obj.pose.bones),
            "bones": bones,
        }
    Path(out).write_text(json.dumps(rigs, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"blend": blend, "rigs": {k: v["count"] for k, v in rigs.items()}}))


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(argv) != 2:
        raise SystemExit("usage: ... --python mocap/dump_rig.py -- <blend> <out.json>")
    dump(argv[0], argv[1])
