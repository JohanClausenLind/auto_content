/**
 * The node right-click menu — the litegraph staple: title, then the verbs. Every action routes
 * through the same editor ops as the keyboard and panels, so undo covers all of it.
 */

import type { ReactElement } from "react";
import { nodeById, type NodeMode } from "./graphModel";
import type { GraphEditor } from "./useGraphEditor";

export interface NodeContextMenuProps {
  readonly editor: GraphEditor;
  readonly nodeId: string;
  readonly at: { x: number; y: number };
  onClose(): void;
}

export function NodeContextMenu({ editor, nodeId, at, onClose }: NodeContextMenuProps): ReactElement | null {
  const node = nodeById(editor.graph, nodeId);
  if (!node) return null;
  const def = editor.catalog.get(node.type);
  const title = node.title ?? def?.title ?? node.type;

  const act = (run: () => void) => () => {
    run();
    onClose();
  };
  const setMode = (mode: NodeMode) => act(() => editor.setMode(nodeId, mode));

  return (
    <div
      className="ng-menu"
      role="menu"
      tabIndex={-1}
      aria-label={`Node menu: ${title}`}
      style={{ left: at.x, top: at.y }}
      onPointerDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      <span className="ng-menu__title">{title}</span>
      <button type="button" role="menuitem" className="ng-menu__item" onClick={act(() => editor.setCollapsed(nodeId, !node.collapsed))}>
        {node.collapsed ? "Expand" : "Collapse"}
      </button>
      <button type="button" role="menuitem" className="ng-menu__item" onClick={act(() => editor.duplicateNodes([nodeId]))}>
        Duplicate <span className="ng-menu__key">Ctrl+D</span>
      </button>
      <span className="ng-menu__sep" aria-hidden="true" />
      {node.mode !== "always" && (
        <button type="button" role="menuitem" className="ng-menu__item" onClick={setMode("always")}>
          Enable
        </button>
      )}
      {node.mode !== "muted" && (
        <button type="button" role="menuitem" className="ng-menu__item" onClick={setMode("muted")}>
          Mute
        </button>
      )}
      {node.mode !== "bypass" && (
        <button type="button" role="menuitem" className="ng-menu__item" onClick={setMode("bypass")}>
          Bypass
        </button>
      )}
      <span className="ng-menu__sep" aria-hidden="true" />
      <button type="button" role="menuitem" className="ng-menu__item" data-danger onClick={act(() => editor.removeNodes([nodeId]))}>
        Delete <span className="ng-menu__key">Del</span>
      </button>
    </div>
  );
}
