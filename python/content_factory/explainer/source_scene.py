"""Source scenes: camera and quote-highlight keys resolved from a capture manifest and its tiles."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from statistics import median

import numpy as np

from content_factory.explainer.color import Oklch, apca_lc, oklch_to_srgb
from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.layout import region
from content_factory.explainer.ocr import crop_rect
from content_factory.explainer.passages import Tile
from content_factory.explainer.timing import TimedAction, reading_ms, to_frames
from content_factory.explainer.tokens_gen import TOKENS
from content_factory.schemas.explainer import (
    MAX_ZOOM,
    CameraKey,
    CaptureAsset,
    CaptureQuote,
    CaptureSection,
    HighlightKey,
    PageRect,
    PixelBox,
    QuoteAction,
    Scene,
    ScrollToAction,
    SourceDocumentTemplate,
    TargetAction,
    VisualSpec,
)

Rgb = tuple[int, int, int]
Durations = dict[tuple[str, int], int]
ORIENTATION_MS = 1200
SCROLL_BASE_MS = 600
SCROLL_MS_PER_1000_PX = 250
SCROLL_MAX_MS = 2400
ZOOM_MS: int = TOKENS["motion"]["duration_ms"]["zoom"]
SETTLE_MS = 300
LINE_SWEEP_MS = 120
SWEEP_TAIL_MS = 200
CLEAR_MS = 250
SECTION_GUTTER_PX = 48
QUOTE_SPAN = 0.7
HIGHLIGHT_PAD_PX = 4
# Highest first: the strongest amber that still leaves the text at Lc 75 wins (DESIGN_SYSTEM.md).
ALPHA_FAMILY = (0.36, 0.30, 0.24, 0.18)
INK_FRACTION = 0.10
TEXT_LC_MIN = 75.0
_EMPHASIS = next(t for t in TOKENS["color"]["state"] if t["id"] == "state.emphasis")
AMBER: Rgb = oklch_to_srgb(Oklch(*_EMPHASIS["oklch"]))
EASING: dict[str, tuple[float, float, float, float]] = {
    name: (float(v[0]), float(v[1]), float(v[2]), float(v[3]))
    for name, v in TOKENS["motion"]["easing"].items()
}


@dataclass(frozen=True)
class CameraState:
    """scroll: the page pixel at the region's top-left; zoom 1 fits the page width to the region."""

    scroll_x: float
    scroll_y: float
    zoom: float


@dataclass(frozen=True)
class PageGeometry:
    region: PixelBox
    page_width: float
    page_height: float

    @property
    def base_scale(self) -> float:
        return self.region.width / self.page_width

    def scale(self, zoom: float) -> float:
        return self.base_scale * zoom

    def visible(self, zoom: float) -> tuple[float, float]:
        """Page pixels the region shows at this zoom: (width, height)."""
        s = self.scale(zoom)
        return self.region.width / s, self.region.height / s

    def clamp(self, state: CameraState) -> CameraState:
        vw, vh = self.visible(state.zoom)
        x = min(max(0.0, state.scroll_x), max(0.0, self.page_width - vw))
        y = min(max(0.0, state.scroll_y), max(0.0, self.page_height - vh))
        return CameraState(round(x, 2), round(y, 2), round(state.zoom, 4))

    def section_view(
        self, section: CaptureSection, scroll_x: float, zoom: float, gutter_px: float
    ) -> CameraState:
        return self.clamp(CameraState(scroll_x, section.scroll_y_px - gutter_px, zoom))

    def focus(self, quote: CaptureQuote, zoom: float | None = None) -> CameraState:
        """Centre the quote with a line of context above; zoom so its widest line spans ~70 %."""
        rects = quote.line_rects
        x0, x1 = min(r.x for r in rects), max(r.x + r.width for r in rects)
        y0, y1 = min(r.y for r in rects), max(r.y + r.height for r in rects)
        line_h = median(r.height for r in rects)
        if zoom is None:
            widest = max(r.width for r in rects)
            fit_w = QUOTE_SPAN * self.region.width / (widest * self.base_scale)
            fit_h = self.region.height / ((y1 - y0 + 2 * line_h) * self.base_scale)
            zoom = min(MAX_ZOOM, max(1.0, min(fit_w, fit_h)))
        vw, vh = self.visible(zoom)
        scroll_y = min((y0 + y1) / 2 - vh / 2, y0 - line_h)
        return self.clamp(CameraState((x0 + x1) / 2 - vw / 2, scroll_y, zoom))


