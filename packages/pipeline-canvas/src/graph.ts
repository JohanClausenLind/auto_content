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

/** An edge as reported by the run API (real dependencies from the compiled DAG). */
export interface ProvidedEdge {
  source: string;
  target: string;
}

/**
 * Build the graph. When the API provides real edges (compiled workspace graphs), they are the
 * truth; otherwise fall back to the implicit chains campaign runs follow:
 * shared nodes form one chain in array order; each deliverable's nodes form a
 * chain in array order that hangs off the last shared node.
 */
export function buildGraph(nodes: readonly RunNode[], provided?: readonly ProvidedEdge[] | null): RunGraph {
  // A provided-but-empty edge list is a real answer (an all-parallel DAG), not "unknown":
  // only null/undefined — the compiled dag.json was unavailable — falls back to guessing.
  if (provided) {
    const ids = new Set(nodes.map((n) => n.node_id));
    const edges: GraphEdge[] = provided
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ id: `${e.source}->${e.target}`, source: e.source, target: e.target }));
    return { nodes: [...nodes], edges };
  }
  return buildImplicitGraph(nodes);
}

function buildImplicitGraph(nodes: readonly RunNode[]): RunGraph {
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
  const eta = formatEta(node.eta_seconds);
  if (eta) parts.push(`${eta} to go`);
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

/**
 * An estimate, phrased as one. "~30 s", "~4 min", "~1.2 h" — coarse on purpose, because the
 * spread behind these medians is real and a figure like "3 m 47 s" reads as a promise. Empty
 * string for no estimate, so a caller can `&&` it into place like `formatDuration`.
 */
export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 1) return "~any moment";
  if (seconds < 90) return `~${Math.round(seconds)} s`;
  if (seconds < 5400) return `~${Math.round(seconds / 60)} min`;
  return `~${(seconds / 3600).toFixed(1)} h`;
}
