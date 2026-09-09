/**
 * Folded groups: a view over the flat graph.
 *
 * Two properties carry the whole feature, and they are what these tests pin.
 *
 * The first is that a group changes nothing about the graph. Fold five nodes and the nodes, the
 * links, the execution order and the validation results are identical — which is why the
 * compiler, the runner and every existing test never had to learn what a group is.
 *
 * The second is that folding cannot hide anything. A folded group's ports are derived from the
 * links every time they are asked for, so a required input nobody connected still shows on the
 * folded node, and a member rewired from outside changes the folded node's slots immediately.
 * A view that could hide a hole in the graph would be worse than no view.
 */

import { describe, expect, it } from "vitest";
import {
  applyOp,
  applyOps,
  emptyGraph,
  GraphOpError,
  GraphParseError,
  groupNodesOps,
  groupOf,
  groupPorts,
  isHidden,
  makeNode,
  parseGraph,
  parseGroupPort,
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
  for (const node of [
    makeNode(catalog, "test.brief", { id: "b", x: 0, y: 0, values: { topic: "cats" } }),
    makeNode(catalog, "test.script", { id: "s", x: 200, y: 10 }),
    makeNode(catalog, "test.video", { id: "v", x: 400, y: 20 }),
    makeNode(catalog, "test.save", { id: "o", x: 600, y: 30 }),
  ]) {
    graph = applyOp(graph, { op: "add_node", node }).graph;
  }
  for (const l of [
    link("l1", "b", "brief", "s", "brief"),
    link("l2", "s", "script", "v", "script"),
    link("l3", "v", "video", "o", "video"),
  ]) {
    graph = applyOp(graph, { op: "connect", link: l }).graph;
  }
  return graph;
}

function fold(graph: WorkspaceGraph, members: string[], name = "Middle"): WorkspaceGraph {
  return applyOps(graph, groupNodesOps(graph, members, { name, id: "grp1" })).graph;
}

