"""Human voice takes: recordings made outside the factory, force-aligned to the locked script.

The ``voice_over`` stage is the twin of ``synthesize_narration`` — it produces exactly the same
``NarrationSegment`` per beat, so captions, the timeline compiler, the mix and the final mux never
learn whether a machine or a person spoke. What differs is where the audio comes from and where the
timings come from:

* audio: ``<takes_dir>/<beat_id>.wav`` (or ``<beat_id>.<speaker>.wav`` for a two-hander), never
  synthesized. A missing take fails the stage by name instead of quietly substituting a voice.
* timings: forced alignment (ADR-0004 precedence — whisperx, else faster-whisper), each in its own
  environment behind :data:`SUBPROCESS_RUN`; ``even_split`` apportions the measured duration by
  word length and is the offline stand-in the core suite runs on.

The transcript is compared with the locked script through the same
:func:`content_factory.human_tasks.validation.validate_take` an operator's take review uses, so a
mislabelled or re-read take is rejected here for the same reason and with the same diff it would
show in the UI — improvisation inside the tolerance is accepted as performed and recorded as such.
"""

from __future__ import annotations

import json
import subprocess
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from content_factory.audio.normalize import tokenize_words
from content_factory.schemas.audio import NarrationSegment, TimingSource, VoiceIdentity
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.scenes import WordTiming

# Seam: unit tests replace this instead of running an aligner.
SUBPROCESS_RUN: Callable[..., subprocess.CompletedProcess] = subprocess.run

AUDIO_SUFFIXES = (".wav", ".flac", ".mp3", ".m4a")


class TakeError(RuntimeError):
    """A take is missing, unreadable, or says something other than the script."""


@dataclass(frozen=True)
class Take:
    beat_id: str
    speaker: str
    path: Path

    @property
    def take_id(self) -> str:
        return f"{self.beat_id}.{self.speaker}"


def discover_takes(
    takes_dir: Path, beat_ids: Sequence[str], *, also: Sequence[Path] = ()
) -> dict[str, Take]:
    """One take per beat: ``<beat_id>.wav`` or ``<beat_id>.<speaker>.wav``.

    Two speakers in one beat is a directing decision, not a file-naming one — record the beat as
    two beats. Here, more than one candidate for a beat is an error rather than a coin flip.

    ``also`` are further directories to look in, searched after ``takes_dir``. The run's uploads
    folder is passed there by the stage, because "the material goes in ``<project>/uploads``" is
    the one rule every other lane follows and a take set had been the single exception: an
    operator who used ``--input`` or dropped the files on the canvas put them exactly where this
    function did not look.
    """
    roots = [takes_dir, *also]
    found: dict[str, list[Take]] = {b: [] for b in beat_ids}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.suffix.lower() not in AUDIO_SUFFIXES or not path.is_file():
                continue
            stem = path.name[: -len(path.suffix)]
            beat, _, speaker = stem.partition(".")
            if beat in found:
                found[beat].append(Take(beat, speaker or "voice", path))
    missing = [b for b in beat_ids if not found[b]]
    if missing:
        where = " or ".join(str(r) for r in roots)
        raise TakeError(
            f"no recording for {len(missing)} beat(s) in {where}: {', '.join(missing)} "
            f"(expected <beat_id>{AUDIO_SUFFIXES[0]})"
        )
    ambiguous = {b: [t.path.name for t in ts] for b, ts in found.items() if len(ts) > 1}
    if ambiguous:
        raise TakeError(f"more than one recording per beat: {ambiguous}")
    return {b: ts[0] for b, ts in found.items()}


def to_wav_mono16k(src: Path, dest: Path, *, sample_rate: int = 24000) -> None:
    """Normalise any recording to the mono PCM WAV the rest of the audio chain assumes."""
    from content_factory.audio.mix import ffmpeg

    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            "-map_metadata",
            "-1",
            str(dest),
        ],
        timeout=600,
    )


def wav_facts(path: Path) -> tuple[int, int, int]:
    """(sample_rate_hz, channels, duration_ms) of a PCM WAV."""
    with wave.open(str(path), "rb") as wf:
        frames, rate, channels = wf.getnframes(), wf.getframerate(), wf.getnchannels()
    if frames == 0 or rate == 0:
        raise TakeError(f"{path.name}: empty recording")
    return rate, channels, max(1, round(frames * 1000 / rate))


def even_split(words: Sequence[str], duration_ms: int) -> list[WordTiming]:
    """Apportion the measured duration by word length. Deterministic, offline, and honest about
    what it is: the segment is real audio, the per-word boundaries are estimates."""
    weights = [max(1, len(w)) for w in words]
    total = sum(weights)
    out: list[WordTiming] = []
    cursor = 0
    for i, (word, weight) in enumerate(zip(words, weights, strict=True)):
        end = duration_ms if i == len(words) - 1 else cursor + round(duration_ms * weight / total)
        end = min(max(end, cursor + 1), duration_ms)
        out.append(WordTiming(word=word, start_ms=cursor, end_ms=end))
        cursor = end
    return out


