"""Wan-Animate-2 pose package binds and declares the missing weights honestly."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from content_factory.comfyui.client import inject_parameters
from content_factory.controls import pose_video_from_track
from content_factory.media.wan_packages import snap_length_4k1, wan_animate2_pose_package
from content_factory.schemas.comfyui import CapabilityFlag


def test_snap_4k1() -> None:
    assert snap_length_4k1(81) == 81 and snap_length_4k1(80) == 81 and snap_length_4k1(1) == 5
    assert all((snap_length_4k1(n) - 1) % 4 == 0 for n in range(1, 300))


def test_package_binds_pose_video_and_reference() -> None:
    pkg = wan_animate2_pose_package(width=768, height=448, length=49)
    assert pkg.package_id == "wan-animate-2.pose"
    assert {CapabilityFlag.pose_video, CapabilityFlag.image_to_video} <= set(pkg.capabilities)
    names = {p.name for p in pkg.parameters}
    assert {
        "prompt",
        "first_frame",
        "pose_video",
        "width",
        "height",
        "length",
        "pose_strength",
        "seed",
    } <= names
    wf = inject_parameters(
        pkg,
        {
            "prompt": "walks left",
            "first_frame": "anchor.png",
            "pose_video": "pose.mp4",
            "width": 768,
            "height": 448,
            "length": 49,
            "pose_strength": 0.9,
            "seed": 3,
        },
    )
    assert wf["9"]["inputs"]["file"] == "pose.mp4" and wf["11"]["inputs"]["pose_video"] == ["10", 0]
    assert wf["11"]["inputs"]["reference_image"] == ["6", 0] and wf["11"]["inputs"][
        "clip_vision_output"
    ] == ["8", 0]
    assert wf["12"]["inputs"]["latent_image"] == ["11", 2]
    missing = {m.filename for m in pkg.required_models}
    assert {"clip_vision_h.safetensors", "wan_2.1_vae.safetensors"} <= missing
    assert pkg.expected_outputs == ("15",)


def test_pose_video_from_track(tmp_path: Path) -> None:
    frames = tmp_path / "pose_skeleton" / "frames"
    frames.mkdir(parents=True)
    for i in range(6):
        Image.new("RGB", (64, 64), (i * 30, 0, 0)).save(frames / f"{i:04d}.png")
    out = pose_video_from_track(tmp_path, fps=24)
    assert out == tmp_path / "pose_skeleton" / "pose.mp4" and out.stat().st_size > 0
