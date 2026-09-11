/**
 * The workspace: a node graph editor over the real pipeline stages.
 *
 * Layout mirrors the classic node-editor chrome — workflow tabs on top, a dock on the left, the
 * canvas in the middle, a Workflow Overview panel on the right, and a job queue that shows actual
 * production runs. Dropping a file on the canvas uploads it, identifies it from its bytes and
 * spawns the node that holds it, with the next steps offered underneath.
 *
 * The dock holds the node library, the model library and the run history behind one set of tabs,
 * and opening a run opens it over the canvas (`RunPane`) with the list still there. That is the
 * whole point: a run parked at the frame-review gate is answered here, with the drawings in front
 * of you and the next run one click away — no page to leave and come back from.
 *
 * Graphs persist to the server and to this browser (see storage.ts); Check is real validation,
 * and Run compiles the graph onto the production pipeline — a stage with no executor is refused
 * with its reason rather than quietly dropped.
 *
 * **A run can be laid over the canvas.** Picking one from the history attaches what each step
 * produced to the node that produced it — thumbnails on the node, the way ComfyUI does it — and
 * closing the run's pane leaves the graph there with the drawings still on it. That is what makes
 * the canvas a place to review from rather than only a place to build in: the node that drew a
 * bad picture, the knobs that drew it and the picture itself are one thing on screen, and
 * clicking the node's own count opens every frame, voice line and film that step made.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  NodeGraphEditor,
  NodeLibraryPanel,
  parseGraph,
  PropertiesPanel,
  serializeGraph,
  useGraphEditor,
  type WorkspaceGraph,
} from "@content-factory/node-graph";
import { api, isApiError } from "../api/client";
import { historyQuery, historyRunQuery, queryKeys, runReviewQuery, runsQuery } from "../api/queries";
import type { GraphRunStarted, HistoryRun, RunSummary } from "../api/types";
import { insertBlockOps, type WorkflowBlock } from "./blocks";
import { workspaceCatalog } from "./catalog";
import { DropSuggestions, useFileDrops } from "./DroppedFile";
import { loadWorkspace, newUntitledGraph, saveActive, saveGraphs } from "./storage";
import { ModelsPanel } from "./ModelsPanel";
import { HistoryPanel } from "./HistoryPanel";
import { NodeOutputPane } from "./NodeOutputPane";
import { awaitingByNode, runOnCanvas } from "./nodeOutputs";
import { RunPane } from "./RunPane";
import { formatExact, runLabel } from "./runFormat";
import { TemplatesPanel } from "./TemplatesPanel";
import type { WorkflowTemplate } from "./templates";

function TabName({ graph, active, onRename }: { graph: WorkspaceGraph; active: boolean; onRename(name: string): void }) {
  const [editing, setEditing] = useState(false);
  if (editing && active) {
    return (
      <input
        className="cf-wstab__input"
        defaultValue={graph.name}
        aria-label="Graph name"
        maxLength={200}
        autoFocus
        onFocus={(e) => e.target.select()}
        onBlur={(e) => {
          const name = e.target.value.trim();
          if (name && name !== graph.name) onRename(name);
          setEditing(false);
        }}
        onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
          if (e.key === "Enter") e.currentTarget.blur();
          if (e.key === "Escape") setEditing(false);
        }}
      />
    );
  }
  return (
    <span className="cf-wstab__name" onDoubleClick={() => active && setEditing(true)}>
      {graph.name}
    </span>
  );
}

const RUN_STATE_TONE: Record<string, string> = {
  COMPLETE: "ok",
  PRODUCING: "busy",
  PREFLIGHTING: "busy",
  APPROVED: "busy",
  WAITING_FOR_APPROVAL: "wait",
  CREATED: "wait",
  BLOCKED: "bad",
  FAILED: "bad",
  CANCELLED: "bad",
};

/** What the left dock is showing. One dock, because "the node library" and "what I made
 *  yesterday" compete for exactly the same strip of screen, and only one is ever in use. */
type DockTab = "nodes" | "models" | "history";

const DOCK_LABEL: Record<DockTab, string> = {
  nodes: "Node library",
  models: "Model library",
  history: "Run history",
};

