"""Artifact / noise detection: measure one speech take before deciding what to do to it.

The first step of every generated-audio chain in this repo. It answers three questions with
numbers rather than taste:

* is the asset usable at all (level, clipping, DC, dead silence)?
* is it dirty (noise floor, spectral flatness) — i.e. worth a cleanup pass?
* is it band-limited (a 22/24 kHz TTS has nothing above 11-12 kHz) — i.e. worth band extension?

The measurements are material-agnostic; ``profile`` decides which crossings count as defects, so
the same code serves a narration beat and a generated rain bed without pretending a flat spectrum
means the same thing in both.

Everything is derived from one decode of the file: FFmpeg to raw mono float samples, then NumPy.
No model, no network, deterministic, and cheap enough to run on every beat.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from content_factory.schemas.audio import (
    AudioArtifactFinding,
    AudioArtifactReport,
    AudioArtifactThresholds,
    AudioProfile,
)

# Analysis constants. 1024-sample frames at 48 kHz are ~21 ms — short enough that a frame is
# either speech or silence, long enough for a usable spectrum.
FRAME = 1024
SPEECH_BAND_HZ = (300.0, 5000.0)
SIBILANCE_BAND_HZ = (5000.0, 9000.0)
# The quietest tenth of frames is the noise floor: narration always has gaps between words.
NOISE_PERCENTILE = 10.0
# A frame this far below the loudest frame is silence, not speech, and must not drag the
# spectral measurements towards the noise it contains.
SPEECH_FLOOR_DB = 35.0
# "Real energy" for the band-limit search: the highest bin still within this of the peak bin.
BAND_LIMIT_FLOOR_DB = 50.0
FULL_SCALE_16BIT = 1.0 - 1.0 / 32768.0


class DetectError(Exception):
    pass


def _db(x: float) -> float:
    return float(20.0 * np.log10(x)) if x > 0 else -float("inf")


def decode_mono(path: Path) -> tuple[np.ndarray, int]:
    """One decode of any FFmpeg-readable file to mono float32 at its own sample rate."""
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise DetectError(f"ffprobe failed on {path}: {probe.stderr[-500:]}")
    streams = json.loads(probe.stdout).get("streams") or []
    if not streams:
        raise DetectError(f"{path} has no audio stream")
    sample_rate = int(streams[0]["sample_rate"])
    dec = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "f32le", "-ac", "1", "-"],
        capture_output=True,
        check=False,
    )
    if dec.returncode != 0:
        raise DetectError(f"ffmpeg decode failed on {path}: {dec.stderr[-500:].decode()}")
    samples = np.frombuffer(dec.stdout, dtype="<f4").astype(np.float64)
    if samples.size == 0:
        raise DetectError(f"{path} decoded to zero samples")
    return samples, sample_rate


def _frame_rms(samples: np.ndarray) -> np.ndarray:
    n = samples.size // FRAME
    if n == 0:
        return np.array([float(np.sqrt(np.mean(samples**2)))])
    frames = samples[: n * FRAME].reshape(n, FRAME)
    return np.sqrt(np.mean(frames**2, axis=1))


def _speech_spectrum(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean magnitude spectrum of the speech frames only (silence excluded)."""
    n = samples.size // FRAME
    if n == 0:
        window = np.hanning(samples.size)
        mag = np.abs(np.fft.rfft(samples * window))
        return mag, np.fft.rfftfreq(samples.size, 1.0 / sample_rate)
    frames = samples[: n * FRAME].reshape(n, FRAME)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    loudest = rms.max()
    keep = rms > loudest * 10.0 ** (-SPEECH_FLOOR_DB / 20.0) if loudest > 0 else np.ones(n, bool)
    if not keep.any():
        keep = np.ones(n, bool)
    window = np.hanning(FRAME)
    mag = np.abs(np.fft.rfft(frames[keep] * window, axis=1)).mean(axis=0)
    return mag, np.fft.rfftfreq(FRAME, 1.0 / sample_rate)


def _band_power(mag: np.ndarray, freqs: np.ndarray, lo: float, hi: float) -> float:
    sel = (freqs >= lo) & (freqs < hi)
    return float(np.sum(mag[sel] ** 2))


