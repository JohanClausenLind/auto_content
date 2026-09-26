"""Gate D1: a real capture as a source scene; scroll, zoom, highlight, legibility, determinism."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from content_factory.explainer import render
from content_factory.explainer.capture import CaptureResult, capture_url
from content_factory.explainer.color import apca_lc
from content_factory.explainer.compile import compile_episode, write_bundle
from content_factory.explainer.errors import EpisodeInvalidError
from content_factory.explainer.layout import region
from content_factory.explainer.passages import QuoteRequest, ResolvedPage, Tile, resolve_quotes
from content_factory.explainer.replay import ReplayServer
from content_factory.explainer.source_scene import (
    ALPHA_FAMILY,
    AMBER,
    ZOOM_MS,
    PageGeometry,
    blend_multiply,
    camera_at,
    line_progress,
    overlay_rgba,
    screen_rect,
)
from content_factory.explainer.sources import build_manifest, capture_asset
from content_factory.explainer.timing import to_frames
from content_factory.schemas.explainer import (
    MAX_ZOOM,
    Action,
    AssetRef,
    Beat,
    CaptureAsset,
    CaptureQuote,
    CaptureSection,
    Claim,
    CompiledExplainerScene,
    Cue,
    DomRangeLocator,
    DurationClass,
    Entity,
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    ExplainerRenderBundle,
    HoldAction,
    PageRect,
    PixelBox,
    QuoteAction,
    Scene,
    ScriptPlan,
    ScriptSegment,
    ScrollToAction,
    ShowSourceAction,
    SourceCaptureManifest,
    SourceDocumentTemplate,
    TitlePromise,
    Viewport,
    VisualSpec,
    tokenize,
)
from content_factory.schemas.research import EvidenceLocator
from tests.unit.explainer_site import FixtureSite
from tests.unit.test_explainer_capture import BELOW_FOLD, MULTI_LINE, SITE, SOURCE, VIEWPORT

SEG = "seg_source00001"
SCENE = "scn_source00001"
SPOKEN = (
    "The measurements themselves say it plainly: a faster tram does not shorten the trip, "
    "because every second gained is spent standing at the busiest stops along the line"
)
SHA = hashlib.sha256(b"synthetic").hexdigest()
INK = (17, 17, 17)
GREY = (150, 150, 150)
WIDE = (
    PageRect(x=200, y=1000, width=760, height=22),
    PageRect(x=200, y=1032, width=700, height=22),
    PageRect(x=200, y=1064, width=520, height=22),
)
NARROW = (PageRect(x=300, y=1300, width=200, height=22),)
Rgb = tuple[int, int, int]


def _beat(beat_id: str, token: int, *actions: Action, duration: DurationClass = "short") -> Beat:
    cue = Cue(segment_id=SEG, token_start=token, token_end=token, duration_class=duration)
    return Beat(beat_id=beat_id, cue=cue, actions=actions)


def _episode(
    manifest: SourceCaptureManifest, quote: CaptureQuote
) -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    source = EvidenceSource(
        source_id=manifest.source_id,
        url=manifest.url,
        canonical_url=manifest.url,
        publisher=manifest.publisher,
        title=manifest.title,
        accessed_at="2026-09-22",
        content_sha256=manifest.artifact_sha256,
        capture_id=manifest.capture_id,
    )
    item = EvidenceItem(
        item_id="evi_dwell000001",
        source_id=manifest.source_id,
        passage=quote.text,
        locator=EvidenceLocator(kind="char_range", start=0, end=len(quote.text)),
        quoted_at="2026-09-22",
    )
    claim = Claim(
        claim_id="clm_dwell000001",
        statement="The gain from a faster tram is spent at the stops",
        epistemic_class="observation",
        evidence_ids=(item.item_id,),
        rationale="stated in the source",
        checked_at="2026-09-22",
    )
    pack = EvidencePack(
        pack_id="pack_sourcegate1",
        topic="Why the tram is late",
        sources=(source,),
        items=(item,),
        claims=(claim,),
    ).frozen("2026-09-22T00:00:00Z")
    segment = ScriptSegment(
        segment_id=SEG,
        section="build_model",
        spoken_text=SPOKEN,
        tokens=tokenize(SPOKEN),
        claim_ids=(claim.claim_id,),
    )
    script = ScriptPlan(
        script_id="scr_sourcegate1",
        channel_id="ch_explain01",
        pack_id=pack.pack_id,
        pack_hash=pack.pack_hash(),
        question="Where do the tram's minutes go?",
        contribution="Read the measurement sentence in its source.",
        promises=(
            TitlePromise(
                title="Doors, not motors",
                thumbnail_promise="The seconds go to the stops",
                claim_ids=(claim.claim_id,),
            ),
        ),
        segments=(segment,),
        locked_at="2026-09-22T00:00:00Z",
    )
    template = SourceDocumentTemplate(
        template="source_document",
        capture_asset_id="ast_capture0001",
        initial_section_id=manifest.sections[0].section_id,
    )
    scene = Scene(
        scene_id=SCENE,
        section="build_model",
        purpose="Read the passage in its source",
        template=template,
        beats=(
            _beat("bt_show000001", 0, ShowSourceAction(action="show_source")),
            _beat(
                "bt_scroll00001", 2, ScrollToAction(action="scroll_to", section_id=quote.section_id)
            ),
            _beat(
                "bt_focus000001", 4, QuoteAction(action="focus_passage", quote_id=quote.quote_id)
            ),
            _beat(
                "bt_hilite00001", 6, QuoteAction(action="highlight_quote", quote_id=quote.quote_id)
            ),
            _beat("bt_hold0000001", 8, HoldAction(action="hold"), duration="long"),
        ),
        source_ids=(manifest.source_id,),
    )
    spec = VisualSpec(
        spec_id="spec_sourcegate",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        design_system_version=1,
        entities=(
            Entity(entity_id=quote.quote_id, label="the measurements passage", kind="quote"),
        ),
        assets=(
            AssetRef(
                asset_id="ast_capture0001",
                kind="capture",
                sha256=manifest.artifact_sha256,
                capture_id=manifest.capture_id,
            ),
        ),
        scenes=(scene,),
    )
    return pack, script, spec


def _synthetic_capture(into: Path, ink: Rgb = INK) -> CaptureAsset:
    """Two white tiles with ink blocks where the quotes' line rects say the words are."""
    width, height = 1280, 800
    tiles: list[Tile] = []
    for i, top in enumerate((0, 800)):
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        for rect in (*WIDE, *NARROW):
            y0 = rect.y - top
            if not -rect.height < y0 < height:
                continue
            x = rect.x
            while x + 30 <= rect.x + rect.width:
                draw.rectangle((x, y0 + 4, x + 30, y0 + rect.height - 4), fill=ink)
                x += 46
        path = into / f"tile-{i:04d}.png"
        image.save(path)
        tiles.append(Tile(path, top))
    locator = DomRangeLocator(
        kind="dom_range", start_path="/p", start_offset=0, end_path="/p", end_offset=8
    )
    manifest = SourceCaptureManifest(
        capture_id="cap_synthetic001",
        source_id="src_synthetic001",
        url="http://127.0.0.1/synthetic.html",
        publisher="Synthetic Notes",
        title="A page drawn for the test",
        captured_at="2026-09-22T00:00:00Z",
        capture_kind="wacz",
        artifact_sha256=SHA,
        viewport=Viewport(width=width, height=height),
        page_height_px=1600,
        text_sha256=SHA,
        extractor="synthetic",
        extractor_version="0",
        sections=(
            CaptureSection(section_id="sec_top00000001", heading="Top", order=0, scroll_y_px=0),
            CaptureSection(section_id="sec_deep0000001", heading="Deep", order=1, scroll_y_px=900),
        ),
        quotes=(
            CaptureQuote(
                quote_id="qt_widelines0001",
                section_id="sec_deep0000001",
                text="three lines of words drawn as blocks across the page",
                locator=locator,
                occurrence_index=0,
                line_rects=WIDE,
            ),
            CaptureQuote(
                quote_id="qt_narrowline001",
                section_id="sec_deep0000001",
                text="one short line",
                locator=locator,
                occurrence_index=0,
                line_rects=NARROW,
            ),
        ),
    )
    return capture_asset(manifest, tiles)


