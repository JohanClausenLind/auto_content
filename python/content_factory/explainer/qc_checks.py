"""Blocking deterministic QC on a rendered explainer: every check records pass, fail or unknown."""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cached_property
from itertools import combinations, pairwise
from pathlib import Path

import numpy as np
from PIL import Image

from content_factory.explainer import ocr as ocr_mod
from content_factory.explainer.colors import co_visible_sets, token_hex
from content_factory.explainer.errors import ContractIssue
from content_factory.explainer.evidence import check_evidence
from content_factory.explainer.layout import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CONTENT_BOX,
    region,
    role_px,
)
from content_factory.explainer.qc import (
    Decoder,
    QcFinding,
    QcUnmeasurableError,
    check_rendered,
    decode_frames,
    deemphasised_at,
    ink_hex_for,
    sample_frames,
    shared_value_domain,
    text_contrast_lc,
    visible_at,
)
from content_factory.explainer.render import PUBLIC_ASSETS, REPO_ROOT, URL_PREFIXES
from content_factory.explainer.source_scene import (
    HIGHLIGHT_PAD_PX,
    PageGeometry,
    camera_at,
    padded,
    screen_rect,
)
from content_factory.explainer.timing import TokenClock, to_frames
from content_factory.explainer.tokens_gen import TOKENS
from content_factory.explainer.validate import check_episode
from content_factory.schemas.audio import LoudnessReport
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import (
    ACTION_TEMPLATES,
    AnnotateAction,
    CaptureTile,
    ChartTemplate,
    CompiledExplainerScene,
    DiagramTemplate,
    EvidencePack,
    ExplainerRenderBundle,
    HighlightKey,
    NarrationManifest,
    PageRect,
    PixelBox,
    Scene,
    ScriptPlan,
    SourceDocumentTemplate,
    TextTemplate,
    VisualSpec,
)

QC_VERSION = "0.5.0"
# The order is the reviewer's prompt: review.py hashes it into ReviewerIdentity.prompt_sha256.
CHECKS: tuple[str, ...] = (
    "contract",
    "evidence",
    "assets",
    "capability",
    "cue_coverage",
    "clipping",
    "ink_overflow",
    "text_floor",
    "overlap",
    "safe_area",
    "duration",
    "true_peak",
    "loudness",
    "text_apca",
    "line_edge",
    "small_screen",
    "ocr_text",
    "transition",
    "axis_change",
    "narration_visual",
    "misleading_axis",
)
Ocr = Callable[[Sequence[Image.Image]], list[str]]
SMALL_WIDTH = 360
RING_PX = 6
INK_THRESHOLD = 60
OCR_PAD_PX = 6
TEXT_LC_MIN = 75.0
TRUE_PEAK_MAX_DBTP = -1.0
TARGET_LUFS = -14.0
LOUDNESS_TOLERANCE_LU = 1.0
CUE_SLACK_FRAMES = 1
RECT_TOLERANCE_PX = 0.5
AXIS_WORD = "axis"
OCR_TEXT_VARIANTS = frozenset({"quotation_card", "statement"})
FLOORS_PX: dict[str, int] = TOKENS["typography"]["floors_px_at_1080"]
_SURFACE = token_hex("ui.surface.0")


def run_deterministic_qc(
    bundle: ExplainerRenderBundle,
    mp4: Path,
    *,
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    narration: NarrationManifest | None = None,
    mix_loudness: LoudnessReport | None = None,
    small_width: int = SMALL_WIDTH,
    decode: Decoder | None = None,
    ocr: Ocr | None = None,
) -> list[QcFinding]:
    """Every check in CHECKS order; unknown findings carry the reason in evidence."""
    qc = _Qc(
        bundle,
        mp4,
        pack,
        script,
        spec,
        narration,
        mix_loudness,
        small_width,
        decode or decode_frames,
        ocr or paddle_ocr,
    )
    findings: list[QcFinding] = []
    for check in CHECKS:
        findings += getattr(qc, f"check_{check}")()
    return findings


def sampled_ms(bundle: ExplainerRenderBundle) -> dict[str, list[int]]:
    """The timestamps QC looked at per scene: action ends, hold midpoints, boundaries ±1 frame."""
    fps, last = bundle.timeline.fps, bundle.timeline.total_frames - 1
    return {
        s.scene_id: sorted({math.floor(min(f, last) * 1000 / fps) for f in _samples(s)})
        for s in bundle.timeline.scenes
    }


def paddle_ocr(crops: Sequence[Image.Image]) -> list[str]:
    """One PaddleOCR run over the crops; RuntimeError when the OCR venv is not installed."""
    with tempfile.TemporaryDirectory(prefix="qc-ocr-") as tmp:
        paths: list[Path] = []
        for i, crop in enumerate(crops):
            path = Path(tmp) / f"crop-{i:03d}.png"
            crop.save(path)
            paths.append(path)
        recognised = ocr_mod.run_paddle(paths)
    return [" ".join(line.text for line in lines) for lines in recognised]


def _samples(compiled: CompiledExplainerScene) -> set[int]:
    start, last = compiled.start_frame, compiled.start_frame + compiled.duration_frames - 1
    return sample_frames(compiled) | {start, min(start + 1, last), last}


@dataclass(frozen=True)
class _Placed:
    """A text box at the position the renderer uses; only exact boxes are measured on pixels."""

    entity_id: str
    box: PixelBox
    font_px: int | None
    text: str | None
    exact: bool


