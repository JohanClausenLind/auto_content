import type { ReactNode } from "react";
import { formatDuration, formatEta } from "./graph";
import { deliverableOf, type RunNode } from "./types";

export interface RunNodeListProps {
  nodes: readonly RunNode[];
  /** When present, each step is a button; when absent the list is read-only. */
  onSelect?: (node: RunNode) => void;
  selectedNodeId?: string | null;
  "aria-label"?: string;
}

function StepContent({ node }: { node: RunNode }) {
  const deliverable = deliverableOf(node);
  const duration = formatDuration(node.duration_ms);
  const eta = duration ? "" : formatEta(node.eta_seconds);
  return (
    <>
      <span className="cf-runlist__stage">{node.stage}</span>
      <span className="cf-runlist__scope">{deliverable ?? "shared"}</span>
      <span className="cf-runlist__state" data-state={node.state}>
        {node.state}
      </span>
      <span className="cf-runlist__meta">
        {node.cache_hit && <span className="cf-runlist__badge">cached</span>}
        {duration && <span className="cf-runlist__duration">{duration}</span>}
        {eta && <span className="cf-runlist__eta">{eta}</span>}
        {node.attempts > 1 && <span className="cf-runlist__attempts">{node.attempts} attempts</span>}
        {node.error && <span className="cf-runlist__error">{node.error}</span>}
      </span>
    </>
  );
}

/**
 * Plain, screen-reader-friendly rendering of the same run nodes the canvas
 * shows. Selection stays in sync with the canvas through `selectedNodeId`.
 */
export function RunNodeList({ nodes, onSelect, selectedNodeId = null, "aria-label": ariaLabel = "Pipeline steps" }: RunNodeListProps) {
  return (
    <ol className="cf-runlist" aria-label={ariaLabel}>
      {nodes.map((node) => {
        const content: ReactNode = <StepContent node={node} />;
        return (
          <li key={node.node_id} className="cf-runlist__item" data-state={node.state}>
            {onSelect ? (
              <button
                type="button"
                className="cf-runlist__row cf-runlist__row--button"
                aria-pressed={node.node_id === selectedNodeId}
                onClick={() => onSelect(node)}
              >
                {content}
              </button>
            ) : (
              <span className="cf-runlist__row">{content}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
