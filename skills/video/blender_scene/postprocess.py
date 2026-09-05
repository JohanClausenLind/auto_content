"""Turn Blender's raw outputs into the final pass directories (venv side: numpy, Pillow, OpenCV).

Final layout under <out_dir>:
    <kind>/frames/NNNN.png (+ .exr for depth_exr) + NNNN.done.json
    skeleton/frames/NNNN.json + .done.json, layout/frames/NNNN.json + .done.json
    camera.json, metadata.json, spec.json
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from palette import palette_bytes
from PIL import Image
from pngio import encode_png, sha256_hex
from spec import SKILL_VERSION, canonical_dumps


class PostprocessError(RuntimeError):
    pass


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _input_hash(manifest: dict[str, Any], pass_name: str, frame: int) -> str:
    key = {
        "spec_sha256": manifest["spec_sha256"],
        "pass": pass_name,
        "frame_index": frame,
        "skill_version": SKILL_VERSION,
        "blender_version": manifest["blender_version"],
        "engines": manifest["engines"],
        "depth": manifest["depth"],
    }
    return sha256_hex(canonical_dumps(key).encode("utf-8"))


def _marker(
    out_dir: Path, pass_name: str, frame: int, manifest: dict[str, Any], **fields: Any
) -> dict[str, Any]:
    doc = {
        "frame_index": frame,
        "pass": pass_name,
        "input_hash": _input_hash(manifest, pass_name, frame),
        "width": manifest["width"],
        "height": manifest["height"],
        **fields,
    }
    _write(
        out_dir / pass_name / "frames" / f"{frame:04d}.done.json",
        (canonical_dumps(doc) + "\n").encode(),
    )
    return doc


def _png_pass(
    out_dir: Path,
    pass_name: str,
    frame: int,
    img: Image.Image,
    manifest: dict[str, Any],
    **extra: Any,
) -> str:
    data = encode_png(img)
    sha = sha256_hex(data)
    _write(out_dir / pass_name / "frames" / f"{frame:04d}.png", data)
    _marker(out_dir, pass_name, frame, manifest, png_sha256=sha, **extra)
    return sha


def _json_pass(
    out_dir: Path, pass_name: str, frame: int, doc: dict[str, Any], manifest: dict[str, Any]
) -> str:
    data = (canonical_dumps(doc) + "\n").encode()
    sha = sha256_hex(data)
    _write(out_dir / pass_name / "frames" / f"{frame:04d}.json", data)
    _marker(out_dir, pass_name, frame, manifest, json_sha256=sha)
    return sha


def depth_to_16bit(depth: np.ndarray, near: float, far: float) -> np.ndarray:
    norm = np.clip((depth.astype(np.float64) - near) / (far - near), 0.0, 1.0)
    return np.rint(norm * 65535.0).astype(np.uint16)


def depth_to_8bit_inverse(depth: np.ndarray, near: float, far: float) -> np.ndarray:
    """ControlNet convention: near = bright, far/background = 0."""
    norm = np.clip((depth.astype(np.float64) - near) / (far - near), 0.0, 1.0)
    return np.rint((1.0 - norm) * 255.0).astype(np.uint8)


def normals_to_rgb8(normals: np.ndarray) -> np.ndarray:
    """(n+1)/2 encoding; pixels with no surface (zero vector) are black, not mid-grey."""
    n = normals.astype(np.float64)
    rgb = np.rint(np.clip((n + 1.0) * 0.5, 0.0, 1.0) * 255.0).astype(np.uint8)
    rgb[np.all(np.abs(n) < 1e-6, axis=-1)] = 0
    return rgb


def index_to_palette_image(index: np.ndarray) -> Image.Image:
    img = Image.fromarray(index.astype(np.uint8), mode="P")
    img.putpalette(palette_bytes())
    return img


def canny_edges(rgb: np.ndarray, low: int, high: int) -> np.ndarray:
    """Canny on a contrast-stretched grey image. The clay render is deliberately low contrast, so
    thresholds are applied after stretching the grey range to 0..255 (deterministic, integer)."""
    import cv2

    gray = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY)
    lo, hi = int(gray.min()), int(gray.max())
    if hi > lo:
        gray = (
            np.rint((gray.astype(np.int32) - lo) * (255.0 / (hi - lo)))
            .clip(0, 255)
            .astype(np.uint8)
        )
    return cv2.Canny(gray, float(low), float(high), L2gradient=True)


def canonical_rgb(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGB").copy()


def finalize(
    raw_dir: Path, out_dir: Path, spec: dict[str, Any], *, keep_raw: bool = False
) -> dict[str, Any]:
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    camera = json.loads((raw_dir / "camera.json").read_text(encoding="utf-8"))
    passes = set(spec["render"]["passes"])
    frames: list[int] = list(manifest["frames_rendered"])
    near, far = float(manifest["depth"]["near"]), float(manifest["depth"]["far"])
    w, h = manifest["width"], manifest["height"]
    counts: dict[str, int] = {}

    def bump(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for frame in frames:
        need_depth = passes & {"depth", "depth16", "depth_exr", "normals", "segmentation"}
        if need_depth:
            depth = np.load(raw_dir / f"depth_{frame:04d}.npy")
            if depth.shape != (h, w):
                raise PostprocessError(f"depth {frame} has shape {depth.shape}, expected {(h, w)}")
        if "depth" in passes:
            img = Image.fromarray(depth_to_8bit_inverse(depth, near, far), mode="L")
            _png_pass(
                out_dir, "depth", frame, img, manifest, near=near, far=far, encoding="gray8_inverse"
            )
            bump("depth")
        if "depth16" in passes:
            img = Image.fromarray(depth_to_16bit(depth, near, far))  # uint16 -> mode I;16
            _png_pass(
                out_dir,
                "depth16",
                frame,
                img,
                manifest,
                near=near,
                far=far,
                encoding="gray16_linear",
            )
            bump("depth16")
        if "depth_exr" in passes:
            src = raw_dir / f"depth_{frame:04d}.exr"
            data = src.read_bytes()
            _write(out_dir / "depth_exr" / "frames" / f"{frame:04d}.exr", data)
            _marker(
                out_dir,
                "depth_exr",
                frame,
                manifest,
                exr_sha256=sha256_hex(data),
                encoding="exr32_metres",
            )
            bump("depth_exr")
        if "normals" in passes:
            normals = np.load(raw_dir / f"normals_{frame:04d}.npy")
            img = Image.fromarray(normals_to_rgb8(normals), mode="RGB")
            _png_pass(out_dir, "normals", frame, img, manifest, space="camera", encoding="(n+1)/2")
            bump("normals")
        if "segmentation" in passes:
            index = np.load(raw_dir / f"index_{frame:04d}.npy")
            ids_present = sorted(int(v) for v in np.unique(index))
            _png_pass(
                out_dir,
                "segmentation",
                frame,
                index_to_palette_image(index),
                manifest,
                encoding="index8",
                seg_ids=ids_present,
                background=0,
            )
            bump("segmentation")
        rgb_img: Image.Image | None = None
        if passes & {"rough_rgb", "canny"}:
            rgb_img = canonical_rgb(raw_dir / f"rgb_{frame:04d}.png")
            if rgb_img.size != (w, h):
                raise PostprocessError(f"rgb {frame} has size {rgb_img.size}, expected {(w, h)}")
        if "rough_rgb" in passes and rgb_img is not None:
            _png_pass(
                out_dir, "rough_rgb", frame, rgb_img, manifest, engine=manifest["engines"]["rgb"]
            )
            bump("rough_rgb")
        if "canny" in passes and rgb_img is not None:
            c = spec["render"].get("canny", {"low": 100, "high": 200})
            edges = canny_edges(
                np.asarray(rgb_img), int(c.get("low", 100)), int(c.get("high", 200))
            )
            _png_pass(
                out_dir,
                "canny",
                frame,
                Image.fromarray(edges, mode="L"),
                manifest,
                low=c.get("low", 100),
                high=c.get("high", 200),
            )
            bump("canny")
        if "pose_skeleton" in passes:
            doc = json.loads((raw_dir / f"skeleton_{frame:04d}.json").read_text(encoding="utf-8"))
            _json_pass(out_dir, "skeleton", frame, doc, manifest)
            bump("skeleton")
        if "layout_boxes" in passes:
            doc = json.loads((raw_dir / f"layout_{frame:04d}.json").read_text(encoding="utf-8"))
            _json_pass(out_dir, "layout", frame, doc, manifest)
            bump("layout")

    _write(out_dir / "camera.json", (json.dumps(camera, indent=1, sort_keys=True) + "\n").encode())
    _write(out_dir / "spec.json", (json.dumps(spec, indent=1, sort_keys=True) + "\n").encode())
    metadata = {
        "schema": "cf.blender_scene.metadata.v1",
        **{
            k: manifest[k]
            for k in (
                "skill_version",
                "blender_version",
                "blender_build_hash",
                "spec_sha256",
                "shot_id",
                "width",
                "height",
                "fps",
                "frame_count",
                "frames_rendered",
                "seed",
                "passes",
                "engines",
                "seg",
                "characters",
            )
        },
        # One row per posed character per frame. For a cf.clip.v2 clip it names the clip, the
        # actor and how many bones the aim solve actually aimed, which is the difference between
        # a retarget that ran and one that silently did nothing.
        "poses": manifest.get("poses", []),
        "depth": {
            **manifest["depth"],
            "encoding_8bit": "255*(1-(z-near)/(far-near)), background 0",
            "encoding_16bit": "65535*(z-near)/(far-near), background 65535",
            "exr": "float32 metres",
        },
        "normals": {"space": "camera", "encoding": "(n+1)/2; +X right, +Y up, +Z toward camera"},
        "segmentation": {"encoding": "index8 PNG (mode P), pixel = seg_id, 0 = background"},
        "skeleton": {
            "joints": "OpenPose-18 names, normalised x/y (y down), z metres, visible/in_frame"
        },
        "layout": {"box": "normalised x,y,w,h", "xxyy": "HiDream [x1,x2,y1,y2] relative"},
        "counts": counts,
    }
    _write(
        out_dir / "metadata.json", (json.dumps(metadata, indent=1, sort_keys=True) + "\n").encode()
    )
    expected = len(frames)
    for name, n in counts.items():
        if n != expected:
            raise PostprocessError(f"pass {name} has {n} frames, expected {expected}")
    if not keep_raw:
        shutil.rmtree(raw_dir, ignore_errors=True)
    return metadata
