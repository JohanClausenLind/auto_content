"""Pixel QC on decoded frames: APCA of read text, edge contrast of data lines, per sample time."""

from __future__ import annotations

import io
import math
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

from content_factory.explainer.color import apca_lc, rgb_of
from content_factory.explainer.colors import token_hex
from content_factory.explainer.layout import role_px, synthetic_id
from content_factory.explainer.tokens_gen import TOKENS
from content_factory.schemas.explainer import (
    AnnotateAction,
    ChartTemplate,
    CompiledExplainerScene,
    EvidenceDataset,
    ExplainerRenderBundle,
    PixelBox,
    Scene,
    SeriesBinding,
)

Rgb = tuple[int, int, int]
Point = tuple[float, float]
INK_DISTANCE = 60.0
BACKGROUND_MARGIN_PX = 12
EDGE_SAMPLES = 24
QC_DECODE_WIDTH = 360
STROKE_PX_AT_360: int = TOKENS["lines"]["data_stroke_min_px_at_360"]
DEEMPHASIS_ALPHA: float = next(
    t["alpha"] for t in TOKENS["color"]["state"] if t["id"] == "state.deemphasis"
)
_FFMPEG = "ffmpeg"


class QcUnmeasurableError(ValueError):
    """The check could not run on this frame; the finding records why with passed=None."""


class Decoder(Protocol):
    """decode_frames' shape, so a test can hand QC drawn frames instead of an mp4."""

    def __call__(
        self, mp4: Path, timestamps_ms: Sequence[int], *, width: int | None = None
    ) -> list[Image.Image]: ...


Fetch = Callable[..., Image.Image | None]


@dataclass(frozen=True)
class QcFinding:
    check: str
    scene_id: str
    entity_id: str | None
    at_ms: int
    measured: float | None
    threshold: float
    passed: bool | None
    evidence: str


def decode_frames(
    mp4: Path, timestamps_ms: Sequence[int], *, width: int | None = None
) -> list[Image.Image]:
    """One RGB frame per timestamp via ffmpeg; width rescales with the aspect kept (scale=W:-2)."""
    frames: list[Image.Image] = []
    for ms in timestamps_ms:
        args = [_FFMPEG, "-loglevel", "error", "-ss", f"{ms / 1000:.3f}", "-i", str(mp4)]
        if width is not None:
            args += ["-vf", f"scale={width}:-2"]
        args += ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
        completed = subprocess.run(args, capture_output=True, check=False)  # noqa: S603  ffmpeg from PATH, argument array
        if completed.returncode != 0 or not completed.stdout:
            detail = completed.stderr.decode(errors="replace").strip() or "no frame"
            msg = f"ffmpeg could not decode {mp4.name} at {ms} ms: {detail}"
            raise QcUnmeasurableError(msg)
        frames.append(Image.open(io.BytesIO(completed.stdout)).convert("RGB"))
    return frames


def encode_like_youtube(src: Path, out: Path) -> Path:
    """Re-encode at a realistic delivery bitrate so QC sees what the platform would show."""
    args = [
        _FFMPEG, "-y", "-loglevel", "error", "-i", str(src),
        "-c:v", "libx264", "-b:v", "8M", "-maxrate", "8M", "-bufsize", "16M",
        "-preset", "medium", "-pix_fmt", "yuv420p", str(out),
    ]  # fmt: skip
    subprocess.run(args, check=True)  # noqa: S603  ffmpeg from PATH, argument array
    return out


def text_contrast_lc(frame: Image.Image, box: PixelBox, ink_hex: str) -> float:
    """APCA Lc of the ink actually rendered in the box against the median of its surroundings."""
    pixels = np.asarray(frame.convert("RGB"), dtype=np.float64)
    ink = np.array(rgb_of(ink_hex), dtype=np.float64)
    inner = _crop(pixels, box, 0)
    inner_mask = _near(inner, ink)
    if not inner_mask.any():
        msg = f"no pixels within {INK_DISTANCE:.0f} of ink {ink_hex} in {_describe(box)}"
        raise QcUnmeasurableError(msg)
    outer = _crop(pixels, box, BACKGROUND_MARGIN_PX)
    background = outer[~_near(outer, ink)]
    if background.size == 0:
        msg = f"every pixel around {_describe(box)} is ink-coloured; no background to measure"
        raise QcUnmeasurableError(msg)
    return apca_lc(_median_rgb(inner[inner_mask]), _median_rgb(background))


