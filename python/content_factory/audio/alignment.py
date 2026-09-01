"""Alignment validation (15): monotonicity, overlaps, missing words, gaps, duration mismatch."""

from __future__ import annotations

from content_factory.audio.normalize import tokenize_words
from content_factory.schemas.audio import AlignmentFinding, AlignmentReport, NarrationSegment

MAX_GAP_MS = 1500
DURATION_TOLERANCE_MS = 400


def validate_alignment(segment: NarrationSegment) -> AlignmentReport:
    findings: list[AlignmentFinding] = []
    script = tokenize_words(segment.spoken_text)
    words = segment.words
    if not words:
        return AlignmentReport(
            beat_id=segment.beat_id,
            findings=(
                AlignmentFinding(check="empty", severity="blocker", message="no word timings"),
            ),
            coverage=0.0,
        )
    prev_end = 0
    for i, w in enumerate(words):
        if w.start_ms < prev_end:
            sev = "critical" if prev_end - w.start_ms > 40 else "minor"
            findings.append(
                AlignmentFinding(
                    check="overlap",
                    severity=sev,
                    message=f"word {i} ({w.word!r}) starts {prev_end - w.start_ms} ms before the previous word ends",  # noqa: E501
                    word_index=i,
                )
            )  # type: ignore[arg-type]
        if i > 0 and w.start_ms < words[i - 1].start_ms:
            findings.append(
                AlignmentFinding(
                    check="monotonic",
                    severity="critical",
                    message=f"word {i} starts before word {i - 1}",
                    word_index=i,
                )
            )
        if i > 0 and w.start_ms - prev_end > MAX_GAP_MS:
            findings.append(
                AlignmentFinding(
                    check="gap",
                    severity="major",
                    message=f"{w.start_ms - prev_end} ms silence before word {i} ({w.word!r})",
                    word_index=i,
                )
            )
        prev_end = max(prev_end, w.end_ms)
    lower_script = [s.lower() for s in script]
    lower_words = [w.word.lower() for w in words]
    if len(lower_words) < len(lower_script):
        missing = len(lower_script) - len(lower_words)
        findings.append(
            AlignmentFinding(
                check="missing_words",
                severity="critical" if missing > max(1, len(lower_script) // 10) else "major",
                message=f"{missing} script word(s) have no timing",
            )
        )
    elif len(lower_words) > len(lower_script):
        findings.append(
            AlignmentFinding(
                check="extra_words",
                severity="major",
                message=f"{len(lower_words) - len(lower_script)} timed word(s) not in the script",
            )
        )
    if abs(segment.duration_ms - words[-1].end_ms) > DURATION_TOLERANCE_MS + 1500:
        findings.append(
            AlignmentFinding(
                check="duration_mismatch",
                severity="major",
                message=f"last word ends at {words[-1].end_ms} ms but audio is {segment.duration_ms} ms",  # noqa: E501
            )
        )
    coverage = min(1.0, len(lower_words) / max(1, len(lower_script)))
    return AlignmentReport(beat_id=segment.beat_id, findings=tuple(findings), coverage=coverage)
