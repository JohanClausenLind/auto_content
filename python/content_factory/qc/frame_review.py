"""Deterministic findings about generated frames, and the contact sheet a reviewer looks at.

These checks exist because of specific failures that were obvious in the pictures and invisible
to the code. Each one is here for a reason that was measured, not imagined:

* **tonal collapse** — an illustration style crushed 31 % of pixels to near-black and left only
  36 % midtones, where a photograph runs 60-80 %. The frames looked like photocopies.
* **half-applied colour** — a style saying "monochrome, no colour" still left 39 % of pixels
  saturated, so the image was neither colour nor grey. That mismatch is what reads as *weird*.
* **edge intrusion** — figures entering from the frame edges, or a subject cut in half, because
  the staging did not fit the lens.
* **background churn** — consecutive frames of one film inventing a new street each time, which
  is what a sequence looks like when nothing anchors it.

What they cannot judge is whether a frame makes bodily or narrative sense: whether these are the
same two people as the last frame, whether the pose is anatomically possible, whether there are
two figures where two were staged. That is what the contact sheet and a reviewer are for.
"""

from __future__ import annotations

import io
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict, cast

from PIL import Image, ImageChops, ImageDraw, ImageStat

from content_factory.schemas.review import FrameFinding

# Thresholds. Deliberately loose: a finding should mean "look at this", not "this is wrong".
MIDTONE_MIN = 0.30
"""A floor and never a ceiling, and the wording below says so on purpose.

The detail string used to read "a photograph runs 60-80%", which invites somebody to add the
matching maximum. Measured on nine frames of this model's output, scored by eye first
(2026-09-10): the best frame in the set — a honeybee on lavender, 9/10 — is **0.960** midtones,
and the worst — six consistent views of an object that is not what was asked for, 2/10 — is
**0.935**. The lowest reading in the whole set, **0.679**, belongs to a 4/10 frame. Midtone
fraction does not separate a good frame from a bad one here; it separates a *picture* from a
frame crushed to two tones, which is the only thing this check is for."""
"""Fraction of pixels between 60 and 195 luma. Below this the tonal range has collapsed."""
CLIP_BLACK_MAX = 0.35
"""Fraction crushed under luma 12."""
MONOCHROME_SAT_MAX = 0.12
"""For a style that asked for no colour: fraction of pixels above saturation 60."""
EDGE_INK_MAX = 0.55
"""Fraction of the 2 %-wide frame border that is subject rather than background."""
BACKGROUND_CHURN_MAX = 42.0
"""Mean absolute difference between consecutive frames outside the moving region."""

_ANALYSIS_SIZE = (640, 360)


def _load(png: bytes) -> Image.Image:
    return (
        Image.open(io.BytesIO(png)).convert("RGB").resize(_ANALYSIS_SIZE, Image.Resampling.LANCZOS)
    )


def _channel_values(img: Image.Image, channel: str | None = None) -> list[int]:
    """Every pixel of one channel as ints. Goes through ``tobytes`` rather than ``getdata``,
    which Pillow deprecates in 14, and which pyright cannot type as iterable anyway."""
    band = img.convert("L") if channel is None else img.convert("HSV").getchannel(channel)
    return list(band.tobytes())


def tonal_findings(png: bytes) -> list[FrameFinding]:
    """Is there a picture here, or has it been crushed to two tones?"""
    values = _channel_values(_load(png))
    n = len(values)
    midtones = sum(1 for v in values if 60 < v < 195) / n
    crushed = sum(1 for v in values if v < 12) / n
    return [
        FrameFinding(
            check="midtone_range",
            passed=midtones >= MIDTONE_MIN,
            severity="advisory",
            detail=f"{midtones:.0%} of pixels are midtones (under 30% is a crushed frame)",
            measured=round(midtones, 4),
            threshold=MIDTONE_MIN,
        ),
        FrameFinding(
            check="black_clipping",
            passed=crushed <= CLIP_BLACK_MAX,
            severity="advisory",
            detail=f"{crushed:.0%} of pixels are crushed to near-black",
            measured=round(crushed, 4),
            threshold=CLIP_BLACK_MAX,
        ),
    ]