def line_edge_contrast(frame: Image.Image, points: Sequence[Point], stroke_px: float) -> float:
    """Mean |luminance step| across the polyline at 24 points, sampling ±stroke_px, 0..255."""
    luminance = _luminance(np.asarray(frame.convert("RGB"), dtype=np.float64))
    segments = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
    lengths = [math.dist(a, b) for a, b in segments]
    total = sum(lengths)
    if total <= 0:
        msg = "the polyline has no length"
        raise QcUnmeasurableError(msg)
    steps: list[float] = []
    for k in range(EDGE_SAMPLES):
        target = (k + 0.5) / EDGE_SAMPLES * total
        travelled = 0.0
        for (a, b), length in zip(segments, lengths, strict=True):
            if travelled + length < target and length > 0:
                travelled += length
                continue
            if length == 0:
                continue
            t = (target - travelled) / length
            x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
            centre = _sample(luminance, x, y)
            plus = _sample(luminance, x + nx * stroke_px, y + ny * stroke_px)
            minus = _sample(luminance, x - nx * stroke_px, y - ny * stroke_px)
            steps.append((abs(centre - plus) + abs(centre - minus)) / 2)
            break
    return float(np.mean(steps))


def series_points(
    chart: ChartTemplate, dataset: EvidenceDataset, binding: SeriesBinding, plot: PixelBox
) -> list[Point]:
    """Where a series is drawn: one nice()d value axis shared by all series, scalePoint x."""
    columns = [c.name for c in dataset.columns]
    y_lo, y_hi = shared_value_domain(chart, dataset)
    rows = list(dataset.rows)
    if chart.series_field is not None:
        column = columns.index(chart.series_field)
        rows = [r for r in rows if str(r.values[column]) == binding.value]
    xi = columns.index(chart.x.field)
    yi = columns.index(_value_column(chart, binding))
    pairs = [(r.values[xi], r.values[yi]) for r in rows if isinstance(r.values[yi], int | float)]
    if not pairs:
        return []
    ys = [float(y) for _, y in pairs]  # type: ignore[arg-type]
    kind = chart.x.kind if chart.chart_kind == "scatter" else "nominal"
    xs = _x_positions([x for x, _ in pairs], kind, plot)
    return [
        (x, _linear(y, y_lo, y_hi, plot.y + plot.height, plot.y))
        for x, y in zip(xs, ys, strict=True)
    ]


def _value_column(chart: ChartTemplate, binding: SeriesBinding) -> str:
    """Long data plots y.field; wide data plots the column the series binding names."""
    if chart.series_field is None and binding.value:
        return binding.value
    return chart.y.field


def shared_value_domain(chart: ChartTemplate, dataset: EvidenceDataset) -> tuple[float, float]:
    """The renderer's value domain: every series' values, zero baseline when asked, then nice()."""
    columns = [c.name for c in dataset.columns]
    values: list[float] = []
    for binding in chart.series:
        column = columns.index(_value_column(chart, binding))
        for row in dataset.rows:
            cell = row.values[column]
            if isinstance(cell, int | float):
                values.append(float(cell))
    if not values:
        return 0.0, 1.0
    lo = min(0.0, *values) if chart.baseline_zero else min(values)
    return nice_domain(lo, max(values))


