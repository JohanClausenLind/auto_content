"""Converting what an operator actually has into what the pipeline actually reads.

The upload allowlist is deliberately narrow — `mp4`, `mov`, `wav`, `mp3`, `flac`, `png`, `jpg`,
`webp` — because every stage downstream assumes those. That narrowness was also a wall: an OBS
screen recording is a Matroska file, so dropping one on the canvas answered *"file type
'video/x-matroska' is not accepted"*, and the operator's next move was a terminal.

This module is the missing step between those two facts. A handful of extra containers are
accepted **on the condition that they are converted first**, into the formats the pipeline
already delivers in: H.264/AAC in MP4 for picture, PCM WAV for sound, PNG for stills.

Three things make it cheap and safe:

* **Remux before re-encode.** A screen recording is usually already H.264 with AAC audio, so the
  streams are copied into an MP4 container — seconds, and not one pixel re-encoded. Only a file
  whose codecs cannot be delivered (VP9, Opus, MPEG-4 Part 2) is actually transcoded.
* **Only the first video and audio stream are mapped.** Matroska carries subtitles, chapters and
  font attachments that MP4 cannot hold, and a blind ``-c copy`` fails on them. Dropping them is
  a real loss of information, so it is reported rather than silent.
* **The type still comes from the bytes.** Nothing here decides what a file is: the caller has
  already sniffed it through :mod:`content_factory.ingest.uploads`, and only the MIMEs in
  :data:`CONVERTIBLE` — each verified against ``python-magic`` and against this host's ffmpeg —
  ever reach a converter. An extension is never consulted.

Not accepted, on purpose: MPEG-TS, whose bytes ``python-magic`` reports as
``application/octet-stream`` on this host. Accepting that MIME would accept every unidentifiable
file in exchange for one container.

The module also answers a second question, about files the allowlist already accepts:
:func:`blank_picture` — *is this MP4 a film, or a recording with nothing to look at?* A phone, a
meeting recorder and a screen recorder pointed at a blanked display all write MP4, and the bytes
say ``video/mp4`` whether the picture is an interview or an hour of black. Only the picture can
tell those apart, so it is measured rather than assumed.
"""

from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from content_factory.qc.media import ffprobe

Kind = Literal["video", "audio", "image"]
Action = Literal["none", "remux", "transcode", "rewrite"]

# Sniffed MIME -> what it is. Accepted only because the converter below can turn each into the
# canonical format for its kind; the MIME strings were read off `magic.from_file(mime=True)` on
# this host (2026-09-09) rather than assumed.
CONVERTIBLE: dict[str, Kind] = {
    "video/x-matroska": "video",  # .mkv — OBS, ffmpeg's own default for screen capture
    "video/webm": "video",
    "video/x-msvideo": "video",  # .avi
    "audio/x-m4a": "audio",
    "audio/ogg": "audio",  # ogg and opus both sniff to this
    "audio/x-aiff": "audio",
    "image/tiff": "image",
    "image/bmp": "image",
}

# What each kind becomes. These are the formats the rest of the repo already assumes: H.264 in
# MP4 with faststart is what media QC probes and what every destination package carries, PCM WAV
# is what the audio chain reads, PNG is what the renderer and the sequence engine write.
TARGET: dict[Kind, tuple[str, str]] = {
    "video": ("mp4", "video/mp4"),
    "audio": ("wav", "audio/x-wav"),
    "image": ("png", "image/png"),
}

# Codecs that can be copied into MP4 as they are. Anything else is re-encoded to them.
COPYABLE_VIDEO = frozenset({"h264"})
COPYABLE_AUDIO = frozenset({"aac"})
COPYABLE_PIX_FMT = frozenset({"yuv420p", "yuvj420p"})


class ConversionError(Exception):
    pass


@dataclass(frozen=True)
class Converted:
    """The file the caller should go on to store, and what was done to get it."""

    path: Path
    kind: Kind
    action: Action
    from_mime: str
    to_mime: str
    detail: str
    """One line for the operator: what happened, and what was dropped if anything."""

    @property
    def converted(self) -> bool:
        return self.action != "none"


def needs_conversion(sniffed_mime: str) -> bool:
    return sniffed_mime in CONVERTIBLE


