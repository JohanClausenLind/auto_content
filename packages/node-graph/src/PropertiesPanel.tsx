/**
 * The right-hand panel: a Parameters tab for whatever is selected and a Nodes tab listing the
 * whole graph in execution order — the same split the reference editor uses, and the list doubles
 * as the screen-reader path through the graph.
 */

import { useState } from "react";
import { splitTypes } from "./datatypes";
import { nodeById, type NodeMode } from "./graphModel";
import type { NodeDefinition } from "./nodeDefs";
import type { GraphEditor } from "./useGraphEditor";
import { WidgetRow } from "./widgets";

export interface PropertiesPanelProps {
  readonly editor: GraphEditor;
  readonly readOnly?: boolean;
}

const EXECUTOR_LABEL: Record<NonNullable<NodeDefinition["executor"]>, string> = {
  deterministic: "deterministic: same input, same output",
  ai: "runs a model",
  human: "waits for a person",
  hybrid: "part deterministic, part model",
};

const MODES: readonly { value: NodeMode; label: string }[] = [
  { value: "always", label: "Always" },
  { value: "muted", label: "Muted" },
  { value: "bypass", label: "Bypass" },
];

function SelectedNode({ editor, nodeId, readOnly }: { editor: GraphEditor; nodeId: string; readOnly: boolean }) {
  const node = nodeById(editor.graph, nodeId);
  if (!node) return null;
  const def = editor.catalog.get(node.type);
  const problems = editor.problemsByNode.get(node.id) ?? [];

  return (
    <div className="ng-props__node">
      {def && (
        // What the node is, before how it is configured: the type's own name, one line on what it
        // does, and the longer explanation where the definition carries one.
        <div className="ng-props__about">
          <p className="ng-props__summary">{def.summary}</p>
          {def.help && <p className="ng-props__help">{def.help}</p>}
          <p className="ng-props__type">
            <code>{def.type}</code>
            {def.executor && <span className="ng-props__executor"> · {EXECUTOR_LABEL[def.executor]}</span>}
          </p>
        </div>
      )}
      <label className="ng-props__field">
        <span className="ng-props__label">Title</span>
        <input
          type="text"
          value={node.title ?? def?.title ?? node.type}
          disabled={readOnly}
          onChange={(event) => editor.setTitle(node.id, event.target.value)}
          onBlur={(event) => {
            const trimmed = event.target.value.trim();
            if (trimmed === "" || trimmed === def?.title) editor.setTitle(node.id, null);
          }}
        />
      </label>
      <label className="ng-props__field">
        <span className="ng-props__label">Mode</span>
        <select
          value={node.mode}
          disabled={readOnly}
          onChange={(event) => editor.setMode(node.id, event.target.value as NodeMode)}
        >
          {MODES.map((mode) => (
            <option key={mode.value} value={mode.value}>
              {mode.label}
            </option>
          ))}
        </select>
      </label>

      {def && def.widgets.length > 0 && (
        <div className="ng-props__widgets">
          {def.widgets.map((spec) => (
            <WidgetRow
              key={spec.name}
              nodeId={`props-${node.id}`}
              spec={spec}
              value={node.values[spec.name] ?? spec.default}
              disabled={readOnly}
              onChange={(value) => editor.setWidget(node.id, spec.name, value)}
            />
          ))}
        </div>
      )}

      <label className="ng-props__field">
        <span className="ng-props__label">Note</span>
        <textarea
          rows={3}
          value={node.note}
          readOnly={readOnly}
          placeholder="Add a note…"
          maxLength={5000}
          onChange={(event) => editor.setNote(node.id, event.target.value)}
        />
      </label>

      {def && (def.inputs.length > 0 || def.outputs.length > 0) && (
        <dl className="ng-props__slots">
          {def.inputs.map((slot) => (
            <div key={`in-${slot.name}`} className="ng-props__slot">
              <dt>→ {slot.label ?? slot.name}</dt>
              <dd>{splitTypes(slot.type).join(" or ")}</dd>
            </div>
          ))}
          {def.outputs.map((slot) => (
            <div key={`out-${slot.name}`} className="ng-props__slot">
              <dt>{slot.label ?? slot.name} →</dt>
              <dd>{splitTypes(slot.type).join(" or ")}</dd>
            </div>
          ))}
        </dl>
      )}

      {problems.length > 0 && (
        <ul className="ng-props__problems">
          {problems.map((problem, index) => (
            <li key={index} data-severity={problem.severity}>
              {problem.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PropertiesPanel({ editor, readOnly = false }: PropertiesPanelProps) {
  const [tab, setTab] = useState<"parameters" | "nodes">("parameters");
  const selectedId = editor.selection.length === 1 ? editor.selection[0] : null;
  const order = editor.order ?? editor.graph.nodes.map((n) => n.id);

  return (
    <div className="ng-props">
      <div className="ng-props__tabs" role="tablist" aria-label="Graph overview">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "parameters"}
          onClick={() => setTab("parameters")}
        >
          Parameters
        </button>
        <button type="button" role="tab" aria-selected={tab === "nodes"} onClick={() => setTab("nodes")}>
          Nodes
        </button>
      </div>

      {tab === "parameters" &&
        (selectedId ? (
          <SelectedNode editor={editor} nodeId={selectedId} readOnly={readOnly} />
        ) : (
          <p className="ng-props__empty">
            {editor.selection.length > 1
              ? `${editor.selection.length} nodes selected`
              : "Select a node to edit its parameters."}
          </p>
        ))}

      {tab === "nodes" && (
        <ol className="ng-props__list" aria-label="Nodes in execution order">
          {order.map((id) => {
            const node = nodeById(editor.graph, id);
            if (!node) return null;
            const def = editor.catalog.get(node.type);
            const errors = (editor.problemsByNode.get(id) ?? []).filter((p) => p.severity === "error");
            return (
              <li key={id}>
                <button
                  type="button"
                  className="ng-props__row"
                  aria-pressed={editor.selection.includes(id)}
                  onClick={() => editor.select([id])}
                >
                  <span className="ng-props__row-title">{node.title ?? def?.title ?? node.type}</span>
                  {errors.length > 0 && <span className="ng-props__row-errors">⚠ {errors.length}</span>}
                </button>
              </li>
            );
          })}
          {order.length === 0 && <p className="ng-props__empty">The graph is empty.</p>}
        </ol>
      )}
    </div>
  );
}
