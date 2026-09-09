"""One workflow, as data.

A workflow used to live in two hand-written places: the web canvas's template array and the local
runner's stage table. They drifted, as duplicated definitions do. The canvas had nine templates and
the runner five, only three ids appeared in both, the same film was called two different things, and
the runner carried human review gates the canvas had no nodes for, so a canvas run of that film
silently shipped without them.

This contract is the single definition. It carries what both front doors need: the node graph and
its wires for the canvas, the per-node widget values, the model requirements, and the linear
``order`` the runner executes. A validator asserts ``order`` is a topological order of ``wires``,
which is what makes one file able to serve both honestly rather than approximately.

Order and values key on **node key**, never on stage. A workflow may legitimately use the same
stage twice - two ``generate_anchor`` nodes with different prompts, say - and a stage-keyed table
silently collapses them, which is a bug the old runner had.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from content_factory.schemas.base import SchemaModel, VersionedModel
from content_factory.schemas.dag import Stage

WorkflowId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{2,48}$")]
NodeKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
SlotName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
WidgetValue = str | int | float | bool

# The file inputs: a recording, a still or a clip the operator supplies. On the canvas they are
# what a dropped file becomes; in a lane definition they are the declared entry point — the node
# that says "your material goes here" — which is what lets a lane whose first stage reads the run
# directory still show a connected graph instead of an unexplained empty slot.
SOURCE_TYPES: frozenset[str] = frozenset({"input.audio", "input.image", "input.video"})

# Node types the canvas accepts that are not pipeline stages: the campaign brief that starts a
# graph, the file inputs, a free-text sticky note, the publish terminal, and the deliverables
# terminal that marks where a lane's files land.
NON_STAGE_TYPES: frozenset[str] = frozenset(
    {"input.brief", "utility.note", "publish.social", "output.deliverables"} | SOURCE_TYPES
)

# Types that carry no work and are skipped by the runner. A note is decoration; a brief is the
# input the run is started with; a file input is staged into the run's uploads folder before the
# first stage rather than executed; the deliverables terminal names a folder the run writes to
# anyway, which is why it is a marker and not a stage.
NON_RUNNABLE_TYPES: frozenset[str] = frozenset(
    {"input.brief", "utility.note", "output.deliverables"} | SOURCE_TYPES
)

WorkflowCategory = Literal["image", "video", "audio", "article", "email", "social", "utility"]


class ModelRequirement(SchemaModel):
    """A weight or skill environment a workflow needs before it can run.

    ``kind="comfy"`` resolves inside ComfyUI's model folders, ``kind="path"`` is a directory in the
    weight store, ``kind="skill"`` is a skill env under ``skills/``. The web panel turns these into
    a readiness column and a download command.
    """

    kind: Literal["comfy", "path", "skill"]
    label: str = Field(min_length=1, max_length=120)
    folder: str = Field(default="", max_length=64)
    """ComfyUI model folder, for ``kind="comfy"``."""
    filename: str = Field(default="", max_length=200)
    path_includes: str = Field(default="", max_length=200)
    """Substring that identifies the weight directory, for ``kind="path"``."""
    skill: str = Field(default="", max_length=120)
    source_url: str = Field(default="", max_length=500)
    optional: bool = False

    @model_validator(mode="after")
    def _kind_has_its_fields(self) -> ModelRequirement:
        if self.kind == "comfy" and not (self.folder and self.filename):
            msg = "a comfy requirement needs both folder and filename"
            raise ValueError(msg)
        if self.kind == "path" and not (self.path_includes or self.filename):
            msg = "a path requirement needs path_includes or filename"
            raise ValueError(msg)
        if self.kind == "skill" and not self.skill:
            msg = "a skill requirement needs skill"
            raise ValueError(msg)
        return self


class WorkflowNode(SchemaModel):
    key: NodeKey
    type: str = Field(min_length=1, max_length=64)
    """A ``Stage`` value or one of :data:`NON_STAGE_TYPES`."""
    values: dict[str, WidgetValue] = Field(default_factory=dict)
    """Widget values frozen onto this node, keyed by the widget names the node catalogue
    declares."""
    note: str = Field(default="", max_length=400)
    title: str = Field(default="", max_length=80)
    x: float | None = None
    y: float | None = None
    """Canvas position. Absent means the exporter lays the node out by topological depth."""

    @model_validator(mode="after")
    def _type_is_known(self) -> WorkflowNode:
        if self.type not in NON_STAGE_TYPES and self.type not in {s.value for s in Stage}:
            msg = f"node {self.key}: unknown type {self.type!r}"
            raise ValueError(msg)
        return self

    @property
    def stage(self) -> Stage | None:
        return Stage(self.type) if self.type not in NON_STAGE_TYPES else None

    @property
    def runnable(self) -> bool:
        return self.type not in NON_RUNNABLE_TYPES


class WorkflowWire(SchemaModel):
    from_key: NodeKey
    from_slot: SlotName
    to_key: NodeKey
    to_slot: SlotName


class WorkflowGroup(SchemaModel):
    """Nodes this lane shows as one, and what it calls them.

    Half of every lane in the catalogue is the same few steps — read the recording, clean up the
    voice, build the captions, check and package. A group is how a definition says "these are one
    idea", so opening the lane on the canvas shows a step called *Captions from the voice* instead
    of two nodes an operator has to recognise, and opening the group shows both with every widget
    on them. Purely presentation: the runner reads ``order``, which knows nothing about groups.
    """

    key: NodeKey
    name: str = Field(min_length=1, max_length=80)
    members: tuple[NodeKey, ...] = Field(min_length=1)
    collapsed: bool = True


class WorkflowTemplate(VersionedModel):
    """A complete workflow: what it is for, what it needs, its graph, and its run order."""

    id: WorkflowId
    name: str = Field(min_length=3, max_length=80)
    """Deliberately general. A workflow is named for what it does to the material, not for the one
    story it was first used on: a lane that cuts drawings into a film is a picture story whether
    the script is a romance, a fable or a product explainer."""
    description: str = Field(min_length=10, max_length=600)
    category: WorkflowCategory
    tags: tuple[str, ...] = ()
    caveat: str = Field(default="", max_length=400)
    """Why this workflow cannot run end to end yet, if it cannot. A definition naming a stage
    with no executor must say so here, which keeps the catalogue honest, not aspirational."""
    prerequisite: str = Field(default="", max_length=400)
    """What the operator must supply first: a recording, an image, a written script."""
    models: tuple[ModelRequirement, ...] = ()
    nodes: tuple[WorkflowNode, ...] = Field(min_length=1)
    wires: tuple[WorkflowWire, ...] = ()
    order: tuple[NodeKey, ...] = Field(min_length=1)
    """The runner's linear order, by node key. Must be a topological order of ``wires``."""
    groups: tuple[WorkflowGroup, ...] = ()
    """How the canvas folds this lane. Presentation only; nothing executes differently."""

    @model_validator(mode="after")
    def _coherent(self) -> WorkflowTemplate:
        keys = [n.key for n in self.nodes]
        if len(set(keys)) != len(keys):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            msg = f"duplicate node keys: {dupes}"
            raise ValueError(msg)
        known = set(keys)

        seen_inputs: set[tuple[str, str]] = set()
        for w in self.wires:
            if w.from_key not in known or w.to_key not in known:
                msg = (
                    f"wire {w.from_key}.{w.from_slot} -> {w.to_key}.{w.to_slot}"
                    " names an unknown node"
                )
                raise ValueError(msg)
            if w.from_key == w.to_key:
                msg = f"node {w.from_key} is wired to itself"
                raise ValueError(msg)
            if (w.to_key, w.to_slot) in seen_inputs:
                msg = f"input {w.to_key}.{w.to_slot} is wired twice"
                raise ValueError(msg)
            seen_inputs.add((w.to_key, w.to_slot))

        runnable = [n.key for n in self.nodes if n.runnable]
        if set(self.order) != set(runnable):
            missing = sorted(set(runnable) - set(self.order))
            extra = sorted(set(self.order) - set(runnable))
            msg = (
                "order must cover every runnable node exactly once"
                f" (missing {missing}, extra {extra})"
            )
            raise ValueError(msg)
        if len(set(self.order)) != len(self.order):
            msg = "order repeats a node key"
            raise ValueError(msg)

        position = {key: i for i, key in enumerate(self.order)}
        for w in self.wires:
            a, b = position.get(w.from_key), position.get(w.to_key)
            if a is not None and b is not None and a > b:
                msg = (
                    f"order is not a topological order of the wires: {w.to_key} runs before"
                    f" {w.from_key}, which feeds it"
                )
                raise ValueError(msg)

        # A cycle is unreachable given the order check above, but a wire between two non-runnable
        # nodes escapes it, so the graph is checked directly too.
        outgoing: dict[str, list[str]] = {k: [] for k in known}
        for w in self.wires:
            outgoing[w.from_key].append(w.to_key)
        state: dict[str, int] = {}

        def visit(node: str) -> None:
            if state.get(node) == 2:
                return
            if state.get(node) == 1:
                msg = f"the graph has a cycle through {node}"
                raise ValueError(msg)
            state[node] = 1
            for nxt in outgoing[node]:
                visit(nxt)
            state[node] = 2

        for key in known:
            visit(key)

        group_keys: set[str] = set()
        owner: dict[str, str] = {}
        for group in self.groups:
            if group.key in group_keys:
                msg = f"duplicate group key {group.key}"
                raise ValueError(msg)
            group_keys.add(group.key)
            for member in group.members:
                if member not in known:
                    msg = f"group {group.key} names unknown node {member}"
                    raise ValueError(msg)
                if member in owner:
                    msg = f"node {member} is in two groups ({owner[member]}, {group.key})"
                    raise ValueError(msg)
                owner[member] = group.key
        return self

    def node(self, key: str) -> WorkflowNode:
        for n in self.nodes:
            if n.key == key:
                return n
        msg = f"workflow {self.id} has no node {key!r}"
        raise KeyError(msg)

    def stage_order(self) -> tuple[tuple[str, Stage, dict[str, WidgetValue]], ...]:
        """``(node_key, stage, values)`` in run order, skipping the non-runnable nodes."""
        out = []
        for key in self.order:
            node = self.node(key)
            stage = node.stage
            if stage is None:
                continue
            out.append((key, stage, dict(node.values)))
        return tuple(out)

    def stages(self) -> tuple[Stage, ...]:
        return tuple(stage for _key, stage, _values in self.stage_order())
