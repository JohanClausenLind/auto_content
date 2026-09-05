"""synthesize_narration: TTS selection by settings and per-beat caching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.audio.tts import KokoroTTS, MockTTS, SynthesisResult, TTSExecutor
from content_factory.config import get_settings
from content_factory.schemas.audio import NarrationRequest
from content_factory.schemas.fixtures import sample_campaign
from content_factory.workflows import stages as st
from content_factory.workflows.stages import StageContext, stage_synthesize_narration


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    campaign = sample_campaign()
    return StageContext(
        workspace_id=campaign.workspace_id,
        project_dir=tmp_path / "project",
        artifacts_dir=tmp_path / "artifacts",
        campaign=campaign,
        deliverable_id="dlv_testvideo001",
        quality="smoke",
        dep_outputs={},
    )


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    def _set(**kw: str) -> None:
        for k, v in kw.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()  # type: ignore[attr-defined]

    yield _set
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_default_is_the_mock_and_kokoro_is_selectable(env) -> None:
    tts, voice = st._tts_executor()
    assert isinstance(tts, MockTTS) and voice.provider == "mock"
    env(
        CF__NARRATION__TTS="kokoro",
        CF__NARRATION__KOKORO_VOICE="bf_emma",
        CF__NARRATION__SPEED="1.1",
    )
    tts, voice = st._tts_executor()
    assert isinstance(tts, KokoroTTS) and tts.skill_dir.name == "kokoro"
    assert (voice.provider, voice.voice_id, voice.speed) == ("kokoro", "bf_emma", 1.1)


class CountingTTS(TTSExecutor):
    provider = "mock"

    def __init__(self) -> None:
        self.calls = 0
        self.inner = MockTTS()

    def synthesize(self, request: NarrationRequest) -> SynthesisResult:
        self.calls += 1
        return self.inner.synthesize(request)


def test_beats_are_spoken_once_and_reused_until_the_script_or_voice_changes(
    ctx: StageContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from content_factory.runners import demo as demo_fixtures

    tts = CountingTTS()
    monkeypatch.setattr(st, "_tts_executor", lambda _ctx=None: (tts, demo_fixtures.MOCK_VOICE))
    first = stage_synthesize_narration(ctx)
    assert first.facts["spoken"] == 4 and tts.calls == 4
    markers = sorted((ctx.ddir() / "audio").glob("*.tts.json"))
    assert len(markers) == 4 and "audio_sha256" in json.loads(markers[0].read_text())

    second = stage_synthesize_narration(ctx)
    assert second.facts["spoken"] == 0 and tts.calls == 4
    assert second.outputs_hash == first.outputs_hash

    faster = demo_fixtures.MOCK_VOICE.model_copy(update={"speed": 1.3})
    monkeypatch.setattr(st, "_tts_executor", lambda _ctx=None: (tts, faster))
    third = stage_synthesize_narration(ctx)
    assert third.facts["spoken"] == 4 and tts.calls == 8  # a new voice re-speaks every beat


def test_kokoro_drops_punctuation_tokens_from_word_timings(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    from content_factory.audio import tts as tts_mod
    from content_factory.schemas.audio import VoiceIdentity

    wav = tmp_path / "out.wav"
    wav.write_bytes(b"RIFF" + b"\0" * 40)
    payload = {
        "wav": str(wav),
        "sample_rate": 24000,
        "duration_ms": 1200,
        "tokens": [
            {"text": "In", "start_ts": 0.0, "end_ts": 0.2},
            {"text": "2025", "start_ts": 0.2, "end_ts": 0.7},
            {"text": ",", "start_ts": 0.7, "end_ts": 0.8},
            {"text": "wind", "start_ts": 0.8, "end_ts": 1.1},
            {"text": ".", "start_ts": 1.1, "end_ts": 1.2},
        ],
    }

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload) + "\n", "")

    monkeypatch.setattr(tts_mod.subprocess, "run", fake_run)
    voice = VoiceIdentity(
        provider="kokoro", voice_id="af_heart", model_revision="hexgrad/Kokoro-82M"
    )
    result = KokoroTTS(tmp_path).synthesize(
        NarrationRequest(
            beat_id="beat_000000001",
            display_text="In 2025, wind.",
            spoken_text="In 2025, wind.",
            voice=voice,
        )
    )
    assert [w.word for w in result.segment.words] == ["In", "2025", "wind"]
    assert result.segment.timing_source == "provider" and result.segment.duration_ms == 1200


# ---- Qwen3-TTS: the narration voice since 2026-09-07 -------------------------------------------
#
# It returns no word timings at all, so the interesting behaviour is not the audio — it is that the
# timings are measured afterwards and snapped onto the locked script, and that a beat the model did
# not actually speak is refused rather than shipped with captions that drift against it.

QWEN_LINE = "In late 2024, Swedish wind out-produced nuclear."


def _pcm_wav(path: Path, *, seconds: float = 2.0, rate: int = 24000) -> Path:
    import math
    import struct
    import wave as wave_mod

    n = int(rate * seconds)
    samples = [round(8000 * math.sin(2 * math.pi * 180 * i / rate)) for i in range(n)]
    with wave_mod.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{n}h", *samples))
    return path


def _qwen_request() -> NarrationRequest:
    from content_factory.schemas.audio import VoiceIdentity

    return NarrationRequest(
        beat_id="beat_000000001",
        display_text=QWEN_LINE,
        spoken_text=QWEN_LINE,
        voice=VoiceIdentity(
            provider="qwen3tts",
            voice_id="ryan",
            model_revision="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        ),
    )


def _fake_skill(monkeypatch: pytest.MonkeyPatch, wav_seconds: float = 2.0):
    """Stand in for `skills/audio/qwen3tts/run.py`: writes the wav it was asked for and prints the
    one JSON line the real script prints — `tokens` empty, because the model has no timings."""
    import subprocess

    from content_factory.audio import tts as tts_mod

    seen: dict[str, list[str]] = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = list(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        _pcm_wav(out, seconds=wav_seconds)
        payload = {
            "wav": str(out),
            "sample_rate": 24000,
            "duration_ms": int(wav_seconds * 1000),
            "tokens": [],
            "mode": "custom_voice",
            "speaker": "ryan",
            "model_revision": "Qwen3-TTS-12Hz-1.7B-CustomVoice",
        }
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload) + "\n", "")

    monkeypatch.setattr(tts_mod.subprocess, "run", fake_run)
    return seen


def test_qwen3tts_even_split_times_every_script_word(tmp_path: Path, monkeypatch) -> None:
    from content_factory.audio.normalize import tokenize_words
    from content_factory.audio.tts import Qwen3TTS

    seen = _fake_skill(monkeypatch)
    result = Qwen3TTS(tmp_path, aligner="even_split", instruct="Calm.").synthesize(_qwen_request())
    seg = result.segment
    assert [w.word for w in seg.words] == tokenize_words(QWEN_LINE)
    # even_split never listens, so it must not claim the timings were measured.
    assert seg.timing_source == "estimated"
    assert seg.duration_ms == 2000 and seg.words[-1].end_ms <= 2000
    assert seg.voice.model_revision == "Qwen3-TTS-12Hz-1.7B-CustomVoice"
    # The delivery note and the timbre reach the skill; the text goes on stdin, not argv.
    assert "--speaker" in seen["cmd"] and "ryan" in seen["cmd"]
    assert "--instruct" in seen["cmd"] and "Calm." in seen["cmd"]


def test_qwen3tts_forced_alignment_snaps_measured_spans_onto_the_script(
    tmp_path: Path, monkeypatch
) -> None:
    import subprocess

    from content_factory.audio import takes as takes_mod
    from content_factory.audio.normalize import tokenize_words
    from content_factory.audio.tts import Qwen3TTS

    _fake_skill(monkeypatch)
    words = tokenize_words(QWEN_LINE)
    # What the aligner "heard": the same words, evenly spaced across the two seconds.
    rows = [[w, i * 0.25, i * 0.25 + 0.2] for i, w in enumerate(words)]
    monkeypatch.setattr(
        takes_mod,
        "SUBPROCESS_RUN",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(rows), ""),
    )
    seg = Qwen3TTS(tmp_path, aligner="faster_whisper").synthesize(_qwen_request()).segment
    assert seg.timing_source == "forced_alignment"
    assert [w.word for w in seg.words] == words  # the script's words, not the transcript's
    assert seg.words[0].start_ms == 0 and seg.words[1].start_ms == 250
    assert all(
        a.end_ms <= b.start_ms or a.end_ms <= b.end_ms
        for a, b in zip(seg.words, seg.words[1:], strict=False)
    )


def test_qwen3tts_refuses_a_beat_that_does_not_say_the_script(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    from content_factory.audio import takes as takes_mod
    from content_factory.audio.tts import Qwen3TTS, TTSError

    _fake_skill(monkeypatch)
    rows = [["completely", 0.0, 0.4], ["different", 0.4, 0.9], ["words", 0.9, 1.4]]
    monkeypatch.setattr(
        takes_mod,
        "SUBPROCESS_RUN",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, json.dumps(rows), ""),
    )
    with pytest.raises(TTSError, match="did not speak the locked script"):
        Qwen3TTS(tmp_path, aligner="faster_whisper").synthesize(_qwen_request())


def test_qwen3tts_fingerprint_makes_a_delivery_note_a_cache_miss(tmp_path: Path) -> None:
    from content_factory.audio.tts import Qwen3TTS

    plain = Qwen3TTS(tmp_path).fingerprint()
    directed = Qwen3TTS(tmp_path, instruct="Urgent, clipped.").fingerprint()
    assert plain != directed
    # The request carries the voice and the text; the fingerprint carries everything else that
    # changes the audio, or `stage_synthesize_narration` would reuse the wrong take.
    assert directed["instruct"] == "Urgent, clipped." and plain["aligner"] == "faster_whisper"


def test_qwen3tts_is_the_configured_narration_voice(env, tmp_path: Path) -> None:
    from content_factory.audio.tts import Qwen3TTS

    env(
        CF__NARRATION__TTS="qwen3tts",
        CF__NARRATION__QWEN_SPEAKER="aiden",
        CF__NARRATION__QWEN_LANGUAGE="english",
        CF__NARRATION__QWEN_INSTRUCT="Calm documentary narrator.",
    )
    tts, voice = st._tts_executor()
    assert isinstance(tts, Qwen3TTS) and tts.skill_dir.name == "qwen3tts"
    assert (voice.provider, voice.voice_id) == ("qwen3tts", "aiden")
    assert voice.model_revision.endswith("CustomVoice")
    assert tts.instruct == "Calm documentary narrator."


def test_qwen3tts_voice_clone_needs_both_the_clip_and_its_transcript(env, tmp_path: Path) -> None:
    from content_factory.audio.tts import Qwen3TTS

    ref = _pcm_wav(tmp_path / "ref.wav")
    env(CF__NARRATION__TTS="qwen3tts", CF__NARRATION__QWEN_REF_AUDIO=str(ref))
    with pytest.raises(RuntimeError, match="qwen_ref_text"):
        st._tts_executor()
    env(CF__NARRATION__QWEN_REF_TEXT="Hello, this is the reference clip.")
    tts, voice = st._tts_executor()
    # A cloned voice runs on the Base weights, which declare no built-in timbres at all.
    assert voice.model_revision.endswith("Base") and voice.voice_id == "ref.wav"
    assert isinstance(tts, Qwen3TTS) and tts.ref_audio == ref


def test_a_canvas_node_overrides_the_configured_voice(env, tmp_path: Path) -> None:
    """The widgets on the Synthesize Narration node have to actually reach the executor, or they
    are decoration. `voice` also accepts the canvas's label for Kokoro."""
    from content_factory.audio.tts import KokoroTTS, Qwen3TTS
    from content_factory.schemas.fixtures import sample_campaign

    def node(**values: str) -> StageContext:
        campaign = sample_campaign()
        return StageContext(
            workspace_id=campaign.workspace_id,
            project_dir=tmp_path / "p",
            artifacts_dir=tmp_path / "a",
            campaign=campaign,
            deliverable_id="dlv_testvideo001",
            quality="smoke",
            dep_outputs={},
            params=values,
        )

    tts, voice = st._tts_executor(node(voice="qwen3tts", speaker="serena", instruct="Wry."))
    assert isinstance(tts, Qwen3TTS) and voice.voice_id == "serena" and tts.instruct == "Wry."

    tts, voice = st._tts_executor(node(voice="kokoro-82m", speaker="bf_emma"))
    assert isinstance(tts, KokoroTTS) and voice.voice_id == "bf_emma"

    # Settings still decide when the node leaves its widgets at the default.
    env(CF__NARRATION__TTS="qwen3tts", CF__NARRATION__QWEN_SPEAKER="aiden")
    _tts, voice = st._tts_executor(node())
    assert voice.voice_id == "aiden"
