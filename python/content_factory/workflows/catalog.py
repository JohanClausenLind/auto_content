"""Load the workflow definitions in ``workflows/*.yaml``."""

from __future__ import annotations

import json
import warnings
from functools import lru_cache
from pathlib import Path

import yaml

from content_factory.schemas.dag import Stage
from content_factory.schemas.workflow_template import (
    NON_STAGE_TYPES,
    WorkflowTemplate,
)
from content_factory.schemas.workspace_graph import (
    WorkspaceGraph,
    WorkspaceGroup,
    WorkspaceLink,
    WorkspaceNode,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFINITIONS_DIR = REPO_ROOT / "workflows"
NODE_CATALOG_PATH = REPO_ROOT / "fixtures" / "schema" / "node_catalog.json"


class WorkflowDefinitionError(ValueError):
    """A definition file is wrong. The message always names the file."""


@lru_cache(maxsize=1)
def node_catalog() -> dict[str, dict]:
    """The generated node catalogue: widget and slot names per node type."""
    if not NODE_CATALOG_PATH.is_file():
        msg = (
            f"{NODE_CATALOG_PATH} is missing. Regenerate it with"
            " `node scripts/dump_node_catalog.mjs` (or `just schemas`)."
        )
        raise WorkflowDefinitionError(msg)
    return json.loads(NODE_CATALOG_PATH.read_text())["node_types"]


def check_against_catalog(template: WorkflowTemplate, *, where: str = "") -> None:
    """Every widget value and wire slot must exist on its node type."""
    catalog = node_catalog()
    prefix = f"{where}: " if where else ""
    problems: list[str] = []

    for node in template.nodes:
        spec = catalog.get(node.type)
        if spec is None:
            problems.append(f"node {node.key}: type {node.type!r} is not in the node catalogue")
            continue
        declared = set(spec["widgets"])
        options = spec.get("widget_options", {})
        for key in sorted(node.values):
            if key not in declared:
                problems.append(
                    f"node {node.key} ({node.type}) sets {key!r}, which it does not declare;"
                    f" it has {sorted(declared)}"
                )
                continue
            # A combo widget accepts only the values it lists.
            allowed = options.get(key)
            if allowed and str(node.values[key]) not in {str(a) for a in allowed}:
                problems.append(
                    f"node {node.key} ({node.type}) sets {key}={node.values[key]!r},"
                    f" which is not one of {allowed}"
                )

    for wire in template.wires:
        src = catalog.get(template.node(wire.from_key).type)
        dst = catalog.get(template.node(wire.to_key).type)
        if src and wire.from_slot not in src["outputs"]:
            problems.append(
                f"wire from {wire.from_key}.{wire.from_slot}: that node outputs {src['outputs']}"
            )
        if dst and wire.to_slot not in dst["inputs"]:
            problems.append(
                f"wire into {wire.to_key}.{wire.to_slot}: that node accepts {dst['inputs']}"
            )
        # Slot types, so a definition cannot describe a wire the canvas would refuse to draw. A
        # slot's type is a comma-separated list of what it accepts.
        if (
            src
            and dst
            and wire.from_slot in src["output_types"]
            and wire.to_slot in dst["input_types"]
        ):
            produced = src["output_types"][wire.from_slot].split(",")
            accepted = dst["input_types"][wire.to_slot].split(",")
            if not set(produced) & set(accepted):
                problems.append(
                    f"wire {wire.from_key}.{wire.from_slot} -> {wire.to_key}.{wire.to_slot}:"
                    f" produces {'/'.join(produced)} but that input accepts {'/'.join(accepted)}"
                )

    # Every required input of a runnable node must be fed.
    fed = {(w.to_key, w.to_slot) for w in template.wires}
    unfed: list[str] = []
    for node in template.nodes:
        spec = catalog.get(node.type)
        if spec is None or not node.runnable:
            continue
        for slot in spec["required_inputs"]:
            if (node.key, slot) not in fed:
                unfed.append(f"{node.key}.{slot}")
        # A node that takes one thing in two shapes (a picture is a frame sequence or a clip)
        # declares the alternatives instead of marking both required.
        for group in spec.get("requires_one_of", ()):
            if not any((node.key, slot) in fed for slot in group):
                unfed.append(f"{node.key}.{'|'.join(group)}")
    if unfed:
        problems.append(
            "these required inputs are wired to nothing: "
            + ", ".join(sorted(unfed))
            + ". Every lane's entry point is a node — input.audio, input.image or input.video for"
            " material the operator supplies — so a stage that reads the run directory still says"
            " where its input comes from."
        )

    if problems:
        raise WorkflowDefinitionError(prefix + "; ".join(problems))


def load_definition_file(path: Path) -> WorkflowTemplate:
    """Parse and fully validate one definition file."""
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        msg = f"{path.name}: not valid YAML: {exc}"
        raise WorkflowDefinitionError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path.name}: the top level must be a mapping"
        raise WorkflowDefinitionError(msg)
    try:
        template = WorkflowTemplate.model_validate(raw)
    except ValueError as exc:
        msg = f"{path.name}: {exc}"
        raise WorkflowDefinitionError(msg) from exc
    if template.id != path.stem:
        msg = f"{path.name}: id is {template.id!r}, so the file should be {template.id}.yaml"
        raise WorkflowDefinitionError(msg)
    check_against_catalog(template, where=path.name)
    return template