@dataclass(frozen=True)
class SourceMove:
    """One action's camera intent: travel from origin to target over move_ms, then pause."""

    beat_id: str
    index: int
    action: str
    origin: CameraState
    target: CameraState
    move_ms: int
    duration_ms: int
    quote: CaptureQuote | None = None
    rgba: str = ""


@dataclass(frozen=True)
class SourcePlan:
    scene_id: str
    geometry: PageGeometry
    initial: CameraState
    moves: tuple[SourceMove, ...]

    def durations_ms(self) -> Durations:
        return {(m.beat_id, m.index): m.duration_ms for m in self.moves}

    def tracks(
        self, actions: Sequence[TimedAction], fps: int, scene_start_frame: int
    ) -> tuple[tuple[CameraKey, ...], tuple[HighlightKey, ...]]:
        """Camera and highlight keys on timeline frames, from the spans timing gave each action."""
        spans = {(a.beat_id, a.index): a for a in actions}
        keys: list[CameraKey] = []
        highlights: list[HighlightKey] = []
        for move in self.moves:
            span = spans[move.beat_id, move.index]
            start = to_frames(span.start_ms, fps)
            if move.move_ms > 0 and move.target != move.origin:
                _push(keys, _key(start, move.origin, "hold"))
                _push(keys, _key(to_frames(span.start_ms + move.move_ms, fps), move.target, "move"))
            elif move.target != move.origin or not keys:
                _push(keys, _key(start, move.target, "hold"))
            if move.action == "highlight_quote" and move.quote is not None:
                highlights.append(_highlight(move, span.start_ms, fps))
            elif move.action == "clear_highlight":
                highlights = [_cleared(h, span.start_ms, fps) for h in highlights]
        if not keys:
            keys.append(_key(scene_start_frame, self.initial, "hold"))
        return tuple(keys), tuple(highlights)


def plan_source_scenes(spec: VisualSpec, captures: Iterable[CaptureAsset]) -> dict[str, SourcePlan]:
    """A plan per source_document scene, or every missing capture and bad target at once."""
    by_id = {c.capture_id: c for c in captures}
    assets = {a.asset_id: a for a in spec.assets}
    plans: dict[str, SourcePlan] = {}
    issues: list[ContractIssue] = []
    for i, scene in enumerate(spec.scenes):
        template = scene.template
        if not isinstance(template, SourceDocumentTemplate):
            continue
        where = f"VisualSpec.scenes[{i}]"
        capture_id = assets[template.capture_asset_id].capture_id or ""
        capture = by_id.get(capture_id)
        if capture is None:
            issues.append(
                ContractIssue(
                    kind="invalid_reference",
                    where=f"{where}.template.capture_asset_id",
                    message=f"scene {scene.scene_id} shows capture {capture_id}, not given.",
                    fix="pass its CaptureAsset (manifest, tiles) to compile_episode(captures=...).",
                    ids=(scene.scene_id, capture_id),
                )
            )
            continue
        try:
            plans[scene.scene_id] = plan_source_scene(scene, capture, region(scene, "page"), where)
        except EpisodeInvalidError as error:
            issues.extend(error.issues)
    if issues:
        raise EpisodeInvalidError(issues)
    return plans


