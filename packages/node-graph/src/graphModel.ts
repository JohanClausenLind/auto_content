/**
 * The graph and every edit to it.
 *
 * Edits are typed operations applied by pure functions that also return the exact inverse, the
 * same discipline EditorCore uses for project revisions: undo is replaying inverses, never a
 * snapshot diff. Nothing here touches React or the DOM, so all of it is testable in isolation.
 */

import {
  findSlot,
  parseChips,
  visibleWidgets,
  widgetDefaults,
  type NodeCatalog,
  type WidgetValue,
} from "./nodeDefs";
import { typesCompatible } from "./datatypes";

export type NodeMode = "always" | "muted" | "bypass";

export interface GraphNode {
  readonly id: string;
  /** A type in the catalogue. Unknown types still render, flagged as a problem. */
  readonly type: string;
  /** Operator-renamed title, or null to use the definition's. */
  readonly title: string | null;
  readonly x: number;
  readonly y: number;
  /** Body width in canvas units, or null for the definition's default. */
  readonly width: number | null;
  readonly collapsed: boolean;
  /** The free text under the node body. */
  readonly note: string;
  readonly values: Readonly<Record<string, WidgetValue>>;
  readonly mode: NodeMode;
}

export interface GraphLink {
  readonly id: string;
  readonly from_node: string;
  readonly from_slot: string;
  readonly to_node: string;
  readonly to_slot: string;
}

/**
 * A named set of nodes that can be shown as one.
 *
 * The problem it solves: half of every lane in this catalogue is the same three or four steps —
 * clean up the voice, finish the picture, check it and package it — and an operator opening a
 * graph to change one prompt had to read all of them every time. A group is how a lane says
 * "these five nodes are one idea called Clean up voice". Collapsed, the canvas draws one node
 * with the group's own boundary slots; opened, it draws the members inside a frame, and every
 * widget is there to change.
 *
 * It is a **view**, not a container. The nodes and links stay exactly where they were in the flat
 * graph, so the compiler, the runner, validation, execution order and every existing test see the
 * same graph they always did — which is the whole reason this can exist without a second graph
 * format, a nested compiler and a subgraph contract.
 */
export interface GraphGroup {
  readonly id: string;
  /** What it is called when folded. */
  readonly name: string;
  /** The block it was inserted from, or "" for a hand-made group. Provenance, not behaviour. */
  readonly template: string;
  readonly collapsed: boolean;
  /** Node ids. Every one must exist, and a node belongs to at most one group. */
  readonly members: readonly string[];
  /** Where the folded node sits. Members keep their own positions inside the frame. */
  readonly x: number;
  readonly y: number;
}

export interface WorkspaceGraph {
  readonly schema_version: 1;
  readonly graph_id: string;
  readonly name: string;
  readonly nodes: readonly GraphNode[];
  readonly links: readonly GraphLink[];
  readonly groups: readonly GraphGroup[];
}

export type GraphOp =
  | { readonly op: "add_node"; readonly node: GraphNode }
  | { readonly op: "remove_node"; readonly node_id: string }
  | { readonly op: "move_node"; readonly node_id: string; readonly x: number; readonly y: number }
  | { readonly op: "set_title"; readonly node_id: string; readonly title: string | null }
  | { readonly op: "set_note"; readonly node_id: string; readonly note: string }
  | {
      readonly op: "set_widget";
      readonly node_id: string;
      readonly name: string;
      readonly value: WidgetValue;
    }
  | { readonly op: "set_collapsed"; readonly node_id: string; readonly collapsed: boolean }
  | { readonly op: "set_width"; readonly node_id: string; readonly width: number | null }
  | { readonly op: "set_mode"; readonly node_id: string; readonly mode: NodeMode }
  | { readonly op: "connect"; readonly link: GraphLink }
  | { readonly op: "disconnect"; readonly link_id: string }
  | { readonly op: "rename_graph"; readonly name: string }
  | { readonly op: "add_group"; readonly group: GraphGroup }
  | { readonly op: "remove_group"; readonly group_id: string }
  | { readonly op: "rename_group"; readonly group_id: string; readonly name: string }
  | { readonly op: "set_group_collapsed"; readonly group_id: string; readonly collapsed: boolean }
  | { readonly op: "set_group_members"; readonly group_id: string; readonly members: readonly string[] }
  /** Absolute, so the inverse is the previous position; members shift by the same delta. */
  | { readonly op: "move_group"; readonly group_id: string; readonly x: number; readonly y: number };

export class GraphOpError extends Error {
  constructor(
    readonly op: GraphOp["op"],
    message: string,
  ) {
    super(`${op}: ${message}`);
    this.name = "GraphOpError";
  }
}

export interface ApplyResult {
  readonly graph: WorkspaceGraph;
  /** Applying this restores the graph exactly. */
  readonly inverse: GraphOp;
}

