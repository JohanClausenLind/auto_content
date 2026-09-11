import { describe, expect, it } from "vitest";
import { buildGraph, describeRunNode, formatDuration, formatEta } from "../src/graph";
import { deliverableOf, isSharedNode } from "../src/types";
import { node, RUN_NODES } from "./fixtures";

describe("buildGraph", () => {
  it("chains shared nodes and hangs each deliverable chain off the last shared node", () => {
    const graph = buildGraph(RUN_NODES);
    expect(graph.nodes).toHaveLength(6);
    expect(graph.edges.map((e) => `${e.source}->${e.target}`).sort()).toEqual(
      [
        "research->plan",
        "plan->script:d1",
        "script:d1->render:d1",
        "plan->script:d2",
        "script:d2->render:d2",
      ].sort(),
    );
  });

  it("handles a run with no shared nodes: independent deliverable chains", () => {
    const nodes = [
      node({ node_id: "script:d1", stage: "script", deliverable_id: "d1" }),
      node({ node_id: "render:d1", stage: "render", deliverable_id: "d1" }),
      node({ node_id: "script:d2", stage: "script", deliverable_id: "d2" }),
    ];
    const graph = buildGraph(nodes);
    expect(graph.edges.map((e) => e.id)).toEqual(["script:d1->render:d1"]);
  });

  it("handles a shared-only run and an empty run", () => {
    const shared = [node({ node_id: "a", stage: "a" }), node({ node_id: "b", stage: "b" })];
    expect(buildGraph(shared).edges).toEqual([{ id: "a->b", source: "a", target: "b" }]);
    expect(buildGraph([])).toEqual({ nodes: [], edges: [] });
  });

  it("falls back to parsing node_id when deliverable_id is missing", () => {
    const n = node({ node_id: "render:d9", stage: "render" });
    expect(deliverableOf(n)).toBe("d9");
    expect(isSharedNode(n)).toBe(false);
    expect(isSharedNode(node({ node_id: "plan", stage: "plan" }))).toBe(true);
  });
});

describe("formatDuration", () => {
  it("formats ms, seconds and minutes", () => {
    expect(formatDuration(null)).toBe("");
    expect(formatDuration(850)).toBe("850 ms");
    expect(formatDuration(1234)).toBe("1.2 s");
    expect(formatDuration(2000)).toBe("2 s");
    expect(formatDuration(125_000)).toBe("2 m 05 s");
  });
});

describe("describeRunNode", () => {
  it("includes scope, state, cache and error", () => {
    const failed = RUN_NODES[4];
    expect(failed).toBeDefined();
    expect(describeRunNode(failed!)).toBe("script, d2, failed, 1 m 04 s, error: voice model unavailable");
    const cached = RUN_NODES[0];
    expect(describeRunNode(cached!)).toBe("research, shared, complete, from cache, 850 ms");
  });
});

describe("provided edges", () => {
  it("real edges from the API replace the implicit chain heuristic", () => {
    const nodes = [
      { node_id: "plan_story", stage: "plan_story", deliverable_id: null, state: "complete", attempts: 1, cache_hit: false, duration_ms: 10, error: null },
      { node_id: "generate_video:g", stage: "generate_video", deliverable_id: "dlv_x", state: "complete", attempts: 1, cache_hit: false, duration_ms: 10, error: null },
      { node_id: "render_animation:a", stage: "render_animation", deliverable_id: "dlv_x", state: "complete", attempts: 1, cache_hit: false, duration_ms: 10, error: null },
    ] as const;
    const provided = [
      { source: "plan_story", target: "generate_video:g" },
      { source: "plan_story", target: "render_animation:a" },
      { source: "ghost", target: "plan_story" }, // dangling: dropped, never drawn
    ];
    const graph = buildGraph([...nodes], provided);
    expect(graph.edges.map((e) => e.id).sort()).toEqual([
      "plan_story->generate_video:g",
      "plan_story->render_animation:a",
    ]);
    // fan-out, not a chain: the implicit heuristic would have chained g -> a
    const implicit = buildGraph([...nodes]);
    expect(implicit.edges.some((e) => e.source === "generate_video:g")).toBe(true);
  });
});

describe("formatEta", () => {
  it("phrases an estimate as one, coarsely, and says nothing when it has nothing to say", () => {
    // Coarse on purpose: the medians behind these have a real spread, and "3 m 47 s" reads as a
    // promise the estimator never made.
    expect(formatEta(42)).toBe("~42 s");
    expect(formatEta(89)).toBe("~89 s");
    expect(formatEta(90)).toBe("~2 min");
    expect(formatEta(608)).toBe("~10 min");
    expect(formatEta(7200)).toBe("~2.0 h");
    // "0 left" on a stage still running is not "done", it is imminent.
    expect(formatEta(0.4)).toBe("~any moment");
    // No estimate is the empty string, so a caller can && it into place like formatDuration.
    expect(formatEta(null)).toBe("");
    expect(formatEta(undefined)).toBe("");
    expect(formatEta(-5)).toBe("");
    expect(formatEta(Number.NaN)).toBe("");
  });

  it("puts the estimate in the accessible description of an unfinished node", () => {
    const running = node({ node_id: "script:d1", stage: "script", state: "running", eta_seconds: 42 });
    expect(describeRunNode(running)).toContain("~42 s to go");
    const done = node({ node_id: "plan", stage: "plan", state: "complete", duration_ms: 1200 });
    expect(describeRunNode(done)).not.toContain("to go");
  });
});
