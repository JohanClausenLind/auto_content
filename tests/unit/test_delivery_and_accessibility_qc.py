from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from content_factory.qc.accessibility import (
    check_artboard_accessibility,
    check_flashing,
    export_accessibility_report,
)
from content_factory.qc.delivery import check_delivery_promise
from content_factory.schemas.fixtures import sample_artboard
from content_factory.schemas.scenes import CompiledScene, CompiledTimeline


def _timeline(total: int, scene_len: int) -> CompiledTimeline:
    scenes = []
    cursor = 0
    i = 0
    while cursor < total:
        d = min(scene_len, total - cursor)
        scenes.append(
            CompiledScene(
                scene_id=f"scn_qc{i:010d}",
                beat_id=f"beat_qc{i:08d}",
                start_frame=cursor,
                duration_frames=d,
            )
        )
        cursor += d
        i += 1
    return CompiledTimeline(
        timeline_id="tl_qc0000000001",
        plan_id="plan_qc00000001",
        fps=30,
        width=320,
        height=180,
        total_frames=total,
        scenes=tuple(scenes),
        compiler_version="t",
        plan_hash="0" * 64,
    )


def _make(tmp: Path, name: str, src: str, vf: str | None = None, seconds: int = 4) -> Path:
    out = tmp / name
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        src,
        "-t",
        str(seconds),
        "-r",
        "30",
        "-pix_fmt",
        "yuv420p",
    ]
    if vf:
        cmd += ["-vf", vf]
    subprocess.run([*cmd, str(out)], check=True)
    return out


def test_fake_animated_explainer_fails_and_real_animation_passes(tmp_path: Path) -> None:
    # "Pan-zoom slideshow": one realistic editorial slide with a slow Ken Burns zoom — the
    # classic fake explainer. Built from a smooth PIL slide (like a real artboard export).
    from PIL import Image as PILImage
    from PIL import ImageDraw

    slide = PILImage.new("RGB", (640, 360), (247, 245, 240))
    d = ImageDraw.Draw(slide)
    d.rectangle([60, 60, 580, 110], fill=(15, 23, 32))
    d.rectangle([60, 150, 400, 190], fill=(47, 111, 143))
    d.rectangle([60, 220, 520, 250], fill=(120, 120, 120))
    slide_path = tmp_path / "slide.png"
    slide.save(slide_path)
    fake = tmp_path / "fake.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(slide_path),
            "-vf",
            "zoompan=z='1+0.002*on':d=120:s=320x180:fps=30",
            "-t",
            "4",
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(fake),
        ],
        check=True,
    )
    tl = _timeline(120, 60)
    result = check_delivery_promise(fake, tl)
    assert not result.passed, result.facts
    assert any(f.check == "delivery_promise" for f in result.findings)
    stats = result.facts["stats"]
    assert isinstance(stats, list)
    assert all(not st["animated"] for st in stats), result.facts
    # Real animation: moving content inside the frame.
    real = _make(tmp_path, "real.mp4", "testsrc=size=320x180:rate=30")
    ok = check_delivery_promise(real, tl)
    assert ok.passed, ok.facts


def test_flashing_detection_blocks_strobe_and_passes_calm_video(tmp_path: Path) -> None:
    frames_dir = tmp_path / "strobe-frames"
    frames_dir.mkdir()
    from PIL import Image as PILImage

    for i in range(120):
        colour = 255 if (i // 2) % 2 == 0 else 8  # ~7.5 flashes/second
        PILImage.new("L", (320, 180), colour).save(frames_dir / f"{i:04d}.png")
    strobe = tmp_path / "strobe.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-framerate",
            "30",
            "-i",
            str(frames_dir / "%04d.png"),
            "-pix_fmt",
            "yuv420p",
            str(strobe),
        ],
        check=True,
    )
    result = check_flashing(strobe, fps=30, frames=120)
    rate = result.facts["flash_rate_per_s"]
    assert isinstance(rate, float)
    assert not result.passed and rate > 3
    calm = _make(tmp_path, "calm.mp4", "color=c=gray:size=320x180:rate=30")
    assert check_flashing(calm, fps=30, frames=120).passed


def test_artboard_accessibility_and_report_export(tmp_path: Path) -> None:
    good = check_artboard_accessibility(sample_artboard())
    assert good.passed
    bad_board = sample_artboard().model_copy(update={"alt_text": " "})
    bad = check_artboard_accessibility(bad_board)
    assert not bad.passed and any(f.check == "alt_text" for f in bad.findings)
    report = export_accessibility_report(
        tmp_path / "a11y.json", "dlv_image0000001", {"artboard": good}
    )
    assert report["passed"] and (tmp_path / "a11y.json").exists()


def test_broken_reading_order_is_caught() -> None:
    board = sample_artboard()
    layers = list(board.layers)
    layers[1] = layers[1].model_copy(update={"reading_order": 5})
    with pytest.raises(ValueError):  # the contract itself refuses gaps
        board.model_copy(update={"layers": tuple(layers)})
        from content_factory.schemas.artboards import ArtboardSpec

        ArtboardSpec.model_validate(
            {
                **board.model_dump(mode="json"),
                "layers": [layer.model_dump(mode="json") for layer in layers],
            }
        )
