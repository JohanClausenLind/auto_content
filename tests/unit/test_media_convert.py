"""Converting what an operator has into what the pipeline reads.

Every fixture here is made by ffmpeg in the test rather than committed, because what is being
checked is a decision about real codecs: whether a file can be *copied* into MP4 or has to be
re-encoded, and that the result is actually readable afterwards. The MIME strings the decisions
key on are asserted too — they come from `python-magic` on this host, and a wrong one would make
a whole container silently unacceptable again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from content_factory.ingest.convert import (
    CONVERTIBLE,
    ConversionError,
    blank_picture,
    convert_media,
    needs_conversion,
)
from content_factory.ingest.uploads import ALLOWED, sniff_mime

FFMPEG = ("ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error")


def _run(*args: str) -> None:
    subprocess.run([*FFMPEG, *args], check=True, capture_output=True, timeout=120)


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Two seconds of picture and tone in the codecs a delivery MP4 is made of."""
    out = tmp_path_factory.mktemp("normalize") / "src.mp4"
    _run(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=15:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(out),
    )
    return out


def test_the_convertible_set_is_disjoint_from_what_is_already_accepted() -> None:
    """Conversion exists to accept what would otherwise be refused — never to re-encode a file
    the pipeline can already read, which would cost quality for nothing."""
    assert set(CONVERTIBLE) & set(ALLOWED) == set()
    assert needs_conversion("video/x-matroska") and not needs_conversion("video/mp4")
    # Everything convertible becomes something the allowlist accepts, or the conversion is a
    # dead end and the file gets refused after the work.
    from content_factory.ingest.convert import TARGET

    for kind in set(CONVERTIBLE.values()):
        assert TARGET[kind][1] in ALLOWED, kind


def test_a_matroska_recording_is_copied_into_mp4_without_re_encoding(
    source: Path, tmp_path: Path
) -> None:
    """The screen-recording case: OBS writes H.264/AAC in Matroska, so this is a container
    change. A re-encode here would cost minutes and quality for no reason."""
    mkv = tmp_path / "2026-09-09 13-35-29.mkv"
    _run("-i", str(source), "-c", "copy", str(mkv))
    assert sniff_mime(mkv) == "video/x-matroska"

    result = convert_media(mkv, sniff_mime(mkv), tmp_path / "out")

    assert result.action == "remux"
    assert result.kind == "video"
    assert sniff_mime(result.path) == "video/mp4"
    assert "no re-encode" in result.detail
    # Byte-for-byte the same picture: the video stream was copied, so its size barely moves.
    assert abs(result.path.stat().st_size - mkv.stat().st_size) < mkv.stat().st_size * 0.2
    assert result.path.name.endswith(".mp4")


def test_a_webm_is_re_encoded_because_its_codecs_cannot_be_delivered(
    source: Path, tmp_path: Path
) -> None:
    webm = tmp_path / "clip.webm"
    _run("-i", str(source), "-c:v", "libvpx-vp9", "-b:v", "150k", "-c:a", "libopus", str(webm))
    assert sniff_mime(webm) == "video/webm"

    result = convert_media(webm, sniff_mime(webm), tmp_path / "out")

    assert result.action == "transcode"
    assert "re-encoded to H.264/AAC" in result.detail and "vp9" in result.detail
    assert sniff_mime(result.path) == "video/mp4"


def test_a_video_with_a_subtitle_track_converts_and_says_what_was_dropped(
    source: Path, tmp_path: Path
) -> None:
    """MP4 cannot carry Matroska's subtitles, and a blind `-c copy` fails on them. Mapping only
    the first video and audio stream is the fix; saying so is the honest half of it."""
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n")
    mkv = tmp_path / "with-subs.mkv"
    _run("-i", str(source), "-i", str(srt), "-c", "copy", "-c:s", "srt", str(mkv))

    result = convert_media(mkv, sniff_mime(mkv), tmp_path / "out")

    assert result.action == "remux"
    assert "dropped" in result.detail and "subtitle" in result.detail


def test_a_silent_recording_keeps_working(source: Path, tmp_path: Path) -> None:
    mkv = tmp_path / "silent.mkv"
    _run("-i", str(source), "-an", "-c:v", "copy", str(mkv))
    result = convert_media(mkv, sniff_mime(mkv), tmp_path / "out")
    assert result.action == "remux" and "no audio" in result.detail
    assert sniff_mime(result.path) == "video/mp4"


@pytest.mark.parametrize(
    ("ext", "args", "expected_mime", "in_detail"),
    [
        ("m4a", ("-vn", "-c:a", "aac"), "audio/x-m4a", "from aac"),
        ("opus", ("-vn", "-c:a", "libopus"), "audio/ogg", "from opus"),
        ("aiff", ("-vn", "-c:a", "pcm_s16be"), "audio/x-aiff", "from pcm_s16be"),
    ],
)
def test_audio_containers_become_pcm_wav(
    source: Path,
    tmp_path: Path,
    ext: str,
    args: tuple[str, ...],
    expected_mime: str,
    in_detail: str,
) -> None:
    clip = tmp_path / f"take.{ext}"
    _run("-i", str(source), *args, str(clip))
    assert sniff_mime(clip) == expected_mime

    result = convert_media(clip, sniff_mime(clip), tmp_path / "out")

    assert result.kind == "audio"
    assert sniff_mime(result.path) in ("audio/x-wav", "audio/wav")
    assert in_detail in result.detail
    # The rate is the source's own: resampling would be a production decision, not an accept.
    assert "Hz" in result.detail