def _timeout_for(duration_s: float, *, copying: bool) -> int:
    """Enough for the work, not unbounded. A copy is I/O; an encode is roughly realtime on CPU."""
    if copying:
        return max(60, int(duration_s / 4) + 60)
    return min(3600, max(180, int(duration_s * 4) + 120))


def _probe(path: Path) -> tuple[dict | None, dict | None, float]:
    """(video stream, audio stream, duration seconds). Raises when nothing is readable."""
    try:
        data = ffprobe(path)
    except Exception as err:  # ffprobe exits non-zero on a file it cannot demux
        raise ConversionError(f"unreadable media: {err}") from err
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise ConversionError("no audio or video stream in the file")
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return video, audio, duration


def _dropped_streams(path: Path) -> list[str]:
    """Streams MP4 cannot carry, named so their loss is stated rather than discovered."""
    try:
        data = ffprobe(path)
    except Exception:
        return []
    kinds: dict[str, int] = {}
    for stream in data.get("streams") or []:
        kind = str(stream.get("codec_type") or "?")
        if kind in ("video", "audio"):
            continue
        kinds[kind] = kinds.get(kind, 0) + 1
    extra_video = sum(1 for s in data.get("streams") or [] if s.get("codec_type") == "video") - 1
    extra_audio = sum(1 for s in data.get("streams") or [] if s.get("codec_type") == "audio") - 1
    out = [f"{count} {kind}" for kind, count in sorted(kinds.items())]
    if extra_video > 0:
        out.append(f"{extra_video} extra video")
    if extra_audio > 0:
        out.append(f"{extra_audio} extra audio")
    return out


def _run(args: list[str], *, timeout: int) -> None:
    # One ffmpeg entry point for the repo: already an argument array, already `-nostdin -y`, and
    # already raising with the tail of stderr, which is the only useful part of an ffmpeg failure.
    from content_factory.audio.mix import ffmpeg

    try:
        ffmpeg(args, timeout=timeout)
    except Exception as err:
        raise ConversionError(str(err)) from err


def _convert_video(path: Path, dest: Path, from_mime: str) -> Converted:
    video, audio, duration = _probe(path)
    if video is None:
        # A .mkv or .webm carrying only sound is an audio file wearing a video container.
        return _convert_audio(path, dest.with_suffix(".wav"), from_mime)
    copyable = (
        str(video.get("codec_name")) in COPYABLE_VIDEO
        and str(video.get("pix_fmt")) in COPYABLE_PIX_FMT
        and (audio is None or str(audio.get("codec_name")) in COPYABLE_AUDIO)
    )
    # Map the first video and (optionally) the first audio stream only: MP4 cannot hold the
    # subtitles, chapters and attachments Matroska carries, and `-c copy` over them fails.
    args = ["-i", str(path), "-map", "0:v:0", "-map", "0:a:0?"]
    if copyable:
        args += ["-c", "copy"]
    else:
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    args += ["-movflags", "+faststart", str(dest)]
    _run(args, timeout=_timeout_for(duration, copying=copyable))
    _verify(dest, expect="video")

    dropped = _dropped_streams(path)
    if copyable:
        detail = (
            f"{video.get('codec_name')}"
            f"{'/' + str(audio.get('codec_name')) if audio else ' (no audio)'}"
            " copied into MP4 — no re-encode"
        )
    else:
        detail = (
            f"re-encoded to H.264/AAC from {video.get('codec_name')}"
            f"{'/' + str(audio.get('codec_name')) if audio else ' (no audio)'}"
        )
    if dropped:
        detail += f"; dropped {', '.join(dropped)} (MP4 cannot carry them)"
    return Converted(
        path=dest,
        kind="video",
        action="remux" if copyable else "transcode",
        from_mime=from_mime,
        to_mime=TARGET["video"][1],
        detail=detail,
    )


