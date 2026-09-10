"""The voice chain: artifact detection, the deterministic FFmpeg tail, and the restore_speech stage.

The two neural steps (ClearerVoice, Resemble Enhance) are behind a subprocess seam and are not run
here — the core suite has no GPU and no model weights. What is tested is everything around them:
the measurements that decide whether they run, the de-esser/EQ/compressor tail, the true-peak
limiter now in front of the R128 master, and the promise the whole chain exists to keep — that a
restored beat is exactly as long as the beat that went in, so no word timing moves.

The live models have their own test at the bottom, marked ``gpu``.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest

from content_factory.audio.detect import decode_mono, detect_artifacts
from content_factory.audio.mix import limit_true_peak, master, measure_loudness
from content_factory.audio.restore import (
    RestorationError,
    SkillDirs,
    apply_voice_chain,
    restore_beat,
    voice_chain_filters,
)
from content_factory.runners.local import make_context
from content_factory.schemas.audio import (
    MasterChainSpec,
    NarrationSegment,
    SpeechRestorationSpec,
    VoiceChainSpec,
)
from content_factory.schemas.fixtures import sample_story_plan
from content_factory.workflows.stages import (
    _load_segments,
    stage_restore_speech,
    stage_synthesize_narration,
)

BEAT = "bt_beat00000001"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_wav(path: Path, samples: list[float], rate: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = [max(-1.0, min(1.0, s)) for s in samples]
    frames = struct.pack(f"<{len(clipped)}h", *[round(s * 32767) for s in clipped])
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)
    return path


def _voice_like(path: Path, *, rate: int = 24000, seconds: float = 2.0, gain: float = 0.5) -> Path:
    """A tonal, band-limited stand-in for speech: a 180 Hz fundamental with harmonics up to
    ~3 kHz, gated into words so the detector has silences to measure a noise floor in."""
    n = int(rate * seconds)
    out = []
    for i in range(n):
        t = i / rate
        # 250 ms of "word", 100 ms of gap.
        voiced = (i % int(rate * 0.35)) < int(rate * 0.25)
        value = 0.0
        if voiced:
            for h, amp in ((1, 1.0), (2, 0.5), (3, 0.3), (6, 0.15), (12, 0.06)):
                value += amp * math.sin(2 * math.pi * 180 * h * t)
            value *= gain / 2.0
        out.append(value)
    return _write_wav(path, out, rate)


def _noise(path: Path, *, rate: int = 48000, seconds: float = 2.0, gain: float = 0.3) -> Path:
    # A fixed LCG rather than `random`: the same bytes on every run, so a failure is reproducible.
    state = 12345
    out = []
    for _ in range(int(rate * seconds)):
        state = (1103515245 * state + 12345) % (1 << 31)
        out.append(gain * ((state / (1 << 30)) - 1.0))
    return _write_wav(path, out, rate)


def _sibilant(path: Path, *, rate: int = 48000, seconds: float = 2.0) -> Path:
    """A voice-like tone with a loud 5-9 kHz band on top: what a spitty 's' measures like."""
    state = 999
    out = []
    for i in range(int(rate * seconds)):
        t = i / rate
        state = (1103515245 * state + 12345) % (1 << 31)
        hiss = (state / (1 << 30)) - 1.0
        # Modulating the hiss at 7 kHz puts its energy in the sibilance band.
        out.append(
            0.25 * math.sin(2 * math.pi * 180 * t) + 0.35 * hiss * math.sin(2 * math.pi * 7000 * t)
        )
    return _write_wav(path, out, rate)


# ---- detection --------------------------------------------------------------------------------


def test_detects_a_band_limited_take(tmp_path: Path) -> None:
    report = detect_artifacts(_voice_like(tmp_path / "v.wav"), asset_id=BEAT)
    assert report.sample_rate_hz == 24000
    assert report.duration_ms == 2000
    # Nothing above 12 kHz can exist at 24 kHz, and the harmonics stop well below that.
    assert report.band_limit_hz < report.thresholds.band_limit_hz_min
    assert report.needs_band_extension
    assert report.has("band_limited")
    assert report.passed  # band-limited is advisory, not a failure


def test_detects_noise_and_asks_for_cleanup(tmp_path: Path) -> None:
    report = detect_artifacts(_noise(tmp_path / "n.wav"), asset_id=BEAT)
    assert report.spectral_flatness > report.thresholds.spectral_flatness_max
    assert report.has("spectral_flatness")
    assert report.has("noise_floor")  # noise has no gaps: the floor is the signal
    assert report.needs_cleanup


def test_detects_sibilance(tmp_path: Path) -> None:
    report = detect_artifacts(_sibilant(tmp_path / "s.wav"), asset_id=BEAT)
    assert report.sibilance_ratio > report.thresholds.sibilance_ratio_max
    assert report.has("sibilance")


def test_detects_clipping_and_a_quiet_take(tmp_path: Path) -> None:
    hot = detect_artifacts(_voice_like(tmp_path / "hot.wav", gain=4.0), asset_id=BEAT)
    assert hot.clipped_sample_ratio > hot.thresholds.clipped_sample_ratio_max
    assert hot.has("clipping")
    assert hot.needs_cleanup

    quiet = detect_artifacts(_voice_like(tmp_path / "quiet.wav", gain=0.01), asset_id=BEAT)
    assert quiet.peak_dbfs < quiet.thresholds.peak_dbfs_min
    assert quiet.has("level")


def test_digital_silence_is_a_blocker(tmp_path: Path) -> None:
    report = detect_artifacts(_write_wav(tmp_path / "z.wav", [0.0] * 24000, 24000), asset_id=BEAT)
    assert report.has("silence")
    assert not report.passed


# ---- the deterministic tail -------------------------------------------------------------------


def test_chain_order_is_the_documented_order() -> None:
    chain = voice_chain_filters(VoiceChainSpec(), sample_rate_hz=48000, length_samples=48000)
    order = [
        chain.index("aresample"),
        chain.index("deesser"),
        chain.index("highpass"),
        chain.index("equalizer"),
        chain.index("acompressor"),
        chain.index("atrim"),
    ]
    assert order == sorted(order), chain
    # The tail is what guarantees the length, so both halves of that must be present.
    assert "apad=whole_len=48000" in chain
    assert "atrim=end_sample=48000" in chain


def test_voice_chain_keeps_the_length_and_reaches_the_delivery_rate(tmp_path: Path) -> None:
    src = _voice_like(tmp_path / "v.wav", rate=24000, seconds=2.0)
    before = detect_artifacts(src, asset_id=BEAT)
    out = tmp_path / "out.wav"
    apply_voice_chain(
        src,
        out,
        VoiceChainSpec(),
        sample_rate_hz=48000,
        length_samples=round(before.duration_ms * 48000 / 1000),
    )
    after = detect_artifacts(out, asset_id=BEAT)
    assert after.sample_rate_hz == 48000
    assert after.duration_ms == before.duration_ms
    samples, _rate = decode_mono(out)
    assert samples.size == 96000  # exactly 2.000 s at 48 kHz, to the sample


def test_voice_chain_is_deterministic(tmp_path: Path) -> None:
    src = _voice_like(tmp_path / "v.wav")
    outs = []
    for i in range(2):
        out = tmp_path / f"o{i}.wav"
        apply_voice_chain(src, out, VoiceChainSpec(), sample_rate_hz=48000, length_samples=96000)
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]


def test_de_esser_reduces_sibilance(tmp_path: Path) -> None:
    src = _sibilant(tmp_path / "s.wav")
    before = detect_artifacts(src, asset_id=BEAT)
    length = round(before.duration_ms * 48000 / 1000)
    on, off = tmp_path / "on.wav", tmp_path / "off.wav"
    apply_voice_chain(
        src,
        on,
        VoiceChainSpec(de_ess=True, de_ess_intensity=1.0),
        sample_rate_hz=48000,
        length_samples=length,
    )
    apply_voice_chain(
        src, off, VoiceChainSpec(de_ess=False), sample_rate_hz=48000, length_samples=length
    )
    assert (
        detect_artifacts(on, asset_id=BEAT).sibilance_ratio
        < detect_artifacts(off, asset_id=BEAT).sibilance_ratio
    )


# ---- the master tail ---------------------------------------------------------------------------


def test_limiter_caps_the_true_peak(tmp_path: Path) -> None:
    hot = _voice_like(tmp_path / "hot.wav", rate=48000, gain=1.9)  # peaks at full scale
    assert measure_loudness(hot).true_peak_dbtp > -1.0
    spec = MasterChainSpec()
    out = tmp_path / "limited.wav"
    limit_true_peak(hot, out, spec)
    assert measure_loudness(out).true_peak_dbtp <= spec.limiter_ceiling_dbtp + 0.1


def test_master_still_hits_the_house_target_with_the_limiter(tmp_path: Path) -> None:
    hot = _voice_like(tmp_path / "hot.wav", rate=48000, gain=1.9)
    report = master(hot, tmp_path / "m.wav", MasterChainSpec())
    assert report.passed, (report.integrated_lufs, report.true_peak_dbtp)
    assert report.true_peak_dbtp <= -1.0 + 0.1


# ---- the chain with both model steps off ------------------------------------------------------


def _skills() -> SkillDirs:
    return SkillDirs(
        clearervoice=REPO_ROOT / "skills" / "audio" / "clearervoice",
        resemble_enhance=REPO_ROOT / "skills" / "audio" / "resemble_enhance",
    )


def test_restore_beat_records_what_ran_and_what_did_not(tmp_path: Path) -> None:
    src = _voice_like(tmp_path / "v.wav")
    out = tmp_path / "restored.wav"
    report = restore_beat(
        src,
        out,
        beat_id=BEAT,
        spec=SpeechRestorationSpec(),  # every model step off
        skills=_skills(),
        workdir=tmp_path / "work",
    )
    # `_voice_like` is a clean synthetic tone: nothing for the de-esser to do, so it does not run
    # and says why. It is gated on the measurement like every model step in the chain.
    assert report.steps == ("detect", "voice_chain:eq+compress")
    assert not report.before.needs_de_ess
    assert [s.split(":")[0] for s in report.skipped] == [
        "cleanup",
        "band_extension",
        "enhancer",
        "de_ess",
    ]
    assert report.input_duration_ms == report.output_duration_ms
    assert report.output_sample_rate_hz == 48000
    assert report.before.needs_band_extension  # 24 kHz in
    # The tail resamples but cannot invent a top end: only a model can clear this finding.
    assert report.after.needs_band_extension


def _essy(path: Path, *, rate: int = 48000, seconds: float = 2.0) -> Path:
    """`_voice_like` with a 7 kHz band on top of every word: an ess loud enough to trip the gate."""
    n = int(rate * seconds)
    out = []
    for i in range(n):
        t = i / rate
        voiced = (i % int(rate * 0.35)) < int(rate * 0.25)
        value = 0.0
        if voiced:
            for h, amp in ((1, 1.0), (2, 0.5), (3, 0.3), (6, 0.15), (12, 0.06)):
                value += amp * math.sin(2 * math.pi * 180 * h * t)
            # The sibilant: three partials across the 5-9 kHz band the detector measures.
            for f, amp in ((5800.0, 0.9), (7000.0, 1.1), (8400.0, 0.8)):
                value += amp * math.sin(2 * math.pi * f * t)
            value *= 0.5 / 2.0
        out.append(value)
    return _write_wav(path, out, rate)


def test_restore_beat_de_esses_the_beat_that_needs_it(tmp_path: Path) -> None:
    """The gate fires, and the filter behind it actually reduces the band it is named for.

    Both halves matter. The intensity shipped at 0.25, which measured -0.1 % on a real narration
    beat — the step was recorded as having run and had done nothing (2026-09-10).
    """
    src = _essy(tmp_path / "s.wav")
    out = tmp_path / "restored.wav"
    report = restore_beat(
        src,
        out,
        beat_id=BEAT,
        spec=SpeechRestorationSpec(),
        skills=_skills(),
        workdir=tmp_path / "work",
    )
    assert report.before.needs_de_ess, "the fixture has to be essy or the test proves nothing"
    assert "voice_chain:de_ess+eq+compress" in report.steps
    assert report.after.sibilance_ratio < report.before.sibilance_ratio * 0.9


def test_restore_beat_refuses_a_missing_take(tmp_path: Path) -> None:
    with pytest.raises(RestorationError, match="input not found"):
        restore_beat(
            tmp_path / "nope.wav",
            tmp_path / "out.wav",
            beat_id=BEAT,
            spec=SpeechRestorationSpec(),
            skills=_skills(),
            workdir=tmp_path / "work",
        )


def test_restore_beat_refuses_an_uninstalled_skill(tmp_path: Path) -> None:
    """Turning a model on without building its environment must say so, not silently skip it."""
    with pytest.raises(RestorationError, match="skill not installed"):
        restore_beat(
            _voice_like(tmp_path / "v.wav"),
            tmp_path / "out.wav",
            beat_id=BEAT,
            spec=SpeechRestorationSpec(enhancer="resemble_enhance"),
            skills=SkillDirs(
                clearervoice=tmp_path / "no-such-skill",
                resemble_enhance=tmp_path / "no-such-skill",
            ),
            workdir=tmp_path / "work",
        )


# ---- the stage ---------------------------------------------------------------------------------


def test_stage_restores_every_beat_and_keeps_the_timings(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    plan = sample_story_plan()
    stage_synthesize_narration(ctx)
    audio = ctx.ddir() / "audio"
    before = {
        b.beat_id: NarrationSegment.model_validate_json(
            (audio / f"{b.beat_id}.segment.json").read_text()
        )
        for b in plan.beats
    }

    out = stage_restore_speech(ctx)
    assert out.facts["beats"] == len(plan.beats)
    assert out.facts["restored"] == len(plan.beats)
    reports = json.loads((audio / "restoration.json").read_text())
    assert len(reports) == len(plan.beats)

    for beat in plan.beats:
        restored = audio / f"{beat.beat_id}.restored.wav"
        assert restored.exists()
        assert (audio / f"{beat.beat_id}.wav").exists()  # the synthesis output is never overwritten
        after = NarrationSegment.model_validate_json(
            (audio / f"{beat.beat_id}.segment.json").read_text()
        )
        old = before[beat.beat_id]
        assert after.duration_ms == old.duration_ms
        assert after.words == old.words
        assert after.sample_rate_hz == 48000
        assert after.audio_sha256 != old.audio_sha256  # it describes the restored bytes now

    # Everything downstream reads the restored file.
    _plan, _segs, files = _load_segments(ctx)
    assert all(p.name.endswith(".restored.wav") for p in files.values())


def test_stage_is_cached_per_beat(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    stage_synthesize_narration(ctx)
    first = stage_restore_speech(ctx)
    second = stage_restore_speech(ctx)
    assert second.facts["restored"] == 0
    assert second.facts["beats"] == first.facts["beats"]


def test_stage_can_be_switched_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF__SPEECH_RESTORATION__ENABLED", "0")
    from content_factory.config.settings import get_settings

    get_settings.cache_clear()
    try:
        ctx = make_context(project_dir=tmp_path)
        stage_synthesize_narration(ctx)
        out = stage_restore_speech(ctx)
        assert out.facts["beats"] == 0
        assert "enabled=0" in str(out.facts["skipped"])
        # Nothing was restored earlier in this run, so there is nothing stale to warn about.
        assert out.facts["already_restored"] == []
        assert not (ctx.ddir() / "audio" / "restoration.json").exists()
    finally:
        get_settings.cache_clear()


# ---- the live models ---------------------------------------------------------------------------


@pytest.mark.gpu
def test_live_chain_rebuilds_the_missing_top_end(tmp_path: Path) -> None:
    """Needs the two skill environments and the weights under models/speech_restoration/.

    Run with: uv run pytest tests/unit/test_speech_restoration.py -m gpu
    (``--device cpu`` is the default and works, just slowly: Resemble Enhance is ~19x realtime.)
    """
    src = _voice_like(tmp_path / "v.wav", rate=24000, seconds=2.0)
    out = tmp_path / "restored.wav"
    report = restore_beat(
        src,
        out,
        beat_id=BEAT,
        spec=SpeechRestorationSpec(
            cleanup="clearervoice",
            band_extension="clearervoice_sr",
            enhancer="resemble_enhance",
            gate="always",
        ),
        skills=_skills(),
        workdir=tmp_path / "work",
    )
    assert report.steps == (
        "detect",
        "cleanup:clearervoice",
        "band_extension:clearervoice_sr",
        "enhancer:resemble_enhance:enhance",
        "voice_chain:de_ess+eq+compress",
    )
    assert report.input_duration_ms == report.output_duration_ms
    assert report.after.band_limit_hz > report.before.band_limit_hz
