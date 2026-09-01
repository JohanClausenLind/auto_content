"""Phase-3: mock TTS determinism, alignment validation, character-alignment collapsing."""

from __future__ import annotations

import base64
import io
import json
import struct
import wave

import httpx
import pytest

from content_factory.audio.alignment import validate_alignment
from content_factory.audio.normalize import normalize_for_speech, tokenize_words
from content_factory.audio.tts import (
    ElevenLabsTTS,
    MockTTS,
    TTSError,
    words_from_character_alignment,
)
from content_factory.schemas.audio import NarrationRequest, PronunciationEntry, VoiceIdentity
from content_factory.schemas.scenes import WordTiming

VOICE = VoiceIdentity(provider="mock", voice_id="narrator-a", model_revision="mock-1")


def req(text: str, **over) -> NarrationRequest:
    return NarrationRequest(
        beat_id="beat_000000001",
        display_text=text,
        spoken_text=normalize_for_speech(text),
        voice=over.pop("voice", VOICE),
        **over,
    )


def test_normalization_applies_lexicon_and_units() -> None:
    lex = (PronunciationEntry(term="Energimyndigheten", respelling="en-er-gee-MIN-dig-heh-ten"),)
    out = normalize_for_speech("Energimyndigheten reports 21% growth, e.g. wind.", lex)
    assert "en-er-gee-MIN-dig-heh-ten" in out
    assert "21 percent" in out and "e.g." not in out


def test_mock_tts_is_deterministic_and_timed_within_audio() -> None:
    r = req("In 2025, wind supplied about a fifth of Sweden's electricity.")
    a = MockTTS().synthesize(r)
    b = MockTTS().synthesize(r)
    assert a.audio == b.audio
    assert a.segment.audio_sha256 == b.segment.audio_sha256
    assert a.segment.words == b.segment.words
    assert len(a.segment.words) == len(tokenize_words(r.spoken_text))
    with wave.open(io.BytesIO(a.audio)) as wf:
        assert wf.getframerate() == 24000
        real_ms = wf.getnframes() * 1000 // 24000
    assert abs(real_ms - a.segment.duration_ms) <= 2
    report = validate_alignment(a.segment)
    assert report.passed and report.coverage == 1.0
    # A different voice or speed changes the audio.
    c = MockTTS().synthesize(
        req(
            "In 2025, wind supplied about a fifth of Sweden's electricity.",
            voice=VOICE.model_copy(update={"voice_id": "narrator-b"}),
        )
    )
    assert c.segment.audio_sha256 != a.segment.audio_sha256


def test_alignment_validator_catches_injected_defects() -> None:
    seg = (
        MockTTS()
        .synthesize(req("Three things drove it: new turbines, better siting, and cheaper finance."))
        .segment
    )
    words = list(seg.words)
    # Overlap
    w1 = words[1]
    bad = seg.model_copy(
        update={
            "words": (
                words[0],
                WordTiming(word=w1.word, start_ms=max(0, words[0].start_ms - 80), end_ms=w1.end_ms),
                *words[2:],
            )
        }
    )
    r = validate_alignment(bad)
    assert not r.passed and any(f.check in {"overlap", "monotonic"} for f in r.findings)
    # Missing words
    r2 = validate_alignment(seg.model_copy(update={"words": tuple(words[: len(words) // 2])}))
    assert any(f.check == "missing_words" for f in r2.findings) and r2.coverage < 1.0
    # Long gap
    shifted = [
        *words[:2],
        *(
            w.model_copy(update={"start_ms": w.start_ms + 2000, "end_ms": w.end_ms + 2000})
            for w in words[2:]
        ),
    ]
    cap = max(w.end_ms for w in shifted)
    r3 = validate_alignment(
        seg.model_copy(update={"words": tuple(shifted), "duration_ms": cap + 200})
    )
    assert any(f.check == "gap" for f in r3.findings)


def test_character_alignment_collapses_to_words() -> None:
    text = "Hello brave world"
    chars = list("Hello brave world")
    starts = [i * 0.05 for i in range(len(chars))]
    ends = [s + 0.05 for s in starts]
    words = words_from_character_alignment(text, chars, starts, ends)
    assert [w.word for w in words] == ["Hello", "brave", "world"]
    assert words[0].start_ms == 0 and words[0].end_ms == 250
    assert words[2].end_ms == 850
    with pytest.raises(TTSError, match="3 words for 2"):
        words_from_character_alignment("Hello world", chars, starts, ends)


def test_elevenlabs_adapter_parses_with_timestamps_response() -> None:
    pcm = struct.pack("<2400h", *([1000] * 2400))  # 0.1 s at 24 kHz

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["xi-api-key"] == "test-key"
        assert "/with-timestamps" in str(request.url)
        body = json.loads(request.content)
        assert body["text"].startswith("Hello")
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(pcm).decode(),
                "alignment": {
                    "characters": list("Hello world"),
                    "character_start_times_seconds": [i * 0.01 for i in range(11)],
                    "character_end_times_seconds": [i * 0.01 + 0.01 for i in range(11)],
                },
            },
        )

    tts = ElevenLabsTTS("test-key", transport=httpx.MockTransport(handler))
    voice = VoiceIdentity(
        provider="elevenlabs", voice_id="voice123", model_revision="eleven_multilingual_v2"
    )
    result = tts.synthesize(
        NarrationRequest(
            beat_id="beat_000000001",
            display_text="Hello world",
            spoken_text="Hello world",
            voice=voice,
        )
    )
    assert [w.word for w in result.segment.words] == ["Hello", "world"]
    assert result.segment.timing_source == "provider"
    assert result.segment.sample_rate_hz == 24000
    assert result.segment.duration_ms == 110  # last aligned word ends after the PCM payload

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad key"})

    with pytest.raises(TTSError, match="401"):
        ElevenLabsTTS("bad", transport=httpx.MockTransport(error_handler)).synthesize(
            NarrationRequest(
                beat_id="beat_000000001", display_text="x", spoken_text="x", voice=voice
            )
        )
