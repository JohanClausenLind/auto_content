"""A stand-in for ``uv run … skills/video/blender_scene/render.py``: writes the skill's full
output contract from the ShotSpec alone (Pillow + numpy), so the control plane's Blender path is
testable without Blender. Deterministic: same spec -> same bytes."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

FAKE_BLENDER_VERSION = "5.2.1 LTS (fake)"
OPENPOSE18 = (
    "nose",
    "neck",
    "r_shoulder",
    "r_elbow",
    "r_wrist",
    "l_shoulder",
    "l_elbow",
    "l_wrist",
    "r_hip",
    "r_knee",
    "r_ankle",
    "l_hip",
    "l_knee",
    "l_ankle",
    "r_eye",
    "l_eye",
    "r_ear",
    "l_ear",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon(doc: Any) -> bytes:
    return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_fake_outputs(spec: dict[str, Any], out: Path) -> dict[str, Any]:
    w, h = spec["width"], spec["height"]
    frame_count = spec["frame_count"]
    frames = list(spec.get("render", {}).get("frames") or range(frame_count))
    passes = list(spec.get("render", {}).get("passes") or [])
    chars = [c["id"] for c in spec.get("characters", [])]
    props = [p["id"] for p in spec.get("props", []) if p.get("seg", True)]
    seg: dict[str, int] = {}
    for i, e in enumerate(chars + props):
        seg[e] = i + 1
    seg["ground"] = 0
    manifest = {
        "schema": "cf.blender_scene.manifest.v1",
        "skill_version": "0.1.0",
        "blender_version": FAKE_BLENDER_VERSION,
        "blender_build_hash": "fake",
        "spec_sha256": _sha(_canon(spec)),
        "shot_id": spec["shot_id"],
        "width": w,
        "height": h,
        "fps": spec.get("fps", 24),
        "frame_count": frame_count,
        "frames_rendered": frames,
        "seed": spec.get("seed", 0),
        "passes": sorted(passes),
        "engines": {"data": "cycles_cpu", "rgb": "workbench"},
        "depth": {"near": 1.0, "far": 9.0, "source": "auto:entities"},
        "seg": {"objects": seg, "background": 0},
        "characters": {
            c: {"asset": "fake", "rig_type": "mpfb.default", "kp_joints": 18} for c in chars
        },
    }

    def marker(pass_name: str, frame: int, **fields: Any) -> None:
        doc = {
            "frame_index": frame,
            "pass": pass_name,
            "input_hash": _sha(f"{pass_name}:{frame}".encode()),
            "width": w,
            "height": h,
            **fields,
        }
        _write(out / pass_name / "frames" / f"{frame:04d}.done.json", _canon(doc))

    yy, xx = np.mgrid[0:h, 0:w]
    for f in frames:
        t = f / max(1, frame_count - 1)
        box = (int(w * (0.4 - 0.1 * t)), int(h * 0.3), int(w * (0.6 + 0.1 * t)), int(h * 0.8))
        index = np.zeros((h, w), dtype=np.uint8)
        if chars or props:
            index[box[1] : box[3], box[0] : box[2]] = 1
        for kind in passes:
            if kind == "rough_rgb":
                rgb = np.stack(
                    [
                        (xx * 255 // max(1, w - 1)),
                        (yy * 255 // max(1, h - 1)),
                        np.full_like(xx, 40 + f),
                    ],
                    -1,
                ).astype(np.uint8)
                data = _png(Image.fromarray(rgb, "RGB"))
                _write(out / kind / "frames" / f"{f:04d}.png", data)
                marker(kind, f, png_sha256=_sha(data))
            elif kind in ("depth", "canny"):
                arr = (
                    (255 - xx * 255 // max(1, w - 1)).astype(np.uint8)
                    if kind == "depth"
                    else (index * 255).astype(np.uint8)
                )
                data = _png(Image.fromarray(arr, "L"))
                _write(out / kind / "frames" / f"{f:04d}.png", data)
                marker(kind, f, png_sha256=_sha(data))
            elif kind == "depth16":
                data = _png(Image.fromarray((xx * 65535 // max(1, w - 1)).astype(np.uint16)))
                _write(out / kind / "frames" / f"{f:04d}.png", data)
                marker(kind, f, png_sha256=_sha(data))
            elif kind == "depth_exr":
                data = b"FAKE-EXR" + bytes([f])
                _write(out / kind / "frames" / f"{f:04d}.exr", data)
                marker(kind, f, exr_sha256=_sha(data))
            elif kind == "normals":
                data = _png(
                    Image.fromarray(np.full((h, w, 3), (128, 128, 255), dtype=np.uint8), "RGB")
                )
                _write(out / kind / "frames" / f"{f:04d}.png", data)
                marker(kind, f, png_sha256=_sha(data))
            elif kind == "segmentation":
                img = Image.fromarray(index, "P")
                img.putpalette([0, 0, 0, 85, 131, 242] + [0] * (768 - 6))
                data = _png(img)
                _write(out / kind / "frames" / f"{f:04d}.png", data)
                marker(kind, f, png_sha256=_sha(data))
        if "pose_skeleton" in passes:
            people = []
            for cid in chars:
                joints = {
                    name: {
                        "x": round(0.45 + 0.1 * (i % 3) / 2, 4),
                        "y": round(0.15 + 0.7 * i / 17, 4),
                        "z": 5.0,
                        "visible": True,
                        "in_frame": True,
                    }
                    for i, name in enumerate(OPENPOSE18)
                }
                joints["l_ear"] = {
                    "x": 1.4,
                    "y": 0.1,
                    "z": 5.0,
                    "visible": False,
                    "in_frame": False,
                }  # off screen
                people.append(
                    {
                        "character_id": cid,
                        "seg_id": seg[cid],
                        "joints": joints,
                        "bones": [["neck", "nose"], ["nose", "l_ear"], ["neck", "r_shoulder"]],
                        "openpose18": [],
                    }
                )
            doc = {
                "schema": "cf.blender_scene.skeleton.v1",
                "frame_index": f,
                "width": w,
                "height": h,
                "people": people,
            }
            data = _canon(doc)
            _write(out / "skeleton" / "frames" / f"{f:04d}.json", data)
            marker("skeleton", f, json_sha256=_sha(data))
        if "layout_boxes" in passes:
            objects = []
            for e in chars + [p["id"] for p in spec.get("props", [])]:
                bx = {
                    "x": box[0] / w,
                    "y": box[1] / h,
                    "w": (box[2] - box[0]) / w,
                    "h": (box[3] - box[1]) / h,
                }
                objects.append(
                    {
                        "object_id": e,
                        "kind": "character" if e in chars else "prop",
                        "seg_id": seg.get(e, 0),
                        "box": bx,
                        "xxyy": [bx["x"], bx["x"] + bx["w"], bx["y"], bx["y"] + bx["h"]],
                        "depth_min": 4.0,
                        "depth_max": 6.0,
                        "visible_fraction": 1.0,
                    }
                )
            doc = {
                "schema": "cf.blender_scene.layout.v1",
                "frame_index": f,
                "width": w,
                "height": h,
                "objects": objects,
                "hidream_layout_bboxes": [o["xxyy"] for o in objects][:5],
            }
            data = _canon(doc)
            _write(out / "layout" / "frames" / f"{f:04d}.json", data)
            marker("layout", f, json_sha256=_sha(data))

    cam_frames = []
    for f in frames:
        cam_frames.append(
            {
                "frame_index": f,
                "position": [0.0, -6.0 + 4.0 * f / max(1, frame_count - 1), 1.6],
                "rotation_quat_wxyz": [0.7071, 0.7071, 0.0, 0.0],
                "look_at": [0.0, 0.0, 1.0],
                "lens_mm": 35.0,
                "sensor_width_mm": 36.0,
                "focus_distance": 6.0,
                "intrinsics": [35 / 36 * w, 35 / 36 * w, w / 2, h / 2],
                "world_to_camera": [1, 0, 0, 0, 0, 0, 1, -1.6, 0, -1, 0, -6, 0, 0, 0, 1],
                "track_quat_check": [0.7071, 0.7071, 0.0, 0.0],
            }
        )
    _write(
        out / "camera.json", _canon({"schema": "cf.blender_scene.camera.v1", "frames": cam_frames})
    )
    _write(
        out / "metadata.json",
        _canon({**manifest, "schema": "cf.blender_scene.metadata.v1", "counts": {}}),
    )
    _write(out / "spec.json", _canon(spec))
    return manifest


def fake_subprocess_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
    """Drop-in for ``subprocess.run`` on the skill command built by ``controls.blender``."""
    idx = cmd.index(next(a for a in cmd if a.endswith("render.py")))
    spec_path, out_dir = Path(cmd[idx + 1]), Path(cmd[idx + 2])
    spec = json.loads(spec_path.read_text())
    manifest = write_fake_outputs(spec, out_dir)
    summary = {
        "ok": True,
        "exit": 0,
        "frames": len(manifest["frames_rendered"]),
        "passes": manifest["passes"],
        "engines": manifest["engines"],
        "blender_version": FAKE_BLENDER_VERSION,
        "spec_sha256": manifest["spec_sha256"],
    }
    return subprocess.CompletedProcess(
        cmd, 0, stdout="Blender (fake)\n" + json.dumps(summary, sort_keys=True) + "\n", stderr=""
    )


def failing_subprocess_run(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        cmd,
        3,
        stdout=json.dumps({"ok": False, "exit": 3, "error": "blender failed: simulated"}) + "\n",
        stderr="Traceback (fake)",
    )
