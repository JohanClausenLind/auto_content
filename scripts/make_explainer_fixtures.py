"""Write the Gate B1 fixture trio: an evidence pack, script and visual spec that add up."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from content_factory.schemas.base import SchemaModel
from content_factory.schemas.explainer import (
    AnnotateAction,
    AssetRef,
    Beat,
    Calculation,
    ChartTemplate,
    Claim,
    ClaimOperand,
    CompareAction,
    Cue,
    DatasetColumn,
    DatasetRow,
    Entity,
    EvidenceDataset,
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    FieldEncoding,
    HoldAction,
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
from content_factory.schemas.research import EvidenceLocator, SourceClass

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "fixtures" / "explainer" / "amdahl"
AT = "2026-09-16T00:00:00Z"
DOI = "https://doi.org/10.1145/1465482.1465560"
TITLE = "Validity of the single processor approach to achieving large scale computing capabilities"
AMDAHL = (
    "Amdahl (1967) argued that the serial fraction of a program bounds the speed-up from "
    "parallel hardware."
)
COLD_OPEN = (
    "Your program takes 100 milliseconds. You buy a processor twice as fast. It still takes 60."
)
BUILD_MODEL = (
    "80 milliseconds of computation plus 20 milliseconds of other work is 100 milliseconds."
)
CHANGE_VARIABLE = "Double the computation speed and you get 40 plus 20: 60 milliseconds."
SHOW_LIMITS = (
    "In 1967, Gene Amdahl argued that the serial part of a program bounds the speed-up from "
    "faster hardware."
)
SYNTHESIS = (
    "Speed up only the part you measured, and the rest of the wait stays exactly where it was."
)


def _ms(value: float) -> Quantity:
    return Quantity(magnitude=value, unit="ms")


def _assumed(claim_id: str, statement: str, label: str, value: Quantity, rationale: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=statement,
        short_label=label,
        epistemic_class="illustrative_assumption",
        value=value,
        rationale=rationale,
        checked_at=AT,
    )


def _derived(claim_id: str, statement: str, label: str, value: Quantity, calc_id: str) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=statement,
        short_label=label,
        epistemic_class="illustrative_assumption",
        value=value,
        calculation_id=calc_id,
        rationale="Recomputed from the illustrative inputs by the named calculation.",
        checked_at=AT,
    )


def _operands(*claim_ids: str) -> tuple[ClaimOperand, ...]:
    return tuple(ClaimOperand(kind="claim", claim_id=cid) for cid in claim_ids)


def _row(scenario: str, part: str, ms: float, claim_id: str) -> DatasetRow:
    return DatasetRow(key=f"{scenario}-{part}", values=(scenario, part, ms), claim_ids=(claim_id,))


def build_pack() -> EvidencePack:
    source = EvidenceSource(
        source_id="src_amdahl_1967",
        url=DOI,
        canonical_url=DOI,
        publisher="ACM",
        title=TITLE,
        author="Amdahl, G. M.",
        published_at="1967-04-18",
        accessed_at=AT,
        content_sha256=hashlib.sha256(TITLE.encode("utf-8")).hexdigest(),
        source_class=SourceClass.analysis,
    )
    item = EvidenceItem(
        item_id="evi_amdahl_title",
        source_id=source.source_id,
        passage=TITLE,
        locator=EvidenceLocator(kind="char_range", start=0, end=len(TITLE)),
        time_basis="1967",
        quoted_at=AT,
    )
    claims = (
        Claim(
            claim_id="clm_amdahl_1967",
            statement=AMDAHL,
            short_label="Amdahl's 1967 bound",
            epistemic_class="observation",
            evidence_ids=(item.item_id,),
            time_basis="1967",
            stable=True,
            rationale="The paper's thesis, as its title states it; quoted rather than valued.",
            checked_at=AT,
        ),
        _assumed(
            "clm_compute_80ms",
            "In the illustration, computation takes 80 ms of the wait.",
            "Computation: 80 ms",
            _ms(80),
            "A round split chosen for the illustration; any large share makes the same point.",
        ),
        _assumed(
            "clm_other_20ms",
            "In the illustration, other work takes 20 ms of the wait.",
            "Other work: 20 ms",
            _ms(20),
            "The part the faster processor does not touch; chosen round for the illustration.",
        ),
        _assumed(
            "clm_speedup_2x",
            "The faster processor halves computation time.",
            "Processor 2x faster",
            Quantity(magnitude=2, unit="ratio"),
            "Doubling is the cleanest case of the argument.",
        ),
        _derived(
            "clm_total_100ms",
            "The program takes 100 ms before the upgrade.",
            "Total before: 100 ms",
            _ms(100),
            "calc_total_base",
        ),
        _derived(
            "clm_compute_40ms",
            "Computation takes 40 ms on the faster processor.",
            "Computation after: 40 ms",
            _ms(40),
            "calc_compute_fast",
        ),
        _derived(
            "clm_total_60ms",
            "The program takes 60 ms after the upgrade.",
            "Total after: 60 ms",
            _ms(60),
            "calc_total_fast",
        ),
    )
    calculations = (
        Calculation(
            calc_id="calc_total_base",
            op="add",
            operands=_operands("clm_compute_80ms", "clm_other_20ms"),
            result_claim_id="clm_total_100ms",
        ),
        Calculation(
            calc_id="calc_compute_fast",
            op="div",
            operands=_operands("clm_compute_80ms", "clm_speedup_2x"),
            result_claim_id="clm_compute_40ms",
        ),
        Calculation(
            calc_id="calc_total_fast",
            op="add",
            operands=_operands("clm_compute_40ms", "clm_other_20ms"),
            result_claim_id="clm_total_60ms",
        ),
    )
    dataset = EvidenceDataset(
        dataset_id="ds_latency_split",
        title="Where the wait goes",
        columns=(
            DatasetColumn(name="scenario", kind="nominal"),
            DatasetColumn(name="part", kind="nominal"),
            DatasetColumn(name="ms", kind="quantitative", unit="ms"),
        ),
        rows=(
            _row("baseline", "computation", 80, "clm_compute_80ms"),
            _row("baseline", "other", 20, "clm_other_20ms"),
            _row("faster", "computation", 40, "clm_compute_40ms"),
            _row("faster", "other", 20, "clm_other_20ms"),
        ),
    )
    return EvidencePack(
        pack_id="pack_amdahl_demo",
        topic="Why doubling the processor does not halve the wait",
        language="en",
        frozen_at=AT,
        sources=(source,),
        items=(item,),
        claims=claims,
        datasets=(dataset,),
        calculations=calculations,
    )


def _segment(segment_id: str, section: str, text: str, *claim_ids: str) -> ScriptSegment:
    return ScriptSegment.model_validate(
        {
            "segment_id": segment_id,
            "section": section,
            "spoken_text": text,
            "tokens": tokenize(text),
            "claim_ids": claim_ids,
        }
    )


def build_script(pack: EvidencePack) -> ScriptPlan:
    return ScriptPlan(
        script_id="scr_amdahl_demo",
        channel_id="ch_explainer_demo",
        pack_id=pack.pack_id,
        pack_hash=pack.pack_hash(),
        question="Why does a processor twice as fast not make the program twice as fast?",
        contribution=(
            "One wait split into two parts shows the part you did not speed up setting the "
            "ceiling, then names the paper that proved it in general."
        ),
        promises=(
            TitlePromise(
                title="Why doubling the processor does not halve the wait",
                thumbnail_promise="100 ms becomes 60 ms, not 50",
                claim_ids=("clm_total_100ms", "clm_total_60ms"),
            ),
            TitlePromise(
                title="Amdahl's law in one bar chart",
                thumbnail_promise="Two bars, one untouched part, one ceiling",
                claim_ids=("clm_amdahl_1967",),
            ),
            TitlePromise(
                title="The 20 milliseconds you cannot buy your way out of",
                thumbnail_promise="Faster hardware leaves the other work alone",
                claim_ids=("clm_other_20ms",),
            ),
        ),
        segments=(
            _segment(
                "seg_cold_open",
                "cold_open",
                COLD_OPEN,
                "clm_total_100ms",
                "clm_speedup_2x",
                "clm_total_60ms",
            ),
            _segment(
                "seg_build_model",
                "build_model",
                BUILD_MODEL,
                "clm_compute_80ms",
                "clm_other_20ms",
                "clm_total_100ms",
            ),
            _segment(
                "seg_change_variable",
                "change_variable",
                CHANGE_VARIABLE,
                "clm_speedup_2x",
                "clm_compute_40ms",
                "clm_other_20ms",
                "clm_total_60ms",
            ),
            _segment("seg_show_limits", "show_limits", SHOW_LIMITS, "clm_amdahl_1967"),
            _segment("seg_synthesis", "synthesis", SYNTHESIS),
        ),
        locked_at=AT,
    )


def _at(segment: ScriptSegment, word: str) -> int:
    """Index of the first token equal to ``word`` once trailing punctuation is stripped."""
    for index, token in enumerate(segment.tokens):
        if token.rstrip(".,:;!?") == word:
            return index
    msg = f"{word!r} is not a token of {segment.segment_id}"
    raise SystemExit(msg)


def _last(segment: ScriptSegment) -> int:
    return len(segment.tokens) - 1


def _cue(segment: ScriptSegment, start: int, end: int, relation: str, duration: str) -> Cue:
    return Cue.model_validate(
        {
            "segment_id": segment.segment_id,
            "token_start": start,
            "token_end": end,
            "relation": relation,
            "duration_class": duration,
        }
    )


def _reveal(*targets: str) -> TargetAction:
    return TargetAction(action="reveal", targets=targets)


def build_spec(pack: EvidencePack, script: ScriptPlan) -> VisualSpec:
    dataset = pack.datasets[0]
    build = script.segment("seg_build_model")
    change = script.segment("seg_change_variable")
    limits = script.segment("seg_show_limits")
    bars = Scene(
        scene_id="scn_latency_bars",
        section="build_model",
        purpose="Show the wait as two stacked parts so the untouched part is visible early.",
        template=ChartTemplate(
            template="chart",
            chart_kind="stacked_bar",
            dataset_asset_id="ast_latency_split",
            x=FieldEncoding(field="scenario", kind="nominal", title="Scenario"),
            y=FieldEncoding(field="ms", kind="quantitative", unit="ms", title="ms"),
            series_field="part",
            series=(
                SeriesBinding(value="computation", entity_id="ent_series_compute"),
                SeriesBinding(value="other", entity_id="ent_series_other"),
            ),
        ),
        beats=(
            Beat(
                beat_id="bt_reveal_series",
                cue=_cue(build, 0, 1, "on", "short"),
                actions=(_reveal("ent_series_compute", "ent_series_other"),),
            ),
            Beat(
                beat_id="bt_highlight_compute",
                cue=_cue(build, _at(build, "80"), _at(build, "computation"), "on", "short"),
                actions=(TargetAction(action="highlight", targets=("ent_series_compute",)),),
            ),
            Beat(
                beat_id="bt_reveal_base",
                cue=_cue(build, _at(build, "100"), _last(build), "on", "short"),
                actions=(_reveal("ent_total_base"),),
            ),
            Beat(
                beat_id="bt_annotate_base",
                cue=_cue(build, _at(build, "100"), _last(build), "after", "short"),
                actions=(
                    AnnotateAction(
                        action="annotate",
                        targets=("ent_total_base",),
                        text="100 ms",
                        claim_id="clm_total_100ms",
                    ),
                ),
            ),
            Beat(
                beat_id="bt_reveal_fast",
                cue=_cue(change, _at(change, "60"), _at(change, "60"), "on", "short"),
                actions=(_reveal("ent_total_fast"),),
            ),
            Beat(
                beat_id="bt_compare_totals",
                cue=_cue(change, _at(change, "60"), _last(change), "on", "medium"),
                actions=(
                    CompareAction(action="compare", targets=("ent_total_base", "ent_total_fast")),
                ),
            ),
            Beat(
                beat_id="bt_hold_bars",
                cue=_cue(change, _last(change), _last(change), "after", "long"),
                actions=(HoldAction(action="hold"),),
            ),
        ),
        claim_ids=(
            "clm_compute_80ms",
            "clm_other_20ms",
            "clm_total_100ms",
            "clm_compute_40ms",
            "clm_total_60ms",
        ),
    )
    sixty = Scene(
        scene_id="scn_big_sixty",
        section="change_variable",
        purpose="Land the answer as one number the viewer cannot miss.",
        template=TextTemplate(
            template="text",
            variant="big_number",
            items=(TextItem(entity_id="ent_big_sixty", text="60 ms", claim_id="clm_total_60ms"),),
        ),
        layout="overlay",
        beats=(
            Beat(
                beat_id="bt_reveal_sixty",
                cue=_cue(change, _at(change, "60"), _at(change, "60"), "on", "short"),
                actions=(_reveal("ent_big_sixty"),),
            ),
            Beat(
                beat_id="bt_hold_sixty",
                cue=_cue(change, _last(change), _last(change), "after", "long"),
                actions=(HoldAction(action="hold"),),
            ),
        ),
        claim_ids=("clm_total_60ms",),
    )
    card = Scene(
        scene_id="scn_amdahl_card",
        section="show_limits",
        purpose="Name the general result and its author; the illustration is not the proof.",
        template=TextTemplate(
            template="text",
            variant="statement",
            items=(
                TextItem(entity_id="ent_amdahl_quote", text=AMDAHL, claim_id="clm_amdahl_1967"),
            ),
        ),
        beats=(
            Beat(
                beat_id="bt_reveal_card",
                cue=_cue(limits, 0, _at(limits, "Amdahl"), "on", "short"),
                actions=(_reveal("ent_amdahl_quote"),),
            ),
            Beat(
                beat_id="bt_hold_card",
                cue=_cue(limits, _last(limits), _last(limits), "after", "long"),
                actions=(HoldAction(action="hold"),),
            ),
        ),
        claim_ids=("clm_amdahl_1967",),
        source_ids=("src_amdahl_1967",),
    )
    return VisualSpec(
        spec_id="vs_amdahl_demo",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        design_system_version=1,
        entities=(
            Entity(
                entity_id="ent_series_compute",
                label="Computation",
                short_label="Computation",
                kind="series",
                claim_ids=("clm_compute_80ms", "clm_compute_40ms"),
            ),
            Entity(
                entity_id="ent_series_other",
                label="Other work",
                short_label="Other",
                kind="series",
                claim_ids=("clm_other_20ms",),
            ),
            Entity(
                entity_id="ent_total_base",
                label="Total before",
                kind="value",
                claim_ids=("clm_total_100ms",),
            ),
            Entity(
                entity_id="ent_total_fast",
                label="Total after",
                kind="value",
                claim_ids=("clm_total_60ms",),
            ),
            Entity(
                entity_id="ent_big_sixty",
                label="60 ms",
                kind="text",
                claim_ids=("clm_total_60ms",),
            ),
            Entity(
                entity_id="ent_amdahl_quote",
                label="Amdahl 1967",
                kind="quote",
                claim_ids=("clm_amdahl_1967",),
            ),
        ),
        assets=(
            AssetRef(
                asset_id="ast_latency_split",
                kind="dataset",
                sha256=hashlib.sha256(dataset.canonical_json().encode("utf-8")).hexdigest(),
                dataset_id=dataset.dataset_id,
            ),
        ),
        scenes=(bars, sixty, card),
    )


def build_fixtures() -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    pack = build_pack()
    script = build_script(pack)
    return pack, script, build_spec(pack, script)


def write_fixtures(out_dir: Path) -> list[Path]:
    """Write pack.json, script.json and spec.json; the bytes depend only on the models."""
    pack, script, spec = build_fixtures()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in (("pack", pack), ("script", script), ("spec", spec)):
        path = out_dir / f"{name}.json"
        path.write_text(_dumps(model), encoding="utf-8")
        written.append(path)
    return written


def _dumps(model: SchemaModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="directory for the three files")
    args = parser.parse_args()
    for path in write_fixtures(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
