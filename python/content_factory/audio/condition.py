"""Conditioning for generated *non-speech* audio: sound effects, ambience beds, generated music.

The same architecture as the speech chain in :mod:`content_factory.audio.restore` —

    measure  ->  repair only what is broken  ->  normalise  ->  cap the true peak
             ->  land at the delivery rate, exactly as long as it came in

— and deliberately none of its models. That is the whole design decision, and it was made from
measurements on this host (2026-09-07), not from taste. Running the speech chain on sounds from
``assets/sfx``:

    ClearerVoice MossFormer2_SE_48K   whoosh   rms -24.0 -> -70.9 dBFS   0.5 % of the energy left
                                      rain     rms -33.6 -> -73.5 dBFS   1.0 %
                                      impact   rms -28.0 -> -71.3 dBFS   0.7 %
    Resemble Enhance                  whoosh   rms -24.0 -> -73.4 dBFS   waveform correlation
                                                                          0.002 with its input

Both models do exactly what they were trained to do: a speech enhancer treats everything that is
not a voice as the noise it exists to remove, and a speech restorer rebuilds what it hears as
speech. A whoosh comes back as unrelated sub-200 Hz rumble. So this module has no field, flag or
code path for either of them.

What generated non-speech audio actually needs is different and much simpler: the damage a
generator leaves (a DC step, clipping from an over-hot render, clicks at the seams between
generated windows), and a *predictable level*, so the bed's place in the mix stops depending on
how loud the model happened to render this time.

Length is load-bearing here for the same reason it is in speech: a bed is scored against the
picture frame by frame, so the chain ends by padding or trimming to the exact input length and
refuses anything that drifted further than the spec allows.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from content_factory.audio.detect import decode_mono, detect_artifacts
from content_factory.audio.mix import AudioError, ffmpeg
from content_factory.schemas.audio import (
    AudioArtifactReport,
    AudioProfile,
    SoundConditionReport,
    SoundConditionSpec,
)
from content_factory.schemas.base import file_sha256

CHAIN_VERSION = "0.1.0"


class ConditionError(Exception):
    pass


def measure_loudness_detail(path: Path) -> dict[str, float]:
    """Integrated, max-momentary and true-peak loudness in one ebur128 pass.

    ``mix.measure_loudness`` returns the programme summary; a one-shot needs the *momentary*
    maximum, because its integrated loudness is mostly the silence around it.
    """
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true:framelog=verbose",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    text = proc.stderr
    momentary = [float(m) for m in re.findall(r"M:\s*(-?\d+\.\d+)", text) if float(m) > -70.0]
    summary = text[text.rfind("Summary:") :] if "Summary:" in text else text

    def grab(label: str) -> float | None:
        m = re.search(rf"{label}:\s*(-?[\d.]+|-inf)", summary)
        if not m:
            return None
        return float("-inf") if m.group(1) == "-inf" else float(m.group(1))

    integrated, peak = grab("I"), grab("Peak")
    if integrated is None or peak is None:
        raise ConditionError(f"could not parse ebur128 output: {summary[-400:]}")
    return {
        "integrated_lufs": integrated,
        "max_momentary_lufs": max(momentary) if momentary else integrated,
        "true_peak_dbtp": peak,
    }


def _repair_filters(spec: SoundConditionSpec, before: AudioArtifactReport) -> list[str]:
    """Only the repairs the measurements actually asked for, in signal order."""
    parts: list[str] = []
    if spec.declip and before.has("clipping"):
        parts.append("adeclip")
    if spec.declick:
        # Generated beds are stitched from fixed-length windows; the joins are where clicks live.
        parts.append("adeclick")
    if spec.denoise:
        parts.append(f"afftdn=nr={spec.denoise_nr_db}:nf=-40")
    if spec.remove_dc and before.has("dc_offset"):
        parts.append("dcshift=shift=0:limitergain=0")
    if spec.high_pass_hz > 0:
        parts.append(f"highpass=f={spec.high_pass_hz}:poles=2")
    if spec.low_pass_hz > 0:
        parts.append(f"lowpass=f={spec.low_pass_hz}:poles=2")
    return parts


def _level_gain_db(spec: SoundConditionSpec, measured: dict[str, float]) -> tuple[float, bool]:
    """The gain that puts ``measured`` on target, clamped so nothing lifts a noise floor for ever
    and so the true peak stays under the ceiling. Returns (gain_db, was_limited)."""
    if spec.loudness_metric == "off":
        return 0.0, False
    key = "integrated_lufs" if spec.loudness_metric == "integrated" else "max_momentary_lufs"
    current = measured[key]
    if current == float("-inf"):
        raise ConditionError("asset is silent: there is no level to normalise")
    wanted = spec.target_lufs - current
    headroom = spec.target_true_peak_dbtp - measured["true_peak_dbtp"]
    gain = min(wanted, headroom, spec.max_gain_db)
    gain = max(gain, -spec.max_gain_db)
    return round(gain, 2), gain < wanted - 0.05


def condition_sound(
    in_wav: Path,
    out_wav: Path,
    *,
    asset_id: str,
    spec: SoundConditionSpec | None = None,
    workdir: Path | None = None,
) -> SoundConditionReport:
    """Condition one generated asset and write ``out_wav`` at ``spec.sample_rate_hz``."""
    spec = spec or SoundConditionSpec()
    if spec.profile is AudioProfile.speech:
        raise ConditionError(
            "speech does not belong here: it has its own chain with its own models"
            " (content_factory.audio.restore). This module exists because those models destroy"
            " non-speech material."
        )
    if not in_wav.is_file():
        raise ConditionError(f"input not found: {in_wav}")
    work = workdir or out_wav.parent
    work.mkdir(parents=True, exist_ok=True)
    out_wav.parent.mkdir(parents=True, exist_ok=True)

    before = detect_artifacts(
        in_wav, asset_id=asset_id, profile=spec.profile, thresholds=spec.thresholds
    )
    steps = ["detect"]
    skipped: list[str] = []
    if before.has("silence"):
        raise ConditionError(f"{asset_id}: the generated asset is digital silence")

    samples, in_rate = decode_mono(in_wav)
    length_samples = round(before.duration_ms * spec.sample_rate_hz / 1000)

    repairs = _repair_filters(spec, before)
    if repairs:
        steps.append("repair:" + "+".join(f.split("=")[0] for f in repairs))
    else:
        skipped.append("repair: the measurements found nothing to fix")
    if spec.declip and not before.has("clipping"):
        skipped.append("declip: no clipped samples measured")
    if not spec.denoise:
        # Deliberate: on a bed the "noise" is the content, and this is where a naive reuse of the
        # speech chain does its damage.
        skipped.append("denoise: off by default for non-speech (the noise is often the sound)")

    staged = work / f"{asset_id}.1-repaired.wav"
    chain = [f"aresample={spec.sample_rate_hz}", "aformat=channel_layouts=mono:sample_fmts=fltp"]
    chain += repairs
    try:
        ffmpeg(
            [
                "-i",
                str(in_wav),
                "-af",
                ",".join(chain),
                "-ar",
                str(spec.sample_rate_hz),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(staged),
            ]
        )
    except AudioError as exc:
        raise ConditionError(f"repair pass failed on {asset_id}: {exc}") from exc

    measured = measure_loudness_detail(staged)
    gain_db, limited = _level_gain_db(spec, measured)
    if spec.loudness_metric == "off":
        skipped.append("loudness: off")
    else:
        steps.append(f"loudness:{spec.loudness_metric}->{spec.target_lufs}LUFS")
    if limited:
        # Honest rather than silent: the asset did not reach the target and the caller should know
        # whether that was the ceiling or the gain limit.
        steps.append("loudness:limited_by_true_peak_or_max_gain")

    tail = [f"volume={gain_db}dB"] if gain_db else []
    # The same true-peak safety the programme master uses, one asset early: a bed that peaks over
    # the ceiling forces the master to pull the whole film down to fit it.
    ceiling = 10 ** (spec.target_true_peak_dbtp / 20)
    tail.append(f"alimiter=limit={ceiling:.6f}:attack=5:release=50:level=disabled")
    steps.append("true_peak_limit")
    tail.append(f"apad=whole_len={length_samples}")
    tail.append(f"atrim=end_sample={length_samples}")
    try:
        ffmpeg(
            [
                "-i",
                str(staged),
                "-af",
                ",".join(tail),
                "-ar",
                str(spec.sample_rate_hz),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ]
        )
    except AudioError as exc:
        raise ConditionError(f"level pass failed on {asset_id}: {exc}") from exc

    after = detect_artifacts(
        out_wav, asset_id=asset_id, profile=spec.profile, thresholds=spec.thresholds
    )
    drift = abs(after.duration_ms - before.duration_ms)
    if drift > spec.max_duration_drift_ms:
        raise ConditionError(
            f"{asset_id}: conditioning changed the length by {drift} ms"
            f" (limit {spec.max_duration_drift_ms} ms) — a bed scored against the picture would"
            " no longer line up"
        )
    final = measure_loudness_detail(out_wav)
    del samples, in_rate  # measured via the report; kept above only to fail fast on a bad decode
    return SoundConditionReport(
        asset_id=asset_id,
        profile=spec.profile,
        steps=tuple(steps),
        skipped=tuple(skipped),
        input_sha256=file_sha256(in_wav),
        output_sha256=file_sha256(out_wav),
        input_duration_ms=before.duration_ms,
        output_duration_ms=after.duration_ms,
        output_sample_rate_hz=after.sample_rate_hz,
        gain_applied_db=gain_db,
        gain_limited=limited,
        measured_lufs=(
            final["integrated_lufs"]
            if spec.loudness_metric != "max_momentary"
            else final["max_momentary_lufs"]
        ),
        measured_true_peak_dbtp=final["true_peak_dbtp"],
        before=before,
        after=after,
    )


def report_json(report: SoundConditionReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=1, sort_keys=True)