export interface BatchResult {
  readonly graph: WorkspaceGraph;
  /** Inverse operations in the order they must be applied to undo the batch. */
  readonly inverse: readonly GraphOp[];
}

export function emptyGraph(graphId: string, name = "Untitled graph"): WorkspaceGraph {
  return { schema_version: 1, graph_id: graphId, name, nodes: [], links: [], groups: [] };
}

export function groupById(graph: WorkspaceGraph, groupId: string): GraphGroup | undefined {
  return graph.groups.find((g) => g.id === groupId);
}

/** The group a node belongs to, if any. A node is in at most one. */
export function groupOf(graph: WorkspaceGraph, nodeId: string): GraphGroup | undefined {
  return graph.groups.find((g) => g.members.includes(nodeId));
}

/** True when this node is inside a folded group, so the canvas draws the group instead. */
export function isHidden(graph: WorkspaceGraph, nodeId: string): boolean {
  const group = groupOf(graph, nodeId);
  return group !== undefined && group.collapsed;
}

export interface GroupPort {
  /** Stable handle id: `in:<node>:<slot>` / `out:<node>:<slot>`. */
  readonly id: string;
  readonly node_id: string;
  readonly slot: string;
  readonly type: string;
  readonly label: string;
}

/**
 * The slots a folded group shows: every member input fed from outside, every member output read
 * from outside, plus the unconnected required inputs — so folding can never hide the fact that
 * something still has to be connected.
 *
 * Derived, never stored. A group whose members are rewired shows different ports the moment the
 * links change, which is the only way a folded view can stay honest.
 */
export function groupPorts(
  graph: WorkspaceGraph,
  catalog: NodeCatalog,
  group: GraphGroup,
): { inputs: readonly GroupPort[]; outputs: readonly GroupPort[] } {
  const members = new Set(group.members);
  const inputs: GroupPort[] = [];
  const outputs: GroupPort[] = [];
  const seen = new Set<string>();
  // Member order follows the group's own list, so the ports do not jump around when a link moves.
  for (const nodeId of group.members) {
    const node = nodeById(graph, nodeId);
    const def = node ? catalog.get(node.type) : undefined;
    if (!node || !def) continue;
    const title = node.title ?? def.title;
    for (const slot of def.inputs) {
      const link = linkInto(graph, nodeId, slot.name);
      const fromOutside = link !== undefined && !members.has(link.from_node);
      const openRequired = link === undefined && !slot.optional;
      if (!fromOutside && !openRequired) continue;
      const id = `in:${nodeId}:${slot.name}`;
      if (seen.has(id)) continue;
      seen.add(id);
      inputs.push({
        id,
        node_id: nodeId,
        slot: slot.name,
        type: slot.type,
        label: `${title} · ${slot.label ?? slot.name}`,
      });
    }
    for (const slot of def.outputs) {
      const used = graph.links.some(
        (l) => l.from_node === nodeId && l.from_slot === slot.name && !members.has(l.to_node),
      );
      if (!used) continue;
      const id = `out:${nodeId}:${slot.name}`;
      if (seen.has(id)) continue;
      seen.add(id);
      outputs.push({
        id,
        node_id: nodeId,
        slot: slot.name,
        type: slot.type,
        label: `${title} · ${slot.label ?? slot.name}`,
      });
    }
  }
  return { inputs, outputs };
}

/** Parse a group handle id back into the member and slot it stands for. */
export function parseGroupPort(handle: string): { node_id: string; slot: string } | null {
  const match = /^(?:in|out):([^:]+):(.+)$/.exec(handle);
  return match ? { node_id: match[1]!, slot: match[2]! } : null;
}

export function nodeById(graph: WorkspaceGraph, nodeId: string): GraphNode | undefined {
  return graph.nodes.find((n) => n.id === nodeId);
}

export function linkById(graph: WorkspaceGraph, linkId: string): GraphLink | undefined {
  return graph.links.find((l) => l.id === linkId);
}

/** The link occupying an input slot, if any. An input takes at most one link. */
export function linkInto(graph: WorkspaceGraph, nodeId: string, slot: string): GraphLink | undefined {
  return graph.links.find((l) => l.to_node === nodeId && l.to_slot === slot);
}

export function linksOf(graph: WorkspaceGraph, nodeId: string): readonly GraphLink[] {
  return graph.links.filter((l) => l.from_node === nodeId || l.to_node === nodeId);
}

function requireNode(graph: WorkspaceGraph, op: GraphOp["op"], nodeId: string): GraphNode {
  const node = nodeById(graph, nodeId);
  if (!node) throw new GraphOpError(op, `unknown node ${nodeId}`);
  return node;
}

