"""Delivery-promise QC (17): an "animated explainer" that renders as static pan-zoom slides fails.

Deterministic: per scene, sample frame pairs and measure residual motion after compensating for
global translation/zoom (pan-zoom). High residual = real animation; near-zero residual on most
scenes = a slideshow sold as animation."""

from __future__ import annotations

import io
import subprocess
from collections.abc import Mapping
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


ANIMATED_RESIDUAL_MIN = 0.005
"""Fraction of pixels that must still differ after pan/zoom compensation for a scene to count as
animated. 0.5 %, and the compensation is what makes it mean something: a Ken Burns slideshow
collapses to ~0 once the zoom and the sub-pixel translation are searched out, while genuine
internal animation does not."""

GENERATED_FRACTION_FOR_ANIMATED = 0.5
"""Above this share of generated segments, the film *is* a moving-picture film and being judged as
one is the point. Below it and above zero it is mixed, and only the generated segments are held to
the bar — a typeset card that does not animate is not a defect, it is a card."""


def promised_delivery(intent: str, routes: Mapping[str, int] | None = None) -> str:
    """What this film promises: `animated_explainer`, `mixed` or `chart_led`.

    Derived from what the film is **made of**, not from prose. The old rule was
    `spec.intent.startswith("animated")` over a 1000-character free-text field an operator writes
    a sentence into — so it was false for every real brief and the promise was *always*
    `chart_led`, which is the one value that makes the check unable to fail. A check that cannot
    fail is not a check.

    `routes` are `compose.json`'s per-segment route counts (`{"generate": n, "render": m}`). A
    prose intent that actually names motion still counts, but as one signal rather than the only
    one: an operator who asked for an animated explainer and got a slideshow should hear about it
    even if every segment was rendered as a card.
    """
    words = intent.lower()
    asked_for_motion = any(w in words for w in ("animated", "animation", "motion graphic"))
    counts = dict(routes or {})
    generated = counts.get("generate", 0)
    total = sum(counts.values())
    share = generated / total if total else 0.0
    if asked_for_motion or share >= GENERATED_FRACTION_FOR_ANIMATED:
        return "animated_explainer"
    if generated:
        return "mixed"
    return "chart_led"


def check_delivery_promise(
    video: Path,
    timeline: CompiledTimeline,
    *,
    promised: str = "animated_explainer",
    routes_by_scene: Mapping[str, str] | None = None,
    generated_for_real: bool = True,
) -> QCResult:
    """Does the picture move as much as the film promised?

    ``routes_by_scene`` maps scene id -> `compose.json`'s route for that segment. With it, a
    `mixed` film is judged on its **generated** scenes only: those are the ones that were supposed
    to move, and requiring half of *all* scenes to animate in a film that is mostly typeset cards
    measures the edit rather than the delivery.

    ``generated_for_real`` is False when the clips came from the mock backend. The finding is
    still recorded — a still where a clip should be is worth seeing either way — but it does not
    block, because on the mock a generated clip *is* a static test pattern and failing the run for
    that measures the backend rather than the film. Every offline lane run would otherwise fail
    the check the moment it started working.
    """
    findings: list[Finding] = []
    animated_scenes = 0
    scene_stats: list[dict] = []
    routes_by_scene = dict(routes_by_scene or {})
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
        is_animated = residual > ANIMATED_RESIDUAL_MIN
        animated_scenes += int(is_animated)
        scene_stats.append(
            {
                "scene_id": scene.scene_id,
                "residual": round(residual, 3),
                "animated": is_animated,
                # How this scene reached the cut, when the manifest says. A generated clip that
                # does not move is a different fault from a card that does not.
                "route": routes_by_scene.get(scene.scene_id, "unknown"),
            }
        )
    total = len(scene_stats)
    judged = [
        stat
        for stat in scene_stats
        # A mixed film is judged on the scenes that were supposed to move.
        if promised != "mixed" or stat["route"] == "generate"
    ]
    animated_judged = sum(1 for stat in judged if stat["animated"])
    facts = {
        "promised": promised,
        "generated_for_real": generated_for_real,
        "scenes_sampled": total,
        "animated_scenes": animated_scenes,
        "scenes_judged": len(judged),
        "animated_judged": animated_judged,
        "routes": sorted({stat["route"] for stat in scene_stats}),
        "stats": scene_stats,
    }
    if promised == "animated_explainer" and total and animated_scenes / total < 0.5:
        findings.append(
            Finding(
                "delivery_promise",
                Severity.critical,
                f"promised an animated explainer but only {animated_scenes}/{total} scenes show"
                " real animation (pan-zoom slides detected)",
            )
        )
    elif promised == "mixed" and judged and animated_judged < len(judged):
        still = [stat["scene_id"] for stat in judged if not stat["animated"]]
        findings.append(
            Finding(
                "delivery_promise",
                Severity.critical if generated_for_real else Severity.advisory,
                f"{len(still)} of {len(judged)} generated segment(s) do not move after pan/zoom"
                f" compensation: {still[:5]}. A generated clip that is a still is a wasted"
                " generation, not a stylistic choice"
                + ("" if generated_for_real else " (mock backend: advisory only)"),
            )
        )
    return QCResult(tuple(findings), facts)
