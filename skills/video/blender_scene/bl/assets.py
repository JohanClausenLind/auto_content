"""Scene assembly: environment, props (primitives / glb / blend) and characters (library .blend)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import bmesh  # type: ignore[import-not-found]
import bpy  # type: ignore[import-not-found]


@dataclass
class Entity:
    """One spec-level thing in the scene: a character, a prop or an environment piece."""

    entity_id: str
    kind: str  # "character" | "prop" | "environment"
    objects: list[Any] = field(default_factory=list)  # mesh objects that render
    rig: Any = None
    body: Any = None
    kp_proxy: Any = None
    seg: bool = True
    seg_id: int = 0
    asset: str | None = None
    rig_type: str | None = None
    kp_anchors: dict[str, list[int]] = field(default_factory=dict)
    pose: dict[str, Any] | None = None


def _material(name: str, rgb: tuple[float, float, float]) -> Any:
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = (*rgb, 1.0)  # Workbench MATERIAL colour
    mat.roughness = 0.8
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:  # Cycles fallback colour
        bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.8
    return mat


def _link(obj: Any, scene: Any) -> None:
    scene.collection.objects.link(obj)


def _apply_transform(obj: Any, tf: dict[str, Any]) -> None:
    pos = tf.get("position", (0.0, 0.0, 0.0))
    obj.location = (float(pos[0]), float(pos[1]), float(pos[2]))
    obj.rotation_mode = "XYZ"
    obj.rotation_euler = (0.0, 0.0, math.radians(float(tf.get("yaw_deg", 0.0))))
    s = float(tf.get("scale", 1.0))
    obj.scale = (s, s, s)


def _mesh_object(name: str, build: Any, scene: Any) -> Any:
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    build(bm)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    _link(obj, scene)
    return obj


def primitive(scene: Any, name: str, shape: str, size: tuple[float, float, float]) -> Any:
    sx, sy, sz = (float(v) for v in size)

    def build(bm: Any) -> None:
        if shape == "cube":
            bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
        elif shape == "plane":
            bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
            bmesh.ops.scale(bm, vec=(sx, sy, 1.0), verts=bm.verts)
        elif shape == "sphere":
            bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.5)
            bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
        elif shape == "cylinder":
            bmesh.ops.create_cone(
                bm, cap_ends=True, segments=32, radius1=0.5, radius2=0.5, depth=1.0
            )
            bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
        else:
            raise ValueError(f"unknown primitive shape {shape}")
        for f in bm.faces:
            f.smooth = shape in ("sphere", "cylinder")

    return _mesh_object(name, build, scene)


def build_environment(scene: Any, spec: dict[str, Any]) -> list[Entity]:
    env = spec["environment"]
    entities: list[Entity] = []
    ground = env.get("ground") or {}
    if ground.get("enabled", True):
        size = float(ground.get("size", 40.0))
        obj = primitive(scene, "env:ground", "plane", (size, size, 1.0))
        obj.data.materials.append(
            _material("env:ground", tuple(ground.get("color", (0.35, 0.35, 0.35))))
        )
        entities.append(Entity("ground", "environment", [obj], seg=bool(ground.get("seg", False))))
    walls = env.get("walls")
    if walls:
        sx, sy, sz = (float(v) for v in walls.get("size", (8.0, 8.0, 3.0)))
        obj = primitive(scene, "env:walls", "cube", (sx, sy, sz))
        obj.location = (0.0, 0.0, sz / 2.0)
        me = obj.data
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.reverse_faces(bm, faces=bm.faces)  # render the inside of the box
        bm.to_mesh(me)
        bm.free()
        obj.data.materials.append(
            _material("env:walls", tuple(walls.get("color", (0.6, 0.6, 0.6))))
        )
        entities.append(Entity("walls", "environment", [obj], seg=False))
    return entities


def _import_glb(scene: Any, path: Path) -> list[Any]:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    for o in new:
        if not o.users_collection:
            _link(o, scene)
    return new


def _append_collection(scene: Any, path: Path, collection: str) -> list[Any]:
    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        if collection not in src.collections:
            raise FileNotFoundError(f"{path} has no collection {collection!r}")
        dst.collections = [collection]
    coll = dst.collections[0]
    scene.collection.children.link(coll)
    return list(coll.all_objects)


def _parent_under_root(scene: Any, name: str, objs: list[Any], tf: dict[str, Any]) -> None:
    root = bpy.data.objects.new(name, None)
    _link(root, scene)
    for o in objs:
        if o.parent is None:
            o.parent = root
    _apply_transform(root, tf)


def build_props(scene: Any, spec: dict[str, Any]) -> list[Entity]:
    entities: list[Entity] = []
    for p in spec["props"]:
        pid = p["id"]
        src = p.get("source") or {}
        kind = src.get("kind", "primitive")
        if kind == "primitive":
            obj = primitive(
                scene, f"{pid}:mesh", src.get("shape", "cube"), tuple(src.get("size", (1, 1, 1)))
            )
            _apply_transform(obj, p.get("transform") or {})
            objs = [obj]
        elif kind == "glb":
            objs = _import_glb(scene, Path(src["path"]).expanduser())
            _parent_under_root(scene, f"{pid}:root", objs, p.get("transform") or {})
        elif kind == "blend":
            objs = _append_collection(scene, Path(src["path"]).expanduser(), src["collection"])
            _parent_under_root(scene, f"{pid}:root", objs, p.get("transform") or {})
        else:
            raise ValueError(f"unknown prop source kind {kind}")
        mat = _material(f"prop:{pid}", tuple(p.get("color", (0.5, 0.5, 0.5))))
        meshes = [o for o in objs if o.type == "MESH"]
        for i, o in enumerate(meshes):
            if kind == "primitive" or not o.data.materials:
                o.data.materials.append(mat)
            if kind != "primitive":
                o.name = f"{pid}:mesh.{i}"
        entities.append(Entity(pid, "prop", meshes, seg=bool(p.get("seg", True))))
    return entities


def append_character(scene: Any, spec_char: dict[str, Any], assets_root: Path) -> Entity:
    """Append ``<assets>/characters/<asset>/<asset>.blend`` collection ``CH_<asset>`` and rename
    its objects ``<id>:<role>`` so two instances of one asset never collide."""
    asset = spec_char["asset"]
    cid = spec_char["id"]
    blend = assets_root / "characters" / asset / f"{asset}.blend"
    if not blend.exists():
        raise FileNotFoundError(f"character asset missing: {blend}")
    objs = _append_collection(scene, blend, f"CH_{asset}")
    ent = Entity(cid, "character", asset=asset)
    for o in objs:
        role = o.get("cf_role")
        if not role:
            role = "rig" if o.type == "ARMATURE" else "body"
        o.name = f"{cid}:{role}"
        if o.type == "ARMATURE":
            ent.rig = o
        elif role == "kp":
            ent.kp_proxy = o
            o.hide_render = True
        elif o.type == "MESH":
            ent.objects.append(o)
            if role == "body" and ent.body is None:
                ent.body = o
    if ent.body is not None:
        raw = ent.body.get("cf_kp_anchors")
        if raw:
            ent.kp_anchors = json.loads(raw) if isinstance(raw, str) else dict(raw)
        ent.rig_type = ent.body.get("cf_rig_type")
    root = ent.rig if ent.rig is not None else ent.body
    if root is not None:
        _apply_transform(root, spec_char.get("transform") or {})
    ent.pose = spec_char.get("pose")
    return ent


def build(scene: Any, spec: dict[str, Any], assets_root: Path) -> list[Entity]:
    entities: list[Entity] = []
    for c in spec["characters"]:
        entities.append(append_character(scene, c, assets_root))
    entities.extend(build_props(scene, spec))
    entities.extend(build_environment(scene, spec))
    return entities
