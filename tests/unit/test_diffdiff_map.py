"""The change map's polarity is the inverse of every other mask here, so it gets its own suite."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from content_factory.schemas.sequences import Box
from content_factory.sequences.diffdiff_map import (
    KEEP,
    REGENERATE,
    TOKEN_STRIDE,
    DiffDiffMapError,
    composite,
    describe,
    from_mask_png,
    soft_map,
    token_grid,
)


def _pixels(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("L"), dtype=np.uint8)


def test_box_is_dark_and_background_is_light() -> None:
    """Black regenerates, white keeps -- the direction the block source blends in."""
    png = soft_map(
        width=512, height=512, regenerate=[Box(x=0.25, y=0.25, w=0.5, h=0.5)], feather_tokens=0
    )
    px = _pixels(png)
    assert px[256, 256] == REGENERATE  # centre of the box: repainted
    assert px[8, 8] == KEEP  # far corner: untouched


def test_feather_is_a_ramp_not_a_step() -> None:
    """The grey band is the point: a hard edge has a seam to hide, a ramp does not."""
    hard = _pixels(
        soft_map(
            width=512, height=512, regenerate=[Box(x=0.25, y=0.25, w=0.5, h=0.5)], feather_tokens=0
        )
    )
    soft = _pixels(
        soft_map(
            width=512, height=512, regenerate=[Box(x=0.25, y=0.25, w=0.5, h=0.5)], feather_tokens=2
        )
    )
    assert set(np.unique(hard)) == {REGENERATE, KEEP}
    midtones = ((soft > 8) & (soft < 247)).sum()
    assert midtones > 0, "a feathered map must contain values between the two extremes"


def test_hold_and_outside_shift_the_band() -> None:
    png = soft_map(
        width=256,
        height=256,
        regenerate=[Box(x=0.0, y=0.0, w=1.0, h=1.0)],
        feather_tokens=0,
        hold=0.3,
        outside=1.0,
    )
    assert _pixels(png)[128, 128] == pytest.approx(round(0.3 * 255), abs=1)


def test_hold_above_outside_is_refused() -> None:
    """Otherwise nothing is released and the run returns the input after paying for it."""
    with pytest.raises(DiffDiffMapError, match="nothing regenerates"):
        soft_map(
            width=256,
            height=256,
            regenerate=[Box(x=0.1, y=0.1, w=0.2, h=0.2)],
            hold=0.8,
            outside=0.5,
        )


def test_empty_region_list_is_refused() -> None:
    with pytest.raises(DiffDiffMapError, match="no-op inpaint"):
        soft_map(width=256, height=256, regenerate=[])


def test_box_that_rounds_away_is_refused() -> None:
    """A box legal as a fraction can still vanish once scaled: 0.001 of 256 px rounds to zero."""
    with pytest.raises(DiffDiffMapError, match="empty at 256x256"):
        soft_map(width=256, height=256, regenerate=[Box(x=0.5, y=0.5, w=0.001, h=0.2)])


def test_from_mask_png_inverts_by_default() -> None:
    """ComfyUI and A1111 paint WHITE where the model should work; diffdiff reads white as keep."""
    mask = Image.new("L", (256, 256), 0)
    mask.paste(255, (64, 64, 192, 192))  # conventional: white == inpaint here
    buf = io.BytesIO()
    mask.save(buf, format="PNG")

    converted = _pixels(from_mask_png(buf.getvalue(), feather_tokens=0))
    assert converted[128, 128] == REGENERATE  # the painted region is what gets repainted
    assert converted[8, 8] == KEEP

    verbatim = _pixels(from_mask_png(buf.getvalue(), invert=False, feather_tokens=0))
    assert verbatim[128, 128] == KEEP  # opted out: taken as already-change-map polarity


def test_token_grid_is_the_stride_not_the_pixels() -> None:
    assert token_grid(1024, 1024) == (1024 // TOKEN_STRIDE, 1024 // TOKEN_STRIDE)
    assert token_grid(1280, 720) == (80, 45)


def test_describe_counts_the_released_fraction() -> None:
    half = soft_map(
        width=512, height=512, regenerate=[Box(x=0.0, y=0.0, w=1.0, h=0.5)], feather_tokens=0
    )
    facts = describe(half)
    assert facts["regenerated"] == pytest.approx(0.5, abs=0.02)
    # 15/32, not 0.5: the BILINEAR resize to the 32x32 token grid blends the boundary row, which
    # counts as neither extreme. That one-cell band is the map's resolution limit.
    assert facts["untouched"] == pytest.approx(15 / 32, abs=0.01)
    assert facts["grid_w"] == 32
    assert facts["grid_h"] == 32


def test_composite_restores_the_kept_region_exactly() -> None:
    """Diffdiff anchors latents, not pixels; the VAE round-trip still moves the frozen part."""
    original = Image.new("RGB", (256, 256), (10, 120, 200))
    painted = Image.new("RGB", (256, 256), (240, 30, 30))
    buf_o, buf_p = io.BytesIO(), io.BytesIO()
    original.save(buf_o, format="PNG")
    painted.save(buf_p, format="PNG")
    change_map = soft_map(
        width=256, height=256, regenerate=[Box(x=0.25, y=0.25, w=0.5, h=0.5)], feather_tokens=0
    )

    out = _pixels_rgb(composite(buf_o.getvalue(), buf_p.getvalue(), change_map))
    assert tuple(out[8, 8]) == (10, 120, 200)  # kept: exactly the original, not a re-encode
    assert tuple(out[128, 128]) == (240, 30, 30)  # released: the repaint


def _pixels_rgb(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.uint8)
