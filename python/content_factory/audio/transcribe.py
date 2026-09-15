"""What a recording says, and the story it can be cut into."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from content_factory.audio.normalize import tokenize_words
from content_factory.audio.takes import (
    TakeError,
    even_split,
    faster_whisper_words,
    to_wav_mono16k,
    wav_facts,
)
from content_factory.schemas.audio import SpeechTranscript, TimingSource
from content_factory.schemas.base import file_sha256
from content_factory.schemas.scenes import (
    CalloutScene,
    SceneSpec,
    StoryPlan,
    VisualBeat,
    WordTiming,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle: audio contracts import nothing here
    from content_factory.schemas.audio import NarrationSegment

ENGINES: tuple[str, ...] = ("faster_whisper", "fixture")
"""The transcribers a node may ask for. ``whisperx`` is deliberately absent: the aligner widgets
offer it because ADR-0004 names it first, and it is not installed on this host — offering it here
would be a fourth place to discover that."""

TRANSCRIBE_VERSION = "0.1.0"
"""Bumped when the transcript or the beat split changes shape, because it is part of the input
hash of everything downstream of a transcript."""

_SENTENCE_END = re.compile(r"[.!?…]['\")\]]*$")


def normalise_recording(src: Path, dest: Path, *, sample_rate: int = 24000) -> tuple[int, int, int]:
    """The dropped file as the mono PCM WAV the audio chain assumes; ``(rate, channels, ms)``."""
    if not src.is_file():
        msg = f"no recording at {src}"
        raise TakeError(msg)
    to_wav_mono16k(src, dest, sample_rate=sample_rate)
    return wav_facts(dest)


def transcribe(
    wav: Path,
    *,
    engine: str = "faster_whisper",
    model: str = "base.en",
    compute_type: str = "int8",
    timeout_s: int = 900,
    language: str = "en",
    fixture_text: str = "",
) -> SpeechTranscript:
    """Read ``wav`` into a transcript."""
    if engine not in ENGINES:
        msg = f"unknown transcriber {engine!r}; known: {list(ENGINES)}"
        raise TakeError(msg)
    rate, _channels, duration_ms = wav_facts(wav)
    if engine == "fixture":
        # Whitespace tokens, not normalised ones: the beat splitter reads sentence boundaries from
        # the operator's punctuation. `tokenize_words` still decides whether there is anything here.
        words = [w for w in fixture_text.split() if w.strip()]
        if not tokenize_words(fixture_text):
            msg = "the fixture transcriber needs the transcript text; none was given"
            raise TakeError(msg)
        timings = tuple(even_split(words, duration_ms))
        text = " ".join(fixture_text.split())
        source = TimingSource.estimated
        name = "fixture"
    else:
        heard, spans = faster_whisper_words(
            wav, model=model, compute_type=compute_type, timeout_s=timeout_s
        )
        if not heard:
            msg = f"{wav.name}: the transcriber heard no words in {duration_ms} ms of audio"
            raise TakeError(msg)
        timings = tuple(_clamp_words(heard, spans, duration_ms))
        text = " ".join(heard)
        source = TimingSource.asr
        name = f"faster-whisper:{model}"
    digest = file_sha256(wav)
    return SpeechTranscript(
        transcript_id=f"tsc_{digest[:16]}",
        audio_sha256=digest,
        sample_rate_hz=rate,
        duration_ms=duration_ms,
        language=language,
        engine=name,
        text=text[:200_000],
        words=timings,
        timing_source=source,
    )


def _clamp_words(
    heard: Sequence[str], spans: Sequence[tuple[float, float]], duration_ms: int
) -> list[WordTiming]:
    """Measured spans as contract-legal timings: ordered, non-overlapping, inside the recording."""
    out: list[WordTiming] = []
    cursor = 0
    for word, (start_s, end_s) in zip(heard, spans, strict=True):
        text = word.strip()
        if not text:
            continue
        start = min(max(int(start_s * 1000), cursor), max(0, duration_ms - 1))
        end = min(max(int(end_s * 1000), start + 1), duration_ms)
        out.append(WordTiming(word=text[:80], start_ms=start, end_ms=end))
        cursor = end
    return out


def sentences(transcript: SpeechTranscript) -> list[tuple[str, int, int]]:
    """``(text, start_ms, end_ms)`` per sentence, from the word timings."""
    if not transcript.words:
        return [(transcript.text, 0, transcript.duration_ms)]
    out: list[tuple[str, int, int]] = []
    current: list[WordTiming] = []
    for word in transcript.words:
        current.append(word)
        if _SENTENCE_END.search(word.word):
            out.append((" ".join(w.word for w in current), current[0].start_ms, current[-1].end_ms))
            current = []
    if current:
        out.append((" ".join(w.word for w in current), current[0].start_ms, current[-1].end_ms))
    return out


def split_beats(transcript: SpeechTranscript, *, beats: int) -> list[tuple[str, int, int]]:
    """Cut the transcript into at most ``beats`` spans, ``(text, start_ms, end_ms)``."""
    if beats < 1:
        msg = "a story needs at least one beat"
        raise ValueError(msg)
    parts = sentences(transcript)
    if len(parts) <= beats:
        return _split_long_single(parts, beats=beats) if len(parts) == 1 < beats else parts
    start_ms, end_ms = parts[0][1], parts[-1][2]
    span = max(1, end_ms - start_ms)
    groups: list[list[tuple[str, int, int]]] = [[] for _ in range(beats)]
    for part in parts:
        middle = (part[1] + part[2]) / 2 - start_ms
        index = min(beats - 1, max(0, int(middle * beats / span)))
        groups[index].append(part)
    return [(" ".join(p[0] for p in group), group[0][1], group[-1][2]) for group in groups if group]


def _split_long_single(
    parts: list[tuple[str, int, int]], *, beats: int
) -> list[tuple[str, int, int]]:
    """One unpunctuated sentence divided by word count, so a monologue still gets its pictures."""
    text, start, end = parts[0]
    words = text.split()
    if len(words) < beats * 2:
        return parts
    size = len(words) // beats
    out: list[tuple[str, int, int]] = []
    span = (end - start) // beats
    for i in range(beats):
        chunk = words[i * size : (i + 1) * size] if i < beats - 1 else words[i * size :]
        lo = start + i * span
        hi = end if i == beats - 1 else start + (i + 1) * span
        out.append((" ".join(chunk), lo, max(lo + 1, hi)))
    return out


def story_plan_from_transcript(
    transcript: SpeechTranscript,
    *,
    deliverable_id: str,
    beats: int = 6,
    width: int = 1024,
    height: int = 576,
    fps: int = 24,
    visual_subject: str | None = None,
) -> StoryPlan:
    """The transcript as a typed plan: one beat per span, one callout scene per beat."""
    spans = split_beats(transcript, beats=beats)
    visual_beats: list[VisualBeat] = []
    scenes: list[SceneSpec] = []
    for i, (text, start_ms, end_ms) in enumerate(spans):
        beat_id = f"beat_{i + 1:09d}"
        display = " ".join(text.split())[:1000] or "…"
        visual_beats.append(
            VisualBeat(
                beat_id=beat_id,
                order=i,
                display_text=display,
                measured_start_ms=start_ms,
                measured_end_ms=end_ms,
                # Both: the measured pair says where the words are; planned_duration_ms is what the
                # shot planner and the silent-cut path read, else every drawing gets the default.
                planned_duration_ms=max(200, end_ms - start_ms),
                words=tuple(w for w in transcript.words if start_ms <= w.start_ms < end_ms),
            )
        )
        scenes.append(
            CalloutScene(
                scene_id=f"scn_{i + 1:09d}",
                beat_id=beat_id,
                text=_callout_text(display),
            )
        )
    return StoryPlan(
        plan_id=f"pln_{transcript.audio_sha256[:16]}",
        deliverable_id=deliverable_id,
        fps=fps,  # type: ignore[arg-type]
        width=width,
        height=height,
        beats=tuple(visual_beats),
        scenes=tuple(scenes),
        visual_subject=visual_subject[:400] if visual_subject else None,
    )


def _callout_text(display: str):
    from content_factory.schemas.scenes import TextRef

    return TextRef(text=display[:2000])


HEAD_LEAD_MS = 220
"""How much of the pause before a beat's first word belongs to that beat.

