"""A/V cue drift QC: measured narration vs the compiled timeline, probed start/middle/end.

The narration audio is the clock. After a timeline is compiled from measured word timings,
every audio cue must sit where the measurement says the speech starts. Drift beyond two frames
is a hard failure (blocker), not a warning — downstream captions and label cues inherit it.
"""

from __future__ import annotations

from collections.abc import Sequence

from content_factory.qc.media import Finding, QCResult, Severity
from content_factory.schemas.scenes import CompiledTimeline, VisualBeat
from content_factory.timeline.compiler import ms_to_frames

MAX_DRIFT_FRAMES = 2


def check_av_drift(
    timeline: CompiledTimeline,
    beats: Sequence[VisualBeat],
    *,
    max_drift_frames: int = MAX_DRIFT_FRAMES,
) -> QCResult:
    """Compare each probed beat's measured speech start against its compiled audio cue."""
    findings: list[Finding] = []
    measured = [b for b in sorted(beats, key=lambda b: b.order) if b.measured_start_ms is not None]
    if not measured:
        return QCResult(
            (Finding("drift_unmeasured", Severity.critical, "no measured beats to check"),),
            {"probes": []},
        )
    cues = {c.beat_id: c for c in timeline.audio}
    t0 = measured[0].measured_start_ms or 0
    probe_indexes = sorted({0, len(measured) // 2, len(measured) - 1})
    probes: list[dict[str, object]] = []
    for idx in probe_indexes:
        beat = measured[idx]
        cue = cues.get(beat.beat_id)
        if cue is None:
            findings.append(
                Finding(
                    "drift_missing_cue",
                    Severity.blocker,
                    f"beat {beat.beat_id} has measured speech but no audio cue in the timeline",
                )
            )
            continue
        expected = ms_to_frames((beat.measured_start_ms or 0) - t0, timeline.fps)
        drift = abs(cue.start_frame - expected)
        probes.append(
            {
                "beat_id": beat.beat_id,
                "expected_frame": expected,
                "cue_frame": cue.start_frame,
                "drift_frames": drift,
            }
        )
        if drift > max_drift_frames:
            findings.append(
                Finding(
                    "drift",
                    Severity.blocker,
                    f"beat {beat.beat_id}: cue at frame {cue.start_frame}, speech at "
                    f"{expected} — drift {drift} > {max_drift_frames} frames",
                )
            )
    return QCResult(tuple(findings), {"probes": probes, "fps": timeline.fps})
