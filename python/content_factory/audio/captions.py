"""Caption compiler: measured words → documentary-style cues → SRT and WebVTT."""

from __future__ import annotations

from collections.abc import Callable

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


BINDS_FORWARD: frozenset[str] = frozenset(
    # English
    "a an the and or but nor so yet of to in on at by for from with as into onto over under"
    " is are was were be been being has have had do does did will would can could may might"
    " that this these those my your his her its our their".split()
    # German, because the catalogue narrates in ten languages and this was found on a German cut
    + "der die das den dem des ein eine einen einem einer eines und oder aber auf in im an am"
    " zu zum zur von vom mit für bei nach aus ist sind war waren hat haben wird werden".split()
    # Spanish / French / Italian / Portuguese articles and the commonest prepositions
    + "el la los las un una unos unas y o de del al en con por para es son".split()
    + "le les une des du au aux et ou dans sur avec pour est sont".split()
    + "il lo gli una uno di da nel sul con per è sono".split()
    + "o os as um uma e ou do da no na com".split()
)
"""Closed-class words that bind to what follows them, so a cue must not end on one.

Measured on a German cut (2026-09-10): a cue read "Oben auf einem Berg ist die Luft dünner. Der"
— a dangling article, on screen alone for the last third of a second. English does it too, and
had been doing it all evening without being noticed: "Any sort that works by comparing / two
things has a", "It rains here two hundred days a". The set is small and per-language on purpose:
guessing part of speech is a different problem, and a fixed list of function words is the part
that is safe to be sure about.
"""


def _unhang(groups: list[list[WordTiming]]) -> list[list[WordTiming]]:
    """Move a trailing binding word onto the next cue, so no cue ends on "a" or "der"."""
    for i in range(len(groups) - 1):
        for _ in range(2):
            if len(groups[i]) < 2:
                break
            last = groups[i][-1]
            if last.word.strip().strip(".,;:!?\u2014\u2013").lower() not in BINDS_FORWARD:
                break
            groups[i + 1].insert(0, groups[i].pop())
    return groups


def balanced_groups(
    timed_words: list[WordTiming], *, max_chars_per_line: int = 32
) -> list[list[WordTiming]]:
    """Split a beat into as many cues as the greedy compiler needs, spread evenly (no straggler)."""
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
    return _unhang(groups)


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
    """Like :func:`compile_captions` for one beat, but multi-cue runs split evenly, no straggler."""
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
    """Words are in absolute timeline ms (already offset by each segment's start)."""
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


