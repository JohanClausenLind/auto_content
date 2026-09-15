"""Speak a run of beats as ONE take, then cut it into per-beat segments."""

from __future__ import annotations

import hashlib
import io
import wave
from dataclasses import dataclass

from content_factory.audio.normalize import tokenize_words
from content_factory.schemas.audio import NarrationRequest, NarrationSegment, WordTiming

MAX_MATCH_DB = 3.0
"""Ceiling on the per-beat level correction.

Capped rather than flattened: a narration is allowed to drop for an intimate line. What it is not
allowed to do is drop 5 dB because the sentence was long, which is the artefact actually measured.
"""


class ContinuityError(RuntimeError):
    """The take and the script came apart, so the cuts would land in the wrong places."""


@dataclass(frozen=True)
class Cut:
    """One beat's slice of a shared take."""

    beat_id: str
    audio: bytes
    words: tuple[WordTiming, ...]
    duration_ms: int
    take_span_ms: tuple[int, int]


def joined_text(requests: list[NarrationRequest]) -> str:
    """What actually gets spoken: the beats' spoken text, in order, as one paragraph."""
    return " ".join(r.spoken_text.strip() for r in requests)


def _read(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes)) as handle:
        rate, channels = handle.getframerate(), handle.getnchannels()
        frames = handle.readframes(handle.getnframes())
    import numpy as np

    samples = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate, channels


def _write(samples, rate: int) -> bytes:
    import numpy as np

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def speech_level_db(samples, rate: int) -> float:
    """Level of the *speech*, with the pauses dropped — a long beat is not a quiet beat."""
    import numpy as np

    window = max(1, int(0.03 * rate))
    if len(samples) <= window:
        return -120.0
    frames = np.array(
        [
            np.sqrt((samples[i : i + window] ** 2).mean())
            for i in range(0, len(samples) - window, window)
        ]
    )
    if not len(frames):
        return -120.0
    voiced = frames[frames > frames.max() * 0.12]
    if not len(voiced):
        return -120.0
    return float(20 * np.log10(max(float(voiced.mean()), 1e-9)))


def match_levels(clips: list, rate: int, *, max_db: float = MAX_MATCH_DB) -> list:
    """Bring every piece towards the median speech level, by at most ``max_db``."""
    import numpy as np

    if not clips:
        return clips
    levels = [speech_level_db(c, rate) for c in clips]
    target = float(np.median(levels))
    return [
        clip * (10 ** (float(np.clip(target - level, -max_db, max_db)) / 20.0))
        for clip, level in zip(clips, levels, strict=True)
    ]


SEARCH_MS = 400
"""How far either side of a beat boundary to look for the real pause.

The word times cannot be trusted to find it. `faster_whisper`'s forced alignment **apportions**:
measured over a 72-word take, all 71 word-to-word gaps were exactly 0 ms, every word starting where
the last ended. So "cut at the midpoint of the pause" collapses to "cut on the last word's final
sample", which clips its decay — and that is audible. Measured on the finished cuts, energy in the
last 40 ms against the line's own average: a clean cut sits 45-70 dB down, and the bad ones sat at
**+1, -2 and -13 dB**. The operator picked one of them out by name.

So the boundary is found in the audio: the quietest short window within ``SEARCH_MS`` of where the
words say the beat ends. 400 ms is wide enough to reach a real breath and narrow enough that it
cannot wander into a neighbouring word."""

FADE_MS = 8
"""A short fade at each piece's edges, so a cut landing on a non-zero sample cannot click."""


