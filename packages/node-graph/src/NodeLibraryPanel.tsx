/**
 * The node library: every node type, grouped by category, searchable, and draggable onto the
 * canvas. Clicking a row also adds the node (at the view centre) for people who don't drag.
 */

import { useMemo, useState, type DragEvent } from "react";
import { NODE_DRAG_MIME } from "./NodeGraphEditor";
import type { NodeCatalog, NodeDefinition } from "./nodeDefs";

export interface NodeLibraryPanelProps {
  readonly catalog: NodeCatalog;
  onAdd(type: string): void;
}

function LibraryRow({ def, onAdd }: { def: NodeDefinition; onAdd(type: string): void }) {
  const onDragStart = (event: DragEvent<HTMLButtonElement>) => {
    event.dataTransfer.setData(NODE_DRAG_MIME, def.type);
    event.dataTransfer.effectAllowed = "copy";
  };
  return (
    <li>
      <button
        type="button"
        className="ng-library__item"
        draggable
        onDragStart={onDragStart}
        onClick={() => onAdd(def.type)}
        title={`${def.summary}\nDrag onto the canvas, or click to add.`}
      >
        <span className="ng-library__grip" aria-hidden="true">
          ⠿
        </span>
        <span className="ng-library__name">{def.title}</span>
        {def.executor && def.executor !== "deterministic" && (
          <span className="ng-library__tag" data-executor={def.executor}>
            {def.executor}
          </span>
        )}
      </button>
    </li>
  );
}

export function NodeLibraryPanel({ catalog, onAdd }: NodeLibraryPanelProps) {
  const [query, setQuery] = useState("");
  const [closed, setClosed] = useState<readonly string[]>([]);

  const groups = useMemo(() => {
    const hits = catalog.search(query, 200);
    const byCategory = new Map<string, NodeDefinition[]>();
    for (const def of hits) {
      const list = byCategory.get(def.category);
      if (list) list.push(def);
      else byCategory.set(def.category, [def]);
    }
    return [...byCategory.entries()];
  }, [catalog, query]);

  const toggle = (category: string) =>
    setClosed((current) =>
      current.includes(category) ? current.filter((c) => c !== category) : [...current, category],
    );

  return (
    <div className="ng-library">
      <input
        className="ng-library__search"
        type="search"
        placeholder="Search nodes…"
        aria-label="Search node library"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className="ng-library__groups">
        {groups.length === 0 && <p className="ng-library__empty">No nodes match.</p>}
        {groups.map(([category, defs]) => {
          const open = query !== "" || !closed.includes(category);
          return (
            <section key={category} className="ng-library__group">
              <button
                type="button"
                className="ng-library__heading"
                aria-expanded={open}
                onClick={() => toggle(category)}
              >
                <span aria-hidden="true">{open ? "▾" : "▸"}</span> {category}
                <span className="ng-library__count">{defs.length}</span>
              </button>
              {open && (
                <ul className="ng-library__list">
                  {defs.map((def) => (
                    <LibraryRow key={def.type} def={def} onAdd={onAdd} />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
