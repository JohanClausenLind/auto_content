/**
 * The add-node search: double-click the canvas (or press the library button) and type. It is the
 * fastest way to add a node, so it opens focused, filters as you type, and Enter takes the top
 * hit. When opened by dropping a link on empty canvas it only offers nodes that can accept the
 * dragged type.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { typesCompatible } from "./datatypes";
import type { NodeCatalog, NodeDefinition } from "./nodeDefs";

export interface NodeSearchDialogProps {
  readonly catalog: NodeCatalog;
  /** Canvas position where the dialog sits and where the node will land. */
  readonly at: { x: number; y: number };
  /** Only offer nodes with an input this output type can feed. */
  readonly forOutputType?: string | undefined;
  onPick(def: NodeDefinition): void;
  onClose(): void;
}

export function NodeSearchDialog({ catalog, at, forOutputType, onPick, onClose }: NodeSearchDialogProps) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement | null>(null);

  const hits = useMemo(() => {
    const all = catalog.search(query, 40);
    if (!forOutputType) return all;
    return all.filter((def) => def.inputs.some((input) => typesCompatible(forOutputType, input.type)));
  }, [catalog, query, forOutputType]);

  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    const el = listRef.current?.querySelector('[data-active="true"]');
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <div
      className="ng-search"
      role="dialog"
      aria-label="Add node"
      style={{ left: at.x, top: at.y }}
      onPointerDown={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
        event.stopPropagation();
      }}
    >
      <input
        className="ng-search__input"
        type="search"
        placeholder={forOutputType ? `Nodes accepting ${forOutputType}…` : "Search nodes…"}
        aria-label="Search nodes"
        value={query}
        autoFocus
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setActive((i) => Math.min(hits.length - 1, i + 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((i) => Math.max(0, i - 1));
          } else if (event.key === "Enter") {
            event.preventDefault();
            const hit = hits[active] ?? hits[0];
            if (hit) onPick(hit);
          }
        }}
      />
      <ul className="ng-search__list" role="listbox" aria-label="Matching nodes" ref={listRef}>
        {hits.length === 0 && <li className="ng-search__empty">No nodes match</li>}
        {hits.map((def, index) => (
          <li key={def.type} role="option" aria-selected={index === active} data-active={index === active}>
            <button
              type="button"
              className="ng-search__item"
              onPointerEnter={() => setActive(index)}
              onClick={() => onPick(def)}
            >
              <span className="ng-search__title">{def.title}</span>
              <span className="ng-search__category">{def.category}</span>
              <span className="ng-search__summary">{def.summary}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