def check_rendered(
    bundle: ExplainerRenderBundle,
    mp4: Path,
    *,
    text_lc_min: float = 75.0,
    edge_min: float = 40.0,
    decode: Decoder | None = None,
) -> list[QcFinding]:
    """Sample each scene at action ends and hold midpoints; passed=None when a check cannot run."""
    fetch: Fetch = _decode_or_none if decode is None else partial(_decode_with, decode)
    fps = bundle.timeline.fps
    scenes = {s.scene_id: s for s in bundle.spec.scenes}
    datasets = {d.dataset_id: d for d in bundle.datasets}
    assets = {a.asset_id: a for a in bundle.spec.assets}
    last_frame = bundle.timeline.total_frames - 1
    findings: list[QcFinding] = []
    for compiled in bundle.timeline.scenes:
        scene = scenes[compiled.scene_id]
        texts = {b.entity_id for b in compiled.boxes if b.font_px is not None}
        shown: set[str] = set()
        read: set[str] = set()
        for frame in sorted(sample_frames(compiled)):
            at_ms = math.floor(min(frame, last_frame) * 1000 / fps)
            visible = visible_at(scene, compiled, frame)
            dimmed = deemphasised_at(compiled, frame, visible)
            shown |= visible & texts
            read |= (visible - dimmed) & texts
            # Dimmed text is not being read at this frame; it must be read in full somewhere.
            findings += _text_findings(compiled, visible - dimmed, mp4, at_ms, text_lc_min, fetch)
            template = scene.template
            if isinstance(template, ChartTemplate) and template.chart_kind == "line":
                dataset = datasets.get(assets[template.dataset_asset_id].dataset_id or "")
                # De-emphasised context is drawn at the token's alpha on purpose; its floor
                # scales with it, while every line being read keeps the full one.
                dimmed = deemphasised_at(compiled, frame, visible)
                floors = {
                    e: edge_min * DEEMPHASIS_ALPHA if e in dimmed else edge_min for e in visible
                }
                findings += _edge_findings(
                    bundle, compiled, template, dataset, visible, mp4, at_ms, floors, fetch
                )
        at_ms = math.floor(compiled.start_frame * 1000 / fps)
        for entity_id in sorted(shown - read):
            why = "shown only while de-emphasised: never read at full strength"
            findings.append(
                _finding("text_apca", compiled, entity_id, at_ms, 0.0, text_lc_min, why)
            )
    return findings


