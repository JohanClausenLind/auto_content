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
