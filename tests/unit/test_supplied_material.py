"""Material the operator supplies, and the one place every lane looks for it.

Four lanes in this catalogue work on something the operator already has: a recording to repair, a
clip to finish, a folder of stills to enlarge, a picture to move. Each of them used to say so only
in prose, and each expected the file in a different directory — one of them inside a deliverable
folder named after a generated id, which meant the documented way to run that lane was to run a
different lane first.

There is one rule now: **the material goes in ``<project>/uploads``**, put there by
``--input``, by a file dropped on the canvas, or by hand. These tests pin the rule at both ends —
the staging that puts files there, and the stages that pick them up.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from content_factory.runners.local import make_context, stage_inputs, workflow_inputs
from content_factory.workflows.stages import (
    _adopt_uploaded_picture,
    _anchor_from_upload,
    _chain_inputs,
)


def _wav(path: Path, seconds: float = 1.0, rate: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x01" * int(seconds * rate))
    return path


def _png(path: Path, *, size: tuple[int, int] = (64, 48), colour: str = "#404050") -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def _mp4(path: Path, *, frames: int = 6) -> Path:
    """A real, tiny H.264 clip. ffmpeg is already a hard dependency of the audio chain."""
    from content_factory.audio.mix import ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=64x48:rate=8:duration={frames / 8}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(path),
        ],
        timeout=120,
    )
    return path


# --- staging ----------------------------------------------------------------------------------


def test_a_lane_declares_what_it_needs_supplied() -> None:
    assert workflow_inputs("audio-restore") == ("audio",)
    assert workflow_inputs("audio-picture-story") == ("audio",)
    assert workflow_inputs("video-finish") == ("video",)
    assert workflow_inputs("image-upscale") == ("image",)
    assert workflow_inputs("image-to-video") == ("image",)
    # A lane that makes its own material needs nothing supplied, and says so by having no node.
    assert workflow_inputs("narrated-video") == ()


def test_input_is_copied_into_the_uploads_folder_and_sniffed(tmp_path: Path) -> None:
    source = _wav(tmp_path / "elsewhere" / "talk.wav")
    project = tmp_path / "prj"
    staged = stage_inputs(project, [source], wanted=["audio"], log=lambda _m: None)
    assert staged == [
        {"file": "talk.wav", "kind": "audio", "mime": "audio/x-wav", "bytes": source.stat().st_size}
    ]
    assert (project / "uploads" / "talk.wav").read_bytes() == source.read_bytes()


def test_a_second_identical_stage_does_not_copy_again(tmp_path: Path) -> None:
    source = _wav(tmp_path / "talk.wav")
    project = tmp_path / "prj"
    stage_inputs(project, [source], log=lambda _m: None)
    target = project / "uploads" / "talk.wav"
    before = target.stat().st_mtime_ns
    stage_inputs(project, [source], log=lambda _m: None)
    assert target.stat().st_mtime_ns == before, (
        "--from resumes a run whose uploads are already right"
    )


def test_the_wrong_kind_of_file_for_the_lane_is_refused_by_name(tmp_path: Path) -> None:
    """`_mp4` writes a real picture, which is the point: the exception below it is for a video
    container with nothing in it, and a check that cannot still say no is not a check."""
    clip = _mp4(tmp_path / "holiday.mp4")
    with pytest.raises(ValueError, match=r"holiday\.mp4 is video, and this lane takes audio"):
        stage_inputs(tmp_path / "prj", [clip], wanted=["audio"], log=lambda _m: None)


def _recording_in_an_mp4(path: Path, *, seconds: float = 3.0) -> Path:
    """A recording as a phone writes it: an MP4 whose picture is nothing but black."""
    from content_factory.audio.mix import ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=25:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        timeout=120,
    )  # fmt: skip
    return path


def test_a_recording_that_arrived_in_an_mp4_is_staged_for_an_audio_lane(tmp_path: Path) -> None:
    """The mismatch that is not a mistake. `video/mp4` is what a phone, a voice-memo app and a
    meeting recorder all write, so an audio lane that refuses every one of them sends an operator
    to ffmpeg for a file it can already read. The record says audio while the MIME says video,
    and carries the measurement that settled the disagreement."""
    source = _recording_in_an_mp4(tmp_path / "elsewhere" / "voice-memo.mp4")
    project = tmp_path / "prj"

    staged = stage_inputs(project, [source], wanted=["audio"], log=lambda _m: None)

    assert staged[0]["kind"] == "audio"
    assert staged[0]["mime"] == "video/mp4"
    assert "black" in str(staged[0]["picture"])
    # Copied as it is: the stages convert on their own terms, and `transcribe_audio` normalises
    # this to mono PCM exactly as it would an m4a off the same phone.
    assert (project / "uploads" / "voice-memo.mp4").read_bytes() == source.read_bytes()


def test_a_file_the_pipeline_cannot_read_is_refused_with_what_it_is(tmp_path: Path) -> None:
    weird = tmp_path / "notes.docx"
    weird.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    with pytest.raises(ValueError, match="which this pipeline does not read"):
        stage_inputs(tmp_path / "prj", [weird], log=lambda _m: None)


def test_a_folder_stages_every_file_in_it(tmp_path: Path) -> None:
    folder = tmp_path / "shoot"
    for i in range(3):
        _png(folder / f"frame{i}.png")
    staged = stage_inputs(tmp_path / "prj", [folder], wanted=["image"], log=lambda _m: None)
    assert [s["file"] for s in staged] == ["frame0.png", "frame1.png", "frame2.png"]


def test_an_empty_folder_is_a_mistake_worth_naming(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(ValueError, match="empty directory"):
        stage_inputs(tmp_path / "prj", [empty], log=lambda _m: None)


# --- the stages that pick it up ---------------------------------------------------------------


def test_the_post_chain_adopts_a_supplied_clip(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    _mp4(ctx.project_dir / "uploads" / "camera.mp4")
    name, workdir = _chain_inputs(ctx)[0]
    assert name == "upload"
    assert (workdir / "clip.mp4").is_file()
    # Idempotent: a second call finds the same staged clip rather than copying again.
    assert _chain_inputs(ctx) == [(name, workdir)]


def test_the_post_chain_adopts_supplied_stills_as_a_sequence(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    for i in range(3):
        _png(ctx.project_dir / "uploads" / f"shot{i}.jpg")
    name, workdir = _chain_inputs(ctx)[0]
    assert name == "sequence"
    frames = sorted(p.name for p in (workdir / "frames").glob("*.png"))
    assert frames == ["0000.png", "0001.png", "0002.png"], "PNG is what every post-chain tool reads"


def test_a_clip_and_a_folder_of_stills_together_is_a_refusal(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    _mp4(ctx.project_dir / "uploads" / "camera.mp4")
    _png(ctx.project_dir / "uploads" / "still.png")
    with pytest.raises(RuntimeError, match="Finish one thing at a time"):
        _adopt_uploaded_picture(ctx)


def test_two_clips_is_a_refusal_and_not_a_sort_order(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    _mp4(ctx.project_dir / "uploads" / "a.mp4")
    _mp4(ctx.project_dir / "uploads" / "b.mp4")
    with pytest.raises(RuntimeError, match="2 clips"):
        _adopt_uploaded_picture(ctx)


def test_the_post_chain_with_nothing_at_all_says_how_to_give_it_something(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    with pytest.raises(RuntimeError, match=r"--input <clip or folder of stills>"):
        _chain_inputs(ctx)


def test_the_anchor_can_be_a_picture_the_operator_supplied(tmp_path: Path) -> None:
    """`image-to-video`'s caveat used to say a supplied still was not expressible in the graph.
    This is the behaviour that replaced the caveat: no model runs, and the manifest is the one a
    generated anchor would have written, so Generate Video cannot tell the difference."""
    ctx = make_context(project_dir=tmp_path / "prj")
    _png(ctx.project_dir / "uploads" / "photo.png", size=(128, 96))
    out = _anchor_from_upload(ctx)
    assert out.facts == {
        "anchors": 1,
        "backend": "upload",
        "shots": 1,
        "source": "photo.png",
        "size": "128x96",
    }
    manifest = json.loads((ctx.ddir() / "anchors" / "manifest.json").read_text())
    assert manifest["backend"] == "upload"
    assert manifest["shots"][0]["frames"][0]["path"] == "anchors/anchor.png"
    assert (ctx.ddir() / "anchors" / "anchor.png").is_file()


def test_a_jpeg_anchor_is_converted_rather_than_refused(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    _png(ctx.project_dir / "uploads" / "photo.jpg", size=(96, 96))
    assert _anchor_from_upload(ctx).facts["size"] == "96x96"
    assert (ctx.ddir() / "anchors" / "anchor.png").is_file()


def test_the_anchor_with_no_picture_says_where_to_put_one(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    with pytest.raises(RuntimeError, match="--input <image>"):
        _anchor_from_upload(ctx)


def test_two_pictures_for_one_anchor_is_a_refusal(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    _png(ctx.project_dir / "uploads" / "a.png")
    _png(ctx.project_dir / "uploads" / "b.png")
    with pytest.raises(RuntimeError, match="found 2 pictures"):
        _anchor_from_upload(ctx)


def test_material_already_in_the_run_satisfies_the_input_requirement(tmp_path: Path) -> None:
    """A `--from` resume must not argue about an input the earlier stages staged themselves.

    Measured on `audio-picture-story --from finish` (2026-09-10): refused with "this lane works
    on material you supply", nine stages past the one that reads the recording, about a file
    sitting in the run's own uploads folder. The refusal pointed at `--force`, which is the wrong
    instrument — it also waves through absent weights and unrunnable stages.
    """
    from typer.testing import CliRunner

    from content_factory.cli.main import app

    runner = CliRunner()
    project = tmp_path / "run"
    (project / "uploads").mkdir(parents=True)

    # Nothing staged: still refused, and the hint names the folder as well as the flag.
    empty = tmp_path / "empty"
    result = runner.invoke(app, ["make", "audio-picture-story", "--project-dir", str(empty)])
    assert result.exit_code == 3
    assert '"needs_input": ["audio"]' in result.output
    assert "uploads" in result.output

    # One file staged by hand: accepted, and `--plan` gets far enough to print the steps.
    (project / "uploads" / "reading.wav").write_bytes(b"RIFF....WAVEfmt ")
    planned = runner.invoke(
        app, ["make", "audio-picture-story", "--project-dir", str(project), "--plan"]
    )
    assert planned.exit_code == 0, planned.output
    assert '"workflow": "audio-picture-story"' in planned.output


def test_a_story_of_pictures_is_refused_by_a_lane_that_draws_none(tmp_path: Path) -> None:
    """Five `narrated-video` runs delivered films that are five "PLACEHOLDER · IMAGE / missing
    asset" cards end to end, with narration over them and an mp4 in the delivery package
    (measured 2026-09-10). `qc_deliverable` catches it now, but only after the render; this is
    the same fact, knowable in the first second."""
    import json

    from content_factory.cli.workflows_cmd import _story_wants_pictures_the_lane_cannot_make
    from content_factory.workflows.catalog import load_definitions

    lanes = load_definitions()
    pictures = tmp_path / "pictures.json"
    pictures.write_text(
        json.dumps({"scenes": [{"kind": "image", "asset_id": f"ast_{i}"} for i in range(5)]})
    )
    cards = tmp_path / "cards.json"
    cards.write_text(json.dumps({"scenes": [{"kind": "title"}, {"kind": "callout"}]}))

    run = tmp_path / "run"
    (run / "uploads").mkdir(parents=True)

    # A lane with no drawing stage and nothing supplied: refused, and the message says what to do.
    said = _story_wants_pictures_the_lane_cannot_make(lanes["narrated-video"], str(pictures), run)
    assert "5 of the story's 5 scenes are pictures" in said
    assert "image-set" in said and "--input" in said

    # A story of cards is fine on the same lane.
    assert (
        _story_wants_pictures_the_lane_cannot_make(lanes["narrated-video"], str(cards), run) == ""
    )

    # So is the same picture story on a lane that draws.
    assert _story_wants_pictures_the_lane_cannot_make(lanes["image-set"], str(pictures), run) == ""

    # And supplying the stills lifts it, which is what the one clean run of the five did.
    (run / "uploads" / "still.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert (
        _story_wants_pictures_the_lane_cannot_make(lanes["narrated-video"], str(pictures), run)
        == ""
    )

    # An unreadable story is the runner's problem to report, not this check's.
    assert (
        _story_wants_pictures_the_lane_cannot_make(lanes["narrated-video"], "nope.json", run) == ""
    )
