"""Write fixtures/explainer/review_set: seven accepted/rejected bundle pairs for the QC."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import BaseModel

from content_factory.explainer.compile import compile_episode
from content_factory.explainer.passages import Tile
from content_factory.explainer.qc import QcUnmeasurableError
from content_factory.explainer.qc_checks import run_deterministic_qc
from content_factory.explainer.sources import capture_asset
from content_factory.schemas.explainer import (
    Action,
    AssetRef,
    Beat,
    CaptureAsset,
    CaptureQuote,
    CaptureSection,
    ChartTemplate,
    Claim,
    CompiledExplainerScene,
    Cue,
    DatasetColumn,
    DatasetRow,
    DomRangeLocator,
    DurationClass,
    Entity,
    EvidenceDataset,
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    ExplainerRenderBundle,
    FieldEncoding,
    HoldAction,
    PageRect,
    Quantity,
    QuoteAction,
    Scene,
    ScriptPlan,
    ScriptSegment,
    ScrollToAction,
    Section,
    SeriesBinding,
    ShowSourceAction,
    SourceCaptureManifest,
    SourceDocumentTemplate,
    TargetAction,
    TextItem,
    TextTemplate,
    TitlePromise,
    Viewport,
    VisualSpec,
    tokenize,
)
from content_factory.schemas.research import EvidenceLocator

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "fixtures" / "explainer" / "review_set"
DATE = "2026-09-23"
FROZEN_AT = f"{DATE}T00:00:00Z"
QUOTE = "A faster tram does not shorten the trip"
PASSAGE = (
    "a faster tram does not shorten the trip, because every second gained is spent standing "
    "at the busiest stops"
)
SHA = hashlib.sha256(b"review_set synthetic capture").hexdigest()
INK = (17, 17, 17)
WIDE = (
    PageRect(x=200, y=1000, width=760, height=22),
    PageRect(x=200, y=1032, width=700, height=22),
    PageRect(x=200, y=1064, width=520, height=22),
)
DWELL_S = (10.0, 20.0, 30.0, 40.0)
STOPS = ("A", "B", "C", "D")
SEGMENTS: dict[str, tuple[Section, str, tuple[str, ...]]] = {
    "seg_open00001": ("cold_open", QUOTE, ("clm_stops_gain",)),
    "seg_stakes0001": ("question_stakes", "Where do the saved seconds go on a busy line", ()),
    "seg_source0001": (
        "build_model",
        "The measurements say it plainly: the seconds are spent standing at the busiest stops",
        ("clm_stops_gain",),
    ),
    "seg_run0000001": (
        "run_system",
        "Dwell time grows stop by stop along the line",
        ("clm_dwell_a4",),
    ),
    "seg_change0001": (
        "change_variable",
        "On a busier day the same stops hold the tram far longer",
        ("clm_dwell_b4",),
    ),
    "seg_synth00001": ("synthesis", "The doors decide the timetable", ("clm_stops_gain",)),
}
Mutation = Callable[[ExplainerRenderBundle], ExplainerRenderBundle]


def make_pack() -> EvidencePack:
    source = EvidenceSource(
        source_id="src_transit0001",
        url="http://127.0.0.1/synthetic.html",
        canonical_url="http://127.0.0.1/synthetic.html",
        publisher="Transit Notes",
        title="Why the tram is late",
        accessed_at=DATE,
        content_sha256=SHA,
        capture_id="cap_synthetic01",
    )
    item = EvidenceItem(
        item_id="evi_stops000001",
        source_id=source.source_id,
        passage=PASSAGE,
        locator=EvidenceLocator(kind="char_range", start=0, end=len(PASSAGE)),
        quoted_at=DATE,
    )
    claims = [
        Claim(
            claim_id="clm_stops_gain",
            statement="The gain from a faster tram is spent at the stops",
            epistemic_class="observation",
            evidence_ids=(item.item_id,),
            rationale="stated in the source",
            checked_at=DATE,
        )
    ]
    quiet: list[DatasetRow] = []
    busy: list[DatasetRow] = []
    for k, (stop, seconds) in enumerate(zip(STOPS, DWELL_S, strict=True), start=1):
        a_id, b_id = f"clm_dwell_a{k}", f"clm_dwell_b{k}"
        claims += [_dwell_claim(a_id, stop, seconds), _dwell_claim(b_id, stop, seconds * 10)]
        quiet.append(DatasetRow(key=stop, values=(stop, seconds), claim_ids=(a_id,)))
        busy.append(DatasetRow(key=stop, values=(stop, seconds * 10), claim_ids=(b_id,)))
    columns = (
        DatasetColumn(name="stop", kind="nominal"),
        DatasetColumn(name="dwell", kind="quantitative", unit="s"),
    )
    pack = EvidencePack(
        pack_id="pack_reviewset1",
        topic="Why a faster tram does not shorten the trip",
        sources=(source,),
        items=(item,),
        claims=tuple(claims),
        datasets=(
            EvidenceDataset(dataset_id="ds_dwell_quiet", columns=columns, rows=tuple(quiet)),
            EvidenceDataset(dataset_id="ds_dwell_busy", columns=columns, rows=tuple(busy)),
        ),
    )
    return pack.frozen(FROZEN_AT)


def _dwell_claim(claim_id: str, stop: str, seconds: float) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=f"The tram dwells {seconds:g} s at stop {stop}",
        epistemic_class="illustrative_assumption",
        value=Quantity(magnitude=seconds, unit="s"),
        rationale="illustrative dwell times chosen for round numbers",
        checked_at=DATE,
    )


def make_script(pack: EvidencePack, segment_ids: Sequence[str]) -> ScriptPlan:
    segments = tuple(
        ScriptSegment(
            segment_id=sid,
            section=SEGMENTS[sid][0],
            spoken_text=SEGMENTS[sid][1],
            tokens=tokenize(SEGMENTS[sid][1]),
            claim_ids=SEGMENTS[sid][2],
        )
        for sid in segment_ids
    )
    return ScriptPlan(
        script_id="scr_" + hashlib.sha256("/".join(segment_ids).encode()).hexdigest()[:12],
        channel_id="ch_explain01",
        pack_id=pack.pack_id,
        pack_hash=pack.pack_hash(),
        question="Where do the tram's minutes go?",
        contribution="The stops, not the motor, set the trip time.",
        promises=(
            TitlePromise(
                title="Doors, not motors",
                thumbnail_promise="The seconds go to the stops",
                claim_ids=("clm_stops_gain",),
            ),
        ),
        segments=segments,
        locked_at=FROZEN_AT,
    )


def make_spec(
    pack: EvidencePack,
    script: ScriptPlan,
    scenes: tuple[Scene, ...],
    entities: tuple[Entity, ...],
    assets: tuple[AssetRef, ...] = (),
) -> VisualSpec:
    return VisualSpec(
        spec_id="spec_"
        + hashlib.sha256("/".join(s.scene_id for s in scenes).encode()).hexdigest()[:12],
        script_id=script.script_id,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        design_system_version=1,
        entities=entities,
        assets=assets,
        scenes=scenes,
    )


def beat(
    beat_id: str,
    segment_id: str,
    token: int,
    *actions: Action,
    duration: DurationClass = "short",
) -> Beat:
    cue = Cue(segment_id=segment_id, token_start=token, token_end=token, duration_class=duration)
    return Beat(beat_id=beat_id, cue=cue, actions=actions)


def reveal(*targets: str) -> TargetAction:
    return TargetAction(action="reveal", targets=targets)


def statement(
    scene_id: str, section: Section, entity_id: str, text: str, beats: tuple[Beat, ...]
) -> Scene:
    template = TextTemplate(
        template="text",
        variant="statement",
        items=(TextItem(entity_id=entity_id, text=text, claim_id="clm_stops_gain"),),
    )
    return Scene(
        scene_id=scene_id,
        section=section,
        purpose="Say the one sentence the episode rests on",
        template=template,
        beats=beats,
        claim_ids=("clm_stops_gain",),
    )


def line_chart(
    scene_id: str, section: Section, asset_id: str, title: str, seg: str, t0: int
) -> Scene:
    template = ChartTemplate(
        template="chart",
        chart_kind="line",
        title=title,
        dataset_asset_id=asset_id,
        x=FieldEncoding(field="stop", kind="nominal", title="Stop"),
        y=FieldEncoding(field="dwell", kind="quantitative", unit="s", title="Dwell"),
        series=(SeriesBinding(entity_id="ent_dwell_ser"),),
    )
    suffix = "a" if asset_id.endswith("quiet") else "b"
    return Scene(
        scene_id=scene_id,
        section=section,
        purpose="Show dwell time stop by stop",
        template=template,
        beats=(
            beat(f"bt_dwell_{suffix}_reveal", seg, t0, reveal("ent_dwell_ser")),
            beat(f"bt_dwell_{suffix}_hold00", seg, t0 + 3, HoldAction(action="hold")),
        ),
        claim_ids=tuple(f"clm_dwell_{suffix}{k}" for k in range(1, 5)),
    )


def synthetic_capture(into: Path) -> CaptureAsset:
    """Two white tiles with ink blocks where the quote's line rects say the words are."""
    width, height = 1280, 800
    tiles: list[Tile] = []
    for i, top in enumerate((0, 800)):
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        for rect in WIDE:
            y0 = rect.y - top
            if not -rect.height < y0 < height:
                continue
            x = rect.x
            while x + 30 <= rect.x + rect.width:
                draw.rectangle((x, y0 + 4, x + 30, y0 + rect.height - 4), fill=INK)
                x += 46
        path = into / f"tile-{i:04d}.png"
        image.save(path)
        tiles.append(Tile(path, top))
    manifest = SourceCaptureManifest(
        capture_id="cap_synthetic01",
        source_id="src_transit0001",
        url="http://127.0.0.1/synthetic.html",
        publisher="Transit Notes",
        title="Why the tram is late",
        captured_at=FROZEN_AT,
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
                quote_id="qt_passage00001",
                section_id="sec_deep0000001",
                text=PASSAGE,
                locator=DomRangeLocator(
                    kind="dom_range", start_path="/p", start_offset=0, end_path="/p", end_offset=8
                ),
                occurrence_index=0,
                line_rects=WIDE,
                claim_ids=("clm_stops_gain",),
            ),
        ),
        claim_ids=("clm_stops_gain",),
    )
    return capture_asset(manifest, tiles)