function withNode(graph: WorkspaceGraph, next: GraphNode): WorkspaceGraph {
  return { ...graph, nodes: graph.nodes.map((n) => (n.id === next.id ? next : n)) };
}

function requireGroup(graph: WorkspaceGraph, op: GraphOp["op"], groupId: string): GraphGroup {
  const group = groupById(graph, groupId);
  if (!group) throw new GraphOpError(op, `unknown group ${groupId}`);
  return group;
}

function withGroup(graph: WorkspaceGraph, next: GraphGroup): WorkspaceGraph {
  return { ...graph, groups: graph.groups.map((g) => (g.id === next.id ? next : g)) };
}

/**
 * Node and link order carries no meaning, so it is kept canonical (sorted by id). That makes an
 * inverse restore the previous graph exactly and the serialised form independent of edit order.
 */
function byId<T extends { readonly id: string }>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** Apply exactly one operation. Pure: the input graph is never mutated. */
export function applyOp(graph: WorkspaceGraph, op: GraphOp): ApplyResult {
  switch (op.op) {
    case "add_node": {
      if (nodeById(graph, op.node.id)) throw new GraphOpError(op.op, `duplicate node ${op.node.id}`);
      return {
        graph: { ...graph, nodes: byId([...graph.nodes, op.node]) },
        inverse: { op: "remove_node", node_id: op.node.id },
      };
    }
    case "remove_node": {
      const node = requireNode(graph, op.op, op.node_id);
      if (linksOf(graph, op.node_id).length > 0) {
        throw new GraphOpError(op.op, `node ${op.node_id} still has links; disconnect them first`);
      }
      return {
        graph: { ...graph, nodes: graph.nodes.filter((n) => n.id !== op.node_id) },
        inverse: { op: "add_node", node: node },
      };
    }
    case "move_node": {
      const node = requireNode(graph, op.op, op.node_id);
      return {
        graph: withNode(graph, { ...node, x: op.x, y: op.y }),
        inverse: { op: "move_node", node_id: node.id, x: node.x, y: node.y },
      };
    }
    case "set_title": {
      const node = requireNode(graph, op.op, op.node_id);
      return {
        graph: withNode(graph, { ...node, title: op.title }),
        inverse: { op: "set_title", node_id: node.id, title: node.title },
      };
    }
    case "set_note": {
      const node = requireNode(graph, op.op, op.node_id);
      return {
        graph: withNode(graph, { ...node, note: op.note }),
        inverse: { op: "set_note", node_id: node.id, note: node.note },
      };
    }
    case "set_widget": {
      const node = requireNode(graph, op.op, op.node_id);
      const before = node.values[op.name];
      if (before === undefined) throw new GraphOpError(op.op, `node ${node.id} has no widget ${op.name}`);
      return {
        graph: withNode(graph, { ...node, values: { ...node.values, [op.name]: op.value } }),
        inverse: { op: "set_widget", node_id: node.id, name: op.name, value: before },
      };
    }
    case "set_collapsed": {
      const node = requireNode(graph, op.op, op.node_id);
      return {
        graph: withNode(graph, { ...node, collapsed: op.collapsed }),
        inverse: { op: "set_collapsed", node_id: node.id, collapsed: node.collapsed },
      };
    }
    case "set_width": {
      const node = requireNode(graph, op.op, op.node_id);
      return {
        graph: withNode(graph, { ...node, width: op.width }),
        inverse: { op: "set_width", node_id: node.id, width: node.width },
      };
    }
    case "set_mode": {
      const node = requireNode(graph, op.op, op.node_id);
      return {
        graph: withNode(graph, { ...node, mode: op.mode }),
        inverse: { op: "set_mode", node_id: node.id, mode: node.mode },
      };
    }
    case "connect": {
      const link = op.link;
      if (linkById(graph, link.id)) throw new GraphOpError(op.op, `duplicate link ${link.id}`);
      requireNode(graph, op.op, link.from_node);
      requireNode(graph, op.op, link.to_node);
      if (linkInto(graph, link.to_node, link.to_slot)) {
        throw new GraphOpError(op.op, `input ${link.to_node}.${link.to_slot} is already connected`);
      }
      return {
        graph: { ...graph, links: byId([...graph.links, link]) },
        inverse: { op: "disconnect", link_id: link.id },
      };
    }
    case "disconnect": {
      const link = linkById(graph, op.link_id);
      if (!link) throw new GraphOpError(op.op, `unknown link ${op.link_id}`);
      return {
        graph: { ...graph, links: graph.links.filter((l) => l.id !== op.link_id) },
        inverse: { op: "connect", link },
      };
    }
    case "rename_graph": {
      return { graph: { ...graph, name: op.name }, inverse: { op: "rename_graph", name: graph.name } };
    }
    case "add_group": {
      if (groupById(graph, op.group.id)) throw new GraphOpError(op.op, `duplicate group ${op.group.id}`);
      for (const member of op.group.members) {
        if (!nodeById(graph, member)) throw new GraphOpError(op.op, `unknown node ${member}`);
        const owner = groupOf(graph, member);
        if (owner) throw new GraphOpError(op.op, `node ${member} is already in group ${owner.id}`);
      }
      return {
        graph: { ...graph, groups: byId([...graph.groups, op.group]) },
        inverse: { op: "remove_group", group_id: op.group.id },
      };
    }
    case "remove_group": {
      const group = groupById(graph, op.group_id);
      if (!group) throw new GraphOpError(op.op, `unknown group ${op.group_id}`);
      return {
        graph: { ...graph, groups: graph.groups.filter((g) => g.id !== op.group_id) },
        // Ungrouping keeps every node and link: a group is a view, and dropping it drops the view.
        inverse: { op: "add_group", group },
      };
    }
    case "rename_group": {
      const group = requireGroup(graph, op.op, op.group_id);
      return {
        graph: withGroup(graph, { ...group, name: op.name }),
        inverse: { op: "rename_group", group_id: group.id, name: group.name },
      };
    }
    case "set_group_collapsed": {
      const group = requireGroup(graph, op.op, op.group_id);
      return {
        graph: withGroup(graph, { ...group, collapsed: op.collapsed }),
        inverse: { op: "set_group_collapsed", group_id: group.id, collapsed: group.collapsed },
      };
    }
    case "set_group_members": {
      const group = requireGroup(graph, op.op, op.group_id);
      for (const member of op.members) {
        if (!nodeById(graph, member)) throw new GraphOpError(op.op, `unknown node ${member}`);
        const owner = groupOf(graph, member);
        if (owner && owner.id !== group.id) {
          throw new GraphOpError(op.op, `node ${member} is already in group ${owner.id}`);
        }
      }
      return {
        graph: withGroup(graph, { ...group, members: [...op.members] }),
        inverse: { op: "set_group_members", group_id: group.id, members: group.members },
      };
    }
    case "move_group": {
      const group = requireGroup(graph, op.op, op.group_id);
      const dx = op.x - group.x;
      const dy = op.y - group.y;
      const members = new Set(group.members);
      // The members move with the folded node, so opening a group that was dragged across the
      // canvas shows its nodes where the group is and not where it used to be.
      const nodes = graph.nodes.map((n) =>
        members.has(n.id) ? { ...n, x: n.x + dx, y: n.y + dy } : n,
      );
      return {
        graph: { ...withGroup(graph, { ...group, x: op.x, y: op.y }), nodes },
        inverse: { op: "move_group", group_id: group.id, x: group.x, y: group.y },
      };
    }
  }
}

