"""Caption compiler: measured words → documentary-style cues → SRT and WebVTT."""

from __future__ import annotations

from content_factory.schemas.audio import CaptionCue, CaptionTrack
from content_factory.schemas.scenes import WordTiming

MIN_CUE_MS = 1000
MAX_CUE_MS = 6000
MAX_LINES = 2


def wrap_words(words: list[str], max_chars: int) -> list[str]:
    """Greedy word-wrap. Canonical for captions: burned-in text must wrap like the sidecar."""
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


def balanced_groups(
    timed_words: list[WordTiming], *, max_chars_per_line: int = 32
) -> list[list[WordTiming]]:
    """Split one beat's words into the number of cues the greedy compiler would need, but with
    the words spread evenly across them (no one-word straggler at the end)."""
    if not timed_words:
        return []
    greedy = compile_captions(
        "dlv_placeholder00", timed_words, max_chars_per_line=max_chars_per_line
    )
    n = len(greedy.cues)
    if n <= 1:
        return [list(timed_words)]
    total = sum(len(w.word) + 1 for w in timed_words)
    target = total / n
    groups: list[list[WordTiming]] = [[]]
    used = 0.0
    for w in timed_words:
        if groups[-1] and used + (len(w.word) + 1) / 2 > target * len(groups) and len(groups) < n:
            groups.append([])
        groups[-1].append(w)
        used += len(w.word) + 1
    return groups


def cues_from_groups(
    deliverable_id: str,
    groups: list[list[WordTiming]],
    *,
    language: str = "en",
    max_chars_per_line: int = 32,
    first_index: int = 1,
) -> list[CaptionCue]:
    """One cue per word group, lines wrapped to the budget, ends trimmed so cues never overlap."""
    cues: list[CaptionCue] = []
    for group in groups:
        lines = wrap_words([w.word for w in group], max_chars_per_line)
        start = group[0].start_ms
        end = max(group[-1].end_ms, start + MIN_CUE_MS)
        if cues and cues[-1].end_ms > start:
            cues[-1] = cues[-1].model_copy(update={"end_ms": max(start, cues[-1].start_ms + 1)})
        cues.append(
            CaptionCue(
                index=first_index + len(cues),
                start_ms=start,
                end_ms=end,
                lines=tuple(lines[:MAX_LINES]),
            )
        )
    return cues


def balanced_cues(
    deliverable_id: str,
    timed_words: list[WordTiming],
    *,
    language: str = "en",
    max_chars_per_line: int = 32,
) -> CaptionTrack:
    """Like :func:`compile_captions`, but a run of words that needs more than one cue is split into
    cues of roughly equal length instead of full cues followed by a one-word straggler ("quarter",
    "total"). Meant for one beat at a time."""
    if not timed_words:
        msg = "no words to caption"
        raise ValueError(msg)
    cues = cues_from_groups(
        deliverable_id,
        balanced_groups(timed_words, max_chars_per_line=max_chars_per_line),
        language=language,
        max_chars_per_line=max_chars_per_line,
    )
    return CaptionTrack(
        deliverable_id=deliverable_id,
        language=language,
        cues=tuple(cues),
        max_chars_per_line=max_chars_per_line,
    )


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
        lines = wrap_words(text, max_chars_per_line)
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


def _ass_ts(ms: int) -> str:
    cs = max(0, ms) // 10
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    sec, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{sec:02d}.{c:02d}"


def _ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB -> ASS &HAABBGGRR."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def to_ass(
    groups: list[list[WordTiming]],
    *,
    width: int,
    height: int,
    font: str = "Inter",
    size_frac: float = 0.03,
    bottom_frac: float = 0.22,
    max_chars_per_line: int = 22,
    highlight: str | None = "#8BBDEB",
    box_alpha: float = 0.55,
    hook: str | None = None,
    hook_seconds: float = 2.8,
    hook_font: str = "Sora",
    hook_size_frac: float = 0.04,
    hook_top_frac: float = 0.14,
) -> str:
    """Burn-in captions as ASS: one event per spoken word, the whole cue visible and the current
    word in the highlight colour (colour only — no scale, no bounce), plus an optional headline
    over the opening seconds. Sizes are in frame pixels (PlayRes = frame), so the same fractions
    hold for 9:16 and 16:9."""
    cap_size = max(12, round(size_frac * height))
    margin_v = round(bottom_frac * height)
    margin_h = round(0.07 * width)
    alpha = round((1.0 - box_alpha) * 255)
    hook_size = max(12, round(hook_size_frac * height))
    hook_margin = round(hook_top_frac * height)
    white = "&H00FFFFFF"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",  # smart wrapping: the hook headline breaks into balanced lines
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{cap_size},{white},{white},&H{alpha:02X}000000,&H{alpha:02X}000000,"
        f"-1,0,0,0,100,100,0,0,3,{max(6, round(cap_size * 0.22))},0,2,"
        f"{margin_h},{margin_h},{margin_v},1",
        f"Style: Hook,{hook_font},{hook_size},{white},{white},&H60000000,&H60000000,"
        f"-1,0,0,0,100,100,0,0,1,{max(2, round(hook_size * 0.08))},0,8,"
        f"{margin_h},{margin_h},{hook_margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if hook:
        end_ms = round(hook_seconds * 1000)
        lines.append(
            f"Dialogue: 1,{_ass_ts(0)},{_ass_ts(end_ms)},Hook,,0,0,0,,"
            + "{\\fad(120,260)}"
            + _ass_escape(hook)
        )
    hl = _ass_colour(highlight) if highlight else None
    for group in groups:
        if not group:
            continue
        wrapped = wrap_words([w.word for w in group], max_chars_per_line)
        # word index -> line index, to place \N breaks
        breaks: set[int] = set()
        count = 0
        for line in wrapped[:-1]:
            count += len(line.split())
            breaks.add(count)
        cue_end = max(group[-1].end_ms, group[0].start_ms + MIN_CUE_MS)
        for i, w in enumerate(group):
            start = w.start_ms
            end = group[i + 1].start_ms if i + 1 < len(group) else cue_end
            if end <= start:
                end = start + 1
            parts: list[str] = []
            for j, other in enumerate(group):
                if j in breaks:
                    parts.append("\\N")
                elif j > 0:
                    parts.append(" ")
                word = _ass_escape(other.word)
                if hl and j == i:
                    parts.append("{\\1c" + hl + "&}" + word + "{\\1c" + white + "&}")
                else:
                    parts.append(word)
            lines.append(
                f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Cap,,0,0,0,,{''.join(parts)}"
            )
    return "\n".join(lines) + "\n"