def _quietest_ms(samples, rate: int, around_ms: int, *, search_ms: int = SEARCH_MS) -> int:
    """The centre of the quietest 20 ms window within ``search_ms`` of ``around_ms``."""
    import numpy as np

    window = max(1, int(0.02 * rate))
    lo = max(0, int((around_ms - search_ms) / 1000 * rate))
    hi = min(len(samples) - window, int((around_ms + search_ms) / 1000 * rate))
    if hi <= lo:
        return around_ms
    hop = max(1, window // 4)
    positions = list(range(lo, hi, hop))
    energies = np.array([float((samples[i : i + window] ** 2).mean()) for i in positions])
    # The middle of the quiet region, not `argmin`: near-identical windows tie in a pause, and the
    # earliest would park the cut against the preceding word. The median keeps room tone both sides.
    floor = float(energies.min())
    tolerance = floor * 1.05 + 1e-12
    quiet = [pos for pos, energy in zip(positions, energies, strict=True) if energy <= tolerance]
    best = quiet[len(quiet) // 2]
    return int((best + window / 2) / rate * 1000)


def _fade_edges(clip, rate: int, *, fade_ms: int = FADE_MS):
    """Taper both ends, so a split that lands on a non-zero sample does not click."""
    import numpy as np

    n = min(int(fade_ms / 1000 * rate), len(clip) // 2)
    if n <= 0:
        return clip
    ramp = np.linspace(0.0, 1.0, n, dtype="float32")
    clip[:n] *= ramp
    clip[-n:] *= ramp[::-1]
    return clip


def cut_take(
    requests: list[NarrationRequest],
    take_audio: bytes,
    take_words: list[WordTiming],
    take_duration_ms: int,
    *,
    match: bool = True,
    max_db: float = MAX_MATCH_DB,
) -> list[Cut]:
    """Split one take into per-beat pieces at the pauses between beats."""
    counts = [len(tokenize_words(r.spoken_text)) for r in requests]
    if sum(counts) != len(take_words):
        raise ContinuityError(
            f"the aligner returned {len(take_words)} words for {sum(counts)} in the script; "
            "the take and the script have come apart, so every cut after the first mismatch "
            "would land in the wrong place"
        )
    samples, rate, _ = _read(take_audio)
    spans: list[tuple[NarrationRequest, int, int, list[WordTiming]]] = []
    index = 0
    for request, count in zip(requests, counts, strict=True):
        first, last = take_words[index], take_words[index + count - 1]
        previous_end = take_words[index - 1].end_ms if index else 0
        next_start = (
            take_words[index + count].start_ms
            if index + count < len(take_words)
            else take_duration_ms
        )
        # Where the words say the beat ends, then where the audio says the pause actually is.
        nominal_start = (previous_end + first.start_ms) // 2 if index else 0
        nominal_end = (last.end_ms + next_start) // 2
        start_ms = 0 if not index else _quietest_ms(samples, rate, nominal_start)
        end_ms = (
            take_duration_ms
            if index + count >= len(take_words)
            else _quietest_ms(samples, rate, nominal_end)
        )
        if end_ms <= start_ms:  # no findable pause: keep the word boundary rather than invert
            start_ms, end_ms = nominal_start, max(nominal_end, nominal_start + 1)
        spans.append((request, start_ms, end_ms, list(take_words[index : index + count])))
        index += count

    clips = [
        _fade_edges(samples[int(a / 1000 * rate) : int(b / 1000 * rate)].copy(), rate)
        for _, a, b, _ in spans
    ]
    if match:
        clips = match_levels(clips, rate, max_db=max_db)
    cuts: list[Cut] = []
    for (request, start_ms, end_ms, words), clip in zip(spans, clips, strict=True):
        audio = _write(clip, rate)
        shifted = tuple(
            WordTiming(
                word=w.word,
                start_ms=max(0, w.start_ms - start_ms),
                end_ms=max(1, w.end_ms - start_ms),
            )
            for w in words
        )
        cuts.append(
            Cut(
                beat_id=request.beat_id,
                audio=audio,
                words=shifted,
                duration_ms=max(1, end_ms - start_ms),
                take_span_ms=(start_ms, end_ms),
            )
        )
    return cuts


def segment_for(cut: Cut, request: NarrationRequest, take: NarrationSegment) -> NarrationSegment:
    """A per-beat segment built from one piece of a shared take."""
    return NarrationSegment(
        beat_id=request.beat_id,
        audio_sha256=hashlib.sha256(cut.audio).hexdigest(),
        audio_format="wav",
        sample_rate_hz=take.sample_rate_hz,
        channels=1,
        duration_ms=cut.duration_ms,
        words=cut.words,
        timing_source=take.timing_source,
        voice=request.voice,
        spoken_text=request.spoken_text,
        display_text=request.display_text,
        normalization_version=request.normalization_version,
    )