/** Apply a batch atomically: on any failure the original graph is returned unchanged. */
export function applyOps(graph: WorkspaceGraph, ops: readonly GraphOp[]): BatchResult {
  let current = graph;
  const inverse: GraphOp[] = [];
  for (const op of ops) {
    const result = applyOp(current, op);
    current = result.graph;
    inverse.push(result.inverse);
  }
  return { graph: current, inverse: inverse.reverse() };
}

// --- op builders: the compound edits the editor actually performs -----------------------------

let counter = 0;

/** Ids are opaque strings; the prefix only helps when reading a serialised graph. */
export function newId(prefix: string): string {
  counter += 1;
  const random = Math.random().toString(36).slice(2, 8);
  return `${prefix}_${Date.now().toString(36)}${counter.toString(36)}${random}`;
}

export interface NewNodeOptions {
  readonly id?: string;
  readonly x: number;
  readonly y: number;
  readonly note?: string;
  readonly values?: Readonly<Record<string, WidgetValue>>;
  readonly title?: string | null;
  readonly width?: number | null;
}

/** A node of `type` with the definition's widget defaults filled in. */
export function makeNode(catalog: NodeCatalog, type: string, options: NewNodeOptions): GraphNode {
  const def = catalog.get(type);
  if (!def) throw new Error(`unknown node type ${type}`);
  return {
    id: options.id ?? newId("node"),
    type,
    title: options.title ?? null,
    x: options.x,
    y: options.y,
    width: options.width ?? null,
    collapsed: false,
    note: options.note ?? "",
    values: { ...widgetDefaults(def), ...options.values },
    mode: "always",
  };
}

