"""`voice.synthesize` skill contract with interchangeable executors (ADR 0004).

* MockTTS      — deterministic offline audio + synthetic timings (core CI).
* ElevenLabsTTS — premium, character-level alignment → word timings (respx-tested).
* KokoroTTS    — local Apache-2.0 model with token timestamps, run in an isolated environment.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import subprocess
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx

from content_factory.audio.normalize import tokenize_words
from content_factory.schemas.audio import NarrationRequest, NarrationSegment, TimingSource
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.scenes import WordTiming


class TTSError(Exception):
    pass


@dataclass(frozen=True)
class SynthesisResult:
    segment: NarrationSegment
    audio: bytes  # WAV bytes


class TTSExecutor(ABC):
    provider: str

    @abstractmethod
    def synthesize(self, request: NarrationRequest) -> SynthesisResult: ...


def _wav_bytes(samples: list[int], sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


class MockTTS(TTSExecutor):
    """Deterministic: each word becomes a short tone burst whose length follows its syllable count;
    the same request always yields identical bytes and timings. Never used for real output."""

    provider = "mock"
    SAMPLE_RATE = 24000

    def synthesize(self, request: NarrationRequest) -> SynthesisResult:
        words = tokenize_words(request.spoken_text)
        if not words:
            raise TTSError("nothing to speak")
        speed = request.voice.speed
        seed = int(
            hashlib.sha256(f"{request.voice.voice_id}|{request.spoken_text}".encode()).hexdigest()[
                :8
            ],
            16,
        )
        timings: list[WordTiming] = []
        samples: list[int] = []
        cursor_ms = 120  # lead-in silence
        samples.extend([0] * (self.SAMPLE_RATE * cursor_ms // 1000))
        for i, w in enumerate(words):
            syllables = max(1, sum(1 for ch in w.lower() if ch in "aeiouy"))
            dur = int((130 + 115 * syllables) / speed)
            freq = 160 + ((seed >> (i % 16)) & 0x3F) * 3  # 160–349 Hz, deterministic per word
            n = self.SAMPLE_RATE * dur // 1000
            for k in range(n):
                env = min(1.0, k / 240, (n - k) / 240)
                samples.append(
                    int(6000 * env * math.sin(2 * math.pi * freq * k / self.SAMPLE_RATE))
                )
            timings.append(WordTiming(word=w, start_ms=cursor_ms, end_ms=cursor_ms + dur))
            cursor_ms += dur
            gap = 80 if not w.endswith((",", ";")) else 200
            if i < len(words) - 1:
                samples.extend([0] * (self.SAMPLE_RATE * gap // 1000))
                cursor_ms += gap
        samples.extend([0] * (self.SAMPLE_RATE * 200 // 1000))
        cursor_ms += 200
        audio = _wav_bytes(samples, self.SAMPLE_RATE)
        seg = NarrationSegment(
            beat_id=request.beat_id,
            audio_sha256=sha256_hex(audio),
            sample_rate_hz=self.SAMPLE_RATE,
            duration_ms=cursor_ms,
            words=tuple(timings),
            timing_source=TimingSource.synthetic,
            voice=request.voice,
            spoken_text=request.spoken_text,
            display_text=request.display_text,
            normalization_version=request.normalization_version,
        )
        return SynthesisResult(seg, audio)


def words_from_character_alignment(
    text: str, chars: list[str], starts_s: list[float], ends_s: list[float]
) -> list[WordTiming]:
    """Collapse a character-level alignment (ElevenLabs shape) into word timings in integer ms."""
    if not (len(chars) == len(starts_s) == len(ends_s)):
        raise TTSError("alignment arrays differ in length")
    words: list[WordTiming] = []
    current: list[str] = []
    start: float | None = None
    end: float = 0.0
    for ch, s, e in zip(chars, starts_s, ends_s, strict=True):
        if ch.isspace():
            if current:
                words.append(
                    WordTiming(
                        word="".join(current).strip(".,;:!?\"'()"),
                        start_ms=round(start * 1000),
                        end_ms=round(end * 1000),
                    )
                )  # type: ignore[arg-type]
                current, start = [], None
            continue
        if start is None:
            start = s
        current.append(ch)
        end = e
    if current:
        words.append(
            WordTiming(
                word="".join(current).strip(".,;:!?\"'()"),
                start_ms=round(start * 1000),
                end_ms=round(end * 1000),
            )
        )  # type: ignore[arg-type]
    words = [w for w in words if w.word]
    if len(words) != len(tokenize_words(text)):
        raise TTSError(
            f"alignment produced {len(words)} words for {len(tokenize_words(text))} script words"
        )
    return words


class ElevenLabsTTS(TTSExecutor):
    """POST /v1/text-to-speech/{voice_id}/with-timestamps → base64 audio + character alignment."""

    provider = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.elevenlabs.io",
        model_id: str = "eleven_multilingual_v2",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise TTSError("ELEVENLABS_API_KEY missing")
        self._http = httpx.Client(
            base_url=base_url, headers={"xi-api-key": api_key}, timeout=120, transport=transport
        )
        self.model_id = model_id

    def synthesize(self, request: NarrationRequest) -> SynthesisResult:
        import base64

        resp = self._http.post(
            f"/v1/text-to-speech/{request.voice.voice_id}/with-timestamps",
            params={"output_format": "pcm_24000"},
            json={
                "text": request.spoken_text,
                "model_id": self.model_id,
                "voice_settings": {"speed": request.voice.speed},
            },
        )
        if resp.status_code >= 400:
            raise TTSError(f"elevenlabs {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        pcm = base64.b64decode(data["audio_base64"])
        align = data.get("alignment") or data.get("normalized_alignment")
        words = words_from_character_alignment(
            request.spoken_text,
            align["characters"],
            align["character_start_times_seconds"],
            align["character_end_times_seconds"],
        )
        samples = list(struct.unpack(f"<{len(pcm) // 2}h", pcm[: len(pcm) - len(pcm) % 2]))
        audio = _wav_bytes(samples, 24000)
        duration_ms = max(len(samples) * 1000 // 24000, words[-1].end_ms)
        seg = NarrationSegment(
            beat_id=request.beat_id,
            audio_sha256=sha256_hex(audio),
            sample_rate_hz=24000,
            duration_ms=duration_ms,
            words=tuple(words),
            timing_source=TimingSource.provider,
            voice=request.voice.model_copy(update={"model_revision": self.model_id}),
            spoken_text=request.spoken_text,
            display_text=request.display_text,
            normalization_version=request.normalization_version,
        )
        return SynthesisResult(seg, audio)


class KokoroTTS(TTSExecutor):
    """Runs `skills/audio/kokoro/run.py` in its own uv environment (torch pins never touch the
    control plane). The script prints one JSON line with wav path + token timestamps."""

    provider = "kokoro"

    def __init__(self, skill_dir: Path, *, python: str | None = None) -> None:
        self.skill_dir = skill_dir
        self.python = python

    def synthesize(self, request: NarrationRequest) -> SynthesisResult:
        cmd = (
            [self.python]
            if self.python
            else ["uv", "run", "--project", str(self.skill_dir), "python"]
        )
        cmd += [
            str(self.skill_dir / "run.py"),
            "--voice",
            request.voice.voice_id,
            "--speed",
            str(request.voice.speed),
            "--lang",
            request.voice.locale,
        ]
        proc = subprocess.run(
            cmd,
            input=request.spoken_text,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        if proc.returncode != 0:
            raise TTSError(f"kokoro failed: {proc.stderr[-1000:]}")
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        audio = Path(out["wav"]).read_bytes()
        words = [
            WordTiming(
                word=t["text"],
                start_ms=int(round(t["start_ts"] * 1000)),
                end_ms=int(round(t["end_ts"] * 1000)),
            )
            for t in out["tokens"]
            if t.get("start_ts") is not None
        ]
        seg = NarrationSegment(
            beat_id=request.beat_id,
            audio_sha256=sha256_hex(audio),
            sample_rate_hz=int(out["sample_rate"]),
            duration_ms=int(out["duration_ms"]),
            words=tuple(words),
            timing_source=TimingSource.provider,
            voice=request.voice.model_copy(
                update={"model_revision": str(out.get("model_revision", "kokoro-82m"))}
            ),
            spoken_text=request.spoken_text,
            display_text=request.display_text,
            normalization_version=request.normalization_version,
        )
        return SynthesisResult(seg, audio)
