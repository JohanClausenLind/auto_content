import { describe, expect, it } from "vitest";
import { applyOp, emptyGraph, makeNode, type WorkspaceGraph } from "../src/graphModel";
import { canRedo, canUndo, commit, EMPTY_HISTORY, HISTORY_LIMIT, redo, undo } from "../src/history";
import { catalog } from "./fixtures";

function seeded(): WorkspaceGraph {
  return applyOp(emptyGraph("h1"), {
    op: "add_node",
    node: makeNode(catalog, "test.script", { id: "s", x: 0, y: 0 }),
  }).graph;
}

describe("history", () => {
  it("undo restores and redo replays", () => {
    const graph = seeded();
    const a = commit(graph, EMPTY_HISTORY, [{ op: "move_node", node_id: "s", x: 10, y: 10 }]);
    const b = commit(a.graph, a.history, [{ op: "set_note", node_id: "s", note: "hi" }]);
    expect(canUndo(b.history)).toBe(true);

    const undone = undo(b.graph, b.history);
    expect(undone.graph.nodes[0]?.note).toBe("");
    expect(undone.graph.nodes[0]?.x).toBe(10);
    expect(canRedo(undone.history)).toBe(true);

    const redone = redo(undone.graph, undone.history);
    expect(redone.graph).toEqual(b.graph);

    const back = undo(undo(redone.graph, redone.history).graph, undo(redone.graph, redone.history).history);
    expect(back.graph).toEqual(graph);
  });

  it("coalesces one gesture into one undo step", () => {
    const graph = seeded();
    let state = { graph, history: EMPTY_HISTORY };
    for (const x of [1, 2, 3, 4, 5]) {
      state = commit(state.graph, state.history, [{ op: "move_node", node_id: "s", x, y: 0 }], {
        coalesce_key: "drag-1",
      });
    }
    expect(state.history.past).toHaveLength(1);
    const undone = undo(state.graph, state.history);
    expect(undone.graph.nodes[0]?.x).toBe(0);
  });

  it("a fresh edit severs redo", () => {
    const graph = seeded();
    const a = commit(graph, EMPTY_HISTORY, [{ op: "set_note", node_id: "s", note: "one" }]);
    const undone = undo(a.graph, a.history);
    const b = commit(undone.graph, undone.history, [{ op: "set_note", node_id: "s", note: "two" }]);
    expect(canRedo(b.history)).toBe(false);
  });

  it("keeps at most HISTORY_LIMIT entries", () => {
    const graph = seeded();
    let state = { graph, history: EMPTY_HISTORY };
    for (let i = 0; i < HISTORY_LIMIT + 20; i++) {
      state = commit(state.graph, state.history, [{ op: "move_node", node_id: "s", x: i, y: 0 }]);
    }
    expect(state.history.past.length).toBe(HISTORY_LIMIT);
  });
});