/** Removing a node means dropping its links first, so the batch stays reversible. */
export function removeNodesOps(graph: WorkspaceGraph, nodeIds: readonly string[]): GraphOp[] {
  const ids = new Set(nodeIds);
  const ops: GraphOp[] = [];
  for (const link of graph.links) {
    if (ids.has(link.from_node) || ids.has(link.to_node)) ops.push({ op: "disconnect", link_id: link.id });
  }
  // A group may not name a node that is gone, so the membership is corrected first — and a group
  // whose last member is deleted goes with it rather than staying as an empty label.
  for (const group of graph.groups) {
    const left = group.members.filter((m) => !ids.has(m));
    if (left.length === group.members.length) continue;
    if (left.length === 0) ops.push({ op: "remove_group", group_id: group.id });
    else ops.push({ op: "set_group_members", group_id: group.id, members: left });
  }
  for (const id of nodeIds) {
    if (nodeById(graph, id)) ops.push({ op: "remove_node", node_id: id });
  }
  return ops;
}

/** Fold a selection into one node: the group, named, collapsed, placed at the members' corner. */
export function groupNodesOps(
  graph: WorkspaceGraph,
  nodeIds: readonly string[],
  options: { name?: string; template?: string; id?: string; collapsed?: boolean } = {},
): GraphOp[] {
  const members = nodeIds.filter((id) => nodeById(graph, id) && !groupOf(graph, id));
  if (members.length === 0) return [];
  const xs = members.map((id) => nodeById(graph, id)!.x);
  const ys = members.map((id) => nodeById(graph, id)!.y);
  return [
    {
      op: "add_group",
      group: {
        id: options.id ?? newId("grp"),
        name: options.name ?? "Group",
        template: options.template ?? "",
        collapsed: options.collapsed ?? true,
        members,
        x: Math.min(...xs),
        y: Math.min(...ys),
      },
    },
  ];
}

/**
 * Connecting replaces whatever occupied the input, which is what a graph editor is expected to
 * do when you drop a second link on the same input.
 */
export function connectOps(
  graph: WorkspaceGraph,
  from: { node: string; slot: string },
  to: { node: string; slot: string },
  linkId = newId("link"),
): GraphOp[] {
  const occupied = linkInto(graph, to.node, to.slot);
  const ops: GraphOp[] = [];
  if (occupied) ops.push({ op: "disconnect", link_id: occupied.id });
  ops.push({
    op: "connect",
    link: { id: linkId, from_node: from.node, from_slot: from.slot, to_node: to.node, to_slot: to.slot },
  });
  return ops;
}

/** Paste/duplicate: clone nodes at an offset, keeping links that are internal to the selection. */
export function duplicateOps(
  graph: WorkspaceGraph,
  nodeIds: readonly string[],
  offset: { x: number; y: number },
): { ops: GraphOp[]; newIds: string[] } {
  const selected = graph.nodes.filter((n) => nodeIds.includes(n.id));
  const idMap = new Map(selected.map((n) => [n.id, newId("node")]));
  const ops: GraphOp[] = [];
  for (const node of selected) {
    const id = idMap.get(node.id);
    if (!id) continue;
    ops.push({ op: "add_node", node: { ...node, id, x: node.x + offset.x, y: node.y + offset.y } });
  }
  for (const link of graph.links) {
    const from = idMap.get(link.from_node);
    const to = idMap.get(link.to_node);
    if (!from || !to) continue;
    ops.push({
      op: "connect",
      link: { id: newId("link"), from_node: from, from_slot: link.from_slot, to_node: to, to_slot: link.to_slot },
    });
  }
  return { ops, newIds: [...idMap.values()] };
}

// --- connection rules -------------------------------------------------------------------------

export type ConnectVerdict = { readonly ok: true } | { readonly ok: false; readonly reason: string };

/**
 * Whether a link may be drawn. Refusals carry a reason the UI can show verbatim, because a
 * connection that silently does nothing is the most confusing thing a graph editor can do.
 */
export function canConnect(
  graph: WorkspaceGraph,
  catalog: NodeCatalog,
  from: { node: string; slot: string },
  to: { node: string; slot: string },
): ConnectVerdict {
  if (from.node === to.node) return { ok: false, reason: "a node cannot feed itself" };
  const source = nodeById(graph, from.node);
  const target = nodeById(graph, to.node);
  if (!source || !target) return { ok: false, reason: "unknown node" };
  const sourceDef = catalog.get(source.type);
  const targetDef = catalog.get(target.type);
  if (!sourceDef || !targetDef) return { ok: false, reason: "unknown node type" };
  const output = findSlot(sourceDef.outputs, from.slot);
  const input = findSlot(targetDef.inputs, to.slot);
  if (!output) return { ok: false, reason: `${sourceDef.title} has no output ${from.slot}` };
  if (!input) return { ok: false, reason: `${targetDef.title} has no input ${to.slot}` };
  if (!typesCompatible(output.type, input.type)) {
    return { ok: false, reason: `${output.type} does not fit ${input.type}` };
  }
  if (reaches(graph, to.node, from.node)) return { ok: false, reason: "that would make a cycle" };
  return { ok: true };
}

