/**
 * A folded group: several nodes drawn as one.
 *
 * What it has to communicate, in one node: that it stands for more than itself, what is inside,
 * and what it takes and gives at its boundary. So the header carries a stacked-plates mark and
 * the member count, the boundary slots are the real slots of the real members (derived from the
 * links, never stored), and the body lists what is inside in order — the steps, legible without
 * opening it. Open is one click on the header, and it shows the members exactly as they are, with
 * every widget available: there is no second copy of the graph to edit.
 *
 * A group with an unconnected required input shows that slot too. Folding must never be a way to
 * hide a hole in the graph.
 */

import { Handle, Position, useReactFlow, type Node, type NodeProps } from "@xyflow/react";
import { memo, useState, type KeyboardEvent } from "react";
import { slotColors, splitTypes, typesCompatible } from "./datatypes";
import { useEditorContext } from "./editorContext";
import { groupPorts, type GraphGroup, type GraphProblem, type GroupPort } from "./graphModel";
import { DEFAULT_NODE_WIDTH } from "./nodeDefs";

export interface GroupNodeData extends Record<string, unknown> {
  readonly group: GraphGroup;
  /** Every problem inside the group, so a folded node still shows the count. */
  readonly problems: readonly GraphProblem[];
  /** Member titles in graph order, for the body list. */
  readonly steps: readonly string[];
}

/** Both the folded node and the frame behind an open group carry the same data. */
export type GroupFlowNode = Node<GroupNodeData, "cfGroup" | "cfGroupFrame">;

export const GROUP_NODE_WIDTH = 280;

function Dot({ type }: { type: string }) {
  const colors = slotColors(type);
  const first = colors[0] ?? "#AAAAAA";
  if (colors.length <= 1) {
    return <span className="ng-slot__dot" style={{ backgroundColor: first }} aria-hidden="true" />;
  }
  const stripes = colors
    .map((color, i) => `${color} ${(i / colors.length) * 100}% ${((i + 1) / colors.length) * 100}%`)
    .join(", ");
  return (
    <span className="ng-slot__dot" style={{ background: `conic-gradient(${stripes})` }} aria-hidden="true" />
  );
}

/** The stacked mark: three plates, so a folded group never reads as an ordinary node. */
function StackMark() {
  return (
    <svg className="ng-group__mark" viewBox="0 0 14 14" aria-hidden="true" focusable="false">
      <path d="M1 4.2 7 1.4l6 2.8-6 2.8Z" fill="currentColor" opacity="0.95" />
      <path d="M1 7 7 9.8 13 7" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.6" />
      <path d="M1 9.8 7 12.6 13 9.8" fill="none" stroke="currentColor" strokeWidth="1.4" opacity="0.32" />
    </svg>
  );
}

function PortRow({ port, side }: { port: GroupPort; side: "in" | "out" }) {
  const { readOnly, connecting } = useEditorContext();
  const compatible = side === "in" && connecting !== null && typesCompatible(connecting.type, port.type);
  const dimmed = connecting !== null && !compatible;
  return (
    <div
      className={`ng-slot ng-slot--${side === "in" ? "input" : "output"}`}
      data-compatible={compatible || undefined}
      data-dimmed={dimmed || undefined}
      title={`${port.label}: ${splitTypes(port.type).join(" or ")}`}
    >
      {side === "in" ? (
        <>
          <Handle
            id={port.id}
            type="target"
            position={Position.Left}
            className="ng-handle ng-handle--input"
            isConnectable={!readOnly}
          >
            <Dot type={port.type} />
          </Handle>
          <span className="ng-slot__name">{port.label}</span>
        </>
      ) : (
        <>
          <span className="ng-slot__name">{port.label}</span>
          <Handle
            id={port.id}
            type="source"
            position={Position.Right}
            className="ng-handle ng-handle--output"
            isConnectable={!readOnly}
          >
            <Dot type={port.type} />
          </Handle>
        </>
      )}
    </div>
  );
}

