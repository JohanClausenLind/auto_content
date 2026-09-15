"""Derived artefacts from a control bundle directory: the OpenPose skeleton track as a video."""

from __future__ import annotations

from pathlib import Path

from content_factory.audio.mix import ffmpeg


def pose_video_from_track(shot_dir: Path, *, fps: int, out: Path | None = None) -> Path:
    """Encode ``pose_skeleton/frames/*.png``."""
    frames = sorted((shot_dir / "pose_skeleton" / "frames").glob("*.png"))
    if not frames:
        raise FileNotFoundError(f"no pose_skeleton frames under {shot_dir}")
    target = out or shot_dir / "pose_skeleton" / "pose.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-framerate",
            str(fps),
            "-pattern_type",
            "glob",
            "-i",
            str(shot_dir / "pose_skeleton" / "frames" / "*.png"),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            str(target),
        ],
        timeout=600,
    )
    return target
