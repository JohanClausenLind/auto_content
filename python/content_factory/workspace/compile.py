"""Compile a hand-drawn WorkspaceGraph into the same DeliverableDAG + ContentCampaign the
campaign compiler produces — one execution engine, two front doors.

Every node gets a typed disposition (executes / skipped-with-reason / blocks-the-run), so the
UI can show exactly what a Run will and will not do. The rules:

- ``input.brief`` becomes the campaign brief (topic/audience/quality); never a DAG node.
- ``utility.note`` and muted/bypassed nodes are annotations: skipped, reported.
- ``publish.social`` marks a distribution intent: publishing stays behind its own gated flow.
- ``output.deliverables`` marks where the files land; the run writes there regardless.
- ``input.audio`` / ``input.image`` / ``input.video`` are files the operator dropped on the
  canvas. They carry an artifact key, not a path, and become the campaign's ``staged_uploads``:
  the run's first activity writes them into ``<project>/uploads/`` where the ``ingest`` stage
  reads them. A key that does not belong to this workspace, or is not in the store any more,
  blocks the run instead of failing later inside an activity.
- A pipeline stage with a registered executor becomes a StageNode with the same resource class
  and executor defaults the campaign compiler assigns.
- A stage without an executor (article/newsletter/sequence branches today) *blocks* the run and
  says so, rather than silently pretending it would produce something.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from content_factory.deliverables.dag_compiler import COMPILER_VERSION, stage_defaults
from content_factory.schemas.content import (
    AudioClipSpec,
    CarouselSpec,
    ContentCampaign,
    DeliverableBase,
    ShortVideoSpec,
    SingleImagePostSpec,
    StagedUpload,
    TextPostSpec,
)
from content_factory.schemas.dag import DeliverableDAG, Stage, StageNode
from content_factory.schemas.workspace_graph import WorkspaceGraph, WorkspaceNode

# Stages that inspect or package what another stage made. None of them can be a lane's first
# step, so a graph where one has nothing upstream of it would schedule it before the thing it is
# supposed to read — the canvas refuses that (a required input with no link is an error there),
# and this is the same refusal on the server, where the CLI and the MCP tool arrive.
DOWNSTREAM_ONLY_STAGES: frozenset[Stage] = frozenset(
    {
        Stage.qc_deliverable,
        Stage.originality_gate,
        Stage.compile_destination_packages,
        Stage.package_qc,
    }
)

SHARED_STAGES: frozenset[Stage] = frozenset(
    {
        Stage.ingest,
        Stage.research,
        Stage.verify_claims,
        Stage.compile_datasets,
        Stage.plan_story,
        Stage.originality_topic,
        Stage.preflight,
    }
)

# Sink stage -> inferred deliverable type, checked in order of specificity.
_SINK_DELIVERABLE: tuple[tuple[Stage, type[DeliverableBase]], ...] = (
    (Stage.compose_video, ShortVideoSpec),
    (Stage.generate_video, ShortVideoSpec),
    (Stage.render_scenes, ShortVideoSpec),
    (Stage.compile_cards, CarouselSpec),
    (Stage.render_cards, CarouselSpec),
    (Stage.mix_audio, AudioClipSpec),
    (Stage.render_static, SingleImagePostSpec),
    (Stage.compile_text_package, TextPostSpec),
)

# The dropped-file nodes, and the kind each one stands for. The kind is checked against the
# artifact key rather than trusted: the key records what the sniff decided when the file arrived.
SOURCE_NODE_KINDS: Mapping[str, str] = {
    "input.audio": "audio",
    "input.image": "image",
    "input.video": "video",
}

DispositionKind = Literal["executes", "skipped", "blocks"]


@dataclass(frozen=True)
class NodeDisposition:
    node_id: str
    type: str
    kind: DispositionKind
    reason: str
    dag_node_id: str | None = None


@dataclass(frozen=True)
class GraphCompilation:
    ok: bool
    dispositions: list[NodeDisposition]
    campaign: ContentCampaign | None = None
    dag: DeliverableDAG | None = None
    deliverable_type: str | None = None
    problems: list[str] = field(default_factory=list)


def _sanitize(node_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", node_id)[:32] or "n"


def _brief_values(nodes: list[WorkspaceNode]) -> tuple[dict[str, str], list[str]]:
    briefs = [n for n in nodes if n.type == "input.brief" and n.mode == "always"]
    problems: list[str] = []
    if not briefs:
        problems.append("the graph needs a Campaign Brief node (input.brief)")
        return {}, problems
    if len(briefs) > 1:
        problems.append("the graph has more than one Campaign Brief node")
    values = {k: str(v) for k, v in briefs[0].values.items()}
    if not values.get("topic", "").strip():
        problems.append("the Campaign Brief topic is empty")
    return values, problems


def campaign_with_brief(template: ContentCampaign, values: Mapping[str, object]) -> ContentCampaign:
    """``template`` with its brief replaced by an ``input.brief`` node's widget values.

    One helper for both front doors. The canvas path calls it from :func:`compile_graph`; the local
    runner calls it with the lane definition's own ``input.brief`` values so that a lane run gets
    the lane's subject rather than the demo fixture's. Before this, ``runners.local.make_context``
    handed every run ``sample_campaign()`` unchanged, so a film about two people on a plaza was
    generated under "How much of Sweden's electricity came from wind in 2025?" (STATUS 1370, 1678).

    ``topic`` is required and must be non-empty: it is the one field of a brief that no default can
    stand in for. The error names it.
    """
    topic = str(values.get("topic", "")).strip()
    if not topic:
        msg = "the Campaign Brief topic is empty: set input.brief.topic (or pass --subject)"
        raise ValueError(msg)
    audience = str(values.get("audience", "")).strip()
    return template.model_copy(
        update={
            "brief": template.brief.model_copy(
                update={
                    "topic": topic[:500],
                    "objective": (audience or topic)[:1000] or "-",
                    "audience": audience[:500],
                }
            )
        }
    )


def _staged_upload(node: WorkspaceNode, workspace_id: str) -> tuple[StagedUpload | None, str]:
    """One dropped-file node as a staged upload, or the reason it cannot be one.

    Everything except the display name comes out of the content-addressed artifact key
    (``<workspace>/originals-<kind>/<ab>/<sha256>.<ext>``), so the node cannot claim a kind or a
    hash the stored bytes do not have — the only fields a browser controls are which key to name
    and what to call it.
    """
    key = str(node.values.get("asset", "")).strip()
    name = str(node.values.get("filename", "")).strip() or "dropped"
    expected_kind = SOURCE_NODE_KINDS.get(node.type, "")
    if not key:
        return None, "no file dropped on it yet"
    parts = key.split("/")
    if len(parts) != 4 or parts[0] != workspace_id:
        return None, "the file reference does not belong to this workspace"
    kind = parts[1].removeprefix("originals-")
    sha = parts[3].split(".")[0]
    if kind != expected_kind:
        return None, f"the reference is a {kind} file, not {expected_kind}"
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        return None, "the file reference is malformed"
    size = node.values.get("bytes", 0)
    try:
        size_bytes = max(1, int(size))
    except (TypeError, ValueError):
        size_bytes = 1
    return (
        StagedUpload(
            asset_id=key,
            filename=name,
            kind=kind,  # type: ignore[arg-type]
            size_bytes=size_bytes,
            sha256=sha,
        ),
        "",
    )


def compile_graph(graph: WorkspaceGraph, template: ContentCampaign) -> GraphCompilation:
    """Pure: same graph + same campaign template → same compilation. ``template`` supplies the
    structural campaign fields (workspace, destinations) that a graph does not carry."""
    from content_factory.workflows.stages import STAGE_EXECUTORS

    defaults = stage_defaults()
    stage_values = {s.value for s in Stage}
    nodes = list(graph.nodes)
    dispositions: list[NodeDisposition] = []
    problems: list[str] = []

    brief, brief_problems = _brief_values(nodes)
    problems.extend(brief_problems)

    included: dict[str, tuple[WorkspaceNode, Stage]] = {}
    staged: list[StagedUpload] = []
    for node in nodes:
        if node.type in SOURCE_NODE_KINDS:
            upload, why = _staged_upload(node, template.workspace_id)
            if upload is None:
                kind, reason = "blocks", why
                problems.append(f"{node.title or node.type}: {why}")
            else:
                if all(u.asset_id != upload.asset_id for u in staged):
                    staged.append(upload)
                kind, reason = (
                    "skipped",
                    f"staged into the run's uploads folder as {upload.filename}",
                )
            dispositions.append(NodeDisposition(node.id, node.type, kind, reason))
            continue
        if node.type == "input.brief":
            kind, reason = "skipped", "becomes the campaign brief"
        elif node.type == "utility.note":
            kind, reason = "skipped", "note: canvas annotation only"
        elif node.type == "publish.social":
            kind, reason = "skipped", "publishing runs through the gated distribution flow"
        elif node.type == "output.deliverables":
            kind, reason = "skipped", "marks where the run already writes its deliverable files"
        elif node.mode != "always":
            kind, reason = "skipped", f"node is {node.mode}"
        elif node.type in stage_values:
            stage = Stage(node.type)
            if stage in STAGE_EXECUTORS:
                included[node.id] = (node, stage)
                kind, reason = "executes", ""
            else:
                kind, reason = "blocks", f"stage {stage.value} has no executor yet"
                problems.append(f"{node.title or stage.value}: no executor for this stage yet")
        else:
            kind, reason = "blocks", f"unknown node type {node.type}"
            problems.append(f"unknown node type {node.type}")
        dispositions.append(NodeDisposition(node.id, node.type, kind, reason))

    if not included:
        problems.append("the graph has no runnable pipeline stages")

    if problems:
        return GraphCompilation(ok=False, dispositions=dispositions, problems=problems)

    # One synthetic deliverable, its type inferred from the most final stage present.
    stages_present = {stage for _, stage in included.values()}
    spec_cls: type[DeliverableBase] = ShortVideoSpec
    for sink, cls in _SINK_DELIVERABLE:
        if sink in stages_present:
            spec_cls = cls
            break
    deliverable_id = f"dlv_{uuid.uuid5(uuid.NAMESPACE_URL, graph.graph_id).hex[:16]}"
    template_destinations = template.deliverables[0].destinations if template.deliverables else ()
    deliverable = spec_cls(
        deliverable_id=deliverable_id,
        title=graph.name[:200],
        intent=f"workspace graph {graph.graph_id}",
        destinations=template_destinations,
    )

    draft = campaign_with_brief(template, brief).model_copy(
        update={
            "campaign_id": f"cmp_{uuid.uuid5(uuid.NAMESPACE_URL, graph.graph_id).hex[:16]}",
            "deliverables": (deliverable,),
            # Template relationships reference the template's own deliverables; none survive.
            "relationships": (),
            # Sorted so the same graph always compiles to the same campaign: the run's input
            # hash covers this, and a set's iteration order would break cache reuse.
            "staged_uploads": tuple(sorted(staged, key=lambda u: u.asset_id)),
        }
    )
    # model_copy skips validators; re-validating here keeps an inconsistent campaign out of the
    # workers (it would otherwise only surface inside the first activity).
    campaign = ContentCampaign.model_validate(draft.model_dump(mode="json"))

    # DAG node ids: bare stage for shared singletons, stage:<suffix> otherwise.
    stage_counts: dict[Stage, int] = {}
    for _, stage in included.values():
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    dag_ids: dict[str, str] = {}
    for node_id, (graph_node, stage) in included.items():
        if stage in SHARED_STAGES and stage_counts[stage] == 1:
            dag_ids[node_id] = stage.value
        else:
            dag_ids[node_id] = f"{stage.value}:{_sanitize(graph_node.id)}"

    deps: dict[str, list[str]] = {node_id: [] for node_id in included}
    for link in graph.links:
        if link.from_node in included and link.to_node in included:
            deps[link.to_node].append(link.from_node)

    # The workflow runs every shared node before any per-deliverable node, so a shared stage
    # depending on a per-deliverable one can never be satisfied at run time (the dependency's
    # output would be missing). Refuse it here with the node named, instead of a KeyError
    # inside the workflow after the operator approved preflight.
    node_by_id = {n.id: n for n in nodes}
    for node_id, (graph_node, stage) in included.items():
        if stage in SHARED_STAGES:
            for dep in deps[node_id]:
                dep_stage = included[dep][1]
                if dep_stage not in SHARED_STAGES:
                    problems.append(
                        f"{graph_node.title or stage.value}: a shared stage cannot depend on "
                        f"per-deliverable stage {dep_stage.value}"
                    )
    # An inspector or packager with nothing upstream: it would run before the deliverable it is
    # meant to read exists, and its report would describe an empty folder. Dependencies come only
    # from links (above), so an unwired delivery node is not "ordered last" — it is unordered.
    for node_id, (graph_node, stage) in included.items():
        if stage in DOWNSTREAM_ONLY_STAGES and not deps[node_id]:
            problems.append(
                f"{graph_node.title or stage.value}: nothing is wired into it, so it would run"
                " before the deliverable it reads exists. Connect what this lane finishes with"
                " (the cut, the frames, the image or the master)."
            )

    # A link out of a muted/bypassed stage into a running one means the target's input is
    # never produced; dropping the link silently would fail the run only after approval.
    for link in graph.links:
        if link.to_node in included and link.from_node not in included:
            src = node_by_id.get(link.from_node)
            if src is not None and src.type in stage_values and src.mode != "always":
                target_node, target_stage = included[link.to_node]
                problems.append(
                    f"{target_node.title or target_stage.value}: depends on "
                    f"{src.title or src.type}, which is {src.mode} and will not run"
                )
    if problems:
        return GraphCompilation(ok=False, dispositions=dispositions, problems=problems)

    stage_nodes: list[StageNode] = []
    for node_id, (graph_node, stage) in included.items():
        rc, executor = defaults[stage]
        stage_nodes.append(
            StageNode(
                node_id=dag_ids[node_id],
                stage=stage,
                deliverable_id=None if stage in SHARED_STAGES else deliverable_id,
                depends_on=tuple(sorted(dag_ids[d] for d in deps[node_id])),
                executor=executor,
                resource_class=rc,
                # What the operator typed into the node travels with it: the stage reads the keys
                # it knows, and the values are part of the node's input hash, so a changed widget
                # re-runs that stage and everything downstream of it.
                params={k: str(v) for k, v in sorted(graph_node.values.items())},
            )
        )

    # The approval gate is an invariant of every run: the workflow parks on the preflight
    # revision. A graph without a Preflight Gate node gets one injected (after every other
    # shared stage) rather than a run that skips human review.
    if Stage.preflight not in stages_present:
        shared_ids = tuple(sorted(n.node_id for n in stage_nodes if n.deliverable_id is None))
        rc, executor = defaults[Stage.preflight]
        stage_nodes.append(
            StageNode(
                node_id=Stage.preflight.value,
                stage=Stage.preflight,
                deliverable_id=None,
                depends_on=shared_ids,
                executor=executor,
                resource_class=rc,
            )
        )
        dispositions.append(
            NodeDisposition(
                node_id="__preflight__",
                type=Stage.preflight.value,
                kind="executes",
                reason="added automatically: every run parks for operator approval",
                dag_node_id=Stage.preflight.value,
            )
        )
    stage_nodes.sort(key=lambda n: n.node_id)

    dag = DeliverableDAG(
        campaign_id=campaign.campaign_id,
        nodes=tuple(stage_nodes),
        compiler_version=f"{COMPILER_VERSION}+workspace-graph",
    )

    final = [
        d
        if d.node_id not in included
        else NodeDisposition(d.node_id, d.type, d.kind, d.reason, dag_ids.get(d.node_id))
        for d in dispositions
    ]
    return GraphCompilation(
        ok=True,
        dispositions=final,
        campaign=campaign,
        dag=dag,
        deliverable_type=getattr(deliverable, "type", spec_cls.__name__),
    )
