"""Isolates every Blender API call whose name or shape has moved between versions."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]
import numpy as np


def blender_version() -> str:
    return bpy.app.version_string


def build_hash() -> str:
    return (
        str(bpy.app.build_hash, "utf-8")
        if isinstance(bpy.app.build_hash, bytes)
        else str(bpy.app.build_hash)
    )


def set_engine(scene: Any, engine: str) -> None:
    scene.render.engine = engine


def set_if_has(obj: Any, name: str, value: Any) -> bool:
    if hasattr(obj, name):
        try:
            setattr(obj, name, value)
            return True
        except Exception:
            return False
    return False


def light_direction(azimuth_deg: float, elevation_deg: float) -> tuple[float, float, float]:
    """Direction the key light travels (Workbench ``light_direction`` semantics: towards the scene)."""
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    x = math.cos(el) * math.sin(az)
    y = -math.cos(el) * math.cos(az)
    z = -math.sin(el)
    return (x, y, z)


def read_multilayer_exr(path: Path) -> dict[str, np.ndarray]:
    """Read every channel of a (multi-part) OpenEXR file into float32 arrays keyed by
    ``<layer>.<pass>.<channel>`` as Blender names them (e.g. ``ViewLayer.Depth.Z``)."""
    import OpenImageIO as oiio  # type: ignore[import-not-found]  # bundled with Blender

    inp = oiio.ImageInput.open(str(path))
    if inp is None:
        raise RuntimeError(f"OpenImageIO cannot open {path}: {oiio.geterror()}")
    out: dict[str, np.ndarray] = {}
    try:
        sub = 0
        while True:
            spec = inp.spec()
            names = list(spec.channelnames)
            part_name = (
                spec.get_string_attribute("name") if hasattr(spec, "get_string_attribute") else ""
            )
            arr = inp.read_image(sub, 0, 0, spec.nchannels, oiio.FLOAT)  # current part, not part 0
            if arr is None:
                raise RuntimeError(f"OpenImageIO read failed for {path}: {inp.geterror()}")
            arr = np.asarray(arr, dtype=np.float32).reshape(spec.height, spec.width, spec.nchannels)
            for i, n in enumerate(names):
                key = n if "." in n or not part_name else f"{part_name}.{n}"
                out[key] = arr[:, :, i]
            sub += 1
            if not inp.seek_subimage(sub, 0):
                break
    finally:
        inp.close()
    return out


def find_channel(channels: dict[str, np.ndarray], *suffixes: str) -> np.ndarray | None:
    for suffix in suffixes:
        for name, arr in channels.items():
            if name.endswith(suffix):
                return arr
    return None


def write_single_channel_exr(path: Path, data: np.ndarray) -> None:
    """Float32 single-channel EXR (ZIP) via OpenImageIO; no timestamps, deterministic bytes."""
    import OpenImageIO as oiio  # type: ignore[import-not-found]

    h, w = data.shape
    spec = oiio.ImageSpec(w, h, 1, oiio.FLOAT)
    spec.channelnames = ("Z",)
    spec.attribute("compression", "zip")
    # OpenImageIO stamps the wall clock into EXR headers unless told otherwise; pin it.
    spec.attribute("DateTime", "2000:01:01 00:00:00")
    out = oiio.ImageOutput.create(str(path))
    if out is None:
        raise RuntimeError(f"OpenImageIO cannot create {path}: {oiio.geterror()}")
    try:
        if not out.open(str(path), spec):
            raise RuntimeError(f"OpenImageIO open failed: {out.geterror()}")
        # OIIO expects (height, width, channels); a bare 2-D array crashes contiguize() at some sizes.
        out.write_image(np.ascontiguousarray(data.astype(np.float32)).reshape(h, w, 1))
    finally:
        out.close()
