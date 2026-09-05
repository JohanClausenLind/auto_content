/**
 * Undo/redo as stacks of inverse operations.
 *
 * A history entry is (ops, inverse) so undo replays the inverse and redo replays the original —
 * no snapshots, so memory stays flat no matter how large the graph grows.
 *
 * `coalesce_key` merges consecutive entries that belong to one gesture: dragging a node emits a
 * move per pointermove, and typing emits a set_widget per keystroke; both should undo in one step.
 */

import { applyOps, type GraphOp, type WorkspaceGraph } from "./graphModel";

export interface HistoryEntry {
  readonly ops: readonly GraphOp[];
  readonly inverse: readonly GraphOp[];
  readonly coalesce_key: string | null;
  readonly label: string;
}

export interface GraphHistory {
  readonly past: readonly HistoryEntry[];
  readonly future: readonly HistoryEntry[];
}

export const EMPTY_HISTORY: GraphHistory = { past: [], future: [] };

/** How many gestures stay undoable. Beyond this the oldest entry is dropped. */
export const HISTORY_LIMIT = 200;

export interface CommitOptions {
  readonly coalesce_key?: string | null;
  readonly label?: string;
}

export interface CommitResult {
  readonly graph: WorkspaceGraph;
  readonly history: GraphHistory;
}

/** Apply ops and record them. Throws (leaving both graph and history untouched) if any op fails. */
export function commit(
  graph: WorkspaceGraph,
  history: GraphHistory,
  ops: readonly GraphOp[],
  options: CommitOptions = {},
): CommitResult {
  if (ops.length === 0) return { graph, history };
  const { graph: next, inverse } = applyOps(graph, ops);
  const coalesceKey = options.coalesce_key ?? null;
  const label = options.label ?? ops[0]?.op ?? "edit";
  const previous = history.past.at(-1);

  // Same gesture: keep the original inverse (it restores the state before the gesture began).
  if (coalesceKey !== null && previous && previous.coalesce_key === coalesceKey) {
    const merged: HistoryEntry = {
      ops: [...previous.ops, ...ops],
      inverse: [...inverse, ...previous.inverse],
      coalesce_key: coalesceKey,
      label: previous.label,
    };
    return { graph: next, history: { past: [...history.past.slice(0, -1), merged], future: [] } };
  }

  const entry: HistoryEntry = { ops, inverse, coalesce_key: coalesceKey, label };
  const past = [...history.past, entry].slice(-HISTORY_LIMIT);
  return { graph: next, history: { past, future: [] } };
}

export function canUndo(history: GraphHistory): boolean {
  return history.past.length > 0;
}

export function canRedo(history: GraphHistory): boolean {
  return history.future.length > 0;
}

export function undo(graph: WorkspaceGraph, history: GraphHistory): CommitResult {
  const entry = history.past.at(-1);
  if (!entry) return { graph, history };
  const { graph: next } = applyOps(graph, entry.inverse);
  return {
    graph: next,
    history: { past: history.past.slice(0, -1), future: [...history.future, entry] },
  };
}

export function redo(graph: WorkspaceGraph, history: GraphHistory): CommitResult {
  const entry = history.future.at(-1);
  if (!entry) return { graph, history };
  const { graph: next } = applyOps(graph, entry.ops);
  return {
    graph: next,
    history: { past: [...history.past, entry], future: history.future.slice(0, -1) },
  };
}
