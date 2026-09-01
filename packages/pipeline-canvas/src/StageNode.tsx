import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo, type KeyboardEvent } from "react";
import { describeRunNode, formatDuration } from "./graph";
import { deliverableOf, type RunNode } from "./types";

export interface StageNodeData extends Record<string, unknown> {
  run: RunNode;
  /** Whether selecting nodes does anything on this canvas. */
  interactive: boolean;
  isSelected: boolean;
  onSelect?: (node: RunNode) => void;
}

export type StageFlowNode = Node<StageNodeData, "stage">;

export const StageNode = memo(function StageNode({ data }: NodeProps<StageFlowNode>) {
  const { run, interactive, isSelected, onSelect } = data;
  const deliverable = deliverableOf(run);
  const duration = formatDuration(run.duration_ms);

  const select = () => onSelect?.(run);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select();
    }
  };

  return (
    <div
      className="cf-runnode"
      data-state={run.state}
      data-interactive={interactive || undefined}
      data-selected={isSelected || undefined}
      {...(interactive
        ? { role: "button", tabIndex: 0, "aria-pressed": isSelected, onClick: select, onKeyDown }
        : {})}
      aria-label={describeRunNode(run)}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className="cf-runnode__handle" />
      <span className="cf-runnode__stage" aria-hidden="true">
        {run.stage}
      </span>
      <span className="cf-runnode__scope" aria-hidden="true">
        {deliverable ?? "shared"}
      </span>
      <span className="cf-runnode__meta" aria-hidden="true">
        {run.state === "complete" && (
          <span className="cf-runnode__check">
            {"✓"}
          </span>
        )}
        {run.state === "failed" && <span className="cf-runnode__cross">{"✕"}</span>}
        <span className="cf-runnode__state">{run.state}</span>
        {run.cache_hit && <span className="cf-runnode__badge">cached</span>}
        {duration && <span className="cf-runnode__duration">{duration}</span>}
      </span>
      <Handle type="source" position={Position.Right} isConnectable={false} className="cf-runnode__handle" />
    </div>
  );
});