# --- the seven pairs ---


def pair_tiny_text(pack: EvidencePack) -> tuple[ScriptPlan, ExplainerRenderBundle, Mutation]:
    script = make_script(pack, ("seg_open00001",))
    scene = statement(
        "scn_open0000001",
        "cold_open",
        "ent_open_stmt",
        QUOTE,
        (
            beat("bt_open_reveal0", "seg_open00001", 0, reveal("ent_open_stmt")),
            beat("bt_open_hold000", "seg_open00001", 2, HoldAction(action="hold"), duration="long"),
        ),
    )
    spec = make_spec(pack, script, (scene,), (_text_entity("ent_open_stmt"),))
    bundle = compile_episode(pack, script, spec)
    return script, bundle, lambda b: with_box(b, scene.scene_id, "ent_open_stmt", font_px=18)


def pair_clipping(pack: EvidencePack) -> tuple[ScriptPlan, ExplainerRenderBundle, Mutation]:
    script, bundle, _ = pair_tiny_text(pack)
    scene_id = bundle.timeline.scenes[0].scene_id

    def mutate(b: ExplainerRenderBundle) -> ExplainerRenderBundle:
        box = next(x for x in b.timeline.scenes[0].boxes if x.entity_id == "ent_open_stmt").box
        moved = box.model_copy(update={"x": b.timeline.width - box.width + 120})
        return with_box(b, scene_id, "ent_open_stmt", box=moved)

    return script, bundle, mutate