/** Can `target` be reached from `start` by following links downstream? */
function reaches(graph: WorkspaceGraph, start: string, target: string): boolean {
  if (start === target) return true;
  const seen = new Set<string>([start]);
  const queue = [start];
  while (queue.length > 0) {
    const current = queue.shift();
    if (current === undefined) break;
    for (const link of graph.links) {
      if (link.from_node !== current) continue;
      if (link.to_node === target) return true;
      if (!seen.has(link.to_node)) {
        seen.add(link.to_node);
        queue.push(link.to_node);
      }
    }
  }
  return false;
}

/** Execution order, or null when the graph contains a cycle. Deterministic (Kahn, stable). */
export function topoOrder(graph: WorkspaceGraph): readonly string[] | null {
  const indegree = new Map(graph.nodes.map((n) => [n.id, 0]));
  for (const link of graph.links) {
    if (!indegree.has(link.to_node) || !indegree.has(link.from_node)) continue;
    indegree.set(link.to_node, (indegree.get(link.to_node) ?? 0) + 1);
  }
  const ready = graph.nodes.filter((n) => (indegree.get(n.id) ?? 0) === 0).map((n) => n.id);
  const order: string[] = [];
  while (ready.length > 0) {
    const id = ready.shift();
    if (id === undefined) break;
    order.push(id);
    for (const link of graph.links) {
      if (link.from_node !== id) continue;
      const next = (indegree.get(link.to_node) ?? 0) - 1;
      indegree.set(link.to_node, next);
      if (next === 0) ready.push(link.to_node);
    }
  }
  return order.length === graph.nodes.length ? order : null;
}

// --- validation -------------------------------------------------------------------------------

export type ProblemSeverity = "error" | "warning";

export interface GraphProblem {
  readonly node_id: string | null;
  readonly severity: ProblemSeverity;
  readonly message: string;
}

/** Everything wrong with the graph, in a form the UI can list next to the nodes. */
export function validateGraph(graph: WorkspaceGraph, catalog: NodeCatalog): readonly GraphProblem[] {
  const problems: GraphProblem[] = [];
  for (const node of graph.nodes) {
    const def = catalog.get(node.type);
    if (!def) {
      problems.push({ node_id: node.id, severity: "error", message: `unknown node type ${node.type}` });
      continue;
    }
    if (def.kind === "note") continue;
    for (const input of def.inputs) {
      if (input.optional) continue;
      if (!linkInto(graph, node.id, input.name)) {
        problems.push({
          node_id: node.id,
          severity: "error",
          message:
            `${def.title}: input ${input.label ?? input.name} is not connected` +
            (input.hint ? ` — ${input.hint}` : ""),
        });
      }
    }
    for (const group of def.requires_one_of ?? []) {
      if (group.some((name) => linkInto(graph, node.id, name))) continue;
      const slots = group.map((name) => findSlot(def.inputs, name));
      const labels = group.map((name, i) => slots[i]?.label ?? name);
      const hint = slots.find((slot) => slot?.hint)?.hint;
      problems.push({
        node_id: node.id,
        severity: "error",
        message:
          `${def.title}: connect one of ${labels.join(" or ")}` + (hint ? ` — ${hint}` : ""),
      });
    }
    // Hidden widgets are skipped: a control the operator cannot see must never block a graph,
    // and `restore_speech` with the enhancer off should not demand an enhancer mode.
    for (const widget of visibleWidgets(def, node.values)) {
      if (!widget.required) continue;
      const value = node.values[widget.name];
      const empty =
        widget.kind === "chips"
          ? parseChips(value ?? "").length === 0
          : typeof value !== "string" || value.trim() === "";
      if (empty) {
        problems.push({
          node_id: node.id,
          severity: "error",
          message:
            `${def.title}: ${widget.label ?? widget.name} is empty` +
            (widget.hint ? ` — ${widget.hint}` : ""),
        });
      }
    }
    const hasOutputs = def.outputs.length > 0;
    const used = graph.links.some((l) => l.from_node === node.id);
    if (hasOutputs && !used) {
      problems.push({
        node_id: node.id,
        severity: "warning",
        message: `${def.title}: nothing consumes its output`,
      });
    }
  }
  const owner = new Map<string, string>();
  for (const group of graph.groups) {
    if (group.members.length === 0) {
      problems.push({ node_id: null, severity: "warning", message: `group ${group.name} is empty` });
    }
    for (const member of group.members) {
      if (!nodeById(graph, member)) {
        problems.push({
          node_id: null,
          severity: "error",
          message: `group ${group.name} names a node that is not in the graph`,
        });
        continue;
      }
      const already = owner.get(member);
      if (already !== undefined && already !== group.id) {
        problems.push({
          node_id: member,
          severity: "error",
          message: `this node is in two groups (${already} and ${group.id})`,
        });
      }
      owner.set(member, group.id);
    }
  }
  if (topoOrder(graph) === null) {
    problems.push({ node_id: null, severity: "error", message: "the graph contains a cycle" });
  }
  return problems;
}

