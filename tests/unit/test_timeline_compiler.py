from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from content_factory.schemas.fixtures import sample_story_plan
from content_factory.schemas.scenes import WordTiming
from content_factory.timeline.compiler import TimelineError, compile_timeline, ms_to_frames


def test_planned_durations_compile_to_contiguous_integer_frames() -> None:
    plan = sample_story_plan()
    tl = compile_timeline(plan, timeline_id="tl_test00000001", narrated=False)
    assert tl.fps == 30 and tl.total_frames == sum(s.duration_frames for s in tl.scenes)
    # 4200+3800+6000+3000 ms → 126+114+180+90 frames, plus a 250 ms (8-frame) handle on the last.
    assert [s.duration_frames for s in tl.scenes] == [126, 114, 180, 98]
    assert tl.audio == ()
    assert 15 * 30 <= tl.total_frames <= 30 * 30
    again = compile_timeline(plan, timeline_id="tl_test00000001", narrated=False)
    assert again.content_hash() == tl.content_hash()


def test_narration_requested_but_unmeasured_refuses_to_freeze_timing() -> None:
    with pytest.raises(TimelineError, match="generate narration first"):
        compile_timeline(sample_story_plan(), timeline_id="tl_test00000002", narrated=True)


def test_measured_timings_drive_cuts_and_word_cues() -> None:
    plan = sample_story_plan()
    starts = [0, 4300, 8200, 14400]
    ends = [4100, 8000, 14000, 16900]
    beats = []
    for b, s, e in zip(plan.beats, starts, ends, strict=True):
        words = tuple(
            WordTiming(word=w, start_ms=s + k * 300, end_ms=s + k * 300 + 250)
            for k, w in enumerate(b.display_text.split()[:4])
        )
        beats.append(
            b.model_copy(update={"measured_start_ms": s, "measured_end_ms": e, "words": words})
        )
    plan = plan.model_copy(update={"beats": tuple(beats)})
    tl = compile_timeline(plan, timeline_id="tl_test00000003", narrated=True)
    # Cuts land on the next beat's speech start: 4300 ms → 129 frames, 3900 → 117, 6200 → 186, last 2500+250 → 83.
    assert [s.duration_frames for s in tl.scenes] == [129, 117, 186, 83]
    assert [a.start_frame for a in tl.audio] == [s.start_frame for s in tl.scenes]
    assert (
        tl.scenes[1].word_cues[0] == (0, "That") and tl.scenes[1].word_cues[1][0] == 9
    )  # 300 ms → 9 frames


@given(ms=st.integers(min_value=0, max_value=10_000_000), fps=st.sampled_from([24, 25, 30, 60]))
def test_ms_to_frames_is_integer_and_monotonic(ms: int, fps: int) -> None:
    f = ms_to_frames(ms, fps)
    assert isinstance(f, int)
    assert ms_to_frames(ms + 1000, fps) == f + fps
    assert ms_to_frames(ms, fps, round_up=True) >= f