@lru_cache(maxsize=1)
def load_definitions() -> dict[str, WorkflowTemplate]:
    """Every definition, keyed by id, in sorted order so iteration is deterministic."""
    if not DEFINITIONS_DIR.is_dir():
        msg = f"{DEFINITIONS_DIR} does not exist"
        raise WorkflowDefinitionError(msg)
    out: dict[str, WorkflowTemplate] = {}
    for path in sorted(DEFINITIONS_DIR.glob("*.yaml")):
        template = load_definition_file(path)
        out[template.id] = template
    if not out:
        msg = f"no workflow definitions in {DEFINITIONS_DIR}"
        raise WorkflowDefinitionError(msg)
    return out


def load_definition(workflow_id: str) -> WorkflowTemplate:
    """One lane, fully validated - and only that lane has to be right for it to run."""
    try:
        definitions = load_definitions()
    except WorkflowDefinitionError as exc:
        path = DEFINITIONS_DIR / f"{workflow_id}.yaml"
        if not path.is_file():
            raise
        warnings.warn(
            f"the workflow catalogue has a problem elsewhere: {exc}",
            stacklevel=2,
        )
        return load_definition_file(path)
    if workflow_id not in definitions:
        msg = f"unknown workflow {workflow_id!r}; known: {sorted(definitions)}"
        raise WorkflowDefinitionError(msg)
    return definitions[workflow_id]


def workflow_ids() -> tuple[str, ...]:
    return tuple(load_definitions())


def stage_order(workflow_id: str) -> tuple[tuple[str, Stage, dict], ...]:
    return load_definition(workflow_id).stage_order()


def params_by_node(workflow_id: str) -> dict[str, dict]:
    """Widget values keyed by node key, which is how the runner freezes them onto a stage."""
    return {key: values for key, _stage, values in stage_order(workflow_id) if values}


def runnable_missing_executor(template: WorkflowTemplate) -> tuple[str, ...]:
    """Stages in this definition that have no executor, so cannot run yet."""
    from content_factory.workflows.stages import STAGE_EXECUTORS

    return tuple(
        node.type
        for node in template.nodes
        if node.type not in NON_STAGE_TYPES and Stage(node.type) not in STAGE_EXECUTORS
    )


def to_workspace_graph(template: WorkflowTemplate) -> WorkspaceGraph:
    """A definition as the canvas document, so it can be compiled offline the way a Run would."""
    depth = _layout_depth(template)
    nodes = tuple(
        WorkspaceNode(
            id=_node_id(template.id, node.key),
            type=node.type,
            x=node.x if node.x is not None else -1280.0 + 340.0 * depth[node.key],
            y=(
                node.y
                if node.y is not None
                else 120.0 * _row_within_depth(template, node.key, depth)
            ),
            values=dict(node.values),
            note=node.note,
            title=node.title,
        )
        for node in template.nodes
    )
    links = tuple(
        WorkspaceLink(
            id=f"ed_{w.from_key}_{w.from_slot}_{w.to_key}_{w.to_slot}"[:64],
            from_node=_node_id(template.id, w.from_key),
            from_slot=w.from_slot,
            to_node=_node_id(template.id, w.to_key),
            to_slot=w.to_slot,
        )
        for w in template.wires
    )
    by_id = {n.id: n for n in nodes}
    groups = tuple(
        WorkspaceGroup(
            id=f"gr_{template.id}_{g.key}"[:64],
            name=g.name,
            template=g.key,
            collapsed=g.collapsed,
            members=tuple(_node_id(template.id, key) for key in g.members),
            # The folded node sits at the corner of what it folds, so a lane opens with its
            # groups where its nodes were rather than stacked at the origin.
            x=min(by_id[_node_id(template.id, k)].x for k in g.members),
            y=min(by_id[_node_id(template.id, k)].y for k in g.members),
        )
        for g in template.groups
    )
    return WorkspaceGraph(
        graph_id=template.id, name=template.name, nodes=nodes, links=links, groups=groups
    )


def _node_id(workflow_id: str, key: str) -> str:
    """Stable, and truncated to the contract's own 64-character limit rather than to 40."""
    return f"nd_{workflow_id}_{key}"[:64]


def _layout_depth(template: WorkflowTemplate) -> dict[str, int]:
    """Longest-path depth per node, so the auto-layout puts producers left of consumers."""
    incoming: dict[str, list[str]] = {n.key: [] for n in template.nodes}
    for w in template.wires:
        incoming[w.to_key].append(w.from_key)
    depth: dict[str, int] = {}

    def resolve(key: str, seen: frozenset[str] = frozenset()) -> int:
        if key in depth:
            return depth[key]
        if key in seen:  # the contract rejects cycles; this only guards a partial graph
            return 0
        parents = incoming[key]
        value = 0 if not parents else 1 + max(resolve(p, seen | {key}) for p in parents)
        depth[key] = value
        return value

    for node in template.nodes:
        resolve(node.key)
    return depth


def _row_within_depth(template: WorkflowTemplate, key: str, depth: dict[str, int]) -> int:
    same = [n.key for n in template.nodes if depth[n.key] == depth[key]]
    return same.index(key)