def pair_wrong_highlight(
    pack: EvidencePack, folder: Path
) -> tuple[ScriptPlan, ExplainerRenderBundle, Mutation]:
    capture = synthetic_capture(folder)
    script = make_script(pack, ("seg_source0001",))
    seg = "seg_source0001"
    quote_id = capture.manifest.quotes[0].quote_id
    scene = Scene(
        scene_id="scn_source00001",
        section="build_model",
        purpose="Read the passage in its source",
        template=SourceDocumentTemplate(
            template="source_document",
            capture_asset_id="ast_capture0001",
            initial_section_id="sec_top00000001",
        ),
        beats=(
            beat("bt_src_show0001", seg, 0, ShowSourceAction(action="show_source")),
            beat(
                "bt_src_scroll01",
                seg,
                2,
                ScrollToAction(action="scroll_to", section_id="sec_deep0000001"),
            ),
            beat("bt_src_focus001", seg, 4, QuoteAction(action="focus_passage", quote_id=quote_id)),
            beat(
                "bt_src_hilite01", seg, 6, QuoteAction(action="highlight_quote", quote_id=quote_id)
            ),
            beat("bt_src_hold0001", seg, 8, HoldAction(action="hold"), duration="long"),
        ),
        claim_ids=("clm_stops_gain",),
        source_ids=("src_transit0001",),
    )
    spec = make_spec(
        pack,
        script,
        (scene,),
        (Entity(entity_id=quote_id, label="the measurements passage", kind="quote"),),
        (
            AssetRef(
                asset_id="ast_capture0001",
                kind="capture",
                sha256=capture.manifest.artifact_sha256,
                capture_id=capture.capture_id,
            ),
        ),
    )
    bundle = relative_tiles(compile_episode(pack, script, spec, captures=(capture,)))

    def mutate(b: ExplainerRenderBundle) -> ExplainerRenderBundle:
        def shift(compiled: CompiledExplainerScene) -> CompiledExplainerScene:
            highlights = tuple(
                h.model_copy(
                    update={"rects": tuple(r.model_copy(update={"x": r.x + 200}) for r in h.rects)}
                )
                for h in compiled.highlights
            )
            return compiled.model_copy(update={"highlights": highlights})

        return with_scene(b, scene.scene_id, shift)

    return script, bundle, mutate