export const GroupNodeView = memo(function GroupNodeView({ data, selected }: NodeProps<GroupFlowNode>) {
  const { editor, readOnly } = useEditorContext();
  const { fitView } = useReactFlow();
  const { group, problems, steps } = data;
  const [editing, setEditing] = useState(false);
  const ports = groupPorts(editor.graph, editor.catalog, group);
  const errors = problems.filter((p) => p.severity === "error");

  /** Opening a group takes you into it: the members appear and the view frames them. */
  const open = () => {
    if (readOnly) return;
    editor.setGroupCollapsed(group.id, false);
    // After the commit, not with it: the member nodes do not exist in the canvas until the graph
    // change has been rendered, and fitView can only frame nodes it can find.
    setTimeout(() => {
      void fitView({ nodes: group.members.map((id) => ({ id })), padding: 0.3, duration: 260 });
    }, 0);
  };

  return (
    <div
      className="ng-node ng-group"
      data-selected={selected || undefined}
      data-errors={errors.length > 0 || undefined}
      style={{ width: GROUP_NODE_WIDTH || DEFAULT_NODE_WIDTH }}
    >
      <header className="ng-node__header ng-group__header">
        <button
          type="button"
          className="ng-node__collapse nodrag"
          aria-label={`Open ${group.name}`}
          aria-expanded={false}
          onClick={open}
        >
          <span aria-hidden="true">▸</span>
        </button>
        <StackMark />
        {editing && !readOnly ? (
          <input
            className="ng-node__title-input"
            defaultValue={group.name}
            aria-label="Group name"
            autoFocus
            onFocus={(event) => event.target.select()}
            onBlur={(event) => {
              const next = event.target.value.trim();
              if (next && next !== group.name) editor.renameGroup(group.id, next);
              setEditing(false);
            }}
            onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
              if (event.key === "Enter") event.currentTarget.blur();
              if (event.key === "Escape") setEditing(false);
              event.stopPropagation();
            }}
            onPointerDown={(event) => event.stopPropagation()}
          />
        ) : (
          <span
            className="ng-node__title"
            title={`${group.members.length} nodes — click Open to change what is inside`}
            onDoubleClick={() => setEditing(true)}
          >
            {group.name}
          </span>
        )}
        <span className="ng-group__count" aria-label={`${group.members.length} nodes`}>
          {group.members.length}
        </span>
        {errors.length > 0 && (
          <span
            className="ng-node__badge"
            data-badge="error"
            title={errors.map((p) => p.message).join("\n")}
          >
            {errors.length}
          </span>
        )}
      </header>

      <div className="ng-node__body">
        {(ports.inputs.length > 0 || ports.outputs.length > 0) && (
          <div className="ng-node__slots">
            <div className="ng-node__inputs">
              {ports.inputs.map((port) => (
                <PortRow key={port.id} port={port} side="in" />
              ))}
            </div>
            <div className="ng-node__outputs">
              {ports.outputs.map((port) => (
                <PortRow key={port.id} port={port} side="out" />
              ))}
            </div>
          </div>
        )}

        <ol className="ng-group__steps">
          {steps.map((step, index) => (
            <li key={`${step}-${index}`} className="ng-group__step">
              {step}
            </li>
          ))}
        </ol>

        <div className="ng-group__actions nodrag">
          <button type="button" className="ng-group__open" disabled={readOnly} onClick={open}>
            Open
          </button>
          <button
            type="button"
            className="ng-group__ungroup"
            disabled={readOnly}
            title="Keep the nodes, drop the folded view"
            onClick={() => editor.ungroup(group.id)}
          >
            Ungroup
          </button>
        </div>
      </div>
    </div>
  );
});

/** The frame drawn behind an open group: a labelled region, and the way to fold it again. */
export const GroupFrameView = memo(function GroupFrameView({ data }: NodeProps<GroupFlowNode>) {
  const { editor, readOnly } = useEditorContext();
  const { group } = data;
  return (
    <div className="ng-groupframe" data-template={group.template || undefined}>
      <header className="ng-groupframe__head">
        <StackMark />
        <span className="ng-groupframe__name">{group.name}</span>
        <button
          type="button"
          className="ng-groupframe__fold nodrag"
          aria-label={`Fold ${group.name}`}
          disabled={readOnly}
          onClick={() => editor.setGroupCollapsed(group.id, true)}
        >
          Fold
        </button>
        <button
          type="button"
          className="ng-groupframe__fold nodrag"
          disabled={readOnly}
          title="Keep the nodes, drop the folded view"
          onClick={() => editor.ungroup(group.id)}
        >
          Ungroup
        </button>
      </header>
    </div>
  );
});
