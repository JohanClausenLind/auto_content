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
import { NodeContextMenu } from "./NodeContextMenu";
import { NodeSearchDialog } from "./NodeSearchDialog";
import type { GraphEditor } from "./useGraphEditor";
import { nodeById } from "./graphModel";

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

const nodeTypes = { cfNode: GraphNodeView };

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
  const [rfNodes, setRfNodes] = useState<GraphFlowNode[]>([]);
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
      return editor.graph.nodes.map((node) => {
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
    });
  }, [editor.graph, editor.selection, editor.problemsByNode, editor.catalog, statuses, readOnly]);

  const edges: Edge[] = useMemo(
    () =>
      editor.graph.links.map((link) => {
        const source = nodeById(editor.graph, link.from_node);
        const def = source ? editor.catalog.get(source.type) : undefined;
        const type = def ? (findSlot(def.outputs, link.from_slot)?.type ?? "ANY") : "ANY";
        return {
          id: link.id,
          source: link.from_node,
          sourceHandle: link.from_slot,
          target: link.to_node,
          targetHandle: link.to_slot,
          style: { stroke: slotColor(type) },
          focusable: true,
        };
      }),
    [editor.graph, editor.catalog],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<GraphFlowNode>[]) => {
      setRfNodes((nodes) => applyNodeChanges(changes, nodes));
      const selected = changes.filter((c) => c.type === "select");
      if (selected.length > 0) {
        const on = new Set(editor.selection);
        for (const change of selected) {
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
    (_event: unknown, _node: GraphFlowNode, nodes: GraphFlowNode[]) => {
      if (readOnly) return;
      editor.moveNodes(nodes.map((n) => ({ id: n.id, x: n.position.x, y: n.position.y })));
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
        { node: connection.source, slot: connection.sourceHandle },
        { node: connection.target, slot: connection.targetHandle },
      );
      if (!verdict.ok) showRefusal(verdict.reason);
    },
    [editor, readOnly, showRefusal],
  );

  const onConnectStart: OnConnectStart = useCallback(
    (_event, params) => {
      if (params.handleType !== "source" || !params.nodeId || !params.handleId) return;
      const node = nodeById(editor.graph, params.nodeId);
      const def = node ? editor.catalog.get(node.type) : undefined;
      const type = def ? (findSlot(def.outputs, params.handleId)?.type ?? "ANY") : "ANY";
      setConnecting({ node: params.nodeId, slot: params.handleId, type });
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
              { node: connection.source, slot: connection.sourceHandle },
              { node: connection.target, slot: connection.targetHandle },
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
