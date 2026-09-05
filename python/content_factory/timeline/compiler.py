"""Timeline compiler (2.2, 2.3): measured milliseconds in, integer frames out.

Order of truth: narration timings (measured) → planned durations (silent deliverables). Scene
durations are never frozen before narration exists when narration is requested.
"""

from __future__ import annotations

from content_factory.schemas.base import OpaqueId
from content_factory.schemas.scenes import (
    AudioCue,
    CompiledScene,
    CompiledTimeline,
    StoryPlan,
    VisualBeat,
)

COMPILER_VERSION = "0.2.0"  # 0.2.0: narrated boundaries at absolute frames (no cumulative drift)


class TimelineError(Exception):
    pass


def ms_to_frames(ms: int, fps: int, *, round_up: bool = False) -> int:
    """Integer frames from integer ms; round to nearest (or up) — never floats downstream."""
    num = ms * fps
    if round_up:
        return (num + 999) // 1000
    return (num + 500) // 1000


def _beat_span_ms(beat: VisualBeat, *, require_measured: bool) -> tuple[int, int]:
    if beat.measured_start_ms is not None and beat.measured_end_ms is not None:
        if beat.measured_end_ms <= beat.measured_start_ms:
            raise TimelineError(f"beat {beat.beat_id}: measured end before start")
        return beat.measured_start_ms, beat.measured_end_ms
    if require_measured:
        raise TimelineError(
            f"beat {beat.beat_id}: narration requested but no measured timing yet — generate narration first"  # noqa: E501
        )
    if beat.planned_duration_ms is None:
        raise TimelineError(f"beat {beat.beat_id}: no measured or planned duration")
    return -1, beat.planned_duration_ms  # start resolved sequentially


def compile_timeline(plan: StoryPlan, *, timeline_id: OpaqueId, narrated: bool) -> CompiledTimeline:
    fps = plan.fps
    handle_f = ms_to_frames(plan.handle_ms, fps)
    min_f = ms_to_frames(plan.min_scene_ms, fps, round_up=True)
    beats = sorted(plan.beats, key=lambda b: b.order)
    scenes_by_beat: dict[str, list] = {}
    for s in plan.scenes:
        scenes_by_beat.setdefault(s.beat_id, []).append(s)

    compiled: list[CompiledScene] = []
    audio: list[AudioCue] = []
    cursor = 0
    t0: int | None = None
    for i, beat in enumerate(beats):
        start_ms, end_ms = _beat_span_ms(beat, require_measured=narrated)
        if narrated:
            # Measured: the scene holds until the next beat's speech starts (plus a handle at the
            # end of the last beat), so cuts land on speech boundaries, never mid-word. Boundaries
            # are placed at ABSOLUTE frame indices (round(measured_ms - t0)) rather than by
            # accumulating per-span round-ups: the narration stem sits at absolute milliseconds,
            # so accumulated rounding would drift the cues off the voice by ~1 frame per beat.
            if t0 is None:
                t0 = start_ms
            nxt = beats[i + 1] if i + 1 < len(beats) else None
            speech_frames = ms_to_frames(end_ms - start_ms, fps, round_up=True)
            audio.append(
                AudioCue(
                    beat_id=beat.beat_id, start_frame=cursor, duration_frames=max(1, speech_frames)
                )
            )
            if nxt is not None and nxt.measured_start_ms is not None:
                boundary = ms_to_frames(nxt.measured_start_ms - t0, fps)
            else:
                boundary = ms_to_frames(end_ms - t0, fps) + handle_f
            # The narration audio is the clock: never clamp to min_scene_ms here, or the
            # cursor overshoots the absolute boundary and every later cue (and cut) lands
            # off the voice — which the av_drift QC then fails as a blocker. min_scene_ms
            # remains a planning guard for the silent path below.
            duration = max(1, boundary - cursor)
        else:
            duration = max(min_f, ms_to_frames(end_ms, fps, round_up=True))
            if i == len(beats) - 1:
                duration += handle_f
        scene_list = scenes_by_beat.get(beat.beat_id, [])
        if not scene_list:
            raise TimelineError(f"beat {beat.beat_id} has no scene")
        # Multiple scenes on one beat split its duration evenly (integer remainder to the last).
        per = duration // len(scene_list)
        for j, scene in enumerate(scene_list):
            d = per if j < len(scene_list) - 1 else duration - per * (len(scene_list) - 1)
            cues: list[tuple[int, str]] = []
            if narrated and len(scene_list) == 1:
                for w in beat.words:
                    cues.append((ms_to_frames(w.start_ms - start_ms, fps), w.word))
            compiled.append(
                CompiledScene(
                    scene_id=scene.scene_id,
                    beat_id=beat.beat_id,
                    start_frame=cursor,
                    duration_frames=d,
                    word_cues=tuple(cues),
                )
            )
            cursor += d
    return CompiledTimeline(
        timeline_id=timeline_id,
        plan_id=plan.plan_id,
        fps=fps,
        width=plan.width,
        height=plan.height,
        total_frames=cursor,
        scenes=tuple(compiled),
        audio=tuple(audio),
        compiler_version=COMPILER_VERSION,
        plan_hash=plan.content_hash(),
    )