def _convert_audio(path: Path, dest: Path, from_mime: str) -> Converted:
    _video, audio, duration = _probe(path)
    if audio is None:
        raise ConversionError("no audio stream to convert")
    # PCM at the source's own rate and channel count: a conversion that resampled would be making
    # a production decision (the mix has its own target) rather than accepting a file.
    _run(
        ["-i", str(path), "-map", "0:a:0", "-c:a", "pcm_s16le", str(dest)],
        timeout=_timeout_for(duration, copying=False),
    )
    _verify(dest, expect="audio")
    rate = audio.get("sample_rate")
    channels = audio.get("channels")
    return Converted(
        path=dest,
        kind="audio",
        action="transcode",
        from_mime=from_mime,
        to_mime=TARGET["audio"][1],
        detail=(
            f"decoded from {audio.get('codec_name')} to 16-bit PCM WAV"
            f"{f' at {rate} Hz' if rate else ''}"
            f"{', mono' if channels == 1 else f', {channels} channels' if channels else ''}"
        ),
    )


def _convert_image(path: Path, dest: Path, from_mime: str) -> Converted:
    # Pillow rather than ffmpeg: it is already a dependency, it is what the renderer and QC use
    # for stills, and it keeps one more untrusted-media parser out of the picture path.
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as img:
            mode = "RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB"
            frames = getattr(img, "n_frames", 1)
            img.convert(mode).save(dest, format="PNG")
    except (OSError, UnidentifiedImageError, ValueError) as err:
        raise ConversionError(f"could not read the image: {err}") from err
    _verify(dest, expect="image")
    detail = f"converted to PNG ({mode})"
    if frames > 1:
        detail += f"; kept the first of {frames} pages"
    return Converted(
        path=dest,
        kind="image",
        action="rewrite",
        from_mime=from_mime,
        to_mime=TARGET["image"][1],
        detail=detail,
    )


def _verify(path: Path, *, expect: Kind) -> None:
    """A converter that exits 0 and writes nothing usable has still failed."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ConversionError("the conversion produced no file")
    if expect == "image":
        from PIL import Image

        try:
            with Image.open(path) as img:
                img.verify()
        except Exception as err:
            raise ConversionError(f"the converted image is unreadable: {err}") from err
        return
    video, audio, _ = _probe(path)
    if expect == "video" and video is None:
        raise ConversionError("the converted file has no video stream")
    if expect == "audio" and audio is None:
        raise ConversionError("the converted file has no audio stream")


def convert_media(path: Path, sniffed_mime: str, out_dir: Path) -> Converted:
    """Convert ``path`` into the canonical format for its kind, or return it untouched.

    ``sniffed_mime`` must be what ``ingest.uploads`` sniffed from the bytes. A MIME that is
    neither already accepted nor in :data:`CONVERTIBLE` is not this module's business: it returns
    the file unchanged and lets the allowlist refuse it.
    """
    kind = CONVERTIBLE.get(sniffed_mime)
    if kind is None:
        return Converted(
            path=path,
            kind="video",  # unused: nothing was done and the caller re-sniffs
            action="none",
            from_mime=sniffed_mime,
            to_mime=sniffed_mime,
            detail="",
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = TARGET[kind][0]
    dest = out_dir / f"{path.stem or 'converted'}.{ext}"
    if dest.resolve() == path.resolve():
        dest = out_dir / f"{path.stem}-converted.{ext}"
    if kind == "video":
        return _convert_video(path, dest, sniffed_mime)
    if kind == "audio":
        return _convert_audio(path, dest, sniffed_mime)
    return _convert_image(path, dest, sniffed_mime)


# --- a recording wearing a video container -----------------------------------------------------

BLANK_PICTURE_SAMPLES = 12
"""How many frames are looked at, spread evenly across the file.

Bounded on purpose. This runs before a lane starts, in front of an operator waiting for an error
or a run, so a verdict about a three-hour file may not cost three hours: each sample is an input
seek and one decoded frame, so twelve of them is well under a second whatever the duration. A
full ``blackdetect`` pass over thirty minutes of 1080p black measured 14 s on this host, and the
same pass over thirty minutes of *picture* would be minutes — the wrong cost to pay for a check
whose usual answer is "no, that is a film".
"""

BLACK_LUMA_MAX = 24
"""At or below this, a pixel is black. Limited-range black is 16 and a codec puts a few levels of
noise around it, so the bar sits above 16 rather than at it."""

STILL_LUMA_DELTA_MAX = 3
"""How far two samples may differ, per pixel, and still be the same picture: an unchanging image
re-encoded moves a level or two between keyframes."""

SAMPLE_EDGE = 32
"""Frames are compared as 32x32 greyscale thumbnails. Small enough that the comparison is free,
large enough that a caption, a face or a moving cursor survives the shrink."""


@dataclass(frozen=True)
class BlankPicture:
    """Why a file with a video track is really a recording, in the operator's terms."""

    reason: str
    certainty: Literal["exact", "sampled"]
    """``exact`` when the container itself settles it — no video stream, cover art, a single
    frame. ``sampled`` when it took looking at the pixels, which is a measurement of
    :data:`BLANK_PICTURE_SAMPLES` frames rather than of every frame, and says so."""


