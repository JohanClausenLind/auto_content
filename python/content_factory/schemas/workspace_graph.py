"""Workspace graph contract (the node-graph editor's document).

This is the same document the TypeScript editor serialises — field for field — so a graph saved
from the browser round-trips through this contract byte-compatibly. Node ``type`` values are
either a pipeline :class:`~content_factory.schemas.dag.Stage` name or one of the editor's
non-stage node types (``input.brief``, the ``input.audio``/``image``/``video`` file inputs,
``utility.note``, ``publish.social``); the compiler in ``content_factory.workspace`` decides what
each one means for execution. ``groups`` are folded views over the same flat nodes and links: the
canvas draws a group as one node, and nothing downstream of the canvas knows they exist.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import SchemaModel

NodeMode = Literal["always", "muted", "bypass"]

WidgetValue = str | int | float | bool


class WorkspaceNode(SchemaModel):
    id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=64)
    key: str = Field(default="", max_length=64)
    """The lane's own name for this step — ``anchor``, ``spokes``, ``frames_gate`` — when this
    node came from a lane in ``workflows/*.yaml``. Empty for a hand-built node.

    It exists to join a graph to a run. ``run.json`` records what each step did and what it
    produced keyed by exactly this name (see :mod:`content_factory.services.run_nodes`), and the
    canvas's own ids are generated per graph, so without it "show me what this node made" could
    only be answered by matching on stage type — which is ambiguous in every lane that runs one
    stage twice, and those are the lanes where the answer matters most.

    Not an identity: two graphs built from the same lane share these keys, and that is the point.
    ``id`` stays the thing links refer to."""
    title: str | None = Field(default=None, max_length=200)
    x: float
    y: float
    width: float | None = Field(default=None, ge=1)
    collapsed: bool = False
    note: str = Field(default="", max_length=5000)
    values: dict[str, WidgetValue] = Field(default_factory=dict)
    mode: NodeMode = "always"


class WorkspaceLink(SchemaModel):
    id: str = Field(min_length=1, max_length=64)
    from_node: str = Field(min_length=1, max_length=64)
    from_slot: str = Field(min_length=1, max_length=64)
    to_node: str = Field(min_length=1, max_length=64)
    to_slot: str = Field(min_length=1, max_length=64)


class WorkspaceGroup(SchemaModel):
    """A named set of nodes the canvas can draw as one.

    Purely a view: the nodes and links stay in the flat graph exactly as they were, so the
    compiler, the runner and execution order never see a group at all. It exists because half of
    every lane is the same few steps — clean up the voice, finish the picture, check and package —
    and an operator changing one prompt should not have to read all of them. Folded, the canvas
    draws one node with the group's boundary slots; opened, it draws the members in a frame with
    every widget available.

    ``members`` order is meaningful: it decides the order of a folded group's ports.
    """

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    template: str = Field(default="", max_length=64)
    """The block this was inserted from, for provenance. Empty for a hand-made group."""
    collapsed: bool = True
    members: tuple[str, ...] = ()
    x: float = 0.0
    y: float = 0.0


class WorkspaceGraph(SchemaModel):
    schema_version: Literal[1] = 1
    graph_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    nodes: tuple[WorkspaceNode, ...] = ()
    links: tuple[WorkspaceLink, ...] = ()
    groups: tuple[WorkspaceGroup, ...] = ()
    """Folded views over ``nodes``. Absent in every document written before groups existed, which
    is why it defaults to empty rather than being required."""

    @model_validator(mode="after")
    def _invariants(self) -> WorkspaceGraph:
        node_ids: set[str] = set()
        for node in self.nodes:
            if node.id in node_ids:
                raise ValueError(f"duplicate node id {node.id}")
            node_ids.add(node.id)
        link_ids: set[str] = set()
        occupied: set[tuple[str, str]] = set()
        for link in self.links:
            if link.id in link_ids:
                raise ValueError(f"duplicate link id {link.id}")
            link_ids.add(link.id)
            if link.from_node not in node_ids or link.to_node not in node_ids:
                raise ValueError(f"link {link.id} references an unknown node")
            if link.from_node == link.to_node:
                raise ValueError(f"link {link.id} connects a node to itself")
            target = (link.to_node, link.to_slot)
            if target in occupied:
                raise ValueError(f"input {link.to_node}.{link.to_slot} is connected twice")
            occupied.add(target)
        group_ids: set[str] = set()
        owner: dict[str, str] = {}
        for group in self.groups:
            if group.id in group_ids:
                raise ValueError(f"duplicate group id {group.id}")
            group_ids.add(group.id)
            for member in group.members:
                if member not in node_ids:
                    raise ValueError(f"group {group.id} names unknown node {member}")
                if member in owner:
                    msg = f"node {member} is in two groups ({owner[member]}, {group.id})"
                    raise ValueError(msg)
                owner[member] = group.id
        if self._topological_order() is None:
            raise ValueError("the graph contains a cycle")
        return self

    def _topological_order(self) -> list[str] | None:
        indegree = {n.id: 0 for n in self.nodes}
        for link in self.links:
            indegree[link.to_node] += 1
        ready = [n.id for n in self.nodes if indegree[n.id] == 0]
        order: list[str] = []
        while ready:
            current = ready.pop(0)
            order.append(current)
            for link in self.links:
                if link.from_node != current:
                    continue
                indegree[link.to_node] -= 1
                if indegree[link.to_node] == 0:
                    ready.append(link.to_node)
        return order if len(order) == len(self.nodes) else None

    def topological(self) -> tuple[WorkspaceNode, ...]:
        order = self._topological_order()
        assert order is not None  # the validator refused cyclic graphs
        by_id = {n.id: n for n in self.nodes}
        return tuple(by_id[node_id] for node_id in order)
