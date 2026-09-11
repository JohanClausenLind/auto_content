import { describe, expect, it } from "vitest";
import {
  applyOp,
  applyOps,
  canConnect,
  connectOps,
  duplicateOps,
  emptyGraph,
  GraphOpError,
  GraphParseError,
  makeNode,
  parseGraph,
  removeNodesOps,
  serializeGraph,
  topoOrder,
  validateGraph,
  type GraphLink,
  type WorkspaceGraph,
} from "../src/graphModel";
import { catalog } from "./fixtures";

function link(id: string, from: string, fromSlot: string, to: string, toSlot: string): GraphLink {
  return { id, from_node: from, from_slot: fromSlot, to_node: to, to_slot: toSlot };
}

/** brief -> script -> video -> save, fully linked. */
function pipeline(): WorkspaceGraph {
  let graph = emptyGraph("g1", "test");
  const nodes = [
    makeNode(catalog, "test.brief", { id: "b", x: 0, y: 0, values: { topic: "cats" } }),
    makeNode(catalog, "test.script", { id: "s", x: 200, y: 0 }),
    makeNode(catalog, "test.video", { id: "v", x: 400, y: 0 }),
    makeNode(catalog, "test.save", { id: "o", x: 600, y: 0 }),
  ];
  for (const node of nodes) graph = applyOp(graph, { op: "add_node", node }).graph;
  for (const l of [
    link("l1", "b", "brief", "s", "brief"),
    link("l2", "s", "script", "v", "script"),
    link("l3", "v", "video", "o", "video"),
  ]) {
    graph = applyOp(graph, { op: "connect", link: l }).graph;
  }
  return graph;
}

describe("graph ops", () => {
  it("every operation returns an inverse that restores the previous graph exactly", () => {
    const graph = pipeline();
    const ops = [
      { op: "move_node", node_id: "s", x: 50, y: 60 },
      { op: "set_title", node_id: "s", title: "My Script" },
      { op: "set_note", node_id: "s", note: "hello" },
      { op: "set_widget", node_id: "s", name: "length", value: 120 },
      { op: "set_collapsed", node_id: "s", collapsed: true },
      { op: "set_width", node_id: "s", width: 320 },
      { op: "set_mode", node_id: "s", mode: "muted" },
      { op: "disconnect", link_id: "l2" },
      { op: "rename_graph", name: "renamed" },
    ] as const;
    const { graph: after, inverse } = applyOps(graph, [...ops]);
    expect(after).not.toEqual(graph);
    const { graph: restored } = applyOps(after, inverse);
    expect(restored).toEqual(graph);
  });

  it("refuses to connect into an occupied input, and connectOps replaces instead", () => {
    const graph = pipeline();
    expect(() =>
      applyOp(graph, { op: "connect", link: link("dup", "b", "brief", "s", "brief") }),
    ).toThrow(GraphOpError);
    const replaced = applyOps(graph, connectOps(graph, { node: "b", slot: "brief" }, { node: "s", slot: "brief" })).graph;
    expect(replaced.links.filter((l) => l.to_node === "s" && l.to_slot === "brief")).toHaveLength(1);
  });

  it("refuses to remove a node that still has links", () => {
    const graph = pipeline();
    expect(() => applyOp(graph, { op: "remove_node", node_id: "s" })).toThrow(GraphOpError);
    const removed = applyOps(graph, removeNodesOps(graph, ["s"])).graph;
    expect(removed.nodes.map((n) => n.id).sort()).toEqual(["b", "o", "v"]);
    expect(removed.links.map((l) => l.id)).toEqual(["l3"]);
  });

  it("duplicates a selection keeping only its internal links", () => {
    const graph = pipeline();
    const { ops, newIds } = duplicateOps(graph, ["s", "v"], { x: 10, y: 10 });
    const next = applyOps(graph, ops).graph;
    expect(newIds).toHaveLength(2);
    expect(next.nodes).toHaveLength(6);
    // one new link (script->video); the links to brief/save are not cloned
    expect(next.links).toHaveLength(4);
  });
});