Enough to carry the breath and the onset of the first consonant, which is what makes a line sound
*started* rather than spliced in. Not more: a long pause handed to the next beat delays its line
and holds the previous picture past the point the eye has finished with it, so the remainder of a
gap stays with the beat that precedes it as tail."""

TAIL_PAD_MS = 400
"""How far past the last word of the LAST beat to run, so the recording's final decay survives.

Only the last beat needs it. Every other beat runs to the next beat's start, so its tail is
whatever the speaker actually left there."""


def beat_spans(transcript: SpeechTranscript, plan: StoryPlan) -> dict[str, tuple[int, int]]:
    """``beat_id -> (start_ms, end_ms)`` in the recording, matched by walking the words."""
    words = transcript.words
    normalised = [[t.lower() for t in tokenize_words(w.word)] for w in words]
    flat = [(i, t) for i, tokens in enumerate(normalised) for t in tokens]
    cursor = 0
    out: dict[str, tuple[int, int]] = {}
    missing: list[str] = []
    for beat in sorted(plan.beats, key=lambda b: b.order):
        wanted = [t.lower() for t in tokenize_words(beat.spoken_text or beat.display_text)]
        first_word: int | None = None
        last_word: int | None = None
        probe = cursor
        for token in wanted:
            hit = None
            # A small window: an unbounded search lets a rewritten beat match a word from much
            # later and produce a span that runs backwards over the next beat.
            for offset in range(probe, min(probe + 8, len(flat))):
                if flat[offset][1] == token:
                    hit = offset
                    break
            if hit is None:
                continue
            if first_word is None:
                first_word = flat[hit][0]
            last_word = flat[hit][0]
            probe = hit + 1
        if first_word is None or last_word is None:
            if beat.measured_start_ms is not None and beat.measured_end_ms is not None:
                out[beat.beat_id] = (beat.measured_start_ms, beat.measured_end_ms)
            else:
                missing.append(beat.beat_id)
            continue
        cursor = probe
        out[beat.beat_id] = (words[first_word].start_ms, words[last_word].end_ms)
    if missing:
        raise TakeError(
            "these beats say nothing the recording says, so there is no audio to cut for them: "
            + ", ".join(missing)
        )
    order = [b.beat_id for b in sorted(plan.beats, key=lambda b: b.order)]
    return _close_the_gaps(out, order, transcript.duration_ms)


def _close_the_gaps(
    spans: dict[str, tuple[int, int]], order: Sequence[str], duration_ms: int
) -> dict[str, tuple[int, int]]:
    """Move every boundary off a word edge and into the silence beside it."""
    ordered = [beat_id for beat_id in order if beat_id in spans]
    if not ordered:
        return spans
    starts = {b: spans[b][0] for b in ordered}
    ends = {b: spans[b][1] for b in ordered}

    # One boundary per adjacent pair, used as both the earlier end and the later start: moving
    # each edge independently leaves a sliver between them, a hard cut in miniature.
    for index in range(len(ordered) - 1):
        this_id, next_id = ordered[index], ordered[index + 1]
        gap = spans[next_id][0] - spans[this_id][1]
        if gap <= 0:
            continue  # overlapping or touching already: a different fault, and not this one's
        boundary = spans[next_id][0] - min(HEAD_LEAD_MS, gap)
        ends[this_id] = boundary
        starts[next_id] = boundary

    # The first line needs its run-up too, and has no previous beat to take it from. The last
    # needs its decay, and has no next beat to bound it.
    starts[ordered[0]] = max(0, spans[ordered[0]][0] - HEAD_LEAD_MS)
    ends[ordered[-1]] = min(duration_ms, spans[ordered[-1]][1] + TAIL_PAD_MS)

    out = dict(spans)
    for beat_id in ordered:
        out[beat_id] = (starts[beat_id], ends[beat_id])
    return out


def cut_beat(source: Path, dest: Path, *, start_ms: int, end_ms: int) -> None:
    """One beat's audio out of a continuous recording, as its own mono PCM WAV."""
    from content_factory.audio.mix import ffmpeg

    if end_ms <= start_ms:
        msg = f"a beat cannot end at or before it starts ({start_ms} ms -> {end_ms} ms)"
        raise TakeError(msg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-i",
            str(source),
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-to",
            f"{end_ms / 1000:.3f}",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            "-map_metadata",
            "-1",
            str(dest),
        ]
    )


