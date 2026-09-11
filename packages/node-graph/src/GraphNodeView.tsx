/**
 * One node on the canvas, drawn the way a node graph draws them: a 30px title bar with a
 * collapse chevron and an editable name, slot rows with coloured dots straddling the node edge,
 * widget pills, then the free-text note underneath. Geometry and palette follow litegraph
 * (30px title, 20px slots, 8px radius) so the canvas reads like the editors people know.
 */

import { Handle, Position, useReactFlow, type Node, type NodeProps } from "@xyflow/react";
import { memo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { slotColors, splitTypes, typesCompatible } from "./datatypes";
import { useEditorContext } from "./editorContext";
import type { GraphNode, GraphProblem } from "./graphModel";
import {
  DEFAULT_NODE_WIDTH,
  MAX_NODE_WIDTH,
  MIN_NODE_WIDTH,
  visibleWidgets,
  type NodeDefinition,
  type SlotSpec,
} from "./nodeDefs";
import { useAutoGrowTextarea, WidgetRow } from "./widgets";

export interface GraphNodeData extends Record<string, unknown> {
  readonly node: GraphNode;
  readonly def: NodeDefinition | null;
  readonly problems: readonly GraphProblem[];
  /** Live run state when the canvas mirrors an actual run. */
  readonly status?: {
    readonly state: "queued" | "running" | "complete" | "failed" | "blocked" | "skipped";
    readonly progress?: number;
    readonly detail?: string;
  };
}

export type GraphFlowNode = Node<GraphNodeData, "cfNode">;

function SlotDot({ type }: { type: string }) {
  const colors = slotColors(type);
  const first = colors[0] ?? "#AAAAAA";
  if (colors.length <= 1) {
    return <span className="ng-slot__dot" style={{ backgroundColor: first }} aria-hidden="true" />;
  }
  const stripes = colors
    .map((color, i) => `${color} ${(i / colors.length) * 100}% ${((i + 1) / colors.length) * 100}%`)
    .join(", ");
  return <span className="ng-slot__dot" style={{ background: `conic-gradient(${stripes})` }} aria-hidden="true" />;
}

function InputRow({ node, slot }: { node: GraphNode; slot: SlotSpec }) {
  const { editor, readOnly, connecting } = useEditorContext();
  const connected = editor.graph.links.some((l) => l.to_node === node.id && l.to_slot === slot.name);
  const compatible =
    connecting !== null && connecting.node !== node.id && typesCompatible(connecting.type, slot.type);
  const dimmed = connecting !== null && !compatible;
  return (
    <div
      className="ng-slot ng-slot--input"
      data-connected={connected || undefined}
      data-compatible={compatible || undefined}
      data-dimmed={dimmed || undefined}
      title={`${slot.label ?? slot.name}: ${splitTypes(slot.type).join(" or ")}${slot.optional ? " (optional)" : ""}`}
    >
      <Handle
        id={slot.name}
        type="target"
        position={Position.Left}
        className="ng-handle ng-handle--input"
        isConnectable={!readOnly}
        onDoubleClick={() => {
          if (!readOnly) editor.disconnectInput(node.id, slot.name);
        }}
      >
        <SlotDot type={slot.type} />
      </Handle>
      <span className="ng-slot__name">{slot.label ?? slot.name}</span>
    </div>
  );
}

function OutputRow({ node, slot }: { node: GraphNode; slot: SlotSpec }) {
  const { editor, readOnly, connecting } = useEditorContext();
  const connected = editor.graph.links.some((l) => l.from_node === node.id && l.from_slot === slot.name);
  return (
    <div
      className="ng-slot ng-slot--output"
      data-connected={connected || undefined}
      data-dimmed={(connecting !== null && connecting.node !== node.id) || undefined}
      title={`${slot.label ?? slot.name}: ${splitTypes(slot.type).join(" or ")}`}
    >
      <span className="ng-slot__name">{slot.label ?? slot.name}</span>
      <Handle
        id={slot.name}
        type="source"
        position={Position.Right}
        className="ng-handle ng-handle--output"
        isConnectable={!readOnly}
      >
        <SlotDot type={slot.type} />
      </Handle>
    </div>
  );
}

/** Title text that turns into an input on double-click, as node titles do. */
function NodeTitle({ node, def }: { node: GraphNode; def: NodeDefinition | null }) {
  const { editor, readOnly } = useEditorContext();
  const [editing, setEditing] = useState(false);
  const title = node.title ?? def?.title ?? node.type;

  if (editing && !readOnly) {
    return (
      <input
        className="ng-node__title-input"
        defaultValue={title}
        aria-label="Node title"
        autoFocus
        onFocus={(event) => event.target.select()}
        onBlur={(event) => {
          const next = event.target.value.trim();
          editor.setTitle(node.id, next === "" || next === def?.title ? null : next);
          setEditing(false);
        }}
        onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") setEditing(false);
          event.stopPropagation();
        }}
        onPointerDown={(event) => event.stopPropagation()}
      />
    );
  }
  return (
    <span className="ng-node__title" onDoubleClick={() => setEditing(true)} title={def?.summary ?? node.type}>
      {title}
    </span>
  );
}

