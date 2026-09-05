"""Build one character asset from a recipe (runs inside Blender with MPFB2 enabled):

    assets_build/run_in_blender.sh assets_build/build_character.py recipes/man_01.json <assets_root>

Writes <assets_root>/characters/<name>/<name>.blend with collection CH_<name> holding
  <name>:body  the MakeHuman basemesh (Mask modifier hides helpers), custom props
               cf_role="body", cf_rig_type, cf_kp_anchors (OpenPose-18 -> basemesh vertex ids)
  <name>:rig   the MPFB default rig (163 bones), rotation mode QUATERNION
  <name>:kp    keypoint proxy: same mesh, armature modifier, NO mask, hide_render
and <name>.asset.json with provenance (recipe hash, MPFB + Blender versions, height, digests).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy  # type: ignore[import-not-found]
from _mpfb import COCO_TO_OPENPOSE18, Mpfb, args_after_dashes
from mathutils import Vector, kdtree  # type: ignore[import-not-found]

ANCHOR_NEIGHBOURS = 8


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluated_world_coords(body) -> list:
    """Vertex positions with shape keys (macro targets) and modifiers applied — ``data.vertices``
    alone is the undeformed base mesh and would put every recipe at the same height."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        mw = body.matrix_world
        return [mw @ v.co for v in me.vertices]
    finally:
        ev.to_mesh_clear()


def bake_anchors(m: Mpfb, body, rig) -> dict[str, list[int]]:
    """OpenPose-18 joint -> basemesh vertex indices, resolved in the rest pose on the evaluated
    (shape-keyed) mesh. Vertex/mean entries use MPFB's indices directly; bone head/tail entries
    take the nearest vertices to the rest-pose bone end, which lands on MakeHuman's joint helper
    cubes (so the anchor follows the joint)."""
    coords = evaluated_world_coords(body)
    kd = kdtree.KDTree(len(coords))
    for i, co in enumerate(coords):
        kd.insert(co, i)
    kd.balance()
    anchors: dict[str, list[int]] = {}
    for entry in m.openpose.COCO:
        ours = COCO_TO_OPENPOSE18.get(entry["name"])
        if ours is None:
            continue
        kind, data = entry["type"], entry["data"]
        if kind == "vertex":
            anchors[ours] = [int(data)]
        elif kind == "mean":
            anchors[ours] = [int(i) for i in data]
        else:  # head / tail of a default-rig bone
            loc = m.RigService.get_world_space_location_of_pose_bone(str(data), rig)[kind]
            anchors[ours] = sorted(
                int(idx) for (_co, idx, _d) in kd.find_n(Vector(loc), ANCHOR_NEIGHBOURS)
            )
    missing = set(COCO_TO_OPENPOSE18.values()) - set(anchors)
    if missing:
        raise RuntimeError(f"anchors missing for {sorted(missing)}")
    return anchors


def main() -> int:
    argv = args_after_dashes()
    if len(argv) < 2:
        raise SystemExit("usage: run_in_blender.sh build_character.py <recipe.json> <assets_root>")
    recipe_path, assets_root = Path(argv[0]), Path(argv[1])
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    name = recipe["name"]
    rig_name = recipe.get("rig", "default")

    m = Mpfb()
    scene = bpy.context.scene
    body = m.create_human(recipe.get("macro"))
    rig = m.add_rig(body, rig_name)
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"

    anchors = bake_anchors(m, body, rig)

    # Clay material for the rough RGB pass (MPFB skins are optional and not needed for structure).
    skin = tuple(recipe.get("skin_color", (0.8, 0.62, 0.52)))
    mat = bpy.data.materials.new(f"{name}:skin")
    mat.diffuse_color = (*skin, 1.0)
    mat.roughness = 0.7
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*skin, 1.0)
    body.data.materials.clear()
    body.data.materials.append(mat)

    body.name = f"{name}:body"
    rig.name = rig.data.name = f"{name}:rig"
    body["cf_role"] = "body"
    body["cf_rig_type"] = f"mpfb.{rig_name}"
    body["cf_kp_anchors"] = json.dumps(anchors, sort_keys=True)
    rig["cf_role"] = "rig"

    kp = body.copy()  # shares the mesh datablock, keeps parent + modifiers
    kp.name = f"{name}:kp"
    for mod in list(kp.modifiers):
        if mod.type == "MASK":
            kp.modifiers.remove(mod)
    kp.hide_render = True
    kp["cf_role"] = "kp"
    scene.collection.objects.link(kp)

    coll = bpy.data.collections.new(f"CH_{name}")
    scene.collection.children.link(coll)
    for obj in (body, rig, kp):
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        coll.objects.link(obj)

    zs = [co.z for co in evaluated_world_coords(body)]
    height_m = round(max(zs) - min(zs), 4)

    out_dir = assets_root / "characters" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    blend = out_dir / f"{name}.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), compress=True)
    asset = {
        "schema": "cf.character_asset.v1",
        "name": name,
        "recipe": recipe,
        "recipe_sha256": _sha(recipe_path),
        "mpfb_version": m.version,
        "blender_version": bpy.app.version_string,
        "rig_type": f"mpfb.{rig_name}",
        "rig_bones": len(rig.pose.bones),
        "vertex_count": len(body.data.vertices),
        "height_m": height_m,
        "kp_joints": sorted(anchors),
        "collection": f"CH_{name}",
        "objects": {"body": body.name, "rig": rig.name, "kp": kp.name},
        "blend_sha256": _sha(blend),
        "license": "MakeHuman basemesh/targets CC0; built with MPFB2 (GPL-3.0-or-later)",
    }
    (out_dir / f"{name}.asset.json").write_text(json.dumps(asset, indent=1, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "blend": str(blend),
                "height_m": height_m,
                "vertices": len(body.data.vertices),
                "bones": len(rig.pose.bones),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
