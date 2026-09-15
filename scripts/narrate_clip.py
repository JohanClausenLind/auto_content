"""Speak a script, time it against a picture, duck the bed under it, and mux."""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from content_factory.audio.mix import LoudnessReport

REPO = Path(__file__).resolve().parents[1]
SPEECH_LUFS = -16.0
"""Working level for the voice track before it meets the bed -- not a delivery target.

The programme master is `audio.mix.DEFAULT_MASTER_CHAIN` (-14 LUFS / -1 dBTP), which is the repo's
standard and does true-peak limiting properly."""


@dataclass
class Spoken:
    line_id: str
    text: str
    at_s: float
    wav: Path
    duration_s: float
    words: list[dict]

    @property
    def ends_s(self) -> float:
        return self.at_s + self.duration_s


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as handle:
        return handle.getnframes() / handle.getframerate()


def speak(
    script: dict, out_dir: Path, *, instruct: str, speaker: str = "", describe: str = ""
) -> list[Spoken]:
    """One wav per line, cached on disk so re-timing does not re-speak."""
    from content_factory.audio.tts import Qwen3TTS
    from content_factory.config import get_settings
    from content_factory.schemas.audio import NarrationRequest, VoiceIdentity

    cfg = get_settings().narration
    tts = Qwen3TTS(
        REPO / "skills" / "audio" / "qwen3tts",
        device=cfg.qwen_device,
        language=cfg.qwen_language,
        instruct=instruct,
        seed=cfg.qwen_seed,
        takes=1,
    )
    voice = VoiceIdentity(
        provider="qwen3tts",
        voice_id="designed" if describe else (speaker or cfg.qwen_speaker),
        model_revision="qwen3-tts-1.7b",
        speed=cfg.speed,
        seed=cfg.qwen_seed,
    )
    spoken: list[Spoken] = []
    for i, line in enumerate(script["lines"]):
        wav = out_dir / f"{line['id']}.wav"
        meta = out_dir / f"{line['id']}.json"
        if not (wav.exists() and meta.exists()):
            request = NarrationRequest(
                beat_id=f"bea_vo{i:07d}",
                display_text=line["text"],
                spoken_text=line["text"],
                voice=voice,
            )
            result = tts.synthesize(request)
            wav.write_bytes(result.audio)
            meta.write_text(
                json.dumps(
                    {
                        "duration_ms": result.segment.duration_ms,
                        "timing_source": str(result.segment.timing_source),
                        "words": [w.model_dump(mode="json") for w in result.segment.words],
                    },
                    indent=1,
                )
            )
            print(f"  spoke {line['id']}: {result.segment.duration_ms / 1000:.2f}s", flush=True)
        facts = json.loads(meta.read_text())
        spoken.append(
            Spoken(
                line_id=line["id"],
                text=line["text"],
                at_s=float(line["at"]),
                wav=wav,
                duration_s=wav_seconds(wav),
                words=facts["words"],
            )
        )
    return spoken


LINE_MATCH_MAX_DB = 3.0
"""Ceiling on the line-to-line level correction.

Even inside ONE take the model does not hold its level: measured across the seven cut lines,
speech-only level (pauses excluded, so this is not a long-line artefact) spread **5.4 dB**, with the
two longest sentences consistently the quietest. That reads as inconsistency on top of the prosody.

Capped rather than flattened, because a narration is allowed to drop for an intimate line -- what
it is not allowed to do is drop 5 dB because the sentence was long. 3 dB takes the measured spread
to roughly 2 and leaves deliberate shading intact."""


def _speech_level_db(samples, rate: int) -> float:
    """Level of the *speech*, with the pauses dropped -- a long line is not a quiet line."""
    import numpy as np

    window = max(1, int(0.03 * rate))
    frames = np.array(
        [
            np.sqrt((samples[i : i + window] ** 2).mean())
            for i in range(0, max(1, len(samples) - window), window)
        ]
    )
    if not len(frames):
        return -120.0
    voiced = frames[frames > frames.max() * 0.12]
    if not len(voiced):
        return -120.0
    return float(20 * np.log10(max(voiced.mean(), 1e-9)))


