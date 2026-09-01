import { ReactFlow, type Edge } from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";
import { buildGraph } from "./graph";
import { layoutGraph, type LaidOutGraph } from "./layout";
import { StageNode, type StageFlowNode } from "./StageNode";
import type { RunNode } from "./types";

export interface RunCanvasProps {
  /** Nodes exactly as returned by `GET /v1/runs/{run_id}`. */
  nodes: readonly RunNode[];
  /** When present, nodes are clickable/focusable; when absent the canvas is read-only. */
  onSelect?: (node: RunNode) => void;
  selectedNodeId?: string | null;
  "aria-label"?: string;
}

const nodeTypes = { stage: StageNode };

/**
 * Left-to-right run DAG rendered with React Flow, positioned by ELK (layered).
 * Pair it with `<RunNodeList>` for screen readers and small screens.
 */
export function RunCanvas({ nodes, onSelect, selectedNodeId = null, "aria-label": ariaLabel = "Pipeline graph" }: RunCanvasProps) {
  const graph = useMemo(() => buildGraph(nodes), [nodes]);
  const [layout, setLayout] = useState<LaidOutGraph | null>(null);

  useEffect(() => {
    let cancelled = false;
    void layoutGraph(graph).then((laidOut) => {
      if (!cancelled) setLayout(laidOut);
    });
    return () => {
      cancelled = true;
    };
  }, [graph]);

  const flowNodes: StageFlowNode[] = useMemo(
    () =>
      (layout?.nodes ?? []).map((placed) => ({
        id: placed.node.node_id,
        type: "stage" as const,
        position: { x: placed.x, y: placed.y },
        width: placed.width,
        height: placed.height,
        draggable: false,
        connectable: false,
        selectable: false,
        focusable: false,
        data: {
          run: placed.node,
          interactive: onSelect !== undefined,
          isSelected: placed.node.node_id === selectedNodeId,
          ...(onSelect ? { onSelect } : {}),
        },
      })),
    [layout, onSelect, selectedNodeId],
  );

  const flowEdges: Edge[] = useMemo(
    () => (layout?.edges ?? []).map((edge) => ({ ...edge, focusable: false, selectable: false })),
    [layout],
  );

  return (
    <div className="cf-runcanvas" role="region" aria-label={ariaLabel} data-interactive={onSelect !== undefined || undefined}>
      {layout && (
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.2}
          maxZoom={1.5}
          nodesDraggable={false}
          nodesConnectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          elementsSelectable={false}
          zoomOnDoubleClick={false}
          deleteKeyCode={null}
          selectionKeyCode={null}
          multiSelectionKeyCode={null}
        />
      )}
    </div>
  );
}