describe("folded groups", () => {
  it("changes nothing about the graph it folds", () => {
    const flat = pipeline();
    const folded = fold(flat, ["s", "v"]);
    expect(folded.nodes).toEqual(flat.nodes);
    expect(folded.links).toEqual(flat.links);
    expect(topoOrder(folded)).toEqual(topoOrder(flat));
    expect(validateGraph(folded, catalog)).toEqual(validateGraph(flat, catalog));
  });

  it("takes its position from the corner of what it folds", () => {
    const group = fold(pipeline(), ["s", "v"]).groups[0]!;
    expect([group.x, group.y]).toEqual([200, 10]);
    expect(group.collapsed).toBe(true);
    expect(group.members).toEqual(["s", "v"]);
  });

  it("hides its members while it is folded and shows them when it is opened", () => {
    let graph = fold(pipeline(), ["s", "v"]);
    expect(isHidden(graph, "s")).toBe(true);
    expect(isHidden(graph, "b")).toBe(false);
    graph = applyOp(graph, { op: "set_group_collapsed", group_id: "grp1", collapsed: false }).graph;
    expect(isHidden(graph, "s")).toBe(false);
    expect(groupOf(graph, "s")?.id).toBe("grp1");
  });

  it("shows the boundary slots of its members, and only those", () => {
    const graph = fold(pipeline(), ["s", "v"]);
    const ports = groupPorts(graph, catalog, graph.groups[0]!);
    // `brief` is fed from outside the group; `script` is internal and must not appear; and
    // `video.image` is optional and unfed, so it is not a boundary of anything.
    expect(ports.inputs.map((p) => `${p.node_id}.${p.slot}`)).toEqual(["s.brief"]);
    expect(ports.outputs.map((p) => `${p.node_id}.${p.slot}`)).toEqual(["v.video"]);
    // The label names the member, because "brief" alone says nothing about which node's brief.
    expect(ports.inputs[0]!.label).toBe("Write Script · brief");
    expect(ports.inputs[0]!.type).toBe("BRIEF");
  });

  it("still shows a required input nobody connected", () => {
    // The property that makes folding safe: `video.script` is required and unfed, so the folded
    // node carries the slot and the error stays visible on it.
    let graph = pipeline();
    graph = applyOp(graph, { op: "disconnect", link_id: "l2" }).graph;
    graph = fold(graph, ["v", "o"]);
    const ports = groupPorts(graph, catalog, graph.groups[0]!);
    expect(ports.inputs.map((p) => `${p.node_id}.${p.slot}`)).toContain("v.script");
    const errors = validateGraph(graph, catalog).filter((p) => p.severity === "error");
    expect(errors.some((e) => e.node_id === "v")).toBe(true);
  });

  it("recomputes its ports when a member is rewired from outside", () => {
    // Nothing is stored: `video.video` is a boundary output only because something outside the
    // group reads it, so cutting that link takes the port away without touching the group.
    let graph = fold(pipeline(), ["s", "v"]);
    expect(groupPorts(graph, catalog, graph.groups[0]!).outputs).toHaveLength(1);
    graph = applyOp(graph, { op: "disconnect", link_id: "l3" }).graph;
    expect(groupPorts(graph, catalog, graph.groups[0]!).outputs).toHaveLength(0);
  });

  it("moves its members with it, so opening it shows them where the group is", () => {
    const graph = applyOp(fold(pipeline(), ["s", "v"]), {
      op: "move_group",
      group_id: "grp1",
      x: 1200,
      y: 400,
    }).graph;
    expect(graph.nodes.find((n) => n.id === "s")!.x).toBe(1200);
    expect(graph.nodes.find((n) => n.id === "v")!.x).toBe(1400);
    expect(graph.nodes.find((n) => n.id === "b")!.x).toBe(0);
  });

  it("undoes exactly, position included", () => {
    const folded = fold(pipeline(), ["s", "v"]);
    const moved = applyOp(folded, { op: "move_group", group_id: "grp1", x: 900, y: 900 });
    expect(applyOp(moved.graph, moved.inverse).graph).toEqual(folded);
  });

  it("refuses a node that is already in another group", () => {
    const graph = fold(pipeline(), ["s", "v"]);
    expect(() =>
      applyOp(graph, {
        op: "add_group",
        group: {
          id: "grp2",
          name: "Other",
          template: "",
          collapsed: true,
          members: ["v", "o"],
          x: 0,
          y: 0,
        },
      }),
    ).toThrow(GraphOpError);
  });

  it("ungrouping keeps every node and link", () => {
    const folded = fold(pipeline(), ["s", "v"]);
    const flat = applyOp(folded, { op: "remove_group", group_id: "grp1" }).graph;
    expect(flat.groups).toEqual([]);
    expect(flat.nodes).toEqual(folded.nodes);
    expect(flat.links).toEqual(folded.links);
  });

  it("deleting a member corrects the group, and deleting all of them removes it", () => {
    const folded = fold(pipeline(), ["s", "v"]);
    const partial = applyOps(folded, removeNodesOps(folded, ["v"])).graph;
    expect(partial.groups[0]!.members).toEqual(["s"]);
    const gone = applyOps(folded, removeNodesOps(folded, ["s", "v"])).graph;
    expect(gone.groups).toEqual([]);
  });

  it("round-trips through the serialised document", () => {
    const folded = fold(pipeline(), ["s", "v"]);
    expect(parseGraph(JSON.parse(serializeGraph(folded)))).toEqual(folded);
  });

  it("loads a document written before groups existed", () => {
    const flat = pipeline();
    const document = JSON.parse(serializeGraph(flat)) as Record<string, unknown>;
    delete document.groups;
    expect(parseGraph(document).groups).toEqual([]);
  });

  it("refuses a group that names a node the document does not have", () => {
    const document = JSON.parse(serializeGraph(pipeline())) as Record<string, unknown>;
    document.groups = [
      { id: "grp1", name: "Ghost", template: "", collapsed: true, members: ["nope"], x: 0, y: 0 },
    ];
    expect(() => parseGraph(document)).toThrow(GraphParseError);
  });

  it("refuses a document that puts one node in two groups", () => {
    const document = JSON.parse(serializeGraph(pipeline())) as Record<string, unknown>;
    document.groups = [
      { id: "g1", name: "A", template: "", collapsed: true, members: ["s"], x: 0, y: 0 },
      { id: "g2", name: "B", template: "", collapsed: true, members: ["s"], x: 0, y: 0 },
    ];
    expect(() => parseGraph(document)).toThrow(/already in group/);
  });

  it("parses a port handle back into the member and slot it stands for", () => {
    expect(parseGroupPort("in:node_a1:first_frame")).toEqual({
      node_id: "node_a1",
      slot: "first_frame",
    });
    expect(parseGroupPort("out:node_a1:video")).toEqual({ node_id: "node_a1", slot: "video" });
    // An ordinary node's handle is a bare slot name and must not be mistaken for a port.
    expect(parseGroupPort("first_frame")).toBeNull();
  });
});