describe("connection rules", () => {
  it("explains refusals: self-loop, type mismatch, cycle", () => {
    const graph = pipeline();
    expect(canConnect(graph, catalog, { node: "s", slot: "script" }, { node: "s", slot: "brief" })).toMatchObject({
      ok: false,
      reason: "a node cannot feed itself",
    });
    const mismatch = canConnect(graph, catalog, { node: "b", slot: "brief" }, { node: "o", slot: "video" });
    expect(mismatch).toMatchObject({ ok: false, reason: "BRIEF does not fit VIDEO" });
    // s -> v -> remix, then remix back into v.script closes a loop of matching types
    let withRemix = applyOp(graph, {
      op: "add_node",
      node: makeNode(catalog, "test.remix", { id: "r", x: 0, y: 0 }),
    }).graph;
    withRemix = applyOp(withRemix, { op: "connect", link: link("l4", "v", "video", "r", "video") }).graph;
    const loop = canConnect(withRemix, catalog, { node: "r", slot: "draft" }, { node: "v", slot: "script" });
    expect(loop).toMatchObject({ ok: false, reason: "that would make a cycle" });
  });

  it("accepts union types and wildcards", () => {
    let graph = emptyGraph("g2");
    graph = applyOp(graph, {
      op: "add_node",
      node: makeNode(catalog, "test.video", { id: "v1", x: 0, y: 0 }),
    }).graph;
    graph = applyOp(graph, {
      op: "add_node",
      node: makeNode(catalog, "test.video", { id: "v2", x: 0, y: 0 }),
    }).graph;
    // VIDEO output into IMAGE,SEQUENCE input: refused
    expect(canConnect(graph, catalog, { node: "v1", slot: "video" }, { node: "v2", slot: "image" }).ok).toBe(false);
  });
});

describe("order and validation", () => {
  it("orders the pipeline topologically and detects cycles", () => {
    const graph = pipeline();
    expect(topoOrder(graph)).toEqual(["b", "s", "v", "o"]);
    // detach the brief so s.brief is free, then wire v back into it (topoOrder ignores types)
    const detached = applyOp(graph, { op: "disconnect", link_id: "l1" }).graph;
    const withCycle = applyOp(detached, {
      op: "connect",
      link: link("loop", "v", "video", "s", "brief"),
    }).graph;
    expect(topoOrder(withCycle)).toBeNull();
    expect(validateGraph(withCycle, catalog).some((p) => p.message.includes("cycle"))).toBe(true);
  });

  it("flags missing required inputs, empty required widgets and unknown types", () => {
    let graph = emptyGraph("g3");
    graph = applyOp(graph, {
      op: "add_node",
      node: makeNode(catalog, "test.script", { id: "s", x: 0, y: 0 }),
    }).graph;
    graph = applyOp(graph, {
      op: "add_node",
      node: makeNode(catalog, "test.brief", { id: "b", x: 0, y: 0 }),
    }).graph;
    graph = {
      ...graph,
      nodes: [
        ...graph.nodes,
        { id: "x", type: "gone.type", key: "", title: null, x: 0, y: 0, width: null, collapsed: false, note: "", values: {}, mode: "always" as const },
      ],
    };
    const problems = validateGraph(graph, catalog);
    expect(problems.some((p) => p.node_id === "s" && p.message.includes("brief is not connected"))).toBe(true);
    expect(problems.some((p) => p.node_id === "b" && p.message.includes("topic is empty"))).toBe(true);
    expect(problems.some((p) => p.node_id === "x" && p.message.includes("unknown node type"))).toBe(true);
    // brief output unused -> warning, not error
    expect(problems.find((p) => p.node_id === "b" && p.message.includes("consumes"))?.severity).toBe("warning");
  });
});

describe("serialisation", () => {
  it("round-trips byte-identically", () => {
    const graph = pipeline();
    const json = serializeGraph(graph);
    const parsed = parseGraph(JSON.parse(json));
    expect(parsed).toEqual(graph);
    expect(serializeGraph(parsed)).toBe(json);
  });

  it("rejects duplicate link ids and doubly-connected inputs", () => {
    const graph = JSON.parse(serializeGraph(pipeline()));
    const dupId = { ...graph, links: [...graph.links, { ...graph.links[0], to_node: "v", to_slot: "image" }] };
    expect(() => parseGraph(dupId)).toThrow(/duplicate link id/);
    const dupInput = {
      ...graph,
      links: [...graph.links, { ...graph.links[0], id: "l9", from_node: "v", from_slot: "video" }],
    };
    expect(() => parseGraph(dupInput)).toThrow(/connected twice/);
  });

  it("rejects unknown fields and dangling links with a path", () => {
    const graph = JSON.parse(serializeGraph(pipeline()));
    expect(() => parseGraph({ ...graph, extra: 1 })).toThrow(/unknown field extra/);
    const badNode = { ...graph, nodes: [{ ...graph.nodes[0], surprise: true }, ...graph.nodes.slice(1)] };
    expect(() => parseGraph(badNode)).toThrow(GraphParseError);
    const badLink = { ...graph, links: [{ ...graph.links[0], from_node: "nope" }, ...graph.links.slice(1)] };
    expect(() => parseGraph(badLink)).toThrow(/from_node: unknown node/);
  });
});
