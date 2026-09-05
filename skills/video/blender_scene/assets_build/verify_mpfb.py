"""Prove MPFB is usable from this Blender: enable it, create a human, add the default rig."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy  # type: ignore[import-not-found]
from _mpfb import Mpfb

m = Mpfb()
body = m.create_human()
rig = m.add_rig(body, "default")
verts = len(body.data.vertices)
if verts < 13000 or len(rig.pose.bones) < 100:
    raise SystemExit(f"unexpected MPFB output: {verts} vertices, {len(rig.pose.bones)} bones")
print(
    json.dumps(
        {
            "ok": True,
            "blender": bpy.app.version_string,
            "mpfb": m.version,
            "vertices": verts,
            "bones": len(rig.pose.bones),
            "modifiers": [mod.type for mod in body.modifiers],
        }
    )
)
