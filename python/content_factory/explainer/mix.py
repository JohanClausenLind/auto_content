"""Stems and the master for one episode: narration, a ducked music bed and effects, summed once."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from content_factory.audio.mix import DEFAULT_MASTER_CHAIN, ffmpeg, master, measure_loudness
from content_factory.explainer.narration import STEM_RATE_HZ, read_wav_48k, write_wav
from content_factory.schemas.audio import LoudnessReport

MIX_VERSION = "2"
MUSIC_BED_DB = -18.0
DUCK_DEPTH_DB = -12.0
DUCK_ATTACK_S = 0.08
DUCK_RELEASE_S = 0.45
DUCK_FLOOR_DB = -42.0
BED_FADE_S = 1.0
PREMIX_PEAK = 0.891


@dataclass(frozen=True)
class MixResult:
    narration_stem: Path
    music_stem: Path
    sfx_stem: Path
    master: Path
    loudness: LoudnessReport


def duck_curve(
    voice: np.ndarray,
    rate: int,
    *,
    depth_db: float = DUCK_DEPTH_DB,
    block_s: float = 0.02,
    attack_s: float = DUCK_ATTACK_S,
    release_s: float = DUCK_RELEASE_S,
    floor_db: float = DUCK_FLOOR_DB,
) -> np.ndarray:
    """Bed gain per sample: 1.0 in the gaps, exactly depth_db under speech, sliding between."""
    # A detector with a fixed depth, not sidechaincompress: the compressor swung 4.5-24.1 dB with
    # the syllable and left a soft line 2.2 dB clear of the bed (journal 2026-09-13).
    block = max(1, int(block_s * rate))
    if len(voice) < block:
        return np.ones(len(voice), dtype=np.float32)
    blocks = len(voice) // block
    rms = np.sqrt((voice[: blocks * block].reshape(blocks, block) ** 2).mean(axis=1))
    speaking = 20 * np.log10(np.maximum(rms, 1e-9)) > floor_db
    hold = max(1, int(release_s / block_s))
    held = np.copy(speaking)
    countdown = 0
    for i, on in enumerate(speaking):
        countdown = hold if on else max(0, countdown - 1)
        held[i] = on or countdown > 0
    target = np.where(held, 10 ** (depth_db / 20.0), 1.0)
    smoothed = np.empty_like(target)
    level = 1.0
    a_att = 1.0 - np.exp(-block_s / max(attack_s, 1e-3))
    a_rel = 1.0 - np.exp(-block_s / max(release_s, 1e-3))
    for i, want in enumerate(target):
        level += (want - level) * (a_att if want < level else a_rel)
        smoothed[i] = level
    points = np.linspace(0, len(voice) - 1, len(smoothed))
    return np.interp(np.arange(len(voice)), points, smoothed).astype(np.float32)


# Speech peaks sit 15-20 dB over its loudness; a linear master reaches -14 LUFS under -1.3 dBTP
# only if they arrive within 12.7 dB, so the voice sits at -16 LUFS with true peaks at -5.
NARRATION_LUFS = -16.0
NARRATION_PEAK_DBTP = -5.0
LEVEL_TOLERANCE_LU = 0.5
LEVEL_PASSES = 6


def level_narration(stem: Path, out: Path) -> None:
    """Gain then limit the voice, re-gaining until the limiter's loss is made up."""
    measured = measure_loudness(stem).integrated_lufs
    gain_db = NARRATION_LUFS - measured if math.isfinite(measured) else 0.0
    for _ in range(LEVEL_PASSES):
        _gain_and_limit(stem, out, gain_db)
        measured = measure_loudness(out).integrated_lufs
        if not math.isfinite(measured) or abs(measured - NARRATION_LUFS) <= LEVEL_TOLERANCE_LU:
            return
        gain_db += NARRATION_LUFS - measured


def _gain_and_limit(stem: Path, out: Path, gain_db: float) -> None:
    # Limiting at 4x the stem rate makes the sample-peak ceiling a true-peak one.
    limit = 10 ** (NARRATION_PEAK_DBTP / 20)
    ffmpeg(
        [
            "-i",
            str(stem),
            "-af",
            f"aresample={4 * STEM_RATE_HZ},volume={gain_db:.2f}dB,alimiter=limit={limit:.6f}"
            f":attack=2:release=20:level=disabled:latency=true,aresample={STEM_RATE_HZ}",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out),
        ]
    )


def mix_episode(
    stem: Path, music: Path | None, sfx: Sequence[tuple[int, Path]], out_dir: Path
) -> MixResult:
    """Three stems written separately, summed once, mastered through the programme chain."""
    out_dir.mkdir(parents=True, exist_ok=True)
    leveled = out_dir / "narration-leveled.wav"
    level_narration(stem, leveled)
    voice = read_wav_48k(leveled)
    leveled.unlink(missing_ok=True)
    n = len(voice)
    bed = np.zeros(n, dtype=np.float32)
    if music is not None:
        loop = read_wav_48k(music)
        if len(loop):
            bed = np.resize(loop, n) * 10 ** (MUSIC_BED_DB / 20.0) * duck_curve(voice, STEM_RATE_HZ)
            fade = min(n, int(BED_FADE_S * STEM_RATE_HZ))
            bed[n - fade :] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    effects = np.zeros(n, dtype=np.float32)
    for at_ms, path in sfx:
        clip = read_wav_48k(path)
        start = at_ms * STEM_RATE_HZ // 1000
        end = min(n, start + len(clip))
        if start < end:
            effects[start:end] += clip[: end - start]
    narration_stem, music_stem, sfx_stem, premix, mastered = (
        out_dir / f"{name}.wav" for name in ("narration", "music", "sfx", "premix", "master")
    )
    write_wav(narration_stem, voice, STEM_RATE_HZ)
    write_wav(music_stem, bed, STEM_RATE_HZ)
    write_wav(sfx_stem, effects, STEM_RATE_HZ)
    summed = voice + bed + effects
    peak = float(np.max(np.abs(summed))) if n else 0.0
    # A linear trim keeps the 16-bit premix off the rails; loudnorm puts the level back.
    write_wav(premix, summed * (PREMIX_PEAK / peak) if peak > PREMIX_PEAK else summed, STEM_RATE_HZ)
    report = master(premix, mastered, DEFAULT_MASTER_CHAIN)
    premix.unlink(missing_ok=True)
    return MixResult(narration_stem, music_stem, sfx_stem, mastered, report)
