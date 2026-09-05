"""Segmentation palette: stable id -> colour for indexed PNGs and viewer-friendly overlays."""

from __future__ import annotations

_GOLDEN = 0.618033988749895


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    i = int(h * 6.0) % 6
    f = h * 6.0 - int(h * 6.0)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return (round(r * 255), round(g * 255), round(b * 255))


def color_for_id(seg_id: int) -> tuple[int, int, int]:
    """Deterministic, well separated colours; 0 (background) is black."""
    if seg_id <= 0:
        return (0, 0, 0)
    h = (seg_id * _GOLDEN) % 1.0
    s = 0.65 if seg_id % 2 else 0.9
    v = 0.95 if seg_id % 3 else 0.7
    return _hsv_to_rgb(h, s, v)


def palette_bytes() -> bytes:
    """768-byte RGB palette for a mode-P PNG: index i -> color_for_id(i)."""
    out = bytearray()
    for i in range(256):
        out.extend(color_for_id(i))
    return bytes(out)