def match_line_levels(clips: list, rate: int, *, max_db: float = LINE_MATCH_MAX_DB) -> list:
    """Bring every line towards the median speech level, by at most ``max_db``."""
    import numpy as np

    levels = [_speech_level_db(c, rate) for c in clips]
    target = float(np.median(levels))
    out = []
    for clip, level in zip(clips, levels, strict=True):
        correction = float(np.clip(target - level, -max_db, max_db))
        out.append(clip * (10 ** (correction / 20.0)))
    return out


def speak_continuous(
    script: dict, out_dir: Path, *, instruct: str, speaker: str = "", describe: str = ""
) -> list[Spoken]:
    """Speak the WHOLE script as one utterance, then cut it at the pauses."""
    from content_factory.audio.continuity import cut_take, joined_text
    from content_factory.audio.tts import Qwen3TTS
    from content_factory.config import get_settings
    from content_factory.schemas.audio import NarrationRequest, VoiceIdentity

    cfg = get_settings().narration
    lines = script["lines"]
    voice = VoiceIdentity(
        provider="qwen3tts",
        voice_id="designed" if describe else (speaker or cfg.qwen_speaker),
        model_revision="qwen3-tts-1.7b",
        speed=cfg.speed,
        seed=cfg.qwen_seed,
    )
    requests = [
        NarrationRequest(
            beat_id=f"bea_vo{i:07d}",
            display_text=line["text"],
            spoken_text=line["text"],
            voice=voice,
        )
        for i, line in enumerate(lines)
    ]
    take = out_dir / "take.wav"
    meta = out_dir / "take.json"
    if not (take.exists() and meta.exists()):
        tts = Qwen3TTS(
            REPO / "skills" / "audio" / "qwen3tts",
            device=cfg.qwen_device,
            language=cfg.qwen_language,
            instruct=instruct,
            describe=describe,
            seed=cfg.qwen_seed,
            takes=1,
        )
        paragraph = joined_text(requests)
        result = tts.synthesize(
            NarrationRequest(
                beat_id="bea_vo0000000",
                display_text=paragraph,
                spoken_text=paragraph,
                voice=voice,
            )
        )
        take.write_bytes(result.audio)
        meta.write_text(
            json.dumps(
                {
                    "duration_ms": result.segment.duration_ms,
                    "timing_source": str(result.segment.timing_source),
                    "words": [w.model_dump(mode="json") for w in result.segment.words],
                },
                indent=1,
            )
        )
        print(f"  one take: {result.segment.duration_ms / 1000:.2f}s", flush=True)

    from content_factory.schemas.audio import WordTiming

    facts = json.loads(meta.read_text())
    words = [WordTiming(**w) for w in facts["words"]]
    cuts = cut_take(requests, take.read_bytes(), words, facts["duration_ms"])
    spoken: list[Spoken] = []
    for line, _request, cut in zip(lines, requests, cuts, strict=True):
        wav = out_dir / f"{line['id']}.wav"
        wav.write_bytes(cut.audio)
        (out_dir / f"{line['id']}.json").write_text(
            json.dumps(
                {
                    "duration_ms": cut.duration_ms,
                    "timing_source": facts["timing_source"],
                    "cut_from_take_ms": list(cut.take_span_ms),
                    "words": [w.model_dump(mode="json") for w in cut.words],
                },
                indent=1,
            )
        )
        spoken.append(
            Spoken(
                line_id=line["id"],
                text=line["text"],
                at_s=float(line["at"]),
                wav=wav,
                duration_s=wav_seconds(wav),
                words=[w.model_dump(mode="json") for w in cut.words],
            )
        )
    return spoken


def check_timing(spoken: list[Spoken], video_s: float, *, gap_s: float = 0.35) -> list[str]:
    """Every way the measured lines disagree with the intended times, named."""
    problems: list[str] = []
    for a, b in itertools.pairwise(spoken):
        if a.ends_s + gap_s > b.at_s:
            problems.append(
                f"{a.line_id} ends at {a.ends_s:.2f}s and {b.line_id} starts at {b.at_s:.2f}s"
                f" -- {a.ends_s + gap_s - b.at_s:.2f}s too close (wants {gap_s:.2f}s of air)"
            )
    last = spoken[-1]
    if last.ends_s > video_s:
        problems.append(f"{last.line_id} runs {last.ends_s - video_s:.2f}s past the end of the cut")
    return problems


