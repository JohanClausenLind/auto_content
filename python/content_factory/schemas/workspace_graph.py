"""Workspace graph contract (the node-graph editor's document).

This is the same document the TypeScript editor serialises — field for field — so a graph saved
from the browser round-trips through this contract byte-compatibly. Node ``type`` values are
either a pipeline :class:`~content_factory.schemas.dag.Stage` name or one of the editor's
non-stage node types (``input.brief``, ``utility.note``, ``publish.social``); the compiler in
``content_factory.workspace`` decides what each one means for execution.
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


class WorkspaceGraph(SchemaModel):
    schema_version: Literal[1] = 1
    graph_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    nodes: tuple[WorkspaceNode, ...] = ()
    links: tuple[WorkspaceLink, ...] = ()

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
