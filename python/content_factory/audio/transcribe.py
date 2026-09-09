"""What a recording says, and the story it can be cut into.

Every other lane in this factory starts from something written: a brief, a research pass, a
StoryPlan fixture. This is the other direction — the material arrives as sound and the words have
to be read off it before anything can be planned. Two things live here, because they are two
halves of one idea:

* :func:`transcribe` reads a recording into a :class:`SpeechTranscript`: the text, and where each
  word is in the audio. ``faster_whisper`` measures the timings; ``fixture`` takes a transcript
  the operator already has and apportions it across the duration, which is the offline path the
  core suite runs on and the honest answer for a recording whose script is known.
* :func:`story_plan_from_transcript` cuts that transcript into beats at sentence boundaries and
  builds the typed :class:`StoryPlan` the rest of the pipeline consumes. Each beat's duration is
  **measured**, not planned: it is exactly the span of the words it owns, so the picture drawn for
  that beat is on screen for exactly as long as those words are spoken.

:func:`beat_spans` is what makes the audio side work: given a plan built this way (or any plan
whose beats are spans of the same words in order), it says which milliseconds of the recording
each beat owns, so ``voice_over`` can cut per-beat takes out of one continuous recording instead
of asking an operator to record their interview again beat by beat.

Nothing here loads a model in this process: faster-whisper runs in its own environment behind
``takes.SUBPROCESS_RUN``, which is also the seam the tests replace.
"""

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
    """The dropped file as the mono PCM WAV the audio chain assumes; ``(rate, channels, ms)``.

    The same normalisation ``voice_over`` applies to a take, for the same reason: everything after
    this point measures, cuts and mixes PCM, and an m4a from a phone is not that.
    """
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
    """Read ``wav`` into a transcript. ``wav`` must already be normalised PCM.

    ``fixture`` needs ``fixture_text``: a transcript nobody measured is text with estimated
    boundaries, and it says so in ``timing_source`` rather than passing itself off as measured.
    """
    if engine not in ENGINES:
        msg = f"unknown transcriber {engine!r}; known: {list(ENGINES)}"
        raise TakeError(msg)
    rate, _channels, duration_ms = wav_facts(wav)
    if engine == "fixture":
        # Whitespace tokens, not normalised ones: the punctuation the operator typed is what the
        # beat splitter reads sentence boundaries from, and stripping it here would turn a
        # six-sentence transcript into one unpunctuated run and cost every downstream cut its
        # boundaries. `tokenize_words` is still what decides whether there is anything here.
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
    """Measured spans as contract-legal timings: ordered, non-overlapping, inside the recording.

    faster-whisper returns seconds as floats and occasionally overlaps two words by a few
    milliseconds or runs the last one a hair past the end of the file. The contract refuses the
    second and ``audio.alignment`` calls an overlap over 40 ms a critical finding — so a beat cut
    from a transcript would fail its own alignment check over a rounding artefact. Each word
    therefore starts no earlier than the previous one ended: a start nudged forward by
    milliseconds is inaudible, and refusing the transcript would mean no lane could use a real
    recording.
    """
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
    """``(text, start_ms, end_ms)`` per sentence, from the word timings.

    Sentence boundaries come from the punctuation the transcriber emitted. A recording with no
    punctuation at all (some models emit none) is one sentence, which the beat splitter then
    divides by duration — a worse cut, and still a cut, rather than a failure.
    """
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
    """Cut the transcript into at most ``beats`` spans, ``(text, start_ms, end_ms)``.

    Sentences are grouped by where they sit in the *spoken duration*, not by sentence count: a
    picture that has to hold for a 20-second digression and one that holds for a three-word aside
    are the same amount of drawing, and the long one is where a viewer notices a still picture. So
    each sentence goes to the beat its own midpoint falls in, which spreads the drawings evenly
    over the recording instead of over the punctuation. A transcript with fewer sentences than
    beats yields one beat per sentence rather than an invented split — the recording said what it
    said.
    """
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
    """The transcript as a typed plan: one beat per span, one callout scene per beat.

    ``callout`` is the scene kind on purpose. The scene kind is what the shot planner reads to
    choose a camera move, and a callout is a slow push in — a drawing that breathes while a line
    is spoken over it. It is also the only kind that needs neither a dataset nor a stored asset,
    which matters because the picture for this beat does not exist yet: it is about to be drawn
    from the staging, and an ``image`` scene would have to name an asset id nothing has produced.

    Beat durations are measured spans of the recording, so a shot plan built from this holds each
    drawing for exactly its own words.
    """
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
                # Both, deliberately. The measured pair says where the words are in the recording;
                # planned_duration_ms is what the shot planner and the silent-cut path read, and a
                # plan that carried only the measurement would give every drawing the default
                # length.
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


def beat_spans(transcript: SpeechTranscript, plan: StoryPlan) -> dict[str, tuple[int, int]]:
    """``beat_id -> (start_ms, end_ms)`` in the recording, matched by walking the words.

    The plan's beats are spans of these same words in order, so matching is a single forward walk
    rather than a search: consume the transcript's words while they keep matching the beat's, and
    the beat owns from the first match to the last. Comparison is on the normalised token, so
    punctuation and casing the transcriber attached to a word cannot break the walk, and a word
    the plan dropped (an editor's tidy-up) is skipped rather than ending the beat early.

    A beat that matches nothing at all keeps its own measured span when it has one; failing that
    it is reported by name, because a beat whose audio nobody can locate must not silently become
    the whole recording.
    """
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
            # A small window: the next few transcript tokens. Unbounded search would let a beat
            # whose text was rewritten match a word from much later in the recording and produce
            # a span that runs backwards over the next beat.
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
    return out


def cut_beat(source: Path, dest: Path, *, start_ms: int, end_ms: int) -> None:
    """One beat's audio out of a continuous recording, as its own mono PCM WAV.

    Sample-accurate re-encode rather than a stream copy: the beats have to abut exactly, and a
    copy would snap each cut to the nearest packet boundary and lose or repeat a few milliseconds
    at every one of them. The recording is already PCM by the time this runs, so there is no
    generation loss to weigh against that.
    """
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
    """A beat cut out of a transcribed recording, as the same segment a take would produce.

    The word timings are the transcript's own, shifted to be relative to this beat's WAV, so
    nothing re-runs an aligner: the recording was measured once, whole, and a beat is a window on
    that measurement. ``timing_source`` is carried over rather than re-asserted — an estimated
    transcript yields estimated beats, and a caption cue built from it must not claim to be
    measured.

    The words are clamped to the beat's own duration because the WAV's length is decided by
    FFmpeg's resampler and can land a millisecond short of the span that asked for it.
    """
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
