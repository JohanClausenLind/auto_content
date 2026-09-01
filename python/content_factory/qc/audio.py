"""Audio QC: loudness/true-peak compliance, silence outside pauses, A/V duration match."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from content_factory.audio.mix import measure_loudness
from content_factory.qc.media import Finding, QCResult, Severity, ffprobe


def check_audio_in_video(
    path: Path, *, expected_speech_ms: int, target_lufs: float = -14.0, target_tp: float = -1.0
) -> QCResult:
    findings: list[Finding] = []
    info = ffprobe(path)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if audio is None:
        return QCResult((Finding("audio_stream", Severity.blocker, "no audio stream"),), {})
    facts: dict[str, object] = {
        "audio_codec": audio.get("codec_name"),
        "sample_rate": audio.get("sample_rate"),
        "channels": audio.get("channels"),
    }
    a_dur = float(audio.get("duration") or 0)
    v_dur = float(video.get("duration") or 0) if video else a_dur
    facts["audio_duration_s"] = a_dur
    facts["video_duration_s"] = v_dur
    if abs(a_dur - v_dur) > 0.25:
        findings.append(
            Finding("av_duration", Severity.major, f"audio {a_dur:.2f}s vs video {v_dur:.2f}s")
        )
    rep = measure_loudness(path, target_lufs=target_lufs, target_tp=target_tp)
    facts["integrated_lufs"] = rep.integrated_lufs
    facts["true_peak_dbtp"] = rep.true_peak_dbtp
    if abs(rep.integrated_lufs - target_lufs) > rep.tolerance_lu:
        findings.append(
            Finding(
                "loudness",
                Severity.major,
                f"{rep.integrated_lufs:.1f} LUFS vs target {target_lufs}",
            )
        )
    if rep.true_peak_dbtp > target_tp + 0.1:
        findings.append(
            Finding(
                "true_peak", Severity.major, f"{rep.true_peak_dbtp:.1f} dBTP exceeds {target_tp}"
            )
        )
    # Silence outside pauses: total detected silence must not exceed non-speech budget generously.
    sil = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-45dB:d=2.5",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    long_silences = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", sil.stderr)]
    facts["long_silences_s"] = long_silences
    if long_silences:
        findings.append(
            Finding(
                "silence",
                Severity.major,
                f"{len(long_silences)} silence(s) ≥ 2.5 s inside the programme",
            )
        )
    if expected_speech_ms and a_dur * 1000 < expected_speech_ms * 0.9:
        findings.append(
            Finding(
                "truncated", Severity.critical, "audio shorter than the narration it should carry"
            )
        )
    return QCResult(tuple(findings), facts)
