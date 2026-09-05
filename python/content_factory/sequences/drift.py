"""Drift QC (16.6): deterministic comparison of each generated frame against the anchor.

Locked-region similarity is measured OUTSIDE the frame's declared motion region (the subject may
move; everything else may not). Style delta is a global luminance-histogram distance. The
consistency priority (composition > subject anatomy > style > fine detail) is expressed as which
checks block: locked-region (composition) failures block first, style second."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter

from content_factory.schemas.sequences import Box

UNCALIBRATED = (0.30, 0.60)
"""The (locked_region_similarity, style_delta_max) pair for a **first run against a real model**.

Not a recommendation and not a default: it is loose enough that drift QC observes rather than
gates, which is what you want on the run whose whole purpose is to produce the numbers a real
threshold would be set from. It came out of `scripts/generate_holding_hands.py`, where it was two
inline floats with the comment "calibrate before tightening" — a calibration living in a script.
Named here so a caller selects it deliberately, and so the next person can see there is exactly one
uncalibrated profile rather than a different pair of magic numbers in every script.

Once a style or a camera move has been measured, the answer is an entry in
`ImageSequenceSettings.drift_thresholds.by_style` / `.by_camera`, not another use of this.
"""


@dataclass(frozen=True)
class DriftReport:
    frame_index: int
    locked_region_similarity: float  # 1.0 = identical outside the motion region
    style_delta: float  # 0.0 = identical global style
    passed: bool
    reasons: tuple[str, ...]


def _load(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def _mask_out(img: Image.Image, boxes: list[Box]) -> Image.Image:
    from PIL import ImageDraw

    out = img.copy()
    draw = ImageDraw.Draw(out)
    w, h = img.size
    for b in boxes:
        draw.rectangle(
            [round(b.x * w), round(b.y * h), round((b.x + b.w) * w), round((b.y + b.h) * h)],
            fill=(127, 127, 127),
        )
    return out


def _similarity(a: Image.Image, b: Image.Image) -> float:
    a = a.resize((128, 128)).filter(ImageFilter.GaussianBlur(1)).convert("L")
    b = b.resize((128, 128)).filter(ImageFilter.GaussianBlur(1)).convert("L")
    hist = ImageChops.difference(a, b).histogram()
    total = sum(hist)
    mean = sum(i * c for i, c in enumerate(hist)) / max(1, total)
    return max(0.0, 1.0 - mean / 64.0)


def _style_delta(a: Image.Image, b: Image.Image) -> float:
    ha = a.convert("L").histogram()
    hb = b.convert("L").histogram()
    na, nb = sum(ha), sum(hb)
    return sum(abs(x / na - y / nb) for x, y in zip(ha, hb, strict=True)) / 2.0


STRUCTURAL_GRID = 8
"""Cells per side for :func:`structural_similarity`. Eight is 64 cells: coarse enough that a few
pixels of camera drift do not register, fine enough to tell a figure on the left from one on the
right. It is also small enough that the whole comparison is pure Python arithmetic over 64 floats,
which keeps it deterministic across machines the way the rest of drift QC is."""


def structural_similarity(a_png: bytes, b_png: bytes, *, grid: int = STRUCTURAL_GRID) -> float:
    """Where the light is, rather than how much of it there is. 1.0 identical, 0.0 anticorrelated.

    `_similarity` measures mean absolute luminance difference, which is blind to *arrangement*:
    measured on six unrelated rendered cards (2026-09-08), completely different pictures sharing a
    flat paper ground scored **0.86-0.93** — higher than the bar a guide would be held to. This
    correlates the two frames' 8x8 block grids instead, so a picture with its content in different
    places scores low however similar its overall brightness is.

    Reported alongside the existing numbers rather than replacing them, and **not** gating: on the
    same six cards it separated better (0.47-0.84 for unrelated pairs against 0.83-0.98 for two
    moments of one scene) but the ranges still touch, and there is no live guided clip on this host
    to calibrate against. Two numbers whose disagreement is visible beat one number that is wrong.
    """
    x = _blocks(a_png, grid)
    y = _blocks(b_png, grid)
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    dx = [v - mx for v in x]
    dy = [v - my for v in y]
    numerator = sum(p * q for p, q in zip(dx, dy, strict=True))
    denominator = (sum(p * p for p in dx) ** 0.5) * (sum(q * q for q in dy) ** 0.5)
    if denominator == 0:
        # A flat frame has no arrangement to compare. Two flat frames agree; a flat frame against
        # a structured one does not, and saying "identical" there would hide the worst failure.
        return 1.0 if numerator == 0 and sum(dy) == 0 and sum(dx) == 0 else 0.0
    return round(max(0.0, min(1.0, (numerator / denominator + 1.0) / 2.0)), 4)


def _blocks(png: bytes, grid: int) -> list[float]:
    """Mean luminance per cell, 0..1, in row order. `BOX` resampling *is* the block mean."""
    img = Image.open(io.BytesIO(png)).convert("L").resize((grid * 8, grid * 8))
    small = img.resize((grid, grid), Image.Resampling.BOX)
    return [b / 255.0 for b in small.tobytes()]


GUIDE_SIMILARITY_MIN = 0.90
GUIDE_STRUCTURAL_MIN = 0.98
"""How close a generated frame has to be to the guide anchor pinned at that index.

**Calibrated on the first live guided run** (LTX-2.5 distilled GGUF through
`ltx-2.5.i2v-guided1`, 49 frames at 704x384, one anchor pinned at frame 48, 2026-09-09). Measured
against the end anchor, frame by frame:

