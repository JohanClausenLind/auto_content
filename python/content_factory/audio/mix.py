"""Narration layout, mastering, and muxing via a typed FFmpeg wrapper (argument arrays only).

Layout: lead-in → segment 1 → pause → segment 2 … → tail. Produces the narration stem (WAV),
the per-beat measured offsets (ms) that drive the timeline compiler, a mastered stem at the house
target (true-peak limiter into two-pass EBU R128 loudnorm, -14 LUFS / -1 dBTP by default), and the
muxed delivery MP4.

The per-beat voice chain that runs before any of this - detection, cleanup, band extension,
restoration, de-esser, EQ, compression - is content_factory.audio.restore.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from content_factory.audio.cues import SoundLibrary, resolve
from content_factory.schemas.audio import (
    AudioMixSpec,
    CueSheet,
    LoudnessReport,
    MasterChainSpec,
    NarrationSegment,
)
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


def add_music_bed(
    narration_wav: Path,
    music: Path,
    out_wav: Path,
    *,
    duration_ms: int,
    gain_db: float = -18.0,
    sample_rate_hz: int = 48000,
    duck_against: Path | None = None,
) -> None:
    """Mix a music bed under the narration stem: the bed loops to the stem's length, sits at
    ``gain_db`` below full scale, ducks slightly under speech via sidechain compression, and
    fades out over the final second. The combined stem then goes through the same two-pass
    loudnorm master as a narration-only mix.

    ``duck_against`` is the sidechain *key* when the thing being mixed into is no longer just
    speech. Beds are added one after another, so by the time the effects bed arrives the first
    input already contains the music — and keying off that made the effects duck under the music
    as well as under the voice, which at a -18 dB bed is loud enough to hold them ducked for the
    whole film. Pointing the key at the narration stem makes "duck under speech" mean what it
    says. Left unset the key is the mix itself, which is right for the first bed.
    """
    key = duck_against or narration_wav
    inputs = ["-i", str(narration_wav), "-stream_loop", "-1", "-i", str(music)]
    # The key is a third input only when it is a different file, so the common case stays a
    # two-input graph and the filter reads the same either way.
    key_label = "[0:a]"
    if duck_against is not None and duck_against != narration_wav:
        inputs += ["-i", str(key)]
        key_label = "[2:a]"
    ffmpeg(
        [
            *inputs,
            "-filter_complex",
            (
                f"[1:a]aresample={sample_rate_hz},aformat=channel_layouts=mono,"
                f"volume={gain_db}dB[bed];"
                f"{key_label}aresample={sample_rate_hz},aformat=channel_layouts=mono[key];"
                "[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=600[duck];"
                "[0:a][duck]amix=inputs=2:normalize=0:duration=first,"
                f"afade=t=out:st={max(0.0, duration_ms / 1000 - 1.0):.3f}:d=1[out]"
            ),
            "-map",
            "[out]",
            "-ar",
            str(sample_rate_hz),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )


def place_sfx(
    sheet: CueSheet,
    library: SoundLibrary,
    out_wav: Path,
    *,
    sample_rate_hz: int = 48000,
) -> dict[str, object]:
    """Render a cue sheet to one mono wav the length of the mix. One ffmpeg call, no models.

    Each cue becomes an input, trimmed or looped to its own length, gain-trimmed, faded, delayed to
    its own start, and summed. `amix` with `normalize=0` because the levels were decided by the
    cue sheet and the library's own loudness pass — letting ffmpeg renormalise would make every
    cue's level depend on how many other cues happen to be in the film.

    Returns facts for the run record: what was placed, and where.
    """
    pairs = resolve(sheet, library)
    if not pairs:
        # A silent bed of the right length, so callers need no special case: the mix folds in a
        # track that changes nothing rather than branching on whether any cue was cut.
        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={sample_rate_hz}:cl=mono",
                "-t",
                f"{sheet.total_ms / 1000:.3f}",
                "-c:a",
                "pcm_s16le",
                str(out_wav),
            ]
        )
        return {"cues": 0, "sounds": [], "total_ms": sheet.total_ms}

    args: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    for index, (cue, sound) in enumerate(pairs):
        length_ms = cue.duration_ms or round(sound.duration_s * 1000)
        length_ms = min(length_ms, max(1, sheet.total_ms - cue.at_ms))
        if cue.duration_ms is not None and sound.loopable:
            args += ["-stream_loop", "-1"]
        args += ["-i", str(sound.path)]
        parts = [
            f"aresample={sample_rate_hz}",
            "aformat=channel_layouts=mono",
            f"atrim=end={length_ms / 1000:.3f}",
            "asetpts=N/SR/TB",
            f"volume={cue.gain_db}dB",
        ]
        if cue.fade_in_ms:
            parts.append(f"afade=t=in:st=0:d={cue.fade_in_ms / 1000:.3f}")
        if cue.fade_out_ms:
            start = max(0.0, (length_ms - cue.fade_out_ms) / 1000)
            parts.append(f"afade=t=out:st={start:.3f}:d={cue.fade_out_ms / 1000:.3f}")
        # adelay last: a delay before the fades would fade the silence instead of the sound.
        parts.append(f"adelay={cue.at_ms}:all=1")
        label = f"c{index}"
        chains.append(f"[{index}:a]{','.join(parts)}[{label}]")
        labels.append(f"[{label}]")

    joined = "".join(labels)
    graph = (
        ";".join(chains)
        + f";{joined}amix=inputs={len(labels)}:normalize=0:duration=longest,"
        + f"apad=whole_dur={sheet.total_ms / 1000:.3f},"
        + f"atrim=end={sheet.total_ms / 1000:.3f}[out]"
    )
    ffmpeg(
        [
            *args,
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-ar",
            str(sample_rate_hz),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )
    return {
        "cues": len(pairs),
        "sounds": sorted({sound.sound_id for _cue, sound in pairs}),
        "roles": sorted({cue.role.value for cue, _sound in pairs}),
        "total_ms": sheet.total_ms,
        "library_sha256": sheet.library_sha256,
    }


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


DEFAULT_MASTER_CHAIN = MasterChainSpec()


def limit_true_peak(in_wav: Path, out_wav: Path, spec: MasterChainSpec) -> None:
    """Look-ahead peak limiter at ``spec.limiter_ceiling_dbtp``, before normalization.

    This is the step that catches the isolated transients — a plosive, an SFX hit landing on a
    consonant — that would otherwise force loudnorm's own true-peak stage to pull the whole
    programme's gain down to fit them. The ceiling deliberately sits below the delivery ceiling:
    normalization still has to add or remove gain after this, and a limiter parked exactly at the
    delivery ceiling leaves that move nowhere to go. ``level=disabled`` keeps FFmpeg from
    auto-levelling, which would fight the R128 pass that follows.
    """
    ceiling = 10 ** (spec.limiter_ceiling_dbtp / 20)
    ffmpeg(
        [
            "-i",
            str(in_wav),
            "-af",
            (
                f"alimiter=limit={ceiling:.6f}:attack={spec.limiter_attack_ms}"
                f":release={spec.limiter_release_ms}:level=disabled"
            ),
            "-ar",
            str(spec.sample_rate_hz),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )


def master(
    in_wav: Path,
    out_wav: Path,
    spec: MasterChainSpec = DEFAULT_MASTER_CHAIN,
) -> LoudnessReport:
    """The programme master: true-peak limiter -> two-pass EBU R128 normalization -> WAV.

    Two passes because one is a guess: the first measures the programme, the second applies the
    measured correction linearly, which is what keeps the dynamics intact instead of riding gain.
    The result is measured again and returned; the caller decides whether it is close enough.
    """
    source = in_wav
    limited: Path | None = None
    if spec.limiter:
        limited = out_wav.with_name(out_wav.stem + "-limited.wav")
        limit_true_peak(in_wav, limited, spec)
        source = limited
    target_lufs = spec.target_lufs
    # What the master aims at, which is below the delivery ceiling: the AAC encode raises the
    # true peak (see `MasterChainSpec.encode_headroom_db`). The *report* is still measured
    # against the delivery ceiling, so `passed` keeps meaning "the file that ships is legal".
    target_tp = spec.master_true_peak_dbtp
    lra = spec.loudness_range_lu
    first = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(source),
            "-af",
            f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={lra}:print_format=json",
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
    lin = f"loudnorm=I={target_lufs}:TP={target_tp}:LRA={lra}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true:print_format=summary"  # noqa: E501
    ffmpeg(
        [
            "-i",
            str(source),
            "-af",
            lin,
            "-ar",
            str(spec.sample_rate_hz),
            "-c:a",
            "pcm_s16le",
            str(out_wav),
        ]
    )
    if limited is not None:
        limited.unlink(missing_ok=True)
    report = measure_loudness(out_wav, target_lufs=target_lufs, target_tp=target_tp)
    report = _trim_to_ceiling(out_wav, report, target_lufs=target_lufs, target_tp=target_tp)
    # Reported against the delivery ceiling, which is the number a caller's gate is about.
    return report.model_copy(update={"target_true_peak_dbtp": spec.target_true_peak_dbtp})


TRUE_PEAK_TRIM_MARGIN_DB = 0.05
"""How far under the ceiling the corrective trim aims, so rounding cannot leave it on the line."""


def _trim_to_ceiling(
    out_wav: Path, report: LoudnessReport, *, target_lufs: float, target_tp: float
) -> LoudnessReport:
    """Pull the master back under the true-peak ceiling if the linear pass pushed it over.

    `loudnorm` in `linear=true` mode applies one measured gain and does **not** limit: its `TP`
    argument only informs the gain it picks, and its true-peak estimate is a prediction. When the
    prediction is low the delivered file is over the ceiling and nothing catches it — measured on
    `audio-picture-story` (2026-09-10): the master landed on -14.2 LUFS, right on target, with a
    true peak of **-0.8 dBTP** against a -1.0 ceiling, and the stage refused the run at stage
    nine of twenty rather than deliver it.

    The correction is a second linear gain of exactly the overshoot, which moves the true peak by
    the same number of dB and the programme loudness by the same number too — a fifth of a decibel
    on the measured case, well inside the 1 LU tolerance. Linear, because the alternative is to
    compress, and this chain deliberately does not.
    """
    over = report.true_peak_dbtp - target_tp
    if over <= 0:
        return report
    trimmed = out_wav.with_name(out_wav.stem + "-trim.wav")
    ffmpeg(
        [
            "-i",
            str(out_wav),
            "-af",
            f"volume={-(over + TRUE_PEAK_TRIM_MARGIN_DB):.2f}dB",
            "-c:a",
            "pcm_s16le",
            str(trimmed),
        ]
    )
    trimmed.replace(out_wav)
    return measure_loudness(out_wav, target_lufs=target_lufs, target_tp=target_tp)


MUX_PAD_TOLERANCE_MS = 60
"""How far the picture may fall short of the last spoken word before the last frame is held.

