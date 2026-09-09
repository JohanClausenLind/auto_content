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
    clip = _mp4(tmp_path / "holiday.mp4")
    with pytest.raises(ValueError, match=r"holiday\.mp4 is video, and this lane takes audio"):
        stage_inputs(tmp_path / "prj", [clip], wanted=["audio"], log=lambda _m: None)


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