class _Frames:
    """Decoded frames by (ms, width): each sample is decoded once for every check that reads it."""

    def __init__(self, mp4: Path, decode: Decoder, fps: int, total_frames: int) -> None:
        self.mp4, self.decode, self.fps, self.last = mp4, decode, fps, total_frames - 1
        self._cache: dict[tuple[int, int | None], Image.Image] = {}

    def ms(self, frame: int) -> int:
        return math.floor(min(frame, self.last) * 1000 / self.fps)

    def at(self, frame: int, width: int | None = None) -> Image.Image:
        key = (self.ms(frame), width)
        if key not in self._cache:
            self._cache[key] = self.decode(self.mp4, [key[0]], width=width)[0]
        return self._cache[key]


class _Qc:
    def __init__(
        self,
        bundle: ExplainerRenderBundle,
        mp4: Path,
        pack: EvidencePack,
        script: ScriptPlan,
        spec: VisualSpec,
        narration: NarrationManifest | None,
        loudness: LoudnessReport | None,
        small_width: int,
        decode: Decoder,
        ocr: Ocr,
    ) -> None:
        self.bundle, self.mp4, self.pack, self.script, self.spec = bundle, mp4, pack, script, spec
        self.narration, self.loudness, self.small_width = narration, loudness, small_width
        self.decode, self.ocr = decode, ocr
        timeline = bundle.timeline
        self.fps = timeline.fps
        self.frames = _Frames(mp4, decode, timeline.fps, timeline.total_frames)
        self.compiled = {s.scene_id: s for s in timeline.scenes}
        self.scenes = {s.scene_id: s for s in spec.scenes}
        self.entities = {e.entity_id: e for e in spec.entities}
        self.assets = {a.asset_id: a for a in spec.assets}
        self.datasets = {d.dataset_id: d for d in bundle.datasets}
        self.captures = {c.capture_id: c for c in bundle.captures}
        self.layouts = {layout.scene_id: layout for layout in bundle.layouts}

    # --- episode-level checks ---

    def check_contract(self) -> list[QcFinding]:
        manifests = [c.manifest for c in self.bundle.captures]
        issues = check_episode(self.pack, self.script, self.spec, manifests, self.narration)
        issues = [i for i in issues if i not in self._evidence_issues]
        if not issues:
            return [self._episode("contract", True, "check_episode found no issue")]
        return [self._issue("contract", issue) for issue in issues]

    def check_evidence(self) -> list[QcFinding]:
        if not self._evidence_issues:
            return [self._episode("evidence", True, "calculations, datasets and numbers agree")]
        return [self._issue("evidence", issue) for issue in self._evidence_issues]

    def check_assets(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        for scene in self.spec.scenes:
            template = scene.template
            if isinstance(template, ChartTemplate):
                dataset_id = self.assets[template.dataset_asset_id].dataset_id or ""
                present = dataset_id in self.datasets
                findings.append(
                    self._scene(
                        "assets",
                        scene.scene_id,
                        present,
                        f"dataset {dataset_id} {'is' if present else 'is not'} in the bundle",
                    )
                )
            if isinstance(template, SourceDocumentTemplate):
                findings += self._capture_assets(scene, template)
        return findings or [self._episode("assets", True, "no dataset or capture referenced")]

    def check_capability(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        for scene in self.spec.scenes:
            name = scene.template.template
            compiled = self.compiled[scene.scene_id]
            actions = [a.action for beat in scene.beats for a in beat.actions]
            actions += [a.action for a in compiled.actions]
            unsupported = sorted({a for a in actions if name not in ACTION_TEMPLATES.get(a, ())})
            problems = [f"{name} template cannot {', '.join(unsupported)}"] if unsupported else []
            if isinstance(scene.template, SourceDocumentTemplate):
                capture_id = self.assets[scene.template.capture_asset_id].capture_id or ""
                if capture_id not in self.captures:
                    problems.append(f"capture {capture_id} is not in the bundle")
            if isinstance(scene.template, DiagramTemplate) and scene.scene_id not in self.layouts:
                problems.append("diagram scene has no precomputed layout")
            findings.append(
                self._scene(
                    "capability",
                    scene.scene_id,
                    not problems,
                    "; ".join(problems) or f"every action is supported by the {name} template",
                    measured=float(len(problems)),
                )
            )
        return findings

    def check_cue_coverage(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        if self.narration is None:
            why = "no narration manifest: cue tokens cannot be matched to aligned words"
            findings.append(self._episode("cue_coverage", None, why))
        else:
            findings += self._cue_alignment(self.narration)
        try:
            clock = self._clock
        except KeyError as missing:
            return [*findings, self._episode("cue_coverage", None, f"unknown segment {missing}")]
        total_ms = self.bundle.timeline.total_frames * 1000 / self.fps
        late: list[QcFinding] = []
        for segment in self.script.segments:
            end = clock.end_ms(segment.segment_id, len(segment.tokens) - 1)
            if end > total_ms + 1000 / self.fps:
                evidence = (
                    f"segment {segment.segment_id} is spoken until {end} ms; "
                    f"the video ends at {total_ms:.0f} ms"
                )
                late.append(
                    self._at("cue_coverage", self._last_scene, None, int(total_ms), False, evidence)
                )
        if late:
            return findings + late
        return [*findings, self._episode("cue_coverage", True, "every segment ends on screen")]

    def check_duration(self) -> list[QcFinding]:
        timeline = self.bundle.timeline
        try:
            probe = _probe(self.mp4)
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as why:
            return [
                self._episode("duration", None, f"ffprobe could not read {self.mp4.name}: {why}")
            ]
        frames, fps = probe
        ok = frames == timeline.total_frames and fps == timeline.fps
        evidence = (
            f"{frames} frames at {fps:g} fps; timeline {timeline.total_frames} at {timeline.fps}"
        )
        return [
            self._episode("duration", ok, evidence, float(frames), float(timeline.total_frames))
        ]

    def check_true_peak(self) -> list[QcFinding]:
        if self.loudness is None:
            return [self._episode("true_peak", None, "no mix loudness report")]
        peak = self.loudness.true_peak_dbtp
        evidence = f"true peak {peak:.2f} dBTP, ceiling {TRUE_PEAK_MAX_DBTP:.1f}"
        return [
            self._episode(
                "true_peak", peak <= TRUE_PEAK_MAX_DBTP, evidence, peak, TRUE_PEAK_MAX_DBTP
            )
        ]

    def check_loudness(self) -> list[QcFinding]:
        if self.loudness is None:
            return [self._episode("loudness", None, "no mix loudness report")]
        lufs = self.loudness.integrated_lufs
        off = abs(lufs - TARGET_LUFS)
        evidence = (
            f"integrated {lufs:.2f} LUFS, target {TARGET_LUFS:.0f} ±{LOUDNESS_TOLERANCE_LU:.0f}"
        )
        return [
            self._episode(
                "loudness", off <= LOUDNESS_TOLERANCE_LU, evidence, off, LOUDNESS_TOLERANCE_LU
            )
        ]

    # --- geometry checks ---

    def check_clipping(self) -> list[QcFinding]:
        canvas = PixelBox(x=0, y=0, width=CANVAS_WIDTH, height=CANVAS_HEIGHT)
        findings: list[QcFinding] = []
        for scene_id, placed in self._placed.items():
            clipped = [p for p in placed if not _inside(p.box, canvas)]
            findings += [
                self._scene(
                    "clipping",
                    scene_id,
                    False,
                    f"{_describe(p.box)} crosses the canvas edge",
                    p.entity_id,
                )
                for p in clipped
            ]
            if not clipped:
                findings.append(
                    self._scene("clipping", scene_id, True, "every box is inside the canvas")
                )
        return findings

    def check_safe_area(self) -> list[QcFinding]:
        # A box cut by the canvas edge is clipping's finding; this reports the rest of the margin.
        canvas = PixelBox(x=0, y=0, width=CANVAS_WIDTH, height=CANVAS_HEIGHT)
        findings: list[QcFinding] = []
        for scene_id, placed in self._placed.items():
            outside = [
                p for p in placed if _inside(p.box, canvas) and not _inside(p.box, CONTENT_BOX)
            ]
            findings += [
                self._scene(
                    "safe_area",
                    scene_id,
                    False,
                    f"{_describe(p.box)} leaves the safe area",
                    p.entity_id,
                )
                for p in outside
            ]
            if not outside:
                findings.append(
                    self._scene("safe_area", scene_id, True, "every box is inside the safe area")
                )
        return findings

    def check_text_floor(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        for scene in self.spec.scenes:
            compiled = self.compiled[scene.scene_id]
            small: list[QcFinding] = []
            for box in compiled.boxes:
                if box.font_px is None:
                    continue
                floor = _floor_px(scene, box.entity_id)
                if box.font_px < floor:
                    evidence = f"{box.font_px} px is below the {floor} px floor"
                    small.append(
                        self._scene(
                            "text_floor",
                            scene.scene_id,
                            False,
                            evidence,
                            box.entity_id,
                            float(box.font_px),
                            float(floor),
                        )
                    )
            findings += small or [
                self._scene("text_floor", scene.scene_id, True, "every text meets its floor")
            ]
        return findings

    def check_overlap(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        for scene in self.spec.scenes:
            placed = {p.entity_id: p for p in self._placed[scene.scene_id]}
            formula = _formula_groups(scene)
            seen: set[tuple[str, str]] = set()
            hits: list[QcFinding] = []
            for group in co_visible_sets(scene):
                shown = [placed[e] for e in group if e in placed]
                for a, b in combinations(shown, 2):
                    pair = (a.entity_id, b.entity_id)
                    if pair in seen or set(pair) <= formula or not _intersects(a.box, b.box):
                        continue
                    seen.add(pair)
                    evidence = (
                        f"{a.entity_id} {_describe(a.box)} overlaps "
                        f"{b.entity_id} {_describe(b.box)}"
                    )
                    hits.append(
                        self._scene("overlap", scene.scene_id, False, evidence, a.entity_id)
                    )
            findings += hits or [
                self._scene("overlap", scene.scene_id, True, "no visible boxes overlap")
            ]
        return findings

    # --- pixel checks ---

    def check_text_apca(self) -> list[QcFinding]:
        found = [f for f in self._rendered if f.check == "text_apca"]
        return found or [self._episode("text_apca", True, "no text on screen at any sample")]

    def check_line_edge(self) -> list[QcFinding]:
        found = [f for f in self._rendered if f.check == "line_edge"]
        return found or [self._episode("line_edge", True, "no line chart to measure")]

    def check_ink_overflow(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        for scene in self.spec.scenes:
            template = scene.template
            # A formula box is an estimate (layout.formula_width_px), not a verified fit.
            if not isinstance(template, TextTemplate) or template.variant == "formula":
                continue
            compiled = self.compiled[scene.scene_id]
            for placed in (p for p in self._placed[scene.scene_id] if p.exact):
                frame = _reveal_end(compiled, placed.entity_id)
                findings.append(self._ink_finding(compiled, placed, frame))
        return findings or [self._episode("ink_overflow", True, "no text template to measure")]

    def check_small_screen(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        body = role_px("body")
        for scene in self.spec.scenes:
            compiled = self.compiled[scene.scene_id]
            boxes = [
                p for p in self._placed[scene.scene_id] if p.exact and (p.font_px or 0) >= body
            ]
            if not boxes:
                continue
            for frame in sorted(_samples(compiled)):
                visible = visible_at(scene, compiled, frame)
                read = visible - deemphasised_at(compiled, frame, visible)
                for placed in (p for p in boxes if p.entity_id in read):
                    findings.append(self._small_finding(compiled, placed, frame))
        return findings or [self._episode("small_screen", True, "no read text to measure")]

    def check_ocr_text(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        crops: list[tuple[CompiledExplainerScene, str | None, int, str, Image.Image]] = []
        for scene in self.spec.scenes:
            compiled = self.compiled[scene.scene_id]
            template = scene.template
            if isinstance(template, TextTemplate) and template.variant in OCR_TEXT_VARIANTS:
                findings += self._text_crops(scene, template, compiled, crops)
            elif isinstance(template, SourceDocumentTemplate):
                findings += self._highlight_crops(scene, template, compiled, crops)
        if not crops:
            return findings or [self._episode("ocr_text", True, "no quote or statement to read")]
        try:
            read = self.ocr([crop for *_, crop in crops])
        except RuntimeError as why:
            return findings + [
                self._at("ocr_text", compiled, entity, at_ms, None, f"OCR unavailable: {why}")
                for compiled, entity, at_ms, _, _ in crops
            ]
        for (compiled, entity, at_ms, expected, _), text in zip(crops, read, strict=True):
            verdict = ocr_mod.judge(expected, text)
            findings.append(
                self._at(
                    "ocr_text",
                    compiled,
                    entity,
                    at_ms,
                    verdict.passed,
                    verdict.reason,
                    verdict.similarity,
                    ocr_mod.OCR_THRESHOLD,
                )
            )
        return findings

    # --- timing and meaning checks ---

    def check_transition(self) -> list[QcFinding]:
        scenes = self.bundle.timeline.scenes
        if len(scenes) < 2:
            return [self._episode("transition", True, "a single scene has no boundary")]
        try:
            clock = self._clock
        except KeyError as missing:
            return [self._episode("transition", None, f"unknown segment {missing}")]
        findings: list[QcFinding] = []
        for prev, nxt in pairwise(scenes):
            boundary = nxt.start_frame
            late = [a for a in prev.actions if a.end_frame > boundary]
            for action in late:
                evidence = (
                    f"{action.action} of beat {action.beat_id} ends at frame "
                    f"{action.end_frame}, past the boundary at {boundary}"
                )
                findings.append(
                    self._at(
                        "transition",
                        prev,
                        None,
                        self.frames.ms(boundary),
                        False,
                        evidence,
                        float(action.end_frame),
                        float(boundary),
                    )
                )
            first = min(nxt.actions, key=lambda a: a.start_frame, default=None)
            cue_frame = to_frames(clock.anchor_ms(self.scenes[nxt.scene_id].beats[0].cue), self.fps)
            if first is not None and first.start_frame > cue_frame + CUE_SLACK_FRAMES:
                evidence = (
                    f"first {first.action} starts at frame {first.start_frame} but its cue "
                    f"is at {cue_frame}: the previous scene ran long"
                )
                findings.append(
                    self._at(
                        "transition",
                        nxt,
                        None,
                        self.frames.ms(first.start_frame),
                        False,
                        evidence,
                        float(first.start_frame),
                        float(cue_frame),
                    )
                )
            if not late and (first is None or first.start_frame <= cue_frame + CUE_SLACK_FRAMES):
                findings.append(
                    self._at(
                        "transition",
                        nxt,
                        None,
                        self.frames.ms(boundary),
                        True,
                        f"clean cut at frame {boundary}",
                    )
                )
        return findings

    def check_axis_change(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        for a, b in pairwise(self.spec.scenes):
            ta, tb = a.template, b.template
            if not (isinstance(ta, ChartTemplate) and isinstance(tb, ChartTemplate)):
                continue
            shared = {s.entity_id for s in ta.series} & {s.entity_id for s in tb.series}
            if not shared:
                continue
            domain_a, domain_b = self._domain(ta), self._domain(tb)
            if domain_a == domain_b:
                findings.append(
                    self._scene(
                        "axis_change",
                        b.scene_id,
                        True,
                        f"axis {domain_a} carried over from {a.scene_id}",
                    )
                )
                continue
            disclosed = _mentions_axis(b)
            said = "disclosed" if disclosed else "no annotate or title says so"
            evidence = f"axis changes from {domain_a} in {a.scene_id} to {domain_b}; {said}"
            findings.append(
                self._scene(
                    "axis_change", b.scene_id, disclosed, evidence, next(iter(sorted(shared)))
                )
            )
        return findings or [
            self._episode("axis_change", True, "no consecutive charts share a series")
        ]

    def check_narration_visual(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        for scene in self.spec.scenes:
            compiled = self.compiled[scene.scene_id]
            starts = {a.beat_id: a.start_frame for a in reversed(compiled.actions)}
            scene_claims = set(scene.claim_ids)
            if isinstance(scene.template, TextTemplate):
                scene_claims.update(i.claim_id for i in scene.template.items if i.claim_id)
            misses: list[QcFinding] = []
            for beat in scene.beats:
                try:
                    said = set(self.script.segment(beat.cue.segment_id).claim_ids)
                except KeyError:
                    continue
                if not said:
                    continue
                shown = set(scene_claims)
                for action in beat.actions:
                    for target in getattr(action, "targets", ()):
                        shown.update(
                            self.entities[target].claim_ids if target in self.entities else ()
                        )
                    if isinstance(action, AnnotateAction) and action.claim_id:
                        shown.add(action.claim_id)
                if said & shown:
                    continue
                evidence = (
                    f"beat {beat.beat_id} cues segment {beat.cue.segment_id} claiming "
                    f"{', '.join(sorted(said))}; the screen shows "
                    f"{', '.join(sorted(shown)) or 'no claim'}"
                )
                at_ms = self.frames.ms(starts.get(beat.beat_id, compiled.start_frame))
                misses.append(self._at("narration_visual", compiled, None, at_ms, False, evidence))
            findings += misses or [
                self._scene(
                    "narration_visual", scene.scene_id, True, "every cued claim is on screen"
                )
            ]
        return findings

    def check_misleading_axis(self) -> list[QcFinding]:
        findings: list[QcFinding] = []
        for scene in self.spec.scenes:
            chart = scene.template
            if not isinstance(chart, ChartTemplate):
                continue
            problems: list[str] = []
            if chart.chart_kind in {"bar", "stacked_bar"} and not chart.baseline_zero:
                problems.append("bars without a zero baseline")
            dataset = self.datasets.get(self.assets[chart.dataset_asset_id].dataset_id or "")
            if dataset is not None:
                units = {c.name: c.unit for c in dataset.columns}
                drawn = {
                    units.get(
                        s.value if chart.series_field is None and s.value else chart.y.field, ""
                    )
                    for s in chart.series
                }
                if len(drawn) > 1:
                    problems.append(
                        f"series in different units share one axis: {', '.join(sorted(drawn))}"
                    )
                elif chart.y.unit and drawn and drawn != {chart.y.unit}:
                    column_unit = next(iter(drawn)) or "unitless"
                    problems.append(f"axis says {chart.y.unit} but the data is {column_unit}")
            findings.append(
                self._scene(
                    "misleading_axis",
                    scene.scene_id,
                    not problems,
                    "; ".join(problems) or "zero baseline and one unit per axis",
                    measured=float(len(problems)),
                )
            )
        return findings or [self._episode("misleading_axis", True, "no chart")]

    # --- shared state ---

    @cached_property
    def _evidence_issues(self) -> list[ContractIssue]:
        return check_evidence(self.pack, self.script, self.spec)

    @cached_property
    def _clock(self) -> TokenClock:
        return TokenClock(self.script, self.narration)

    @cached_property
    def _rendered(self) -> list[QcFinding]:
        return check_rendered(self.bundle, self.mp4, text_lc_min=TEXT_LC_MIN, decode=self.decode)

    @cached_property
    def _placed(self) -> dict[str, list[_Placed]]:
        return {
            s.scene_id: self._placed_boxes(s, self.compiled[s.scene_id]) for s in self.spec.scenes
        }

    @property
    def _last_scene(self) -> CompiledExplainerScene:
        return self.bundle.timeline.scenes[-1]

    def _placed_boxes(self, scene: Scene, compiled: CompiledExplainerScene) -> list[_Placed]:
        """Text items and nodes sit at their compiled box, edge labels centre on the ELK anchor."""
        # Annotations are placed by the renderer next to the marks they point at: unmeasured here.
        template = scene.template
        layout = self.layouts.get(scene.scene_id)
        nodes = {n.entity_id for n in layout.nodes} if layout else set()
        anchors = {e.entity_id: e.label_anchor for e in layout.edges} if layout else {}
        placed: list[_Placed] = []
        for box in compiled.boxes:
            eid = box.entity_id
            if eid not in self.entities and eid.startswith("annot"):
                continue
            if isinstance(template, TextTemplate) or eid in nodes:
                placed.append(_Placed(eid, box.box, box.font_px, box.text, True))
            elif isinstance(template, DiagramTemplate):
                anchor = anchors.get(eid)
                if anchor is not None:
                    centred = PixelBox(
                        x=max(0, round(anchor.x - box.box.width / 2)),
                        y=max(0, round(anchor.y - box.box.height / 2)),
                        width=box.box.width,
                        height=box.box.height,
                    )
                    placed.append(_Placed(eid, centred, box.font_px, box.text, False))
            else:
                placed.append(_Placed(eid, box.box, box.font_px, box.text, False))
        return placed

    def _domain(self, chart: ChartTemplate) -> tuple[float, float]:
        dataset = self.datasets.get(self.assets[chart.dataset_asset_id].dataset_id or "")
        return shared_value_domain(chart, dataset) if dataset else (0.0, 1.0)

    def _capture_assets(self, scene: Scene, template: SourceDocumentTemplate) -> list[QcFinding]:
        capture_id = self.assets[template.capture_asset_id].capture_id or ""
        capture = self.captures.get(capture_id)
        if capture is None:
            return [
                self._scene(
                    "assets", scene.scene_id, False, f"capture {capture_id} is not in the bundle"
                )
            ]
        findings: list[QcFinding] = []
        for tile in capture.tiles:
            path = _tile_path(tile)
            if path is None:
                findings.append(
                    self._scene(
                        "assets",
                        scene.scene_id,
                        None,
                        f"tile {tile.path} is a URL; not verifiable here",
                    )
                )
            elif not path.is_file():
                findings.append(
                    self._scene("assets", scene.scene_id, False, f"tile {tile.path} is missing")
                )
            else:
                digest = file_sha256(path)
                ok = digest == tile.sha256
                evidence = (
                    f"tile {path.name} hashes to {digest[:12]}, bundle says {tile.sha256[:12]}"
                )
                findings.append(self._scene("assets", scene.scene_id, ok, evidence))
        return findings

    def _cue_alignment(self, narration: NarrationManifest) -> list[QcFinding]:
        aligned = {
            (w.segment_id, w.token_index)
            for take in narration.takes
            if take.alignment is not None
            for w in take.alignment.words
        }
        findings: list[QcFinding] = []
        for scene in self.spec.scenes:
            compiled = self.compiled[scene.scene_id]
            for beat in scene.beats:
                cue = beat.cue
                missing = [
                    k
                    for k in range(cue.token_start, cue.token_end + 1)
                    if (cue.segment_id, k) not in aligned
                ]
                if missing:
                    evidence = (
                        f"beat {beat.beat_id}: tokens {missing} of {cue.segment_id} "
                        "have no aligned word"
                    )
                    findings.append(
                        self._at(
                            "cue_coverage",
                            compiled,
                            None,
                            self.frames.ms(compiled.start_frame),
                            False,
                            evidence,
                        )
                    )
        return findings or [
            self._episode("cue_coverage", True, "every cue token has an aligned word")
        ]

    def _ink_finding(
        self, compiled: CompiledExplainerScene, placed: _Placed, frame: int
    ) -> QcFinding:
        at_ms = self.frames.ms(frame)
        try:
            image = self.frames.at(frame)
        except QcUnmeasurableError as why:
            return self._at("ink_overflow", compiled, placed.entity_id, at_ms, None, str(why))
        mask = _ink_mask(image)
        b = placed.box
        inside = int(mask[b.y : b.y + b.height, b.x : b.x + b.width].sum())
        outer = mask[
            max(0, b.y - RING_PX) : b.y + b.height + RING_PX,
            max(0, b.x - RING_PX) : b.x + b.width + RING_PX,
        ]
        ring = int(outer.sum()) - inside
        if inside == 0:
            return self._at(
                "ink_overflow",
                compiled,
                placed.entity_id,
                at_ms,
                None,
                f"no ink inside {_describe(b)} at the reveal end",
            )
        evidence = (
            f"{ring} ink pixels in the {RING_PX} px ring around {_describe(b)}; {inside} inside"
        )
        return self._at(
            "ink_overflow", compiled, placed.entity_id, at_ms, ring == 0, evidence, float(ring), 0.0
        )

    def _small_finding(
        self, compiled: CompiledExplainerScene, placed: _Placed, frame: int
    ) -> QcFinding:
        at_ms = self.frames.ms(frame)
        try:
            image = self.frames.at(frame, self.small_width)
            scale = image.width / CANVAS_WIDTH
            ink = ink_hex_for(placed.font_px or 0)
            lc = abs(text_contrast_lc(image, _scaled(placed.box, scale), ink))
        except QcUnmeasurableError as why:
            return self._at("small_screen", compiled, placed.entity_id, at_ms, None, str(why))
        evidence = f"Lc {lc:.1f} at {image.width} px wide for {_describe(placed.box)}"
        return self._at(
            "small_screen",
            compiled,
            placed.entity_id,
            at_ms,
            lc >= TEXT_LC_MIN,
            evidence,
            lc,
            TEXT_LC_MIN,
        )

    def _text_crops(
        self,
        scene: Scene,
        template: TextTemplate,
        compiled: CompiledExplainerScene,
        crops: list[tuple[CompiledExplainerScene, str | None, int, str, Image.Image]],
    ) -> list[QcFinding]:
        findings: list[QcFinding] = []
        boxes = {b.entity_id: b for b in compiled.boxes}
        frame = _hold_frame(compiled)
        at_ms = self.frames.ms(frame)
        visible = visible_at(scene, compiled, frame)
        for item in template.items:
            box = boxes.get(item.entity_id)
            if box is None or item.entity_id not in visible:
                continue
            drawn = ocr_mod.judge(item.text, box.text or "")
            if not drawn.passed:
                evidence = f"the compiled text is not the item text: {drawn.reason}"
                findings.append(
                    self._at(
                        "ocr_text",
                        compiled,
                        item.entity_id,
                        at_ms,
                        False,
                        evidence,
                        drawn.similarity,
                        ocr_mod.OCR_THRESHOLD,
                    )
                )
            try:
                image = self.frames.at(frame)
            except QcUnmeasurableError as why:
                findings.append(
                    self._at("ocr_text", compiled, item.entity_id, at_ms, None, str(why))
                )
                continue
            crops.append(
                (
                    compiled,
                    item.entity_id,
                    at_ms,
                    item.text,
                    _crop(image, _padded_box(box.box), at_ms),
                )
            )
        return findings

    def _highlight_crops(
        self,
        scene: Scene,
        template: SourceDocumentTemplate,
        compiled: CompiledExplainerScene,
        crops: list[tuple[CompiledExplainerScene, str | None, int, str, Image.Image]],
    ) -> list[QcFinding]:
        capture = self.captures.get(self.assets[template.capture_asset_id].capture_id or "")
        if capture is None or not compiled.highlights:
            return []
        manifest = capture.manifest
        quotes = {q.quote_id: q for q in manifest.quotes}
        geometry = PageGeometry(
            region(scene, "page"), float(manifest.viewport.width), manifest.page_height_px
        )
        last = compiled.start_frame + compiled.duration_frames - 1
        findings: list[QcFinding] = []
        for key in compiled.highlights:
            quote = quotes.get(key.quote_id)
            if quote is None:
                continue
            frame = _highlight_frame(compiled, key, last)
            at_ms = self.frames.ms(frame)
            expected = tuple(padded(r) for r in quote.line_rects)
            if not _same_rects(key.rects, expected):
                evidence = (
                    f"highlight rects {_rects(key.rects)} are not the quote's padded line "
                    f"rects {_rects(expected)}"
                )
                findings.append(
                    self._at("ocr_text", compiled, key.quote_id, at_ms, False, evidence)
                )
            camera = camera_at(compiled.camera, frame)
            shown = [screen_rect(r, camera, geometry) for r in key.rects]
            box = _union_box(shown)
            if box is None:
                findings.append(
                    self._at(
                        "ocr_text",
                        compiled,
                        key.quote_id,
                        at_ms,
                        None,
                        "the highlight is off screen at the hold",
                    )
                )
                continue
            try:
                image = self.frames.at(frame)
            except QcUnmeasurableError as why:
                findings.append(self._at("ocr_text", compiled, key.quote_id, at_ms, None, str(why)))
                continue
            # What the highlight covers, with capture-time OCR's side margin rather than its pad.
            inset = HIGHLIGHT_PAD_PX - ocr_mod.SIDE_PADDING_PX
            lines = [screen_rect(_narrower(r, inset), camera, geometry) for r in key.rects]
            crops.append((compiled, key.quote_id, at_ms, quote.text, _lines(image, lines, at_ms)))
        return findings

    # --- finding constructors ---

    def _episode(
        self,
        check: str,
        passed: bool | None,
        evidence: str,
        measured: float | None = None,
        threshold: float = 0.0,
    ) -> QcFinding:
        first = self.bundle.timeline.scenes[0]
        if measured is None and passed is not None:
            measured = 0.0 if passed else 1.0
        return QcFinding(check, first.scene_id, None, 0, measured, threshold, passed, evidence)

    def _scene(
        self,
        check: str,
        scene_id: str,
        passed: bool | None,
        evidence: str,
        entity_id: str | None = None,
        measured: float | None = None,
        threshold: float = 0.0,
    ) -> QcFinding:
        compiled = self.compiled[scene_id]
        return self._at(
            check,
            compiled,
            entity_id,
            self.frames.ms(compiled.start_frame),
            passed,
            evidence,
            measured,
            threshold,
        )

    def _at(
        self,
        check: str,
        compiled: CompiledExplainerScene,
        entity_id: str | None,
        at_ms: int,
        passed: bool | None,
        evidence: str,
        measured: float | None = None,
        threshold: float = 0.0,
    ) -> QcFinding:
        if measured is None and passed is not None:
            measured = 0.0 if passed else 1.0
        return QcFinding(
            check, compiled.scene_id, entity_id, at_ms, measured, threshold, passed, evidence
        )

    def _issue(self, check: str, issue: ContractIssue) -> QcFinding:
        scene_id = next(
            (i for i in issue.ids if i in self.compiled), self.bundle.timeline.scenes[0].scene_id
        )
        entity_id = next((i for i in issue.ids if i in self.entities), None)
        return self._scene(check, scene_id, False, str(issue), entity_id)


def _probe(mp4: Path) -> tuple[int, float]:
    """(frame count, fps) of the first video stream via ffprobe."""
    args = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames,r_frame_rate", "-of", "json", str(mp4),
    ]  # fmt: skip
    if not mp4.is_file():
        msg = f"{mp4} is not a file"
        raise FileNotFoundError(msg)
    completed = subprocess.run(args, capture_output=True, text=True, check=True)  # noqa: S603
    stream = json.loads(completed.stdout)["streams"][0]
    num, _, den = str(stream["r_frame_rate"]).partition("/")
    return int(stream["nb_read_frames"]), int(num) / int(den or 1)


def _tile_path(tile: CaptureTile) -> Path | None:
    if tile.path.startswith("assets/"):
        return PUBLIC_ASSETS / tile.path.removeprefix("assets/")
    if tile.path.startswith(URL_PREFIXES):
        return None
    path = Path(tile.path)
    return path if path.is_absolute() else REPO_ROOT / path


def _floor_px(scene: Scene, entity_id: str) -> int:
    """read_text for what the viewer reads (body and display roles), label for everything else."""
    template = scene.template
    if not isinstance(template, TextTemplate):
        return FLOORS_PX["label"]
    ids = [i.entity_id for i in template.items]
    if entity_id not in ids:
        return FLOORS_PX["label"]
    k, count = ids.index(entity_id), len(ids)
    if template.variant == "big_number" and k > 0:
        return FLOORS_PX["label"]
    if template.variant == "quotation_card" and count > 1 and k == count - 1:
        return FLOORS_PX["label"]
    return FLOORS_PX["read_text"]


def _formula_groups(scene: Scene) -> set[str]:
    """A formula's groups, which share its one typeset box by construction (layout._formula)."""
    template = scene.template
    if isinstance(template, TextTemplate) and template.variant == "formula":
        return {item.entity_id for item in template.items}
    return set()


def _reveal_end(compiled: CompiledExplainerScene, entity_id: str) -> int:
    last = compiled.start_frame + compiled.duration_frames - 1
    for action in compiled.actions:
        if action.action == "reveal" and entity_id in action.targets:
            return min(last, action.end_frame)
    return min(sample_frames(compiled))


def _hold_frame(compiled: CompiledExplainerScene) -> int:
    last = compiled.start_frame + compiled.duration_frames - 1
    holds = [a for a in compiled.actions if a.action == "hold"]
    if holds:
        return min(last, (holds[0].start_frame + holds[0].end_frame) // 2)
    return last


def _highlight_frame(compiled: CompiledExplainerScene, key: HighlightKey, last: int) -> int:
    """The hold midpoint while the highlight stands, else the frame its sweep finished."""
    end = last if key.clear_start_frame is None else min(last, key.clear_start_frame)
    hold = _hold_frame(compiled)
    return hold if key.end_frame <= hold <= end else key.end_frame


def _mentions_axis(scene: Scene) -> bool:
    texts = [a.text for b in scene.beats for a in b.actions if isinstance(a, AnnotateAction)]
    if isinstance(scene.template, ChartTemplate):
        texts.append(scene.template.title)
    return any(AXIS_WORD in t.lower() for t in texts)


def _ink_mask(image: Image.Image) -> np.ndarray:
    pixels = np.asarray(image.convert("RGB"), dtype=np.int32)
    background = np.array(_rgb(_SURFACE), dtype=np.int32)
    return np.abs(pixels - background).sum(axis=2) > INK_THRESHOLD


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _crop(image: Image.Image, box: PixelBox, at_ms: int) -> Image.Image:
    """The box's pixels; info["qc_crop"] says where they came from, so a fake OCR can look it up."""
    x0, y0 = max(0, box.x), max(0, box.y)
    x1, y1 = min(image.width, box.x + box.width), min(image.height, box.y + box.height)
    crop = image.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)))
    crop.info["qc_crop"] = (at_ms, x0, y0, x1, y1)
    return crop


def _lines(
    image: Image.Image, lines: Sequence[tuple[float, float, float, float]], at_ms: int
) -> Image.Image:
    """Each highlighted line cut on its own and stacked as at capture, so neighbours stay out."""
    boxes = [
        (
            max(0, math.floor(x0)),
            max(0, math.floor(y0)),
            min(image.width, math.ceil(x1)),
            min(image.height, math.ceil(y1)),
        )
        for x0, y0, x1, y1 in lines
    ]
    # Lines clamped away read as nothing, which the OCR verdict reports as a failure.
    boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]] or [(0, 0, 1, 1)]
    stacked = ocr_mod.stack_lines(
        [image.crop(b) for b in boxes], statistics.median(b[3] - b[1] for b in boxes)
    )
    union = (min(b[0] for b in boxes), min(b[1] for b in boxes))
    stacked.info["qc_crop"] = (at_ms, *union, max(b[2] for b in boxes), max(b[3] for b in boxes))
    return stacked


def _narrower(rect: PageRect, inset: float) -> PageRect:
    return rect.model_copy(update={"x": rect.x + inset, "width": max(1.0, rect.width - 2 * inset)})


def _padded_box(box: PixelBox, pad: int = OCR_PAD_PX) -> PixelBox:
    return PixelBox(
        x=max(0, box.x - pad),
        y=max(0, box.y - pad),
        width=box.width + 2 * pad,
        height=box.height + 2 * pad,
    )


def _scaled(box: PixelBox, scale: float) -> PixelBox:
    x0, y0 = math.floor(box.x * scale), math.floor(box.y * scale)
    x1, y1 = math.ceil((box.x + box.width) * scale), math.ceil((box.y + box.height) * scale)
    return PixelBox(x=x0, y=y0, width=max(1, x1 - x0), height=max(1, y1 - y0))


def _union_box(rects: Sequence[tuple[float, float, float, float]]) -> PixelBox | None:
    x0 = max(0.0, min(r[0] for r in rects) - OCR_PAD_PX)
    y0 = max(0.0, min(r[1] for r in rects) - OCR_PAD_PX)
    x1 = min(float(CANVAS_WIDTH), max(r[2] for r in rects) + OCR_PAD_PX)
    y1 = min(float(CANVAS_HEIGHT), max(r[3] for r in rects) + OCR_PAD_PX)
    if x1 <= x0 or y1 <= y0:
        return None
    return PixelBox(
        x=math.floor(x0), y=math.floor(y0), width=math.ceil(x1 - x0), height=math.ceil(y1 - y0)
    )


def _same_rects(a: Sequence[PageRect], b: Sequence[PageRect]) -> bool:
    if len(a) != len(b):
        return False
    return all(
        max(abs(p.x - q.x), abs(p.y - q.y), abs(p.width - q.width), abs(p.height - q.height))
        <= RECT_TOLERANCE_PX
        for p, q in zip(a, b, strict=True)
    )


def _rects(rects: Sequence[PageRect]) -> str:
    return "; ".join(f"({r.x:g},{r.y:g}) {r.width:g}x{r.height:g}" for r in rects)


def _inside(box: PixelBox, area: PixelBox) -> bool:
    return (
        box.x >= area.x
        and box.y >= area.y
        and box.x + box.width <= area.x + area.width
        and box.y + box.height <= area.y + area.height
    )


def _intersects(a: PixelBox, b: PixelBox) -> bool:
    return (
        a.x < b.x + b.width
        and b.x < a.x + a.width
        and a.y < b.y + b.height
        and b.y < a.y + a.height
    )


def _describe(box: PixelBox) -> str:
    return f"box ({box.x},{box.y}) {box.width}x{box.height}"
