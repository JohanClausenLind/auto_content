/**
 * The editor's state: one graph, its history, and the current selection.
 *
 * It is a reducer on purpose. Every mutation is a pure transition over
 * {graph, history, selection}, so a burst of edits in one tick can never read a stale graph, and
 * a double-invoked updater (StrictMode) can never apply an edit twice.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import {
  canConnect as canConnectTo,
  connectOps,
  duplicateOps,
  emptyGraph,
  groupNodesOps,
  linkInto,
  makeNode,
  newId,
  nodeById,
  removeNodesOps,
  topoOrder,
  validateGraph,
  type ConnectVerdict,
  type GraphLink,
  type GraphNode,
  type GraphOp,
  type GraphProblem,
  type NodeMode,
  type WorkspaceGraph,
} from "./graphModel";
import {
  canRedo as historyCanRedo,
  canUndo as historyCanUndo,
  commit as commitHistory,
  EMPTY_HISTORY,
  redo as historyRedo,
  undo as historyUndo,
  type GraphHistory,
} from "./history";
import { findSlot, type NodeCatalog, type WidgetValue } from "./nodeDefs";
import { typesCompatible } from "./datatypes";

interface EditorState {
  readonly graph: WorkspaceGraph;
  readonly history: GraphHistory;
  readonly selection: readonly string[];
}

type Action =
  | { readonly kind: "select"; readonly node_ids: readonly string[] }
  | { readonly kind: "commit"; readonly ops: readonly GraphOp[]; readonly gesture: string | null; readonly label: string }
  | { readonly kind: "add_node"; readonly node: GraphNode; readonly link?: GraphLink }
  | { readonly kind: "remove_nodes"; readonly node_ids: readonly string[] }
  | { readonly kind: "duplicate"; readonly node_ids: readonly string[] }
  | { readonly kind: "move"; readonly moves: readonly { id: string; x: number; y: number }[]; readonly gesture: string | null }
  | { readonly kind: "undo" }
  | { readonly kind: "redo" }
  | { readonly kind: "replace_graph"; readonly graph: WorkspaceGraph };

export interface SlotRef {
  readonly node: string;
  readonly slot: string;
}

function commitOps(
  state: EditorState,
  ops: readonly GraphOp[],
  gesture: string | null,
  label: string,
): EditorState {
  if (ops.length === 0) return state;
  let result;
  try {
    result = commitHistory(state.graph, state.history, ops, { coalesce_key: gesture, label });
  } catch {
    // An op computed against a graph that changed in the same tick can no longer apply.
    // Refusing the edit (and keeping the graph) beats crashing the whole canvas.
    return state;
  }
  if (result.graph === state.graph) return state;
  return { ...state, graph: result.graph, history: result.history };
}

function reduce(state: EditorState, action: Action): EditorState {
  switch (action.kind) {
    case "select":
      return { ...state, selection: action.node_ids };
    case "commit":
      return commitOps(state, action.ops, action.gesture, action.label);
    case "add_node": {
      const ops: GraphOp[] = [{ op: "add_node", node: action.node }];
      if (action.link) ops.push({ op: "connect", link: action.link });
      const next = commitOps(state, ops, null, `add ${action.node.type}`);
      return { ...next, selection: [action.node.id] };
    }
    case "remove_nodes": {
      const ops = removeNodesOps(state.graph, action.node_ids);
      const next = commitOps(state, ops, null, "delete");
      return { ...next, selection: next.selection.filter((id) => !action.node_ids.includes(id)) };
    }
    case "duplicate": {
      const { ops, newIds } = duplicateOps(state.graph, action.node_ids, { x: 28, y: 28 });
      const next = commitOps(state, ops, null, "duplicate");
      return next === state ? state : { ...next, selection: newIds };
    }
    case "move": {
      const ops: GraphOp[] = [];
      for (const move of action.moves) {
        const node = nodeById(state.graph, move.id);
        if (!node) continue;
        const x = Math.round(move.x);
        const y = Math.round(move.y);
        if (node.x === x && node.y === y) continue;
        ops.push({ op: "move_node", node_id: move.id, x, y });
      }
      return commitOps(state, ops, action.gesture, "move");
    }
    case "undo": {
      const result = historyUndo(state.graph, state.history);
      if (result.graph === state.graph) return state;
      return { ...state, graph: result.graph, history: result.history };
    }
    case "redo": {
      const result = historyRedo(state.graph, state.history);
      if (result.graph === state.graph) return state;
      return { ...state, graph: result.graph, history: result.history };
    }
    case "replace_graph":
      return { graph: action.graph, history: EMPTY_HISTORY, selection: [] };
  }
}

export interface GraphEditor {
  readonly graph: WorkspaceGraph;
  readonly catalog: NodeCatalog;
  readonly problems: readonly GraphProblem[];
  readonly problemsByNode: ReadonlyMap<string, readonly GraphProblem[]>;
  /** Execution order, or null when the graph has a cycle. */
  readonly order: readonly string[] | null;
  readonly selection: readonly string[];
  readonly canUndo: boolean;
  readonly canRedo: boolean;

  select(nodeIds: readonly string[]): void;
  addNode(type: string, position: { x: number; y: number }, values?: Record<string, WidgetValue>): string;
  /**
   * Add a node and, in the same atomic commit, wire `from` into one of its inputs — what
   * dropping a link on empty canvas and picking a node from the search does.
   *
   * One commit rather than addNode-then-connect on purpose: `connect` reads the graph this
   * render closed over, so a caller that adds and then connects is asking about a node that
   * does not exist yet. `toSlot` names the intended input (a suggestion knows which one it
   * means); without it, or when that input cannot take the link, the first compatible input
   * wins, and `values` are set on the new node in the same commit.
   */
  addConnectedNode(
    type: string,
    position: { x: number; y: number },
    from: SlotRef,
    options?: { values?: Record<string, WidgetValue>; toSlot?: string },
  ): string | null;
  removeNodes(nodeIds: readonly string[]): void;
  removeSelected(): void;
  duplicateNodes(nodeIds: readonly string[]): void;
  moveNodes(moves: readonly { id: string; x: number; y: number }[], gesture?: string | null): void;
  /** Draw a link. The refusal reason is returned so the caller can show it. */
  connect(from: SlotRef, to: SlotRef): ConnectVerdict;
  disconnect(linkId: string): void;
  /** Detach whatever feeds this input; true when something was removed. */
  disconnectInput(nodeId: string, slot: string): boolean;
  setWidget(nodeId: string, name: string, value: WidgetValue): void;
  setNote(nodeId: string, note: string): void;
  setTitle(nodeId: string, title: string | null): void;
  setCollapsed(nodeId: string, collapsed: boolean): void;
  setWidth(nodeId: string, width: number | null): void;
  setMode(nodeId: string, mode: NodeMode): void;
  renameGraph(name: string): void;
  /**
   * Apply a batch of prepared operations as one undoable edit.
   *
   * The seam for composites the host builds and the editor cannot know about — inserting a block
   * of five nodes, their wires and the group over them is one gesture and must be one undo. Ops
   * are validated by `applyOp` exactly as every other edit is, so this widens what can be
   * expressed and not what is allowed.
   */
  apply(ops: readonly GraphOp[], label: string): void;

  // --- folded groups: a view over the same flat graph -----------------------------------------
  /** Fold nodes into one named group. Returns the group id, or null when nothing could be. */
  groupNodes(
    nodeIds: readonly string[],
    options?: { name?: string; template?: string; collapsed?: boolean },
  ): string | null;
  /** Drop the folded view. Every node and link stays. */
  ungroup(groupId: string): void;
  setGroupCollapsed(groupId: string, collapsed: boolean): void;
  renameGroup(groupId: string, name: string): void;
  /** Absolute position for the folded node; the members move by the same delta. */
  moveGroup(groupId: string, x: number, y: number): void;
  undo(): void;
  redo(): void;
  /** Load another graph. History is cleared: the old inverses no longer apply. */
  replaceGraph(graph: WorkspaceGraph): void;
  canConnect(from: SlotRef, to: SlotRef): ConnectVerdict;
}

