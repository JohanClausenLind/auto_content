"""Screen-space layout boxes from projected points (pure Python)."""

from __future__ import annotations

import math
from typing import Any


def bbox_from_points(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Axis-aligned box (x, y, w, h) in normalised coords, clipped to [0, 1]; None if empty or
    fully outside the frame."""
    pts = [(u, v) for u, v in points if not (math.isnan(u) or math.isnan(v))]
    if not pts:
        return None
    x0 = max(0.0, min(u for u, _ in pts))
    x1 = min(1.0, max(u for u, _ in pts))
    y0 = max(0.0, min(v for _, v in pts))
    y1 = min(1.0, max(v for _, v in pts))
    if x1 <= x0 or y1 <= y0:
        return None
    return (round(x0, 6), round(y0, 6), round(x1 - x0, 6), round(y1 - y0, 6))


def visible_fraction(points: list[tuple[float, float]]) -> float:
    """Fraction of the unclipped projected area that lies inside the frame (0 if nothing)."""
    pts = [(u, v) for u, v in points if not (math.isnan(u) or math.isnan(v))]
    if not pts:
        return 0.0
    x0, x1 = min(u for u, _ in pts), max(u for u, _ in pts)
    y0, y1 = min(v for _, v in pts), max(v for _, v in pts)
    area = (x1 - x0) * (y1 - y0)
    if area <= 0.0:
        return 1.0 if 0.0 <= x0 <= 1.0 and 0.0 <= y0 <= 1.0 else 0.0
    cx0, cx1 = max(0.0, x0), min(1.0, x1)
    cy0, cy1 = max(0.0, y0), min(1.0, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return 0.0
    return round(((cx1 - cx0) * (cy1 - cy0)) / area, 6)


def xxyy(box: tuple[float, float, float, float]) -> list[float]:
    """Our (x, y, w, h) -> HiDream's relative [x1, x2, y1, y2]."""
    x, y, w, h = box
    return [round(x, 6), round(x + w, 6), round(y, 6), round(y + h, 6)]


def hidream_boxes(objects: list[dict[str, Any]], max_boxes: int = 5) -> list[list[float]]:
    """Layout boxes in object order (matching reference-image order), capped like HiDream caps
    them; the largest boxes win when there are more than ``max_boxes``."""
    with_box = [o for o in objects if o.get("box") is not None]
    if len(with_box) > max_boxes:
        ranked = sorted(with_box, key=lambda o: o["box"]["w"] * o["box"]["h"], reverse=True)
        keep = {id(o) for o in ranked[:max_boxes]}
        with_box = [o for o in with_box if id(o) in keep]
    return [xxyy((o["box"]["x"], o["box"]["y"], o["box"]["w"], o["box"]["h"])) for o in with_box]
