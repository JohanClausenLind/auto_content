from __future__ import annotations

import pytest

from content_factory.style.editorial import (
    EDITORIAL_KIT,
    VIDEO_PROMPT_MAX_WORDS,
    clip_seconds_ok,
    image_prompt,
    video_prompt,
)


def test_kit_palette_and_motion_rules_match_the_channel_identity() -> None:
    kit = EDITORIAL_KIT
    assert (kit.background, kit.ink) == ("#08111F", "#F4F1E8")
    assert (kit.accent, kit.accent_warm) == ("#47D7FF", "#FFB547")
    assert (kit.success, kit.danger) == ("#66E39A", "#FF5C6C")
    assert (kit.motion.transition_overlap_frames.lo, kit.motion.transition_overlap_frames.hi) == (
        6,
        12,
    )
    assert kit.motion.photosensitive_safe is True
    assert (kit.grid_columns, kit.spacing_px) == (12, 8)


def test_image_prompt_appends_the_compositing_suffix() -> None:
    p = image_prompt("a cross-section of an undersea cable on the ocean floor")
    assert p.startswith("a cross-section of an undersea cable")
    assert "no typography, no labels" in p and "motion-graphics compositing" in p
    with pytest.raises(ValueError, match="subject"):
        image_prompt("   ")


def test_video_prompt_is_one_bounded_paragraph_with_every_component() -> None:
    p = video_prompt(
        action="A repeater housing settles onto the seabed",
        object_motion="sediment lifts and drifts sideways in a slow plume",
        appearance="matte off-white cylinder with cyan seam lines",
        environment="deep navy water, faint marine snow",
        camera="static wide shot, slight low angle",
        lighting="single cool key from above, physically plausible falloff",
        end_state="the housing rests level and the plume dissipates",
    )
    assert "\n" not in p and p.count(".") >= 7
    assert len(p.split()) <= VIDEO_PROMPT_MAX_WORDS
    with pytest.raises(ValueError, match="camera"):
        video_prompt(
            action="a",
            object_motion="b",
            appearance="c",
            environment="d",
            camera="  ",
            lighting="f",
            end_state="g",
        )
    with pytest.raises(ValueError, match="limit is 200"):
        video_prompt(
            action="word " * 220,
            object_motion="b",
            appearance="c",
            environment="d",
            camera="e",
            lighting="f",
            end_state="g",
        )


def test_generated_clips_stay_short() -> None:
    assert clip_seconds_ok(2) and clip_seconds_ok(8)
    assert not clip_seconds_ok(1.5) and not clip_seconds_ok(9)