def segment_from_transcript(
    transcript: SpeechTranscript,
    wav: Path,
    *,
    beat_id: str,
    display_text: str,
    span: tuple[int, int],
) -> NarrationSegment:
    """A beat cut out of a transcribed recording, as the same segment a take would produce."""
    from content_factory.schemas.audio import NarrationSegment, VoiceIdentity

    rate, _channels, duration_ms = wav_facts(wav)
    start_ms, _end_ms = span
    words: list[WordTiming] = []
    for word in transcript.words:
        if not (start_ms <= word.start_ms < span[1]):
            continue
        lo = min(max(word.start_ms - start_ms, 0), max(0, duration_ms - 1))
        hi = min(max(word.end_ms - start_ms, lo + 1), duration_ms)
        words.append(WordTiming(word=word.word, start_ms=lo, end_ms=hi))
    if not words:
        # A beat with no located words still has audio and a script line; even_split over its own
        # duration is the honest fallback and is exactly what the aligner-free take path does.
        words = even_split(tokenize_words(display_text) or [display_text[:80]], duration_ms)
    spoken = " ".join(w.word for w in words)
    return NarrationSegment(
        beat_id=beat_id,
        audio_sha256=file_sha256(wav),
        sample_rate_hz=rate,
        channels=1,
        duration_ms=duration_ms,
        words=tuple(words),
        timing_source=transcript.timing_source,
        voice=VoiceIdentity(
            provider="human",
            voice_id="recording",
            model_revision=transcript.engine[:120],
            locale=transcript.language,
        ),
        spoken_text=spoken[:2000] or display_text[:2000],
        display_text=display_text[:2000],
        normalization_version="1",
    )
