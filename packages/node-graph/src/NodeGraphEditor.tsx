/**
 * The canvas. React Flow moves the nodes and draws the links; everything that means something —
 * what may connect to what, what deleting does, what undo restores — lives in the graph model.
 *
 * Interaction matches the node editors people know: drag empty canvas to box-select, wheel to
 * zoom, middle/right-drag to pan, drag a slot to link (incompatible inputs dim), drop a link on
 * empty canvas to search for a node that accepts it, double-click the canvas to search, Delete /
 * Ctrl+Z / Ctrl+Shift+Z / Ctrl+D / Ctrl+C / Ctrl+V do what they say.
 */

import {
  applyNodeChanges,
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type OnConnectEnd,
  type OnConnectStart,
} from "@xyflow/react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { slotColor } from "./datatypes";
import { EditorContext } from "./editorContext";
import { estimateNodeSize, findSlot, type NodeDefinition } from "./nodeDefs";
import { GraphNodeView, type GraphFlowNode, type GraphNodeData } from "./GraphNodeView";
import {
  GROUP_NODE_WIDTH,
  GroupFrameView,
  GroupNodeView,
  type GroupFlowNode,
} from "./GroupNodeView";
import { NodeContextMenu } from "./NodeContextMenu";
import { NodeSearchDialog } from "./NodeSearchDialog";
import type { GraphEditor } from "./useGraphEditor";
import { groupOf, nodeById, parseGroupPort } from "./graphModel";

export const NODE_DRAG_MIME = "application/x-cf-node-type";

export interface NodeStatusMap {
  readonly [nodeId: string]: GraphNodeData["status"];
}

export interface NodeGraphEditorProps {
  readonly editor: GraphEditor;
  readonly readOnly?: boolean;
  /** Live run states keyed by node id, when the canvas mirrors an actual run. */
  readonly statuses?: NodeStatusMap;
  readonly "aria-label"?: string;
  /** Fired when the user picks a node in the canvas (for a properties panel). */
  onInspect?(nodeId: string | null): void;
  /**
   * Files dropped onto the canvas, with the graph position they landed on. The editor knows
   * where the drop happened and nothing else about it: what a file *is*, and which node should
   * hold it, is the host application's question (this one asks its own API to sniff the bytes).
   */
  onDropFiles?(files: readonly File[], position: { x: number; y: number }): void;
}

const nodeTypes = { cfNode: GraphNodeView, cfGroup: GroupNodeView, cfGroupFrame: GroupFrameView };

/** Padding around an open group's members, in canvas units. */
const FRAME_PAD = 26;
const FRAME_HEAD = 30;

type CanvasNode = GraphFlowNode | GroupFlowNode;

/**
 * A handle on a folded group is `in:<member>:<slot>`; a handle on an ordinary node is the slot
 * name. Both arrive through the same React Flow connection, so every connection is translated
 * back to the member and slot it really means before the graph model sees it.
 */
function realEnd(nodeId: string, handle: string): { node: string; slot: string } {
  const port = parseGroupPort(handle);
  return port ? { node: port.node_id, slot: port.slot } : { node: nodeId, slot: handle };
}

interface MenuState {
  readonly screen: { x: number; y: number };
  readonly nodeId: string;
}

interface SearchState {
  readonly screen: { x: number; y: number };
  readonly flow: { x: number; y: number };
  readonly forOutputType?: string;
  readonly connectFrom?: { node: string; slot: string };
}