| frame | similarity | structural |
| --- | --- | --- |
| 0 | 0.783 | 0.828 |
| 12 | 0.779 | 0.811 |
| 24 | 0.828 | 0.850 |
| 36 | 0.887 | 0.960 |
| **48** (the guide) | **0.972** | **0.998** |

Two things that pair of columns settles. The **floor is about 0.78**, not zero: a frame of the same
world that is nothing like the anchor still scores that high, because the measure is luminance
based and the track, the light and the palette are shared. So the 0.60 this constant started at was
not a loose bar, it was no bar — a clip that ignored its guide completely would have passed it.
And the guide frame sits far enough above its neighbours (0.972 against 0.887 one third of a second
earlier) that a bar between them is meaningful. 0.90 and 0.98 are those bars, and each has to fall
inside its own gap — `test_the_calibrated_bars_sit_between_the_measured_values` asserts exactly
that, and it is what caught a first attempt at 0.95, which is *below* the 0.960 the frame twelve
before the guide scored and would therefore have passed it.

The structural column is also the tighter of the two: three seeds of the same shot put the guide
frame at 0.9972-0.9979 while the nearest non-guide frame sat at 0.960, so 0.98 has margin on both
sides where the luminance measure's 0.90 has less.

One clip, one content type, one model. That is one calibration and not a law, and the honest thing
to do with the second one is to widen or tighten these rather than add a parallel constant.
"""


@dataclass(frozen=True)
class GuideAdherence:
    """How well one generated clip passed through the guides it was given."""

    reports: tuple[DriftReport, ...]
    structural: tuple[float, ...]
    passed: bool

    def as_facts(self) -> dict[str, object]:
        return {
            "guides": len(self.reports),
            "passed": self.passed,
            "similarity": [r.locked_region_similarity for r in self.reports],
            "worst": min((r.locked_region_similarity for r in self.reports), default=1.0),
            # Where the light is rather than how much: what catches a clip arriving at the right
            # tone in the wrong composition. Gating, at a bar the live run measured.
            "structural": list(self.structural),
            "structural_worst": min(self.structural, default=1.0),
            "reasons": [reason for r in self.reports for reason in r.reasons],
        }


def guide_adherence(
    frames: dict[int, bytes],
    guides: dict[int, bytes],
    *,
    similarity_min: float = GUIDE_SIMILARITY_MIN,
    structural_min: float = GUIDE_STRUCTURAL_MIN,
    style_delta_max: float = 0.30,
) -> GuideAdherence:
    """Compare each guide anchor with the generated frame at the index it was pinned to.

    The measurement the guided package never had. `LTXVAddGuide` pins an anchor at a frame index
    and nothing afterwards checked that the clip went anywhere near it — a guide whose strength was
    too low, or whose index the length rule had snapped past the end of the clip, produced exactly
    the same "ok" as one the model honoured.

    No motion boxes: a guide pins the *whole* composition, so the whole frame is the locked region.
    That is the difference from the still-edit path, where the subject is expected to move and
    everything else is not.

    **What this cannot tell you**, recorded because the first live run showed it and the numbers
    looked perfect throughout: neither measure distinguishes "the camera moved from A to B" from
    "A and B were cross-dissolved". Given a start and an end anchor far apart in camera space (a
    side view and a head-on view of the same runner), LTX-2.5 hit both pinned frames — 0.97 and
    0.97 — and got between them by drawing **both figures at once** at the midpoint. The similarity
    curve of a dissolve is monotone and indistinguishable from that of a move. Look at the middle
    frame; the metric is a check on the ends only.
    """
    reports = []
    structural = []
    for index in sorted(guides):
        frame = frames.get(index)
        if frame is None:
            reports.append(DriftReport(index, 0.0, 1.0, False, (f"no frame {index} in the clip",)))
            structural.append(0.0)
            continue
        report = drift_report(
            index,
            guides[index],
            frame,
            [],
            locked_region_similarity_min=similarity_min,
            style_delta_max=style_delta_max,
        )
        arrangement = structural_similarity(guides[index], frame)
        if arrangement < structural_min:
            report = DriftReport(
                report.frame_index,
                report.locked_region_similarity,
                report.style_delta,
                False,
                (
                    *report.reasons,
                    f"structural similarity {arrangement:.3f} below {structural_min}"
                    " (the frame is not arranged like the guide)",
                ),
            )
        reports.append(report)
        structural.append(arrangement)
    return GuideAdherence(tuple(reports), tuple(structural), all(r.passed for r in reports))


def drift_report(
    frame_index: int,
    anchor_png: bytes,
    frame_png: bytes,
    motion_boxes: list[Box],
    *,
    locked_region_similarity_min: float = 0.92,
    style_delta_max: float = 0.15,
) -> DriftReport:
    anchor, frame = _load(anchor_png), _load(frame_png)
    if anchor.size != frame.size:
        return DriftReport(
            frame_index,
            0.0,
            1.0,
            False,
            (f"frame size {frame.size} differs from anchor {anchor.size}",),
        )
    locked = _similarity(_mask_out(anchor, motion_boxes), _mask_out(frame, motion_boxes))
    style = _style_delta(anchor, frame)
    reasons: list[str] = []
    if locked < locked_region_similarity_min:
        reasons.append(
            f"locked-region similarity {locked:.3f} below {locked_region_similarity_min} (composition drift)"  # noqa: E501
        )
    if style > style_delta_max:
        reasons.append(f"style delta {style:.3f} above {style_delta_max}")
    return DriftReport(frame_index, round(locked, 4), round(style, 4), not reasons, tuple(reasons))
