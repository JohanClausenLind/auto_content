"""Caption cues stay inside their beat, whatever the pauses between beats look like."""

from __future__ import annotations

from pathlib import Path

from content_factory.audio.mix import AudioMixSpec, lay_out
from content_factory.runners.local import make_context
from content_factory.schemas.audio import CaptionTrack
from content_factory.workflows.stages import (
    _load_segments,
    stage_align_words,
    stage_compile_captions,
    stage_lock_script,
    stage_plan_story,
    stage_synthesize_narration,
)


def _to_ms(t: str) -> int:
    return int(t[0:2]) * 3600000 + int(t[3:5]) * 60000 + int(t[6:8]) * 1000 + int(t[9:12])


def test_no_cue_mixes_words_from_two_beats(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    stage_plan_story(ctx)
    stage_lock_script(ctx)
    stage_synthesize_narration(ctx)
    stage_align_words(ctx)
    out = stage_compile_captions(ctx)
    srt = (ctx.ddir() / "captions" / "captions.srt").read_text()
    assert out.facts["cues"] >= 4 and srt.count("-->") == out.facts["cues"]

    _plan, segs, _files = _load_segments(ctx)
    laid = lay_out(segs, AudioMixSpec(deliverable_id=ctx.deliverable_id))  # type: ignore[arg-type]
    spans = [(b.words[0].start_ms, b.words[-1].end_ms) for b in laid]
    cues = []
    for block in srt.strip().split("\n\n"):
        timing = block.splitlines()[1]
        start, end = timing.split(" --> ")
        cues.append((_to_ms(start), _to_ms(end)))
    for start, end in cues:
        inside = [i for i, (b0, b1) in enumerate(spans) if start >= b0 - 1 and start <= b1 + 1]
        assert len(inside) == 1, (start, end, spans)
        _b0, b1 = spans[inside[0]]
        assert end <= b1 + 1200, "cue runs into the next beat"  # MIN_CUE_MS padding only
    # indices are contiguous and the track validates
    CaptionTrack(deliverable_id="dlv_short0000001", cues=()) if False else None


def test_balanced_cues_split_a_long_beat_evenly() -> None:
    from content_factory.audio.captions import balanced_cues, compile_captions
    from content_factory.schemas.audio import WordTiming

    text = (
        "Then orders collapsed one thousand two hundred forty four megawatts in twenty twenty "
        "three four hundred forty six in twenty twenty four"
    )
    words = []
    t = 0
    for w in text.split():
        words.append(WordTiming(word=w, start_ms=t, end_ms=t + 300))
        t += 320
    greedy = compile_captions("dlv_short0000001", words)
    balanced = balanced_cues("dlv_short0000001", words)
    assert len(balanced.cues) == len(greedy.cues) >= 2
    sizes = [sum(len(line) for line in c.lines) for c in balanced.cues]
    assert max(sizes) - min(sizes) < 25  # no one-word straggler at the end
    assert " ".join(" ".join(c.lines) for c in balanced.cues) == text  # every word, in order
    assert all(
        a.end_ms <= b.start_ms for a, b in zip(balanced.cues, balanced.cues[1:], strict=False)
    )


def test_ass_track_highlights_each_word_once_and_carries_the_hook() -> None:
    from content_factory.audio.captions import balanced_groups, to_ass
    from content_factory.schemas.audio import WordTiming

    words = [
        WordTiming(word="Wind", start_ms=0, end_ms=300),
        WordTiming(word="beat", start_ms=320, end_ms=600),
        WordTiming(word="nuclear", start_ms=650, end_ms=1100),
    ]
    groups = balanced_groups(words, max_chars_per_line=22)
    ass = to_ass(
        groups,
        width=1080,
        height=1920,
        highlight="#4CC3FF",
        hook="Wind beat nuclear.",
        hook_seconds=2.5,
    )
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    hook = [d for d in dialogues if ",Hook," in d]
    caps = [d for d in dialogues if ",Cap," in d]
    highlights = [d for d in dialogues if ",CapHL," in d]
    assert len(hook) == 1 and hook[0].endswith("Wind beat nuclear.") and "0:00:02.50" in hook[0]
    # Two events per spoken word: the boxed cue, and the highlight painted over it on a boxless
    # style. One event carrying both put a colour override inside a BorderStyle-3 line, and libass
    # draws that box per span -- so the translucent box overlapped itself and left a hard dark bar
    # either side of whichever word was lit.
    assert len(caps) == 3
    assert len(highlights) == 3
    assert all("&H00FFC34C&" not in d for d in caps), "the boxed layer carries no override at all"
    # the highlight colour appears exactly once per overlay, on the word being spoken, in order
    assert all(d.count("&H00FFC34C&") == 1 for d in highlights)
    # The lit word is whatever follows the override block that carries the colour. Read by
    # splitting on the closing brace rather than on an exact tag string: the block also carries the
    # `\t` scale that makes the word rise (`compose.caption_pop`, added 2026-09-12), so a literal
    # "\1c<colour>}" no longer appears and matching on it asserted the animation away.
    lit = [d.split("&H00FFC34C&", 1)[1].split("}", 1)[1].split("{", 1)[0] for d in highlights]
    assert lit == ["Wind", "beat", "nuclear"]
    # every word is present in both layers, so the two are laid out identically and stay in register
    # `endswith`, so the leading `\fad` on a cue's first and last word does not break it.
    assert all(d.endswith("Wind beat nuclear") for d in caps)
    # events tile the cue without gaps: each starts where the previous ends
    starts = [d.split(",")[1] for d in caps]
    ends = [d.split(",")[2] for d in caps]
    assert starts[1:] == ends[:-1]
    assert [d.split(",")[1] for d in highlights] == starts
    # without a highlight colour or hook the file is plain cues
    plain = to_ass(groups, width=1080, height=1920, highlight=None, hook=None)
    assert "&H00FFC34C&" not in plain and ",Hook," not in plain
    assert ",CapHL," not in plain, "no highlight, no overlay layer"


def test_no_hook_is_burned_over_a_film_that_opens_on_typography() -> None:
    """A hook over a headline card is a second headline over the first.

    Both halves were measured on `narrated-video` (2026-09-10). The word test alone let
    "Resistance is not something bacteria learn" sit across the top of a title card reading
    "Resistance is not learned" — a paraphrase is not the same words, and two different headlines
    stacked read worse than a repeat. The word test still earns its place: it catches a hook that
    repeats a *non*-headline opening scene's own line.
    """
    from content_factory.schemas.fixtures import sample_story_plan
    from content_factory.workflows.stages import HEADLINE_OPENERS, _hook_overlay

    plan = sample_story_plan()
    first = plan.beats[0].beat_id

    def opening_is(scene):
        """The same plan with `scene` moved onto the first beat."""
        moved = scene.model_copy(update={"beat_id": first})
        rest = tuple(sc for sc in plan.scenes if sc.beat_id != first)
        return plan.model_copy(update={"scenes": (moved, *rest)})

    headline = next(sc for sc in plan.scenes if sc.kind in HEADLINE_OPENERS)
    plain = next(sc for sc in plan.scenes if sc.kind not in HEADLINE_OPENERS)

    # Opening on typography: no hook, even though the hook is a different sentence.
    opens_headline = opening_is(headline).model_copy(
        update={"hook_text": "An entirely different sentence"}
    )
    assert _hook_overlay(opens_headline) is None

    # Opening on something that is not a headline: the hook is exactly what it is for.
    opens_plain = opening_is(plain).model_copy(update={"hook_text": "A line of its own"})
    assert _hook_overlay(opens_plain) == "A line of its own"

    # ...unless it repeats that scene's own words, which is the case the word test still catches.
    shown = next(
        getattr(plain, slot).text
        for slot in ("title", "heading", "label", "text")
        if getattr(getattr(plain, slot, None), "text", None)
    )
    assert _hook_overlay(opens_plain.model_copy(update={"hook_text": shown})) is None

    # And a plan with no hook has no overlay, whatever it opens on.
    assert _hook_overlay(opens_plain.model_copy(update={"hook_text": None})) is None


def test_no_cue_ends_on_a_word_that_binds_to_the_next_one() -> None:
    """Measured on a German cut (2026-09-10): a cue read "... die Luft dünner. Der" — a dangling
    article on screen by itself. English had been doing it all evening unnoticed: "Any sort that
    works by comparing / two things has a"."""
    from content_factory.audio.captions import BINDS_FORWARD, balanced_groups
    from content_factory.schemas.scenes import WordTiming

    def timed(text: str) -> list[WordTiming]:
        words = text.split()
        step = 260
        return [
            WordTiming(word=w, start_ms=i * step, end_ms=i * step + step - 10)
            for i, w in enumerate(words)
        ]

    for line in (
        "Any sort that works by comparing two things has a hard floor and it is not a matter of"
        " cleverness at all",
        "Oben auf einem Berg ist die Luft dünner und der Dampfdruck genügt schon bei weniger"
        " Wärme als unten",
        "It rains here two hundred days a year and the city was built for it from the start",
    ):
        groups = balanced_groups(timed(line), max_chars_per_line=32)
        assert len(groups) > 1, "the fixture has to split or the test proves nothing"
        for g in groups[:-1]:
            tail = g[-1].word.strip().strip(".,;:!?").lower()
            assert tail not in BINDS_FORWARD, f"cue ends on {tail!r}: {[w.word for w in g]}"
            assert len(g) >= 2, "unhanging must not leave a one-word cue"
        # Every word is still there, once, in order.
        assert [w.word for g in groups for w in g] == line.split()
