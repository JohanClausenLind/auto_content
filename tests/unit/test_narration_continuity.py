"""Beats spoken as one take, cut at the pauses."""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from content_factory.audio.continuity import (
    ContinuityError,
    Cut,
    cut_take,
    joined_text,
    match_levels,
    segment_for,
    speech_level_db,
)
from content_factory.schemas.audio import (
    NarrationRequest,
    NarrationSegment,
    TimingSource,
    VoiceIdentity,
    WordTiming,
)

RATE = 16000


def _voice() -> VoiceIdentity:
    return VoiceIdentity(provider="mock", voice_id="v", model_revision="r", speed=1.0)


def _request(beat: str, text: str) -> NarrationRequest:
    return NarrationRequest(beat_id=beat, display_text=text, spoken_text=text, voice=_voice())


def _tone(seconds: float, amplitude: float = 0.3, freq: float = 180.0) -> list[int]:
    return [
        int(amplitude * 32767 * math.sin(2 * math.pi * freq * t / RATE))
        for t in range(int(seconds * RATE))
    ]


def _take_wav(blocks: list[list[int]]) -> bytes:
    samples = [s for block in blocks for s in block]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def _fixture() -> tuple[list[NarrationRequest], bytes, list[WordTiming], int]:
    """Two beats of two words each, with a half-second pause between the beats."""
    requests = [
        _request("bea_one00000001", "alpha bravo"),
        _request("bea_two00000001", "charlie delta"),
    ]
    # alpha/bravo, 0.5 s silence, charlie/delta, then trailing silence: without it the last
    # piece inherits the take's abrupt end, which is the fixture's fault not the cutter's.
    audio = _take_wav(
        [_tone(0.8), [0] * int(0.5 * RATE), _tone(0.8, amplitude=0.15), [0] * int(0.3 * RATE)]
    )
    words = [
        WordTiming(word="alpha", start_ms=0, end_ms=400),
        WordTiming(word="bravo", start_ms=400, end_ms=800),
        WordTiming(word="charlie", start_ms=1300, end_ms=1700),
        WordTiming(word="delta", start_ms=1700, end_ms=2100),
    ]
    return requests, audio, words, 2400


def test_the_cut_lands_in_the_middle_of_the_real_pause() -> None:
    """Found in the AUDIO, and centred in the quiet — not at the first quiet sample."""
    requests, audio, words, duration = _fixture()
    cuts = cut_take(requests, audio, words, duration, match=False)
    assert [c.take_span_ms for c in cuts] == [(0, 1050), (1050, 2400)]
    for cut in cuts:
        for word in cut.words:
            assert 0 <= word.start_ms < word.end_ms <= cut.duration_ms


def test_the_cut_is_the_same_whether_the_aligner_left_gaps_or_not() -> None:
    """The bug this replaces, and the reason the split is not taken from the word times at all."""
    requests, audio, words, duration = _fixture()
    contiguous = [
        WordTiming(word="alpha", start_ms=0, end_ms=400),
        WordTiming(word="bravo", start_ms=400, end_ms=1050),
        WordTiming(word="charlie", start_ms=1050, end_ms=1700),
        WordTiming(word="delta", start_ms=1700, end_ms=2100),
    ]
    assert all(
        contiguous[i + 1].start_ms - contiguous[i].end_ms == 0 for i in range(len(contiguous) - 1)
    ), "the fixture has to actually be gapless for this to prove anything"
    spaced = [c.take_span_ms for c in cut_take(requests, audio, words, duration, match=False)]
    gapless = [c.take_span_ms for c in cut_take(requests, audio, contiguous, duration, match=False)]
    assert spaced == gapless == [(0, 1050), (1050, 2400)]


def test_a_cut_does_not_end_on_a_loud_sample() -> None:
    """What "cut off" sounded like: the piece stopping mid-decay rather than in the pause."""
    import numpy as np

    requests, audio, words, duration = _fixture()
    for cut in cut_take(requests, audio, words, duration, match=False):
        with wave.open(io.BytesIO(cut.audio)) as handle:
            rate = handle.getframerate()
            data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
        samples = data.astype("float32") / 32768.0
        tail = samples[-int(0.04 * rate) :]
        tail_db = 20 * math.log10(max(float(np.sqrt((tail**2).mean())), 1e-9))
        whole_db = 20 * math.log10(max(float(np.sqrt((samples**2).mean())), 1e-9))
        assert tail_db - whole_db < -20, "the piece ends mid-sound"


def test_word_times_are_rebased_onto_each_piece() -> None:
    requests, audio, words, duration = _fixture()
    first, second = cut_take(requests, audio, words, duration, match=False)
    assert [(w.word, w.start_ms) for w in first.words] == [("alpha", 0), ("bravo", 400)]
    # the second beat starts 1050 ms into the take, so its first word is at 250, not 1300
    assert [(w.word, w.start_ms) for w in second.words] == [("charlie", 250), ("delta", 650)]


def test_levels_are_matched_across_the_cut_beats() -> None:
    """Even inside one take the level drifts: 5.4 dB, longest sentences quietest."""
    requests, audio, words, duration = _fixture()
    raw = cut_take(requests, audio, words, duration, match=False)
    matched = cut_take(requests, audio, words, duration, match=True)

    def spread(cuts: list[Cut]) -> float:
        import numpy as np

        levels = []
        for cut in cuts:
            with wave.open(io.BytesIO(cut.audio)) as handle:
                data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
            levels.append(speech_level_db(data.astype("float32") / 32768.0, RATE))
        return max(levels) - min(levels)

    assert spread(raw) > 4.0
    assert spread(matched) < spread(raw) - 2.0


def test_the_correction_is_capped_so_intended_shading_survives() -> None:
    """A narration may drop for an intimate line; it may not drop 5 dB because the line was long."""
    import numpy as np

    loud = np.full(RATE, 0.4, dtype="float32")
    quiet = np.full(RATE, 0.002, dtype="float32")  # ~46 dB down, far past the cap
    matched = match_levels([loud, quiet], RATE, max_db=3.0)
    moved = speech_level_db(matched[1], RATE) - speech_level_db(quiet, RATE)
    assert moved == pytest.approx(3.0, abs=0.2)


def test_a_take_that_came_apart_from_the_script_is_refused_by_name() -> None:
    """The one failure that must not be silent."""
    requests, audio, words, duration = _fixture()
    with pytest.raises(ContinuityError, match="come apart"):
        cut_take(requests, audio, words[:-1], duration, match=False)


def test_the_paragraph_is_the_beats_in_order() -> None:
    requests, _, _, _ = _fixture()
    assert joined_text(requests) == "alpha bravo charlie delta"


def test_each_piece_becomes_a_segment_the_rest_of_the_pipeline_accepts() -> None:
    """Captions, the timeline compiler and the mix never learn the beats shared a take."""
    requests, audio, words, duration = _fixture()
    cuts = cut_take(requests, audio, words, duration)
    take = NarrationSegment(
        beat_id=requests[0].beat_id,
        audio_sha256="0" * 64,
        sample_rate_hz=RATE,
        duration_ms=duration,
        words=tuple(words),
        timing_source=TimingSource.forced_alignment,
        voice=_voice(),
        spoken_text=joined_text(requests),
        display_text=joined_text(requests),
        normalization_version="1",
    )
    for cut, request in zip(cuts, requests, strict=True):
        segment = segment_for(cut, request, take)
        assert segment.beat_id == request.beat_id
        assert segment.spoken_text == request.spoken_text
        assert segment.timing_source is TimingSource.forced_alignment
        assert segment.words[-1].end_ms <= segment.duration_ms
