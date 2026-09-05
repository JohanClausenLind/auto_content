import type ElkConstructor from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk.bundled.js";
import { buildGraph, type GraphEdge, type RunGraph } from "./graph";
import type { RunNode } from "./types";

export const NODE_WIDTH = 208;
export const NODE_HEIGHT = 78;

export interface PositionedRunNode {
  node: RunNode;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LaidOutGraph {
  nodes: PositionedRunNode[];
  edges: GraphEdge[];
  width: number;
  height: number;
}

/** elkjs is ~1.4 MB, so it is loaded on demand the first time a layout runs. */
let elkInstance: InstanceType<typeof ElkConstructor> | null = null;
async function getElk(): Promise<InstanceType<typeof ElkConstructor>> {
  if (!elkInstance) {
    const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
    elkInstance = new ELK();
  }
  return elkInstance;
}

/** Position a run graph left-to-right with ELK's layered algorithm. */
export async function layoutGraph(graph: RunGraph): Promise<LaidOutGraph> {
  const root: ElkNode = {
    id: "run",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "56",
      "elk.spacing.nodeNode": "24",
      "elk.padding": "[top=8,left=8,bottom=8,right=8]",
    },
    children: graph.nodes.map((n) => ({ id: n.node_id, width: NODE_WIDTH, height: NODE_HEIGHT })),
    edges: graph.edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };
  const elk = await getElk();
  const result = await elk.layout(root);
  const byId = new Map((result.children ?? []).map((child) => [child.id, child]));
  const nodes: PositionedRunNode[] = graph.nodes.map((node) => {
    const placed = byId.get(node.node_id);
    return {
      node,
      x: placed?.x ?? 0,
      y: placed?.y ?? 0,
      width: placed?.width ?? NODE_WIDTH,
      height: placed?.height ?? NODE_HEIGHT,
    };
  });
  return { nodes, edges: graph.edges, width: result.width ?? 0, height: result.height ?? 0 };
}

/** Convenience: build the DAG from the API node list (+ real edges when given) and lay it out. */
export async function layoutRunNodes(
  nodes: readonly RunNode[],
  edges?: readonly import("./graph").ProvidedEdge[] | null,
): Promise<LaidOutGraph> {
  return layoutGraph(buildGraph(nodes, edges));
}
