"""Write fixtures/explainer/sixty: a 60 s bicycle-gears episode that exercises most actions."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from content_factory.explainer.compile import compile_episode
from content_factory.schemas.explainer import (
    Action,
    AnnotateAction,
    AssetRef,
    Beat,
    Calculation,
    ChartTemplate,
    Claim,
    ClaimOperand,
    CompareAction,
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
    EvidencePack,
    EvidenceSource,
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
    Section,
    SeriesBinding,
    TargetAction,
    TextItem,
    TextTemplate,
    TitlePromise,
    VisualSpec,
    tokenize,
)
from content_factory.schemas.research import SourceClass

OUT_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "explainer" / "sixty"
DATE = "2026-09-22"
FROZEN_AT = f"{DATE}T00:00:00Z"
RATIONALE = "illustrative gear set chosen for round numbers, not a measured bicycle"
GEARS: tuple[tuple[str, int, int, float], ...] = (
    ("low", 30, 30, 1.0),
    ("mid", 30, 15, 2.0),
    ("high", 45, 15, 3.0),
    ("top", 48, 12, 4.0),
)
WHEEL_M = 2.1
TargetName = Literal[
    "reveal",
    "hide",
    "highlight",
    "clear_highlight",
    "focus",
    "trace",
    "draw",
    "flow",
    "isolate",
    "zoom_to",
    "pan_to",
]
TextVariant = Literal["statement", "big_number", "list", "quotation_card", "formula"]

SEGMENTS: tuple[tuple[str, Section, str, tuple[str, ...]], ...] = (
    (
        "seg_coldopen1",
        "cold_open",
        "Shift a bicycle into top gear and one pedal turn rolls you 8.4 metres. "
        "Shift to the lowest and the same turn moves you 2.1 metres.",
        ("clm_dist_top", "clm_dist_low"),
    ),
    (
        "seg_question1",
        "question_stakes",
        "Same legs, same pedal turn, four times the distance. "
        "Where does it come from, and what does it cost?",
        ("clm_ratio_top",),
    ),
    (
        "seg_build0001",
        "build_model",
        "The chain links a front chainring to a rear cog. "
        "Their tooth counts set the gear ratio: front over rear.",
        (),
    ),
    (
        "seg_run000001",
        "run_system",
        "Our low gear pairs 30 teeth with 30, a ratio of 1. "
        "Top pairs 48 teeth with 12, a ratio of 4.",
        (
            "clm_front_low",
            "clm_rear_low",
            "clm_ratio_low",
            "clm_front_top",
            "clm_rear_top",
            "clm_ratio_top",
        ),
    ),
    (
        "seg_change001",
        "change_variable",
        "Step through the gears and the ratio climbs from 1 to 2, then 3, then 4. "
        "Each step buys distance per pedal turn.",
        ("clm_ratio_low", "clm_ratio_mid", "clm_ratio_high", "clm_ratio_top"),
    ),
    (
        "seg_limits001",
        "show_limits",
        "The price is force. Top gear asks your legs for 4 times the effort of low gear, "
        "so a steep hill stalls you.",
        ("clm_torque_x4",),
    ),
    (
        "seg_synth0001",
        "synthesis",
        "Gears do not make power. They trade force for distance, and choosing one is choosing "
        "which you need now.",
        (),
    ),
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def claim(
    claim_id: str, statement: str, magnitude: float, unit: str, calc: str | None = None
) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=statement,
        epistemic_class="illustrative_assumption",
        value=Quantity(magnitude=magnitude, unit=unit),
        calculation_id=calc,
        rationale=RATIONALE,
        checked_at=DATE,
    )


def make_pack() -> EvidencePack:
    claims: list[Claim] = []
    calcs: list[Calculation] = []
    teeth_rows: list[DatasetRow] = []
    ratio_rows: list[DatasetRow] = []
    for gear, front, rear, ratio in GEARS:
        front_id, rear_id, ratio_id = f"clm_front_{gear}", f"clm_rear_{gear}", f"clm_ratio_{gear}"
        calc_id = f"calc_ratio_{gear}"
        claims += [
            claim(
                front_id, f"The {gear} gear uses a {front}-tooth front chainring", front, "count"
            ),
            claim(rear_id, f"The {gear} gear uses a {rear}-tooth rear cog", rear, "count"),
            claim(ratio_id, f"The {gear} gear ratio is {ratio:g}", ratio, "ratio", calc_id),
        ]
        calcs.append(
            Calculation(
                calc_id=calc_id,
                op="div",
                operands=(
                    ClaimOperand(kind="claim", claim_id=front_id),
                    ClaimOperand(kind="claim", claim_id=rear_id),
                ),
                result_claim_id=ratio_id,
            )
        )
        teeth_rows += [
            DatasetRow(key=f"{gear}-front", values=(gear, "front", front), claim_ids=(front_id,)),
            DatasetRow(key=f"{gear}-rear", values=(gear, "rear", rear), claim_ids=(rear_id,)),
        ]
        ratio_rows.append(
            DatasetRow(
                key=gear, values=(gear, front, rear, ratio), claim_ids=(front_id, rear_id, ratio_id)
            )
        )
    claims += [
        claim("clm_wheel_circ", "The rear wheel rolls 2.1 m per turn", WHEEL_M, "m"),
        claim(
            "clm_dist_top",
            "In top gear one pedal turn rolls the bike 8.4 m",
            8.4,
            "m",
            "calc_dist_top",
        ),
        claim(
            "clm_dist_low",
            "In low gear one pedal turn rolls the bike 2.1 m",
            2.1,
            "m",
            "calc_dist_low",
        ),
        claim(
            "clm_torque_x4",
            "Top gear asks 4 times the pedal effort of low gear",
            4.0,
            "ratio",
            "calc_torque_x4",
        ),
    ]
    calcs += [
        Calculation(
            calc_id="calc_dist_top",
            op="mul",
            operands=(
                ClaimOperand(kind="claim", claim_id="clm_ratio_top"),
                ClaimOperand(kind="claim", claim_id="clm_wheel_circ"),
            ),
            result_claim_id="clm_dist_top",
        ),
        Calculation(
            calc_id="calc_dist_low",
            op="mul",
            operands=(
                ClaimOperand(kind="claim", claim_id="clm_ratio_low"),
                ClaimOperand(kind="claim", claim_id="clm_wheel_circ"),
            ),
            result_claim_id="clm_dist_low",
        ),
        Calculation(
            calc_id="calc_torque_x4",
            op="div",
            operands=(
                ClaimOperand(kind="claim", claim_id="clm_ratio_top"),
                ClaimOperand(kind="claim", claim_id="clm_ratio_low"),
            ),
            result_claim_id="clm_torque_x4",
        ),
    ]
    teeth = EvidenceDataset(
        dataset_id="ds_gear_teeth",
        title="Teeth per gear and part",
        columns=(
            DatasetColumn(name="gear", kind="nominal"),
            DatasetColumn(name="part", kind="nominal"),
            DatasetColumn(name="teeth", kind="quantitative", unit="count"),
        ),
        rows=tuple(teeth_rows),
    )
    ratios = EvidenceDataset(
        dataset_id="ds_gear_ratios",
        title="Gear ratios",
        columns=(
            DatasetColumn(name="gear", kind="nominal"),
            DatasetColumn(name="teeth_front", kind="quantitative", unit="count"),
            DatasetColumn(name="teeth_rear", kind="quantitative", unit="count"),
            DatasetColumn(name="ratio", kind="quantitative", unit="ratio"),
        ),
        rows=tuple(ratio_rows),
    )
    source = EvidenceSource(
        source_id="src_wiki_gear",
        url="https://en.wikipedia.org/wiki/Bicycle_gearing",
        canonical_url="https://en.wikipedia.org/wiki/Bicycle_gearing",
        publisher="Wikipedia",
        title="Bicycle gearing",
        accessed_at=DATE,
        content_sha256=sha("fixture: background reading only, no claim rests on it"),
        source_class=SourceClass.reference,
    )
    pack = EvidencePack(
        pack_id="pack_gears_60s",
        topic="How a bicycle's gears trade force for distance",
        sources=(source,),
        claims=tuple(claims),
        datasets=(teeth, ratios),
        calculations=tuple(calcs),
    )
    return pack.frozen(FROZEN_AT)


def make_script(pack: EvidencePack) -> ScriptPlan:
    segments = tuple(
        ScriptSegment(
            segment_id=sid, section=section, spoken_text=text, tokens=tokenize(text), claim_ids=ids
        )
        for sid, section, text, ids in SEGMENTS
    )
    return ScriptPlan(
        script_id="scr_gears_60s",
        channel_id="ch_explain01",
        pack_id=pack.pack_id,
        pack_hash=pack.pack_hash(),
        question="Why does a higher gear go further per pedal turn but feel harder?",
        contribution="One gear set, one wheel: the ratio is the whole trade.",
        promises=(
            TitlePromise(
                title="Gears trade force for distance",
                thumbnail_promise="4x the distance, 4x the effort",
                claim_ids=("clm_dist_top", "clm_torque_x4"),
            ),
        ),
        segments=segments,
        locked_at=FROZEN_AT,
    )


def token_index(script: ScriptPlan, segment_id: str, word: str, occurrence: int = 0) -> int:
    tokens = script.segment(segment_id).tokens
    hits = [i for i, t in enumerate(tokens) if t == word]
    if len(hits) <= occurrence:
        msg = f"{segment_id} has no token {word!r} (occurrence {occurrence}); tokens: {tokens}"
        raise ValueError(msg)
    return hits[occurrence]


class _Cues:
    """Beats cued by token text, so the fixture survives rewording without index arithmetic."""

    def __init__(self, script: ScriptPlan, segment_id: str) -> None:
        self.script = script
        self.segment_id = segment_id

    def beat(
        self,
        beat_id: str,
        word: str,
        *actions: Action,
        to_word: str | None = None,
        relation: CueRelation = "on",
        duration: DurationClass = "short",
        occurrence: int = 0,
    ) -> Beat:
        start = token_index(self.script, self.segment_id, word, occurrence)
        end = start if to_word is None else token_index(self.script, self.segment_id, to_word)
        cue = Cue(
            segment_id=self.segment_id,
            token_start=start,
            token_end=end,
            relation=relation,
            duration_class=duration,
        )
        return Beat(beat_id=beat_id, cue=cue, actions=actions)


def reveal(*targets: str) -> TargetAction:
    return TargetAction(action="reveal", targets=targets)


def act(action: TargetName, *targets: str) -> TargetAction:
    return TargetAction(action=action, targets=targets)


def text_scene(
    scene_id: str,
    section: Section,
    purpose: str,
    variant: TextVariant,
    items: tuple[TextItem, ...],
    beats: tuple[Beat, ...],
) -> Scene:
    template = TextTemplate(template="text", variant=variant, items=items)
    return Scene(
        scene_id=scene_id, section=section, purpose=purpose, template=template, beats=beats
    )


def make_spec(pack: EvidencePack, script: ScriptPlan) -> VisualSpec:
    entities = (
        Entity(
            entity_id="ent_open_txt",
            label="Opening statement",
            kind="text",
            claim_ids=("clm_dist_top",),
        ),
        Entity(entity_id="ent_big_value", label="4x", kind="value", claim_ids=("clm_ratio_top",)),
        Entity(entity_id="ent_big_label", label="Big number label", kind="text"),
        Entity(entity_id="ent_pedals_n", label="Pedals", kind="node"),
        Entity(
            entity_id="ent_chainrng", label="Front chainring", short_label="Chainring", kind="node"
        ),
        Entity(entity_id="ent_rearcog_n", label="Rear cog", kind="node"),
        Entity(entity_id="ent_wheel_nd", label="Rear wheel", kind="node"),
        Entity(entity_id="ent_crank_ed", label="crank", kind="edge"),
        Entity(entity_id="ent_chain_ed", label="chain", kind="edge"),
        Entity(entity_id="ent_hub_edge", label="hub", kind="edge"),
        Entity(
            entity_id="ent_front_ser",
            label="Front teeth",
            kind="series",
            claim_ids=("clm_front_top",),
        ),
        Entity(
            entity_id="ent_rear_ser", label="Rear teeth", kind="series", claim_ids=("clm_rear_top",)
        ),
        Entity(
            entity_id="ent_ratio_ser",
            label="Gear ratio",
            kind="series",
            claim_ids=("clm_ratio_top",),
        ),
        Entity(entity_id="ent_lim_price", label="The price is force", kind="text"),
        Entity(
            entity_id="ent_lim_effort",
            label="Top gear effort",
            kind="text",
            claim_ids=("clm_torque_x4",),
        ),
        Entity(
            entity_id="ent_lim_lowgr",
            label="Low gear distance",
            kind="text",
            claim_ids=("clm_dist_low",),
        ),
        Entity(entity_id="ent_lim_hills", label="Steep hills", kind="text"),
        Entity(entity_id="ent_frm_ratio", label="gear ratio =", kind="text"),
        Entity(entity_id="ent_frm_frac", label="T_f over T_r", kind="text"),
        Entity(
            entity_id="ent_frm_value",
            label="48 over 12 = 4",
            kind="text",
            claim_ids=("clm_ratio_top",),
        ),
        Entity(entity_id="ent_close_txt", label="Closing statement", kind="text"),
    )
    datasets = {d.dataset_id: d for d in pack.datasets}
    assets = (
        AssetRef(
            asset_id="ast_gear_teeth",
            kind="dataset",
            sha256=datasets["ds_gear_teeth"].content_hash(),
            dataset_id="ds_gear_teeth",
        ),
        AssetRef(
            asset_id="ast_gear_ratio",
            kind="dataset",
            sha256=datasets["ds_gear_ratios"].content_hash(),
            dataset_id="ds_gear_ratios",
        ),
    )
    c1, c2, c3, c4, c5, c6, c7 = (_Cues(script, sid) for sid, _, _, _ in SEGMENTS)
    scenes = (
        text_scene(
            "scn_open0001",
            "cold_open",
            "State the surprising distance per pedal turn",
            "statement",
            (
                TextItem(
                    entity_id="ent_open_txt",
                    text="One pedal turn rolls 8.4 m in top gear",
                    claim_id="clm_dist_top",
                ),
            ),
            (
                c1.beat("bt_open_reveal", "Shift", reveal("ent_open_txt")),
                c1.beat(
                    "bt_open_number",
                    "8.4",
                    act("highlight", "ent_open_txt"),
                    to_word="metres.",
                    duration="beat",
                ),
                c1.beat("bt_open_hold00", "Shift", HoldAction(action="hold"), occurrence=1),
            ),
        ),
        text_scene(
            "scn_question",
            "question_stakes",
            "Name the factor the episode explains",
            "big_number",
            (
                TextItem(entity_id="ent_big_value", text="4×", claim_id="clm_ratio_top"),  # noqa: RUF001  the sign is what the viewer reads
                TextItem(entity_id="ent_big_label", text="more distance per pedal turn"),
            ),
            (
                c2.beat("bt_ques_value0", "four", reveal("ent_big_value")),
                c2.beat(
                    "bt_ques_label0",
                    "the",
                    reveal("ent_big_label"),
                    HoldAction(action="hold"),
                    duration="beat",
                ),
            ),
        ),
        Scene(
            scene_id="scn_model001",
            section="build_model",
            purpose="Build the drivetrain from pedals to wheel",
            template=DiagramTemplate(
                template="diagram",
                direction="LR",
                nodes=(
                    DiagramNodeSpec(entity_id="ent_pedals_n", label="Pedals"),
                    DiagramNodeSpec(entity_id="ent_chainrng", label="Front chainring"),
                    DiagramNodeSpec(entity_id="ent_rearcog_n", label="Rear cog"),
                    DiagramNodeSpec(entity_id="ent_wheel_nd", label="Rear wheel"),
                ),
                edges=(
                    DiagramEdgeSpec(
                        entity_id="ent_crank_ed",
                        source_entity_id="ent_pedals_n",
                        target_entity_id="ent_chainrng",
                        label="crank",
                    ),
                    DiagramEdgeSpec(
                        entity_id="ent_chain_ed",
                        source_entity_id="ent_chainrng",
                        target_entity_id="ent_rearcog_n",
                        label="chain",
                    ),
                    DiagramEdgeSpec(
                        entity_id="ent_hub_edge",
                        source_entity_id="ent_rearcog_n",
                        target_entity_id="ent_wheel_nd",
                        label="hub",
                    ),
                ),
            ),
            initial_visible=("ent_pedals_n", "ent_chainrng", "ent_crank_ed"),
            beats=(
                c3.beat(
                    "bt_model_chain0",
                    "chain",
                    reveal("ent_rearcog_n", "ent_chain_ed"),
                    act("flow", "ent_crank_ed", "ent_chain_ed"),
                ),
                c3.beat(
                    "bt_model_hide00",
                    "Their",
                    act("hide", "ent_pedals_n", "ent_crank_ed"),
                    relation="before",
                    duration="beat",
                ),
                c3.beat(
                    "bt_model_wheel0",
                    "set",
                    reveal("ent_wheel_nd", "ent_hub_edge"),
                    act("trace", "ent_hub_edge"),
                ),
                c3.beat(
                    "bt_model_zoom00",
                    "front",
                    act("zoom_to", "ent_rearcog_n"),
                    act("highlight", "ent_rearcog_n", "ent_chainrng"),
                    to_word="over",
                    duration="medium",
                    occurrence=1,
                ),
            ),
        ),
        Scene(
            scene_id="scn_teeth001",
            section="run_system",
            purpose="Count the teeth of the low and top gears",
            template=ChartTemplate(
                template="chart",
                chart_kind="bar",
                dataset_asset_id="ast_gear_teeth",
                x=FieldEncoding(field="gear", kind="nominal", title="Gear"),
                y=FieldEncoding(field="teeth", kind="quantitative", unit="count", title="Teeth"),
                series_field="part",
                series=(
                    SeriesBinding(value="front", entity_id="ent_front_ser"),
                    SeriesBinding(value="rear", entity_id="ent_rear_ser"),
                ),
            ),
            beats=(
                c4.beat("bt_teeth_front0", "30", reveal("ent_front_ser")),
                c4.beat("bt_teeth_rear00", "30,", reveal("ent_rear_ser")),
                c4.beat(
                    "bt_teeth_compar",
                    "Top",
                    CompareAction(action="compare", targets=("ent_front_ser", "ent_rear_ser")),
                    HoldAction(action="hold"),
                ),
                c4.beat(
                    "bt_teeth_annot0",
                    "48",
                    AnnotateAction(
                        action="annotate",
                        targets=("ent_front_ser",),
                        text="48 teeth",
                        claim_id="clm_front_top",
                    ),
                ),
                c4.beat("bt_teeth_isolat", "12,", act("isolate", "ent_rear_ser")),
            ),
        ),
        Scene(
            scene_id="scn_ratio001",
            section="change_variable",
            purpose="Step the ratio through the four gears",
            template=ChartTemplate(
                template="chart",
                chart_kind="line",
                dataset_asset_id="ast_gear_ratio",
                x=FieldEncoding(field="gear", kind="ordinal", title="Gear"),
                y=FieldEncoding(
                    field="ratio", kind="quantitative", unit="ratio", title="Gear ratio"
                ),
                series=(SeriesBinding(value="ratio", entity_id="ent_ratio_ser"),),
            ),
            beats=(
                c5.beat("bt_ratio_reveal", "Step", reveal("ent_ratio_ser"), relation="before"),
                c5.beat(
                    "bt_ratio_trace0",
                    "climbs",
                    act("trace", "ent_ratio_ser"),
                    to_word="4.",
                    duration="long",
                ),
                c5.beat(
                    "bt_ratio_annot0",
                    "4.",
                    AnnotateAction(
                        action="annotate",
                        targets=("ent_ratio_ser",),
                        text="4×",  # noqa: RUF001  the sign is what the viewer reads
                        claim_id="clm_ratio_top",
                    ),
                    HoldAction(action="hold"),
                    duration="beat",
                ),
                c5.beat(
                    "bt_ratio_hilite", "Each", act("highlight", "ent_ratio_ser"), duration="beat"
                ),
            ),
        ),
        text_scene(
            "scn_limits01",
            "show_limits",
            "Show what the distance costs",
            "list",
            (
                TextItem(entity_id="ent_lim_price", text="The price is force"),
                TextItem(
                    entity_id="ent_lim_effort",
                    text="Top gear: 4 times the pedal effort of low gear",
                    claim_id="clm_torque_x4",
                ),
                TextItem(
                    entity_id="ent_lim_lowgr",
                    text="Low gear: 2.1 m per pedal turn",
                    claim_id="clm_dist_low",
                ),
                TextItem(entity_id="ent_lim_hills", text="Steep hills want low gears"),
            ),
            (
                c6.beat("bt_lim_price000", "The", reveal("ent_lim_price")),
                c6.beat("bt_lim_effort00", "4", reveal("ent_lim_effort")),
                c6.beat("bt_lim_lowgear0", "low", reveal("ent_lim_lowgr")),
                c6.beat("bt_lim_hills000", "steep", reveal("ent_lim_hills")),
            ),
        ),
        text_scene(
            "scn_formula1",
            "synthesis",
            "Write the ratio as a formula",
            "formula",
            (
                TextItem(entity_id="ent_frm_ratio", text="\\text{gear ratio} ="),
                TextItem(entity_id="ent_frm_frac", text="\\frac{T_f}{T_r}"),
                TextItem(
                    entity_id="ent_frm_value", text="= \\frac{48}{12} = 4", claim_id="clm_ratio_top"
                ),
            ),
            (
                c7.beat(
                    "bt_frm_reveal00",
                    "power.",
                    reveal("ent_frm_ratio", "ent_frm_frac", "ent_frm_value"),
                ),
                c7.beat(
                    "bt_frm_compare0",
                    "force",
                    CompareAction(action="compare", targets=("ent_frm_frac", "ent_frm_value")),
                ),
            ),
        ),
        text_scene(
            "scn_close001",
            "synthesis",
            "Close on the trade",
            "statement",
            (TextItem(entity_id="ent_close_txt", text="Gears trade force for distance"),),
            (
                c7.beat("bt_close_reveal", "one", reveal("ent_close_txt")),
                c7.beat("bt_close_hold00", "is", HoldAction(action="hold"), duration="beat"),
            ),
        ),
    )
    return VisualSpec(
        spec_id="spec_gears_60s",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        design_system_version=1,
        entities=entities,
        assets=assets,
        scenes=scenes,
    )


def row_layouts(spec: VisualSpec, regions: dict[str, PixelBox]) -> tuple[DiagramLayout, ...]:
    """Nodes in one row, straight edges: a stand-in for the ELK script when Node is not wanted."""
    layouts: list[DiagramLayout] = []
    for scene in spec.scenes:
        template = scene.template
        if not isinstance(template, DiagramTemplate):
            continue
        plot = regions[scene.scene_id]
        n = len(template.nodes)
        step = plot.width // n
        boxes = {
            node.entity_id: PixelBox(
                x=plot.x + i * step, y=plot.y + plot.height // 2 - 32, width=step - 24, height=64
            )
            for i, node in enumerate(template.nodes)
        }
        edges = tuple(
            LayoutEdge(
                entity_id=e.entity_id,
                points=(
                    LayoutPoint(
                        x=boxes[e.source_entity_id].x + boxes[e.source_entity_id].width,
                        y=plot.y + plot.height / 2,
                    ),
                    LayoutPoint(x=boxes[e.target_entity_id].x, y=plot.y + plot.height / 2),
                ),
            )
            for e in template.edges
        )
        nodes = tuple(LayoutNode(entity_id=eid, box=box) for eid, box in boxes.items())
        layouts.append(
            DiagramLayout(
                scene_id=scene.scene_id,
                width=plot.width,
                height=plot.height,
                nodes=nodes,
                edges=edges,
            )
        )
    return tuple(layouts)


def dump(model: BaseModel, path: Path) -> None:
    payload = json.dumps(
        model.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=True
    )
    path.write_text(payload + "\n", encoding="utf-8")


def main() -> int:
    pack = make_pack()
    script = make_script(pack)
    spec = make_spec(pack, script)
    bundle = compile_episode(pack, script, spec, layout_diagrams=row_layouts)
    fps = bundle.timeline.fps
    for compiled in bundle.timeline.scenes:
        start_s, dur_s = compiled.start_frame / fps, compiled.duration_frames / fps
        count = len(compiled.actions)
        print(f"{compiled.scene_id:>14} {start_s:6.2f} s +{dur_s:5.2f} s  {count} actions")
    total_s = bundle.timeline.total_frames / fps
    tokens = sum(len(s.tokens) for s in script.segments)
    print(f"tokens {tokens}, total_frames {bundle.timeline.total_frames} = {total_s:.2f} s")
    if not 59.0 <= total_s <= 61.0:
        print("the estimated compile must land at 60 s +- 1 s; tune the last hold", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dump(pack, OUT_DIR / "pack.json")
    dump(script, OUT_DIR / "script.json")
    dump(spec, OUT_DIR / "spec.json")
    print(f"wrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
