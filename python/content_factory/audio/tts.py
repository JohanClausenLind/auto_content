"""`voice.synthesize` skill contract with interchangeable executors (ADR 0004).

* MockTTS      — deterministic offline audio + synthetic timings (core CI).
* ElevenLabsTTS — premium, character-level alignment → word timings (respx-tested).
* Qwen3TTS     — the local narration voice: Apache-2.0 weights, nine built-in timbres, style
                 control, ten languages. It returns **no** timings, so the word boundaries come
                 from forced alignment against the audio it just produced (ADR-0004 precedence:
                 provider → forced alignment → ASR).
* KokoroTTS    — the local fallback: smaller, English-leaning, and the only executor here whose
                 timings come from the model itself, which makes it the offline-cheap option.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import subprocess
import tempfile
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

    def fingerprint(self) -> dict[str, object]:
        """Everything about this executor that changes the audio or the timings, for the caller's
        cache key. The ``NarrationRequest`` covers the voice and the text; this covers the rest —
        a delivery instruction or a different aligner has to re-speak the beat, not reuse it."""
        return {}


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
            freq = 160 + ((seed >> (i % 16)) & 0x3F) * 3  # 160-349 Hz, deterministic per word
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
            if current and start is not None:
                words.append(
                    WordTiming(
                        word="".join(current).strip(".,;:!?\"'()"),
                        start_ms=round(start * 1000),
                        end_ms=round(end * 1000),
                    )
                )
                current, start = [], None
            continue
        if start is None:
            start = s
        current.append(ch)
        end = e
    if current and start is not None:
        words.append(
            WordTiming(
                word="".join(current).strip(".,;:!?\"'()"),
                start_ms=round(start * 1000),
                end_ms=round(end * 1000),
            )
        )
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


class Qwen3TTS(TTSExecutor):
    """Runs `skills/audio/qwen3tts/run.py` in its own uv environment, then times what came back.

    Two things make this different from :class:`KokoroTTS`. The voice is chosen by timbre name
    (``ryan``, ``serena``, …) with an optional natural-language ``instruct`` for delivery, or
    cloned from a reference clip on the Base weights. And Qwen3-TTS prints ``tokens: []`` — it has
    no word timestamps at all — so the timings are measured here by forced alignment and snapped
    onto the locked script, exactly the way a human recording is timed in
    :mod:`content_factory.audio.takes`. ``even_split`` is the offline stand-in; it apportions the
    measured duration by word length and records itself as ``estimated``, never as measured.

    The transcript the aligner produces is also compared with the script, so a beat the model
    garbled or truncated fails by name instead of shipping with captions that drift against it.
    """

    provider = "qwen3tts"

    def __init__(
        self,
        skill_dir: Path,
        *,
        device: str = "cuda:0",
        language: str = "english",
        instruct: str = "",
        ref_audio: Path | None = None,
        ref_text: str = "",
        aligner: str = "faster_whisper",
        faster_whisper_model: str = "base.en",
        compute_type: str = "int8",
        timeout_s: int = 900,
        aligner_timeout_s: int = 600,
        script_similarity_min: float = 0.8,
        seed: int | None = None,
    ) -> None:
        self.skill_dir = skill_dir
        self.device = device
        self.seed = seed
        self.language = language
        self.instruct = instruct
        self.ref_audio = ref_audio
        self.ref_text = ref_text
        self.aligner = aligner
        self.faster_whisper_model = faster_whisper_model
        self.compute_type = compute_type
        self.timeout_s = timeout_s
        self.aligner_timeout_s = aligner_timeout_s
        self.script_similarity_min = script_similarity_min
        self.last_script_check = ""
        """How the most recent beat's script check went, for the stage's run record. Set by
        `_time_words`; empty until a beat has been timed by the aligner."""

    def fingerprint(self) -> dict[str, object]:
        return {
            "language": self.language,
            # In the cache key on purpose: a take is only reusable if it would be regenerated the
            # same way, and the seed is what decides that for a sampling model.
            "seed": self.seed,
            "instruct": self.instruct,
            "ref_audio": self.ref_audio.name if self.ref_audio else "",
            "ref_text": self.ref_text,
            "aligner": self.aligner,
            "aligner_model": self.faster_whisper_model if self.aligner != "even_split" else "",
        }

    def _generate(self, request: NarrationRequest, out_wav: Path) -> dict:
        cmd = [
            "uv",
            "run",
            "--project",
            str(self.skill_dir),
            "python",
            str(self.skill_dir / "run.py"),
            "--language",
            self.language,
            "--device",
            self.device,
            "--out",
            str(out_wav),
        ]
        if self.seed is not None:
            cmd += ["--seed", str(self.seed)]
        if self.ref_audio is not None:
            # Base weights: clone the reference voice. It declares no built-in speakers at all.
            cmd += ["--ref-audio", str(self.ref_audio), "--ref-text", self.ref_text]
        else:
            cmd += ["--speaker", request.voice.voice_id]
            if self.instruct:
                cmd += ["--instruct", self.instruct]
        proc = subprocess.run(  # noqa: S603
            cmd,
            input=request.spoken_text,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            check=False,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        if proc.returncode != 0:
            raise TTSError(f"qwen3-tts failed: {proc.stderr[-1000:]}")
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        if not lines:
            raise TTSError("qwen3-tts printed no result line")
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise TTSError(f"qwen3-tts printed {lines[-1][:200]!r}, not JSON") from exc

    def _time_words(
        self, wav: Path, request: NarrationRequest, duration_ms: int
    ) -> tuple[list[WordTiming], TimingSource]:
        from content_factory.audio.takes import (
            TakeError,
            even_split,
            faster_whisper_words,
            snap_to_script,
        )

        script_words = tokenize_words(request.spoken_text)
        if not script_words:
            raise TTSError("nothing to speak")
        if self.aligner == "even_split":
            return even_split(script_words, duration_ms), TimingSource.estimated
        if self.aligner != "faster_whisper":
            raise TTSError(
                f"aligner {self.aligner!r} is not installed on this host;"
                " use faster_whisper or even_split"
            )
        from content_factory.human_tasks.validation import validate_take

        try:
            heard, spans = faster_whisper_words(
                wav,
                model=self.faster_whisper_model,
                compute_type=self.compute_type,
                timeout_s=self.aligner_timeout_s,
            )
        except TakeError as exc:
            raise TTSError(f"alignment failed for {request.beat_id}: {exc}") from exc
        # A beat whose script carries a pronunciation respelling cannot be checked this way, and
        # the check has to be skipped rather than failed. Measured on the narrated-video lane
        # (2026-09-08): the beat "Sources: Energimyndigheten, Svenska kraftnät." scored **0.33**
        # with the raw spelling — the model said "energym and de hetten" — and **0.22** with the
        # respelling that fixed the audio, because the respelling is deliberately not orthographic
        # and `base.en` transcribed it as "energi mundinghen". Both numbers measure the aligner's
        # vocabulary, not whether the model obeyed. Skipped, recorded, and every beat without a
        # respelling is still gated exactly as before.
        respelled = tuple(
            e.term for e in request.lexicon if e.respelling and e.respelling in request.spoken_text
        )
        if respelled:
            self.last_script_check = f"skipped: respelled {', '.join(respelled)}"
        else:
            review = validate_take(
                wav,
                request.spoken_text,
                transcriber=lambda _p: " ".join(heard),
                tolerance=self.script_similarity_min,
                # A narration beat can legitimately be well under the one second a recorded take
                # is held to; what matters here is whether the model said the script.
                min_duration_s=0.2,
            )
            if review.similarity < self.script_similarity_min:
                raise TTSError(
                    f"{request.beat_id}: qwen3-tts did not speak the locked script"
                    f" (similarity {review.similarity:.2f} < {self.script_similarity_min:.2f});"
                    f" diff: {' '.join(review.diff)[:200]}"
                )
            self.last_script_check = f"similarity {review.similarity:.2f}"
        return snap_to_script(script_words, spans, duration_ms), TimingSource.forced_alignment

    def synthesize(self, request: NarrationRequest) -> SynthesisResult:
        with tempfile.TemporaryDirectory(prefix="cf-qwen3tts-") as tmp:
            wav = Path(tmp) / f"{request.beat_id}.wav"
            out = self._generate(request, wav)
            audio = wav.read_bytes()
            sample_rate = int(out["sample_rate"])
            duration_ms = int(out["duration_ms"])
            words, source = self._time_words(wav, request, duration_ms)
        seg = NarrationSegment(
            beat_id=request.beat_id,
            audio_sha256=sha256_hex(audio),
            sample_rate_hz=sample_rate,
            duration_ms=duration_ms,
            words=tuple(words),
            timing_source=source,
            voice=request.voice.model_copy(
                update={"model_revision": str(out.get("model_revision", "Qwen3-TTS-12Hz-1.7B"))}
            ),
            spoken_text=request.spoken_text,
            display_text=request.display_text,
            normalization_version=request.normalization_version,
        )
        return SynthesisResult(seg, audio)


class KokoroTTS(TTSExecutor):
    """Runs `skills/audio/kokoro/run.py` in its own uv environment (torch pins never touch the
    control plane). The script prints one JSON line with wav path + token timestamps.

    Kept as the fallback after Qwen3-TTS took the narration role (2026-09-07): it is 82M
    parameters against 1.7B, needs no aligner because it times its own tokens, and runs on CPU in
    seconds — which is exactly what you want when the card is busy or the aligner is unavailable.
    """

    provider = "kokoro"

    def __init__(
        self,
        skill_dir: Path,
        *,
        python: str | None = None,
        device: str = "cpu",
        lang_code: str = "",
    ) -> None:
        self.skill_dir = skill_dir
        self.python = python
        self.device = device  # cpu by default: the GPU is held by the image/video models
        # Kokoro's own single-letter G2P code, resolved and validated by the control plane
        # (`audio.languages.check_narration_language`). Empty leaves the resolution to run.py,
        # which applies the same table — one of the two has to be authoritative, and it is this
        # one, because it is the side that can refuse a run before the weights load.
        self.lang_code = lang_code

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
            self.lang_code or request.voice.locale,
            "--device",
            self.device,
        ]
        proc = subprocess.run(  # noqa: S603
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
        # Kokoro times punctuation as its own tokens ("," "."); word timings are words only, or
        # the alignment validator counts them as words the script never had.
        words = [
            WordTiming(
                word=t["text"],
                start_ms=round(t["start_ts"] * 1000),
                end_ms=round(t["end_ts"] * 1000),
            )
            for t in out["tokens"]
            if t.get("start_ts") is not None and any(ch.isalnum() for ch in t["text"])
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
