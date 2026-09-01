"""Phase-3 gate: the offline narrated demo renders with synchronized narration and captions,
passing alignment and loudness checks. Needs the headless browser (downloaded by setup.sh)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.runners.demo import run_demo

BROWSER = Path(__file__).resolve().parents[2] / "apps" / "renderer" / "node_modules" / ".remotion"


@pytest.mark.skipif(not BROWSER.exists(), reason="headless browser not downloaded (run setup.sh)")
def test_narrated_offline_demo_passes_all_gates(tmp_path: Path) -> None:
    report = run_demo(
        quality="demo", projects_dir=tmp_path / "projects", artifacts_dir=tmp_path / "artifacts"
    )
    assert report["passed"], json.dumps(report, indent=1)[:2000]
    video = report["deliverables"]["dlv_short0000001"]
    assert video["narrated"] and video["alignment_passed"] and video["audio_qc_passed"]
    assert video["caption_cues"] >= 3
    assert abs(video["loudness"]["integrated_lufs"] - (-14.0)) <= 1.0
    assert video["loudness"]["true_peak_dbtp"] <= -0.9
    assert (
        abs(video["audio_facts"]["audio_duration_s"] - video["audio_facts"]["video_duration_s"])
        <= 0.25
    )
    # Captions and stems exist on disk for the project.
    proj = tmp_path / "projects" / report["project_id"]
    vdir = proj / "deliverables" / "dlv_short0000001"
    assert (vdir / "captions" / "captions.srt").read_text().startswith("1\n")
    assert (vdir / "captions" / "captions.vtt").read_text().startswith("WEBVTT")
    assert (vdir / "audio" / "narration-stem.wav").exists()
    assert (vdir / "audio" / "narration-mastered.wav").exists()
    image = report["deliverables"]["dlv_image0000001"]
    assert image["qc_passed"]