def plan_source_scene(
    scene: Scene, capture: CaptureAsset, page: PixelBox, where: str = "scene"
) -> SourcePlan:
    template = scene.template
    assert isinstance(template, SourceDocumentTemplate)
    manifest = capture.manifest
    geometry = PageGeometry(page, float(manifest.viewport.width), manifest.page_height_px)
    sections = {s.section_id: s for s in manifest.sections}
    quotes = {q.quote_id: q for q in manifest.quotes}
    tiles = tuple(Tile(Path(t.path), int(t.y_px)) for t in capture.tiles)
    initial = geometry.section_view(sections[template.initial_section_id], 0.0, 1.0, 0.0)
    state = initial
    moves: list[SourceMove] = []
    issues: list[ContractIssue] = []
    for j, beat in enumerate(scene.beats):
        for k, action in enumerate(beat.actions):
            at = f"{where}.beats[{j}].actions[{k}]"
            target, move_ms, duration = state, 0, 0
            quote: CaptureQuote | None = None
            rgba = ""
            if action.action == "show_source":
                target, duration = initial, ORIENTATION_MS
            elif isinstance(action, ScrollToAction):
                section = sections[action.section_id]
                target = geometry.section_view(
                    section, state.scroll_x, state.zoom, SECTION_GUTTER_PX
                )
                move_ms = duration = scroll_ms(abs(target.scroll_y - state.scroll_y))
            elif isinstance(action, QuoteAction) and action.action == "focus_passage":
                quote = quotes[action.quote_id]
                target = geometry.focus(quote)
                move_ms, duration = ZOOM_MS, ZOOM_MS + reading_ms(quote.text)
            elif isinstance(action, QuoteAction):
                quote = quotes[action.quote_id]
                duration = SETTLE_MS + LINE_SWEEP_MS * len(quote.line_rects) + SWEEP_TAIL_MS
                try:
                    rgba = overlay_rgba(tiles, quote, manifest.capture_id, at)[0]
                except EpisodeInvalidError as error:
                    issues.extend(error.issues)
            elif action.action == "clear_highlight":
                duration = CLEAR_MS
            elif isinstance(action, TargetAction) and action.action in {"zoom_to", "pan_to"}:
                target_id = action.targets[0]
                if target_id in quotes:
                    q = quotes[target_id]
                    target = (
                        geometry.focus(q)
                        if action.action == "zoom_to"
                        else geometry.focus(q, state.zoom)
                    )
                elif target_id in sections:
                    zoom = 1.0 if action.action == "zoom_to" else state.zoom
                    x = 0.0 if action.action == "zoom_to" else state.scroll_x
                    target = geometry.section_view(sections[target_id], x, zoom, SECTION_GUTTER_PX)
                else:
                    issues.append(
                        _bad_target(at, scene, action.action, target_id, manifest.capture_id)
                    )
                    continue
                if action.action == "zoom_to":
                    move_ms = duration = ZOOM_MS
                else:
                    distance = math.hypot(
                        target.scroll_x - state.scroll_x, target.scroll_y - state.scroll_y
                    )
                    move_ms = duration = scroll_ms(distance)
            else:
                continue
            moves.append(
                SourceMove(
                    beat.beat_id, k, action.action, state, target, move_ms, duration, quote, rgba
                )
            )
            state = target
    if issues:
        raise EpisodeInvalidError(issues)
    return SourcePlan(scene.scene_id, geometry, initial, tuple(moves))


def scroll_ms(distance_px: float) -> int:
    if distance_px <= 0:
        return 0
    return min(SCROLL_MAX_MS, SCROLL_BASE_MS + round(SCROLL_MS_PER_1000_PX * distance_px / 1000))


def padded(rect: PageRect, pad_px: float = HIGHLIGHT_PAD_PX) -> PageRect:
    x, y = max(0.0, rect.x - pad_px), max(0.0, rect.y - pad_px)
    return PageRect(
        x=round(x, 2),
        y=round(y, 2),
        width=round(rect.x + rect.width + pad_px - x, 2),
        height=round(rect.y + rect.height + pad_px - y, 2),
    )


def sample_rect(tiles: Sequence[Tile], rect: PageRect) -> tuple[Rgb, Rgb]:
    """(ink, background) under a rect: its darkest tenth of pixels, and the median of the rest."""
    crop = crop_rect(tiles, rect, HIGHLIGHT_PAD_PX)
    pixels = np.asarray(crop, dtype=np.float64).reshape(-1, 3)
    luminance = pixels @ np.array([0.2126, 0.7152, 0.0722])
    order = np.argsort(luminance, kind="stable")
    k = max(1, int(len(order) * INK_FRACTION))
    rest = order[k:] if len(order) > k else order[:k]
    return _median_rgb(pixels[order[:k]]), _median_rgb(pixels[rest])


