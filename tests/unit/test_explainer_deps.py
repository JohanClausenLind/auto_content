"""Gate B2: the explainer dependency graph and what a source change invalidates."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from content_factory.explainer.deps import (
    DependencyGraph,
    build_graph,
    diff_sources,
    invalidation,
    node_id,
)
from content_factory.schemas.explainer import (
    AnnotateAction,
    AssetRef,
    Beat,
    Calculation,
    CategoryCoverage,
    ChartTemplate,
    Claim,
    ClaimOperand,
    CoverageSpan,
    Cue,
    DatasetColumn,
    DatasetRow,
    Entity,
    EvidenceDataset,
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    FieldEncoding,
    Quantity,
    ReviewedArtifact,
    ReviewerIdentity,
    ReviewReport,
    Scene,
    ScriptPlan,
    ScriptSegment,
    Section,
    SeriesBinding,
    TargetAction,
    Template,
    TextItem,
    TextTemplate,
    TimeInterval,
    TitlePromise,
    VisualSpec,
    tokenize,
)
from content_factory.schemas.research import EvidenceLocator

A, B = "src_source_a", "src_source_b"
IA, IB = "ev_item_a000", "ev_item_b000"
CA, CB, CD, CQ = "clm_claim_a0", "clm_claim_b0", "clm_claim_d0", "clm_claim_q0"
CALC, DS = "calc_add_ab00", "ds_dataset0"
S1, S2, S3 = "seg_segment1", "seg_segment2", "seg_segment3"
SC1, SC2, SC3 = "sc_scene_01", "sc_scene_02", "sc_scene_03"
R1, R2, R3 = "rev_review_1", "rev_review_2", "rev_review_3"
ENT_A, ENT_Q, ENT_SA, ENT_SB = "ent_text_a00", "ent_text_q00", "ent_series_a", "ent_series_b"
ASSET, TIMELINE = "ast_dataset0", "tl_timeline0"
WHEN = "2026-09-01T00:00:00Z"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def source(source_id: str, text: str, accessed_at: str = WHEN) -> EvidenceSource:
    return EvidenceSource(
        source_id=source_id,
        url=f"https://example.org/{source_id}",
        canonical_url=f"https://example.org/{source_id}",
        publisher=source_id,
        accessed_at=accessed_at,
        content_sha256=sha(text),
    )


def item(item_id: str, source_id: str, passage: str) -> EvidenceItem:
    return EvidenceItem(
        item_id=item_id,
        source_id=source_id,
        passage=passage,
        locator=EvidenceLocator(kind="char_range", start=0, end=len(passage)),
        quoted_at=WHEN,
    )


def claim(claim_id: str, statement: str, **fields: Any) -> Claim:
    return Claim(
        claim_id=claim_id,
        statement=statement,
        epistemic_class=fields.pop("epistemic_class", "observation"),
        rationale="fixture",
        checked_at=WHEN,
        **fields,
    )


def segment(
    segment_id: str, section: Section, text: str, claim_ids: tuple[str, ...]
) -> ScriptSegment:
    return ScriptSegment(
        segment_id=segment_id,
        section=section,
        spoken_text=text,
        tokens=tokenize(text),
        claim_ids=claim_ids,
    )


def scene(
    scene_id: str,
    section: Section,
    template: Template,
    beat: Beat,
    claim_ids: tuple[str, ...] = (),
) -> Scene:
    return Scene(
        scene_id=scene_id,
        section=section,
        purpose="fixture",
        template=template,
        beats=(beat,),
        claim_ids=claim_ids,
    )


def review(report_id: str, scene_id: str) -> ReviewReport:
    return ReviewReport(
        report_id=report_id,
        artifact=ReviewedArtifact(kind="mp4", sha256=sha("render")),
        timeline_id=TIMELINE,
        reviewer=ReviewerIdentity(
            model_id="reviewer",
            prompt_sha256=sha("prompt"),
            rubric_version="1",
            modalities=("video",),
        ),
        coverage=(
            CoverageSpan(
                scene_id=scene_id,
                interval=TimeInterval(start_ms=0, end_ms=1000),
                sampled_ms=(0, 500),
            ),
        ),
        categories=(CategoryCoverage(category="readability", covered_by="sampled frames"),),
        disposition="pass",
        authority="advisory",
        created_at=WHEN,
    )


def make_pack() -> EvidencePack:
    ms = Quantity(magnitude=80, unit="ms")
    return EvidencePack(
        pack_id="pack_pack_000",
        topic="Latency of two systems",
        sources=(source(A, "system a text"), source(B, "system b text")),
        items=(item(IA, A, "A takes 80 ms."), item(IB, B, "B takes 20 ms, preliminary.")),
        claims=(
            claim(CA, "A takes 80 ms", evidence_ids=(IA,), value=ms),
            claim(CB, "B takes 20 ms", evidence_ids=(IB,), value=Quantity(magnitude=20, unit="ms")),
            claim(
                CD,
                "Together they take 100 ms",
                epistemic_class="interpretation",
                value=Quantity(magnitude=100, unit="ms"),
                calculation_id=CALC,
            ),
            claim(CQ, "B calls its figure preliminary", evidence_ids=(IB,)),
        ),
        datasets=(
            EvidenceDataset(
                dataset_id=DS,
                columns=(
                    DatasetColumn(name="system", kind="nominal"),
                    DatasetColumn(name="latency", kind="quantitative", unit="ms"),
                ),
                rows=(
                    DatasetRow(key="a", values=("A", 80.0), claim_ids=(CA,)),
                    DatasetRow(key="b", values=("B", 20.0), claim_ids=(CB,)),
                ),
            ),
        ),
        calculations=(
            Calculation(
                calc_id=CALC,
                op="add",
                operands=(
                    ClaimOperand(kind="claim", claim_id=CA),
                    ClaimOperand(kind="claim", claim_id=CB),
                ),
                result_claim_id=CD,
            ),
        ),
    )


def make_script(pack: EvidencePack) -> ScriptPlan:
    return ScriptPlan(
        script_id="scr_script_0",
        channel_id="ch_channel0",
        pack_id=pack.pack_id,
        pack_hash=pack.pack_hash(),
        question="Which system is slower?",
        contribution="Puts both numbers on one axis.",
        promises=(
            TitlePromise(title="100 ms", thumbnail_promise="Two systems, one sum", claim_ids=(CD,)),
        ),
        segments=(
            segment(S1, "cold_open", "System A takes eighty milliseconds.", (CA,)),
            segment(
                S2, "build_model", "System B takes twenty, and calls it preliminary.", (CB, CQ)
            ),
            segment(S3, "synthesis", "Together that is one hundred milliseconds.", (CD,)),
        ),
    )


def make_spec(pack: EvidencePack, script: ScriptPlan) -> VisualSpec:
    reveal_a = Beat(
        beat_id="beat_b1_00000",
        cue=Cue(segment_id=S1, token_start=0, token_end=2),
        actions=(TargetAction(action="reveal", targets=(ENT_A,)),),
    )
    reveal_q = Beat(
        beat_id="beat_b2_00000",
        cue=Cue(segment_id=S2, token_start=0, token_end=2),
        actions=(TargetAction(action="reveal", targets=(ENT_Q,)),),
    )
    chart_beat = Beat(
        beat_id="beat_b3_00000",
        cue=Cue(segment_id=S3, token_start=0, token_end=2),
        actions=(
            TargetAction(action="reveal", targets=(ENT_SA, ENT_SB)),
            AnnotateAction(action="annotate", targets=(ENT_SA,), text="100 ms total", claim_id=CD),
        ),
    )
    chart = ChartTemplate(
        template="chart",
        chart_kind="bar",
        dataset_asset_id=ASSET,
        x=FieldEncoding(field="system", kind="nominal"),
        y=FieldEncoding(field="latency", kind="quantitative", unit="ms"),
        series=(
            SeriesBinding(value="A", entity_id=ENT_SA),
            SeriesBinding(value="B", entity_id=ENT_SB),
        ),
    )
    return VisualSpec(
        spec_id="spec_spec_000",
        script_id=script.script_id,
        script_hash=script.script_hash(),
        pack_hash=pack.pack_hash(),
        design_system_version=1,
        entities=(
            Entity(entity_id=ENT_A, label="80 ms", kind="text"),
            Entity(entity_id=ENT_Q, label="Preliminary", kind="text"),
            Entity(entity_id=ENT_SA, label="System A", kind="series", claim_ids=(CA,)),
            Entity(entity_id=ENT_SB, label="System B", kind="series", claim_ids=(CB,)),
        ),
        assets=(AssetRef(asset_id=ASSET, kind="dataset", sha256=sha("dataset"), dataset_id=DS),),
        scenes=(
            scene(
                SC1,
                "cold_open",
                TextTemplate(
                    template="text",
                    variant="big_number",
                    items=(TextItem(entity_id=ENT_A, text="80 ms"),),
                ),
                reveal_a,
                claim_ids=(CA,),
            ),
            scene(
                SC2,
                "build_model",
                TextTemplate(
                    template="text",
                    variant="statement",
                    items=(TextItem(entity_id=ENT_Q, text="Preliminary", claim_id=CQ),),
                ),
                reveal_q,
            ),
            scene(SC3, "synthesis", chart, chart_beat, claim_ids=(CD,)),
        ),
    )


def revalidated(pack: EvidencePack, **update: Any) -> EvidencePack:
    return EvidencePack.model_validate(pack.model_copy(update=update).model_dump())


@pytest.fixture
def pack() -> EvidencePack:
    return make_pack()


@pytest.fixture
def records(pack: EvidencePack) -> dict[str, Any]:
    script = make_script(pack)
    spec = make_spec(pack, script)
    return {
        "script": script,
        "spec": spec,
        "reviews": (review(R1, SC1), review(R2, SC2), review(R3, SC3)),
    }


A_CHAIN = frozenset(
    {
        node_id("evidence", IA),
        node_id("claim", CA),
        node_id("calc", CALC),
        node_id("claim", CD),
        node_id("dataset", DS),
        node_id("segment", S1),
        node_id("segment", S3),
        node_id("scene", SC1),
        node_id("scene", SC3),
        node_id("review", R1),
        node_id("review", R3),
    }
)
B_ONLY = frozenset(
    {node_id("scene", SC2), node_id("review", R2), node_id("claim", CB), node_id("claim", CQ)}
)


def test_content_change_in_source_a_invalidates_exactly_its_chain(
    pack: EvidencePack, records: dict[str, Any]
) -> None:
    changed = revalidated(pack, sources=(source(A, "system a text, revised"), pack.sources[1]))
    report = invalidation(pack, changed, **records)
    assert report.diff.changed == {A}
    assert report.invalidated == A_CHAIN
    assert report.invalidated.isdisjoint(B_ONLY)


def test_provenance_only_change_invalidates_nothing(
    pack: EvidencePack, records: dict[str, Any]
) -> None:
    later = revalidated(
        pack,
        sources=(source(A, "system a text", accessed_at="2026-09-14T00:00:00Z"), pack.sources[1]),
    )
    report = invalidation(pack, later, **records)
    assert report.diff.provenance_only == {A}
    assert report.diff.changed == frozenset()
    assert report.invalidated == frozenset()
    assert later.pack_hash() == pack.pack_hash()


def test_content_change_changes_pack_hash(pack: EvidencePack) -> None:
    changed = revalidated(pack, sources=(source(A, "system a text, revised"), pack.sources[1]))
    assert changed.pack_hash() != pack.pack_hash()
    assert changed.provenance_hash() != pack.provenance_hash()


def test_removed_source_invalidates_its_dependents(
    pack: EvidencePack, records: dict[str, Any]
) -> None:
    without_b = revalidated(
        pack,
        sources=pack.sources[:1],
        items=pack.items[:1],
        claims=pack.claims[:1],
        datasets=(),
        calculations=(),
    )
    report = invalidation(pack, without_b, **records)
    assert report.diff.removed == {B}
    assert report.invalidated == {
        node_id("evidence", IB),
        node_id("claim", CB),
        node_id("claim", CQ),
        node_id("calc", CALC),
        node_id("claim", CD),
        node_id("dataset", DS),
        node_id("segment", S2),
        node_id("segment", S3),
        node_id("scene", SC2),
        node_id("scene", SC3),
        node_id("review", R2),
        node_id("review", R3),
    }


def test_added_source_is_reported_and_invalidates_nothing(
    pack: EvidencePack, records: dict[str, Any]
) -> None:
    with_c = revalidated(pack, sources=(*pack.sources, source("src_source_c", "system c text")))
    report = invalidation(pack, with_c, **records)
    assert report.diff.added == {"src_source_c"}
    assert report.invalidated == frozenset()


def test_dependents_excludes_seeds_and_reaches_the_diamond_once(
    pack: EvidencePack, records: dict[str, Any]
) -> None:
    graph = build_graph(pack, **records)
    seeds = {node_id("claim", CA), node_id("claim", CB)}
    dependents = graph.dependents(seeds)
    assert dependents.isdisjoint(seeds)
    assert dependents == {
        node_id("calc", CALC),
        node_id("claim", CD),
        node_id("dataset", DS),
        node_id("segment", S1),
        node_id("segment", S2),
        node_id("segment", S3),
        node_id("scene", SC1),
        node_id("scene", SC2),
        node_id("scene", SC3),
        node_id("review", R1),
        node_id("review", R2),
        node_id("review", R3),
    }


def test_dependents_terminates_on_a_cycle() -> None:
    graph = DependencyGraph(
        nodes=frozenset({"claim:x", "calc:y"}),
        downstream={"claim:x": frozenset({"calc:y"}), "calc:y": frozenset({"claim:x"})},
    )
    assert graph.dependents(["claim:x"]) == {"calc:y"}


def test_graph_holds_direct_edges_only(pack: EvidencePack, records: dict[str, Any]) -> None:
    graph = build_graph(pack, **records)
    assert graph.downstream[node_id("source", A)] == {node_id("evidence", IA)}
    assert graph.downstream[node_id("claim", CA)] == {
        node_id("calc", CALC),
        node_id("dataset", DS),
        node_id("segment", S1),
        node_id("scene", SC1),
        node_id("scene", SC3),
    }
    assert graph.downstream[node_id("segment", S2)] == {node_id("scene", SC2)}
    assert node_id("review", R2) not in graph.downstream


def test_by_kind_groups_bare_record_ids(pack: EvidencePack, records: dict[str, Any]) -> None:
    changed = revalidated(pack, sources=(source(A, "system a text, revised"), pack.sources[1]))
    by_kind = invalidation(pack, changed, **records).by_kind()
    assert by_kind["scene"] == {SC1, SC3}
    assert by_kind["claim"] == {CA, CD}
    assert "source" not in by_kind


def test_diff_sources_reports_each_source_once(pack: EvidencePack) -> None:
    new = revalidated(
        pack, sources=(source(A, "revised", accessed_at="2026-09-14"), pack.sources[1])
    )
    diff = diff_sources(pack, new)
    assert diff.changed == {A}
    assert diff.provenance_only == frozenset()
