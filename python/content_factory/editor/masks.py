"""Region Editor foundations (16.4): SelectionRegions rasterized to versioned MaskAssets.

Deterministic: the same region at the same resolution yields byte-identical PNG masks. Masks are
content-addressed and immutable; applying a masked edit creates a new asset revision, never an
overwrite. Whole-image prompting is never a substitute for a mask (enforced by the router's
capability flags — this module only produces the masks)."""

from __future__ import annotations

import io
import itertools
from typing import Annotated, Literal

from PIL import Image, ImageDraw
from pydantic import Field

from content_factory.schemas.base import OpaqueId, SchemaModel, Sha256Hex, sha256_hex
from content_factory.schemas.sequences import Box, Point


class RectRegion(SchemaModel):
    kind: Literal["rect"] = "rect"
    box: Box


class PolygonRegion(SchemaModel):
    kind: Literal["polygon"] = "polygon"
    points: tuple[Point, ...] = Field(min_length=3, max_length=200)


class BrushRegion(SchemaModel):
    """A brush stroke: polyline of normalized points with a normalized radius."""

    kind: Literal["brush"] = "brush"
    points: tuple[Point, ...] = Field(min_length=1, max_length=2000)
    radius: float = Field(gt=0, le=0.25)


SelectionShape = Annotated[RectRegion | PolygonRegion | BrushRegion, Field(discriminator="kind")]


class SelectionRegion(SchemaModel):
    region_id: OpaqueId
    add: tuple[SelectionShape, ...] = Field(min_length=1)
    subtract: tuple[SelectionShape, ...] = ()
    invert: bool = False
    feather_px: int = Field(default=0, ge=0, le=256)
    expand_px: int = Field(default=0, ge=-256, le=256)
    protect: tuple[SelectionShape, ...] = ()  # never modified, whatever add/invert say


class MaskAsset(SchemaModel):
    mask_id: OpaqueId
    region_id: OpaqueId
    source_revision: Sha256Hex
    width: int
    height: int
    png_sha256: Sha256Hex
    coverage: float = Field(ge=0, le=1)


def _draw(shape: SelectionShape, draw: ImageDraw.ImageDraw, w: int, h: int, value: int) -> None:
    if shape.kind == "rect":
        b = shape.box
        draw.rectangle(
            [
                round(b.x * (w - 1)),
                round(b.y * (h - 1)),
                round((b.x + b.w) * (w - 1)),
                round((b.y + b.h) * (h - 1)),
            ],
            fill=value,
        )
    elif shape.kind == "polygon":
        draw.polygon(
            [(round(p.x * (w - 1)), round(p.y * (h - 1))) for p in shape.points], fill=value
        )
    else:
        r = round(shape.radius * min(w, h))
        pts = [(round(p.x * (w - 1)), round(p.y * (h - 1))) for p in shape.points]
        for a, b in itertools.pairwise(pts):
            draw.line([a, b], fill=value, width=max(1, 2 * r))
        for cx, cy in pts:
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=value)


def rasterize(
    region: SelectionRegion, *, width: int, height: int, mask_id: str, source_revision: str
) -> tuple[MaskAsset, bytes]:
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    for shape in region.add:
        _draw(shape, draw, width, height, 255)
    for shape in region.subtract:
        _draw(shape, draw, width, height, 0)
    if region.invert:
        img = img.point(lambda v: 255 - v)
    if region.expand_px:
        from PIL import ImageFilter

        f = (
            ImageFilter.MaxFilter(2 * abs(region.expand_px) + 1)
            if region.expand_px > 0
            else ImageFilter.MinFilter(2 * abs(region.expand_px) + 1)
        )
        img = img.filter(f)
    if region.feather_px:
        from PIL import ImageFilter

        img = img.filter(ImageFilter.GaussianBlur(region.feather_px))
    if region.protect:
        pr = Image.new("L", (width, height), 0)
        pd = ImageDraw.Draw(pr)
        for shape in region.protect:
            _draw(shape, pd, width, height, 255)
        img = Image.composite(Image.new("L", (width, height), 0), img, pr)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    png = buf.getvalue()
    hist = img.histogram()
    coverage = sum(i * c for i, c in enumerate(hist)) / (255.0 * width * height)
    asset = MaskAsset(
        mask_id=mask_id,
        region_id=region.region_id,
        source_revision=source_revision,
        width=width,
        height=height,
        png_sha256=sha256_hex(png),
        coverage=round(coverage, 6),
    )
    return asset, png
