"""Deterministic QC over frames a fake renderer draws from the bundle: every check both ways."""

from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from content_factory.explainer.color import rgb_of
from content_factory.explainer.colors import token_hex
from content_factory.explainer.compile import compile_episode
from content_factory.explainer.layout import region
from content_factory.explainer.qc import QcFinding, ink_hex_for, series_points, visible_at
from content_factory.explainer.qc_checks import (
    CHECKS,
    TEXT_LC_MIN,
    run_deterministic_qc,
    sampled_ms,
)
from content_factory.explainer.source_scene import PageGeometry, camera_at, screen_rect
from content_factory.schemas.audio import LoudnessReport
from content_factory.schemas.explainer import (
    AlignedWord,
    Alignment,
    ChartTemplate,
    CompiledExplainerScene,
    EvidencePack,
    ExplainerRenderBundle,
    NarrationManifest,
    NarrationTake,
    PixelBox,
    ScriptPlan,
    SourceDocumentTemplate,
    VoiceSpec,
)

REPO = Path(__file__).resolve().parents[2]
REVIEW_SET = REPO / "fixtures" / "explainer" / "review_set"
MISSING_MP4 = REVIEW_SET / "no-such-render.mp4"
PAIRS: dict[str, str] = {
    "tiny_text": "text_floor",
    "clipping": "clipping",
    "wrong_highlight": "ocr_text",
    "negation_omitted": "ocr_text",
    "axis_change": "axis_change",
    "narration_visual_mismatch": "narration_visual",
    "broken_transition": "transition",
}
SURFACE = rgb_of(token_hex("ui.surface.0"))
PAPER = (255, 255, 255)
PAGE_INK = (17, 17, 17)
LOW_CONTRAST_PANEL = (150, 150, 150)
# 12 px of background margin on the 360 px decode is 64 px at 1080: the panel must reach past it.
PANEL_MARGIN_PX = 70
Rgb = tuple[int, int, int]
Rect = tuple[int, int, int, int]
Pair = tuple[EvidencePack, ScriptPlan, ExplainerRenderBundle, ExplainerRenderBundle]


def _pair(name: str) -> Pair:
    pack = EvidencePack.model_validate_json((REVIEW_SET / "pack.json").read_text())
    folder = REVIEW_SET / name
    script = ScriptPlan.model_validate_json((folder / "script.json").read_text())
    accepted = ExplainerRenderBundle.model_validate_json((folder / "accepted.json").read_text())
    rejected = ExplainerRenderBundle.model_validate_json((folder / "rejected.json").read_text())
    return pack, script, accepted, rejected


