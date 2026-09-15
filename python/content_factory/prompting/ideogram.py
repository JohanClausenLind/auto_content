"""Ideogram 4's structured caption, built so the key ORDER is right."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from content_factory.schemas.sequences import Box

GRID = 1000
"""Both axes are normalised to 0-1000, independent of the canvas aspect."""

MAX_IMAGE_COLOURS = 16
MAX_ELEMENT_COLOURS = 5

_HEX = re.compile(r"^#(?:[0-9A-F]{3}|[0-9A-F]{6})$")


class IdeogramCaptionError(ValueError):
    """The caption could not be built as specified."""


def _palette(colours: Sequence[str], limit: int) -> list[str]:
    """Normalise to the only spelling the format accepts: uppercase, six digits."""
    out: list[str] = []
    for colour in colours[:limit]:
        value = colour.strip().upper()
        if not _HEX.match(value):
            msg = f"colour {colour!r} is not a hex colour, e.g. '#FF6B35'"
            raise IdeogramCaptionError(msg)
        if len(value) == 4:  # #RGB -> #RRGGBB
            value = "#" + "".join(c * 2 for c in value[1:])
        out.append(value)
    return out


def bbox(box: Box) -> list[int]:
    """A repo ``Box`` (x, y, w, h relative) as Ideogram's ``[ymin, xmin, ymax, xmax]`` of 1000."""

    def grid(value: float) -> int:
        return max(0, min(GRID, round(value * GRID)))

    return [grid(box.y), grid(box.x), grid(box.y + box.h), grid(box.x + box.w)]


def obj(box: Box, desc: str, *, palette: Sequence[str] = ()) -> dict[str, Any]:
    """A thing in the picture. ``desc`` is described, never lettered."""
    element: dict[str, Any] = {"type": "obj", "bbox": bbox(box), "desc": desc}
    colours = _palette(palette, MAX_ELEMENT_COLOURS)
    if colours:
        element["color_palette"] = colours
    return element


def text(box: Box, string: str, *, desc: str = "", palette: Sequence[str] = ()) -> dict[str, Any]:
    """Words to be RENDERED INTO the image. ``desc`` describes the lettering, not the content."""
    element: dict[str, Any] = {"type": "text", "bbox": bbox(box), "text": string, "desc": desc}
    colours = _palette(palette, MAX_ELEMENT_COLOURS)
    if colours:
        element["color_palette"] = colours
    return element


def caption(
    *,
    background: str,
    elements: Sequence[dict[str, Any]],
    high_level_description: str = "",
    aesthetics: str = "",
    lighting: str = "",
    medium: str = "",
    photo: str = "",
    art_style: str = "",
    palette: Sequence[str] = (),
) -> str:
    """The caption as a JSON string, keys in the order the model was trained on."""
    if photo and art_style:
        msg = "pass photo or art_style, not both: they are different style_description shapes"
        raise IdeogramCaptionError(msg)
    if not background:
        msg = "background is mandatory: it is the only part of the scene no element describes"
        raise IdeogramCaptionError(msg)

    doc: dict[str, Any] = {}
    if high_level_description:
        doc["high_level_description"] = high_level_description

    if photo or art_style:
        style: dict[str, Any] = {"aesthetics": aesthetics, "lighting": lighting}
        if photo:
            style["photo"] = photo
            style["medium"] = medium or "photograph"
        else:
            style["medium"] = medium or "illustration"
            style["art_style"] = art_style
        colours = _palette(palette, MAX_IMAGE_COLOURS)
        if colours:
            style["color_palette"] = colours
        doc["style_description"] = style

    doc["compositional_deconstruction"] = {"background": background, "elements": list(elements)}
    return json.dumps(doc, indent=1)
