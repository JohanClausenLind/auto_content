/** Node states as reported by `GET /v1/runs/{run_id}`. */
export type RunNodeState = "queued" | "running" | "complete" | "failed" | "blocked" | "skipped";

/**
 * One pipeline node from the run detail response.
 * `node_id` is `"stage"` for shared nodes or `"stage:deliverableId"` for per-deliverable nodes.
 */
export interface RunNode {
  node_id: string;
  stage: string;
  deliverable_id: string | null;
  state: RunNodeState;
  attempts: number;
  cache_hit: boolean;
  duration_ms: number | null;
  error: string | null;
  /**
   * Seconds this node is still expected to take: the median of what this machine actually
   * measured for the stage, minus whatever a running node has already used. Null for a finished
   * node (it has a real `duration_ms` — an estimate would be replacing a measurement with a
   * guess) and for a stage with no history at all. Optional because a hand-built graph has no run
   * behind it to estimate from.
   */
  eta_seconds?: number | null;
  /** How many past runs the estimate is a median of. One sample is not three. */
  eta_samples?: number;
}

/** The deliverable a node belongs to, or null for shared nodes. */
export function deliverableOf(node: Pick<RunNode, "node_id" | "deliverable_id">): string | null {
  if (node.deliverable_id != null && node.deliverable_id !== "") return node.deliverable_id;
  const colon = node.node_id.indexOf(":");
  return colon > 0 ? node.node_id.slice(colon + 1) : null;
}

export function isSharedNode(node: Pick<RunNode, "node_id" | "deliverable_id">): boolean {
  return deliverableOf(node) === null;
}