def _page_geometry(bundle: ExplainerRenderBundle, capture: CaptureAsset) -> PageGeometry:
    scene = bundle.spec.scenes[0]
    manifest = capture.manifest
    return PageGeometry(
        region(scene, "page"), float(manifest.viewport.width), manifest.page_height_px
    )


def test_focus_passage_never_zooms_past_the_ceiling_and_keys_stay_monotonic(
    tmp_path: Path,
) -> None:
    capture = _synthetic_capture(tmp_path)
    narrow = capture.manifest.quotes[1]
    pack, script, spec = _episode(capture.manifest, narrow)
    bundle = compile_episode(pack, script, spec, captures=(capture,))
    scene = bundle.timeline.scenes[0]
    geometry = _page_geometry(bundle, capture)
    assert geometry.focus(narrow).zoom == MAX_ZOOM
    assert 0.7 * geometry.region.width / (200 * geometry.base_scale) > MAX_ZOOM
    frames = [k.frame for k in scene.camera]
    assert frames == sorted(frames) and len(set(frames)) == len(frames)
    assert max(k.zoom for k in scene.camera) == MAX_ZOOM
    last = scene.start_frame + scene.duration_frames
    assert all(camera_at(scene.camera, f).zoom <= MAX_ZOOM for f in range(scene.start_frame, last))
    assert bundle.captures == (capture,)