function Canvas({
  editor,
  readOnly = false,
  statuses,
  "aria-label": ariaLabel,
  onInspect,
  onDropFiles,
}: NodeGraphEditorProps) {
  const { screenToFlowPosition, getZoom, zoomIn, zoomOut, fitView } = useReactFlow();
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [rfNodes, setRfNodes] = useState<CanvasNode[]>([]);
  const [connecting, setConnecting] = useState<{ node: string; slot: string; type: string } | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [search, setSearch] = useState<SearchState | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [minimap, setMinimap] = useState(true);
  const [zoomLabel, setZoomLabel] = useState(100);
  const clipboard = useRef<readonly string[]>([]);
  const refusalTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showRefusal = useCallback((reason: string) => {
    setRefusal(reason);
    if (refusalTimer.current) clearTimeout(refusalTimer.current);
    refusalTimer.current = setTimeout(() => setRefusal(null), 2600);
  }, []);
  useEffect(() => () => {
    if (refusalTimer.current) clearTimeout(refusalTimer.current);
  }, []);

  // The graph is the truth; React Flow state is rebuilt from it and only owns in-progress drags.
  useEffect(() => {
    setRfNodes((current) => {
      const positions = new Map(current.filter((n) => n.dragging).map((n) => [n.id, n.position]));
      // A folded group is not a node, so its selection lives only in React Flow's own state and
      // has to survive a rebuild driven by the graph.
      const groupSelection = new Set(
        current.filter((n) => n.type === "cfGroup" && n.selected).map((n) => n.id),
      );
      const graph = editor.graph;
      const folded = new Set<string>();
      for (const group of graph.groups) {
        if (group.collapsed) for (const member of group.members) folded.add(member);
      }
      const frames: CanvasNode[] = [];
      const groupNodes: CanvasNode[] = [];
      for (const group of graph.groups) {
        const problems = group.members.flatMap((id) => editor.problemsByNode.get(id) ?? []);
        const steps = group.members
          .map((id) => {
            const node = nodeById(graph, id);
            const def = node ? editor.catalog.get(node.type) : undefined;
            return node?.title ?? def?.title ?? node?.type ?? id;
          })
          .filter((label): label is string => Boolean(label));
        if (group.collapsed) {
          groupNodes.push({
            id: group.id,
            type: "cfGroup" as const,
            position: positions.get(group.id) ?? { x: group.x, y: group.y },
            selected: groupSelection.has(group.id),
            draggable: !readOnly,
            initialWidth: GROUP_NODE_WIDTH,
            // Header, the boundary slots, the step list and the two buttons.
            initialHeight: 30 + 18 + steps.length * 16 + 60,
            data: { group, problems, steps },
          } satisfies GroupFlowNode);
          continue;
        }
        // Open: a labelled frame behind the members, sized to hold them. It is not draggable and
        // not selectable — dragging the frame instead of the node it sits behind is the one thing
        // a region like this must not do.
        const members = group.members
          .map((id) => nodeById(graph, id))
          .filter((n): n is NonNullable<typeof n> => n !== undefined);
        if (members.length === 0) continue;
        const sizes = members.map((node) => estimateNodeSize(node, editor.catalog.get(node.type) ?? null));
        const left = Math.min(...members.map((n) => n.x));
        const top = Math.min(...members.map((n) => n.y));
        const right = Math.max(...members.map((n, i) => n.x + sizes[i]!.width));
        const bottom = Math.max(...members.map((n, i) => n.y + sizes[i]!.height));
        frames.push({
          id: `frame:${group.id}`,
          type: "cfGroupFrame" as const,
          position: { x: left - FRAME_PAD, y: top - FRAME_PAD - FRAME_HEAD },
          selectable: false,
          draggable: false,
          focusable: false,
          zIndex: -1,
          initialWidth: right - left + FRAME_PAD * 2,
          initialHeight: bottom - top + FRAME_PAD * 2 + FRAME_HEAD,
          width: right - left + FRAME_PAD * 2,
          height: bottom - top + FRAME_PAD * 2 + FRAME_HEAD,
          data: { group, problems, steps },
        } satisfies GroupFlowNode);
      }
      const plain = graph.nodes
        .filter((node) => !folded.has(node.id))
        .map((node) => {
          const def = editor.catalog.get(node.type) ?? null;
          const status = statuses?.[node.id];
          const size = estimateNodeSize(node, def);
          return {
            id: node.id,
            type: "cfNode" as const,
            position: positions.get(node.id) ?? { x: node.x, y: node.y },
            selected: editor.selection.includes(node.id),
            draggable: !readOnly,
            initialWidth: size.width,
            initialHeight: size.height,
            data: {
              node,
              def,
              problems: editor.problemsByNode.get(node.id) ?? [],
              ...(status ? { status } : {}),
            },
          } satisfies GraphFlowNode;
        });
      // Frames first so they paint behind everything they contain.
      return [...frames, ...groupNodes, ...plain];
    });
  }, [editor.graph, editor.selection, editor.problemsByNode, editor.catalog, statuses, readOnly]);

  const edges: Edge[] = useMemo(() => {
    const out: Edge[] = [];
    for (const link of editor.graph.links) {
      const source = nodeById(editor.graph, link.from_node);
      const def = source ? editor.catalog.get(source.type) : undefined;
      const type = def ? (findSlot(def.outputs, link.from_slot)?.type ?? "ANY") : "ANY";
      const fromGroup = groupOf(editor.graph, link.from_node);
      const toGroup = groupOf(editor.graph, link.to_node);
      const fromFolded = fromGroup?.collapsed ? fromGroup : undefined;
      const toFolded = toGroup?.collapsed ? toGroup : undefined;
      // A link between two members of the same folded group is internal: it is drawn when the
      // group is open and nowhere at all when it is not, which is the whole point of folding.
      if (fromFolded && toFolded && fromFolded.id === toFolded.id) continue;
      out.push({
        id: link.id,
        source: fromFolded ? fromFolded.id : link.from_node,
        sourceHandle: fromFolded ? `out:${link.from_node}:${link.from_slot}` : link.from_slot,
        target: toFolded ? toFolded.id : link.to_node,
        targetHandle: toFolded ? `in:${link.to_node}:${link.to_slot}` : link.to_slot,
        style: { stroke: slotColor(type) },
        focusable: true,
      });
    }
    return out;
  }, [editor.graph, editor.catalog]);

  const onNodesChange = useCallback(
    (changes: NodeChange<CanvasNode>[]) => {
      setRfNodes((nodes) => applyNodeChanges(changes, nodes));
      const selected = changes.filter((c) => c.type === "select");
      if (selected.length > 0) {
        const on = new Set(editor.selection);
        for (const change of selected) {
          // A folded group is not a node, and the properties panel and the delete key both work
          // on node ids: letting a group id into the selection would make Delete mean nothing.
          if (!nodeById(editor.graph, change.id)) continue;
          if (change.selected) on.add(change.id);
          else on.delete(change.id);
        }
        editor.select([...on]);
        onInspect?.(on.size === 1 ? [...on][0]! : null);
      }
    },
    [editor, onInspect],
  );

  const onNodeDragStop = useCallback(
    (_event: unknown, _node: CanvasNode, nodes: CanvasNode[]) => {
      if (readOnly) return;
      const moves: { id: string; x: number; y: number }[] = [];
      for (const node of nodes) {
        if (nodeById(editor.graph, node.id)) {
          moves.push({ id: node.id, x: node.position.x, y: node.position.y });
        } else if (editor.graph.groups.some((g) => g.id === node.id)) {
          // Dragging the folded node moves the group and everything in it, so opening it later
          // shows the members where the group is now.
          editor.moveGroup(node.id, node.position.x, node.position.y);
        }
      }
      if (moves.length > 0) editor.moveNodes(moves);
    },
    [editor, readOnly],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange<Edge>[]) => {
      if (readOnly) return;
      for (const change of changes) {
        if (change.type === "remove") editor.disconnect(change.id);
      }
    },
    [editor, readOnly],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly || !connection.sourceHandle || !connection.targetHandle) return;
      const verdict = editor.connect(
        realEnd(connection.source, connection.sourceHandle),
        realEnd(connection.target, connection.targetHandle),
      );
      if (!verdict.ok) showRefusal(verdict.reason);
    },
    [editor, readOnly, showRefusal],
  );

  const onConnectStart: OnConnectStart = useCallback(
    (_event, params) => {
      if (params.handleType !== "source" || !params.nodeId || !params.handleId) return;
      const from = realEnd(params.nodeId, params.handleId);
      const node = nodeById(editor.graph, from.node);
      const def = node ? editor.catalog.get(node.type) : undefined;
      const type = def ? (findSlot(def.outputs, from.slot)?.type ?? "ANY") : "ANY";
      setConnecting({ node: from.node, slot: from.slot, type });
    },
    [editor],
  );

  /** Dropping a link on empty canvas opens the search, filtered to inputs the type fits. */
  const onConnectEnd: OnConnectEnd = useCallback(
    (event, state) => {
      const from = connecting;
      setConnecting(null);
      if (readOnly || !from || state.isValid) return;
      if (!(event instanceof MouseEvent)) return;
      const target = event.target as HTMLElement | null;
      if (!target?.classList.contains("react-flow__pane")) return;
      const wrapper = wrapperRef.current?.getBoundingClientRect();
      if (!wrapper) return;
      setSearch({
        screen: { x: event.clientX - wrapper.left, y: event.clientY - wrapper.top },
        flow: screenToFlowPosition({ x: event.clientX, y: event.clientY }),
        forOutputType: from.type,
        connectFrom: { node: from.node, slot: from.slot },
      });
    },
    [connecting, readOnly, screenToFlowPosition],
  );

  const openSearchAt = useCallback(
    (clientX: number, clientY: number) => {
      const wrapper = wrapperRef.current?.getBoundingClientRect();
      if (!wrapper) return;
      setSearch({
        screen: { x: clientX - wrapper.left, y: clientY - wrapper.top },
        flow: screenToFlowPosition({ x: clientX, y: clientY }),
      });
    },
    [screenToFlowPosition],
  );

  const onContextMenu = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>) => {
      if (readOnly) return;
      const target = event.target as HTMLElement;
      const wrapper = wrapperRef.current?.getBoundingClientRect();
      if (!wrapper) return;
      const nodeEl = target.closest<HTMLElement>(".react-flow__node[data-id]");
      if (nodeEl?.dataset.id) {
        event.preventDefault();
        setSearch(null);
        setMenu({
          screen: { x: event.clientX - wrapper.left, y: event.clientY - wrapper.top },
          nodeId: nodeEl.dataset.id,
        });
        return;
      }
      if (target.classList.contains("react-flow__pane")) {
        event.preventDefault();
        setMenu(null);
        openSearchAt(event.clientX, event.clientY);
      }
    },
    [openSearchAt, readOnly],
  );

  const onDoubleClick = useCallback(
    (event: ReactMouseEvent<HTMLDivElement>) => {
      if (readOnly) return;
      const target = event.target as HTMLElement;
      if (!target.classList.contains("react-flow__pane")) return;
      openSearchAt(event.clientX, event.clientY);
    },
    [openSearchAt, readOnly],
  );

  const pickFromSearch = useCallback(
    (def: NodeDefinition) => {
      if (!search) return;
      if (search.connectFrom) editor.addConnectedNode(def.type, search.flow, search.connectFrom);
      else editor.addNode(def.type, search.flow);
      setSearch(null);
    },
    [editor, search],
  );

  const onDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (readOnly) return;
      const files = Array.from(event.dataTransfer.files ?? []);
      if (files.length > 0 && onDropFiles) {
        event.preventDefault();
        onDropFiles(files, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
        return;
      }
      const type = event.dataTransfer.getData(NODE_DRAG_MIME);
      if (!type) return;
      event.preventDefault();
      editor.addNode(type, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
    },
    [editor, readOnly, screenToFlowPosition, onDropFiles],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (readOnly) return;
      const target = event.target as HTMLElement;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      const mod = event.ctrlKey || event.metaKey;
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        editor.removeSelected();
      } else if (mod && !event.shiftKey && event.key.toLowerCase() === "z") {
        event.preventDefault();
        editor.undo();
      } else if (mod && (event.key.toLowerCase() === "y" || (event.shiftKey && event.key.toLowerCase() === "z"))) {
        event.preventDefault();
        editor.redo();
      } else if (mod && event.key.toLowerCase() === "d") {
        event.preventDefault();
        editor.duplicateNodes(editor.selection);
      } else if (mod && event.key.toLowerCase() === "c") {
        clipboard.current = editor.selection;
      } else if (mod && event.key.toLowerCase() === "v") {
        event.preventDefault();
        editor.duplicateNodes(clipboard.current);
      } else if (mod && event.key.toLowerCase() === "a") {
        event.preventDefault();
        editor.select(editor.graph.nodes.map((n) => n.id));
      } else if (mod && event.key.toLowerCase() === "g") {
        // Fold the selection, or open the group the selection is in. The one gesture a canvas
        // with groups has to have, and the same key every editor with groups uses.
        event.preventDefault();
        const inGroup = editor.selection
          .map((id) => groupOf(editor.graph, id))
          .find((group) => group !== undefined);
        if (inGroup) editor.setGroupCollapsed(inGroup.id, !inGroup.collapsed);
        else if (editor.selection.length > 1) editor.groupNodes(editor.selection, { name: "Group" });
      }
    },
    [editor, readOnly],
  );

  const context = useMemo(
    () => ({ editor, readOnly, connecting, refusal }),
    [editor, readOnly, connecting, refusal],
  );

  const problemCount = editor.problems.filter((p) => p.severity === "error").length;

  return (
    <EditorContext.Provider value={context}>
      <div
        ref={wrapperRef}
        className="ng-canvas"
        role="application"
        aria-label={ariaLabel ?? "Node graph"}
        data-readonly={readOnly || undefined}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onDoubleClick={onDoubleClick}
        onPointerDown={(event) => {
          // Clicking back onto the canvas dismisses popovers (their own clicks don't bubble).
          const onPane = (event.target as HTMLElement).classList.contains("react-flow__pane");
          if (search && onPane) setSearch(null);
          if (menu && !(event.target as HTMLElement).closest(".ng-menu")) setMenu(null);
        }}
        onContextMenu={onContextMenu}
        onDragOver={(event) => {
          // A file drag carries "Files" in its types; accepting it here is what stops the browser
          // from navigating away to the dropped file, which is what an unhandled drop does.
          const types = event.dataTransfer.types;
          if (types.includes(NODE_DRAG_MIME) || (onDropFiles && types.includes("Files"))) {
            event.preventDefault();
          }
        }}
        onDrop={onDrop}
      >
        <ReactFlow
          nodes={rfNodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onNodeDragStop={onNodeDragStop}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onConnectStart={onConnectStart}
          onConnectEnd={onConnectEnd}
          isValidConnection={(connection) =>
            !!connection.sourceHandle &&
            !!connection.targetHandle &&
            editor.canConnect(
              realEnd(connection.source, connection.sourceHandle),
              realEnd(connection.target, connection.targetHandle),
            ).ok
          }
          onMove={() => setZoomLabel(Math.round(getZoom() * 100))}
          fitView
          minZoom={0.1}
          maxZoom={3}
          selectionOnDrag={!readOnly}
          panOnDrag={[1, 2]}
          panOnScroll={false}
          zoomOnDoubleClick={false}
          deleteKeyCode={null}
          multiSelectionKeyCode="Shift"
          nodesConnectable={!readOnly}
          nodesDraggable={!readOnly}
          elementsSelectable
          proOptions={{ hideAttribution: false }}
        >
          <Background variant={BackgroundVariant.Dots} gap={24} size={1.5} className="ng-canvas__grid" />
          {minimap && (
            <MiniMap
              className="ng-minimap"
              pannable
              zoomable
              ariaLabel="Graph minimap"
              nodeColor="var(--ng-minimap-node)"
              maskColor="var(--ng-minimap-mask)"
            />
          )}
        </ReactFlow>

        <div className="ng-canvas__stats" aria-hidden="true">
          <span>N: {editor.graph.nodes.length}</span>
          <span>L: {editor.graph.links.length}</span>
          {editor.graph.groups.length > 0 && (
            <span>
              G: {editor.graph.groups.filter((g) => g.collapsed).length}/{editor.graph.groups.length}
            </span>
          )}
          <span>{problemCount > 0 ? `⚠ ${problemCount}` : "✓ valid"}</span>
        </div>

        <div className="ng-canvas__controls" role="toolbar" aria-label="Canvas controls">
          <button type="button" className="ng-ctl" aria-label="Zoom out" onClick={() => void zoomOut()}>
            −
          </button>
          <span className="ng-ctl ng-ctl--label" aria-live="off">
            {zoomLabel}%
          </span>
          <button type="button" className="ng-ctl" aria-label="Zoom in" onClick={() => void zoomIn()}>
            +
          </button>
          <button type="button" className="ng-ctl" aria-label="Fit graph in view" onClick={() => void fitView({ padding: 0.15 })}>
            ⛶
          </button>
          <button
            type="button"
            className="ng-ctl"
            aria-label={minimap ? "Hide minimap" : "Show minimap"}
            aria-pressed={minimap}
            onClick={() => setMinimap((m) => !m)}
          >
            ▦
          </button>
        </div>

        {refusal && (
          <p className="ng-canvas__refusal" role="status">
            {refusal}
          </p>
        )}

        {menu && !readOnly && (
          <NodeContextMenu
            editor={editor}
            nodeId={menu.nodeId}
            at={menu.screen}
            onClose={() => setMenu(null)}
          />
        )}

        {search && !readOnly && (
          <NodeSearchDialog
            catalog={editor.catalog}
            at={search.screen}
            forOutputType={search.forOutputType}
            onPick={pickFromSearch}
            onClose={() => setSearch(null)}
          />
        )}
      </div>
    </EditorContext.Provider>
  );
}

/** The editor canvas. Wraps itself in a ReactFlowProvider so hosts don't have to. */
export function NodeGraphEditor(props: NodeGraphEditorProps) {
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
