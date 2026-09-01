"""Delivery-promise QC (17): an "animated explainer" that renders as static pan-zoom slides fails.

Deterministic: per scene, sample frame pairs and measure residual motion after compensating for
global translation/zoom (pan-zoom). High residual = real animation; near-zero residual on most
scenes = a slideshow sold as animation."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

from PIL import Image, ImageChops

from content_factory.qc.media import Finding, QCResult, Severity
from content_factory.schemas.scenes import CompiledTimeline


def _frame(path: Path, index: int, size: tuple[int, int] = (160, 90)) -> Image.Image:
    out = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select=eq(n\\,{index}),scale={size[0]}:{size[1]}",
            "-vframes",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    return Image.open(io.BytesIO(out.stdout)).convert("L")


def _mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a, b)
    hist = diff.histogram()
    total = sum(hist)
    return sum(i * c for i, c in enumerate(hist)) / max(1, total)


def _moved_fraction(a: Image.Image, b: Image.Image, threshold: int = 18) -> float:
    """Fraction of pixels whose luminance moved noticeably — catches localized animation
    (a counting number, a revealing bullet) that a global mean would wash out."""
    hist = ImageChops.difference(a, b).histogram()
    total = sum(hist)
    return sum(c for i, c in enumerate(hist) if i > threshold) / max(1, total)


def _residual_after_panzoom(a: Image.Image, b: Image.Image) -> float:
    """Minimum difference of b against small global translations x zooms of a. If a pan/zoom
    explains the change, the residual collapses; genuine internal animation does not. A slight
    blur removes sub-pixel resampling noise so slideshows collapse to ~0."""
    from PIL import ImageFilter

    a = a.filter(ImageFilter.GaussianBlur(1.6))
    b = b.filter(ImageFilter.GaussianBlur(1.6))
    w, h = a.size
    best = _mean_abs_diff(a, b)
    candidates: list[Image.Image] = [a]
    for zoom in (1.0, 1.0025, 1.005, 1.0075, 1.01, 1.0125, 1.015, 1.02):
        if zoom == 1.0:
            base = a
        else:
            zw, zh = round(w * zoom), round(h * zoom)
            base = a.resize((zw, zh), Image.Resampling.BICUBIC).crop(
                ((zw - w) // 2, (zh - h) // 2, (zw - w) // 2 + w, (zh - h) // 2 + h)
            )
        for dx in (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5):
            for dy in (-1.0, -0.5, 0.0, 0.5, 1.0):
                if dx == 0 and dy == 0:
                    cand = base
                else:
                    # Sub-pixel translation (bilinear) collapses resampling shimmer from
                    # pan/zoom slideshows without hiding genuine internal animation.
                    cand = base.transform(
                        (w, h),
                        Image.Transform.AFFINE,
                        (1, 0, dx, 0, 1, dy),
                        resample=Image.Resampling.BILINEAR,
                    )
                best = min(best, _mean_abs_diff(cand, b))
                candidates.append(cand)
    # Report the moved-pixel fraction of the best-aligned candidate.
    best_cand = min(candidates, key=lambda c: _mean_abs_diff(c, b))
    return _moved_fraction(best_cand, b)


def check_delivery_promise(
    video: Path, timeline: CompiledTimeline, *, promised: str = "animated_explainer"
) -> QCResult:
    findings: list[Finding] = []
    animated_scenes = 0
    scene_stats: list[dict] = []
    for scene in timeline.scenes:
        if scene.duration_frames < 12:
            continue
        # Compare CLOSE frame pairs (6 frames apart) so a slow pan/zoom stays within the
        # compensation search window while genuine internal animation still registers.
        gap = 6
        scene_end = scene.start_frame + scene.duration_frames - 1
        start = min(scene.start_frame + 3, scene_end - gap)
        mid = min(scene.start_frame + scene.duration_frames // 2, scene_end - gap)
        try:
            late = min(scene.start_frame + (3 * scene.duration_frames) // 4, scene_end - gap)
            pairs = [
                (_frame(video, start), _frame(video, start + gap)),
                (_frame(video, mid), _frame(video, mid + gap)),
                (_frame(video, late), _frame(video, late + gap)),
            ]
        except Exception:  # decode failure or empty frame from ffmpeg
            findings.append(
                Finding("decode", Severity.critical, f"could not sample scene {scene.scene_id}")
            )
            continue
        residual = max(_residual_after_panzoom(a, b) for a, b in pairs)
        is_animated = residual > 0.005  # >0.5% of pixels moved after pan/zoom compensation
        animated_scenes += int(is_animated)
        scene_stats.append(
            {"scene_id": scene.scene_id, "residual": round(residual, 3), "animated": is_animated}
        )
    total = len(scene_stats)
    facts = {"scenes_sampled": total, "animated_scenes": animated_scenes, "stats": scene_stats}
    if promised == "animated_explainer" and total and animated_scenes / total < 0.5:
        findings.append(
            Finding(
                "delivery_promise",
                Severity.critical,
                f"promised an animated explainer but only {animated_scenes}/{total} scenes show real animation (pan-zoom slides detected)",
            )
        )
    return QCResult(tuple(findings), facts)
