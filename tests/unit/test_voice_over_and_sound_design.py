"""Recorded human takes (voice_over) and video-synced SFX (sound_design).

Both stages stand in for a model the core suite must not run: the aligner and MMAudio are behind
seams, and what is tested here is the contract around them — the same NarrationSegment a TTS
produces, a take that says the wrong thing being refused, and the SFX bed reaching the mix.
"""

from __future__ import annotations

import json
import math
import struct
import subprocess
import wave
from itertools import pairwise
from pathlib import Path

import pytest

from content_factory.audio import sfx as sfx_mod
from content_factory.audio import takes as takes_mod
from content_factory.audio.alignment import validate_alignment
from content_factory.audio.normalize import NORMALIZATION_VERSION, normalize_for_speech
from content_factory.audio.takes import (
    Take,
    TakeError,
    discover_takes,
    even_split,
    segment_for_take,
)
from content_factory.runners.local import make_context
from content_factory.schemas.audio import TimingSource
from content_factory.schemas.fixtures import sample_story_plan
from content_factory.workflows.stages import stage_sound_design, stage_voice_over

LINE = "Hey. You are so beautiful."


def _wav(path: Path, *, seconds: float = 2.0, rate: int = 24000) -> Path:
    """A stand-in recording at a normal speaking level — the two-pass loudnorm master cannot lift
    near-silence to -14 LUFS, and neither could a real take recorded that quietly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(rate * seconds)
    samples = [int(9000 * math.sin(2 * math.pi * 180 * i / rate)) for i in range(n)]
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{n}h", *samples))
    return path


def _mp4(path: Path, *, seconds: int = 3) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x96:rate=24",
            "-t",
            str(seconds),
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


# --- takes ------------------------------------------------------------------------------------


def test_discover_takes_accepts_speaker_suffixes_and_names_what_is_missing(tmp_path: Path) -> None:
    _wav(tmp_path / "bea_one00000001.wav")
    _wav(tmp_path / "bea_two00000001.johan.wav")
    found = discover_takes(tmp_path, ["bea_one00000001", "bea_two00000001"])
    assert found["bea_one00000001"].speaker == "voice"
    assert found["bea_two00000001"].speaker == "johan"

    with pytest.raises(TakeError, match="bea_three000001"):
        discover_takes(tmp_path, ["bea_one00000001", "bea_three000001"])


def test_two_recordings_for_one_beat_is_an_error_not_a_coin_flip(tmp_path: Path) -> None:
    _wav(tmp_path / "bea_one00000001.a.wav")
    _wav(tmp_path / "bea_one00000001.b.wav")
    with pytest.raises(TakeError, match="more than one recording"):
        discover_takes(tmp_path, ["bea_one00000001"])


def test_even_split_covers_the_whole_take_without_overlaps() -> None:
    words = even_split(["hey", "you", "are", "so", "beautiful"], 3000)
    assert words[0].start_ms == 0
    assert words[-1].end_ms == 3000
    for a, b in pairwise(words):
        assert a.end_ms == b.start_ms
        assert a.end_ms > a.start_ms


def test_even_split_segment_is_real_audio_with_estimated_timings(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "take.wav", seconds=2.0)
    take = Take("bea_one00000001", "johan", wav)
    segment, verdict = segment_for_take(
        take,
        wav,
        display_text=LINE,
        spoken_text=normalize_for_speech(LINE),
        aligner="even_split",
        normalization_version=NORMALIZATION_VERSION,
    )
    assert segment.timing_source is TimingSource.estimated
    assert segment.voice.provider == "human"
    assert segment.voice.voice_id == "johan"
    assert segment.duration_ms == pytest.approx(2000, abs=5)
    assert verdict == "accepted_unheard"
    # The same validator the synthesized path runs must pass on a recorded one.
    assert validate_alignment(segment).passed


def test_forced_alignment_keeps_the_script_words_and_the_measured_spans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav = _wav(tmp_path / "take.wav", seconds=2.0)
    heard = [
        ["Hey", 0.10, 0.45],
        ["you", 0.50, 0.70],
        ["are", 0.72, 0.90],
        ["so", 0.95, 1.10],
        ["beautiful", 1.15, 1.80],
    ]

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(heard), stderr="")

    monkeypatch.setattr(takes_mod, "SUBPROCESS_RUN", fake_run)
    segment, verdict = segment_for_take(
        Take("bea_one00000001", "johan", wav),
        wav,
        display_text=LINE,
        spoken_text=normalize_for_speech(LINE),
        aligner="faster_whisper",
        normalization_version=NORMALIZATION_VERSION,
    )
    assert segment.timing_source is TimingSource.forced_alignment
    assert verdict == "accept"
    assert [w.word for w in segment.words] == ["Hey", "You", "are", "so", "beautiful"]
    assert segment.words[0].start_ms == 100
    assert segment.words[-1].end_ms == 1800


def test_a_take_that_says_something_else_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wav = _wav(tmp_path / "take.wav", seconds=2.0)
    wrong = [[w, i * 0.3, i * 0.3 + 0.25] for i, w in enumerate("the wind share doubled".split())]

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(wrong), stderr="")

    monkeypatch.setattr(takes_mod, "SUBPROCESS_RUN", fake_run)
    with pytest.raises(TakeError, match="request_rerecord"):
        segment_for_take(
            Take("bea_one00000001", "johan", wav),
            wav,
            display_text=LINE,
            spoken_text=normalize_for_speech(LINE),
            aligner="faster_whisper",
            normalization_version=NORMALIZATION_VERSION,
        )


# --- the stage --------------------------------------------------------------------------------


def test_voice_over_stage_produces_the_segments_the_audio_branch_expects(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    plan = sample_story_plan()
    for beat in plan.beats:
        _wav(tmp_path / "takes" / f"{beat.beat_id}.johan.wav", seconds=3.0)
    out = stage_voice_over(ctx)
    assert out.facts["segments"] == len(plan.beats)
    assert out.facts["aligned"] == len(plan.beats)
    assert out.facts["speakers"] == ["johan"]
    for beat in plan.beats:
        assert (ctx.ddir() / "audio" / f"{beat.beat_id}.wav").exists()
        assert (ctx.ddir() / "audio" / f"{beat.beat_id}.segment.json").exists()

    # Re-running an unchanged take never re-aligns it.
    again = stage_voice_over(ctx)
    assert again.facts["aligned"] == 0
    assert again.outputs_hash == out.outputs_hash


def test_voice_over_names_the_beat_whose_take_is_missing(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    beats = sample_story_plan().beats
    for beat in beats[:-1]:
        _wav(tmp_path / "takes" / f"{beat.beat_id}.wav")
    with pytest.raises(RuntimeError, match=beats[-1].beat_id):
        stage_voice_over(ctx)


def test_sound_design_scores_the_silent_cut_and_leaves_a_bed_for_the_mix(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    exports = ctx.ddir() / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    _mp4(exports / "final.mp4", seconds=3)
    out = stage_sound_design(ctx)
    assert out.facts["backend"] == "mock"
    assert out.facts["scored"] == "final.mp4"
    sfx = ctx.ddir() / "audio" / "sfx.wav"
    assert sfx.exists()
    with wave.open(str(sfx)) as wf:
        assert wf.getnframes() / wf.getframerate() == pytest.approx(3.0, abs=0.2)
    # Conditioning is on by default, so the bed arrives at a known loudness and the gain the mix
    # applies is a trim, not the blind -22 dB cut a raw model output needs.
    assert json.loads((ctx.ddir() / "audio" / "sfx.json").read_text())["gain_db"] == 0.0
    assert out.facts["bed_lufs"] == pytest.approx(-23.0, abs=1.0)
    assert stage_sound_design(ctx).facts["cache_hit"] is True


def test_sound_design_falls_back_to_the_blind_cut_when_conditioning_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `condition=false` the bed is at whatever level the model rendered, so it still needs
    the large attenuation to sit under speech — and nothing is measured or normalised."""
    from content_factory.config import get_settings

    monkeypatch.setenv("CF__SOUND_DESIGN__CONDITION", "false")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        ctx = make_context(project_dir=tmp_path)
        exports = ctx.ddir() / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        _mp4(exports / "final.mp4", seconds=3)
        out = stage_sound_design(ctx)
        assert out.facts["gain_db"] == -22.0
        assert "conditioned" not in out.facts
        assert not (ctx.ddir() / "audio" / "sfx-condition.json").exists()
        assert not (ctx.ddir() / "audio" / "sfx.raw.wav").exists()
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_sound_design_says_what_is_missing_when_there_is_no_picture(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    with pytest.raises(RuntimeError, match="no picture yet"):
        stage_sound_design(ctx)


def test_mmaudio_backend_windows_a_long_clip_and_calls_its_own_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MMAudio generates eight seconds at a time; a 20-second film gets three windows, each with
    its own seed, and each run goes through the checkout's interpreter — never this process."""
    repo = tmp_path / "MMAudio"
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out_dir = Path(cmd[cmd.index("--output") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(cmd[cmd.index("--video") + 1]).stem
        # MMAudio writes flac; the backend transcodes, so a real (tiny) file is needed here.
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "1",
                str(out_dir / f"{stem}.flac"),
            ],
            check=True,
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sfx_mod, "SUBPROCESS_RUN", fake_run)
    backend = sfx_mod.MMAudioBackend(repo_dir=repo)
    video = _mp4(tmp_path / "picture.mp4", seconds=20)
    facts = backend.generate(
        sfx_mod.SfxRequest(video=video, duration_s=20.0, prompt="footsteps", window_s=8.0),
        tmp_path / "out" / "sfx.wav",
    )
    assert facts["windows"] == 3
    assert len(calls) == 3
    assert all(str(repo / ".venv" / "bin" / "python") == c[0] and c[1] == "demo.py" for c in calls)
    assert [c[c.index("--seed") + 1] for c in calls] == ["7", "8", "9"]
    assert (tmp_path / "out" / "sfx.wav").exists()


def test_recorded_takes_and_sfx_reach_the_mastered_mix(tmp_path: Path) -> None:
    """The whole point of the two stages: what the mix masters is the operator's own voice with
    the foley bedded under it, through the same code path a synthesized narration takes."""
    from content_factory.workflows.stages import stage_align_words, stage_mix_audio

    ctx = make_context(project_dir=tmp_path)
    for beat in sample_story_plan().beats:
        _wav(tmp_path / "takes" / f"{beat.beat_id}.johan.wav", seconds=3.0)
    exports = ctx.ddir() / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    _mp4(exports / "final.mp4", seconds=4)

    stage_voice_over(ctx)
    stage_align_words(ctx)
    stage_sound_design(ctx)
    out = stage_mix_audio(ctx)

    assert (ctx.ddir() / "audio" / "narration-with-sfx.wav").exists()
    mastered = ctx.ddir() / "audio" / "narration-mastered.wav"
    assert mastered.exists() and mastered.stat().st_size > 0
    assert out.facts


def test_the_final_mux_takes_whichever_picture_the_lane_produced(tmp_path: Path) -> None:
    """A drawn film has no Remotion bundle. compose_video used to look for one by name and fail
    on a picture that was sitting right next to it."""
    from content_factory.workflows.stages import _silent_picture

    ctx = make_context(project_dir=tmp_path)
    exports = ctx.ddir() / "exports"
    exports.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="no picture yet"):
        _silent_picture(ctx)

    _mp4(exports / "generated.mp4", seconds=1)
    assert _silent_picture(ctx).name == "generated.mp4"
    _mp4(exports / "final.mp4", seconds=1)
    assert _silent_picture(ctx).name == "final.mp4"  # the post chain's output wins
    _mp4(exports / "picture.mp4", seconds=1)
    assert _silent_picture(ctx).name == "picture.mp4"  # …until the mux has set it aside