def blend_multiply(overlay: Rgb, background: Rgb, alpha: float) -> Rgb:
    """mix-blend-mode multiply at alpha, as the renderer draws it: white tints, ink stays dark."""
    r, g, b = (
        round((1 - alpha) * bg + alpha * bg * ov / 255)
        for ov, bg in zip(overlay, background, strict=True)
    )
    return r, g, b


def overlay_rgba(
    tiles: Sequence[Tile], quote: CaptureQuote, capture_id: str, where: str
) -> tuple[str, float, float]:
    """(rgba, alpha, Lc): the strongest family alpha keeping every line's ink at Lc 75 or more."""
    samples = [sample_rect(tiles, rect) for rect in quote.line_rects]
    reached = 0.0
    for alpha in ALPHA_FAMILY:
        lc = min(abs(apca_lc(ink, blend_multiply(AMBER, bg, alpha))) for ink, bg in samples)
        if lc >= TEXT_LC_MIN:
            return f"rgba({AMBER[0]}, {AMBER[1]}, {AMBER[2]}, {alpha})", alpha, lc
        reached = max(reached, lc)
    issue = ContractIssue(
        kind="color",
        where=where,
        message=(
            f"quote {quote.quote_id} of capture {capture_id}: highlighted text reaches APCA Lc "
            f"{reached:.1f} at overlay alpha {ALPHA_FAMILY[-1]}, below {TEXT_LC_MIN:.0f}."
        ),
        fix="re-capture at a larger viewport, or quote a passage in darker ink on lighter ground.",
        ids=(capture_id, quote.quote_id),
    )
    raise EpisodeInvalidError([issue])


def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> Callable[[float], float]:
    """Remotion's Easing.bezier, ported step for step so both twins ease a frame identically."""
    if x1 == y1 and x2 == y2:
        return lambda x: min(1.0, max(0.0, x))
    samples = np.array([_bezier(i * 0.1, x1, x2) for i in range(11)], dtype=np.float32)

    def t_for_x(x: float) -> float:
        start, current = 0.0, 1
        while current != 10 and float(samples[current]) <= x:
            start += 0.1
            current += 1
        current -= 1
        lo, hi = float(samples[current]), float(samples[current + 1])
        guess = start + (x - lo) / (hi - lo) * 0.1
        slope = _slope(guess, x1, x2)
        if slope >= 0.001:
            for _ in range(4):
                slope = _slope(guess, x1, x2)
                if slope == 0.0:
                    return guess
                guess -= (_bezier(guess, x1, x2) - x) / slope
            return guess
        if slope == 0.0:
            return guess
        a, b = start, start + 0.1
        t = a
        for _ in range(10):
            t = a + (b - a) / 2
            if _bezier(t, x1, x2) - x > 0:
                b = t
            else:
                a = t
            if abs(_bezier(t, x1, x2) - x) <= 1e-7:
                break
        return t

    def ease(x: float) -> float:
        x = min(1.0, max(0.0, x))
        if x in (0.0, 1.0):
            return x
        return _bezier(t_for_x(x), y1, y2)

    return ease


def camera_at(keys: Sequence[CameraKey], frame: int) -> CameraState:
    """The camera at a timeline frame: held before the first key, eased by the later key between."""
    if not keys:
        return CameraState(0.0, 0.0, 1.0)
    if frame <= keys[0].frame:
        return _state(keys[0])
    for a, b in pairwise(keys):
        if frame >= b.frame:
            continue
        t = (frame - a.frame) / (b.frame - a.frame)
        eased = 0.0 if b.easing == "hold" else cubic_bezier(*EASING[b.easing])(t)
        return CameraState(
            a.scroll_x + (b.scroll_x - a.scroll_x) * eased,
            a.scroll_y + (b.scroll_y - a.scroll_y) * eased,
            a.zoom + (b.zoom - a.zoom) * eased,
        )
    return _state(keys[-1])


