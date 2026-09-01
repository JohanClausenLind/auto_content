"""Caption compiler: measured words → documentary-style cues → SRT and WebVTT."""

from __future__ import annotations

from content_factory.schemas.audio import CaptionCue, CaptionTrack
from content_factory.schemas.scenes import WordTiming

MIN_CUE_MS = 1000
MAX_CUE_MS = 6000
MAX_LINES = 2


def _wrap(words: list[str], max_chars: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = (cur + " " + w).strip()
        if len(cand) > max_chars and cur:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def compile_captions(
    deliverable_id: str,
    timed_words: list[WordTiming],
    *,
    language: str = "en",
    max_chars_per_line: int = 32,
) -> CaptionTrack:
    """Words are in absolute timeline ms (already offset by each segment's start). Cues break on
    character budget (≤ 2 lines), on max duration, and on gaps ≥ 700 ms (sentence pauses)."""
    cues: list[CaptionCue] = []
    group: list[WordTiming] = []

    def flush() -> None:
        if not group:
            return
        text = [w.word for w in group]
        lines = _wrap(text, max_chars_per_line)
        start = group[0].start_ms
        end = max(group[-1].end_ms, start + MIN_CUE_MS)
        cues.append(
            CaptionCue(
                index=len(cues) + 1, start_ms=start, end_ms=end, lines=tuple(lines[:MAX_LINES])
            )
        )
        group.clear()

    for w in timed_words:
        if group:
            gap = w.start_ms - group[-1].end_ms
            budget = len(" ".join(x.word for x in [*group, w])) > max_chars_per_line * MAX_LINES
            too_long = w.end_ms - group[0].start_ms > MAX_CUE_MS
            if gap >= 700 or budget or too_long:
                flush()
        if group and w.start_ms < cues[-1].end_ms if cues and not group else False:
            pass
        group.append(w)
    flush()
    # Enforce non-overlap after MIN_CUE_MS extension: trim previous cue ends to the next start.
    fixed: list[CaptionCue] = []
    for i, c in enumerate(cues):
        end = c.end_ms
        if i + 1 < len(cues) and end > cues[i + 1].start_ms:
            end = cues[i + 1].start_ms
        fixed.append(
            CaptionCue(
                index=c.index, start_ms=c.start_ms, end_ms=max(end, c.start_ms + 1), lines=c.lines
            )
        )
    return CaptionTrack(
        deliverable_id=deliverable_id,
        language=language,
        cues=tuple(fixed),
        max_chars_per_line=max_chars_per_line,
    )


def _ts(ms: int, sep: str) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{milli:03d}"


def to_srt(track: CaptionTrack) -> str:
    blocks = [
        f"{c.index}\n{_ts(c.start_ms, ',')} --> {_ts(c.end_ms, ',')}\n" + "\n".join(c.lines)
        for c in track.cues
    ]
    return "\n\n".join(blocks) + "\n"


def to_webvtt(track: CaptionTrack) -> str:
    blocks = [
        f"{_ts(c.start_ms, '.')} --> {_ts(c.end_ms, '.')}\n" + "\n".join(c.lines)
        for c in track.cues
    ]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"
