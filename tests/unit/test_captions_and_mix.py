"""Phase-3: caption compilation (SRT/WebVTT), stem layout, mastering to -14 LUFS, mux QC."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from content_factory.audio.captions import compile_captions, to_srt, to_webvtt
from content_factory.audio.mix import (
    apply_measurements,
    build_narration_stem,
    lay_out,
    master,
    measure_loudness,
)
from content_factory.audio.normalize import normalize_for_speech
from content_factory.audio.tts import MockTTS
from content_factory.schemas.audio import (
    AudioMixSpec,
    MasterChainSpec,
    NarrationRequest,
    VoiceIdentity,
)
from content_factory.schemas.fixtures import sample_story_plan
from content_factory.timeline.compiler import compile_timeline

VOICE = VoiceIdentity(provider="mock", voice_id="narrator-a", model_revision="mock-1")
SPEC = AudioMixSpec(deliverable_id="dlv_short0000001")


def _segments(tmp_path: Path):
    plan = sample_story_plan()
    segs, files = [], {}
    for b in plan.beats:
        r = MockTTS().synthesize(
            NarrationRequest(
                beat_id=b.beat_id,
                display_text=b.display_text,
                spoken_text=normalize_for_speech(b.display_text),
                voice=VOICE,
            )
        )
        segs.append(r.segment)
        f = tmp_path / f"{b.beat_id}.wav"
        f.write_bytes(r.audio)
        files[b.beat_id] = f
    return plan, segs, files


def test_layout_measurements_drive_narrated_timeline(tmp_path: Path) -> None:
    plan, segs, _ = _segments(tmp_path)
    laid = lay_out(segs, SPEC)
    assert laid[0].start_ms == SPEC.lead_in_ms
    for prev, nxt in pairwise(laid):
        assert nxt.start_ms == prev.end_ms + SPEC.inter_beat_pause_ms
    beats = apply_measurements(plan.beats, laid)
    assert all(b.measured_start_ms is not None and b.words for b in beats)
    tl = compile_timeline(
        plan.model_copy(update={"beats": beats}), timeline_id="tl_narr00000001", narrated=True
    )
    assert 14 * 30 <= tl.total_frames <= 30 * 30
    assert len(tl.audio) == len(beats)
    # Scene cuts land exactly on the next beat's measured speech start.
    for cue, scene in zip(tl.audio, tl.scenes, strict=True):
        assert cue.start_frame == scene.start_frame


def test_captions_srt_and_vtt_format(tmp_path: Path) -> None:
    _plan, segs, _ = _segments(tmp_path)
    laid = lay_out(segs, SPEC)
    words = [w for b in laid for w in b.words]
    track = compile_captions("dlv_short0000001", words)
    assert all(len(line) <= track.max_chars_per_line + 12 for c in track.cues for line in c.lines)
    assert all(len(c.lines) <= 2 for c in track.cues)
    srt = to_srt(track)
    vtt = to_webvtt(track)
    assert srt.startswith("1\n00:00:00,") and " --> " in srt
    assert vtt.startswith("WEBVTT\n\n00:00:00.")
    for a, b in zip(track.cues, track.cues[1:], strict=False):
        assert b.start_ms >= a.end_ms


def test_stem_master_and_loudness(tmp_path: Path) -> None:
    _plan, segs, files = _segments(tmp_path)
    stem = tmp_path / "stem.wav"
    total_ms = build_narration_stem(segs, files, SPEC, stem)
    laid = lay_out(segs, SPEC)
    assert total_ms == laid[-1].end_ms + SPEC.tail_ms
    mastered = tmp_path / "mastered.wav"
    report = master(
        stem,
        mastered,
        MasterChainSpec(
            target_lufs=SPEC.target_lufs, target_true_peak_dbtp=SPEC.target_true_peak_dbtp
        ),
    )
    assert report.passed, (report.integrated_lufs, report.true_peak_dbtp)
    raw = measure_loudness(stem)
    assert (
        abs(raw.integrated_lufs - report.integrated_lufs) > 0.5
    )  # mastering actually changed level


def _silent_clip(path: Path, *, seconds: float) -> Path:
    """A tiny H.264 clip of the given length, made the way the post chain makes one."""
    from content_factory.audio.mix import ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=64x48:rate=24:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(path),
        ]
    )
    return path


def _tone(path: Path, *, seconds: float) -> Path:
    from content_factory.audio.mix import ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={seconds}",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )
    return path


def test_the_mux_holds_the_last_frame_rather_than_cutting_the_narration(tmp_path: Path) -> None:
    """`-shortest` made a film end when its picture did, and the mix always ends later than the
    beats: it lays the narration out with a lead-in, a pause between beats and a tail. So a lane
    whose picture is exactly as long as its beats lost its ending to a flag — measured on a real
    run as "audio shorter than the narration it should carry", raised by the composer's own QC.
    """
    from content_factory.audio.mix import _media_ms, mux

    picture = _silent_clip(tmp_path / "picture.mp4", seconds=2.0)
    audio = _tone(tmp_path / "audio.wav", seconds=3.4)
    out = tmp_path / "final.mp4"
    # 3.0 s is where the last word ends; the 0.4 s after it is the stem's tail of silence.
    mux(picture, audio, out, min_video_ms=3000)
    # Every word is there, and the picture was extended to carry them.
    assert _media_ms(out) >= 3000 - 60
    from content_factory.qc.media import ffprobe

    streams = {s["codec_type"]: s for s in ffprobe(out)["streams"]}
    assert streams["video"]["codec_name"] == "h264"
    assert streams["audio"]["codec_name"] == "aac"
    assert int(streams["video"]["width"]) == 64


def test_a_picture_that_already_carries_the_words_is_copied_not_re_encoded(tmp_path: Path) -> None:
    """The old path, kept, and it is what every timeline lane takes: the compiler sizes the picture
    to the speech, so what -shortest cuts there is the stem's trailing silence. Holding costs a
    re-encode and there is nothing to save."""
    from content_factory.audio.mix import _media_ms, mux
    from content_factory.qc.media import ffprobe

    picture = _silent_clip(tmp_path / "picture.mp4", seconds=3.0)
    audio = _tone(tmp_path / "audio.wav", seconds=3.4)
    out = tmp_path / "final.mp4"
    mux(picture, audio, out, min_video_ms=2600)
    # -shortest wins: the film is the length of its picture, and nothing was re-encoded.
    assert _media_ms(out) <= _media_ms(picture) + 60
    source = {s["codec_type"]: s for s in ffprobe(picture)["streams"]}["video"]
    muxed = {s["codec_type"]: s for s in ffprobe(out)["streams"]}["video"]
    assert muxed["profile"] == source["profile"]