def test_an_audio_only_matroska_is_treated_as_audio(source: Path, tmp_path: Path) -> None:
    """A .mkv with no picture is an audio file wearing a video container, and the audio lanes
    are what should be offered for it."""
    mka = tmp_path / "voice.mkv"
    _run("-i", str(source), "-vn", "-c:a", "libopus", str(mka))
    result = convert_media(mka, sniff_mime(mka), tmp_path / "out")
    assert result.kind == "audio"
    assert sniff_mime(result.path) in ("audio/x-wav", "audio/wav")


def test_images_are_rewritten_as_png(source: Path, tmp_path: Path) -> None:
    for ext, mime in (("tiff", "image/tiff"), ("bmp", "image/bmp")):
        still = tmp_path / f"frame.{ext}"
        _run("-i", str(source), "-frames:v", "1", str(still))
        assert sniff_mime(still) == mime
        result = convert_media(still, mime, tmp_path / "out")
        assert result.kind == "image" and result.action == "rewrite"
        assert sniff_mime(result.path) == "image/png"


def test_a_file_that_is_not_media_fails_instead_of_producing_an_empty_mp4(tmp_path: Path) -> None:
    fake = tmp_path / "not-a-video.mkv"
    fake.write_bytes(b"\x1a\x45\xdf\xa3" + b"garbage" * 100)  # EBML magic, nothing behind it
    with pytest.raises(ConversionError):
        convert_media(fake, "video/x-matroska", tmp_path / "out")
    # Nothing half-written is left where a caller would find and store it.
    assert not any((tmp_path / "out").glob("*.mp4")) if (tmp_path / "out").is_dir() else True


def test_an_unknown_mime_is_left_for_the_allowlist_to_refuse(tmp_path: Path) -> None:
    path = tmp_path / "thing.bin"
    path.write_bytes(b"\x00\x01")
    result = convert_media(path, "application/octet-stream", tmp_path / "out")
    assert result.action == "none" and result.path == path


# --- a recording wearing a video container -----------------------------------------------------


def _blank(path: Path, *, seconds: float = 3.0, colour: str = "black", tone: bool = True) -> Path:
    """A recording as a phone or a meeting tool writes it: sound, and a picture of nothing."""
    args = [
        "-f",
        "lavfi",
        "-i",
        f"color=c={colour}:s=320x240:r=25:d={seconds}",
        *(["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"] if tone else []),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        *(["-c:a", "aac", "-shortest"] if tone else ["-an"]),
        str(path),
    ]
    _run(*args)
    return path


def test_an_mp4_with_no_video_stream_at_all_is_a_recording(tmp_path: Path) -> None:
    """The commonest shape of the problem, and the one the container settles on its own: a
    voice-memo app writes AAC into MP4, `magic` reads the ftyp brand and says `video/mp4`, and
    there has never been a picture in it."""
    out = tmp_path / "memo.mp4"
    _run("-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(out))
    assert sniff_mime(out) == "video/mp4", "the bytes say video whatever is inside"

    verdict = blank_picture(out)

    assert verdict is not None
    assert verdict.certainty == "exact"
    assert "no video stream" in verdict.reason


def test_an_mp4_whose_picture_is_black_the_whole_way_is_a_recording(tmp_path: Path) -> None:
    verdict = blank_picture(_blank(tmp_path / "interview.mp4"))

    assert verdict is not None
    assert verdict.certainty == "sampled", "pixels had to be looked at, and the word says so"
    assert "black" in verdict.reason and "12 frames" in verdict.reason


def test_an_mp4_that_holds_one_unchanging_cover_is_a_recording(tmp_path: Path) -> None:
    """The other half of the same problem: a podcast tool renders the show's artwork over the
    audio, so the picture is not black — it is simply not a film."""
    verdict = blank_picture(_blank(tmp_path / "episode.mp4", colour="0x203040"))

    assert verdict is not None and verdict.certainty == "sampled"
    assert "never changes" in verdict.reason


def test_a_film_is_not_a_recording_however_much_the_operator_wants_one(source: Path) -> None:
    """The check has to be able to say no, or it is not a check: `--input holiday.mp4` to an
    audio lane is a mistake, and the lane that takes video is one command away."""
    assert blank_picture(source) is None


def test_a_black_video_with_no_sound_is_not_a_recording(tmp_path: Path) -> None:
    """Blankness alone is not the question. The question is whether the sound can stand on its
    own, and silence cannot — so this stays a video and is refused as one."""
    assert blank_picture(_blank(tmp_path / "dead.mp4", tone=False)) is None


def test_a_file_ffprobe_cannot_read_is_not_a_recording(tmp_path: Path) -> None:
    """Total, like every other measurement here: an unreadable file is refused by the allowlist
    that already looked at it, not by an exception out of a heuristic."""
    junk = tmp_path / "broken.mp4"
    junk.write_bytes(b"\x00" * 4096)
    assert blank_picture(junk) is None


def test_looking_at_a_long_recording_costs_a_bounded_number_of_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdict is reached from a fixed sample however long the file is. That is the whole
    reason this can run in front of an operator waiting for a run to start."""
    from content_factory.ingest import convert

    looked_at: list[float] = []
    real = convert._grey_frame

    def counting(path: Path, at_s: float) -> bytes | None:
        looked_at.append(at_s)
        return real(path, at_s)

    monkeypatch.setattr(convert, "_grey_frame", counting)
    assert blank_picture(_blank(tmp_path / "long.mp4", seconds=30)) is not None
    assert len(looked_at) == convert.BLANK_PICTURE_SAMPLES
    assert max(looked_at) > 25, "the samples span the file rather than crowding its opening"
