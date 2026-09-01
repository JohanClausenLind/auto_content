import { describe, expect, it } from "vitest";
import { buildGraph } from "../src/graph";
import { layoutGraph, layoutRunNodes, NODE_HEIGHT, NODE_WIDTH } from "../src/layout";
import { RUN_NODES } from "./fixtures";

describe("ELK layered layout", () => {
  it("positions every node with the configured size", async () => {
    const laidOut = await layoutRunNodes(RUN_NODES);
    expect(laidOut.nodes).toHaveLength(RUN_NODES.length);
    for (const placed of laidOut.nodes) {
      expect(placed.width).toBe(NODE_WIDTH);
      expect(placed.height).toBe(NODE_HEIGHT);
      expect(Number.isFinite(placed.x)).toBe(true);
      expect(Number.isFinite(placed.y)).toBe(true);
    }
    expect(laidOut.width).toBeGreaterThan(0);
    expect(laidOut.height).toBeGreaterThan(0);
  });

  it("lays chains out left to right (x grows along every edge)", async () => {
    const graph = buildGraph(RUN_NODES);
    const laidOut = await layoutGraph(graph);
    const xById = new Map(laidOut.nodes.map((p) => [p.node.node_id, p.x]));
    for (const edge of laidOut.edges) {
      const sourceX = xById.get(edge.source);
      const targetX = xById.get(edge.target);
      expect(sourceX).toBeDefined();
      expect(targetX).toBeDefined();
      expect(targetX!).toBeGreaterThan(sourceX!);
    }
  });

  it("separates parallel deliverable chains vertically", async () => {
    const laidOut = await layoutRunNodes(RUN_NODES);
    const byId = new Map(laidOut.nodes.map((p) => [p.node.node_id, p]));
    const d1 = byId.get("script:d1");
    const d2 = byId.get("script:d2");
    expect(d1).toBeDefined();
    expect(d2).toBeDefined();
    expect(Math.abs(d1!.y - d2!.y)).toBeGreaterThanOrEqual(NODE_HEIGHT);
  });
});