def pair_negation_omitted(pack: EvidencePack) -> tuple[ScriptPlan, ExplainerRenderBundle, Mutation]:
    script = make_script(pack, ("seg_open00001",))
    template = TextTemplate(
        template="text",
        variant="quotation_card",
        items=(
            TextItem(entity_id="ent_quote_txt", text=QUOTE, claim_id="clm_stops_gain"),
            TextItem(entity_id="ent_quote_att", text="Transit Notes"),
        ),
    )
    scene = Scene(
        scene_id="scn_quote000001",
        section="cold_open",
        purpose="Quote the source's own words",
        template=template,
        beats=(
            beat("bt_quote_reveal", "seg_open00001", 0, reveal("ent_quote_txt", "ent_quote_att")),
            beat("bt_quote_hold00", "seg_open00001", 2, HoldAction(action="hold"), duration="long"),
        ),
        claim_ids=("clm_stops_gain",),
        source_ids=("src_transit0001",),
    )
    entities = (
        Entity(
            entity_id="ent_quote_txt",
            label="the quote",
            kind="quote",
            claim_ids=("clm_stops_gain",),
        ),
        Entity(entity_id="ent_quote_att", label="attribution", kind="text"),
    )
    spec = make_spec(pack, script, (scene,), entities)
    bundle = compile_episode(pack, script, spec)
    dropped = QUOTE.replace(" not", "")
    return script, bundle, lambda b: with_box(b, scene.scene_id, "ent_quote_txt", text=dropped)


def pair_axis_change(pack: EvidencePack) -> tuple[ScriptPlan, ExplainerRenderBundle, Mutation]:
    script = make_script(pack, ("seg_run0000001", "seg_change0001"))
    datasets = {d.dataset_id: d for d in pack.datasets}
    assets = tuple(
        AssetRef(
            asset_id=f"ast_dwell_{name}",
            kind="dataset",
            sha256=datasets[f"ds_dwell_{name}"].content_hash(),
            dataset_id=f"ds_dwell_{name}",
        )
        for name in ("quiet", "busy")
    )
    entity = Entity(
        entity_id="ent_dwell_ser",
        label="Dwell time",
        kind="series",
        claim_ids=("clm_dwell_a4", "clm_dwell_b4"),
    )

    def spec_with(title: str) -> VisualSpec:
        scenes = (
            line_chart(
                "scn_dwell_quiet", "run_system", "ast_dwell_quiet", "Quiet day", "seg_run0000001", 0
            ),
            line_chart(
                "scn_dwell_busy0", "change_variable", "ast_dwell_busy", title, "seg_change0001", 2
            ),
        )
        return make_spec(pack, script, scenes, (entity,), assets)

    accepted = compile_episode(pack, script, spec_with("Same stops, new axis"))
    rejected = compile_episode(pack, script, spec_with("Same stops, busier day"))
    return script, accepted, lambda _b: rejected


