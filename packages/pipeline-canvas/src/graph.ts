import { deliverableOf, isSharedNode, type RunNode } from "./types";

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
}

export interface RunGraph {
  nodes: RunNode[];
  edges: GraphEdge[];
}

function chainEdges(chain: readonly RunNode[], edges: GraphEdge[]): void {
  for (let i = 1; i < chain.length; i++) {
    const from = chain[i - 1];
    const to = chain[i];
    if (from && to) edges.push({ id: `${from.node_id}->${to.node_id}`, source: from.node_id, target: to.node_id });
  }
}

/**
 * Build the implicit DAG from the flat node list:
 * shared nodes form one chain in array order; each deliverable's nodes form a
 * chain in array order that hangs off the last shared node.
 */
export function buildGraph(nodes: readonly RunNode[]): RunGraph {
  const edges: GraphEdge[] = [];
  const shared = nodes.filter((n) => isSharedNode(n));
  chainEdges(shared, edges);
  const lastShared = shared.at(-1);

  const byDeliverable = new Map<string, RunNode[]>();
  for (const node of nodes) {
    const deliverable = deliverableOf(node);
    if (deliverable === null) continue;
    const chain = byDeliverable.get(deliverable);
    if (chain) chain.push(node);
    else byDeliverable.set(deliverable, [node]);
  }

  for (const chain of byDeliverable.values()) {
    const first = chain[0];
    if (lastShared && first) {
      edges.push({ id: `${lastShared.node_id}->${first.node_id}`, source: lastShared.node_id, target: first.node_id });
    }
    chainEdges(chain, edges);
  }

  return { nodes: [...nodes], edges };
}

/** One-line accessible description of a node, shared by the canvas and the list. */
export function describeRunNode(node: RunNode): string {
  const parts = [node.stage, deliverableOf(node) ?? "shared", node.state];
  if (node.cache_hit) parts.push("from cache");
  const duration = formatDuration(node.duration_ms);
  if (duration) parts.push(duration);
  if (node.attempts > 1) parts.push(`${node.attempts} attempts`);
  if (node.error) parts.push(`error: ${node.error}`);
  return parts.join(", ");
}

/** Human-readable duration: "850 ms", "1.2 s", "2 m 05 s". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1).replace(/\.0$/, "")} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes} m ${String(seconds).padStart(2, "0")} s`;
}