def sample_frames(compiled: CompiledExplainerScene) -> set[int]:
    """Action ends and hold midpoints: where a reveal has settled and where the eye rests."""
    # An action may end on the boundary frame, which already shows the next scene: stay inside.
    last = compiled.start_frame + compiled.duration_frames - 1
    frames: set[int] = set()
    for action in compiled.actions:
        frames.add(min(last, action.end_frame))
        if action.action == "hold":
            frames.add(min(last, (action.start_frame + action.end_frame) // 2))
    return frames or {compiled.start_frame}


def visible_at(scene: Scene, compiled: CompiledExplainerScene, frame: int) -> set[str]:
    """Entities on screen at a frame: reveals count once finished, hides as soon as they start."""
    visible = set(scene.initial_visible)
    annotations = {
        (b.beat_id, k): synthetic_id(f"annot{k}", b.beat_id)
        for b in scene.beats
        for k, a in enumerate(b.actions)
        if isinstance(a, AnnotateAction)
    }
    for action in compiled.actions:
        if action.action == "reveal" and action.end_frame <= frame:
            visible.update(action.targets)
        elif action.action == "hide" and action.start_frame <= frame:
            visible.difference_update(action.targets)
        elif action.action == "annotate" and action.end_frame <= frame:
            visible.add(annotations[action.beat_id, action.index])
    return visible


def _text_findings(
    compiled: CompiledExplainerScene,
    visible: set[str],
    mp4: Path,
    at_ms: int,
    lc_min: float,
    fetch: Fetch,
) -> list[QcFinding]:
    boxes = [b for b in compiled.boxes if b.font_px is not None and b.entity_id in visible]
    if not boxes:
        return []
    frame = fetch(mp4, at_ms)
    findings: list[QcFinding] = []
    for box in boxes:
        ink = ink_hex_for(box.font_px or 0)
        if frame is None:
            findings.append(
                _finding("text_apca", compiled, box.entity_id, at_ms, None, lc_min, "decode failed")
            )
            continue
        try:
            lc = text_contrast_lc(frame, box.box, ink)
        except QcUnmeasurableError as why:
            findings.append(
                _finding("text_apca", compiled, box.entity_id, at_ms, None, lc_min, str(why))
            )
            continue
        polarity = "light on dark" if lc < 0 else "dark on light"
        evidence = f"Lc {lc:.1f} ({polarity}) for ink {ink} in {_describe(box.box)}"
        findings.append(
            _finding("text_apca", compiled, box.entity_id, at_ms, abs(lc), lc_min, evidence)
        )
    return findings


def _edge_findings(
    bundle: ExplainerRenderBundle,
    compiled: CompiledExplainerScene,
    chart: ChartTemplate,
    dataset: EvidenceDataset | None,
    visible: set[str],
    mp4: Path,
    at_ms: int,
    floors: dict[str, float],
    fetch: Fetch,
) -> list[QcFinding]:
    series = [s for s in chart.series if s.entity_id in visible]
    if not series:
        return []
    plot = next((r.box for r in compiled.regions if r.name == "plot"), None)
    if dataset is None or plot is None:
        why = "no dataset in the bundle" if dataset is None else "no plot region"
        return [
            _finding("line_edge", compiled, s.entity_id, at_ms, None, floors[s.entity_id], why)
            for s in series
        ]
    frame = fetch(mp4, at_ms, width=QC_DECODE_WIDTH)
    if frame is None:
        return [
            _finding(
                "line_edge",
                compiled,
                s.entity_id,
                at_ms,
                None,
                floors[s.entity_id],
                "decode failed",
            )
            for s in series
        ]
    sx, sy = frame.width / bundle.timeline.width, frame.height / bundle.timeline.height
    findings: list[QcFinding] = []
    for binding in series:
        edge_min = floors[binding.entity_id]
        points = [(x * sx, y * sy) for x, y in series_points(chart, dataset, binding, plot)]
        try:
            step = line_edge_contrast(frame, points, STROKE_PX_AT_360)
        except QcUnmeasurableError as why:
            findings.append(
                _finding("line_edge", compiled, binding.entity_id, at_ms, None, edge_min, str(why))
            )
            continue
        evidence = (
            f"mean luminance step {step:.1f} over {len(points)} points at {frame.width} px wide"
        )
        if edge_min < max(floors.values()):
            evidence += (
                f", de-emphasised to alpha {DEEMPHASIS_ALPHA} so the floor is {edge_min:.1f}"
            )
        findings.append(
            _finding("line_edge", compiled, binding.entity_id, at_ms, step, edge_min, evidence)
        )
    return findings


def deemphasised_at(compiled: CompiledExplainerScene, frame: int, ids: set[str]) -> set[str]:
    """Which of ids sit at the de-emphasis alpha by this frame, folding actions as state.ts does."""
    highlighted: set[str] = set()
    kept: set[str] | None = None
    focus: str | None = None
    for action in (a for a in compiled.actions if a.start_frame <= frame):
        targets = set(action.targets)
        if action.action == "highlight":
            highlighted |= targets
        elif action.action == "clear_highlight":
            highlighted = highlighted - targets if targets else set()
            if not targets or focus in targets:
                kept, focus = None, None
        elif action.action == "focus" and action.targets:
            if focus is not None and focus != action.targets[0]:
                highlighted.discard(focus)
            focus = action.targets[0]
            highlighted.add(focus)
            kept = {focus}
        elif action.action == "isolate":
            kept = targets
    return {
        e
        for e in ids
        if (kept is not None and e not in kept) or (highlighted and e not in highlighted)
    }


def _finding(
    check: str,
    compiled: CompiledExplainerScene,
    entity_id: str | None,
    at_ms: int,
    measured: float | None,
    threshold: float,
    evidence: str,
) -> QcFinding:
    passed = None if measured is None else measured >= threshold
    return QcFinding(
        check, compiled.scene_id, entity_id, at_ms, measured, threshold, passed, evidence
    )


def _decode_or_none(mp4: Path, at_ms: int, width: int | None = None) -> Image.Image | None:
    return _decode_with(decode_frames, mp4, at_ms, width)


def _decode_with(
    decode: Decoder, mp4: Path, at_ms: int, width: int | None = None
) -> Image.Image | None:
    try:
        return decode(mp4, [at_ms], width=width)[0]
    except QcUnmeasurableError:
        return None


def _x_positions(xs: Sequence[object], kind: str, plot: PixelBox) -> list[float]:
    x0, x1 = plot.x, plot.x + plot.width
    if kind in {"quantitative", "temporal"} and all(isinstance(x, int | float) for x in xs):
        values = [float(x) for x in xs]  # type: ignore[arg-type]
        return [_linear(v, min(values), max(values), x0, x1) for v in values]
    # d3 scalePoint with padding 0.5, as the renderer draws it: step = range / n.
    domain = list(dict.fromkeys(str(x) for x in xs))
    step = (x1 - x0) / len(domain)
    return [x0 + (domain.index(str(x)) + POINT_PADDING) * step for x in xs]


POINT_PADDING = 0.5


def nice_domain(lo: float, hi: float, count: int = 10) -> tuple[float, float]:
    """d3 scaleLinear().nice(): extend the domain to the tick step that gives ~count ticks."""
    if hi == lo:
        return lo, hi
    for _ in range(10):
        step = _tick_increment(lo, hi, count)
        if step == 0:
            break
        if step > 0:
            new_lo, new_hi = math.floor(lo / step) * step, math.ceil(hi / step) * step
        else:
            new_lo, new_hi = math.ceil(lo * step) / step, math.floor(hi * step) / step
        if (new_lo, new_hi) == (lo, hi):
            break
        lo, hi = new_lo, new_hi
    return lo, hi


def _tick_increment(start: float, stop: float, count: int) -> float:
    step = (stop - start) / max(0, count)
    power = math.floor(math.log10(step))
    error = step / 10**power
    factor = (
        10
        if error >= math.sqrt(50)
        else 5
        if error >= math.sqrt(10)
        else 2
        if error >= math.sqrt(2)
        else 1
    )
    if power >= 0:
        return factor * 10**power
    return -(10**-power) / factor


def _linear(value: float, d0: float, d1: float, r0: float, r1: float) -> float:
    if d1 == d0:
        return (r0 + r1) / 2
    return r0 + (value - d0) / (d1 - d0) * (r1 - r0)


def _crop(pixels: np.ndarray, box: PixelBox, margin: int) -> np.ndarray:
    h, w = pixels.shape[:2]
    x0, y0 = max(0, box.x - margin), max(0, box.y - margin)
    x1, y1 = min(w, box.x + box.width + margin), min(h, box.y + box.height + margin)
    return pixels[y0:y1, x0:x1].reshape(-1, 3)


def _near(pixels: np.ndarray, ink: np.ndarray) -> np.ndarray:
    return np.sqrt(((pixels - ink) ** 2).sum(axis=1)) <= INK_DISTANCE


def _median_rgb(pixels: np.ndarray) -> Rgb:
    r, g, b = (round(float(v)) for v in np.median(pixels, axis=0))
    return r, g, b


def _luminance(pixels: np.ndarray) -> np.ndarray:
    return 0.2126 * pixels[..., 0] + 0.7152 * pixels[..., 1] + 0.0722 * pixels[..., 2]


def _sample(luminance: np.ndarray, x: float, y: float) -> float:
    h, w = luminance.shape
    xi = min(w - 1, max(0, round(x)))
    yi = min(h - 1, max(0, round(y)))
    return float(luminance[yi, xi])


def _describe(box: PixelBox) -> str:
    return f"box ({box.x},{box.y}) {box.width}x{box.height}"


def ink_hex_for(font_px: int) -> str:
    return token_hex("ui.ink.primary" if font_px >= role_px("body") else "ui.ink.secondary")