// --- serialisation ----------------------------------------------------------------------------

export class GraphParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GraphParseError";
  }
}

const NODE_KEYS = ["id", "type", "title", "x", "y", "width", "collapsed", "note", "values", "mode"];
const LINK_KEYS = ["id", "from_node", "from_slot", "to_node", "to_slot"];
const GROUP_KEYS = ["id", "name", "template", "collapsed", "members", "x", "y"];
const GRAPH_KEYS = ["schema_version", "graph_id", "name", "nodes", "links", "groups"];

function record(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new GraphParseError(`${where}: expected an object`);
  }
  return value as Record<string, unknown>;
}

/** Unknown fields are errors, the same rule the Python contracts follow. */
function rejectUnknown(value: Record<string, unknown>, allowed: readonly string[], where: string): void {
  for (const key of Object.keys(value)) {
    if (!allowed.includes(key)) throw new GraphParseError(`${where}: unknown field ${key}`);
  }
}

/** Length bounds mirror the Pydantic contract (schemas/workspace_graph.py): a document this
 * parser accepts must also be accepted by PUT /v1/graphs, or the write-behind save 422s. */
function str(value: unknown, where: string, bounds?: { min?: number; max?: number }): string {
  if (typeof value !== "string") throw new GraphParseError(`${where}: expected a string`);
  if (bounds?.min !== undefined && value.length < bounds.min) {
    throw new GraphParseError(`${where}: at least ${bounds.min} character(s) required`);
  }
  if (bounds?.max !== undefined && value.length > bounds.max) {
    throw new GraphParseError(`${where}: at most ${bounds.max} characters allowed`);
  }
  return value;
}

const ID64 = { min: 1, max: 64 } as const;

function num(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new GraphParseError(`${where}: expected a finite number`);
  }
  return value;
}

function bool(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") throw new GraphParseError(`${where}: expected a boolean`);
  return value;
}

function parseValues(value: unknown, where: string): Record<string, WidgetValue> {
  const raw = record(value, where);
  const out: Record<string, WidgetValue> = {};
  for (const [key, entry] of Object.entries(raw)) {
    if (typeof entry === "string" || typeof entry === "boolean") out[key] = entry;
    else if (typeof entry === "number" && Number.isFinite(entry)) out[key] = entry;
    else throw new GraphParseError(`${where}.${key}: expected a string, number or boolean`);
  }
  return out;
}

function parseMode(value: unknown, where: string): NodeMode {
  const mode = str(value, where);
  if (mode !== "always" && mode !== "muted" && mode !== "bypass") {
    throw new GraphParseError(`${where}: unknown mode ${mode}`);
  }
  return mode;
}

