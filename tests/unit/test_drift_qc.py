from __future__ import annotations

from content_factory.qc.drift import check_av_drift
from content_factory.schemas import scenes
from content_factory.timeline.compiler import compile_timeline


def _measured_plan() -> scenes.StoryPlan:
    spans = [(0, 1900), (2000, 3900), (4000, 6000)]
    beats = tuple(
        scenes.VisualBeat(
            beat_id=f"beat_{i:09d}",
            order=i,
            display_text=f"Sentence {i} of the narration.",
            measured_start_ms=s,
            measured_end_ms=e,
        )
        for i, (s, e) in enumerate(spans)
    )
    scene_list = tuple(
        scenes.TitleScene(
            scene_id=f"scn_{i:09d}",
            beat_id=f"beat_{i:09d}",
            title=scenes.TextRef(text=f"Scene {i}"),
        )
        for i in range(len(spans))
    )
    return scenes.StoryPlan(
        plan_id="plan_drift00001",
        deliverable_id="dlv_drift000001",
        fps=30,
        width=1920,
        height=1080,
        beats=beats,
        scenes=scene_list,
    )


def test_compiled_timeline_has_zero_cue_drift() -> None:
    plan = _measured_plan()
    tl = compile_timeline(plan, timeline_id="tl_drift0000001", narrated=True)
    result = check_av_drift(tl, plan.beats)
    assert result.passed
    probes = result.facts["probes"]
    assert [p["drift_frames"] for p in probes] == [0, 0, 0]  # type: ignore[index]


def test_three_frame_drift_is_a_hard_failure() -> None:
    plan = _measured_plan()
    tl = compile_timeline(plan, timeline_id="tl_drift0000001", narrated=True)
    shifted = tl.model_copy(
        update={
            "audio": tuple(
                c if i == 0 else c.model_copy(update={"start_frame": c.start_frame + 3})
                for i, c in enumerate(tl.audio)
            )
        }
    )
    result = check_av_drift(shifted, plan.beats)
    assert not result.passed
    assert any(f.check == "drift" for f in result.findings)


def test_two_frame_drift_is_within_tolerance() -> None:
    plan = _measured_plan()
    tl = compile_timeline(plan, timeline_id="tl_drift0000001", narrated=True)
    shifted = tl.model_copy(
        update={
            "audio": tuple(
                c.model_copy(update={"start_frame": c.start_frame + 2}) if i == 2 else c
                for i, c in enumerate(tl.audio)
            )
        }
    )
    assert check_av_drift(shifted, plan.beats).passed


def test_long_narration_does_not_accumulate_rounding_drift() -> None:
    """Regression: 40 beats of 1234 ms (never frame-aligned at 30 fps). Per-span round-up used
    to push the cues ~1 frame late per beat; absolute placement keeps every probe within 1."""
    n, span = 40, 1234
    beats = tuple(
        scenes.VisualBeat(
            beat_id=f"beat_{i:09d}",
            order=i,
            display_text=f"Sentence {i} of a much longer narration.",
            measured_start_ms=i * span,
            measured_end_ms=i * span + 1100,
        )
        for i in range(n)
    )
    scene_list = tuple(
        scenes.TitleScene(
            scene_id=f"scn_{i:09d}",
            beat_id=f"beat_{i:09d}",
            title=scenes.TextRef(text=f"Scene {i}"),
        )
        for i in range(n)
    )
    plan = scenes.StoryPlan(
        plan_id="plan_drift00002",
        deliverable_id="dlv_drift000001",
        fps=30,
        width=1920,
        height=1080,
        beats=beats,
        scenes=scene_list,
        min_scene_ms=200,
    )
    tl = compile_timeline(plan, timeline_id="tl_drift0000002", narrated=True)
    result = check_av_drift(tl, plan.beats)
    assert result.passed
    assert all(p["drift_frames"] <= 1 for p in result.facts["probes"])  # type: ignore[index]


def test_unmeasured_beats_fail_honestly() -> None:
    plan = _measured_plan()
    tl = compile_timeline(plan, timeline_id="tl_drift0000001", narrated=True)
    silent = tuple(
        b.model_copy(update={"measured_start_ms": None, "measured_end_ms": None})
        for b in plan.beats
    )
    result = check_av_drift(tl, silent)
    assert not result.passed
    assert result.findings[0].check == "drift_unmeasured"
