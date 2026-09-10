"""Conditioning for generated non-speech audio, and the reason it is not the speech chain.

The design claim this file defends is that the *architecture* is shared and the *models* are not:
detection, true-peak safety and the length lock are the same code as the voice chain, while
ClearerVoice and Resemble Enhance are absent because they were measured to destroy non-speech
material (numbers in `content_factory.audio.condition`'s docstring). So the tests here check that
the profile actually changes what counts as a defect, that the speech path is refused outright, and
that a level the model could not reach is reported rather than clipped into place.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path

import pytest

from content_factory.audio.condition import (
    ConditionError,
    condition_sound,
    measure_loudness_detail,
)
from content_factory.audio.detect import decode_mono, detect_artifacts
from content_factory.runners.local import make_context
from content_factory.schemas.audio import (
    AudioArtifactThresholds,
    AudioProfile,
    SoundConditionSpec,
)
from content_factory.workflows.stages import stage_sound_design

RATE = 48000


def _write(path: Path, samples: list[float], rate: int = RATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = [max(-1.0, min(1.0, s)) for s in samples]
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{len(clipped)}h", *[round(s * 32767) for s in clipped]))
    return path


def _noise_bed(path: Path, *, seconds: float = 2.0, gain: float = 0.05) -> Path:
    """Broadband noise — a rain or room-tone bed. Fixed LCG so a failure reproduces."""
    state = 4242
    out = []
    for _ in range(int(RATE * seconds)):
        state = (1103515245 * state + 12345) % (1 << 31)
        out.append(gain * ((state / (1 << 30)) - 1.0))
    return _write(path, out)


def _one_shot(path: Path, *, seconds: float = 1.0, peak: float = 0.9) -> Path:
    """A transient with a long tail: a whoosh or an impact. Mostly silence, one loud moment —
    which is exactly why its integrated loudness says nothing useful about it."""
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        env = math.exp(-6.0 * i / n)
        out.append(peak * env * math.sin(2 * math.pi * 140 * i / RATE))
    return _write(path, out)


# ---- the design decision, in code ------------------------------------------------------------


def test_speech_is_refused_here(tmp_path: Path) -> None:
    """Speech has its own chain with its own models. Sending it here would silently skip them."""
    with pytest.raises(ConditionError, match="own chain"):
        condition_sound(
            _noise_bed(tmp_path / "n.wav"),
            tmp_path / "out.wav",
            asset_id="sfx",
            spec=SoundConditionSpec(profile=AudioProfile.speech),
        )


def test_the_spec_has_no_way_to_ask_for_a_speech_model() -> None:
    """A regression guard on the decision itself: measured on this host, MossFormer2_SE_48K left
    0.5-1.0 % of a whoosh/rain/impact's energy and Resemble Enhance returned a whoosh with a 0.002
    waveform correlation to its input. If a field for either appears here, that was undone."""
    fields = set(SoundConditionSpec.model_fields)
    assert not {f for f in fields if "enhanc" in f or "clearervoice" in f or "resemble" in f}
    assert "denoise" in fields and SoundConditionSpec().denoise is False


def test_the_profile_decides_what_counts_as_a_defect(tmp_path: Path) -> None:
    """The same broadband bed: hiss in a narration take, the content in a rain loop."""
    bed = _noise_bed(tmp_path / "bed.wav")
    as_speech = detect_artifacts(bed, asset_id="beat_000000001", profile=AudioProfile.speech)
    as_sfx = detect_artifacts(bed, asset_id="rain_bed", profile=AudioProfile.sound_effect)

    assert as_speech.has("spectral_flatness") and as_speech.has("noise_floor")
    assert as_speech.needs_cleanup  # a voice chain would (correctly) want to denoise this

    assert not as_sfx.has("spectral_flatness")
    assert not as_sfx.has("noise_floor")
    assert not as_sfx.has("band_limited")
    assert not as_sfx.needs_cleanup
    # The measurements themselves are identical; only the verdict differs.
    assert as_sfx.spectral_flatness == as_speech.spectral_flatness
    assert as_sfx.profile is AudioProfile.sound_effect


def test_non_speech_thresholds_still_catch_real_damage(tmp_path: Path) -> None:
    """Disabling the voice-shaped checks must not disable the integrity ones."""
    th = AudioArtifactThresholds.for_profile(AudioProfile.sound_effect)
    assert th.clipped_sample_ratio_max > 0  # still checked
    hot = detect_artifacts(
        _one_shot(tmp_path / "hot.wav", peak=1.8), asset_id="hot", profile=AudioProfile.sound_effect
    )
    assert hot.has("clipping") and hot.needs_repair


# ---- levelling -------------------------------------------------------------------------------


def test_a_bed_is_normalised_to_its_integrated_target(tmp_path: Path) -> None:
    src = _noise_bed(tmp_path / "bed.wav", gain=0.02)  # deliberately quiet
    out = tmp_path / "bed.cond.wav"
    report = condition_sound(
        src,
        out,
        asset_id="rain_bed",
        spec=SoundConditionSpec(loudness_metric="integrated", target_lufs=-23.0),
        workdir=tmp_path / "work",
    )
    measured = measure_loudness_detail(out)
    assert abs(measured["integrated_lufs"] - (-23.0)) <= 1.0, measured
    assert measured["true_peak_dbtp"] <= -1.0 + 0.1
    assert report.gain_applied_db > 0 and not report.gain_limited
    assert "loudness:integrated->-23.0LUFS" in report.steps


def test_a_one_shot_that_cannot_reach_its_target_says_so_instead_of_clipping(
    tmp_path: Path,
) -> None:
    """A transient's crest factor means the ceiling is reached long before the loudness target.
    The honest outcome is a limited gain and a flag, not a squashed sound."""
    src = _one_shot(tmp_path / "hit.wav", peak=0.9)
    out = tmp_path / "hit.cond.wav"
    report = condition_sound(
        src,
        out,
        asset_id="impact",
        spec=SoundConditionSpec(loudness_metric="max_momentary", target_lufs=-6.0),
        workdir=tmp_path / "work",
    )
    assert report.gain_limited
    assert "loudness:limited_by_true_peak_or_max_gain" in report.steps
    assert measure_loudness_detail(out)["true_peak_dbtp"] <= -1.0 + 0.1


def test_loudness_can_be_left_alone(tmp_path: Path) -> None:
    report = condition_sound(
        _noise_bed(tmp_path / "n.wav"),
        tmp_path / "o.wav",
        asset_id="bed",
        spec=SoundConditionSpec(loudness_metric="off"),
        workdir=tmp_path / "work",
    )
    assert report.gain_applied_db == 0.0 and "loudness: off" in report.skipped


# ---- the promises shared with the voice chain -------------------------------------------------


def test_length_is_preserved_to_the_sample(tmp_path: Path) -> None:
    """A bed is scored against the picture frame by frame, so this matters as much here as word
    timings do in speech."""
    src = _noise_bed(tmp_path / "bed.wav", seconds=1.5)
    out = tmp_path / "bed.cond.wav"
    report = condition_sound(src, out, asset_id="bed", workdir=tmp_path / "work")
    assert report.input_duration_ms == report.output_duration_ms == 1500
    samples, rate = decode_mono(out)
    assert rate == 48000 and samples.size == 72000


def test_conditioning_is_deterministic(tmp_path: Path) -> None:
    src = _noise_bed(tmp_path / "bed.wav")
    digests = []
    for i in range(2):
        out = tmp_path / f"o{i}.wav"
        condition_sound(src, out, asset_id="bed", workdir=tmp_path / f"w{i}")
        digests.append(out.read_bytes())
    assert digests[0] == digests[1]


def test_declip_runs_only_when_clipping_was_measured(tmp_path: Path) -> None:
    clean = condition_sound(
        _noise_bed(tmp_path / "clean.wav"),
        tmp_path / "clean.out.wav",
        asset_id="clean",
        workdir=tmp_path / "w1",
    )
    assert any("no clipped samples" in s for s in clean.skipped)
    assert not any("adeclip" in s for s in clean.steps)

    hot = condition_sound(
        _one_shot(tmp_path / "hot.wav", peak=1.8),
        tmp_path / "hot.out.wav",
        asset_id="hot",
        workdir=tmp_path / "w2",
    )
    assert any("adeclip" in s for s in hot.steps)


def test_a_silent_asset_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConditionError, match="digital silence"):
        condition_sound(
            _write(tmp_path / "z.wav", [0.0] * RATE),
            tmp_path / "o.wav",
            asset_id="dead",
            workdir=tmp_path / "w",
        )


# ---- the stage --------------------------------------------------------------------------------


def test_sound_design_conditions_the_generated_bed(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    # The post chain's silent cut, which is what sound_design scores. `final.mp4` is the
    # delivered film and is written after this stage, not before it.
    picture = ctx.ddir() / "exports" / "postchain.mp4"
    picture.parent.mkdir(parents=True, exist_ok=True)
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x96:rate=24",
            "-t",
            "3",
            "-pix_fmt",
            "yuv420p",
            str(picture),
        ],
        check=True,
    )
    out = stage_sound_design(ctx)
    audio = ctx.ddir() / "audio"
    # The generator's own output is kept: a rerun starts from what the model produced, never from
    # something already conditioned.
    assert (audio / "sfx.raw.wav").exists()
    assert (audio / "sfx.wav").exists()
    report = json.loads((audio / "sfx-condition.json").read_text())
    assert report["profile"] == "sound_effect"
    assert report["input_duration_ms"] == report["output_duration_ms"]
    assert report["output_sample_rate_hz"] == 48000
    assert out.facts["conditioned"][0] == "detect"
    # The bed's level is now a known number rather than whatever the model rendered, so the mix
    # trims from a target instead of guessing.
    assert out.facts["gain_db"] == 0.0
    assert out.facts["bed_true_peak_dbtp"] <= -1.0 + 0.1
