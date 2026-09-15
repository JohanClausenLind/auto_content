"""Stock footage: a handful of hand-picked clips, fetched per shot rather than mirrored."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from content_factory.qc.media import ffprobe
from content_factory.schemas.base import file_sha256
from content_factory.schemas.reference import (
    Affection,
    ContactTag,
    InteractionTag,
    Modality,
    Posture,
    ReferenceClip,
    ReferenceFile,
    ReferenceSource,
    UsageClass,
)

INGESTER_VERSION = "0.1.0"
SOURCE = ReferenceSource.pexels

PEXELS_DIR = "Stock/pexels"

_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v"})


@dataclass(frozen=True)
class _Verified:
    """What a person saw in the frames. Nothing here is derived from the filename."""

    people_count: int
    affection: Affection
    interaction_tags: tuple[InteractionTag, ...]
    contact_tags: tuple[ContactTag, ...]
    postures: tuple[Posture, ...]
    setting: str
    camera_angles: tuple[str, ...]
    caption: str
    vocabulary_gap: str = ""
    """What the closed vocabulary cannot say about this clip. Reported, never invented as a tag."""


VERIFIED_CLIPS: dict[str, _Verified] = {
    "4701507": _Verified(
        people_count=2,
        affection=Affection.affection,
        # Frames 0, 50, 100 and 149: two seated people across a table, hand around hand. Nobody
        # walks, so hold_hands_walk is wrong; "holding hands while seated" goes in vocabulary_gap.
        interaction_tags=(InteractionTag.conversation,),
        contact_tags=(ContactTag.hands,),
        postures=(Posture.sitting,),
        setting="indoor table, two coffee mugs",
        # A high angle looking across the table, arms entering from both sides. "side" is the
        # bucket in the contract's own examples that this is nearest to.
        camera_angles=("side",),
        caption=(
            "Close-up across a wooden table: two people seated opposite each other, one hand "
            "clasped around the other's hand and wrist beside two white coffee mugs on a dark "
            "green runner. High side angle, hands and forearms only, no faces in frame."
        ),
        vocabulary_gap=(
            "holding hands while seated and still: InteractionTag has hold_hands_walk only, so "
            "this clip is tagged conversation and its caption carries the hand-hold"
        ),
    ),
}


def ingest(root: Path, *, ingested_at: str) -> tuple[list[ReferenceClip], list[str]]:
    """Every verified stock clip present on disk, plus one line per file left out and why."""
    directory = root / PEXELS_DIR
    if not directory.is_dir():
        return [], [f"pexels {PEXELS_DIR}: directory is not on disk, stock is fetched per shot"]

    clips: list[ReferenceClip] = []
    skipped: list[str] = []
    for path in sorted(directory.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _VIDEO_SUFFIXES:
            skipped.append(f"pexels {path.name}: not a video file")
            continue
        clip, reason = _build_clip(root, path, ingested_at)
        if clip is not None:
            clips.append(clip)
        if reason is not None:
            skipped.append(reason)

    clips.sort(key=lambda clip: clip.clip_id)
    return clips, sorted(skipped)


def _build_clip(
    root: Path, path: Path, ingested_at: str
) -> tuple[ReferenceClip | None, str | None]:
    """One clip, or None and the reason. Never raises: a bad or unknown file is a reason string."""
    pexels_id = path.stem
    verified = VERIFIED_CLIPS.get(pexels_id)
    if verified is None:
        return None, (
            f"pexels {path.name}: no verified annotation, and a stock clip is tagged only after a "
            f"frame has been looked at"
        )

    probed, error = _probe(path)
    if probed is None:
        return None, f"pexels {path.name}: {error}"
    width, height, fps, frames = probed

    return (
        ReferenceClip(
            clip_id=f"pexels_{pexels_id}",
            source=SOURCE,
            source_ref=f"pexels/{pexels_id}",
            modality=Modality.video_rgb,
            # Real footage of real hands, lit and graded, which is exactly what the mocap sources
            # cannot give. Nothing is between the lens and the subjects.
            usage=UsageClass.pixels_usable,
            people_count=verified.people_count,
            affection=verified.affection,
            interaction_tags=verified.interaction_tags,
            contact_tags=verified.contact_tags,
            postures=verified.postures,
            setting=verified.setting,
            camera_angles=verified.camera_angles,
            frame_count=frames,
            native_fps=fps,
            duration_s=frames / fps,
            width=width,
            height=height,
            # No pose data ships with stock footage and no pose estimator is installed, so there is
            # nothing to point a pose_root at and claiming a format would be a lie.
            pose_format="none",
            caption=verified.caption,
            # The caption is what the operator saw in the frames; Pexels ships no description here.
            caption_source="operator",
            files=(
                ReferenceFile(
                    role="video",
                    path=path.relative_to(root).as_posix(),
                    sha256=file_sha256(path),
                    size_bytes=path.stat().st_size,
                ),
            ),
            ingested_at=ingested_at,
            ingester_version=INGESTER_VERSION,
        ),
        None,
    )


def _probe(path: Path) -> tuple[tuple[int, int, float, int] | None, str | None]:
    """Width, height, frame rate and frame count from ffprobe, or None and why not."""
    try:
        info = ffprobe(path)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return None, f"ffprobe failed ({type(exc).__name__})"

    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        return None, "no video stream"

    width, height = video.get("width"), video.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width < 1 or height < 1:
        return None, f"ffprobe reports dimensions {width}x{height}"

    fps = _frame_rate(video.get("avg_frame_rate")) or _frame_rate(video.get("r_frame_rate"))
    if fps is None:
        return None, "ffprobe reports no usable frame rate"

    frames = _frame_count(video, info.get("format") or {}, fps)
    if frames is None:
        return None, "ffprobe reports neither a frame count nor a duration"
    return (width, height, fps, frames), None


def _frame_rate(value: object) -> float | None:
    """An ffprobe rational such as ``25/1`` as a float, or None when it is absent or zero."""
    if not isinstance(value, str) or "/" not in value:
        return None
    try:
        rate = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return float(rate) if rate > 0 else None


def _frame_count(video: dict, container: dict, fps: float) -> int | None:
    """``nb_frames`` when the container carries it, otherwise duration times the frame rate."""
    declared = video.get("nb_frames")
    if isinstance(declared, str) and declared.isdigit() and int(declared) > 0:
        return int(declared)
    for source in (video, container):
        duration = source.get("duration")
        try:
            seconds = float(duration)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return max(1, round(seconds * fps))
    return None
