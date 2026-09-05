"""ShotSpec loading + structural validation (pure Python; importable from Blender's interpreter).

The Pydantic contract in ``python/content_factory/schemas/shots.py`` is the source of truth; this
module re-checks the shape the skill depends on so a hand-written spec fails loudly and early.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SKILL_VERSION = "0.1.0"
SCHEMA_ID = "cf.shot_spec.v1"

PASS_KINDS = (
    "rough_rgb",
    "depth",
    "depth16",
    "depth_exr",
    "normals",
    "segmentation",
    "canny",
    "pose_skeleton",
    "layout_boxes",
)
ENGINES = ("workbench", "eevee", "cycles_cpu")
MAX_SEG_ID = 254


class SpecError(ValueError):
    """The spec is structurally unusable for rendering."""


def canonical_dumps(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def spec_sha256(spec: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_dumps(spec).encode("utf-8")).hexdigest()


def _need(d: dict[str, Any], key: str, kind: type | tuple[type, ...], where: str) -> Any:
    if key not in d:
        raise SpecError(f"{where}: missing '{key}'")
    v = d[key]
    if not isinstance(v, kind) or (kind is int and isinstance(v, bool)):
        raise SpecError(f"{where}: '{key}' must be {kind}")
    return v


def _vec3(v: Any, where: str) -> tuple[float, float, float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 3):
        raise SpecError(f"{where}: expected a 3-vector")
    return (float(v[0]), float(v[1]), float(v[2]))


def validate(spec: dict[str, Any]) -> dict[str, Any]:
    """Return ``spec`` with defaults filled in, or raise ``SpecError``."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    out = dict(spec)
    shot_id = _need(out, "shot_id", str, "spec")
    frame_count = _need(out, "frame_count", int, "spec")
    if not 1 <= frame_count <= 600:
        raise SpecError("frame_count must be in 1..600")
    width = _need(out, "width", int, "spec")
    height = _need(out, "height", int, "spec")
    if width % 32 or height % 32 or width < 64 or height < 64:
        raise SpecError("width/height must be multiples of 32 and >= 64")
    out.setdefault("fps", 24)
    out.setdefault("seed", 0)
    out.setdefault("characters", [])
    out.setdefault("props", [])
    out.setdefault("environment", {})
    out.setdefault("lighting", {})
    out.setdefault("render", {})
    out.setdefault("anchor_frames", [0])

    cam = _need(out, "camera", dict, "spec")
    keyframes = _need(cam, "keyframes", list, "camera")
    if not keyframes:
        raise SpecError("camera.keyframes must not be empty")
    last = -1
    for i, kf in enumerate(keyframes):
        where = f"camera.keyframes[{i}]"
        fi = _need(kf, "frame_index", int, where)
        if i == 0 and fi != 0:
            raise SpecError("camera keyframes must start at frame 0")
        if fi <= last:
            raise SpecError("camera keyframes must be strictly increasing")
        if fi >= frame_count:
            raise SpecError(f"{where}: frame_index {fi} >= frame_count")
        last = fi
        _vec3(_need(kf, "position", (list, tuple), where), where)
        has_look = kf.get("look_at") is not None
        has_euler = kf.get("rotation_euler_deg") is not None
        if has_look == has_euler:
            raise SpecError(f"{where}: exactly one of look_at / rotation_euler_deg")
        kf.setdefault("lens_mm", 35.0)
        kf.setdefault("focus_distance", 5.0)
        kf.setdefault("easing_to_next", "ease_in_out")
    cam.setdefault("sensor_width_mm", 36.0)
    cam.setdefault("clip_start", 0.05)
    cam.setdefault("clip_end", 100.0)

    ids: list[str] = []
    seg_ids: list[int] = []
    for c in out["characters"]:
        cid = _need(c, "id", str, "characters[]")
        _need(c, "asset", str, f"character {cid}")
        ids.append(cid)
        if c.get("seg_id") is not None:
            seg_ids.append(int(c["seg_id"]))
        c.setdefault("transform", {})
        c.setdefault("pose", {"kind": "library", "name": "idle"})
    for p in out["props"]:
        pid = _need(p, "id", str, "props[]")
        ids.append(pid)
        p.setdefault("source", {"kind": "primitive", "shape": "cube", "size": [1.0, 1.0, 1.0]})
        p.setdefault("transform", {})
        p.setdefault("color", [0.5, 0.5, 0.5])
        p.setdefault("seg", True)
        if p.get("seg") and p.get("seg_id") is not None:
            seg_ids.append(int(p["seg_id"]))
    if len(set(ids)) != len(ids):
        raise SpecError("character and prop ids must be unique")
    if len(set(seg_ids)) != len(seg_ids) or any(not 1 <= s <= MAX_SEG_ID for s in seg_ids):
        raise SpecError(f"explicit seg_id values must be unique and in 1..{MAX_SEG_ID}")

    render = out["render"]
    render.setdefault("engine", "workbench")
    if render["engine"] not in ENGINES:
        raise SpecError(f"render.engine must be one of {ENGINES}")
    render.setdefault(
        "passes",
        [
            "rough_rgb",
            "depth",
            "depth_exr",
            "normals",
            "segmentation",
            "pose_skeleton",
            "layout_boxes",
        ],
    )
    unknown = [p for p in render["passes"] if p not in PASS_KINDS]
    if unknown:
        raise SpecError(f"unknown render passes {unknown}")
    render.setdefault("depth_range", "auto")
    render.setdefault("canny", {"low": 100, "high": 200})
    frames = render.get("frames")
    anchors = list(out["anchor_frames"])
    if (
        not anchors
        or anchors[0] != 0
        or anchors != sorted(set(anchors))
        or anchors[-1] >= frame_count
    ):
        raise SpecError("anchor_frames must start with 0, increase strictly and stay < frame_count")
    if frames is not None:
        if list(frames) != sorted(set(frames)) or frames[-1] >= frame_count:
            raise SpecError("render.frames must be strictly increasing and < frame_count")
        if not set(anchors) <= set(frames):
            raise SpecError("render.frames must include every anchor frame")
    out["shot_id"] = shot_id
    return out


def load_spec(path: Path) -> dict[str, Any]:
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def frames_to_render(spec: dict[str, Any]) -> list[int]:
    frames = spec["render"].get("frames")
    return list(frames) if frames is not None else list(range(spec["frame_count"]))


def seg_assignments(spec: dict[str, Any]) -> dict[str, int]:
    """Deterministic segmentation ids: characters first, then ``seg: true`` props, explicit
    ``seg_id`` values honoured, the rest filled with the next free id. 0 is background."""
    taken = {int(c["seg_id"]) for c in spec["characters"] if c.get("seg_id") is not None}
    taken |= {
        int(p["seg_id"]) for p in spec["props"] if p.get("seg") and p.get("seg_id") is not None
    }
    assigned: dict[str, int] = {}
    next_id = 1

    def take(entity_id: str, explicit: int | None) -> None:
        nonlocal next_id
        if explicit is not None:
            assigned[entity_id] = explicit
            return
        while next_id in taken:
            next_id += 1
        if next_id > MAX_SEG_ID:
            raise SpecError("too many segmented entities")
        assigned[entity_id] = next_id
        taken.add(next_id)
        next_id += 1

    for c in spec["characters"]:
        take(c["id"], c.get("seg_id"))
    for p in spec["props"]:
        if p.get("seg", True):
            take(p["id"], p.get("seg_id"))
    return assigned