A few frames of a word's decay is not worth a re-encode; a sentence is.
"""


def _media_ms(path: Path) -> int:
    """Duration in whole milliseconds, from the container. 0 when it cannot be read."""
    from content_factory.qc.media import ffprobe

    try:
        seconds = float(ffprobe(path)["format"]["duration"])
    except (KeyError, ValueError, TypeError, subprocess.CalledProcessError):
        return 0
    return max(0, round(seconds * 1000))


def mux(video: Path, audio_wav: Path, out_mp4: Path, *, min_video_ms: int | None = None) -> None:
    """One delivery encode: the video stream copied, the stem as AAC, fast start.

    ``min_video_ms`` is how long the picture has to last for the film to carry everything that is
    said — the end of the last spoken word, as the mix laid it out. Given it, the **last frame is
    held** until then instead of ``-shortest`` cutting the words off.

    That flag alone was fine for as long as every lane's picture came from the timeline compiler,
    which sizes itself to the speech: what it cut was the stem's ``tail_ms`` of silence. A lane
    whose picture is its own length — drawings held for the spans of speech they illustrate —
    loses *sentences* to it instead, which is what a live run of ``audio-picture-story`` measured
    as "audio shorter than the narration it should carry" from the composer's own QC.

    Without ``min_video_ms`` nothing changes: the copy-and-truncate path is taken exactly as
    before, which is what keeps the timeline lanes' frame counts identical to the frame.
    """
    pad_ms = (min_video_ms - _media_ms(video)) if min_video_ms is not None else 0
    if pad_ms <= MUX_PAD_TOLERANCE_MS:
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
        return
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
            # tpad clones the last decoded frame, so the film holds on its final picture instead
            # of cutting to black — and the colour tags are re-declared because the re-encode
            # would otherwise write an untagged stream for players to guess at.
            "-vf",
            f"tpad=stop_mode=clone:stop_duration={pad_ms / 1000:.3f}",
            # Whatever is left of the audio after the last word is still trimmed: the picture is
            # held to carry the words, not to sit on a still through a second of silence.
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-preset",
            "slow",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ],
        timeout=3600,
    )
