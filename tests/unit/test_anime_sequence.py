"""The one-minute limited-animation film: its arithmetic, and its consistency mechanism."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def test_the_film_is_exactly_one_minute() -> None:
    from anime_sequence import FPS, HOLDS, SHOTS, SLOTS_PER_SHOT

    assert sum(HOLDS) == SLOTS_PER_SHOT, "the hold pattern must fill a shot exactly"
    slots = len(SHOTS) * SLOTS_PER_SHOT
    assert slots == 300
    assert slots / FPS == 60.0


def test_limited_animation_really_is_fewer_drawings_than_frames() -> None:
    """The whole reason this is affordable: 120 drawings, not 300."""
    from anime_sequence import HOLDS, SHOTS, SLOTS_PER_SHOT

    drawings = len(SHOTS) * len(HOLDS)
    assert drawings == 120
    assert drawings < len(SHOTS) * SLOTS_PER_SHOT
    assert set(HOLDS) == {2, 3}, "on twos and threes"


BOXES = {"woman": [250, 150, 450, 950], "man": [550, 150, 750, 950]}


def test_each_character_gets_its_own_element_and_its_own_sheet() -> None:
    """Ideogram 4 takes no image input, so this text is the entire character sheet."""
    from anime_sequence import KAEDE, SHOTS, TORU, caption

    doc = json.loads(caption(SHOTS[0], 0, BOXES))
    elements = doc["compositional_deconstruction"]["elements"]
    assert len(elements) == 2
    assert KAEDE in elements[0]["description"]
    assert TORU in elements[1]["description"]
    assert elements[0]["bounding_box"] == BOXES["woman"]


def test_no_element_describes_an_interaction() -> None:
    """The bug this prevents, and it is not a counting bug."""
    from anime_sequence import SHOTS, caption

    for shot in SHOTS:
        doc = json.loads(caption(shot, 0, BOXES))
        assert "two people" in doc["high_level_description"]
        for element in doc["compositional_deconstruction"]["elements"]:
            said = element["description"].lower()
            for word in (
                "together",
                "each other",
                "hand in hand",
                "with him",
                "with her",
                "towards her",
                "towards him",
                "near her shoulder",
                "near his shoulder",
                "the other",
            ):
                assert word not in said, f"{shot['clip']}: element implies a partner ({word!r})"


def test_within_one_shot_only_the_action_and_the_box_change() -> None:
    """Everything else being byte-identical is the consistency mechanism, so assert it is."""
    from anime_sequence import HOLDS, SHOTS, caption

    first = json.loads(caption(SHOTS[0], 0, BOXES))
    last = json.loads(caption(SHOTS[0], len(HOLDS) - 1, BOXES))
    assert first["style_description"] == last["style_description"]
    assert (
        first["compositional_deconstruction"]["background"]
        == last["compositional_deconstruction"]["background"]
    )
    # And the action clause did move, or the shot is a still.
    assert first["high_level_description"] != last["high_level_description"]


def test_the_boxes_are_sent_on_ideograms_scale_not_in_pixels() -> None:
    """0-1000 on both axes. Scaling to the pixel height squashes every box into the top 58 %."""
    from anime_sequence import BOX_SCALE, HEIGHT

    assert BOX_SCALE == 1000
    assert BOX_SCALE != HEIGHT


def test_every_shot_is_one_world() -> None:
    """Twelve cuts along one coast path: a cut should change the view, not the place."""
    from anime_sequence import SHOTS, WORLD

    assert len({s["clip"] for s in SHOTS}) == len(SHOTS), "no take is used twice"
    for shot in SHOTS:
        assert shot["bg"].startswith(WORLD)
        assert len(shot["solo"]) == 2, f"{shot['clip']} needs a solo posture per character"
