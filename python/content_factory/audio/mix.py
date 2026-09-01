"""Narration layout, mastering, and muxing via a typed FFmpeg wrapper (argument arrays only).

Layout: lead-in → segment 1 → pause → segment 2 … → tail. Produces the narration stem (WAV),
the per-beat measured offsets (ms) that drive the timeline compiler, a mastered stem at the house
target (-14 LUFS / -1 dBTP by default, two-pass loudnorm), and the muxed delivery MP4.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from content_factory.schemas.audio import AudioMixSpec, LoudnessReport, NarrationSegment
from content_factory.schemas.scenes import VisualBeat, WordTiming


class AudioError(Exception):
    pass


@dataclass(frozen=True)
class LaidOutBeat:
    beat_id: str
    start_ms: int
    end_ms: int
    words: tuple[WordTiming, ...]  # absolute ms


def ffmpeg(args: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise AudioError(f"ffmpeg failed: {proc.stderr[-1500:]}")
    return proc


def lay_out(segments: list[NarrationSegment], spec: AudioMixSpec) -> list[LaidOutBeat]:
    cursor = spec.lead_in_ms
    out: list[LaidOutBeat] = []
    for i, seg in enumerate(segments):
        words = tuple(
            WordTiming(word=w.word, start_ms=cursor + w.start_ms, end_ms=cursor + w.end_ms)
            for w in seg.words
        )
        out.append(LaidOutBeat(seg.beat_id, cursor, cursor + seg.duration_ms, words))
        cursor += seg.duration_ms + (spec.inter_beat_pause_ms if i < len(segments) - 1 else 0)
    return out


def apply_measurements(
    beats: tuple[VisualBeat, ...], laid_out: list[LaidOutBeat]
) -> tuple[VisualBeat, ...]:
    by_id = {b.beat_id: b for b in laid_out}
    updated: list[VisualBeat] = []
    for b in beats:
        m = by_id.get(b.beat_id)
        if m is None:
            raise AudioError(f"beat {b.beat_id} has no narration segment")
        # Speech end excludes the segment's trailing silence: use the last word end.
        speech_end = m.words[-1].end_ms if m.words else m.end_ms
        updated.append(
            b.model_copy(
                update={
                    "measured_start_ms": m.start_ms,
                    "measured_end_ms": speech_end,
                    "words": m.words,
                }
            )
        )
    return tuple(updated)


def build_narration_stem(
    segments: list[NarrationSegment],
    audio_files: dict[str, Path],
    spec: AudioMixSpec,
    out_wav: Path,
) -> int:
    """Concatenate segments with silences into one stem; returns total duration ms."""
    laid = lay_out(segments, spec)
    total_ms = laid[-1].end_ms + spec.tail_ms if laid else spec.lead_in_ms + spec.tail_ms
    inputs: list[str] = []
    filters: list[str] = []
    for i, seg in enumerate(segments):
        inputs += ["-i", str(audio_files[seg.beat_id])]
        filters.append(
            f"[{i}:a]aresample={spec.sample_rate_hz},aformat=channel_layouts=mono,adelay={laid[i].start_ms}:all=1[a{i}]"
        )
    mix_inputs = "".join(f"[a{i}]" for i in range(len(segments)))
    filters.append(
        f"{mix_inputs}amix=inputs={len(segments)}:normalize=0:duration=longest,apad=whole_dur={total_ms}ms,atrim=0:{total_ms}ms[out]"
    )
    ffmpeg(
        [
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-ar",
            str(spec.sample_rate_hz),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )
    return total_ms


def measure_loudness(
    path: Path, *, target_lufs: float = -14.0, target_tp: float = -1.0
) -> LoudnessReport:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    text = proc.stderr
    summary = text[text.rfind("Summary:") :] if "Summary:" in text else text

    def grab(label: str) -> float | None:
        m = re.search(rf"{label}:\s*(-?[\d.]+|-inf)", summary)
        if not m:
            return None
        return float("-inf") if m.group(1) == "-inf" else float(m.group(1))

    integrated = grab("I")
    tp = grab("Peak")
    lra = grab("LRA")
    if integrated is None or tp is None:
        raise AudioError(f"could not parse ebur128 output: {summary[-500:]}")
    return LoudnessReport(
        integrated_lufs=integrated,
        true_peak_dbtp=tp,
        loudness_range_lu=lra,
        target_lufs=target_lufs,
        target_true_peak_dbtp=target_tp,
    )


def master(
    in_wav: Path,
    out_wav: Path,
    *,
    target_lufs: float = -14.0,
    target_tp: float = -1.0,
    sample_rate: int = 48000,
) -> LoudnessReport:
    """Two-pass loudnorm to the house target, then measure the result."""
    first = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(in_wav),
            "-af",
            f"loudnorm=I={target_lufs}:TP={target_tp}:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", first.stderr, re.S)
    if not m:
        raise AudioError(f"loudnorm analysis failed: {first.stderr[-800:]}")
    stats = json.loads(m.group(0))
    if stats["input_i"] == "-inf":
        raise AudioError("input is silent")
    lin = f"loudnorm=I={target_lufs}:TP={target_tp}:LRA=11:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true:print_format=summary"  # noqa: E501
    ffmpeg(
        ["-i", str(in_wav), "-af", lin, "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(out_wav)]
    )
    return measure_loudness(out_wav, target_lufs=target_lufs, target_tp=target_tp)


def mux(video: Path, audio_wav: Path, out_mp4: Path) -> None:
    """One delivery encode: copy the video stream, AAC the stem, fast start."""
    ffmpeg(
        [
            "-i",
            str(video),
            "-i",
            str(audio_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
    )