export interface UseGraphEditorOptions {
  readonly initialGraph?: WorkspaceGraph;
  /** Called after every committed edit — persistence hangs off this. */
  readonly onChange?: (graph: WorkspaceGraph) => void;
}

export function useGraphEditor(catalog: NodeCatalog, options: UseGraphEditorOptions = {}): GraphEditor {
  const [state, dispatch] = useReducer(reduce, undefined, () => ({
    graph: options.initialGraph ?? emptyGraph(newId("graph")),
    history: EMPTY_HISTORY,
    selection: [] as readonly string[],
  }));

  // No latest-ref dance here: writing a ref during render is not safe under concurrent
  // rendering, and it is not needed — `lastNotified` already makes a re-run for a changed
  // callback identity a no-op, so `options.onChange` can be a plain dependency.
  const lastNotified = useRef(state.graph);
  const notify = options.onChange;
  useEffect(() => {
    if (lastNotified.current === state.graph) return;
    lastNotified.current = state.graph;
    notify?.(state.graph);
  }, [state.graph, notify]);

  const problems = useMemo(() => validateGraph(state.graph, catalog), [state.graph, catalog]);
  const problemsByNode = useMemo(() => {
    const map = new Map<string, GraphProblem[]>();
    for (const problem of problems) {
      if (problem.node_id === null) continue;
      const list = map.get(problem.node_id);
      if (list) list.push(problem);
      else map.set(problem.node_id, [problem]);
    }
    return map as ReadonlyMap<string, readonly GraphProblem[]>;
  }, [problems]);
  const order = useMemo(() => topoOrder(state.graph), [state.graph]);

  const commit = useCallback(
    (ops: readonly GraphOp[], gesture: string | null, label: string) =>
      dispatch({ kind: "commit", ops, gesture, label }),
    [],
  );

  const addNode = useCallback(
    (type: string, position: { x: number; y: number }, values?: Record<string, WidgetValue>) => {
      const node = makeNode(catalog, type, {
        x: Math.round(position.x),
        y: Math.round(position.y),
        ...(values === undefined ? {} : { values }),
      });
      dispatch({ kind: "add_node", node });
      return node.id;
    },
    [catalog],
  );

  const addConnectedNode = useCallback(
    (
      type: string,
      position: { x: number; y: number },
      from: SlotRef,
      options?: { values?: Record<string, WidgetValue>; toSlot?: string },
    ): string | null => {
      const def = catalog.get(type);
      if (!def) return null;
      const source = nodeById(state.graph, from.node);
      const sourceDef = source ? catalog.get(source.type) : undefined;
      const outType = sourceDef ? findSlot(sourceDef.outputs, from.slot)?.type : undefined;
      const node = makeNode(catalog, type, {
        x: Math.round(position.x),
        y: Math.round(position.y),
        ...(options?.values ? { values: options.values } : {}),
      });
      // The node is brand new, so type fit is the only connection rule that can fail.
      const wanted = options?.toSlot
        ? def.inputs.find((i) => i.name === options.toSlot)
        : undefined;
      const input =
        wanted && outType && typesCompatible(outType, wanted.type)
          ? wanted
          : outType
            ? def.inputs.find((i) => typesCompatible(outType, i.type))
            : undefined;
      const link: GraphLink | undefined = input
        ? {
            id: newId("link"),
            from_node: from.node,
            from_slot: from.slot,
            to_node: node.id,
            to_slot: input.name,
          }
        : undefined;
      dispatch({ kind: "add_node", node, ...(link ? { link } : {}) });
      return node.id;
    },
    [catalog, state.graph],
  );

  return {
    graph: state.graph,
    catalog,
    problems,
    problemsByNode,
    order,
    selection: state.selection,
    canUndo: historyCanUndo(state.history),
    canRedo: historyCanRedo(state.history),

    select: (nodeIds) => dispatch({ kind: "select", node_ids: nodeIds }),
    addNode,
    addConnectedNode,
    removeNodes: (nodeIds) => dispatch({ kind: "remove_nodes", node_ids: nodeIds }),
    removeSelected: () => dispatch({ kind: "remove_nodes", node_ids: state.selection }),
    duplicateNodes: (nodeIds) => dispatch({ kind: "duplicate", node_ids: nodeIds }),
    moveNodes: (moves, gesture = null) => dispatch({ kind: "move", moves, gesture }),
    connect: (from, to) => {
      const verdict = canConnectTo(state.graph, catalog, from, to);
      if (verdict.ok) commit(connectOps(state.graph, from, to), null, "connect");
      return verdict;
    },
    disconnect: (linkId) => commit([{ op: "disconnect", link_id: linkId }], null, "disconnect"),
    disconnectInput: (nodeId, slot) => {
      const link = linkInto(state.graph, nodeId, slot);
      if (!link) return false;
      commit([{ op: "disconnect", link_id: link.id }], null, "disconnect");
      return true;
    },
    setWidget: (nodeId, name, value) =>
      commit([{ op: "set_widget", node_id: nodeId, name, value }], `widget:${nodeId}:${name}`, "edit value"),
    setNote: (nodeId, note) => commit([{ op: "set_note", node_id: nodeId, note }], `note:${nodeId}`, "edit note"),
    setTitle: (nodeId, title) => commit([{ op: "set_title", node_id: nodeId, title }], null, "rename node"),
    setCollapsed: (nodeId, collapsed) =>
      commit([{ op: "set_collapsed", node_id: nodeId, collapsed }], null, collapsed ? "collapse" : "expand"),
    setWidth: (nodeId, width) => commit([{ op: "set_width", node_id: nodeId, width }], `width:${nodeId}`, "resize"),
    setMode: (nodeId, mode) => commit([{ op: "set_mode", node_id: nodeId, mode }], null, "change mode"),
    renameGraph: (name) => commit([{ op: "rename_graph", name }], "rename_graph", "rename graph"),
    apply: (ops, label) => commit(ops, null, label),

    groupNodes: (nodeIds, options) => {
      const id = newId("grp");
      const ops = groupNodesOps(state.graph, nodeIds, { ...options, id });
      if (ops.length === 0) return null;
      commit(ops, null, "group nodes");
      return id;
    },
    ungroup: (groupId) => commit([{ op: "remove_group", group_id: groupId }], null, "ungroup"),
    setGroupCollapsed: (groupId, collapsed) =>
      commit(
        [{ op: "set_group_collapsed", group_id: groupId, collapsed }],
        null,
        collapsed ? "fold group" : "open group",
      ),
    renameGroup: (groupId, name) =>
      commit([{ op: "rename_group", group_id: groupId, name }], null, "rename group"),
    moveGroup: (groupId, x, y) =>
      commit(
        [{ op: "move_group", group_id: groupId, x: Math.round(x), y: Math.round(y) }],
        `move_group:${groupId}`,
        "move group",
      ),
    undo: () => dispatch({ kind: "undo" }),
    redo: () => dispatch({ kind: "redo" }),
    replaceGraph: (graph) => dispatch({ kind: "replace_graph", graph }),
    canConnect: (from, to) => canConnectTo(state.graph, catalog, from, to),
  };
}

