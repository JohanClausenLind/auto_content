"""Post-processing without Blender: synthetic raw outputs -> final pass tree."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from postprocess import (
    PostprocessError,
    canny_edges,
    depth_to_8bit_inverse,
    depth_to_16bit,
    finalize,
    normals_to_rgb8,
)
from spec import SKILL_VERSION, load_spec

FIX = Path(__file__).parent / "fixtures" / "shot_cube_dolly.json"


def test_depth_encodings() -> None:
    depth = np.array([[1.0, 2.0], [3.0, 1e10]], dtype=np.float32)
    d16 = depth_to_16bit(depth, 1.0, 3.0)
    assert d16.tolist() == [[0, 32768], [65535, 65535]]
    d8 = depth_to_8bit_inverse(depth, 1.0, 3.0)
    assert d8.tolist() == [[255, 128], [0, 0]]


def test_normals_encoding_background_black() -> None:
    n = np.array([[[0.0, 0.0, 1.0], [0.0, 0.0, 0.0]]], dtype=np.float32)
    rgb = normals_to_rgb8(n)
    assert rgb[0, 0].tolist() == [128, 128, 255] and rgb[0, 1].tolist() == [0, 0, 0]


def test_canny_finds_edges_on_low_contrast_input() -> None:
    rgb = np.full((32, 32, 3), 20, dtype=np.uint8)
    rgb[8:24, 8:24] = 28  # tiny contrast step, as in a dark clay render
    edges = canny_edges(rgb, 100, 200)
    assert edges.dtype == np.uint8 and (edges > 0).sum() > 40
    assert np.array_equal(edges, canny_edges(rgb, 100, 200))


def _fake_raw(raw: Path, spec: dict, frames: list[int]) -> None:
    w, h = spec["width"], spec["height"]
    raw.mkdir(parents=True)
    _yy, xx = np.mgrid[0:h, 0:w]
    for f in frames:
        depth = (2.0 + 6.0 * xx / (w - 1)).astype(np.float32)
        depth[:4, :] = 1e10  # sky
        np.save(raw / f"depth_{f:04d}.npy", depth)
        normals = np.zeros((h, w, 3), dtype=np.float32)
        normals[..., 2] = 1.0
        normals[:4, :] = 0.0
        np.save(raw / f"normals_{f:04d}.npy", normals)
        index = np.zeros((h, w), dtype=np.uint8)
        index[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4] = 1
        np.save(raw / f"index_{f:04d}.npy", index)
        (raw / f"depth_{f:04d}.exr").write_bytes(b"EXR" + bytes([f]))
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        rgb[..., 0] = (40 + 60 * (index > 0)).astype(np.uint8)
        Image.fromarray(rgb, mode="RGB").save(raw / f"rgb_{f:04d}.png")
        (raw / f"skeleton_{f:04d}.json").write_text(
            json.dumps(
                {
                    "schema": "cf.blender_scene.skeleton.v1",
                    "frame_index": f,
                    "width": w,
                    "height": h,
                    "people": [],
                }
            )
        )
        (raw / f"layout_{f:04d}.json").write_text(
            json.dumps(
                {
                    "schema": "cf.blender_scene.layout.v1",
                    "frame_index": f,
                    "objects": [],
                    "hidream_layout_bboxes": [],
                }
            )
        )
    (raw / "camera.json").write_text(
        json.dumps(
            {"schema": "cf.blender_scene.camera.v1", "frames": [{"frame_index": f} for f in frames]}
        )
    )
    (raw / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "cf.blender_scene.manifest.v1",
                "skill_version": SKILL_VERSION,
                "blender_version": "5.2.1 LTS",
                "blender_build_hash": "abc",
                "spec_sha256": "0" * 64,
                "shot_id": spec["shot_id"],
                "width": w,
                "height": h,
                "fps": 24,
                "frame_count": spec["frame_count"],
                "frames_rendered": frames,
                "seed": 7,
                "passes": spec["render"]["passes"],
                "engines": {"data": "cycles_cpu", "rgb": "workbench"},
                "depth": {"near": 2.0, "far": 8.0, "source": "explicit"},
                "seg": {"objects": {"cube": 1}, "background": 0},
                "characters": {},
            }
        )
    )


def test_finalize_writes_every_pass_and_is_byte_identical(tmp_path: Path) -> None:
    spec = load_spec(FIX)
    spec["width"] = spec["height"] = 64
    frames = [0, 1, 2, 3]
    outs = []
    for name in ("a", "b"):
        raw = tmp_path / name / "raw"
        _fake_raw(raw, spec, frames)
        meta = finalize(raw, tmp_path / name, spec, keep_raw=False)
        assert not raw.exists()
        assert meta["counts"] == {
            k: 4
            for k in (
                "canny",
                "depth",
                "depth16",
                "depth_exr",
                "normals",
                "rough_rgb",
                "segmentation",
                "skeleton",
                "layout",
            )
        }
        outs.append(
            {
                p.relative_to(tmp_path / name): p.read_bytes()
                for p in (tmp_path / name).rglob("*")
                if p.is_file()
            }
        )
    assert outs[0] == outs[1]
    out = tmp_path / "a"
    seg = Image.open(out / "segmentation" / "frames" / "0000.png")
    assert seg.mode == "P" and np.asarray(seg)[32, 32] == 1 and np.asarray(seg)[1, 1] == 0
    d16 = np.asarray(Image.open(out / "depth16" / "frames" / "0000.png"))
    assert (
        d16.dtype == np.uint16 and d16[10, 0] == 0 and d16[10, 63] == 65535 and d16[0, 10] == 65535
    )
    d8 = np.asarray(Image.open(out / "depth" / "frames" / "0000.png"))
    assert d8[10, 0] == 255 and d8[0, 10] == 0
    marker = json.loads((out / "depth" / "frames" / "0000.done.json").read_text())
    assert marker["pass"] == "depth" and marker["near"] == 2.0 and len(marker["input_hash"]) == 64
    other = json.loads((out / "depth" / "frames" / "0001.done.json").read_text())
    assert other["input_hash"] != marker["input_hash"]  # frame index is part of the key
    assert (
        (out / "camera.json").exists()
        and (out / "metadata.json").exists()
        and (out / "spec.json").exists()
    )


def test_finalize_rejects_shape_mismatch(tmp_path: Path) -> None:
    spec = load_spec(FIX)
    spec["width"] = spec["height"] = 64
    raw = tmp_path / "raw"
    _fake_raw(raw, spec, [0, 1, 2, 3])
    np.save(raw / "depth_0002.npy", np.zeros((8, 8), dtype=np.float32))
    with pytest.raises(PostprocessError, match="shape"):
        finalize(raw, tmp_path, spec, keep_raw=True)
