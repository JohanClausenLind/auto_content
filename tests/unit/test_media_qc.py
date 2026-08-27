from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from content_factory.qc.media import check_still, check_video

SMOKE = Path(__file__).resolve().parents[2] / "apps" / "renderer" / "out" / "smoke-title.mp4"


def test_still_qc_flags_blank_and_wrong_size(tmp_path: Path) -> None:
    blank = tmp_path / "blank.png"
    Image.new("RGB", (100, 100), (255, 255, 255)).save(blank)
    r = check_still(blank, width=100, height=100)
    assert not r.passed and any(f.check == "blank" for f in r.findings)
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    for x in range(50):
        for y in range(100):
            img.putpixel((x, y), (0, 0, 0))
    ok = tmp_path / "ok.png"
    img.save(ok)
    assert check_still(ok, width=100, height=100).passed
    wrong = check_still(ok, width=200, height=100)
    assert any(f.check == "dimensions" for f in wrong.findings)


def test_video_qc_on_synthetic_clip(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(clip),
        ],  # noqa: S607
        check=True,
    )
    r = check_video(clip, width=320, height=240, fps=30, frames=30)
    assert r.passed, r.findings
    assert r.facts["faststart"] is True
    bad = check_video(clip, width=1920, height=1080, fps=25, frames=30)
    assert {f.check for f in bad.findings} >= {"dimensions", "fps"}
    black = tmp_path / "black.mp4"
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=320x240:rate=30",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(black),
        ],  # noqa: S607
        check=True,
    )
    rb = check_video(black, width=320, height=240, fps=30, frames=30)
    assert any(f.check in {"black_frame", "frozen"} for f in rb.findings)


@pytest.mark.skipif(not SMOKE.exists(), reason="smoke render not present")
def test_smoke_render_passes_video_qc() -> None:
    r = check_video(SMOKE, width=1920, height=1080, fps=30, frames=90)
    assert r.passed, r.findings
