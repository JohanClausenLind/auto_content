"""Gates C2 and C4 offline: label bounds, colours, timing and a bundle that validates, no Node."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from content_factory.explainer import qc
from content_factory.explainer.colors import allocate_colors, token_hex
from content_factory.explainer.compile import compile_episode, write_bundle
from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.fonts import (
    FIT_SAFETY,
    FONTS_VERSION,
    fits,
    measure_text,
    wrap_text,
)
from content_factory.explainer.layout import LayoutChoice, boxes_for, region, role_px
from content_factory.explainer.render import script_path
from content_factory.explainer.timing import (
    DURATION_MS,
    INSPECTION_MS,
    TokenClock,
    reading_ms,
    resolve_timing,
    to_frames,
)
from content_factory.schemas.explainer import (
    Action,
    AssetRef,
    Beat,
    ChartTemplate,
    Claim,
    Cue,
    CueRelation,
    DatasetColumn,
    DatasetRow,
    DiagramEdgeSpec,
    DiagramLayout,
    DiagramNodeSpec,
    DiagramTemplate,
    DurationClass,
    Entity,
    EvidenceDataset,
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    ExplainerRenderBundle,
    FieldEncoding,
    HoldAction,
    LayoutEdge,
    LayoutNode,
    LayoutPoint,
    PixelBox,
    Quantity,
    Scene,
    ScriptPlan,
    ScriptSegment,
    SeriesBinding,
    TargetAction,
    TextItem,
    TextTemplate,
    TitlePromise,
    VisualSpec,
    tokenize,
)
from content_factory.schemas.research import EvidenceLocator

SHA = hashlib.sha256(b"fixture").hexdigest()
SEG = "seg_coldopen1"
SPOKEN = (
    "Solar capacity grew from 100 GW in 2020 to 124 GW in 2021 as panels got cheaper "
    "and grids opened up"
)
SIXTY = Path(__file__).resolve().parents[2] / "fixtures" / "explainer" / "sixty"
SURFACE = token_hex("ui.surface.0")
INK = token_hex("ui.ink.primary")
LABEL_40 = "Installed solar capacity in gigawatts GW"
LABEL_70 = "Installed solar photovoltaic generating capacity, cumulative, gigawatt"


def _claim(claim_id: str, statement: str, magnitude: float, year: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=statement,
        epistemic_class="observation",
        evidence_ids=("evi_growth01",),
        value=Quantity(magnitude=magnitude, unit="GW"),
        time_basis=year,
        rationale="stated in the source table",
        checked_at="2026-09-22",
    )


def _pack() -> EvidencePack:
    source = EvidenceSource(
        source_id="src_iea2025a",
        url="https://example.org/solar",
        canonical_url="https://example.org/solar",
        accessed_at="2026-09-22",
        content_sha256=SHA,
    )
    item = EvidenceItem(
        item_id="evi_growth01",
        source_id="src_iea2025a",
        passage="Capacity rose from 100 GW to 124 GW.",
        locator=EvidenceLocator(kind="char_range", start=0, end=36),
        quoted_at="2026-09-22",
    )
    dataset = EvidenceDataset(
        dataset_id="ds_capacity",
        columns=(
            DatasetColumn(name="year", kind="temporal"),
            DatasetColumn(name="gw", kind="quantitative", unit="GW"),
            DatasetColumn(name="tech", kind="nominal"),
        ),
        rows=(
            DatasetRow(key="2020", values=(2020, 100.0, "solar"), claim_ids=("clm_gw2020a1",)),
            DatasetRow(key="2021", values=(2021, 124.0, "solar"), claim_ids=("clm_gw2021a1",)),
        ),
    )
    pack = EvidencePack(
        pack_id="pack_solar2026",
        topic="Why solar capacity grew",
        sources=(source,),
        items=(item,),
        claims=(
            _claim("clm_gw2020a1", "Solar capacity was 100 GW in 2020", 100, "2020"),
            _claim("clm_gw2021a1", "Solar capacity was 124 GW in 2021", 124, "2021"),
        ),
        datasets=(dataset,),
    )
    return pack.frozen("2026-09-22T00:00:00Z")


def _script(pack: EvidencePack | None = None, spoken: str = SPOKEN) -> ScriptPlan:
    segment = ScriptSegment(
        segment_id=SEG,
        section="cold_open",
        spoken_text=spoken,
        tokens=tokenize(spoken),
        claim_ids=("clm_gw2020a1", "clm_gw2021a1") if pack else (),
    )
    promise = TitlePromise(
        title="Solar's quiet leap",
        thumbnail_promise="+24 GW in a year",
        claim_ids=("clm_gw2021a1",),
    )
    return ScriptPlan(
        script_id="scr_solar001",
        channel_id="ch_explain01",
        pack_id=pack.pack_id if pack else "pack_solar2026",
        pack_hash=pack.pack_hash() if pack else SHA,
        question="Why did solar capacity grow so fast?",
        contribution="A model of the growth from one year of data.",
        promises=(promise,),
        segments=(segment,),
        locked_at="2026-09-22T00:00:00Z",
    )


def _beat(
    beat_id: str,
    start: int,
    *actions: Action,
    end: int | None = None,
    relation: CueRelation = "on",
    duration: DurationClass = "short",
) -> Beat:
    cue = Cue(
        segment_id=SEG,
        token_start=start,
        token_end=start if end is None else end,
        relation=relation,
        duration_class=duration,
    )
    return Beat(beat_id=beat_id, cue=cue, actions=actions)


def _reveal(*targets: str) -> TargetAction:
    return TargetAction(action="reveal", targets=targets)


def _chart(
    scene_id: str,
    bindings: tuple[SeriesBinding, ...],
    beats: tuple[Beat, ...],
    *,
    x_title: str = "",
    y_title: str = "",
) -> Scene:
    template = ChartTemplate(
        template="chart",
        chart_kind="line",
        dataset_asset_id="ast_capacity",
        x=FieldEncoding(field="year", kind="temporal", title=x_title),
        y=FieldEncoding(field="gw", kind="quantitative", unit="GW", title=y_title),
        series_field="tech",
        series=bindings,
    )
    return Scene(
        scene_id=scene_id,
        section="cold_open",
        purpose="Show the one-year jump",
        template=template,
        beats=beats,
    )


def _text(
    scene_id: str, entity_id: str, text: str, beats: tuple[Beat, ...], split: bool = False
) -> Scene:
    template = TextTemplate(
        template="text", variant="statement", items=(TextItem(entity_id=entity_id, text=text),)
    )
    return Scene(
        scene_id=scene_id,
        section="synthesis",
        purpose="Say it",
        template=template,
        layout="split" if split else "primary",
        beats=beats,
    )


def _diagram(scene_id: str, beats: tuple[Beat, ...]) -> Scene:
    template = DiagramTemplate(
        template="diagram",
        nodes=(
            DiagramNodeSpec(entity_id="ent_panels01", label="Panels"),
            DiagramNodeSpec(entity_id="ent_grid0001", label="Grid"),
        ),
        edges=(
            DiagramEdgeSpec(
                entity_id="ent_feed0001",
                source_entity_id="ent_panels01",
                target_entity_id="ent_grid0001",
            ),
        ),
    )
    return Scene(
        scene_id=scene_id,
        section="build_model",
        purpose="Panels feed the grid",
        template=template,
        initial_visible=("ent_panels01", "ent_grid0001", "ent_feed0001"),
        beats=beats,
    )


def _spec(entities: tuple[Entity, ...], scenes: tuple[Scene, ...]) -> VisualSpec:
    return VisualSpec(
        spec_id="spec_solar001",
        script_id="scr_solar001",
        script_hash=SHA,
        pack_hash=SHA,
        design_system_version=1,
        entities=entities,
        assets=(
            AssetRef(asset_id="ast_capacity", kind="dataset", sha256=SHA, dataset_id="ds_capacity"),
        ),
        scenes=scenes,
    )


def _series(
    count: int, labels: dict[int, tuple[str, str]] | None = None
) -> tuple[tuple[Entity, ...], tuple[SeriesBinding, ...]]:
    entities: list[Entity] = []
    bindings: list[SeriesBinding] = []
    for i in range(count):
        label, short = (labels or {}).get(i, (f"Series {i}", ""))
        eid = f"ent_series0{i}"
        entities.append(Entity(entity_id=eid, label=label, short_label=short, kind="series"))
        bindings.append(SeriesBinding(value="solar", entity_id=eid))
    return tuple(entities), tuple(bindings)


def _legend_spec(label: str, short: str = "") -> tuple[VisualSpec, Scene]:
    entities, bindings = _series(1, {0: (label, short)})
    scene = _chart(
        "scn_chart001",
        bindings,
        (_beat("bt_reveal01", 0, _reveal("ent_series00")),),
        x_title="Year",
        y_title="Gigawatts",
    )
    return _spec(entities, (scene,)), scene


def _trio() -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    """Chart, diagram and statement over one 21-token segment; cues leave every floor met."""
    pack = _pack()
    script = _script(pack)
    entities = (
        Entity(entity_id="ent_solar001", label="Solar", kind="series"),
        Entity(entity_id="ent_panels01", label="Panels", kind="node"),
        Entity(entity_id="ent_grid0001", label="Grid", kind="node"),
        Entity(entity_id="ent_feed0001", label="feed", kind="edge"),
        Entity(entity_id="ent_close001", label="Closing line", kind="text"),
    )
    scenes = (
        _chart(
            "scn_chart001",
            (SeriesBinding(value="solar", entity_id="ent_solar001"),),
            (
                _beat("bt_reveal01", 0, _reveal("ent_solar001")),
                _beat("bt_hold0001", 5, HoldAction(action="hold")),
            ),
        ),
        _diagram(
            "scn_diagram1",
            (
                _beat("bt_flow0001", 8, TargetAction(action="flow", targets=("ent_feed0001",))),
                _beat(
                    "bt_hilite01", 10, TargetAction(action="highlight", targets=("ent_grid0001",))
                ),
            ),
        ),
        _text(
            "scn_close001",
            "ent_close001",
            "Cheaper panels opened the grid",
            (_beat("bt_close001", 20, _reveal("ent_close001"), HoldAction(action="hold")),),
        ),
    )
    spec = VisualSpec(
        spec_id="spec_solar001",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        design_system_version=1,
        entities=entities,
        assets=(
            AssetRef(asset_id="ast_capacity", kind="dataset", sha256=SHA, dataset_id="ds_capacity"),
        ),
        scenes=scenes,
    )
    return pack, script, spec


def _fake_layouts(spec: VisualSpec, regions: dict[str, PixelBox]) -> tuple[DiagramLayout, ...]:
    layouts: list[DiagramLayout] = []
    for scene in spec.scenes:
        template = scene.template
        if not isinstance(template, DiagramTemplate):
            continue
        plot = regions[scene.scene_id]
        step = plot.width // len(template.nodes)
        boxes = {
            n.entity_id: PixelBox(x=plot.x + i * step, y=plot.y, width=step - 24, height=64)
            for i, n in enumerate(template.nodes)
        }
        edges = tuple(
            LayoutEdge(
                entity_id=e.entity_id,
                points=(
                    LayoutPoint(x=boxes[e.source_entity_id].x, y=plot.y + 32),
                    LayoutPoint(x=boxes[e.target_entity_id].x, y=plot.y + 32),
                ),
            )
            for e in template.edges
        )
        layouts.append(
            DiagramLayout(
                scene_id=scene.scene_id,
                width=plot.width,
                height=plot.height,
                nodes=tuple(LayoutNode(entity_id=k, box=v) for k, v in boxes.items()),
                edges=edges,
            )
        )
    return tuple(layouts)


def _only_issue(error: EpisodeInvalidError) -> ContractIssue:
    assert len(error.issues) == 1, [str(i) for i in error.issues]
    return error.issues[0]


# --- fonts ---


def test_measure_text_matches_hand_computed_widths() -> None:
    # Inter 400 "Gear ratio": G746 e583 a562 r376 space281 r376 a562 t327 i242 o600 = 4655.
    assert measure_text("Gear ratio", family="Inter", weight=400, font_px=100) == pytest.approx(
        465.5
    )
    # Inter 700 "Ab": A747 b630 = 1377.
    assert measure_text("Ab", family="Inter", weight=700, font_px=28) == pytest.approx(1377 * 0.028)
    # Sora 800, "4" then the multiplication sign: 682 + 598 = 1280.
    assert measure_text("4×", family="Sora", weight=800, font_px=72) == pytest.approx(1280 * 0.072)  # noqa: RUF001  the sign is what the viewer reads


def test_unknown_weight_uses_the_nearest_table() -> None:
    assert measure_text("n", family="Inter", weight=720, font_px=1000) == 623.0
    assert measure_text("n", family="Sora", weight=500, font_px=1000) == 648.0


def test_fallback_advances_match_fit_ts() -> None:
    n_width = measure_text("n", family="Inter", weight=400, font_px=1000)
    assert measure_text("ж", family="Inter", weight=400, font_px=1000) == n_width
    assert measure_text("́", family="Inter", weight=400, font_px=1000) == 0
    assert measure_text("​", family="Inter", weight=400, font_px=1000) == 0
    assert measure_text("🙂", family="Inter", weight=400, font_px=1000) == 1000


def test_fits_applies_the_safety_factor() -> None:
    width = measure_text("Solar", family="Inter", weight=400, font_px=34)
    assert fits("Solar", width * FIT_SAFETY, family="Inter", weight=400, font_px=34)
    assert not fits("Solar", width, family="Inter", weight=400, font_px=34)


def test_wrap_breaks_a_long_word_by_character() -> None:
    lines = wrap_text("a supercalifragilistic word", 120, family="Inter", weight=400, font_px=34)
    assert lines[0] == "a"
    assert "".join(lines[1:-1]).startswith("supercalifra")
    assert lines[-1] == "word"
    for line in lines:
        assert measure_text(line, family="Inter", weight=400, font_px=34) * FIT_SAFETY <= 120


def test_fonts_version_comes_from_the_tables() -> None:
    assert FONTS_VERSION == "5.3.0"


# --- layout ---


def test_forty_character_label_fits_the_legend_at_label_size() -> None:
    assert len(LABEL_40) == 40
    spec, scene = _legend_spec(LABEL_40)
    placed = boxes_for(scene, spec)
    chip = next(b for b in placed.boxes if b.entity_id == "ent_series00")
    legend = region(scene, "legend")
    assert chip.font_px == role_px("label") and chip.lines == 1
    assert legend.y <= chip.box.y and chip.box.y + chip.box.height <= legend.y + legend.height
    assert placed.choices == ()
    assert {b.entity_id for b in placed.boxes} == {
        "ent_series00", "axisx_chart001", "axisy_chart001"
    }  # fmt: skip


def test_seventy_character_label_without_short_label_raises_a_layout_issue() -> None:
    assert len(LABEL_70) == 70
    spec, scene = _legend_spec(LABEL_70)
    with pytest.raises(EpisodeInvalidError) as caught:
        boxes_for(scene, spec)
    issue = _only_issue(caught.value)
    assert issue.kind == "layout"
    assert "ent_series00" in issue.message and LABEL_70 in issue.message
    assert "measures 942 px" in issue.message and "816 px are available" in issue.message
    assert "2 lines needed, 1 allowed" in issue.message
    assert "short_label of at most" in issue.fix
    assert "split scene scn_chart001 after beat bt_reveal01" in issue.fix
    assert "layout 'split'" in issue.fix


def test_seventy_character_label_with_a_fitting_short_label_reports_the_choice() -> None:
    spec, scene = _legend_spec(LABEL_70, "Solar capacity GW")
    placed = boxes_for(scene, spec)
    chip = next(b for b in placed.boxes if b.entity_id == "ent_series00")
    assert chip.font_px == role_px("label")
    assert placed.choices == (LayoutChoice("ent_series00", "Solar capacity GW"),)
    assert placed.texts["ent_series00"] == "Solar capacity GW"


def test_text_is_never_shrunk_below_its_role_size() -> None:
    long = " ".join(["photovoltaic"] * 12)
    beats = (_beat("bt_rev00001", 0, _reveal("ent_text0001")),)
    scene = _text("scn_text0001", "ent_text0001", long, beats, split=True)
    spec = _spec((Entity(entity_id="ent_text0001", label="Wall of text", kind="text"),), (scene,))
    with pytest.raises(EpisodeInvalidError) as caught:
        boxes_for(scene, spec)
    issue = _only_issue(caught.value)
    assert issue.kind == "layout" and "lines needed, 2 allowed" in issue.message


# --- colours ---


def test_allocator_binds_the_same_entity_to_the_same_token_in_two_scenes() -> None:
    entities, bindings = _series(2)
    first = _chart("scn_chart001", bindings, (_beat("bt_a00000001", 0, _reveal("ent_series00")),))
    second = _chart(
        "scn_chart002", bindings[::-1], (_beat("bt_b00000001", 1, _reveal("ent_series01")),)
    )
    colors = allocate_colors(_spec(entities, (first, second)))
    by_scene = {sid: {c.entity_id: c.token_id for c in cs} for sid, cs in colors.items()}
    assert by_scene["scn_chart001"]["ent_series00"] == "data.cat.blue"
    assert by_scene["scn_chart002"]["ent_series00"] == "data.cat.blue"
    assert by_scene["scn_chart001"]["ent_series01"] == "data.cat.orange"
    assert by_scene["scn_chart002"]["ent_series01"] == "data.cat.orange"
    hexes = {c.srgb_hex for cs in colors.values() for c in cs}
    assert hexes == {token_hex("data.cat.blue"), token_hex("data.cat.orange")}


def test_seven_co_visible_series_raise_a_color_issue() -> None:
    entities, bindings = _series(7)
    beats = tuple(_beat(f"bt_reveal0{i}", i, _reveal(b.entity_id)) for i, b in enumerate(bindings))
    with pytest.raises(EpisodeInvalidError) as caught:
        allocate_colors(_spec(entities, (_chart("scn_chart001", bindings, beats),)))
    issue = _only_issue(caught.value)
    assert issue.kind == "color" and "7 categorical" in issue.message
    assert "hide one before revealing the other" in issue.fix


def test_hiding_before_revealing_keeps_seven_series_within_the_palette() -> None:
    entities, bindings = _series(7)
    beats = [_beat(f"bt_reveal0{i}", i, _reveal(b.entity_id)) for i, b in enumerate(bindings[:6])]
    beats.append(_beat("bt_hide00001", 6, TargetAction(action="hide", targets=("ent_series00",))))
    beats.append(_beat("bt_reveal06", 7, _reveal("ent_series06")))
    colors = allocate_colors(_spec(entities, (_chart("scn_chart001", bindings, tuple(beats)),)))
    assert len(colors["scn_chart001"]) == 7


# --- timing ---


def test_anchor_before_on_after() -> None:
    clock = TokenClock(_script())
    assert clock.timing_source == "estimated"
    assert clock.anchor_ms(Cue(segment_id=SEG, token_start=2, token_end=3, relation="on")) == 770
    assert (
        clock.anchor_ms(Cue(segment_id=SEG, token_start=2, token_end=3, relation="before")) == 470
    )
    assert (
        clock.anchor_ms(Cue(segment_id=SEG, token_start=2, token_end=3, relation="after")) == 1690
    )
    assert clock.anchor_ms(Cue(segment_id=SEG, token_start=0, token_end=0, relation="before")) == 0
    assert clock.narration_end_ms == 21 * 385


def test_hold_duration_includes_reading_time() -> None:
    text = "Solar capacity grew fast"
    scene = _text(
        "scn_text0001",
        "ent_text0001",
        text,
        (
            _beat("bt_rev00001", 0, _reveal("ent_text0001")),
            _beat("bt_hold00001", 2, HoldAction(action="hold")),
        ),
    )
    spec = _spec((Entity(entity_id="ent_text0001", label="Line", kind="text"),), (scene,))
    timed = resolve_timing(spec, TokenClock(_script()), {"scn_text0001": {"ent_text0001": text}})
    hold = next(a for a in timed[0].actions if a.action == "hold")
    assert reading_ms(text) == 1213
    assert hold.end_ms - hold.start_ms == DURATION_MS["short"] + 1213 + INSPECTION_MS


def test_reading_floor_raises_timing_with_the_split_fix() -> None:
    text = "Nine words of caption that need their reading time"
    first = _text(
        "scn_text0001", "ent_text0001", text, (_beat("bt_rev00001", 7, _reveal("ent_text0001")),)
    )
    second = _text(
        "scn_text0002", "ent_text0002", "Next", (_beat("bt_rev00002", 8, _reveal("ent_text0002")),)
    )
    spec = _spec(
        (
            Entity(entity_id="ent_text0001", label="Caption", kind="text"),
            Entity(entity_id="ent_text0002", label="Next", kind="text"),
        ),
        (first, second),
    )
    texts = {"scn_text0001": {"ent_text0001": text}, "scn_text0002": {"ent_text0002": "Next"}}
    with pytest.raises(EpisodeInvalidError) as caught:
        resolve_timing(spec, TokenClock(_script()), texts)
    issue = _only_issue(caught.value)
    assert issue.kind == "timing" and "ent_text0001" in issue.message
    assert "needs 3728 ms" in issue.message and "has 600 ms" in issue.message
    assert "move beat bt_rev00002 later" in issue.fix and "split scene scn_text0001" in issue.fix


def test_to_frames_rounds_half_up() -> None:
    assert to_frames(50, 30) == 2
    assert to_frames(49, 30) == 1
    assert to_frames(1000, 30) == 30


# --- compile ---


def test_compile_episode_returns_a_bundle_that_validates() -> None:
    pack, script, spec = _trio()
    bundle = compile_episode(pack, script, spec, layout_diagrams=_fake_layouts)
    again = ExplainerRenderBundle.model_validate(bundle.model_dump(mode="json"))
    assert again == bundle
    assert bundle.timeline.timeline_id == f"tl_{spec.spec_hash()[:12]}"
    assert [d.dataset_id for d in bundle.datasets] == ["ds_capacity"]
    assert [layout.scene_id for layout in bundle.layouts] == ["scn_diagram1"]
    names = {i.name: i.sha256 for i in bundle.timeline.inputs}
    assert names["timing_source"] == hashlib.sha256(b"estimated").hexdigest()
    assert names["spec"] == spec.spec_hash()
    assert bundle.fonts_version == FONTS_VERSION


def test_scenes_are_contiguous_and_total_frames_is_the_sum() -> None:
    pack, script, spec = _trio()
    timeline = compile_episode(pack, script, spec, layout_diagrams=_fake_layouts).timeline
    cursor = 0
    for scene in timeline.scenes:
        assert scene.start_frame == cursor
        cursor += scene.duration_frames
        for action in scene.actions:
            assert scene.start_frame <= action.start_frame <= action.end_frame
    assert cursor == timeline.total_frames
    assert timeline.total_frames == to_frames(11_815, 30)


def test_compile_is_deterministic() -> None:
    pack, script, spec = _trio()
    first = compile_episode(pack, script, spec, layout_diagrams=_fake_layouts)
    second = compile_episode(pack, script, spec, layout_diagrams=_fake_layouts)
    assert first.canonical_json() == second.canonical_json()


def test_write_bundle_writes_canonical_json(tmp_path: Path) -> None:
    pack, script, spec = _trio()
    bundle = compile_episode(pack, script, spec, layout_diagrams=_fake_layouts)
    path = write_bundle(bundle, tmp_path / "out" / "bundle.json")
    assert path.read_text(encoding="utf-8") == bundle.canonical_json() + "\n"


def test_sixty_fixture_compiles_to_sixty_seconds() -> None:
    pack = EvidencePack.model_validate(json.loads((SIXTY / "pack.json").read_text()))
    script = ScriptPlan.model_validate(json.loads((SIXTY / "script.json").read_text()))
    spec = VisualSpec.model_validate(json.loads((SIXTY / "spec.json").read_text()))
    bundle = compile_episode(pack, script, spec, layout_diagrams=_fake_layouts)
    seconds = bundle.timeline.total_frames / bundle.timeline.fps
    assert 59.0 <= seconds <= 61.0
    assert len(bundle.timeline.scenes) == 8
    actions = {a.action for s in bundle.timeline.scenes for a in s.actions}
    assert {
        "reveal",
        "highlight",
        "compare",
        "annotate",
        "isolate",
        "hold",
        "flow",
        "trace",
        "zoom_to",
    } <= actions


def test_missing_renderer_script_is_named() -> None:
    with pytest.raises(FileNotFoundError, match=r"does-not-exist\.mjs"):
        script_path("does-not-exist.mjs")


# --- pixel QC ---


def _frame(size: tuple[int, int], surface: str, ink: str | None, box: PixelBox) -> Image.Image:
    frame = Image.new("RGB", size, surface)
    if ink is not None:
        draw = ImageDraw.Draw(frame)
        inset = (box.x + 10, box.y + 10, box.x + box.width - 10, box.y + box.height - 10)
        draw.rectangle(inset, fill=ink)
    return frame


def test_ink_on_surface_reaches_the_apca_floor() -> None:
    box = PixelBox(x=50, y=50, width=200, height=60)
    lc = qc.text_contrast_lc(_frame((400, 200), SURFACE, INK, box), box, INK)
    assert lc < 0 and abs(lc) >= 75


def test_grey_on_grey_fails_the_apca_floor() -> None:
    box = PixelBox(x=50, y=50, width=200, height=60)
    lc = qc.text_contrast_lc(_frame((400, 200), "#505050", "#909090", box), box, "#909090")
    assert abs(lc) < 75


def test_a_box_without_ink_is_unmeasurable() -> None:
    box = PixelBox(x=50, y=50, width=200, height=60)
    with pytest.raises(qc.QcUnmeasurableError, match="no pixels within"):
        qc.text_contrast_lc(_frame((400, 200), SURFACE, None, box), box, INK)


def test_line_edge_contrast_sees_a_line_and_not_its_absence() -> None:
    frame = Image.new("RGB", (360, 200), SURFACE)
    ImageDraw.Draw(frame).line([(20, 100), (340, 100)], fill=token_hex("data.cat.orange"), width=3)
    assert qc.line_edge_contrast(frame, [(20, 100), (340, 100)], 3) >= 40
    assert qc.line_edge_contrast(frame, [(20, 40), (340, 40)], 3) == 0


def test_series_points_follow_d3_linear_scales() -> None:
    pack, _, spec = _trio()
    chart = spec.scenes[0].template
    assert isinstance(chart, ChartTemplate)
    plot = PixelBox(x=0, y=0, width=100, height=100)
    points = qc.series_points(chart, pack.datasets[0], chart.series[0], plot)
    # Line charts use scalePoint padding 0.5 on x; d3 nice() extends [0, 124] to [0, 130] on y.
    assert points[0] == pytest.approx((25.0, 100 - 100 / 130 * 100))
    assert points[1] == pytest.approx((75.0, 100 - 124 / 130 * 100))


def test_check_rendered_reports_shapes_from_synthetic_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack, script, spec = _trio()
    bundle = compile_episode(pack, script, spec, layout_diagrams=_fake_layouts)
    boxes = [b for s in bundle.timeline.scenes for b in s.boxes if b.font_px is not None]

    def painted(mode: str, width: int | None) -> Image.Image:
        # Ink on the canvas passes, the same ink on mid-grey fails, an empty canvas is unmeasurable.
        frame = Image.new("RGB", (1920, 1080), "#909090" if mode == "grey" else SURFACE)
        draw = ImageDraw.Draw(frame)
        for placed in boxes:
            if mode == "none":
                continue
            box = placed.box
            inset = (box.x + 4, box.y + 4, box.x + box.width - 4, box.y + box.height - 4)
            draw.rectangle(inset, fill=qc.ink_hex_for(placed.font_px or 0))
        return frame if width is None else frame.resize((width, 1080 * width // 1920))

    for mode, expected in (("ink", True), ("grey", False), ("none", None)):
        monkeypatch.setattr(
            qc, "_decode_or_none", lambda mp4, at_ms, width=None, m=mode: painted(m, width)
        )
        findings = qc.check_rendered(bundle, Path("unused.mp4"))
        text = [f for f in findings if f.check == "text_apca"]
        assert text, findings
        assert all(f.threshold == 75.0 and f.scene_id and f.entity_id for f in text)
        if expected is None:
            assert {f.passed for f in text} == {None}
            assert all("no pixels within" in f.evidence and f.measured is None for f in text)
            continue
        # ui.ink.secondary on the canvas measures Lc 70, so at the 75 floor only body text passes.
        body = [f for f in text if f.entity_id == "ent_close001"]
        assert body and all(f.passed is expected for f in body)
        assert all(f.passed is not None and f.measured is not None for f in text)
        if expected is False:
            assert {f.passed for f in text} == {False}
    edges = [f for f in findings if f.check == "line_edge"]
    assert edges and all(f.entity_id == "ent_solar001" and f.passed is False for f in edges)
