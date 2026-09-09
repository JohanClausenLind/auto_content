/**
 * The node right-click menu — the litegraph staple: title, then the verbs. Every action routes
 * through the same editor ops as the keyboard and panels, so undo covers all of it.
 */

import type { ReactElement } from "react";
import { groupById, groupOf, nodeById, type NodeMode } from "./graphModel";
import type { GraphEditor } from "./useGraphEditor";

export interface NodeContextMenuProps {
  readonly editor: GraphEditor;
  readonly nodeId: string;
  readonly at: { x: number; y: number };
  onClose(): void;
}

/**
 * The menu for a folded group. Right-clicking one used to show nothing at all: the menu looked
 * its target up as a node, a group id is not one, and it returned null — so the only object on
 * the canvas that stands for several nodes was the one object with no verbs.
 */
function GroupMenu({
  editor,
  groupId,
  at,
  onClose,
}: {
  editor: GraphEditor;
  groupId: string;
  at: { x: number; y: number };
  onClose(): void;
}): ReactElement | null {
  const group = groupById(editor.graph, groupId);
  if (!group) return null;
  const act = (run: () => void) => () => {
    run();
    onClose();
  };
  return (
    <div
      className="ng-menu"
      role="menu"
      tabIndex={-1}
      aria-label={`Group menu: ${group.name}`}
      style={{ left: at.x, top: at.y }}
      onPointerDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      <span className="ng-menu__title">
        {group.name} · {group.members.length} nodes
      </span>
      <button
        type="button"
        role="menuitem"
        className="ng-menu__item"
        onClick={act(() => editor.setGroupCollapsed(group.id, !group.collapsed))}
      >
        {group.collapsed ? "Open" : "Fold"} <span className="ng-menu__key">Ctrl+G</span>
      </button>
      <button
        type="button"
        role="menuitem"
        className="ng-menu__item"
        onClick={act(() => editor.ungroup(group.id))}
      >
        Ungroup (keep the nodes)
      </button>
      <span className="ng-menu__sep" aria-hidden="true" />
      <button
        type="button"
        role="menuitem"
        className="ng-menu__item"
        data-danger
        onClick={act(() => editor.removeNodes(group.members))}
      >
        Delete all {group.members.length}
      </button>
    </div>
  );
}

export function NodeContextMenu({ editor, nodeId, at, onClose }: NodeContextMenuProps): ReactElement | null {
  const node = nodeById(editor.graph, nodeId);
  // The target may be a folded group rather than a node: on the canvas they are both one box.
  if (!node) return <GroupMenu editor={editor} groupId={nodeId} at={at} onClose={onClose} />;
  const def = editor.catalog.get(node.type);
  const title = node.title ?? def?.title ?? node.type;
  const group = groupOf(editor.graph, nodeId);
  const selection = editor.selection.includes(nodeId) ? editor.selection : [nodeId];

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
      {group === undefined && selection.length > 1 && (
        <button
          type="button"
          role="menuitem"
          className="ng-menu__item"
          onClick={act(() => editor.groupNodes(selection, { name: "Group" }))}
        >
          Fold {selection.length} into a group <span className="ng-menu__key">Ctrl+G</span>
        </button>
      )}
      {group !== undefined && (
        <>
          <button
            type="button"
            role="menuitem"
            className="ng-menu__item"
            onClick={act(() => editor.setGroupCollapsed(group.id, !group.collapsed))}
          >
            {group.collapsed ? `Open ${group.name}` : `Fold ${group.name}`}
            <span className="ng-menu__key">Ctrl+G</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="ng-menu__item"
            onClick={act(() => editor.ungroup(group.id))}
          >
            Ungroup {group.name}
          </button>
        </>
      )}
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