def _span(seconds: float) -> str:
    if seconds >= 60:
        return f"{int(seconds) // 60} min {int(seconds) % 60:02d} s"
    return f"{seconds:.1f} s"


def _grey_frame(path: Path, at_s: float) -> bytes | None:
    """One frame, seeked to and shrunk to a greyscale thumbnail. ``None`` if it cannot be had.

    Input seeking (``-ss`` *before* ``-i``) so the cost is a seek and a GOP rather than a decode
    from the start of the file. PNG through Pillow rather than a raw pipe because ``ffmpeg()`` —
    the repo's one entry point — reads its output as text, which would mangle raw bytes; the same
    trade ``qc.delivery`` makes for the same reason.
    """
    from PIL import Image, UnidentifiedImageError

    proc = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{max(0.0, at_s):.3f}",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            f"scale={SAMPLE_EDGE}:{SAMPLE_EDGE}:flags=bilinear,format=gray",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        with Image.open(io.BytesIO(proc.stdout)) as img:
            return img.convert("L").tobytes()
    except (OSError, UnidentifiedImageError):
        return None


def blank_picture(path: Path) -> BlankPicture | None:
    """Why this video file is really a recording — or ``None`` when it carries a picture.

    An MP4 is the container a phone, a meeting recorder and a screen recorder with a blanked
    display all reach for, so "the file is video/mp4" answers nothing about whether there is
    anything to look at. This is the measurement that does answer it, and it is deliberately hard
    to satisfy: a file qualifies only when it has sound *and* its picture is either absent, one
    attached still, or unchanging — black or otherwise — everywhere it was sampled.

    A file with no audio is never blank-pictured here even if every frame is black: the whole
    point of the question is "can the sound stand on its own", and silence cannot.

    The verdict is a reason rather than a boolean because every caller has to say it out loud. An
    operator whose hour of interview was taken as a recording, or refused as a film, is owed the
    measurement that decided it.
    """
    try:
        video, audio, duration = _probe(path)
    except ConversionError:
        return None
    if audio is None:
        return None
    if video is None:
        return BlankPicture("it has no video stream at all", "exact")
    if int((video.get("disposition") or {}).get("attached_pic") or 0):
        return BlankPicture("its only picture is attached cover art", "exact")
    if int(video.get("nb_frames") or 0) == 1:
        return BlankPicture("its picture is a single frame held for the whole file", "exact")

    times = (
        [duration * (i + 0.5) / BLANK_PICTURE_SAMPLES for i in range(BLANK_PICTURE_SAMPLES)]
        if duration > 0
        else [0.0]
    )
    first: bytes | None = None
    black = still = True
    seen = 0
    for at_s in times:
        frame = _grey_frame(path, at_s)
        if frame is None:
            continue
        seen += 1
        if first is None:
            first = frame
        black = black and max(frame) <= BLACK_LUMA_MAX
        still = (
            still
            and max(abs(a - b) for a, b in zip(frame, first, strict=True)) <= STILL_LUMA_DELTA_MAX
        )
        if not black and not still:
            return None
    # One frame makes "it never changes" true by having nothing to change against, which is not a
    # measurement. Two is the least that can disagree.
    if seen < 2 or not (black or still):
        return None
    where = f"{seen} frames sampled across {_span(duration)}"
    if black:
        return BlankPicture(f"its picture is black in all {where}", "sampled")
    return BlankPicture(
        f"its picture never changes across {where} — a cover, not a film", "sampled"
    )
