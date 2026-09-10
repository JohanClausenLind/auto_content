"""Speech restoration: the voice chain one narration beat travels before it is laid out.

    TTS or a human take
        v
    artifact / noise detection            content_factory.audio.detect
        v
    ClearerVoice MossFormer2_SE_48K       optional cleanup (gated on the detection report)
        v
    ClearerVoice MossFormer2_SR_48K       optional 48 kHz band extension (same gate)
        v
    Resemble Enhance                      main restoration (denoise + generative band repair)
        v
    de-esser -> EQ -> light compression    one deterministic FFmpeg graph
        v
    <target rate> WAV, same length as the input

The two model steps run in their own uv environments (``skills/audio/clearervoice`` and
``skills/audio/resemble_enhance``) and are reached by subprocess, exactly like the TTS executors:
their torch pins never touch the control plane.

Length is sacred here. Every word timing measured at synthesis time keeps pointing at the same
speech, so the chain ends by padding or trimming to the input's exact duration and the report
records the drift each step introduced. A step that drifts past the spec's tolerance fails the
beat instead of quietly moving the captions.

The true-peak limiter and the EBU R128 normalization are *not* here: they belong to the programme
master in :mod:`content_factory.audio.mix`, after the beats are laid out and the music bed and
SFX are under the speech. Limiting each beat separately would flatten the mix twice.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from content_factory.audio.detect import detect_artifacts
from content_factory.audio.mix import AudioError, ffmpeg
from content_factory.schemas.audio import (
    AudioArtifactReport,
    SpeechRestorationReport,
    SpeechRestorationSpec,
    VoiceChainSpec,
)
from content_factory.schemas.base import file_sha256

# Bumped when the chain's own behaviour changes; the stage folds it into its cache key so a
# change here re-restores instead of serving audio the old code produced.
CHAIN_VERSION = "0.1.0"


class RestorationError(Exception):
    pass


# ---- the deterministic tail: de-esser -> EQ -> light compression -----------------------------


def voice_chain_filters(
    spec: VoiceChainSpec, *, sample_rate_hz: int, length_samples: int, de_ess: bool = True
) -> str:
    """The FFmpeg filter chain for one beat, in the order the audio travels it.

    ``length_samples`` is the output length at ``sample_rate_hz``: the chain pads a short result
    and trims a long one, so the beat comes out exactly as long as it went in.

    ``de_ess`` is the caller's measurement, ANDed with the spec's own switch. At an intensity that
    actually reduces esses (see :class:`VoiceChainSpec`) the filter takes a third of the 5-9 kHz
    band off material that never needed it, so it runs on the beats the detector flagged and not
    on the rest — the same gate every model step in this chain already uses.
    """
    parts = [f"aresample={sample_rate_hz}", "aformat=channel_layouts=mono:sample_fmts=fltp"]
    if spec.de_ess and de_ess:
        parts.append(
            f"deesser=i={spec.de_ess_intensity}:m={spec.de_ess_max_reduction}"
            f":f={spec.de_ess_frequency}:s=o"
        )
    if spec.high_pass_hz > 0:
        # Two poles: enough to lose rumble and plosive thump without thinning the voice.
        parts.append(f"highpass=f={spec.high_pass_hz}:poles=2")
    if spec.low_shelf_db != 0:
        parts.append(
            f"bass=g={spec.low_shelf_db}:f={spec.low_shelf_hz}:width_type=h:w={spec.low_shelf_hz}"
        )
    if spec.presence_db != 0:
        parts.append(
            f"equalizer=f={spec.presence_hz}:width_type=q:w={spec.presence_q}:g={spec.presence_db}"
        )
    if spec.air_db != 0:
        parts.append(f"treble=g={spec.air_db}:f={spec.air_hz}:width_type=h:w={spec.air_hz}")
    if spec.compressor:
        parts.append(
            f"acompressor=threshold={spec.comp_threshold_db}dB:ratio={spec.comp_ratio}"
            f":attack={spec.comp_attack_ms}:release={spec.comp_release_ms}"
            f":makeup={10 ** (spec.comp_makeup_db / 20):.6f}"
        )
    parts.append(f"apad=whole_len={length_samples}")
    parts.append(f"atrim=end_sample={length_samples}")
    return ",".join(parts)


def apply_voice_chain(
    in_wav: Path,
    out_wav: Path,
    spec: VoiceChainSpec,
    *,
    sample_rate_hz: int,
    length_samples: int,
    de_ess: bool = True,
) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-i",
            str(in_wav),
            "-af",
            voice_chain_filters(
                spec,
                sample_rate_hz=sample_rate_hz,
                length_samples=length_samples,
                de_ess=de_ess,
            ),
            "-ar",
            str(sample_rate_hz),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )


# ---- the model steps, each in its own environment --------------------------------------------


@dataclass(frozen=True)
class SkillRun:
    """One completed skill invocation: the JSON line it printed, parsed."""

    facts: dict[str, object]

    @property
    def sample_rate(self) -> int:
        return int(self.facts["sample_rate"])  # type: ignore[arg-type]

    @property
    def duration_ms(self) -> int:
        return int(self.facts["duration_ms"])  # type: ignore[arg-type]


def _run_skill(skill_dir: Path, args: list[str], *, timeout_s: int) -> SkillRun:
    if not (skill_dir / "run.py").is_file():
        raise RestorationError(f"skill not installed: {skill_dir}/run.py is missing")
    cmd = ["uv", "run", "--project", str(skill_dir), "python", str(skill_dir / "run.py"), *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    if proc.returncode != 0:
        raise RestorationError(
            f"{skill_dir.name} failed ({proc.returncode}): {proc.stderr[-1200:]}"
        )
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        raise RestorationError(f"{skill_dir.name} printed no result line")
    try:
        return SkillRun(json.loads(lines[-1]))
    except json.JSONDecodeError as exc:
        raise RestorationError(f"{skill_dir.name} printed {lines[-1][:200]!r}, not JSON") from exc


def run_clearervoice(
    skill_dir: Path,
    in_wav: Path,
    out_wav: Path,
    *,
    task: str,
    device: str,
    max_drift_ms: int,
    timeout_s: int,
) -> SkillRun:
    return _run_skill(
        skill_dir,
        [
            "--in",
            str(in_wav),
            "--out",
            str(out_wav),
            "--task",
            task,
            "--device",
            device,
            "--max-drift-ms",
            str(max_drift_ms),
        ],
        timeout_s=timeout_s,
    )


def run_resemble_enhance(
    skill_dir: Path,
    in_wav: Path,
    out_wav: Path,
    *,
    spec: SpeechRestorationSpec,
    timeout_s: int,
) -> SkillRun:
    return _run_skill(
        skill_dir,
        [
            "--in",
            str(in_wav),
            "--out",
            str(out_wav),
            "--mode",
            spec.enhancer_mode,
            "--device",
            spec.device,
            "--nfe",
            str(spec.enhancer_nfe),
            "--solver",
            spec.enhancer_solver,
            "--lambd",
            str(spec.enhancer_lambd),
            "--tau",
            str(spec.enhancer_tau),
            "--max-drift-ms",
            str(spec.max_duration_drift_ms),
        ],
        timeout_s=timeout_s,
    )


# ---- the whole chain for one beat -------------------------------------------------------------


@dataclass(frozen=True)
class SkillDirs:
    clearervoice: Path
    resemble_enhance: Path


def restore_beat(
    in_wav: Path,
    out_wav: Path,
    *,
    beat_id: str,
    spec: SpeechRestorationSpec,
    skills: SkillDirs,
    workdir: Path,
    timeout_s: int = 3600,
    before: AudioArtifactReport | None = None,
) -> SpeechRestorationReport:
    """Take one beat through the chain and write ``out_wav`` at ``spec.sample_rate_hz``.

    ``before`` lets a caller pass a detection report it already has (the stage caches them);
    otherwise the beat is measured here.
    """
    if not in_wav.is_file():
        raise RestorationError(f"input not found: {in_wav}")
    workdir.mkdir(parents=True, exist_ok=True)
    report_before = before or detect_artifacts(in_wav, asset_id=beat_id, thresholds=spec.thresholds)
    forced = spec.gate == "always"

    steps: list[str] = ["detect"]
    skipped: list[str] = []
    current = in_wav

    if spec.cleanup == "clearervoice":
        if forced or report_before.needs_cleanup:
            staged = workdir / f"{beat_id}.1-cleanup.wav"
            run_clearervoice(
                skills.clearervoice,
                current,
                staged,
                task="enhancement",
                device=spec.device,
                max_drift_ms=spec.max_duration_drift_ms,
                timeout_s=timeout_s,
            )
            steps.append("cleanup:clearervoice")
            current = staged
        else:
            skipped.append("cleanup:clearervoice: the take is not noisy (detection found nothing)")
    else:
        skipped.append("cleanup: off")

    if spec.band_extension == "clearervoice_sr":
        if forced or report_before.needs_band_extension:
            staged = workdir / f"{beat_id}.2-bandext.wav"
            run_clearervoice(
                skills.clearervoice,
                current,
                staged,
                task="super_resolution",
                device=spec.device,
                max_drift_ms=spec.max_duration_drift_ms,
                timeout_s=timeout_s,
            )
            steps.append("band_extension:clearervoice_sr")
            current = staged
        else:
            skipped.append(
                f"band_extension:clearervoice_sr: energy already reaches"
                f" {report_before.band_limit_hz / 1000:.1f} kHz"
            )
    else:
        skipped.append("band_extension: off")

    if spec.enhancer == "resemble_enhance":
        staged = workdir / f"{beat_id}.3-enhance.wav"
        run_resemble_enhance(
            skills.resemble_enhance, current, staged, spec=spec, timeout_s=timeout_s
        )
        steps.append(f"enhancer:resemble_enhance:{spec.enhancer_mode}")
        current = staged
    else:
        skipped.append("enhancer: off")

    # The chain tail always runs: it is the step that also guarantees the output length. Only the
    # de-esser inside it is conditional, and on the same measurement the model steps use.
    length_samples = round(report_before.duration_ms * spec.sample_rate_hz / 1000)
    de_ess = spec.chain.de_ess and (forced or report_before.needs_de_ess)
    try:
        apply_voice_chain(
            current,
            out_wav,
            spec.chain,
            sample_rate_hz=spec.sample_rate_hz,
            length_samples=length_samples,
            de_ess=de_ess,
        )
    except AudioError as exc:
        raise RestorationError(f"voice chain failed on {beat_id}: {exc}") from exc
    steps.append(f"voice_chain:{'de_ess+' if de_ess else ''}eq+compress")
    if spec.chain.de_ess and not de_ess:
        skipped.append(
            f"de_ess: 5-9 kHz energy is {report_before.sibilance_ratio:.3f} of the speech band"
            f" (limit {report_before.thresholds.sibilance_ratio_max})"
        )

    report_after = detect_artifacts(out_wav, asset_id=beat_id, thresholds=spec.thresholds)
    drift = abs(report_after.duration_ms - report_before.duration_ms)
    if drift > spec.max_duration_drift_ms:
        raise RestorationError(
            f"{beat_id}: restoration changed the length by {drift} ms"
            f" (limit {spec.max_duration_drift_ms} ms) — word timings would no longer line up"
        )
    return SpeechRestorationReport(
        beat_id=beat_id,
        steps=tuple(steps),
        skipped=tuple(skipped),
        input_sha256=file_sha256(in_wav),
        output_sha256=file_sha256(out_wav),
        input_sample_rate_hz=report_before.sample_rate_hz,
        output_sample_rate_hz=report_after.sample_rate_hz,
        input_duration_ms=report_before.duration_ms,
        output_duration_ms=report_after.duration_ms,
        before=report_before,
        after=report_after,
    )