def pair_narration_visual_mismatch(
    pack: EvidencePack,
) -> tuple[ScriptPlan, ExplainerRenderBundle, Mutation]:
    script = make_script(pack, ("seg_open00001", "seg_run0000001"))

    def spec_with(seg: str) -> VisualSpec:
        scene = statement(
            "scn_open0000001",
            "cold_open",
            "ent_open_stmt",
            QUOTE,
            (
                beat("bt_open_reveal0", seg, 0, reveal("ent_open_stmt")),
                beat("bt_open_hold000", seg, 2, HoldAction(action="hold"), duration="long"),
            ),
        )
        return make_spec(pack, script, (scene,), (_text_entity("ent_open_stmt"),))

    accepted = compile_episode(pack, script, spec_with("seg_open00001"))
    rejected = compile_episode(pack, script, spec_with("seg_run0000001"))
    return script, accepted, lambda _b: rejected


def pair_broken_transition(
    pack: EvidencePack,
) -> tuple[ScriptPlan, ExplainerRenderBundle, Mutation]:
    script = make_script(pack, ("seg_open00001", "seg_stakes0001", "seg_synth00001"))
    scenes = (
        statement(
            "scn_open0000001",
            "cold_open",
            "ent_open_stmt",
            QUOTE,
            (
                beat("bt_open_reveal0", "seg_open00001", 0, reveal("ent_open_stmt")),
                beat(
                    "bt_open_hold000",
                    "seg_open00001",
                    2,
                    HoldAction(action="hold"),
                    duration="beat",
                ),
            ),
        ),
        statement(
            "scn_synth000001",
            "synthesis",
            "ent_synth_txt",
            "The doors decide the timetable",
            (
                beat("bt_synth_reveal", "seg_synth00001", 0, reveal("ent_synth_txt")),
                beat(
                    "bt_synth_hold00",
                    "seg_synth00001",
                    2,
                    HoldAction(action="hold"),
                    duration="long",
                ),
            ),
        ),
    )
    entities = (_text_entity("ent_open_stmt"), _text_entity("ent_synth_txt"))
    spec = make_spec(pack, script, scenes, entities)
    bundle = compile_episode(pack, script, spec)

    def mutate(b: ExplainerRenderBundle) -> ExplainerRenderBundle:
        first, second = b.timeline.scenes[0], b.timeline.scenes[1]

        def overrun(compiled: CompiledExplainerScene) -> CompiledExplainerScene:
            actions = tuple(
                a.model_copy(update={"end_frame": second.start_frame + 30})
                if a.action == "hold"
                else a
                for a in compiled.actions
            )
            return compiled.model_copy(update={"actions": actions})

        return with_scene(b, first.scene_id, overrun)

    return script, bundle, mutate


# --- helpers ---


def _text_entity(entity_id: str) -> Entity:
    return Entity(
        entity_id=entity_id, label="statement", kind="text", claim_ids=("clm_stops_gain",)
    )


def with_scene(
    bundle: ExplainerRenderBundle,
    scene_id: str,
    change: Callable[[CompiledExplainerScene], CompiledExplainerScene],
) -> ExplainerRenderBundle:
    """The bundle with one compiled scene changed, re-validated; the spec hash is untouched."""
    scenes = tuple(change(s) if s.scene_id == scene_id else s for s in bundle.timeline.scenes)
    timeline = bundle.timeline.model_copy(update={"scenes": scenes})
    changed = bundle.model_copy(update={"timeline": timeline})
    return ExplainerRenderBundle.model_validate(changed.model_dump())


def with_box(
    bundle: ExplainerRenderBundle, scene_id: str, entity_id: str, **fields: object
) -> ExplainerRenderBundle:
    def change(compiled: CompiledExplainerScene) -> CompiledExplainerScene:
        boxes = tuple(
            b.model_copy(update=fields) if b.entity_id == entity_id else b for b in compiled.boxes
        )
        return compiled.model_copy(update={"boxes": boxes})

    return with_scene(bundle, scene_id, change)