class FakeRenderer:
    """Frames from the bundle alone: ink blocks in text boxes, series lines, quote blocks."""

    INSET = 10
    LINE_PX = 18

    def __init__(
        self,
        bundle: ExplainerRenderBundle,
        *,
        low_contrast_text: bool = False,
        dim_lines: bool = False,
        spill: frozenset[str] = frozenset(),
    ) -> None:
        self.bundle = bundle
        self.low_contrast_text, self.dim_lines, self.spill = low_contrast_text, dim_lines, spill
        self.scenes = {s.scene_id: s for s in bundle.spec.scenes}
        self.datasets = {d.dataset_id: d for d in bundle.datasets}
        self.assets = {a.asset_id: a for a in bundle.spec.assets}
        self.captures = {c.capture_id: c for c in bundle.captures}
        self.drawn: dict[int, list[tuple[Rect, str]]] = {}

    def __call__(
        self, mp4: Path, timestamps_ms: Sequence[int], *, width: int | None = None
    ) -> list[Image.Image]:
        return [self._frame(ms, width) for ms in timestamps_ms]

    def ocr(self, crops: Sequence[Image.Image]) -> list[str]:
        """What a perfect OCR would read: every drawn text whose block lies inside the crop."""
        read: list[str] = []
        for crop in crops:
            at_ms, x0, y0, x1, y1 = crop.info["qc_crop"]
            inside = [
                text
                for (bx0, by0, bx1, by1), text in self.drawn.get(at_ms, [])
                if text and bx0 >= x0 - 1 and by0 >= y0 - 1 and bx1 <= x1 + 1 and by1 <= y1 + 1
            ]
            read.append(" ".join(inside))
        return read

    def _frame(self, ms: int, width: int | None) -> Image.Image:
        timeline = self.bundle.timeline
        frame = min(timeline.total_frames - 1, math.ceil(ms * timeline.fps / 1000 - 1e-6))
        compiled = next(
            s for s in timeline.scenes if s.start_frame <= frame < s.start_frame + s.duration_frames
        )
        scene = self.scenes[compiled.scene_id]
        image = Image.new("RGB", (timeline.width, timeline.height), SURFACE)
        draw = ImageDraw.Draw(image)
        drawn = self.drawn[ms] = []
        template = scene.template
        if isinstance(template, SourceDocumentTemplate):
            self._page(draw, scene, compiled, template, frame, drawn)
        else:
            visible = visible_at(scene, compiled, frame)
            if isinstance(template, ChartTemplate):
                self._series(draw, compiled, template, visible)
            self._texts(draw, compiled, visible, drawn)
        if width is not None:
            height = round(timeline.height * width / timeline.width)
            image = image.resize((width, height), Image.Resampling.BILINEAR)
        return image

    def _texts(
        self,
        draw: ImageDraw.ImageDraw,
        compiled: CompiledExplainerScene,
        visible: set[str],
        drawn: list[tuple[Rect, str]],
    ) -> None:
        for box in compiled.boxes:
            if box.font_px is None or box.entity_id not in visible:
                continue
            b = box.box
            inset = min(self.INSET, b.width // 3, b.height // 3)
            if self.low_contrast_text:
                m = PANEL_MARGIN_PX
                panel = (b.x - m, b.y - m, b.x + b.width + m, b.y + b.height + m)
                draw.rectangle(panel, fill=LOW_CONTRAST_PANEL)
            x1 = b.x + b.width - inset + (12 if box.entity_id in self.spill else 0)
            width, height = draw.im.size
            rect = (b.x + inset, b.y + inset, min(width, x1), min(height, b.y + b.height - inset))
            draw.rectangle(rect, fill=rgb_of(ink_hex_for(box.font_px)))
            drawn.append((rect, box.text or ""))

    def _series(
        self,
        draw: ImageDraw.ImageDraw,
        compiled: CompiledExplainerScene,
        chart: ChartTemplate,
        visible: set[str],
    ) -> None:
        dataset = self.datasets[self.assets[chart.dataset_asset_id].dataset_id or ""]
        plot = next(r.box for r in compiled.regions if r.name == "plot")
        colors = {c.entity_id: rgb_of(c.srgb_hex) for c in compiled.colors}
        for binding in chart.series:
            if binding.entity_id not in visible:
                continue
            points = series_points(chart, dataset, binding, plot)
            dim: Rgb = (SURFACE[0] + 20, SURFACE[1] + 20, SURFACE[2] + 20)
            color = dim if self.dim_lines else colors[binding.entity_id]
            draw.line(points, fill=color, width=self.LINE_PX)

    def _page(
        self,
        draw: ImageDraw.ImageDraw,
        scene,
        compiled: CompiledExplainerScene,
        template: SourceDocumentTemplate,
        frame: int,
        drawn: list[tuple[Rect, str]],
    ) -> None:
        capture = self.captures[self.assets[template.capture_asset_id].capture_id or ""]
        manifest = capture.manifest
        page = region(scene, "page")
        draw.rectangle((page.x, page.y, page.x + page.width, page.y + page.height), fill=PAPER)
        geometry = PageGeometry(page, float(manifest.viewport.width), manifest.page_height_px)
        camera = camera_at(compiled.camera, frame)
        for quote in manifest.quotes:
            shown = [screen_rect(r, camera, geometry) for r in quote.line_rects]
            for x0, y0, x1, y1 in shown:
                clipped = (
                    max(page.x, round(x0)),
                    max(page.y, round(y0)),
                    min(page.x + page.width, round(x1)),
                    min(page.y + page.height, round(y1)),
                )
                if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                    draw.rectangle(clipped, fill=PAGE_INK)
            union = (
                round(min(r[0] for r in shown)),
                round(min(r[1] for r in shown)),
                round(max(r[2] for r in shown)),
                round(max(r[3] for r in shown)),
            )
            drawn.append((union, quote.text))


def _qc(
    pack: EvidencePack,
    script: ScriptPlan,
    bundle: ExplainerRenderBundle,
    renderer: FakeRenderer | None = None,
    mp4: Path = MISSING_MP4,
    *,
    narration: NarrationManifest | None = None,
    mix_loudness: LoudnessReport | None = None,
) -> list[QcFinding]:
    renderer = renderer or FakeRenderer(bundle)
    return run_deterministic_qc(
        bundle,
        mp4,
        pack=pack,
        script=script,
        spec=bundle.spec,
        narration=narration,
        mix_loudness=mix_loudness,
        decode=renderer,
        ocr=renderer.ocr,
    )


def _failed(findings: Sequence[QcFinding]) -> set[str]:
    return {f.check for f in findings if f.passed is False}


def _by_check(findings: Sequence[QcFinding], check: str) -> list[QcFinding]:
    return [f for f in findings if f.check == check]


def _with_scene(
    bundle: ExplainerRenderBundle,
    scene_id: str,
    change: Callable[[CompiledExplainerScene], CompiledExplainerScene],
) -> ExplainerRenderBundle:
    scenes = tuple(change(s) if s.scene_id == scene_id else s for s in bundle.timeline.scenes)
    timeline = bundle.timeline.model_copy(update={"scenes": scenes})
    return ExplainerRenderBundle.model_validate(
        bundle.model_copy(update={"timeline": timeline}).model_dump()
    )


def _move_box(
    bundle: ExplainerRenderBundle, entity_id: str, **fields: int
) -> ExplainerRenderBundle:
    scene_id = bundle.timeline.scenes[0].scene_id

    def change(compiled: CompiledExplainerScene) -> CompiledExplainerScene:
        boxes = tuple(
            b.model_copy(update={"box": b.box.model_copy(update=fields)})
            if b.entity_id == entity_id
            else b
            for b in compiled.boxes
        )
        return compiled.model_copy(update={"boxes": boxes})

    return _with_scene(bundle, scene_id, change)


# --- the fixture pairs ---


@pytest.mark.parametrize("name", sorted(PAIRS))
def test_rejected_variant_fails_exactly_its_check(name: str) -> None:
    pack, script, _, rejected = _pair(name)
    findings = _qc(pack, script, rejected)
    assert _failed(findings) == {PAIRS[name]}, [
        (f.check, f.entity_id, f.evidence) for f in findings if f.passed is False
    ]


@pytest.mark.parametrize("name", sorted(PAIRS))
def test_accepted_variant_has_no_blocker(name: str) -> None:
    pack, script, accepted, _ = _pair(name)
    findings = _qc(pack, script, accepted)
    assert _failed(findings) == set(), [
        (f.check, f.entity_id, f.evidence) for f in findings if f.passed is False
    ]
    assert {f.check for f in findings} == set(CHECKS)
    unknown = {f.check for f in findings if f.passed is None}
    assert unknown <= {
        "duration",
        "true_peak",
        "loudness",
        "cue_coverage",
        "small_screen",
        "text_apca",
    }


def test_wrong_highlight_names_the_rects_and_reads_nothing_under_the_shifted_overlay() -> None:
    pack, script, _, rejected = _pair("wrong_highlight")
    failed = [f for f in _qc(pack, script, rejected) if f.passed is False]
    assert {f.entity_id for f in failed} == {"qt_passage00001"}
    assert any("not the quote's padded line rects" in f.evidence for f in failed)
    assert any("no text recognised" in f.evidence for f in failed)


def test_negation_omitted_is_caught_before_and_by_the_ocr() -> None:
    pack, script, _, rejected = _pair("negation_omitted")
    failed = [f for f in _qc(pack, script, rejected) if f.passed is False]
    assert {f.entity_id for f in failed} == {"ent_quote_txt"}
    assert any("compiled text is not the item text" in f.evidence for f in failed)
    assert any("words only in the manifest: 'not'" in f.evidence for f in failed)


def test_axis_change_names_both_domains_and_the_missing_disclosure() -> None:
    pack, script, accepted, rejected = _pair("axis_change")
    (bad,) = [f for f in _qc(pack, script, rejected) if f.passed is False]
    assert bad.scene_id == "scn_dwell_busy0" and bad.entity_id == "ent_dwell_ser"
    assert "(0.0, 40.0)" in bad.evidence and "(0.0, 400.0)" in bad.evidence
    assert "no annotate or title says so" in bad.evidence
    (good,) = _by_check(_qc(pack, script, accepted), "axis_change")
    assert good.passed is True and "disclosed" in good.evidence


def test_broken_transition_names_the_action_and_the_boundary() -> None:
    pack, script, _, rejected = _pair("broken_transition")
    (bad,) = [f for f in _qc(pack, script, rejected) if f.passed is False]
    boundary = rejected.timeline.scenes[1].start_frame
    assert bad.scene_id == "scn_open0000001" and f"boundary at {boundary}" in bad.evidence
    assert bad.measured == boundary + 30 and bad.threshold == boundary


def test_sampled_ms_covers_action_ends_hold_midpoints_and_scene_edges() -> None:
    _, _, accepted, _ = _pair("broken_transition")
    fps = accepted.timeline.fps
    for compiled in accepted.timeline.scenes:
        samples = sampled_ms(accepted)[compiled.scene_id]
        last = min(
            accepted.timeline.total_frames - 1, compiled.start_frame + compiled.duration_frames - 1
        )
        expected = {compiled.start_frame, compiled.start_frame + 1, last}
        for action in compiled.actions:
            expected.add(action.end_frame)
            if action.action == "hold":
                expected.add((action.start_frame + action.end_frame) // 2)
        assert samples == sorted({math.floor(min(f, last) * 1000 / fps) for f in expected})


# --- the remaining checks, one accepted bundle mutated at a time ---


def test_contract_fails_on_an_unfrozen_pack() -> None:
    pack, script, accepted, _ = _pair("tiny_text")
    unfrozen = pack.model_copy(update={"frozen_at": None})
    findings = _qc(unfrozen, script, accepted)
    assert _failed(findings) == {"contract"}
    (bad,) = [f for f in findings if f.passed is False]
    assert "[stale_binding]" in bad.evidence and "is not frozen" in bad.evidence


def test_evidence_fails_on_a_dataset_cell_that_matches_no_claim() -> None:
    pack, script, accepted, _ = _pair("axis_change")
    quiet = next(d for d in pack.datasets if d.dataset_id == "ds_dwell_quiet")
    rows = (quiet.rows[0].model_copy(update={"values": ("A", 11.0)}), *quiet.rows[1:])
    broken = quiet.model_copy(update={"rows": rows})
    bad_pack = pack.model_copy(
        update={"datasets": tuple(broken if d is quiet else d for d in pack.datasets)}
    )
    findings = _qc(bad_pack, script, accepted)
    assert {"evidence", "contract"} <= _failed(findings)
    (cell,) = [f for f in findings if f.check == "evidence"]
    assert "matches no claim" in cell.evidence and "[dataset_mismatch]" in cell.evidence
    assert all("dataset_mismatch" not in f.evidence for f in _by_check(findings, "contract"))


def test_assets_fails_when_a_tile_is_missing_or_its_hash_differs() -> None:
    pack, script, accepted, _ = _pair("wrong_highlight")
    capture = accepted.captures[0]
    ok = _by_check(_qc(pack, script, accepted), "assets")
    assert ok and all(f.passed for f in ok) and len(ok) == len(capture.tiles)
    gone = capture.tiles[0].model_copy(update={"path": "fixtures/explainer/review_set/nope.png"})
    wrong = capture.tiles[1].model_copy(update={"sha256": hashlib.sha256(b"other").hexdigest()})
    broken = accepted.model_copy(
        update={"captures": (capture.model_copy(update={"tiles": (gone, wrong)}),)}
    )
    bad = [f for f in _qc(pack, script, broken) if f.check == "assets" and f.passed is False]
    assert [("missing" in f.evidence, "hashes to" in f.evidence) for f in bad] == [
        (True, False),
        (False, True),
    ]


def test_capability_fails_on_an_action_the_template_cannot_do() -> None:
    pack, script, accepted, _ = _pair("axis_change")

    def flow(compiled: CompiledExplainerScene) -> CompiledExplainerScene:
        actions = tuple(
            a.model_copy(update={"action": "flow"}) if a.action == "hold" else a
            for a in compiled.actions
        )
        return compiled.model_copy(update={"actions": actions})

    broken = _with_scene(accepted, "scn_dwell_quiet", flow)
    findings = _qc(pack, script, broken)
    assert _failed(findings) == {"capability"}
    (bad,) = [f for f in findings if f.passed is False]
    assert bad.evidence == "chart template cannot flow" and bad.scene_id == "scn_dwell_quiet"


def _manifest(
    script: ScriptPlan, *, drop: frozenset[tuple[str, int]] = frozenset()
) -> NarrationManifest:
    takes: list[NarrationTake] = []
    offset = 0
    for segment in script.segments:
        count = len(segment.tokens)
        words = tuple(
            AlignedWord(
                segment_id=segment.segment_id,
                token_index=k,
                start_ms=k * 385,
                end_ms=(k + 1) * 385,
                confidence=1.0,
            )
            for k in range(count)
            if (segment.segment_id, k) not in drop
        )
        takes.append(
            NarrationTake(
                take_id=f"take_{segment.segment_id[4:]}",
                segment_ids=(segment.segment_id,),
                kind="synthesized",
                audio_sha256=hashlib.sha256(segment.segment_id.encode()).hexdigest(),
                start_ms=offset,
                duration_ms=count * 385,
                sample_rate_hz=48000,
                alignment=Alignment(aligner="fake", aligner_version="0", words=words),
            )
        )
        offset += count * 385
    return NarrationManifest(
        manifest_id="nm_reviewset001",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        voice=VoiceSpec(kind="designed", voice_id="fake"),
        takes=tuple(takes),
        total_duration_ms=offset,
        created_at="2026-09-23",
    )


def test_cue_coverage_needs_an_aligned_word_for_every_cue_token() -> None:
    pack, script, accepted, _ = _pair("tiny_text")
    without = _by_check(_qc(pack, script, accepted), "cue_coverage")
    assert [f.passed for f in without] == [None, True]
    assert "no narration manifest" in without[0].evidence
    aligned = _qc(pack, script, accepted, narration=_manifest(script))
    assert all(f.passed for f in _by_check(aligned, "cue_coverage"))
    assert _failed(aligned) == set()
    gappy = _manifest(script, drop=frozenset({("seg_open00001", 2)}))
    findings = _qc(pack, script, accepted, narration=gappy)
    assert _failed(findings) == {"cue_coverage"}
    (bad,) = [f for f in findings if f.passed is False]
    assert "bt_open_hold000" in bad.evidence and "tokens [2]" in bad.evidence


def test_ink_overflow_fails_when_ink_spills_past_the_measured_box() -> None:
    pack, script, accepted, _ = _pair("tiny_text")
    findings = _qc(
        pack, script, accepted, FakeRenderer(accepted, spill=frozenset({"ent_open_stmt"}))
    )
    assert _failed(findings) == {"ink_overflow"}
    (bad,) = [f for f in findings if f.passed is False]
    assert bad.measured and bad.measured > 0 and "ink pixels in the 6 px ring" in bad.evidence


def test_overlap_fails_when_two_visible_boxes_intersect() -> None:
    pack, script, accepted, _ = _pair("negation_omitted")
    quote = next(b for b in accepted.timeline.scenes[0].boxes if b.entity_id == "ent_quote_txt").box
    touching = _move_box(accepted, "ent_quote_att", y=quote.y + quote.height - 2)
    findings = _qc(pack, script, touching)
    assert _failed(findings) == {"overlap"}
    (bad,) = [f for f in findings if f.passed is False]
    assert "ent_quote_txt" in bad.evidence and "ent_quote_att" in bad.evidence


def test_safe_area_fails_for_a_box_inside_the_canvas_but_outside_the_margin() -> None:
    pack, script, accepted, _ = _pair("tiny_text")
    findings = _qc(pack, script, _move_box(accepted, "ent_open_stmt", x=10))
    assert _failed(findings) == {"safe_area"}
    assert "leaves the safe area" in next(f for f in findings if f.passed is False).evidence


def _tiny_mp4(path: Path, frames: int, fps: int) -> Path:
    args = [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=64x36:r={fps}",
        "-frames:v", str(frames), "-pix_fmt", "yuv420p", str(path),
    ]  # fmt: skip
    subprocess.run(args, check=True)
    return path


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not on PATH"
)
def test_duration_compares_the_probed_frame_count_with_the_timeline(tmp_path: Path) -> None:
    pack, script, accepted, _ = _pair("tiny_text")
    total, fps = accepted.timeline.total_frames, accepted.timeline.fps
    exact = _tiny_mp4(tmp_path / "exact.mp4", total, fps)
    (ok,) = _by_check(_qc(pack, script, accepted, mp4=exact), "duration")
    assert ok.passed is True and ok.measured == total
    short = _tiny_mp4(tmp_path / "short.mp4", total - 5, fps)
    (bad,) = _by_check(_qc(pack, script, accepted, mp4=short), "duration")
    assert bad.passed is False and bad.measured == total - 5 and bad.threshold == total
    (unknown,) = _by_check(_qc(pack, script, accepted), "duration")
    assert unknown.passed is None and "ffprobe could not read" in unknown.evidence


def test_true_peak_and_loudness_read_the_mix_report() -> None:
    pack, script, accepted, _ = _pair("tiny_text")
    good = LoudnessReport(integrated_lufs=-14.4, true_peak_dbtp=-1.3)
    findings = _qc(pack, script, accepted, mix_loudness=good)
    assert [f.passed for f in findings if f.check in {"true_peak", "loudness"}] == [True, True]
    loud = LoudnessReport(integrated_lufs=-11.0, true_peak_dbtp=-0.2)
    findings = _qc(pack, script, accepted, mix_loudness=loud)
    assert _failed(findings) == {"true_peak", "loudness"}
    peak, lufs = (next(f for f in findings if f.check == c) for c in ("true_peak", "loudness"))
    assert peak.measured == -0.2 and peak.threshold == -1.0
    assert lufs.measured == 3.0 and lufs.threshold == 1.0
    findings = _qc(pack, script, accepted)
    assert [f.passed for f in findings if f.check in {"true_peak", "loudness"}] == [None, None]


def test_text_contrast_fails_at_full_size_and_on_the_small_screen_when_the_ground_is_light() -> (
    None
):
    pack, script, accepted, _ = _pair("tiny_text")
    findings = _qc(pack, script, accepted, FakeRenderer(accepted, low_contrast_text=True))
    failed = _failed(findings)
    assert {"text_apca", "small_screen"} <= failed <= {"text_apca", "small_screen", "ink_overflow"}
    small = [f for f in findings if f.check == "small_screen" and f.passed is False]
    assert small and all(f.measured is not None and f.measured < TEXT_LC_MIN for f in small)
    assert all("at 360 px wide" in f.evidence for f in small)


def test_legend_contrast_alone_fails_when_only_label_text_sits_on_a_light_ground() -> None:
    pack, script, accepted, _ = _pair("axis_change")
    findings = _qc(pack, script, accepted, FakeRenderer(accepted, low_contrast_text=True))
    assert _failed(findings) == {"text_apca"}
    small = [f for f in findings if f.check == "small_screen"]
    assert [(f.entity_id, f.evidence) for f in small] == [(None, "no read text to measure")]


def test_line_edge_fails_when_the_series_line_has_no_contrast() -> None:
    pack, script, accepted, _ = _pair("axis_change")
    clear = _by_check(_qc(pack, script, accepted), "line_edge")
    assert clear and all(f.passed for f in clear)
    findings = _qc(pack, script, accepted, FakeRenderer(accepted, dim_lines=True))
    assert _failed(findings) == {"line_edge"}
    assert all(f.entity_id == "ent_dwell_ser" for f in findings if f.passed is False)


def test_misleading_axis_flags_an_axis_unit_that_is_not_the_data_unit() -> None:
    pack, script, accepted, _ = _pair("axis_change")
    spec = accepted.spec
    first = spec.scenes[0]
    assert isinstance(first.template, ChartTemplate)
    template = first.template.model_copy(
        update={"y": first.template.y.model_copy(update={"unit": "min"})}
    )
    scenes = (first.model_copy(update={"template": template}), *spec.scenes[1:])
    bundle = compile_episode(pack, script, spec.model_copy(update={"scenes": scenes}))
    findings = _qc(pack, script, bundle)
    assert _failed(findings) == {"misleading_axis"}
    (bad,) = [f for f in findings if f.passed is False]
    assert bad.evidence == "axis says min but the data is s"


def test_ocr_text_is_unknown_with_the_reason_when_the_ocr_venv_is_missing() -> None:
    pack, script, accepted, _ = _pair("negation_omitted")
    renderer = FakeRenderer(accepted)

    def no_ocr(crops: Sequence[Image.Image]) -> list[str]:
        msg = "PaddleOCR venv missing at .venvs/paddleocr/bin/python"
        raise RuntimeError(msg)

    findings = run_deterministic_qc(
        accepted,
        MISSING_MP4,
        pack=pack,
        script=script,
        spec=accepted.spec,
        decode=renderer,
        ocr=no_ocr,
    )
    ocr = _by_check(findings, "ocr_text")
    assert ocr and all(f.passed is None for f in ocr)
    assert all(f.evidence.startswith("OCR unavailable: PaddleOCR venv missing") for f in ocr)
    assert _failed(findings) == set()


def test_ocr_reads_only_items_visible_at_the_hold() -> None:
    pack, script, accepted, _ = _pair("negation_omitted")

    def unseen(compiled: CompiledExplainerScene) -> CompiledExplainerScene:
        actions = tuple(
            a.model_copy(update={"targets": ("ent_quote_txt",)}) if a.action == "reveal" else a
            for a in compiled.actions
        )
        return compiled.model_copy(update={"actions": actions})

    hidden = _with_scene(accepted, accepted.timeline.scenes[0].scene_id, unseen)
    ocr = _by_check(_qc(pack, script, hidden), "ocr_text")
    assert [f.entity_id for f in ocr] == ["ent_quote_txt"] and ocr[0].passed is True


def test_every_finding_carries_a_scene_and_a_reason() -> None:
    pack, script, accepted, _ = _pair("wrong_highlight")
    scenes = {s.scene_id for s in accepted.timeline.scenes}
    for finding in _qc(pack, script, accepted):
        assert finding.scene_id in scenes and finding.evidence
        assert (finding.passed is None) == (finding.measured is None) or finding.check == "ocr_text"
        assert isinstance(finding.at_ms, int) and finding.at_ms >= 0


def test_pixel_box_mutations_keep_the_bundle_valid() -> None:
    _, _, accepted, rejected = _pair("clipping")
    box = next(b for b in rejected.timeline.scenes[0].boxes if b.entity_id == "ent_open_stmt").box
    assert isinstance(box, PixelBox) and box.x + box.width > rejected.timeline.width
    assert rejected.timeline.spec_hash == accepted.timeline.spec_hash