function QueuePanel({ runs, onClose }: { runs: readonly RunSummary[] | undefined; onClose(): void }) {
  return (
    <section className="cf-queue" aria-label="Job queue">
      <header className="cf-queue__head">
        <h2 className="cf-queue__title">Job Queue</h2>
        <button type="button" className="cf-queue__close" aria-label="Close job queue" onClick={onClose}>
          ✕
        </button>
      </header>
      <p className="cf-queue__hint">
        Durable runs — started from Create or by Run on this canvas. Local runs made with{" "}
        <code>content-factory make</code> are not here: they are under History, with their outputs.
      </p>
      <ul className="cf-queue__list">
        {(runs ?? []).map((run) => (
          <li key={run.run_id}>
            <Link to="/projects/$runId" params={{ runId: run.run_id }} className="cf-queue__row">
              <span className="cf-queue__name">{run.campaign_id} · {run.quality}</span>
              <span className="cf-queue__state" data-tone={RUN_STATE_TONE[run.state] ?? "wait"}>
                {run.state.toLowerCase().replaceAll("_", " ")}
              </span>
            </Link>
          </li>
        ))}
        {(runs ?? []).length === 0 && <li className="cf-queue__empty">No runs yet.</li>}
      </ul>
    </section>
  );
}

export function WorkspacePage() {
  const initial = useMemo(loadWorkspace, []);
  const [graphs, setGraphs] = useState<readonly WorkspaceGraph[]>(initial.graphs);
  const [activeId, setActiveId] = useState(initial.activeId);
  const [dockOpen, setDockOpen] = useState(true);
  const [dockTab, setDockTab] = useState<DockTab>("nodes");
  const [overviewOpen, setOverviewOpen] = useState(true);
  const [queueOpen, setQueueOpen] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  /** The run open over the canvas. Null is the canvas, which is what the workspace is for. */
  const [openRun, setOpenRun] = useState<string | null>(null);
  /**
   * The run whose output is drawn on the nodes. Separate from `openRun` on purpose.
   *
   * Picking a run sets both, and closing the run's pane clears only the first — so the operator
   * lands on the canvas with that run's drawings on the nodes that made them, which is the view
   * the whole feature exists for. A pane over the canvas cannot be the answer to "show me the
   * output on the node page", because it is in front of the node page.
   */
  const [shownRun, setShownRun] = useState<string | null>(null);
  /** The node whose full output is open: every frame at size, the voice lines playing. */
  const [openNode, setOpenNode] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [runState, setRunState] = useState<
    | null
    | { kind: "starting" }
    | { kind: "started"; result: GraphRunStarted }
    | { kind: "blocked"; problems: string[] }
    | { kind: "error"; message: string }
  >(null);
  const addOffset = useRef(0);
  const queryClient = useQueryClient();
  // One debounce timer PER graph: a single shared timer would let an edit to graph B cancel
  // graph A's still-pending save, silently losing A's edit on the server.
  const putTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const [saveError, setSaveError] = useState<string | null>(null);
  const graphsRef = useRef(graphs);
  graphsRef.current = graphs;

  /** Debounced write-behind to the server; localStorage already has the same bytes. */
  const queuePut = useCallback((graph: WorkspaceGraph) => {
    const pending = putTimers.current.get(graph.graph_id);
    if (pending) clearTimeout(pending);
    putTimers.current.set(
      graph.graph_id,
      setTimeout(() => {
        putTimers.current.delete(graph.graph_id);
        void api.graphs
          .put(graph.graph_id, JSON.parse(serializeGraph(graph)))
          .then(() => setSaveError(null))
          .catch((err) => {
            // Offline (status 0) or a lost session: the browser copy stays the source until
            // the next save. A 4xx/5xx means the server REFUSED the document — surface it,
            // or the operator edits for an hour believing everything is persisted.
            if (isApiError(err) && err.status >= 400) setSaveError(err.detail);
          });
      }, 800),
    );
  }, []);

  // First mount: adopt the server's graphs when it has any; seed it from local when it has none.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const listing = await api.graphs.list();
        if (cancelled) return;
        if (listing.length === 0) {
          await Promise.all(
            graphsRef.current.map((graph) =>
              api.graphs.put(graph.graph_id, JSON.parse(serializeGraph(graph))),
            ),
          );
          return;
        }
        const docs = (
          await Promise.all(listing.map((item) => api.graphs.get(item.graph_id)))
        ).map(parseGraph);
        if (cancelled || docs.length === 0) return;
        if (putTimers.current.size > 0) return; // the operator already edited locally; their copy wins
        // Merge, never clobber: a graph that exists only in this browser must survive adoption.
        const serverIds = new Set(docs.map((g) => g.graph_id));
        const localOnly = graphsRef.current.filter((g) => !serverIds.has(g.graph_id));
        const merged = [...docs, ...localOnly];
        setGraphs(merged);
        saveGraphs(merged);
        for (const graph of localOnly) queuePut(graph);
        const kept = merged.find((g) => g.graph_id === activeId) ?? merged[0]!;
        editor.replaceGraph(kept);
        setActiveId(kept.graph_id);
      } catch {
        // Server unreachable: localStorage keeps working; the next successful save syncs.
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeGraph = graphs.find((g) => g.graph_id === activeId) ?? graphs[0]!;

  const editor = useGraphEditor(workspaceCatalog, {
    initialGraph: activeGraph,
    onChange: (graph) => {
      setGraphs((current) => {
        const next = current.map((g) => (g.graph_id === graph.graph_id ? graph : g));
        saveGraphs(next);
        return next;
      });
      queuePut(graph);
      setChecked(false);
      setRunState(null);
    },
  });

  // Files dropped on the canvas: uploaded, identified from their bytes, and turned into the
  // node that can hold them plus the steps worth taking on them.
  const dropState = useFileDrops(editor);

  const { data: runs } = useQuery(runsQuery);
  const activeRuns = (runs ?? []).filter((r) => !["COMPLETE", "FAILED", "CANCELLED"].includes(r.state)).length;
  // The same cached list the dock's history panel reads. It is here for one number: how many
  // drawings are waiting on somebody, on the button that opens them. A gate nobody can see from
  // the screen they work on is a gate that parks 25 runs overnight.
  const { data: history } = useQuery(historyQuery);
  const awaitingReview = (history ?? []).reduce((sum, run) => sum + run.awaiting_review, 0);

  // The run laid over the canvas, and its gate. Both are the same cached queries the run pane
  // and the history list read, so opening a run and showing it on the canvas is one fetch.
  const shown = useQuery({ ...historyRunQuery(shownRun ?? ""), enabled: shownRun !== null });
  const shownReview = useQuery({ ...runReviewQuery(shownRun ?? ""), enabled: shownRun !== null });
  const unreviewed = (shownReview.data?.reviews ?? []).reduce((sum, r) => sum + r.unreviewed, 0);
  const onCanvas = useMemo(
    () =>
      runOnCanvas(editor.graph.nodes, shown.data, {
        awaitingByNode: awaitingByNode(shown.data, unreviewed),
      }),
    [editor.graph.nodes, shown.data, unreviewed],
  );
  const openNodeStep = openNode ? onCanvas.stepByNodeId[openNode] : undefined;

  /**
   * Show a run on the canvas and open it.
   *
   * One click does both because they are one intention — "let me look at this run" — and the
   * pane is closed with its own ✕, which is what leaves the canvas showing it.
   */
  const selectRun = (runId: string) => {
    setOpenRun(runId);
    setShownRun(runId);
    setOpenNode(null);
  };

  /** Step through the history without going back to the list. Newest first, as the list is. */
  const stepRun = (delta: number) => {
    const rows: readonly HistoryRun[] = history ?? [];
    const at = rows.findIndex((r) => r.run_id === shownRun);
    // Not in the list at all — it is older than the hundred rows the history serves, or the list
    // has not arrived. Stepping from nowhere would jump to the newest run, which is not "next".
    if (at < 0) return;
    const next = rows[at + delta];
    if (next) selectRun(next.run_id);
  };

  useEffect(() => saveActive(activeId), [activeId]);

  /** Clicking the tab you are already on closes the dock, the way the other side panels behave. */
  const toggleDock = (tab: DockTab) => {
    if (dockOpen && dockTab === tab) setDockOpen(false);
    else {
      setDockTab(tab);
      setDockOpen(true);
    }
  };

  const switchTo = (graph: WorkspaceGraph) => {
    setActiveId(graph.graph_id);
    editor.replaceGraph(graph);
    setChecked(false);
    // The drop strip points at nodes in the graph that was open; they are not in this one.
    dropState.reset();
  };

  const addGraph = () => {
    const graph = newUntitledGraph(graphs);
    setGraphs((current) => {
      const next = [...current, graph];
      saveGraphs(next);
      return next;
    });
    queuePut(graph);
    switchTo(graph);
  };

  const closeGraph = (graphId: string) => {
    if (graphs.length <= 1) return;
    if (typeof window.confirm === "function" && !window.confirm("Delete this graph? It only exists in this browser.")) {
      return;
    }
    const remaining = graphs.filter((g) => g.graph_id !== graphId);
    setGraphs(remaining);
    saveGraphs(remaining);
    void api.graphs.delete(graphId).catch(() => {});
    if (activeId === graphId) switchTo(remaining[0]!);
  };

  const useTemplate = (template: WorkflowTemplate) => {
    const graph = template.build();
    setGraphs((current) => {
      const next = [...current, graph];
      saveGraphs(next);
      return next;
    });
    queuePut(graph);
    switchTo(graph);
    setTemplatesOpen(false);
  };

  /**
   * A block goes into the graph that is open, not into a new tab: it is a step, not a lane. It
   * lands to the right of everything already there so it never covers a node, and folded, so the
   * graph gains one node called "Clean up the voice" rather than three the operator has to read.
   */
  const insertBlock = (block: WorkflowBlock) => {
    const nodes = editor.graph.nodes;
    const right = nodes.length > 0 ? Math.max(...nodes.map((n) => n.x)) + 380 : 80;
    const top = nodes.length > 0 ? Math.min(...nodes.map((n) => n.y)) : 80;
    editor.apply(insertBlockOps(editor.graph, block, { x: right, y: top }), `add ${block.name}`);
    setTemplatesOpen(false);
  };

  const addFromLibrary = (type: string) => {
    addOffset.current = (addOffset.current + 1) % 8;
    const spread = addOffset.current * 36;
    editor.addNode(type, { x: 80 + spread, y: 80 + spread });
  };

  const errors = editor.problems.filter((p) => p.severity === "error");
  const warnings = editor.problems.filter((p) => p.severity === "warning");

  const runGraph = async () => {
    setChecked(true);
    if (errors.length > 0) return;
    setRunState({ kind: "starting" });
    try {
      const graph = editor.graph;
      const pending = putTimers.current.get(graph.graph_id);
      if (pending) {
        clearTimeout(pending);
        putTimers.current.delete(graph.graph_id);
      }
      await api.graphs.put(graph.graph_id, JSON.parse(serializeGraph(graph)));
      const result = await api.graphs.run(graph.graph_id, "demo");
      setRunState({ kind: "started", result });
      void queryClient.invalidateQueries({ queryKey: queryKeys.runs });
    } catch (err) {
      if (isApiError(err) && err.status === 422) {
        const body = err.data as { detail?: { problems?: string[] } } | null;
        const problems = Array.isArray(body?.detail?.problems) && body.detail.problems.length > 0
          ? body.detail.problems
          : [err.detail];
        setRunState({ kind: "blocked", problems });
      } else {
        setRunState({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      }
    }
  };

  return (
    <div className="cf-workspace">
      <div className="cf-workspace__top">
        <div className="cf-workspace__tabs" role="tablist" aria-label="Open graphs">
          {graphs.map((graph) => (
            <span
              key={graph.graph_id}
              className="cf-wstab"
              data-active={graph.graph_id === activeGraph.graph_id || undefined}
            >
              <button
                type="button"
                role="tab"
                aria-selected={graph.graph_id === activeGraph.graph_id}
                className="cf-wstab__button"
                onClick={() => graph.graph_id !== activeGraph.graph_id && switchTo(graph)}
              >
                <TabName
                  graph={graph}
                  active={graph.graph_id === activeGraph.graph_id}
                  onRename={(name) => editor.renameGraph(name)}
                />
              </button>
              {graphs.length > 1 && (
                <button
                  type="button"
                  className="cf-wstab__close"
                  aria-label={`Delete graph ${graph.name}`}
                  onClick={() => closeGraph(graph.graph_id)}
                >
                  ✕
                </button>
              )}
            </span>
          ))}
          <button type="button" className="cf-wstab__new" aria-label="New graph" onClick={addGraph}>
            +
          </button>
        </div>

        <div className="cf-workspace__actions">
          <button
            type="button"
            className="cf-wsbtn"
            aria-label="Toggle node library"
            aria-pressed={dockOpen && dockTab === "nodes"}
            onClick={() => toggleDock("nodes")}
          >
            Nodes
          </button>
          <button
            type="button"
            className="cf-wsbtn"
            aria-label="Toggle model library"
            aria-pressed={dockOpen && dockTab === "models"}
            onClick={() => toggleDock("models")}
          >
            Models
          </button>
          <button
            type="button"
            className="cf-wsbtn"
            aria-pressed={templatesOpen}
            onClick={() => setTemplatesOpen((o) => !o)}
          >
            Templates
          </button>
          {/* No aria-label: the badge is part of what this button says, and "History, 2 to
              review" is the whole point of putting the count here. */}
          <button
            type="button"
            className="cf-wsbtn"
            aria-pressed={dockOpen && dockTab === "history"}
            onClick={() => toggleDock("history")}
          >
            History
            {awaitingReview > 0 && (
              <span className="cf-wsbtn__badge">{awaitingReview} to review</span>
            )}
          </button>
          <button type="button" className="cf-wsbtn" disabled={!editor.canUndo} onClick={editor.undo}>
            Undo
          </button>
          <button type="button" className="cf-wsbtn" disabled={!editor.canRedo} onClick={editor.redo}>
            Redo
          </button>
          <button
            type="button"
            className="cf-wsbtn"
            aria-label="Toggle workflow overview"
            aria-pressed={overviewOpen}
            onClick={() => setOverviewOpen((o) => !o)}
          >
            Overview
          </button>
        </div>
      </div>

      <div className="cf-workspace__body">
        {dockOpen && (
          <aside
            className="cf-workspace__library"
            data-tab={dockTab}
            aria-label={DOCK_LABEL[dockTab]}
          >
            {dockTab === "nodes" && <NodeLibraryPanel catalog={workspaceCatalog} onAdd={addFromLibrary} />}
            {dockTab === "models" && <ModelsPanel />}
            {dockTab === "history" && (
              <HistoryPanel selected={shownRun ?? openRun} onSelect={selectRun} />
            )}
          </aside>
        )}

        <div className="cf-workspace__canvas">
          {/* Over the canvas, not instead of it: the editor keeps its scroll, its selection and
              its pending edits while a run is being looked at, and closing the run puts the graph
              back exactly as it was. */}
          {openRun && <RunPane runId={openRun} onClose={() => setOpenRun(null)} />}
          {/* The node's own output, over the canvas but under nothing else: it is opened from a
              node and closed back to the same node, so it must not push the graph around. */}
          {openNode && openNodeStep && shownRun && (
            <NodeOutputPane
              runId={shownRun}
              nodeTitle={
                editor.graph.nodes.find((n) => n.id === openNode)?.title ??
                workspaceCatalog.get(editor.graph.nodes.find((n) => n.id === openNode)?.type ?? "")
                  ?.title ??
                openNodeStep.node
              }
              step={openNodeStep}
              files={onCanvas.filesByNodeId[openNode] ?? []}
              onClose={() => setOpenNode(null)}
            />
          )}
          <NodeGraphEditor
            editor={editor}
            aria-label={`Graph: ${activeGraph.name}`}
            outputs={onCanvas.byNodeId}
            onOpenOutputs={setOpenNode}
            onDropFiles={dropState.onDropFiles}
          />
          <DropSuggestions state={dropState} editor={editor} />
          {shownRun && (
            /* What the canvas is currently showing, with the way out of it and the way to the
               next one. It is a strip at the top rather than a note in a panel because the graph
               underneath now has somebody else's drawings on it, and a canvas that is quietly
               showing last night's run is a canvas nobody can trust. */
            <section className="cf-onnodes" aria-label="Run shown on the canvas">
              <span className="cf-onnodes__label">On the nodes</span>
              <span className="cf-onnodes__name">
                {shown.data?.subject ?? runLabel({ run_id: shownRun })}
              </span>
              <span className="cf-onnodes__meta">
                {shown.data ? (
                  <>
                    {runLabel(shown.data)} · {shown.data.workflow ?? "unknown lane"} ·{" "}
                    <time dateTime={new Date(shown.data.finished_at * 1000).toISOString()}>
                      {formatExact(shown.data.finished_at)}
                    </time>
                  </>
                ) : (
                  "reading the run…"
                )}
              </span>
              {onCanvas.unmatchedSteps.length > 0 && (
                /* The graph on screen is not the lane that ran. Said plainly, with the steps
                   named, because the alternative is a canvas that looks like the run produced
                   nothing at six of its nodes. */
                <span className="cf-onnodes__warn">
                  {onCanvas.unmatchedSteps.length} step
                  {onCanvas.unmatchedSteps.length === 1 ? "" : "s"} of this run are not on this
                  canvas: {onCanvas.unmatchedSteps.join(", ")}
                </span>
              )}
              {onCanvas.unattributed > 0 && (
                <span className="cf-onnodes__warn">
                  {onCanvas.unattributed} file{onCanvas.unattributed === 1 ? "" : "s"} this run did
                  not record a step for
                </span>
              )}
              <span className="cf-onnodes__nav">
                <button type="button" aria-label="Newer run" onClick={() => stepRun(-1)}>
                  ↑
                </button>
                <button type="button" aria-label="Older run" onClick={() => stepRun(1)}>
                  ↓
                </button>
                {!openRun && (
                  <button type="button" onClick={() => setOpenRun(shownRun)}>
                    Open
                  </button>
                )}
                <button
                  type="button"
                  aria-label="Clear the run from the canvas"
                  onClick={() => {
                    setShownRun(null);
                    setOpenNode(null);
                  }}
                >
                  ✕
                </button>
              </span>
            </section>
          )}
          <div className="cf-runbar" role="toolbar" aria-label="Run controls">
            <button
              type="button"
              className="cf-runbar__run"
              disabled={runState?.kind === "starting"}
              onClick={() => void runGraph()}
            >
              <span className="cf-wsbtn__tri" aria-hidden="true" />
              {runState?.kind === "starting" ? "Starting…" : "Run"}
            </button>
            <button type="button" className="cf-runbar__btn" onClick={() => setChecked(true)}>
              Check graph
            </button>
            <button
              type="button"
              className="cf-runbar__btn"
              aria-pressed={queueOpen}
              onClick={() => setQueueOpen((o) => !o)}
            >
              {activeRuns} active
            </button>
          </div>
          {saveError && (
            <p className="cf-check__warnings" role="alert">
              Not saved to the server: {saveError}
            </p>
          )}
          {checked && (
            <section className="cf-check" aria-label="Graph check result">
              <header className="cf-check__head">
                <strong>
                  {runState?.kind === "blocked"
                    ? "Run refused"
                    : errors.length === 0
                      ? "Graph is valid"
                      : `${errors.length} problem${errors.length === 1 ? "" : "s"}`}
                </strong>
                <button type="button" aria-label="Close check result" onClick={() => setChecked(false)}>
                  ✕
                </button>
              </header>
              {errors.length > 0 && (
                <ul className="cf-check__list">
                  {errors.map((p, i) => (
                    <li key={i}>{p.message}</li>
                  ))}
                </ul>
              )}
              {errors.length === 0 && editor.order && (
                <p className="cf-check__order">
                  Order: {editor.order
                    .map((id) => {
                      const node = editor.graph.nodes.find((n) => n.id === id);
                      const def = node ? workspaceCatalog.get(node.type) : undefined;
                      return def?.kind === "note" ? null : (node?.title ?? def?.title ?? id);
                    })
                    .filter(Boolean)
                    .join(" → ")}
                </p>
              )}
              {warnings.length > 0 && (
                <p className="cf-check__warnings">
                  {warnings.length} warning{warnings.length === 1 ? "" : "s"} (unused outputs)
                </p>
              )}
              {runState?.kind === "started" && (
                <p className="cf-check__started" role="status">
                  Run <Link to="/projects/$runId" params={{ runId: runState.result.run_id }}>{runState.result.run_id}</Link>{" "}
                  started ({runState.result.dag_nodes} stages, {runState.result.deliverable_type}). It waits at the
                  approval gate like every production run.
                </p>
              )}
              {runState?.kind === "blocked" && (
                <ul className="cf-check__list">
                  {runState.problems.map((problem, index) => (
                    <li key={index}>{problem}</li>
                  ))}
                </ul>
              )}
              {runState?.kind === "error" && <p className="cf-check__warnings">{runState.message}</p>}
              <p className="cf-check__honest">
                Saved to the server (and this browser). Run compiles the graph onto the production pipeline —
                stages without an executor yet are refused with a reason, and publishing stays behind the
                gated distribution flow.
              </p>
            </section>
          )}
        </div>

        {overviewOpen && (
          <aside className="cf-workspace__overview" aria-label="Workflow overview">
            <header className="cf-workspace__overview-head">
              <h2>Workflow Overview</h2>
            </header>
            <PropertiesPanel editor={editor} />
          </aside>
        )}

        {queueOpen && <QueuePanel runs={runs} onClose={() => setQueueOpen(false)} />}
        {templatesOpen && (
          <TemplatesPanel
            onUse={useTemplate}
            onInsertBlock={insertBlock}
            onClose={() => setTemplatesOpen(false)}
          />
        )}
      </div>
    </div>
  );
}
