"""Canonical PNG encoding (venv side, Pillow): no ancillary chunks, fixed compression, so equal
pixels give equal bytes — the same settings content-factory's control compiler uses."""

from __future__ import annotations

import hashlib
import io

from PIL import Image


def encode_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
