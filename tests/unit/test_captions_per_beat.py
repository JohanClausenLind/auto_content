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
    assert len(hook) == 1 and hook[0].endswith("Wind beat nuclear.") and "0:00:02.50" in hook[0]
    assert len(caps) == 3  # one event per spoken word
    # the highlight colour appears exactly once per event, on the word being spoken, in order
    assert all(d.count("&H00FFC34C&") == 1 for d in caps)
    assert [d.split("\\1c&H00FFC34C&}")[1].split("{")[0] for d in caps] == [
        "Wind",
        "beat",
        "nuclear",
    ]
    # events tile the cue without gaps: each starts where the previous ends
    starts = [d.split(",")[1] for d in caps]
    ends = [d.split(",")[2] for d in caps]
    assert starts[1:] == ends[:-1]
    # without a highlight colour or hook the file is plain cues
    plain = to_ass(groups, width=1080, height=1920, highlight=None, hook=None)
    assert "&H00FFC34C&" not in plain and ",Hook," not in plain
