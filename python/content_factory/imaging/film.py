"""Put the evidence of a camera back into a frame: grain, halation, a little lens falloff.

Why this exists, measured rather than felt. A sensor puts a noise floor on every pixel it records,
including a blurred sky; a diffusion model asked for a clean picture puts one nowhere. Over the
generated sets on this host, counting 16-px tiles whose high-pass residual has a standard deviation
under 0.6 (``qc.frame_review.dead_flat_fraction``):

    demo-pebble   anchor, `single-image` lane      95.4 % of tiles carry no texture at all
    w-iceberg     keyframe                         91.4 %
    ps2b-amber    anchor                           86.7 %
    ps2b-amber    after SeedVR2                    75.6 %   (the upscaler sharpens edges; it does
                                                             not add a noise floor, and on a
                                                             faceted subject it sharpens the
                                                             facets)

A photograph runs well under 10 %. That single number is most of what "it doesn't look real" means
before composition or anatomy come into it.

**This is a finisher, not a fix.** Grain on a faceted low-poly render is a grainy faceted low-poly
render. The prompt and the model are what decide whether the picture is of a photographable thing;
this decides whether the pixels carry the trace of having been photographed. Both matter and they
are not substitutes, which is why this is a separate, explicit step rather than something bolted
silently onto every generation.

Deterministic by construction: the grain field is generated from an explicit seed with numpy's
``default_rng``, so the same frame and the same settings give byte-identical output on any machine,
and the whole :class:`FilmResponse` can therefore sit in a cache key.
"""

from __future__ import annotations

import io

from pydantic import Field

from content_factory.schemas.base import SchemaModel


class FilmResponse(SchemaModel):
    """How much camera to put back. Every field is off at zero, so the identity is expressible."""

    grain: float = Field(default=0.012, ge=0.0, le=0.2)
    """Luma grain as a fraction of full scale, before the tonal weighting below.

    0.012 is about 3 levels of 8-bit at mid grey, which is the order of a clean modern sensor at
    base ISO — enough to give every tile a noise floor, not enough to read as "grainy". The default
    is chosen to move the dead-flat fraction, not to be visible."""
    chroma_grain: float = Field(default=0.4, ge=0.0, le=2.0)
    """Colour grain as a multiple of ``grain``. Under 1 because real chroma noise is lower
    amplitude than luma noise and more spatially correlated; equal amounts read as digital
    speckle."""
    shadow_weighting: float = Field(default=0.6, ge=0.0, le=1.0)
    """How much less grain lands in the deepest shadows and brightest highlights than in the
    midtones. Film grain follows exposure — the toe and the shoulder are quieter — and a uniform
    field over a black sky is the single most obvious tell of added noise."""
    halation: float = Field(default=0.10, ge=0.0, le=1.0)
    """Strength of the warm bloom bled around clipped highlights. This is light scattering back off
    the film base (or off a sensor's cover glass), and it is the thing whose absence makes a
    rendered specular look pasted on."""
    halation_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    """Luma above which a pixel is treated as a source of halation."""
    vignette: float = Field(default=0.06, ge=0.0, le=0.5)
    """Corner falloff as a fraction of centre brightness. Small: this is lens physics, not the
    Instagram filter, and anything a viewer notices as a vignette is too much."""
    seed: int = Field(default=0, ge=0)
    """The grain field's seed. Part of the record: two frames of one sequence must not share a
    grain field or the noise reads as a static overlay rather than as grain, so callers vary it per
    frame — and a rerun with the same seed reproduces the frame exactly."""


def apply_film_response(png: bytes, response: FilmResponse | None = None) -> bytes:
    """``png`` with a photographic response applied. Pure, deterministic, PNG in and PNG out."""
    import numpy as np
    from PIL import Image

    response = response or FilmResponse()
    image = Image.open(io.BytesIO(png)).convert("RGB")
    arr = np.asarray(image, dtype=np.float32) / 255.0

    if response.halation > 0:
        arr = _halate(arr, response)
    if response.vignette > 0:
        arr = _vignette(arr, response.vignette)
    if response.grain > 0:
        arr = _grain(arr, response, np)

    out = np.clip(arr * 255.0 + 0.5, 0, 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(out, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def _luma(arr):
    import numpy as np

    return np.tensordot(arr, np.array([0.2126, 0.7152, 0.0722], dtype="float32"), axes=([2], [0]))


def _halate(arr, response: FilmResponse):
    """Warm bloom bled out of the clipped highlights, screened back over the frame."""
    import numpy as np
    from PIL import Image, ImageFilter

    luma = _luma(arr)
    excess = np.clip(luma - response.halation_threshold, 0.0, None)
    if not excess.any():
        return arr
    excess = excess / max(float(excess.max()), 1e-6)
    radius = max(2.0, min(arr.shape[0], arr.shape[1]) / 220.0)
    blurred = (
        np.asarray(
            Image.fromarray((excess * 255).astype("uint8"), mode="L").filter(
                ImageFilter.GaussianBlur(radius)
            ),
            dtype=np.float32,
        )
        / 255.0
    )
    # Warm, because halation is scattering through the film base and the red layer sits deepest;
    # a neutral bloom reads as a rendering artefact rather than as light.
    tint = np.array([1.0, 0.72, 0.52], dtype="float32")
    glow = blurred[:, :, None] * tint[None, None, :] * response.halation
    return 1.0 - (1.0 - arr) * (1.0 - np.clip(glow, 0.0, 1.0))  # screen


def _vignette(arr, strength: float):
    import numpy as np

    h, w = arr.shape[:2]
    ys = (np.linspace(-1.0, 1.0, h, dtype="float32") ** 2)[:, None]
    xs = (np.linspace(-1.0, 1.0, w, dtype="float32") ** 2)[None, :]
    # cos^4 falloff is the textbook lens model; normalised so the centre is untouched.
    radial = np.sqrt(np.clip(ys + xs, 0.0, None) / 2.0)
    mask = 1.0 - strength * (radial**2)
    return arr * mask[:, :, None]


def _grain(arr, response: FilmResponse, np):
    """Exposure-weighted luma grain plus a weaker, softer chroma field."""
    rng = np.random.default_rng(response.seed)
    h, w = arr.shape[:2]
    luma = _luma(arr)
    # Film grain is loudest in the midtones and quiet in the toe and the shoulder. At weighting 0
    # this is flat; at 1 the extremes get none.
    exposure = 1.0 - response.shadow_weighting * (2.0 * np.abs(luma - 0.5)) ** 2
    mono = rng.normal(0.0, response.grain, size=(h, w)).astype("float32") * exposure
    out = arr + mono[:, :, None]
    if response.chroma_grain > 0:
        chroma = rng.normal(0.0, response.grain * response.chroma_grain, size=(h, w, 3)).astype(
            "float32"
        )
        # Zero-sum across channels so chroma noise shifts hue without shifting exposure — that is
        # what separates it from three independent luma fields, which read as RGB speckle.
        chroma -= chroma.mean(axis=2, keepdims=True)
        out = out + chroma * exposure[:, :, None]
    return np.clip(out, 0.0, 1.0)
