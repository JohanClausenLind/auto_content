"""Guides are measured, and drift thresholds are per style and per camera.

`LTXVAddGuide` pins an anchor at a frame index and **nothing checked that the clip went anywhere
near it**: a guide whose strength was too low, or whose index the 8k+1 length rule snapped past the
end of the clip, produced exactly the same "ok" as one the model honoured. The thresholds in
`sequences/drift.py` are calibrated against the first live guided run (2026-09-09) and the numbers
are in that module's docstrings; these tests hold the behaviour those numbers were chosen for.

Deliberately offline: PNGs are drawn here with Pillow, so the suite needs no clip, no ComfyUI and
no GPU.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from content_factory.config.settings import DriftThresholds, DriftThresholdSet
from content_factory.sequences.drift import (
    GUIDE_SIMILARITY_MIN,
    GUIDE_STRUCTURAL_MIN,
    UNCALIBRATED,
    guide_adherence,
    structural_similarity,
)
from content_factory.sequences.styles import STYLE_PRESETS, style_name_for


def _png(fill: tuple[int, int, int], *, box: tuple[int, int, int, int] | None = None) -> bytes:
    img = Image.new("RGB", (128, 72), fill)
    if box is not None:
        ImageDraw.Draw(img).rectangle(box, fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


LEFT = _png((200, 200, 195), box=(8, 8, 40, 64))
RIGHT = _png((200, 200, 195), box=(88, 8, 120, 64))
PLAIN = _png((200, 200, 195))


def test_a_clip_that_hits_its_guide_passes() -> None:
    result = guide_adherence({48: LEFT}, {48: LEFT})
    assert result.passed
    facts = result.as_facts()
    assert facts["worst"] == 1.0 and facts["structural_worst"] == 1.0
    assert facts["reasons"] == []


def test_the_same_content_in_the_wrong_place_fails_on_arrangement() -> None:
    """The failure the luminance measure alone cannot see: identical ink, mirrored.

    Both frames are the same off-white ground with the same dark block, so they have almost the
    same luminance histogram and almost the same mean absolute difference. Only the block
    correlation notices that the subject moved from one side to the other.
    """
    result = guide_adherence({48: RIGHT}, {48: LEFT})
    assert not result.passed
    reasons = " ".join(r for report in result.reports for r in report.reasons)
    assert "not arranged like the guide" in reasons
    assert result.structural[0] < GUIDE_STRUCTURAL_MIN


def test_a_guide_index_past_the_end_of_the_clip_is_a_named_finding() -> None:
    """The 8k+1 snapping failure: a guide pinned at a frame the clip does not have. It used to be
    invisible — the request carried the guide, the clip came back, nothing compared them."""
    result = guide_adherence({0: LEFT}, {96: LEFT})
    assert not result.passed
    assert result.as_facts()["reasons"] == ["no frame 96 in the clip"]
    assert result.as_facts()["worst"] == 0.0


def test_no_guides_is_not_a_failure() -> None:
    """Most clips have none: an unguided i2v request is the normal case."""
    result = guide_adherence({0: LEFT}, {})
    assert result.passed
    assert result.as_facts() == {
        "guides": 0,
        "passed": True,
        "similarity": [],
        "worst": 1.0,
        "structural": [],
        "structural_worst": 1.0,
        "reasons": [],
    }


def test_structural_similarity_is_symmetric_and_bounded() -> None:
    assert structural_similarity(LEFT, LEFT) == 1.0
    assert structural_similarity(LEFT, RIGHT) == structural_similarity(RIGHT, LEFT)
    for a, b in ((LEFT, RIGHT), (LEFT, PLAIN), (PLAIN, RIGHT)):
        assert 0.0 <= structural_similarity(a, b) <= 1.0


def test_a_flat_frame_against_a_structured_one_scores_zero_not_one() -> None:
    """A frame with no arrangement has no correlation to report, and calling that "identical"
    would hide the worst failure there is: a clip that came back as an empty grey field."""
    assert structural_similarity(PLAIN, PLAIN) == 1.0
    assert structural_similarity(PLAIN, LEFT) == 0.0


def test_the_calibrated_bars_sit_between_the_measured_values() -> None:
    """The live run measured 0.972 at the guide frame and 0.887 one third of a second earlier, and
    0.998 / 0.960 structurally. Both bars have to fall inside those gaps or they mean nothing."""
    assert 0.887 < GUIDE_SIMILARITY_MIN < 0.972
    assert 0.960 < GUIDE_STRUCTURAL_MIN < 0.998


def test_drift_thresholds_layer_per_field() -> None:
    thresholds = DriftThresholds(
        by_camera={"slow_push_in": DriftThresholdSet(locked_region_similarity=0.70)},
        by_style={"watercolour": DriftThresholdSet(style_delta_max=0.40)},
    )
    assert thresholds.resolve() == (0.92, 0.15)
    # A camera move has an opinion about composition and says nothing about style, and vice versa;
    # an override that had to restate the other's number would go stale silently.
    assert thresholds.resolve(camera="slow_push_in") == (0.70, 0.15)
    assert thresholds.resolve(style="watercolour") == (0.92, 0.40)
    assert thresholds.resolve(camera="slow_push_in", style="watercolour") == (0.70, 0.40)


def test_style_wins_over_camera_when_both_set_the_same_field() -> None:
    """A camera move is a fact about the shot; a style is a decision about the film."""
    thresholds = DriftThresholds(
        by_camera={"pan_left": DriftThresholdSet(locked_region_similarity=0.50)},
        by_style={"oil": DriftThresholdSet(locked_region_similarity=0.60)},
    )
    assert thresholds.resolve(camera="pan_left", style="oil") == (0.60, 0.15)


def test_an_unknown_style_or_camera_takes_the_defaults() -> None:
    thresholds = DriftThresholds(by_style={"oil": DriftThresholdSet(style_delta_max=0.9)})
    assert thresholds.resolve(style="not_a_style", camera="not_a_camera") == (0.92, 0.15)


def test_the_override_tables_start_empty() -> None:
    """An override has to come from a measured run. Shipping invented per-style numbers would be
    the hardcoded pair again, with more places to look for it."""
    default = DriftThresholds()
    assert default.by_style == {} and default.by_camera == {}


def test_a_style_prompt_resolves_back_to_its_preset_name() -> None:
    for name, prompt in STYLE_PRESETS.items():
        assert style_name_for(prompt) == name
    # A hand-written prompt has no name and therefore no override: nobody has measured it.
    assert style_name_for("photorealistic, warm golden-hour light, tender documentary feel") == ""
    assert style_name_for("") == ""


def test_there_is_exactly_one_uncalibrated_profile() -> None:
    """It used to be two magic floats inline in scripts/generate_holding_hands.py."""
    locked, delta = UNCALIBRATED
    assert 0.0 < locked < DriftThresholds().locked_region_similarity
    assert delta > DriftThresholds().style_delta_max


@pytest.mark.parametrize("size", [(64, 64), (200, 100)])
def test_a_guide_of_a_different_size_than_the_frame_is_a_finding(size: tuple[int, int]) -> None:
    """A resized clip against an unresized anchor is a mistake worth naming, not a comparison."""
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 200, 195)).save(buf, "PNG")
    result = guide_adherence({0: buf.getvalue()}, {0: LEFT})
    assert not result.passed
    assert any("differs from anchor" in r for report in result.reports for r in report.reasons)