def relative_tiles(bundle: ExplainerRenderBundle) -> ExplainerRenderBundle:
    """Tile paths relative to the repo root, so the fixture is byte-stable across checkouts."""
    captures = tuple(
        c.model_copy(
            update={
                "tiles": tuple(
                    t.model_copy(update={"path": Path(t.path).relative_to(REPO).as_posix()})
                    for t in c.tiles
                )
            }
        )
        for c in bundle.captures
    )
    changed = bundle.model_copy(update={"captures": captures})
    return ExplainerRenderBundle.model_validate(changed.model_dump())


def _unmeasurable(
    mp4: Path, timestamps_ms: Sequence[int], *, width: int | None = None
) -> list[Image.Image]:
    msg = "the fixture builder decodes no frames"
    raise QcUnmeasurableError(msg)


def _no_ocr(crops: Sequence[Image.Image]) -> list[str]:
    msg = "the fixture builder runs no OCR"
    raise RuntimeError(msg)


def failed_checks(
    pack: EvidencePack, script: ScriptPlan, bundle: ExplainerRenderBundle
) -> set[str]:
    """Checks that fail without any pixels: the structural half of the verdict."""
    findings = run_deterministic_qc(
        bundle,
        OUT_DIR / "no-such.mp4",
        pack=pack,
        script=script,
        spec=bundle.spec,
        decode=_unmeasurable,
        ocr=_no_ocr,
    )
    return {f.check for f in findings if f.passed is False}


def dump(model: BaseModel, path: Path) -> None:
    payload = json.dumps(
        model.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=True
    )
    path.write_text(payload + "\n", encoding="utf-8")


PAIRS: tuple[tuple[str, str], ...] = (
    ("tiny_text", "text_floor"),
    ("clipping", "clipping"),
    ("wrong_highlight", "ocr_text"),
    ("negation_omitted", "ocr_text"),
    ("axis_change", "axis_change"),
    ("narration_visual_mismatch", "narration_visual"),
    ("broken_transition", "transition"),
)


def build_pair(
    name: str, pack: EvidencePack, folder: Path
) -> tuple[ScriptPlan, ExplainerRenderBundle, ExplainerRenderBundle]:
    builders: dict[str, Callable[[], tuple[ScriptPlan, ExplainerRenderBundle, Mutation]]] = {
        "tiny_text": lambda: pair_tiny_text(pack),
        "clipping": lambda: pair_clipping(pack),
        "wrong_highlight": lambda: pair_wrong_highlight(pack, folder),
        "negation_omitted": lambda: pair_negation_omitted(pack),
        "axis_change": lambda: pair_axis_change(pack),
        "narration_visual_mismatch": lambda: pair_narration_visual_mismatch(pack),
        "broken_transition": lambda: pair_broken_transition(pack),
    }
    script, accepted, mutate = builders[name]()
    return script, accepted, mutate(accepted)


def main() -> int:
    pack = make_pack()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dump(pack, OUT_DIR / "pack.json")
    rows = ["| pair | expected failed check on `rejected.json` |", "| --- | --- |"]
    for name, expected in PAIRS:
        folder = OUT_DIR / name
        folder.mkdir(exist_ok=True)
        script, accepted, rejected = build_pair(name, pack, folder)
        ok, bad = failed_checks(pack, script, accepted), failed_checks(pack, script, rejected)
        if ok or bad != {expected}:
            print(
                f"{name}: accepted fails {sorted(ok)}, rejected fails {sorted(bad)}",
                file=sys.stderr,
            )
            return 1
        dump(script, folder / "script.json")
        dump(accepted, folder / "accepted.json")
        dump(rejected, folder / "rejected.json")
        rows.append(f"| `{name}` | `{expected}` |")
        print(f"{name:>26}  {expected:<17} {accepted.timeline.total_frames} frames")
    readme = (
        "# Review set\n\n"
        "Seven accepted/rejected pairs of compiled `ExplainerRenderBundle` JSON for the "
        "deterministic QC (`content_factory.explainer.qc_checks`), built by "
        "`scripts/make_review_fixtures.py` from `pack.json` and each pair's `script.json`. "
        "A rejected bundle is the accepted one mutated past the compiler's guards, or a second "
        "compile of a spec the compiler cannot refuse.\n\n" + "\n".join(rows) + "\n"
    )
    (OUT_DIR / "README.md").write_text(readme, encoding="utf-8")
    print(f"wrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
