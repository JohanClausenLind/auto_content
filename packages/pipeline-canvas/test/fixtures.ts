import type { RunNode } from "../src/types";

export function node(overrides: Partial<RunNode> & Pick<RunNode, "node_id" | "stage">): RunNode {
  return {
    deliverable_id: null,
    state: "queued",
    attempts: 1,
    cache_hit: false,
    duration_ms: null,
    error: null,
    ...overrides,
  };
}

/** Two shared stages, then two deliverables with two stages each. */
export const RUN_NODES: RunNode[] = [
  node({ node_id: "research", stage: "research", state: "complete", cache_hit: true, duration_ms: 850 }),
  node({ node_id: "plan", stage: "plan", state: "complete", duration_ms: 1200 }),
  node({ node_id: "script:d1", stage: "script", deliverable_id: "d1", state: "running", attempts: 2, eta_seconds: 42, eta_samples: 9 }),
  node({ node_id: "render:d1", stage: "render", deliverable_id: "d1", state: "queued", eta_seconds: 600, eta_samples: 3 }),
  node({ node_id: "script:d2", stage: "script", deliverable_id: "d2", state: "failed", error: "voice model unavailable", duration_ms: 64_000 }),
  node({ node_id: "render:d2", stage: "render", deliverable_id: "d2", state: "blocked" }),
];
