"""Drift QC (16.6): deterministic comparison of each generated frame against the anchor.

Locked-region similarity is measured OUTSIDE the frame's declared motion region (the subject may
move; everything else may not). Style delta is a global luminance-histogram distance. The
consistency priority (composition > subject anatomy > style > fine detail) is expressed as which
checks block: locked-region (composition) failures block first, style second."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter

from content_factory.schemas.sequences import Box


@dataclass(frozen=True)
class DriftReport:
    frame_index: int
    locked_region_similarity: float  # 1.0 = identical outside the motion region
    style_delta: float  # 0.0 = identical global style
    passed: bool
    reasons: tuple[str, ...]


def _load(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def _mask_out(img: Image.Image, boxes: list[Box]) -> Image.Image:
    from PIL import ImageDraw

    out = img.copy()
    draw = ImageDraw.Draw(out)
    w, h = img.size
    for b in boxes:
        draw.rectangle(
            [round(b.x * w), round(b.y * h), round((b.x + b.w) * w), round((b.y + b.h) * h)],
            fill=(127, 127, 127),
        )
    return out


def _similarity(a: Image.Image, b: Image.Image) -> float:
    a = a.resize((128, 128)).filter(ImageFilter.GaussianBlur(1)).convert("L")
    b = b.resize((128, 128)).filter(ImageFilter.GaussianBlur(1)).convert("L")
    hist = ImageChops.difference(a, b).histogram()
    total = sum(hist)
    mean = sum(i * c for i, c in enumerate(hist)) / max(1, total)
    return max(0.0, 1.0 - mean / 64.0)


def _style_delta(a: Image.Image, b: Image.Image) -> float:
    ha = a.convert("L").histogram()
    hb = b.convert("L").histogram()
    na, nb = sum(ha), sum(hb)
    return sum(abs(x / na - y / nb) for x, y in zip(ha, hb, strict=True)) / 2.0


def drift_report(
    frame_index: int,
    anchor_png: bytes,
    frame_png: bytes,
    motion_boxes: list[Box],
    *,
    locked_region_similarity_min: float = 0.92,
    style_delta_max: float = 0.15,
) -> DriftReport:
    anchor, frame = _load(anchor_png), _load(frame_png)
    if anchor.size != frame.size:
        return DriftReport(
            frame_index,
            0.0,
            1.0,
            False,
            (f"frame size {frame.size} differs from anchor {anchor.size}",),
        )
    locked = _similarity(_mask_out(anchor, motion_boxes), _mask_out(frame, motion_boxes))
    style = _style_delta(anchor, frame)
    reasons: list[str] = []
    if locked < locked_region_similarity_min:
        reasons.append(
            f"locked-region similarity {locked:.3f} below {locked_region_similarity_min} (composition drift)"
        )
    if style > style_delta_max:
        reasons.append(f"style delta {style:.3f} above {style_delta_max}")
    return DriftReport(frame_index, round(locked, 4), round(style, 4), not reasons, tuple(reasons))