def detect_artifacts(
    path: Path,
    *,
    asset_id: str,
    profile: AudioProfile = AudioProfile.speech,
    thresholds: AudioArtifactThresholds | None = None,
) -> AudioArtifactReport:
    """Measure ``path`` and record every threshold it crosses.

    The measurements are the same for every kind of material; the ``profile`` decides which
    crossings are defects. See :meth:`AudioArtifactThresholds.for_profile`.
    """
    th = thresholds or AudioArtifactThresholds.for_profile(profile)
    samples, sample_rate = decode_mono(path)
    duration_ms = max(1, round(samples.size * 1000 / sample_rate))

    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples**2)))
    dc_offset = float(abs(np.mean(samples)))
    clipped = int(np.count_nonzero(np.abs(samples) >= FULL_SCALE_16BIT))
    clipped_ratio = clipped / samples.size

    frame_rms = _frame_rms(samples)
    noise_floor = float(np.percentile(frame_rms, NOISE_PERCENTILE))

    mag, freqs = _speech_spectrum(samples, sample_rate)
    speech_power = _band_power(mag, freqs, *SPEECH_BAND_HZ)
    sibilance_power = _band_power(mag, freqs, *SIBILANCE_BAND_HZ)
    sibilance_ratio = sibilance_power / speech_power if speech_power > 0 else 0.0

    # Spectral flatness (geometric over arithmetic mean of the power spectrum) across the speech
    # band: tonal voiced speech sits low, hiss and buzz sit high.
    band = (freqs >= SPEECH_BAND_HZ[0]) & (freqs < SPEECH_BAND_HZ[1])
    power = mag[band] ** 2 + 1e-20
    # Clamped: a perfectly flat spectrum (digital silence, where every bin is the epsilon)
    # computes to 1 plus float error, which the contract rejects.
    raw_flatness = float(np.exp(np.mean(np.log(power))) / np.mean(power)) if power.size else 0.0
    flatness = min(1.0, max(0.0, raw_flatness))

    # Band limit: the highest bin still within BAND_LIMIT_FLOOR_DB of the strongest bin.
    if mag.max() > 0:
        floor = mag.max() * 10.0 ** (-BAND_LIMIT_FLOOR_DB / 20.0)
        above = np.nonzero(mag >= floor)[0]
        band_limit_hz = float(freqs[above[-1]]) if above.size else 0.0
    else:
        band_limit_hz = 0.0

    findings: list[AudioArtifactFinding] = []
    if peak <= 0:
        findings.append(
            AudioArtifactFinding(
                check="silence",
                severity="blocker",
                message="the take is digital silence",
                value=0.0,
            )
        )
    else:
        if _db(peak) < th.peak_dbfs_min:
            findings.append(
                AudioArtifactFinding(
                    check="level",
                    severity="major",
                    message=f"peak {_db(peak):.1f} dBFS is below {th.peak_dbfs_min} dBFS",
                    value=_db(peak),
                )
            )
        if clipped_ratio > th.clipped_sample_ratio_max:
            findings.append(
                AudioArtifactFinding(
                    check="clipping",
                    severity="major",
                    message=f"{clipped} samples ({clipped_ratio:.4%}) are at full scale",
                    value=clipped_ratio,
                )
            )
        if dc_offset > th.dc_offset_max:
            findings.append(
                AudioArtifactFinding(
                    check="dc_offset",
                    severity="minor",
                    message=f"DC offset {dc_offset:.4f} exceeds {th.dc_offset_max}",
                    value=dc_offset,
                )
            )
        if th.noise_floor_dbfs_max < 0 and _db(noise_floor) > th.noise_floor_dbfs_max:
            findings.append(
                AudioArtifactFinding(
                    check="noise_floor",
                    severity="minor",
                    message=(
                        f"noise floor {_db(noise_floor):.1f} dBFS is above"
                        f" {th.noise_floor_dbfs_max} dBFS"
                    ),
                    value=_db(noise_floor),
                )
            )
        if sibilance_ratio > th.sibilance_ratio_max:
            speech = profile is AudioProfile.speech
            findings.append(
                AudioArtifactFinding(
                    # Same number, different defect: esses that spit in a voice, a harsh top end
                    # in a designed sound.
                    check="sibilance" if speech else "harsh_band",
                    severity="minor",
                    message=(
                        f"5-9 kHz energy is {sibilance_ratio:.3f} of the"
                        f" {'speech' if speech else 'mid'} band"
                        f" (limit {th.sibilance_ratio_max})"
                    ),
                    value=sibilance_ratio,
                )
            )
        if th.spectral_flatness_max < 1.0 and flatness > th.spectral_flatness_max:
            findings.append(
                AudioArtifactFinding(
                    check="spectral_flatness",
                    severity="minor",
                    message=(
                        f"spectral flatness {flatness:.3f} exceeds {th.spectral_flatness_max}:"
                        " hiss or buzz rather than voice"
                    ),
                    value=flatness,
                )
            )
        if th.band_limit_hz_min > 1.0 and band_limit_hz < th.band_limit_hz_min:
            findings.append(
                AudioArtifactFinding(
                    check="band_limited",
                    severity="advisory",
                    message=(
                        f"energy stops at {band_limit_hz / 1000:.1f} kHz"
                        f" (want {th.band_limit_hz_min / 1000:.1f} kHz)"
                    ),
                    value=band_limit_hz,
                )
            )

    return AudioArtifactReport(
        profile=profile,
        asset_id=asset_id,
        sample_rate_hz=sample_rate,
        duration_ms=duration_ms,
        peak_dbfs=_db(peak) if peak > 0 else -120.0,
        rms_dbfs=_db(rms) if rms > 0 else -120.0,
        noise_floor_dbfs=_db(noise_floor) if noise_floor > 0 else -120.0,
        clipped_sample_ratio=clipped_ratio,
        dc_offset=dc_offset,
        sibilance_ratio=sibilance_ratio,
        band_limit_hz=band_limit_hz,
        spectral_flatness=flatness,
        thresholds=th,
        findings=tuple(findings),
    )