COLOUR_SAT_MIN = 0.06
"""The least *any* colour picture has: of the pixels bright enough to carry a colour, the
fraction with saturation above 25.

The other direction of the same check, and it was missing. A style with no monochrome word in it
can still come back grey — the model simply does not apply the instruction — and a grey frame from
a colour brief is the same half-applied style the check below exists for, only more obvious.

Measured over one evening's 27 HiDream anchors (2026-09-09), the separation is not close:

    a sourdough loaf under a colour brief, returned grey     0.002
    a deep-ocean frame under a colour brief, returned grey   0.011
    a fogged grey "space" frame, three attempts running      0.014 - 0.040
    ---------------------------------------------------------------
    a genuinely dark frame that kept its colour              0.072
    the least saturated ordinary frame                       0.19

Both greyscale returns were bad pictures for other reasons too, which is the point: this catches a
model that has stopped following the prompt, cheaply, before a person is asked to look.
"""


def colour_findings(png: bytes, *, expect_monochrome: bool) -> list[FrameFinding]:
    """A style that asked for no colour and got some is a half-applied instruction, and looks it.

    So is a style that asked for colour and got none.
    """
    if not expect_monochrome:
        img = _load(png).convert("HSV")
        sat = list(img.getchannel("S").tobytes())
        val = list(img.getchannel("V").tobytes())
        # Among the pixels bright enough to carry a colour at all. A near-black pixel has no
        # meaningful hue, so counting the whole frame would mark every legitimately dark picture
        # as greyscale — an underwater frame lit by one beam is mostly black on purpose.
        lit = [s for s, v in zip(sat, val, strict=True) if v > 40]
        coloured = (sum(1 for s in lit if s > 25) / len(lit)) if lit else 1.0
        return [
            FrameFinding(
                check="colour_present",
                passed=coloured >= COLOUR_SAT_MIN,
                severity="blocker",
                detail=(
                    f"nothing in the style asked for monochrome and only {coloured:.1%} of pixels"
                    " carry any colour — the model returned a greyscale frame"
                ),
                measured=round(coloured, 4),
                threshold=COLOUR_SAT_MIN,
            )
        ]
    values = _channel_values(_load(png), "S")
    coloured = sum(1 for s in values if s > 60) / len(values)
    return [
        FrameFinding(
            check="monochrome_honoured",
            passed=coloured <= MONOCHROME_SAT_MAX,
            severity="blocker",
            detail=(
                f"the style asked for no colour and {coloured:.0%} of pixels are saturated — "
                "neither a colour image nor a grey one"
            ),
            measured=round(coloured, 4),
            threshold=MONOCHROME_SAT_MAX,
        )
    ]


BORDER_BAND_MAX = 0.08
"""How much of a frame may be flat bars along two opposite edges before it is a pillarbox.

An image model asked for a 16:9 frame sometimes draws a narrower picture and fills the sides with
one flat colour. It is never wanted, it is invisible to every other check here — the bars are mid
grey, so nothing is crushed and nothing is blown — and it is unmistakable once measured. Over one
evening's anchors the separation is total: the one pillarboxed frame put **60 %** of its width in
flat bars, and every other frame measured **0**.
"""


def _flat_run(img: Image.Image, *, vertical: bool, tolerance: int = 6) -> float:
    """The fraction of the frame taken by bars of **one flat colour** on both opposite edges.

    Flatness alone is not enough to say "bar": every column of a horizontal gradient is flat, and
    a plain sky is a legitimate picture. What makes a pad a pad is that the runs on the two
    opposite edges are flat, are the *same* colour as each other, and are not the colour of the
    picture between them.
    """
    width, height = img.size
    span = width if vertical else height

    def line(index: int) -> tuple[tuple[int, int], ...]:
        """One row or column's per-band (min, max). RGB, so `getextrema` gives one pair a band."""
        box = (index, 0, index + 1, height) if vertical else (0, index, width, index + 1)
        return tuple(cast("tuple[tuple[int, int], ...]", img.crop(box).getextrema()))

    def flat_as(index: int, colour: tuple[int, ...]) -> bool:
        return all(
            hi - lo <= tolerance and abs(lo - want) <= tolerance
            for (lo, hi), want in zip(line(index), colour, strict=True)
        )

    head, tail = line(0), line(span - 1)
    if any(hi - lo > tolerance for lo, hi in head) or any(hi - lo > tolerance for lo, hi in tail):
        return 0.0
    bar = tuple(lo for lo, _hi in head)
    if any(abs(lo - want) > tolerance for (lo, _hi), want in zip(tail, bar, strict=True)):
        return 0.0  # two different edge colours is a picture, not a pad
    lead = 0
    while lead < span and flat_as(lead, bar):
        lead += 1
    trail = 0
    while trail < span - lead and flat_as(span - 1 - trail, bar):
        trail += 1
    # A frame that is flat all the way across has no picture in it to be padded around; that is a
    # different fault and `tonal_findings` is the one that measures it.
    return (lead + trail) / span if lead and trail and lead + trail < span else 0.0


