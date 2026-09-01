from __future__ import annotations

import io

from PIL import Image

from content_factory.editor.masks import (
    BrushRegion,
    PolygonRegion,
    RectRegion,
    SelectionRegion,
    rasterize,
)
from content_factory.schemas.sequences import Box, Point

SRC = "a" * 64


def region(**over):
    base = {
        "region_id": "reg_test00000001",
        "add": (RectRegion(box=Box(x=0.25, y=0.25, w=0.5, h=0.5)),),
    }
    base.update(over)
    return SelectionRegion(**base)


def test_rasterization_is_deterministic_and_content_addressed() -> None:
    a, png_a = rasterize(
        region(), width=256, height=256, mask_id="msk_a0000000001", source_revision=SRC
    )
    b, png_b = rasterize(
        region(), width=256, height=256, mask_id="msk_a0000000002", source_revision=SRC
    )
    assert png_a == png_b and a.png_sha256 == b.png_sha256
    assert 0.2 < a.coverage < 0.3
    img = Image.open(io.BytesIO(png_a))
    assert img.getpixel((128, 128)) == 255 and img.getpixel((10, 10)) == 0


def test_subtract_invert_feather_expand_and_protect() -> None:
    r = region(subtract=(RectRegion(box=Box(x=0.4, y=0.4, w=0.2, h=0.2)),))
    asset, png = rasterize(r, width=200, height=200, mask_id="msk_b0000000001", source_revision=SRC)
    img = Image.open(io.BytesIO(png))
    assert img.getpixel((100, 100)) == 0 and img.getpixel((60, 60)) == 255
    _inv, png_inv = rasterize(
        region(invert=True), width=200, height=200, mask_id="msk_c0000000001", source_revision=SRC
    )
    assert Image.open(io.BytesIO(png_inv)).getpixel((10, 10)) == 255
    feathered, _ = rasterize(
        region(feather_px=8), width=200, height=200, mask_id="msk_d0000000001", source_revision=SRC
    )
    assert feathered.png_sha256 != asset.png_sha256
    protected, png_p = rasterize(
        region(protect=(RectRegion(box=Box(x=0.45, y=0.45, w=0.1, h=0.1)),)),
        width=200,
        height=200,
        mask_id="msk_e0000000001",
        source_revision=SRC,
    )
    assert (
        Image.open(io.BytesIO(png_p)).getpixel((100, 100)) == 0
    )  # protected pixels never selected
    del protected


def test_brush_and_polygon_shapes() -> None:
    brush = region(
        add=(BrushRegion(points=(Point(x=0.2, y=0.5), Point(x=0.8, y=0.5)), radius=0.05),)
    )
    _, png = rasterize(brush, width=300, height=300, mask_id="msk_f0000000001", source_revision=SRC)
    img = Image.open(io.BytesIO(png))
    assert img.getpixel((150, 150)) == 255 and img.getpixel((150, 40)) == 0
    poly = region(
        add=(PolygonRegion(points=(Point(x=0.5, y=0.1), Point(x=0.9, y=0.9), Point(x=0.1, y=0.9))),)
    )
    _, png2 = rasterize(poly, width=300, height=300, mask_id="msk_g0000000001", source_revision=SRC)
    assert Image.open(io.BytesIO(png2)).getpixel((150, 200)) == 255