def build_voice_track(spoken: list[Spoken], video_s: float, dest: Path) -> None:
    """Lines dropped onto one silent track at their measured positions."""
    inputs: list[str] = ["-f", "lavfi", "-t", f"{video_s}", "-i", "anullsrc=r=44100:cl=mono"]
    filters: list[str] = []
    labels = ["[0:a]"]
    for i, s in enumerate(spoken, start=1):
        inputs += ["-i", str(s.wav)]
        delay = int(s.at_s * 1000)
        filters.append(f"[{i}:a]aformat=channel_layouts=mono,adelay={delay}|{delay}[v{i}]")
        labels.append(f"[v{i}]")
    filters.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0[out]")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-t",
            f"{video_s}",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ],
        check=True,
    )


VOICE_LUFS = -18.0
"""The voice track is levelled before it meets the bed.

Not cosmetic: the duck is keyed off the voice, so an uneven voice ducks unevenly. Measured before
this existed, the bed dropped 12.8 dB under a firm line and only 6.6 dB under a longer, softer one
-- leaving the words just 3 dB clear of the bed exactly where the line was hardest to hear."""


def duck_curve(
    voice,
    rate: int,
    *,
    depth_db: float,
    block_s: float = 0.02,
    attack_s: float = 0.08,
    release_s: float = 0.45,
    floor_db: float = -42.0,
):
    """A gain curve for the bed: full level in the gaps, exactly ``depth_db`` down under a line."""
    import numpy as np

    block = max(1, int(block_s * rate))
    blocks = max(1, len(voice) // block)
    rms = np.sqrt((voice[: blocks * block].reshape(blocks, block) ** 2).mean(axis=1))
    speaking = 20 * np.log10(np.maximum(rms, 1e-9)) > floor_db
    # hold the duck open across the short silences inside a sentence
    hold = max(1, int(release_s / block_s))
    held = np.copy(speaking)
    countdown = 0
    for i, on in enumerate(speaking):
        countdown = hold if on else max(0, countdown - 1)
        held[i] = on or countdown > 0
    target = np.where(held, 10 ** (depth_db / 20.0), 1.0)
    # one-pole smoothing so the bed slides rather than steps
    smoothed = np.empty_like(target)
    level = 1.0
    a_att = 1.0 - np.exp(-block_s / max(attack_s, 1e-3))
    a_rel = 1.0 - np.exp(-block_s / max(release_s, 1e-3))
    for i, want in enumerate(target):
        level += (want - level) * (a_att if want < level else a_rel)
        smoothed[i] = level
    return np.interp(np.arange(len(voice)), np.linspace(0, len(voice) - 1, len(smoothed)), smoothed)


def mix(voice_path: Path, bed_path: Path, dest: Path, *, depth_db: float = -12.0) -> None:
    """Voice over a bed ducked by a computed curve, then levelled for delivery."""
    import numpy as np

    _to_lufs(voice_path, VOICE_LUFS)
    voice, rate = _read(voice_path)
    bed, bed_rate = _read(bed_path)
    if bed_rate != rate:
        raise RuntimeError(f"bed is {bed_rate} Hz and the voice is {rate} Hz")
    n = min(len(voice), len(bed))
    voice, bed = voice[:n], bed[:n]
    ducked = bed * duck_curve(voice, rate, depth_db=depth_db)
    out = np.clip(ducked + voice, -1.0, 1.0)
    _write(out, rate, dest)
    _write(ducked, rate, dest.with_name("bed_ducked.wav"))
    report = master_programme(dest)
    print(
        f"  master: {report.integrated_lufs:.1f} LUFS, {report.true_peak_dbtp:.2f} dBTP"
        f" -- {'within' if report.passed else 'OVER'} the delivery ceiling"
    )


def _read(path: Path):
    import numpy as np

    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
        channels = handle.getnchannels()
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def _write(samples, rate: int, dest: Path) -> None:
    import numpy as np

    with wave.open(str(dest), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def _to_lufs(wav: Path, target: float) -> None:
    """Static gain to a loudness target."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(wav), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    measured = None
    for line in proc.stderr.splitlines():
        if line.strip().startswith("I:"):
            try:
                measured = float(line.split()[-2])
            except (ValueError, IndexError):
                measured = None
    if measured is None:
        return
    tmp = wav.with_suffix(".gain.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(wav),
            "-af",
            f"volume={target - measured:.2f}dB",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(tmp),
        ],
        check=True,
    )
    tmp.replace(wav)


def master_programme(wav: Path) -> LoudnessReport:
    """The repo's own master chain, rather than a gain and a sample limiter."""
    from content_factory.audio.mix import DEFAULT_MASTER_CHAIN, master

    mastered = wav.with_name(wav.stem + "-master.wav")
    report = master(wav, mastered, DEFAULT_MASTER_CHAIN)
    mastered.replace(wav)
    return report


def write_captions(spoken: list[Spoken], dest: Path) -> None:
    """An SRT cut from the *measured* word timings, so the captions cannot drift off the voice."""

    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    for n, line in enumerate(spoken, start=1):
        blocks.append(f"{n}\n{stamp(line.at_s)} --> {stamp(line.ends_s)}\n{line.text}\n")
    dest.write_text("\n".join(blocks), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("script")
    ap.add_argument("bed")
    ap.add_argument("out_dir", nargs="?", default="output/vo")
    ap.add_argument("--instruct", default="calm, reflective, unhurried, a quiet memory")
    ap.add_argument(
        "--describe",
        default="",
        help="VoiceDesign: build the narrator from this sentence instead of picking a timbre",
    )
    ap.add_argument(
        "--voice",
        default="",
        help=(
            "Qwen timbre, overriding narration.qwen_speaker. Only `aiden` and `ryan` are "
            "English-native; the rest carry an accent. Measured on one line, 2026-09-13: "
            "aiden 124 Hz at 3.6 words/s, ryan 127 Hz at 1.9, uncle_fu 140 Hz at 3.2, "
            "sohee 182 Hz, vivian 179 Hz, serena 233 Hz"
        ),
    )
    ap.add_argument("--subtitles", action="store_true", help="burn the captions into the picture")
    ap.add_argument(
        "--per-line",
        action="store_true",
        help="speak each line as its own utterance (the old behaviour; see `speak_continuous`)",
    )
    args = ap.parse_args()

    from content_factory.qc.media import ffprobe

    video, bed = Path(args.video), Path(args.bed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = json.loads(Path(args.script).read_text())
    video_s = float(ffprobe(video)["format"]["duration"])

    speaker_fn = speak if args.per_line else speak_continuous
    spoken = speaker_fn(
        script, out_dir, instruct=args.instruct, speaker=args.voice, describe=args.describe
    )
    print(f"\n{'line':>5} {'at':>7} {'len':>7} {'ends':>7}  text")
    for s in spoken:
        print(
            f"{s.line_id:>5} {s.at_s:>6.2f}s {s.duration_s:>6.2f}s {s.ends_s:>6.2f}s  {s.text[:54]}"
        )
    problems = check_timing(spoken, video_s)
    print()
    for p in problems:
        print(f"  TIMING: {p}")
    if not problems:
        print("  timing: every line fits, with air between them")

    voice = out_dir / "voice.wav"
    build_voice_track(spoken, video_s, voice)
    mixed = out_dir / "mixed.wav"
    mix(voice, bed, mixed)
    write_captions(spoken, out_dir / "captions.srt")

    muxed = out_dir / "narrated.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(mixed)]
    if args.subtitles:
        srt = (out_dir / "captions.srt").resolve()
        style = "FontName=HelveticaNeue Condensed,FontSize=22,PrimaryColour=&H00FFFFFF,"
        style += "OutlineColour=&H20000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=48"
        cmd += ["-vf", f"subtitles='{srt}':force_style='{style}'", "-c:v", "libx264", "-crf", "18"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-shortest", str(muxed)]
    subprocess.run(cmd, check=True)
    print(f"\n-> {muxed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
