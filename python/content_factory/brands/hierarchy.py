"""BrandNode hierarchy (phase 12): parent brand → child brand/location. Locked templates and
policies inherit downward and cannot be overridden locally; unlocked values may be."""

from __future__ import annotations

from dataclasses import dataclass, field


class BrandHierarchyError(Exception):
    pass


@dataclass(frozen=True)
class BrandNode:
    node_id: str
    parent_id: str | None
    name: str
    tokens: dict[str, str] = field(default_factory=dict)  # design token overrides
    locked_tokens: frozenset[str] = frozenset()  # keys children may NOT override
    policies: dict[str, str] = field(default_factory=dict)  # e.g. disclosure, approval mode
    locked_policies: frozenset[str] = frozenset()


@dataclass
class BrandTree:
    nodes: dict[str, BrandNode] = field(default_factory=dict)

    def add(self, node: BrandNode) -> None:
        if node.parent_id is not None and node.parent_id not in self.nodes:
            raise BrandHierarchyError(f"parent {node.parent_id!r} does not exist")
        if node.node_id in self.nodes:
            raise BrandHierarchyError(f"node {node.node_id!r} already exists")
        # A child may not even DECLARE an override for a key its ancestry locked.
        locked = self._locked_keys(node.parent_id)
        illegal_tokens = set(node.tokens) & locked["tokens"]
        illegal_policies = set(node.policies) & locked["policies"]
        if illegal_tokens or illegal_policies:
            raise BrandHierarchyError(
                f"locked upstream, cannot override here: tokens={sorted(illegal_tokens)} policies={sorted(illegal_policies)}"  # noqa: E501
            )
        self.nodes[node.node_id] = node

    def _locked_keys(self, start: str | None) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {"tokens": set(), "policies": set()}
        current = start
        while current is not None:
            node = self.nodes[current]
            out["tokens"] |= set(node.locked_tokens)
            out["policies"] |= set(node.locked_policies)
            current = node.parent_id
        return out

    def _chain(self, node_id: str) -> list[BrandNode]:
        if node_id not in self.nodes:
            raise BrandHierarchyError(f"unknown node {node_id!r}")
        chain: list[BrandNode] = []
        current: str | None = node_id
        while current is not None:
            node = self.nodes[current]
            chain.append(node)
            current = node.parent_id
        return list(reversed(chain))  # root first

    def effective(self, node_id: str) -> dict[str, dict[str, str]]:
        """Root-to-leaf merge; a locked key keeps the value from the node that locked it."""
        tokens: dict[str, str] = {}
        policies: dict[str, str] = {}
        locked_t: set[str] = set()
        locked_p: set[str] = set()
        for node in self._chain(node_id):
            for k, v in node.tokens.items():
                if k not in locked_t:
                    tokens[k] = v
            for k, v in node.policies.items():
                if k not in locked_p:
                    policies[k] = v
            locked_t |= set(node.locked_tokens)
            locked_p |= set(node.locked_policies)
        return {"tokens": tokens, "policies": policies}