def snap_to_script(
    script_words: Sequence[str], measured: Sequence[tuple[float, float]], duration_ms: int
) -> list[WordTiming]:
    """Forced alignment gives measured spans for what was *heard*; the script says what the words
    *are*. Shared by the recorded-take path and by any TTS that returns no timings of its own.

    When the counts agree, each script word takes the matching span; when they do not, the script
    is apportioned across the measured speech span so the captions still land in the right place
    instead of failing the beat."""
    if len(measured) == len(script_words):
        out: list[WordTiming] = []
        prev_end = 0
        for word, (start_s, end_s) in zip(script_words, measured, strict=True):
            start = min(max(int(start_s * 1000), prev_end), duration_ms - 1)
            end = min(max(int(end_s * 1000), start + 1), duration_ms)
            out.append(WordTiming(word=word, start_ms=start, end_ms=end))
            prev_end = end
        return out
    speech_start = int(measured[0][0] * 1000) if measured else 0
    speech_end = int(measured[-1][1] * 1000) if measured else duration_ms
    span = max(1, min(speech_end, duration_ms) - speech_start)
    inner = even_split(script_words, span)
    return [
        WordTiming(
            word=w.word,
            start_ms=w.start_ms + speech_start,
            end_ms=min(w.end_ms + speech_start, duration_ms),
        )
        for w in inner
    ]


def faster_whisper_words(
    wav: Path, *, model: str, compute_type: str, timeout_s: int, language: str = ""
) -> tuple[list[str], list[tuple[float, float]]]:
    """Transcribe with word timestamps in an isolated environment (no torch in this process).

    faster-whisper 1.2.1 is the ADR-0004 fallback aligner and the one installed on this host; it
    runs CPU int8, so it costs no VRAM while the image models hold the card.

    ``language`` is the ISO subtag of what is being *spoken*, and it is told rather than detected:
    this is forced alignment against a script somebody already wrote, so the language is known and
    letting Whisper guess it from a 3-second beat only adds a way to be wrong. Empty keeps the
    detection, for a recording whose language nobody declared.
    """
    script = (
        "import json,sys\n"
        "from faster_whisper import WhisperModel\n"
        "m=WhisperModel(sys.argv[1], device='cpu', compute_type=sys.argv[2])\n"
        "segs,_=m.transcribe(sys.argv[3], word_timestamps=True, vad_filter=False,\n"
        "                    language=sys.argv[4] or None)\n"
        "w=[[x.word.strip(), x.start, x.end] for s in segs for x in (s.words or [])]\n"
        "json.dump(w, sys.stdout)\n"
    )
    proc = SUBPROCESS_RUN(
        [
            "uv",
            "run",
            "--with",
            "faster-whisper==1.2.1",
            "python",
            "-c",
            script,
            model,
            compute_type,
            str(wav),
            language,
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        raise TakeError(f"faster-whisper failed on {wav.name}: {proc.stderr.strip()[-400:]}")
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise TakeError(f"faster-whisper returned no JSON for {wav.name}: {exc}") from exc
    return [str(r[0]) for r in rows], [(float(r[1]), float(r[2])) for r in rows]


def segment_for_take(
    take: Take,
    wav: Path,
    *,
    display_text: str,
    spoken_text: str,
    aligner: str,
    faster_whisper_model: str = "base.en",
    compute_type: str = "int8",
    timeout_s: int = 600,
    script_similarity_min: float = 0.85,
    normalization_version: str,
) -> tuple[NarrationSegment, str]:
    """One recorded beat as a NarrationSegment, plus the take-review verdict that let it through."""
    rate, channels, duration_ms = wav_facts(wav)
    script_words = tokenize_words(spoken_text)
    if not script_words:
        raise TakeError(f"{take.beat_id}: the locked script for this beat has no words")

    verdict = "accepted_unheard"  # even_split never listens; nothing to compare with the script
    if aligner == "even_split":
        words = even_split(script_words, duration_ms)
        source = TimingSource.estimated
    else:
        if aligner == "whisperx":  # same contract, different environment; not installed here yet
            raise TakeError(
                "whisperx aligner is not installed on this host; use faster_whisper or even_split"
            )
        from content_factory.human_tasks.validation import validate_take

        heard, spans = faster_whisper_words(
            wav,
            model=faster_whisper_model,
            compute_type=compute_type,
            timeout_s=timeout_s,
        )
        review = validate_take(
            wav,
            spoken_text,
            transcriber=lambda _p: " ".join(heard),
            tolerance=script_similarity_min,
        )
        if review.verdict in ("request_rerecord", "reject_format"):
            raise TakeError(
                f"{take.path.name}: {review.verdict} — the take does not match the locked script "
                f"(similarity {review.similarity:.2f} < {script_similarity_min:.2f}). "
                f"diff: {' '.join(review.diff)[:200]}"
            )
        verdict = review.verdict
        words = snap_to_script(script_words, spans, duration_ms)
        source = TimingSource.forced_alignment

    segment = NarrationSegment(
        beat_id=take.beat_id,
        audio_sha256=sha256_hex(wav.read_bytes()),
        audio_format="wav",
        sample_rate_hz=rate,
        channels=channels,  # type: ignore[arg-type]
        duration_ms=duration_ms,
        words=tuple(words),
        timing_source=source,
        voice=VoiceIdentity(
            provider="human",
            voice_id=take.speaker[:80],
            model_revision=f"take:{take.path.name}"[:120],
        ),
        spoken_text=spoken_text,
        display_text=display_text,
        normalization_version=normalization_version,
    )
    return segment, verdict