def border_findings(png: bytes) -> list[FrameFinding]:
    """A picture drawn smaller than the frame it was asked for, with flat bars beside it."""
    img = _load(png)
    img = img.resize((160, 90) if img.width >= img.height else (90, 160))
    worst = max(_flat_run(img, vertical=True), _flat_run(img, vertical=False))
    return [
        FrameFinding(
            check="fills_the_frame",
            passed=worst <= BORDER_BAND_MAX,
            severity="blocker",
            detail=(
                f"{worst:.0%} of the frame is flat bars on two opposite edges — the model drew a"
                " smaller picture and padded it"
            ),
            measured=round(worst, 4),
            threshold=BORDER_BAND_MAX,
        )
    ]


def edge_findings(png: bytes) -> list[FrameFinding]:
    """Subject matter jammed against the frame border: a figure cut off, or limbs entering from
    outside, which is what a shot looks like when the staging does not fit the lens."""
    img = _load(png).convert("L")
    w, h = img.size
    band = max(2, round(min(w, h) * 0.02))
    whole = ImageStat.Stat(img).mean[0]
    inner = img.crop((band, band, w - band, h - band))
    border_mean = (
        whole * w * h - ImageStat.Stat(inner).mean[0] * inner.size[0] * inner.size[1]
    ) / (w * h - inner.size[0] * inner.size[1])
    # Subject matter is darker than sky/road in these films; a border far from the inner mean is
    # either a vignette or something standing in it.
    divergence = abs(border_mean - whole) / 255.0
    return [
        FrameFinding(
            check="frame_edges_clear",
            passed=divergence <= EDGE_INK_MAX,
            severity="advisory",
            detail=f"frame border differs from the image mean by {divergence:.0%}",
            measured=round(divergence, 4),
            threshold=EDGE_INK_MAX,
        )
    ]


def continuity_finding(previous: bytes, current: bytes) -> FrameFinding:
    """Do consecutive frames share a world? This is the check that would have caught thirty
    drawings each inventing their own street."""
    a, b = _load(previous).convert("L"), _load(current).convert("L")
    churn = ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
    return FrameFinding(
        check="continuity",
        passed=churn <= BACKGROUND_CHURN_MAX,
        severity="advisory",
        detail=(
            f"this frame differs from the previous one by {churn:.1f} "
            f"(a held drawing is ~0, a cut in the same world ~30, a new world 55+)"
        ),
        measured=round(churn, 2),
        threshold=BACKGROUND_CHURN_MAX,
    )


SET_DISTANCE_MAX = 0.11
"""How far one frame may sit from the rest of its set before it is the odd one out.

Distance is palette and tone, scaled 0-1 (see `_descriptor`), and the number is a frame's *median*
distance to the others. Calibrated against this machine's own output rather than guessed, because
the first guess (0.22) was wrong in the direction that matters — it would have flagged nothing:

===============================================  =========================
six views of one pinecone (`ps1c-pinecone`)      0.033 - 0.046 per frame
six views of one whelk (`ps3c-whelk`)            0.033 - 0.053 per frame
six views of one amber (`ps2c-amber`)            0.020 median, the *lowest*
one whelk drawing dropped into the pinecone set  **0.140**
two frames from genuinely different runs         0.111 - 0.167 pairwise
===============================================  =========================

So 0.11 sits at twice the worst legitimate reading and a fifth below a real intruder. The amber
entry is the one to remember: that set's palette went **uniformly tan**, a fault obvious by eye
and scored here as the most consistent set of the three — because it was. Low distance means "one
world", never "good"."""


