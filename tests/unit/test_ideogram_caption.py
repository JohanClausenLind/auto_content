"""Ideogram 4's caption format, where three separate things are counter-intuitive."""

from __future__ import annotations

import json

import pytest

from content_factory.prompting.ideogram import (
    GRID,
    IdeogramCaptionError,
    bbox,
    caption,
    obj,
    text,
)
from content_factory.schemas.sequences import Box


def test_the_box_is_y_first_not_x_first() -> None:
    """`[ymin, xmin, ymax, xmax]` — the opposite of every other box in this repo."""
    # Far left, near the top: x small, y small, wide, short.
    box = Box(x=0.10, y=0.20, w=0.30, h=0.40)
    assert bbox(box) == [200, 100, 600, 400]
    ymin, xmin, ymax, xmax = bbox(box)
    assert (ymin, ymax) == (200, 600), "y bounds must come first"
    assert (xmin, xmax) == (100, 400)


def test_the_grid_is_0_1000_on_both_axes_regardless_of_aspect() -> None:
    """Scaling to a 16:9 canvas's pixel height squashes every element into the top 58 %."""
    full = bbox(Box(x=0.0, y=0.0, w=1.0, h=1.0))
    assert full == [0, 0, GRID, GRID]


def test_an_object_carries_desc_and_a_text_element_carries_text() -> None:
    """`text` is LETTERED INTO the picture; prose there comes back drawn across the frame."""
    box = Box(x=0.2, y=0.2, w=0.5, h=0.5)
    thing = obj(box, "two adults in winter coats")
    assert thing["type"] == "obj"
    assert thing["desc"] == "two adults in winter coats"
    assert "text" not in thing, "an object must never carry a text field to render"

    sign = text(box, "OPEN", desc="painted shop lettering")
    assert sign["type"] == "text"
    assert sign["text"] == "OPEN"
    assert sign["desc"] == "painted shop lettering"


def test_the_keys_are_bbox_and_desc_not_bounding_box_and_description() -> None:
    thing = obj(Box(x=0.1, y=0.1, w=0.2, h=0.2), "a bicycle")
    assert set(thing) == {"type", "bbox", "desc"}


def test_style_description_key_order_is_load_bearing_for_a_photograph() -> None:
    """The model was trained with these keys in one order; another samples off-distribution."""
    doc = json.loads(
        caption(
            background="a city pavement",
            elements=[obj(Box(x=0.3, y=0.2, w=0.4, h=0.7), "two adults")],
            aesthetics="muted, lived-in",
            lighting="overcast daylight",
            photo="35 mm, fine grain",
            medium="photograph",
        )
    )
    assert list(doc["style_description"]) == ["aesthetics", "lighting", "photo", "medium"]


def test_an_illustration_uses_a_different_key_order_again() -> None:
    doc = json.loads(
        caption(
            background="a pale studio sweep",
            elements=[obj(Box(x=0.3, y=0.3, w=0.3, h=0.3), "a teapot")],
            aesthetics="flat, bold",
            lighting="even",
            art_style="flat vector illustration",
            palette=["#FF6B35"],
        )
    )
    assert list(doc["style_description"]) == [
        "aesthetics",
        "lighting",
        "medium",
        "art_style",
        "color_palette",
    ]


def test_element_key_order_differs_between_obj_and_text() -> None:
    box = Box(x=0.1, y=0.1, w=0.2, h=0.2)
    assert list(obj(box, "a lamp", palette=["#112233"])) == [
        "type",
        "bbox",
        "desc",
        "color_palette",
    ]
    assert list(text(box, "SALE", desc="vinyl letters")) == ["type", "bbox", "text", "desc"]


def test_photo_and_art_style_are_mutually_exclusive() -> None:
    with pytest.raises(IdeogramCaptionError, match="not both"):
        caption(
            background="x",
            elements=[],
            photo="35 mm",
            art_style="flat vector",
        )


def test_background_is_mandatory() -> None:
    """Nothing else in the caption describes the part of the frame no element covers."""
    with pytest.raises(IdeogramCaptionError, match="background is mandatory"):
        caption(background="", elements=[obj(Box(x=0.1, y=0.1, w=0.1, h=0.1), "a cup")])


def test_colours_are_normalised_to_the_one_spelling_the_format_accepts() -> None:
    """Ideogram rejects lowercase and three-digit shorthand; both name a colour unambiguously."""
    box = Box(x=0.1, y=0.1, w=0.1, h=0.1)
    assert obj(box, "a cup", palette=["#ff6b35"])["color_palette"] == ["#FF6B35"]
    assert obj(box, "a cup", palette=["#fff"])["color_palette"] == ["#FFFFFF"]
    assert obj(box, "a cup", palette=["#FF6B35"])["color_palette"] == ["#FF6B35"]


def test_something_that_is_not_a_colour_raises_rather_than_being_dropped() -> None:
    """Dropping it would leave a palette quietly shorter than the author wrote."""
    for bad in ("fff000", "#GGGGGG", "red", "#FF6B3"):
        with pytest.raises(IdeogramCaptionError, match="not a hex colour"):
            obj(Box(x=0.1, y=0.1, w=0.1, h=0.1), "a cup", palette=[bad])


def test_element_palettes_cap_at_five_and_image_palettes_at_sixteen() -> None:
    many = [f"#{i:02X}00FF" for i in range(20)]
    assert len(obj(Box(x=0.1, y=0.1, w=0.1, h=0.1), "a cup", palette=many)["color_palette"]) == 5
    doc = json.loads(
        caption(
            background="a wall",
            elements=[],
            aesthetics="a",
            lighting="b",
            photo="c",
            palette=many,
        )
    )
    assert len(doc["style_description"]["color_palette"]) == 16


def test_only_compositional_deconstruction_is_required() -> None:
    doc = json.loads(caption(background="a wall", elements=[]))
    assert set(doc) == {"compositional_deconstruction"}
    assert doc["compositional_deconstruction"]["background"] == "a wall"


def test_elements_keep_the_order_they_were_given() -> None:
    """The node's own instruction: background-most element first, foreground last."""
    far = obj(Box(x=0.0, y=0.0, w=1.0, h=0.5), "a distant row of shopfronts")
    near = obj(Box(x=0.3, y=0.2, w=0.4, h=0.7), "two adults in winter coats")
    doc = json.loads(caption(background="a street", elements=[far, near]))
    descs = [e["desc"] for e in doc["compositional_deconstruction"]["elements"]]
    assert descs == ["a distant row of shopfronts", "two adults in winter coats"]