def test_a_quote_starting_mid_line_is_framed_whole_on_both_lines() -> None:
    geometry = PageGeometry(PixelBox(x=96, y=54, width=1728, height=924), 1280.0, 5000.0)
    quote = CaptureQuote(
        quote_id="qt_midline00001",
        section_id="sec_deep0000001",
        text="a sentence that starts halfway along one line and ends on the next",
        locator=DomRangeLocator(
            kind="dom_range", start_path="/p", start_offset=0, end_path="/p", end_offset=8
        ),
        occurrence_index=0,
        line_rects=(
            PageRect(x=560, y=2000, width=660, height=24),
            PageRect(x=60, y=2030, width=640, height=24),
        ),
    )
    camera = geometry.focus(quote)
    for rect in quote.line_rects:
        x0, _, x1, _ = screen_rect(rect, camera, geometry)
        assert geometry.region.x <= x0 and x1 <= geometry.region.x + geometry.region.width


def test_source_actions_run_one_after_another(tmp_path: Path) -> None:
    capture = _synthetic_capture(tmp_path)
    pack, script, spec = _episode(capture.manifest, capture.manifest.quotes[0])
    scene = compile_episode(pack, script, spec, captures=(capture,)).timeline.scenes[0]
    ends = [a.end_frame for a in scene.actions]
    starts = [a.start_frame for a in scene.actions]
    assert all(s >= e for s, e in zip(starts[1:], ends, strict=False))
    assert [a.action for a in scene.actions] == [
        "show_source", "scroll_to", "focus_passage", "highlight_quote", "hold"
    ]  # fmt: skip
    (highlight,) = scene.highlights
    assert highlight.quote_id == "qt_widelines0001" and len(highlight.rects) == 3
    assert highlight.clear_start_frame is None
    # show_source jump, scroll origin and arrival, then the zoom's arrival: the moves keep "move".
    assert [k.easing for k in scene.camera] == ["hold", "hold", "move", "move"]
    scroll_end = camera_at(scene.camera, scene.camera[2].frame)
    mid = camera_at(scene.camera, (scene.camera[1].frame + scene.camera[2].frame) // 2)
    assert scene.camera[0].scroll_y < mid.scroll_y < scroll_end.scroll_y


def test_highlight_takes_the_strongest_alpha_that_keeps_ink_legible(tmp_path: Path) -> None:
    capture = _synthetic_capture(tmp_path)
    tiles = [Tile(Path(t.path), int(t.y_px)) for t in capture.tiles]
    rgba, alpha, lc = overlay_rgba(tiles, capture.manifest.quotes[0], "cap_synthetic001", "here")
    assert alpha == ALPHA_FAMILY[0] and lc >= 75
    assert rgba == f"rgba(255, 189, 74, {alpha})"


def test_grey_ink_fails_the_highlight_naming_the_quote_and_its_lc(tmp_path: Path) -> None:
    capture = _synthetic_capture(tmp_path, ink=GREY)
    pack, script, spec = _episode(capture.manifest, capture.manifest.quotes[0])
    with pytest.raises(EpisodeInvalidError) as info:
        compile_episode(pack, script, spec, captures=(capture,))
    (issue,) = info.value.issues
    assert issue.kind == "color"
    assert "qt_widelines0001" in issue.message and "APCA Lc" in issue.message
    assert f"alpha {ALPHA_FAMILY[-1]}" in issue.message


# --- gate D1 on the real fixture capture ---


@dataclass(frozen=True)
class Rendered:
    bundle: ExplainerRenderBundle
    scene: CompiledExplainerScene
    capture: CaptureAsset
    quote: CaptureQuote
    geometry: PageGeometry
    at: dict[str, int]
    frames: dict[str, np.ndarray]
    first: list[Path]
    second: list[Path]
    tint: Rgb


@pytest.fixture(scope="module")
def site() -> Iterator[FixtureSite]:
    with FixtureSite(SITE) as served:
        yield served


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("source-gate")


@pytest.fixture(scope="module")
def captured(site: FixtureSite, out_dir: Path) -> CaptureResult:
    return capture_url(
        site.url, out_dir, viewport=VIEWPORT, allow_loopback=True, provenance_summary=False
    )


@pytest.fixture(scope="module")
def resolved(captured: CaptureResult, site: FixtureSite, out_dir: Path) -> ResolvedPage:
    requests = (QuoteRequest(BELOW_FOLD), QuoteRequest(MULTI_LINE))
    with ReplayServer(captured.wacz_path) as replay:
        url = replay.replay_url(site.url)
        return resolve_quotes(url, requests, viewport=VIEWPORT, tiles_dir=out_dir / "tiles")


@pytest.fixture(scope="module")
def rendered(captured: CaptureResult, resolved: ResolvedPage, out_dir: Path) -> Rendered:
    manifest = build_manifest(SOURCE, captured, resolved)
    capture = capture_asset(manifest, resolved.tiles)
    quote = next(q for q in manifest.quotes if q.text == BELOW_FOLD)
    pack, script, spec = _episode(manifest, quote)
    bundle = compile_episode(pack, script, spec, captures=(capture,))
    scene = bundle.timeline.scenes[0]
    path = write_bundle(render.stage_captures(bundle), out_dir / "bundle.json")
    by_action = {a.action: a for a in scene.actions}
    (highlight,) = scene.highlights
    last = scene.start_frame + scene.duration_frames - 1
    zoom_frames = to_frames(ZOOM_MS, bundle.timeline.fps)
    at = {
        "show_end": by_action["show_source"].end_frame,
        "scroll_mid": (by_action["scroll_to"].start_frame + by_action["scroll_to"].end_frame) // 2,
        "scroll_end": by_action["scroll_to"].end_frame,
        "zoom_mid": by_action["focus_passage"].start_frame + zoom_frames // 2,
        "sweep_start": highlight.start_frame,
        "sweep_mid": (highlight.start_frame + highlight.end_frame) // 2,
        "sweep_end": highlight.end_frame,
        "hold_mid": (by_action["hold"].start_frame + by_action["hold"].end_frame) // 2,
        "hold_end": min(by_action["hold"].end_frame, last),
    }
    wanted = sorted(set(at.values()))
    first = render.render_frames(path, out_dir / "frames-a", wanted, "stills")
    second = render.render_frames(path, out_dir / "frames-b", wanted, "stills")
    by_frame = dict(zip(wanted, first, strict=True))
    frames = {name: _pixels(by_frame[frame]) for name, frame in at.items()}
    (out_dir / "report.json").write_text(
        json.dumps(
            {"at": at, "rgba": highlight.rgba, "camera": [k.model_dump() for k in scene.camera]}
        )
    )
    alpha = float(highlight.rgba.rsplit(",", 1)[1].rstrip(")"))
    tint = blend_multiply(AMBER, (255, 255, 255), alpha)
    return Rendered(
        bundle,
        scene,
        capture,
        quote,
        _page_geometry(bundle, capture),
        at,
        frames,
        first,
        second,
        tint,
    )


def _pixels(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.int32)


def _tinted(pixels: np.ndarray, tint: Rgb, tolerance: int = 12) -> np.ndarray:
    """Paper under the overlay: near the multiply blend on white; subpixel fringes fall outside."""
    return (np.abs(pixels - np.array(tint, dtype=np.int32)) <= tolerance).all(axis=-1)


def _crop(pixels: np.ndarray, box: tuple[float, float, float, float], inset: int = 0) -> np.ndarray:
    x0, y0, x1, y1 = box
    return pixels[round(y0) + inset : round(y1) - inset, round(x0) + inset : round(x1) - inset]


def _ring(
    pixels: np.ndarray, box: tuple[float, float, float, float], gap: int, thick: int
) -> np.ndarray:
    x0, y0, x1, y1 = (round(v) for v in box)
    outer = pixels[y0 - gap - thick : y1 + gap + thick, x0 - gap - thick : x1 + gap + thick]
    mask = np.ones(outer.shape[:2], dtype=bool)
    mask[thick:-thick, thick:-thick] = False
    return outer[mask]


def _legibility_lc(pixels: np.ndarray) -> float:
    """APCA between the darkest tenth of the pixels and the median of the rest (compiler model)."""
    flat = pixels.reshape(-1, 3).astype(np.float64)
    order = np.argsort(flat @ np.array([0.2126, 0.7152, 0.0722]), kind="stable")
    k = max(1, len(order) // 10)
    ink = tuple(round(float(v)) for v in np.median(flat[order[:k]], axis=0))
    rest = tuple(round(float(v)) for v in np.median(flat[order[k:]], axis=0))
    return abs(apca_lc((ink[0], ink[1], ink[2]), (rest[0], rest[1], rest[2])))


def _screen_rects(r: Rendered, frame: int) -> list[tuple[float, float, float, float]]:
    camera = camera_at(r.scene.camera, frame)
    (highlight,) = r.scene.highlights
    return [screen_rect(rect, camera, r.geometry) for rect in highlight.rects]


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _page(r: Rendered, frame_pixels: np.ndarray) -> np.ndarray:
    box = r.geometry.region
    return frame_pixels[box.y : box.y + box.height, box.x : box.x + box.width]


@pytest.mark.render
def test_d1_scroll_moves_the_page_before_any_highlight(rendered: Rendered) -> None:
    start, mid = (
        _page(rendered, rendered.frames["show_end"]),
        _page(rendered, rendered.frames["scroll_mid"]),
    )
    changed = float((np.abs(start - mid).sum(axis=2) > 30).mean())
    assert changed > 0.1, f"only {changed:.1%} of the page changed mid-scroll"
    for name in ("show_end", "scroll_mid", "zoom_mid"):
        tinted = float(_tinted(_page(rendered, rendered.frames[name]), rendered.tint).mean())
        assert tinted < 0.01, f"{name}: {tinted:.2%} of the page carries the overlay tint already"
    end = _page(rendered, rendered.frames["scroll_end"])
    assert float((np.abs(end - mid).sum(axis=2) > 30).mean()) > 0.1
    camera_mid = camera_at(rendered.scene.camera, rendered.at["scroll_mid"])
    camera_end = camera_at(rendered.scene.camera, rendered.at["scroll_end"])
    assert 0 < camera_mid.scroll_y < camera_end.scroll_y
    assert camera_end.zoom == 1.0
    assert 1.0 < camera_at(rendered.scene.camera, rendered.at["zoom_mid"]).zoom <= MAX_ZOOM


@pytest.mark.render
def test_d1_highlight_lands_inside_the_quote_rects_under_the_camera(rendered: Rendered) -> None:
    (highlight,) = rendered.scene.highlights
    for name in ("sweep_start", "sweep_mid", "sweep_end", "hold_end"):
        frame = rendered.at[name]
        pixels = rendered.frames[name]
        rects = _screen_rects(rendered, frame)
        for i, box in enumerate(rects):
            progress = line_progress(highlight, i, frame)
            fraction = float(_tinted(_crop(pixels, box, inset=2), rendered.tint).mean())
            if progress >= 1:
                assert fraction > 0.4, f"{name}: line {i} drawn but only {fraction:.1%} tinted"
            elif progress <= 0:
                assert fraction < 0.03, f"{name}: line {i} not yet due but {fraction:.1%} tinted"
        ring = _ring(pixels, _union(rects), gap=12, thick=6)
        outside = float(_tinted(ring, rendered.tint).mean())
        assert outside < 0.003, f"{name}: {outside:.2%} of the 12 px ring is tinted"
    assert all(
        line_progress(highlight, i, rendered.at["hold_end"]) == 1
        for i in range(len(highlight.rects))
    )


@pytest.mark.render
def test_d1_highlighted_text_stays_legible_at_sweep_end_and_hold_end(rendered: Rendered) -> None:
    for name in ("sweep_end", "hold_end"):
        for i, box in enumerate(_screen_rects(rendered, rendered.at[name])):
            lc = _legibility_lc(_crop(rendered.frames[name], box, inset=2))
            assert lc >= 75, f"{name}: line {i} reads at APCA Lc {lc:.1f}"


@pytest.mark.render
def test_d1_attribution_names_the_source_below_the_page(rendered: Rendered) -> None:
    box = rendered.geometry.region
    for name in ("show_end", "hold_end"):
        band = rendered.frames[name][
            box.y + box.height : box.y + box.height + 48, box.x : box.x + box.width
        ]
        assert int((band.sum(axis=2) > 400).sum()) > 200, (
            f"{name}: no attribution text under the page"
        )
    manifest = rendered.capture.manifest
    assert manifest.publisher == "Transit Notes" and manifest.title.startswith(
        "Why the tram is late"
    )


@pytest.mark.render
def test_d1_rendering_the_same_frames_twice_is_pixel_identical(rendered: Rendered) -> None:
    def sha(path: Path) -> str:
        with Image.open(path) as image:
            return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()

    assert [sha(p) for p in rendered.first] == [sha(p) for p in rendered.second]