def _descriptor(png: bytes) -> tuple[list[float], list[float]]:
    """What "the same world" is measurable as: the palette and the tonal shape.

    A coarse hue histogram (12 buckets, weighted by saturation so a grey wall does not vote on
    hue) and a 16-bucket luma histogram. Deliberately coarse — this must not fire because a
    subject moved, only because the *world* changed.
    """
    img = _load(png)
    hues = [0.0] * 12
    for hue, saturation in zip(_channel_values(img, "H"), _channel_values(img, "S"), strict=True):
        hues[min(hue * 12 // 256, 11)] += saturation / 255.0
    hue_total = sum(hues) or 1.0
    luma = [0.0] * 16
    for value in _channel_values(img):
        luma[min(value * 16 // 256, 15)] += 1.0
    luma_total = sum(luma) or 1.0
    return ([h / hue_total for h in hues], [v / luma_total for v in luma])


def _distance(a: tuple[list[float], list[float]], b: tuple[list[float], list[float]]) -> float:
    """Half the total variation distance of both histograms, averaged: 0 identical, 1 disjoint."""
    hue = sum(abs(x - y) for x, y in zip(a[0], b[0], strict=True)) / 2
    luma = sum(abs(x - y) for x, y in zip(a[1], b[1], strict=True)) / 2
    return (hue + luma) / 2


class ConsistencyPair(TypedDict):
    a: str
    b: str
    distance: float


class ConsistencyReport(TypedDict):
    """The whole comparison, written beside the images so a reviewer can cite it."""

    frames: int
    pairs_compared: int
    median_distance: float
    per_frame_median: dict[str, float]
    worst_pair: ConsistencyPair | None
    threshold: float
    spread: bool
    """The set has no consistent core: the *typical* pair already disagrees. Then there is no odd
    one out to name, and `outliers` is empty — see `consistency_matrix`."""
    outliers: list[str]
    pairs: list[ConsistencyPair]


def consistency_matrix(pngs: Sequence[tuple[str, bytes]]) -> ConsistencyReport:
    """Every frame against **every other frame**, not just its neighbour.

    `continuity_finding` compares consecutive frames, which answers "was there a cut here" and
    cannot answer "do all six of these belong to one set" — a set can drift a little at each step
    and end somewhere else entirely, with every consecutive pair looking fine. So this is all
    pairs: 15 comparisons for a six-picture story.

    Each frame gets its **median distance to the others**, which is what identifies the odd one
    out: one frame that disagrees with everything has a high median, while a set with two equal
    halves has no outlier and says so instead of blaming one side.

    What it cannot judge, and the reason a reviewer still looks: whether the *subject* is the same
    subject. Two drawings of different objects in the same palette and light score as consistent,
    which is precisely the failure this machine produced when "honey-coloured" drew jars of honey.
    """
    ids = [frame_id for frame_id, _ in pngs]
    descriptors = [_descriptor(png) for _, png in pngs]
    pairs: list[ConsistencyPair] = []
    distances: list[float] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            distance = _distance(descriptors[i], descriptors[j])
            distances.append(distance)
            pairs.append({"a": ids[i], "b": ids[j], "distance": round(distance, 4)})
    per_frame: dict[str, float] = {}
    for i, frame_id in enumerate(ids):
        others = [_distance(descriptors[i], descriptors[j]) for j in range(len(ids)) if j != i]
        per_frame[frame_id] = round(statistics.median(others), 4) if others else 0.0
    worst = pairs[distances.index(max(distances))] if pairs else None
    median = round(statistics.median(distances), 4) if distances else 0.0
    # An outlier is only meaningful against a set that agrees with itself. When the *typical* pair
    # is already past the threshold there is no centre to be far from, and naming every frame an
    # outlier is both useless and wrong — measured on `w-iceberg`, an image-set of six
    # deliberately different viewpoints: median 0.243, and all six "outliers". A set is allowed
    # to be varied on purpose; what it cannot hide is one frame that left the others behind.
    spread = median > SET_DISTANCE_MAX
    return {
        "frames": len(ids),
        "pairs_compared": len(pairs),
        "median_distance": median,
        "per_frame_median": per_frame,
        "worst_pair": worst,
        "threshold": SET_DISTANCE_MAX,
        "spread": spread,
        "outliers": []
        if spread
        else sorted(f for f, d in per_frame.items() if d > SET_DISTANCE_MAX),
        "pairs": pairs,
    }


def consistency_findings(pngs: Sequence[tuple[str, bytes]]) -> dict[str, FrameFinding]:
    """One finding per frame: how far it sits from the rest of the set."""
    if len(pngs) < 2:
        return {}
    matrix = consistency_matrix(pngs)
    per_frame = matrix["per_frame_median"]
    spread = matrix["spread"]
    median = matrix["median_distance"]
    out: dict[str, FrameFinding] = {}
    for frame_id, distance in per_frame.items():
        if spread:
            # One statement about the set, repeated on each frame rather than six accusations.
            detail = (
                f"this set has no consistent core: the typical pair sits {median:.3f} apart "
                f"(one world measures 0.02-0.05). Deliberate for a set of different viewpoints, "
                f"wrong for frames meant to share a world — which of the two it is, is what "
                f"looking decides"
            )
        else:
            detail = (
                f"palette and tone sit {distance:.3f} from the median of the other "
                f"{len(pngs) - 1} frame(s), which agree with each other at {median:.3f} "
                f"(a frame from another set measures 0.14). Says nothing about whether the "
                f"subject is the same subject, or whether the set is any good"
            )
        out[frame_id] = FrameFinding(
            check="set_consistency",
            passed=not spread and distance <= SET_DISTANCE_MAX,
            severity="advisory",
            detail=detail,
            measured=distance,
            threshold=SET_DISTANCE_MAX,
        )
    return out


def review_findings(
    pngs: Sequence[tuple[str, bytes]], *, expect_monochrome: bool = False
) -> dict[str, list[FrameFinding]]:
    """Every finding for every frame, in frame order."""
    out: dict[str, list[FrameFinding]] = {}
    consistency = consistency_findings(pngs)
    previous: bytes | None = None
    for frame_id, png in pngs:
        findings = tonal_findings(png)
        findings += colour_findings(png, expect_monochrome=expect_monochrome)
        findings += border_findings(png)
        findings += edge_findings(png)
        if previous is not None:
            findings.append(continuity_finding(previous, png))
        if frame_id in consistency:
            findings.append(consistency[frame_id])
        out[frame_id] = findings
        previous = png
    return out


def contact_sheet(
    pngs: Sequence[tuple[str, bytes]],
    dest: Path,
    *,
    findings: dict[str, list[FrameFinding]] | None = None,
    columns: int = 3,
    tile_width: int = 520,
) -> bytes:
    """The sheet a reviewer actually looks at: every frame, in order, labelled, with the count of
    findings that want attention. Large enough to judge a face, small enough to take in at once."""
    if not pngs:
        msg = "no frames to review"
        raise ValueError(msg)
    findings = findings or {}
    first = Image.open(io.BytesIO(pngs[0][1]))
    tile_h = round(tile_width * first.height / first.width)
    label_h = 22
    cols = min(columns, len(pngs))
    rows = (len(pngs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_width, rows * (tile_h + label_h)), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    for i, (frame_id, png) in enumerate(pngs):
        x = (i % cols) * tile_width
        y = (i // cols) * (tile_h + label_h)
        tile = (
            Image.open(io.BytesIO(png))
            .convert("RGB")
            .resize((tile_width, tile_h), Image.Resampling.LANCZOS)
        )
        sheet.paste(tile, (x, y + label_h))
        flagged = sum(1 for f in findings.get(frame_id, []) if not f.passed)
        label = f"{i + 1}. {frame_id}" + (f"   ({flagged} to check)" if flagged else "")
        draw.text((x + 6, y + 5), label, fill=(255, 210, 120) if flagged else (240, 240, 240))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    return buf.getvalue()
