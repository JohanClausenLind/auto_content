"""The explicit dependency graph over explainer records, and what a source change invalidates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from content_factory.schemas.explainer import (
    AnnotateAction,
    ChartTemplate,
    ClaimOperand,
    CompareAction,
    DiagramTemplate,
    EvidencePack,
    EvidenceSource,
    ReviewReport,
    Scene,
    ScriptPlan,
    SourceCaptureManifest,
    SourceDocumentTemplate,
    TargetAction,
    TextTemplate,
    VisualSpec,
    hash_without_provenance,
)

NodeKind = Literal[
    "source", "capture", "evidence", "claim", "calc", "dataset", "segment", "scene", "review"
]


def node_id(kind: NodeKind, record_id: str) -> str:
    return f"{kind}:{record_id}"


def split_node(node: str) -> tuple[str, str]:
    """A node id back into (kind, record id)."""
    kind, _, record_id = node.partition(":")
    return kind, record_id


def group_by_kind(nodes: Iterable[str]) -> dict[str, frozenset[str]]:
    """Node ids grouped by kind; the values are bare record ids, the key carries the kind."""
    groups: defaultdict[str, set[str]] = defaultdict(set)
    for node in nodes:
        kind, record_id = split_node(node)
        groups[kind].add(record_id)
    return {kind: frozenset(ids) for kind, ids in groups.items()}


@dataclass(frozen=True)
class DependencyGraph:
    """Declared records as nodes and direct upstream-to-downstream edges between them."""

    nodes: frozenset[str]
    downstream: Mapping[str, frozenset[str]]

    def dependents(self, node_ids: Iterable[str]) -> frozenset[str]:
        """Everything transitively downstream of the seeds, the seeds themselves excluded."""
        seeds = set(node_ids)
        reached: set[str] = set()
        frontier = list(seeds)
        while frontier:
            for child in self.downstream.get(frontier.pop(), ()):
                if child not in reached:
                    reached.add(child)
                    frontier.append(child)
        return frozenset(reached - seeds)

    @staticmethod
    def by_kind(node_ids: Iterable[str]) -> dict[str, frozenset[str]]:
        return group_by_kind(node_ids)


class _Edges:
    """Collects edges; a reference to an undeclared record is dropped, not an error."""

    def __init__(self, nodes: frozenset[str]) -> None:
        self.nodes = nodes
        self.downstream: defaultdict[str, set[str]] = defaultdict(set)

    def link(self, upstream: str, downstream: str) -> None:
        if upstream in self.nodes and downstream in self.nodes:
            self.downstream[upstream].add(downstream)

    def graph(self) -> DependencyGraph:
        return DependencyGraph(self.nodes, {k: frozenset(v) for k, v in self.downstream.items()})


def build_graph(
    pack: EvidencePack,
    script: ScriptPlan | None = None,
    spec: VisualSpec | None = None,
    manifests: Sequence[SourceCaptureManifest] = (),
    reviews: Sequence[ReviewReport] = (),
) -> DependencyGraph:
    edges = _Edges(_declared_nodes(pack, script, spec, manifests, reviews))
    _link_pack(edges, pack)
    for manifest in manifests:
        edges.link(node_id("source", manifest.source_id), node_id("capture", manifest.capture_id))
    if script is not None:
        for segment in script.segments:
            for claim_id in segment.claim_ids:
                edges.link(node_id("claim", claim_id), node_id("segment", segment.segment_id))
    if spec is not None:
        _link_scenes(edges, spec)
    for review in reviews:
        for span in review.coverage:
            edges.link(node_id("scene", span.scene_id), node_id("review", review.report_id))
    return edges.graph()


def _declared_nodes(
    pack: EvidencePack,
    script: ScriptPlan | None,
    spec: VisualSpec | None,
    manifests: Sequence[SourceCaptureManifest],
    reviews: Sequence[ReviewReport],
) -> frozenset[str]:
    nodes = {node_id("source", s.source_id) for s in pack.sources}
    nodes.update(node_id("capture", s.capture_id) for s in pack.sources if s.capture_id)
    nodes.update(node_id("capture", m.capture_id) for m in manifests)
    nodes.update(node_id("evidence", i.item_id) for i in pack.items)
    nodes.update(node_id("claim", c.claim_id) for c in pack.claims)
    nodes.update(node_id("calc", c.calc_id) for c in pack.calculations)
    nodes.update(node_id("dataset", d.dataset_id) for d in pack.datasets)
    nodes.update(node_id("segment", s.segment_id) for s in (script.segments if script else ()))
    nodes.update(node_id("scene", s.scene_id) for s in (spec.scenes if spec else ()))
    nodes.update(node_id("review", r.report_id) for r in reviews)
    return frozenset(nodes)


def _link_pack(edges: _Edges, pack: EvidencePack) -> None:
    for source in pack.sources:
        if source.capture_id is not None:
            edges.link(node_id("source", source.source_id), node_id("capture", source.capture_id))
    for item in pack.items:
        edges.link(node_id("source", item.source_id), node_id("evidence", item.item_id))
    for claim in pack.claims:
        for evidence_id in claim.evidence_ids:
            edges.link(node_id("evidence", evidence_id), node_id("claim", claim.claim_id))
    for calc in pack.calculations:
        for operand in calc.operands:
            if isinstance(operand, ClaimOperand):
                edges.link(node_id("claim", operand.claim_id), node_id("calc", calc.calc_id))
        edges.link(node_id("calc", calc.calc_id), node_id("claim", calc.result_claim_id))
    for dataset in pack.datasets:
        for row in dataset.rows:
            for claim_id in row.claim_ids:
                edges.link(node_id("claim", claim_id), node_id("dataset", dataset.dataset_id))


def _link_scenes(edges: _Edges, spec: VisualSpec) -> None:
    claims_of = {e.entity_id: e.claim_ids for e in spec.entities}
    assets = {a.asset_id: a for a in spec.assets}
    for scene in spec.scenes:
        target = node_id("scene", scene.scene_id)
        claim_ids = set(scene.claim_ids)
        for entity_id in _scene_entities(scene):
            claim_ids.update(claims_of.get(entity_id, ()))
        template = scene.template
        if isinstance(template, TextTemplate):
            claim_ids.update(i.claim_id for i in template.items if i.claim_id is not None)
        for beat in scene.beats:
            edges.link(node_id("segment", beat.cue.segment_id), target)
            for action in beat.actions:
                if isinstance(action, AnnotateAction) and action.claim_id is not None:
                    claim_ids.add(action.claim_id)
        for claim_id in claim_ids:
            edges.link(node_id("claim", claim_id), target)
        for source_id in scene.source_ids:
            edges.link(node_id("source", source_id), target)
        if isinstance(template, ChartTemplate):
            asset = assets.get(template.dataset_asset_id)
            if asset is not None and asset.dataset_id is not None:
                edges.link(node_id("dataset", asset.dataset_id), target)
        if isinstance(template, SourceDocumentTemplate):
            asset = assets.get(template.capture_asset_id)
            if asset is not None and asset.capture_id is not None:
                edges.link(node_id("capture", asset.capture_id), target)


def _scene_entities(scene: Scene) -> set[str]:
    """Entities the scene shows: initially visible, bound by the template, or targeted by a beat."""
    template = scene.template
    ids = set(scene.initial_visible)
    if isinstance(template, ChartTemplate):
        ids.update(s.entity_id for s in template.series)
    elif isinstance(template, DiagramTemplate):
        ids.update(n.entity_id for n in template.nodes)
        ids.update(e.entity_id for e in template.edges)
    elif isinstance(template, TextTemplate):
        ids.update(i.entity_id for i in template.items)
    for beat in scene.beats:
        for action in beat.actions:
            if isinstance(action, TargetAction | CompareAction | AnnotateAction):
                ids.update(action.targets)
    return ids


@dataclass(frozen=True)
class SourceDiff:
    """Source ids by what changed between two packs; provenance_only means only timestamps moved."""

    changed: frozenset[str]
    provenance_only: frozenset[str]
    added: frozenset[str]
    removed: frozenset[str]


def diff_sources(old: EvidencePack, new: EvidencePack) -> SourceDiff:
    before = {s.source_id: s for s in old.sources}
    after = {s.source_id: s for s in new.sources}
    changed: set[str] = set()
    provenance_only: set[str] = set()
    for source_id in before.keys() & after.keys():
        was, now = before[source_id], after[source_id]
        if _source_content_hash(was) != _source_content_hash(now):
            changed.add(source_id)
        elif was != now:
            provenance_only.add(source_id)
    return SourceDiff(
        changed=frozenset(changed),
        provenance_only=frozenset(provenance_only),
        added=frozenset(after.keys() - before.keys()),
        removed=frozenset(before.keys() - after.keys()),
    )


def _source_content_hash(source: EvidenceSource) -> str:
    return hash_without_provenance(source.model_dump(mode="json"))


@dataclass(frozen=True)
class InvalidationReport:
    """The source diff and every node downstream of a changed or removed source."""

    diff: SourceDiff
    invalidated: frozenset[str]

    def by_kind(self) -> dict[str, frozenset[str]]:
        return group_by_kind(self.invalidated)


def invalidation(
    old_pack: EvidencePack,
    new_pack: EvidencePack,
    script: ScriptPlan | None = None,
    spec: VisualSpec | None = None,
    manifests: Sequence[SourceCaptureManifest] = (),
    reviews: Sequence[ReviewReport] = (),
) -> InvalidationReport:
    """Dependents in the OLD graph of every source whose content changed or that disappeared."""
    diff = diff_sources(old_pack, new_pack)
    graph = build_graph(old_pack, script, spec, manifests, reviews)
    seeds = [node_id("source", source_id) for source_id in diff.changed | diff.removed]
    return InvalidationReport(diff=diff, invalidated=graph.dependents(seeds))
