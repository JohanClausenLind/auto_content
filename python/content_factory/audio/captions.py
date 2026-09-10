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
    """Move a trailing binding word onto the next cue, so no cue ends on "a" or "der".

    At most two words per boundary ("and the"), and never the last word a cue has: a cue with
    one word in it is worse than a cue that ends on an article.
    """
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
    """Split one beat's words into the number of cues the greedy compiler would need, but with
    the words spread evenly across them (no one-word straggler at the end).

    A cue also never ends on a word that binds to the next one — see :func:`_unhang`.
    """
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


def wrap_chars_for(width: int, height: int, size_frac: float, configured: int = 0) -> int:
    """Characters per caption line for this frame, or ``configured`` when it is set.

    One number has to serve the SRT/VTT cue grouping and the burn-in wrap, or the file and the
    picture disagree about where a cue breaks. A fixed 22 is the vertical calibration: 22
    characters of 58 px type fills 71 % of a 1080 px frame's usable width. On a 1920 px one the
    same 22 is a line a third of the frame wide and the block stacks four deep in the middle of
    the picture, so the count is derived from the frame instead and comes out at 39 there and at
    22 — unchanged — on the phone frame it was tuned on.
    """
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
    font: str = "Inter",
    size_frac: float = 0.0533,
    bottom_frac: float = 0.22,
    max_chars_per_line: int = 0,
    highlight: str | None = "#8BBDEB",
    box_alpha: float = 0.55,
    hook: str | None = None,
    hook_seconds: float = 2.8,
    hook_font: str = "Sora",
    hook_size_frac: float = 0.0711,
    hook_top_frac: float = 0.14,
) -> str:
    """Burn-in captions as ASS: one event per spoken word, the whole cue visible and the current
    word in the highlight colour (colour only — no scale, no bounce), plus an optional headline
    over the opening seconds.

    **Text sizes are fractions of the frame's shorter side, not of its height.** They used to be
    fractions of the height, with a docstring claiming "the same fractions hold for 9:16 and 16:9"
    — which is exactly what they do not do. Every default here was calibrated on a 1080x1920
    vertical frame, so on a 1920x1080 one the same fraction of height produced captions at 32 px
    instead of 58 and a hook headline at 43 px instead of 77: 55 % of the size they were designed
    to be, measured on a rendered landscape film. The shorter side is 1080 either way, so one
    fraction now means one physical size in both orientations, and the vertical output is
    unchanged to the pixel (0.0533 x 1080 rounds to the same 58 px that 0.03 x 1920 did).

    ``max_chars_per_line`` of 0 derives the wrap from the frame: the same share of the usable
    width the vertical calibration used (22 characters of 58 px type inside a 1080 px frame is
    71 % of it), which is 22 on a phone frame and 39 on a landscape one. A fixed 22 on 16:9 is a
    line a third of the frame wide, and the block stacks four deep in the middle of the picture.

    Positions are still fractions of the height, because they are about the frame and not about
    the type: ``bottom_frac`` exists to clear the platform UI band at the bottom of a phone.
    """
    short = min(width, height)
    cap_size = max(12, round(size_frac * short))
    margin_v = round(bottom_frac * height)
    margin_h = round(0.07 * width)
    alpha = round((1.0 - box_alpha) * 255)
    hook_size = max(12, round(hook_size_frac * short))
    hook_margin = round(hook_top_frac * height)
    max_chars_per_line = wrap_chars_for(width, height, size_frac, max_chars_per_line)
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
        # The box padding (the Outline field, under BorderStyle 3) has to stay under half the
        # leading, or a two-line cue's boxes overlap and the overlap composites darker: a
        # horizontal dark band straight through the middle of the block. libass advances a line by
        # about 1.2x the size, so the gap between glyph boxes is ~0.2x and the padding gets half.
        f"Style: Cap,{font},{cap_size},{white},{white},&H{alpha:02X}000000,&H{alpha:02X}000000,"
        f"-1,0,0,0,100,100,0,0,3,{max(3, round(cap_size * 0.07))},0,2,"
        f"{margin_h},{margin_h},{margin_v},1",
        # The highlight layer: the same font, size, margins and alignment as Cap, so a word lands
        # exactly on its twin below -- but BorderStyle 1 with no outline and no shadow, so it
        # draws glyphs and no box at all. See the loop below for why the colour cannot go on Cap.
        f"Style: CapHL,{font},{cap_size},{white},{white},&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,0,0,2,"
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

        def _laid_out(
            render: Callable[[int, str], str],
            words: list[WordTiming] = group,
            line_breaks: set[int] = breaks,
        ) -> str:
            """One cue laid out identically however each word is rendered, so two layers of the
            same cue break in the same places and stay in register."""
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
            # The cue, boxed, with no override tag anywhere in it. A `{\1c}` mid-line makes libass
            # draw the BorderStyle-3 box **per span**, and adjacent spans overlap by the box
            # padding -- so a translucent box composited over itself came out as two hard dark
            # bars either side of whichever word was highlighted, on every frame of every film.
            lines.append(f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Cap,,0,0,0,,{plain}")
            if hl:
                # The current word alone, in the highlight colour, on a boxless style laid out
                # identically -- so it lands exactly on its white twin underneath. Every other
                # word is transparent and still occupies its space, which is what keeps the two
                # layers in register.
                painted = _laid_out(
                    lambda j, word, _i=i: (
                        "{\\alpha&H00&\\1c" + hl + "&}" + word + "{\\alpha&HFF&}"
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