def test_hold_mode_cuts_the_drawings_together_without_inventing_frames(tmp_path: Path) -> None:
    """The choppiness is the point. Held drawings must stay dead still between cuts — an
    interpolated or resampled join would put frames on screen that were never drawn."""
    import subprocess as sp

    from PIL import Image, ImageChops, ImageStat

    from content_factory.workflows.stages import _hold_stills

    ctx = make_context(project_dir=tmp_path)
    anchors = ctx.ddir() / "anchors"
    entries = []
    for i, colour in enumerate(((200, 30, 30), (30, 200, 30), (30, 30, 200))):
        path = anchors / f"sht_x{i:04d}" / "0000.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 180), colour).save(path)
        entries.append(
            {
                "shot_id": f"sht_x{i:04d}",
                "width": 320,
                "height": 180,
                "frames": [
                    {
                        "frame_index": 0,
                        "path": f"anchors/sht_x{i:04d}/0000.png",
                        "sha256": f"{i:064d}",
                    }
                ],
            }
        )

    out = _hold_stills(ctx, {"shots": entries}, {})
    assert out.facts["motion"] == "hold" and out.facts["stills"] == 3
    video = ctx.ddir() / "exports" / "generated.mp4"
    assert video.exists()

    frames_dir = tmp_path / "probe"
    frames_dir.mkdir()
    sp.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            "fps=4,scale=32:18",
            str(frames_dir / "%03d.png"),
        ],
        check=True,
    )
    images = [Image.open(p).convert("L") for p in sorted(frames_dir.glob("*.png"))]
    diffs = [ImageStat.Stat(ImageChops.difference(a, b)).mean[0] for a, b in pairwise(images)]
    cuts = [d for d in diffs if d > 5]
    # Two cuts between three drawings, and every other pair is a still hold.
    assert len(cuts) == 2, f"expected 2 cuts, got {len(cuts)} from {diffs}"
    assert max(d for d in diffs if d <= 5) < 1.0, "a held drawing drifted between frames"

    assert _hold_stills(ctx, {"shots": entries}, {}).facts["cache_hit"] is True


def test_interpolation_can_be_switched_off(tmp_path: Path) -> None:
    """`engine: none` leaves the picture exactly as the video branch made it."""
    from dataclasses import replace

    from content_factory.workflows.stages import stage_interpolate

    ctx = make_context(project_dir=tmp_path)
    exports = ctx.ddir() / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    picture = _mp4(exports / "generated.mp4", seconds=2)
    before = picture.read_bytes()

    out = stage_interpolate(replace(ctx, params={"engine": "none"}))
    assert out.facts["engine"] == "none" and out.facts["clips"] == 0
    assert picture.read_bytes() == before, "the picture must be left untouched"