/** Width-only resize, the litegraph way: height follows content. */
function ResizeGrip({ node }: { node: GraphNode; }) {
  const { editor } = useEditorContext();
  const { getZoom } = useReactFlow();
  const start = useRef<{ x: number; width: number } | null>(null);

  const onPointerDown = (event: ReactPointerEvent<HTMLSpanElement>) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    start.current = { x: event.clientX, width: node.width ?? DEFAULT_NODE_WIDTH };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLSpanElement>) => {
    const state = start.current;
    if (!state) return;
    const delta = (event.clientX - state.x) / (getZoom() || 1);
    const width = Math.round(Math.min(MAX_NODE_WIDTH, Math.max(MIN_NODE_WIDTH, state.width + delta)));
    editor.setWidth(node.id, width);
  };
  const onPointerUp = (event: ReactPointerEvent<HTMLSpanElement>) => {
    start.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <span
      className="ng-node__grip nodrag"
      aria-hidden="true"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    />
  );
}

export const GraphNodeView = memo(function GraphNodeView({ data, selected }: NodeProps<GraphFlowNode>) {
  const { editor, readOnly } = useEditorContext();
  const { node, def, problems, status } = data;
  const shownWidgets = def ? visibleWidgets(def, node.values) : [];
  const noteRef = useAutoGrowTextarea(node.note, 22);
  const isNote = def?.kind === "note";
  const errors = problems.filter((p) => p.severity === "error");
  const width = node.width ?? def?.width ?? DEFAULT_NODE_WIDTH;

  return (
    <div
      className="ng-node"
      data-selected={selected || undefined}
      data-collapsed={node.collapsed || undefined}
      data-mode={node.mode === "always" ? undefined : node.mode}
      data-kind={isNote ? "note" : undefined}
      data-state={status?.state}
      data-errors={errors.length > 0 || undefined}
      style={{ width }}
    >
      <header className="ng-node__header">
        <button
          type="button"
          className="ng-node__collapse nodrag"
          aria-label={node.collapsed ? "Expand node" : "Collapse node"}
          aria-expanded={!node.collapsed}
          onClick={() => !readOnly && editor.setCollapsed(node.id, !node.collapsed)}
        >
          <span aria-hidden="true">{node.collapsed ? "▸" : "▾"}</span>
        </button>
        <NodeTitle node={node} def={def} />
        {def?.executor && def.executor !== "deterministic" && (
          <span className="ng-node__badge" data-badge={def.executor} title={`${def.executor} executor`}>
            {def.executor === "ai" ? "AI" : def.executor === "human" ? "HUMAN" : "HYBRID"}
          </span>
        )}
        {node.mode !== "always" && (
          <span className="ng-node__badge" data-badge={node.mode}>
            {node.mode === "muted" ? "muted" : "bypass"}
          </span>
        )}
        {status && (
          <span className="ng-node__badge" data-badge={status.state} title={status.detail}>
            {status.state}
          </span>
        )}
        {errors.length > 0 && (
          <span className="ng-node__badge" data-badge="error" title={errors.map((p) => p.message).join("\n")}>
            {errors.length}
          </span>
        )}
      </header>

      {node.collapsed && def && (
        <div className="ng-node__collapsed-dots" aria-hidden="true">
          {def.inputs.length > 0 && (
            <span className="ng-collapsed-handle ng-collapsed-handle--in">
              {def.inputs.map((slot) => (
                <Handle
                  key={slot.name}
                  id={slot.name}
                  type="target"
                  position={Position.Left}
                  className="ng-handle ng-handle--collapsed"
                  isConnectable={!readOnly}
                />
              ))}
            </span>
          )}
          {def.outputs.length > 0 && (
            <span className="ng-collapsed-handle ng-collapsed-handle--out">
              {def.outputs.map((slot) => (
                <Handle
                  key={slot.name}
                  id={slot.name}
                  type="source"
                  position={Position.Right}
                  className="ng-handle ng-handle--collapsed"
                  isConnectable={!readOnly}
                />
              ))}
            </span>
          )}
        </div>
      )}

      {!node.collapsed && (
        <div className="ng-node__body">
          {status?.state === "running" && (
            <span
              className="ng-node__progress"
              style={{ width: `${Math.round(Math.min(1, Math.max(0, status.progress ?? 0)) * 100)}%` }}
              aria-hidden="true"
            />
          )}
          {def && (def.inputs.length > 0 || def.outputs.length > 0) && (
            <div className="ng-node__slots">
              <div className="ng-node__inputs">
                {def.inputs.map((slot) => (
                  <InputRow key={slot.name} node={node} slot={slot} />
                ))}
              </div>
              <div className="ng-node__outputs">
                {def.outputs.map((slot) => (
                  <OutputRow key={slot.name} node={node} slot={slot} />
                ))}
              </div>
            </div>
          )}

          {def && shownWidgets.length > 0 && (
            <div className="ng-node__widgets nodrag nowheel">
              {shownWidgets.map((spec) => (
                <WidgetRow
                  key={spec.name}
                  nodeId={node.id}
                  spec={spec}
                  value={node.values[spec.name] ?? spec.default}
                  disabled={readOnly || node.mode !== "always"}
                  onChange={(value) => editor.setWidget(node.id, spec.name, value)}
                />
              ))}
            </div>
          )}

          {!def && (
            <p className="ng-node__unknown" role="note">
              Unknown node type <code>{node.type}</code>
            </p>
          )}

          <div className="ng-node__note nodrag" data-empty={node.note === "" || undefined}>
            <textarea
              ref={noteRef}
              className="ng-node__note-input"
              aria-label={`Note for ${node.title ?? def?.title ?? node.type}`}
              placeholder={isNote ? "Write here…" : "Add a note…"}
              value={node.note}
              rows={isNote ? 6 : 1}
              readOnly={readOnly}
              spellCheck={false}
              onChange={(event) => editor.setNote(node.id, event.target.value)}
            />
          </div>
        </div>
      )}

      {!node.collapsed && !readOnly && <ResizeGrip node={node} />}
    </div>
  );
});