/** Parse a serialised graph strictly. Throws `GraphParseError` with a readable path on any drift. */
export function parseGraph(value: unknown): WorkspaceGraph {
  const raw = record(value, "graph");
  rejectUnknown(raw, GRAPH_KEYS, "graph");
  if (raw.schema_version !== 1) throw new GraphParseError("graph.schema_version: expected 1");
  if (!Array.isArray(raw.nodes)) throw new GraphParseError("graph.nodes: expected an array");
  if (!Array.isArray(raw.links)) throw new GraphParseError("graph.links: expected an array");

  const nodes: GraphNode[] = raw.nodes.map((entry, index) => {
    const where = `graph.nodes[${index}]`;
    const node = record(entry, where);
    rejectUnknown(node, NODE_KEYS, where);
    const width = node.width === null ? null : num(node.width, `${where}.width`);
    if (width !== null && width < 1) {
      throw new GraphParseError(`${where}.width: must be at least 1`);
    }
    return {
      id: str(node.id, `${where}.id`, ID64),
      type: str(node.type, `${where}.type`, ID64),
      title: node.title === null ? null : str(node.title, `${where}.title`, { max: 200 }),
      x: num(node.x, `${where}.x`),
      y: num(node.y, `${where}.y`),
      width,
      collapsed: bool(node.collapsed, `${where}.collapsed`),
      note: str(node.note, `${where}.note`, { max: 5000 }),
      values: parseValues(node.values, `${where}.values`),
      mode: parseMode(node.mode, `${where}.mode`),
    };
  });

  const ids = new Set<string>();
  for (const node of nodes) {
    if (ids.has(node.id)) throw new GraphParseError(`graph.nodes: duplicate node id ${node.id}`);
    ids.add(node.id);
  }

  const links: GraphLink[] = raw.links.map((entry, index) => {
    const where = `graph.links[${index}]`;
    const link = record(entry, where);
    rejectUnknown(link, LINK_KEYS, where);
    const parsed: GraphLink = {
      id: str(link.id, `${where}.id`, ID64),
      from_node: str(link.from_node, `${where}.from_node`, ID64),
      from_slot: str(link.from_slot, `${where}.from_slot`, ID64),
      to_node: str(link.to_node, `${where}.to_node`, ID64),
      to_slot: str(link.to_slot, `${where}.to_slot`, ID64),
    };
    if (!ids.has(parsed.from_node)) throw new GraphParseError(`${where}.from_node: unknown node`);
    if (!ids.has(parsed.to_node)) throw new GraphParseError(`${where}.to_node: unknown node`);
    if (parsed.from_node === parsed.to_node) {
      throw new GraphParseError(`${where}: a node cannot link to itself`);
    }
    return parsed;
  });

  // The invariants applyOp enforces must also hold for data arriving from storage.
  const linkIds = new Set<string>();
  const occupiedInputs = new Set<string>();
  for (const link of links) {
    if (linkIds.has(link.id)) throw new GraphParseError(`graph.links: duplicate link id ${link.id}`);
    linkIds.add(link.id);
    const input = `${link.to_node}.${link.to_slot}`;
    if (occupiedInputs.has(input)) {
      throw new GraphParseError(`graph.links: input ${input} is connected twice`);
    }
    occupiedInputs.add(input);
  }

  // Absent, not empty, in every document written before groups existed: a graph saved by an
  // older build must still load, and it loads as a graph with no folded views.
  const rawGroups = raw.groups === undefined ? [] : raw.groups;
  if (!Array.isArray(rawGroups)) throw new GraphParseError("graph.groups: expected an array");
  const nodeIds = new Set(nodes.map((n) => n.id));
  const claimed = new Map<string, string>();
  const groups: GraphGroup[] = rawGroups.map((entry, index) => {
    const where = `graph.groups[${index}]`;
    const group = record(entry, where);
    rejectUnknown(group, GROUP_KEYS, where);
    if (!Array.isArray(group.members)) throw new GraphParseError(`${where}.members: expected an array`);
    const members = group.members.map((member, i) => {
      const id = str(member, `${where}.members[${i}]`, ID64);
      if (!nodeIds.has(id)) throw new GraphParseError(`${where}.members[${i}]: unknown node ${id}`);
      const already = claimed.get(id);
      if (already !== undefined) {
        throw new GraphParseError(`${where}.members[${i}]: node ${id} is already in group ${already}`);
      }
      claimed.set(id, str(group.id, `${where}.id`, ID64));
      return id;
    });
    return {
      id: str(group.id, `${where}.id`, ID64),
      name: str(group.name, `${where}.name`, { min: 1, max: 80 }),
      template: group.template === undefined ? "" : str(group.template, `${where}.template`, { max: 64 }),
      collapsed: bool(group.collapsed, `${where}.collapsed`),
      members,
      x: num(group.x, `${where}.x`),
      y: num(group.y, `${where}.y`),
    };
  });
  const groupIds = new Set<string>();
  for (const group of groups) {
    if (groupIds.has(group.id)) throw new GraphParseError(`graph.groups: duplicate group id ${group.id}`);
    groupIds.add(group.id);
  }

  const graph: WorkspaceGraph = {
    schema_version: 1,
    graph_id: str(raw.graph_id, "graph.graph_id", ID64),
    name: str(raw.name, "graph.name", { min: 1, max: 200 }),
    nodes: byId(nodes),
    links: byId(links),
    groups: byId(groups),
  };
  if (topoOrder(graph) === null) throw new GraphParseError("graph: the graph contains a cycle");
  return graph;
}

/** Stable JSON: keys in a fixed order so the same graph always serialises byte-identically. */
export function serializeGraph(graph: WorkspaceGraph): string {
  return JSON.stringify({
    schema_version: graph.schema_version,
    graph_id: graph.graph_id,
    name: graph.name,
    nodes: graph.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      title: n.title,
      x: n.x,
      y: n.y,
      width: n.width,
      collapsed: n.collapsed,
      note: n.note,
      values: Object.fromEntries(Object.entries(n.values).sort(([a], [b]) => a.localeCompare(b))),
      mode: n.mode,
    })),
    links: graph.links.map((l) => ({
      id: l.id,
      from_node: l.from_node,
      from_slot: l.from_slot,
      to_node: l.to_node,
      to_slot: l.to_slot,
    })),
    groups: graph.groups.map((g) => ({
      id: g.id,
      name: g.name,
      template: g.template,
      collapsed: g.collapsed,
      // Member order is meaningful — it decides the order of a folded group's ports — so it is
      // kept as it is rather than sorted like the id-keyed collections.
      members: [...g.members],
      x: g.x,
      y: g.y,
    })),
  });
}