def wrap_chars_for(width: int, height: int, size_frac: float, configured: int = 0) -> int:
    """Characters per caption line for this frame, or ``configured`` when it is set."""
    if configured > 0:
        return configured
    cap = max(12, round(size_frac * min(width, height)))
    usable = max(1, width - 2 * round(0.07 * width))
    # 0.52 em is Inter Bold's average advance. Clamped to the range caption practice lives in.
    return min(42, max(16, round(0.71 * usable / (0.52 * cap))))


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
    font: str = "HelveticaNeue Condensed",
    size_frac: float = 0.0533,
    bottom_frac: float = 0.22,
    max_chars_per_line: int = 0,
    highlight: str | None = "#8BBDEB",
    box_alpha: float = 0.0,
    pop: float = 0.10,
    fade_ms: int = 90,
    hook: str | None = None,
    hook_seconds: float = 2.8,
    hook_font: str = "HelveticaNeue Condensed",
    hook_size_frac: float = 0.0711,
    hook_top_frac: float = 0.14,
) -> str:
    """Burn-in ASS captions: type scales with the shorter frame side, positions with the height."""
    short = min(width, height)
    cap_size = max(12, round(size_frac * short))
    margin_v = round(bottom_frac * height)
    margin_h = round(0.07 * width)
    alpha = round((1.0 - box_alpha) * 255)
    hook_size = max(12, round(hook_size_frac * short))
    hook_margin = round(hook_top_frac * height)
    max_chars_per_line = wrap_chars_for(width, height, size_frac, max_chars_per_line)
    white = "&H00FFFFFF"
    # A box when asked for, an outline when not. `edge` is the OutlineColour field, which libass
    # reads as the box fill under BorderStyle 3 and as the glyph outline under 1.
    outline_px = max(2, round(cap_size * 0.085))
    shadow_depth = max(1, round(cap_size * 0.045))
    if box_alpha > 0:
        border_style, border_width = 3, max(3, round(cap_size * 0.07))
        edge = f"&H{alpha:02X}000000"
        shadow_colour = f"&H{alpha:02X}000000"
    else:
        border_style, border_width = 1, outline_px
        edge = "&H20000000"  # near-opaque black: the outline is thin, so it needs the weight
        shadow_colour = "&H70000000"  # softer, and offset by shadow_depth
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
        # BorderStyle 3 is the box, 1 outline-and-shadow. Under 3 the Outline field is box padding
        # and must stay under half the leading (~0.1x size), or a two-line cue's boxes overlap dark.
        f"Style: Cap,{font},{cap_size},{white},{white},{edge},{shadow_colour},"
        f"-1,0,0,0,100,100,0,0,{border_style},{border_width},{shadow_depth},2,"
        f"{margin_h},{margin_h},{margin_v},1",
        # Highlight layer: identical layout and outline to Cap so a word lands on its twin without
        # a white halo. The colour cannot go on Cap itself: see the loop below.
        f"Style: CapHL,{font},{cap_size},{white},{white},{edge},{shadow_colour},"
        f"-1,0,0,0,100,100,0,0,1,{outline_px},{shadow_depth},2,"
        f"{margin_h},{margin_h},{margin_v},1",
        f"Style: Hook,{hook_font},{hook_size},{white},{white},&H20000000,&H70000000,"
        f"-1,0,0,0,100,100,0,0,1,{max(2, round(hook_size * 0.08))},"
        f"{max(1, round(hook_size * 0.04))},8,"
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

        def _laid_out(
            render: Callable[[int, str], str],
            words: list[WordTiming] = group,
            line_breaks: set[int] = breaks,
        ) -> str:
            """Same layout however each word renders, so the two layers stay in register."""
            out: list[str] = []
            for j, other in enumerate(words):
                if j in line_breaks:
                    out.append("\\N")
                elif j > 0:
                    out.append(" ")
                out.append(render(j, _ass_escape(other.word)))
            return "".join(out)

        plain = _laid_out(lambda _j, word: word)
        for i, w in enumerate(group):
            start = w.start_ms
            end = group[i + 1].start_ms if i + 1 < len(group) else cue_end
            if end <= start:
                end = start + 1
            # No override tag in the boxed cue: a mid-line `{\1c}` makes libass box each span, and
            # the overlaps composite dark. Fade only the first and last word, or the block blinks.
            fade = ""
            if fade_ms > 0 and (i == 0 or i == len(group) - 1):
                fade_in = fade_ms if i == 0 else 0
                fade_out = fade_ms if i == len(group) - 1 else 0
                fade = f"{{\\fad({fade_in},{fade_out})}}"
            lines.append(f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Cap,,0,0,0,,{fade}{plain}")
            if hl:
                # The other words stay transparent but keep their space, so the highlight lands on
                # its white twin; pop scales the word only, so the line never reflows.
                grow = ""
                settle = ""
                if pop > 0:
                    up = round(100 * (1 + pop))
                    grow = f"\\fscx100\\fscy100\\t(0,90,\\fscx{up}\\fscy{up})"
                    settle = "\\t(90,190,\\fscx100\\fscy100)"
                painted = _laid_out(
                    lambda j, word, _i=i, _g=grow, _s=settle: (
                        "{\\alpha&H00&\\1c"
                        + hl
                        + "&"
                        + _g
                        + _s
                        + "}"
                        + word
                        + "{\\alpha&HFF&\\fscx100\\fscy100}"
                        if j == _i
                        else word
                    )
                )
                lines.append(
                    f"Dialogue: 1,{_ass_ts(start)},{_ass_ts(end)},CapHL,,0,0,0,,"
                    + "{\\alpha&HFF&}"
                    + painted
                )
    return "\n".join(lines) + "\n"