def screen_rect(
    rect: PageRect, camera: CameraState, geometry: PageGeometry
) -> tuple[float, float, float, float]:
    """A page rect on the canvas under this camera: (x0, y0, x1, y1)."""
    s = geometry.scale(camera.zoom)
    x0 = geometry.region.x + (rect.x - camera.scroll_x) * s
    y0 = geometry.region.y + (rect.y - camera.scroll_y) * s
    return x0, y0, x0 + rect.width * s, y0 + rect.height * s


def line_progress(key: HighlightKey, line: int, frame: int) -> float:
    """How much of line `line` the sweep has drawn at `frame`, 0..1, left to right."""
    per_line = (key.end_frame - key.start_frame) / len(key.rects)
    if per_line <= 0:
        return 1.0 if frame >= key.start_frame else 0.0
    return min(1.0, max(0.0, (frame - key.start_frame - line * per_line) / per_line))


def highlight_opacity(key: HighlightKey, frame: int) -> float:
    """1 while the highlight stands; the clear fades it to 0 between its two frames."""
    if key.clear_start_frame is None or key.clear_end_frame is None:
        return 1.0
    span = max(1, key.clear_end_frame - key.clear_start_frame)
    return 1.0 - min(1.0, max(0.0, (frame - key.clear_start_frame) / span))


def _highlight(move: SourceMove, start_ms: int, fps: int) -> HighlightKey:
    assert move.quote is not None
    sweep_start = to_frames(start_ms + SETTLE_MS, fps)
    sweep_end = to_frames(start_ms + SETTLE_MS + LINE_SWEEP_MS * len(move.quote.line_rects), fps)
    return HighlightKey(
        quote_id=move.quote.quote_id,
        start_frame=sweep_start,
        end_frame=max(sweep_end, sweep_start),
        rects=tuple(padded(r) for r in move.quote.line_rects),
        rgba=move.rgba,
    )


def _cleared(key: HighlightKey, start_ms: int, fps: int) -> HighlightKey:
    if key.clear_start_frame is not None:
        return key
    start = max(to_frames(start_ms, fps), key.end_frame)
    end = max(to_frames(start_ms + CLEAR_MS, fps), start)
    return HighlightKey(**{**key.model_dump(), "clear_start_frame": start, "clear_end_frame": end})


def _key(frame: int, state: CameraState, easing: str) -> CameraKey:
    return CameraKey(
        frame=frame,
        scroll_x=state.scroll_x,
        scroll_y=state.scroll_y,
        zoom=state.zoom,
        easing=easing,  # type: ignore[arg-type]
    )


def _push(keys: list[CameraKey], key: CameraKey) -> None:
    # A key's easing describes the segment that arrives at it; an origin key landing on the same
    # frame as the previous arrival is a zero-length hold and must not erase that easing.
    if keys and key.frame == keys[-1].frame:
        easing = keys[-1].easing if key.easing == "hold" else key.easing
        keys[-1] = key.model_copy(update={"easing": easing})
        return
    assert not keys or key.frame > keys[-1].frame, "source actions run in sequence (timing.py)"
    keys.append(key)


def _state(key: CameraKey) -> CameraState:
    return CameraState(key.scroll_x, key.scroll_y, key.zoom)


def _bad_target(
    at: str, scene: Scene, action: str, target_id: str, capture_id: str
) -> ContractIssue:
    return ContractIssue(
        kind="invalid_reference",
        where=f"{at}.targets[0]",
        message=(
            f"scene {scene.scene_id} {action} targets {target_id}, which is neither a quote nor "
            f"a section of capture {capture_id}."
        ),
        fix="target a quote_id or section_id from the capture manifest.",
        ids=(scene.scene_id, target_id),
    )


def _median_rgb(pixels: np.ndarray) -> Rgb:
    r, g, b = (round(float(v)) for v in np.median(pixels, axis=0))
    return r, g, b


def _bezier(t: float, a1: float, a2: float) -> float:
    return ((1.0 - 3.0 * a2 + 3.0 * a1) * t + (3.0 * a2 - 6.0 * a1)) * t * t + 3.0 * a1 * t


def _slope(t: float, a1: float, a2: float) -> float:
    return 3.0 * (1.0 - 3.0 * a2 + 3.0 * a1) * t * t + 2.0 * (3.0 * a2 - 6.0 * a1) * t + 3.0 * a1
