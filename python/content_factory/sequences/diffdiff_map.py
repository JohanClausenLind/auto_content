"""The Differential Diffusion change map: which pixels Ideogram 4 may repaint, and how freely."""

from __future__ import annotations

import io
from collections.abc import Sequence

import numpy as np
from PIL import Image, ImageFilter

from content_factory.schemas.sequences import Box

TOKEN_STRIDE = 16
"""``vae_scale_factor`` (8) * ``patch_size`` (2) for Ideogram 4's Flux2 VAE.

Read off ``Ideogram4DiffDiffPrepareLatentsStep.__call__``, which computes the change map's grid as
``height // (components.vae_scale_factor * patch)``. If a future checkpoint changes either number
this constant is the one thing to update, and :func:`token_grid` is how callers should ask.
"""

KEEP = 255
"""White: hold this pixel to the reference for the whole schedule."""

REGENERATE = 0
"""Black: release this pixel at step 0 and denoise it freely."""


class DiffDiffMapError(ValueError):
    """The change map could not be built as specified."""


def token_grid(width: int, height: int) -> tuple[int, int]:
    """The grid the map is actually decided on. Anything finer than one cell is invisible."""
    return (max(1, width // TOKEN_STRIDE), max(1, height // TOKEN_STRIDE))


def soft_map(
    *,
    width: int,
    height: int,
    regenerate: Sequence[Box],
    feather_tokens: float = 2.0,
    hold: float = 0.0,
    outside: float = 1.0,
) -> bytes:
    """A greyscale PNG change map: ``regenerate`` boxes painted black, everything else white."""
    if width <= 0 or height <= 0:
        msg = f"size must be positive, got {width}x{height}"
        raise DiffDiffMapError(msg)
    for name, value in (("hold", hold), ("outside", outside)):
        if not 0.0 <= value <= 1.0:
            msg = f"{name} must be in 0..1, got {value}"
            raise DiffDiffMapError(msg)
    if hold >= outside:
        # A silent no-op otherwise: the run pays its full 40 s a frame and returns the input
        # unchanged, because no token is ever released (journal 2026-09-15).
        msg = f"hold ({hold}) must be below outside ({outside}) or nothing regenerates"
        raise DiffDiffMapError(msg)
    if not regenerate:
        msg = "no regions to regenerate: a map that keeps everything is a no-op inpaint"
        raise DiffDiffMapError(msg)

    canvas = np.full((height, width), float(outside), dtype=np.float32)
    for box in regenerate:
        x0 = max(0, min(width, round(box.x * width)))
        y0 = max(0, min(height, round(box.y * height)))
        x1 = max(0, min(width, round((box.x + box.w) * width)))
        y1 = max(0, min(height, round((box.y + box.h) * height)))
        if x1 <= x0 or y1 <= y0:
            msg = f"box {box!r} is empty at {width}x{height}"
            raise DiffDiffMapError(msg)
        canvas[y0:y1, x0:x1] = float(hold)

    image = Image.fromarray(np.clip(canvas * 255.0, 0, 255).astype(np.uint8), mode="L")
    if feather_tokens > 0:
        image = image.filter(ImageFilter.GaussianBlur(radius=feather_tokens * TOKEN_STRIDE / 2))
    return to_png(image)


def from_mask_png(
    mask_png: bytes,
    *,
    invert: bool = True,
    feather_tokens: float = 2.0,
    hold: float = 0.0,
    outside: float = 1.0,
) -> bytes:
    """Convert a conventional inpaint mask into a change map."""
    source = Image.open(io.BytesIO(mask_png)).convert("L")
    values = np.asarray(source, dtype=np.float32) / 255.0
    if invert:
        values = 1.0 - values
    # A conventional mask is binary; rescale it into [hold, outside] so the same knobs work
    # whether the region came from a Box or from a painted PNG.
    values = float(hold) + values * (float(outside) - float(hold))
    image = Image.fromarray(np.clip(values * 255.0, 0, 255).astype(np.uint8), mode="L")
    if feather_tokens > 0:
        image = image.filter(ImageFilter.GaussianBlur(radius=feather_tokens * TOKEN_STRIDE / 2))
    return to_png(image)


def to_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("L").save(buf, format="PNG")
    return buf.getvalue()


def describe(map_png: bytes) -> dict[str, float]:
    """What a map will actually do, as numbers — for logs and for the review gate."""
    image = Image.open(io.BytesIO(map_png)).convert("L")
    grid_w, grid_h = token_grid(*image.size)
    cells = (
        np.asarray(image.resize((grid_w, grid_h), Image.Resampling.BILINEAR), dtype=np.float32)
        / 255.0
    )
    return {
        "regenerated": float((cells < 0.5).mean()),
        "untouched": float((cells >= 0.999).mean()),
        "mean": float(cells.mean()),
        "grid_w": float(grid_w),
        "grid_h": float(grid_h),
    }


def composite(original_png: bytes, painted_png: bytes, map_png: bytes) -> bytes:
    """Paste ``original_png`` back where the map keeps, so the untouched part stops drifting."""
    # Diffdiff anchors LATENTS, not pixels: the VAE round-trip alone moved 13.5 % of the frozen
    # background by >8/255, and compositing takes that to 0.2 % (journal 2026-09-15).
    base = Image.open(io.BytesIO(original_png)).convert("RGB")
    painted = Image.open(io.BytesIO(painted_png)).convert("RGB")
    keep = Image.open(io.BytesIO(map_png)).convert("L")
    if painted.size != base.size:
        painted = painted.resize(base.size)
    if keep.size != base.size:
        keep = keep.resize(base.size)
    # The map is the alpha directly: white keeps the original, black takes the repaint, and the
    # feathered ramp cross-fades so the join is the same soft edge the sampler already used.
    arr_b = np.asarray(base, dtype=np.float32)
    arr_p = np.asarray(painted, dtype=np.float32)
    alpha = (np.asarray(keep, dtype=np.float32) / 255.0)[..., None]
    mixed = arr_b * alpha + arr_p * (1.0 - alpha)
    out = Image.fromarray(np.clip(mixed, 0, 255).astype(np.uint8), mode="RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
