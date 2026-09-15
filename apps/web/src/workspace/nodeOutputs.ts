/** Putting a run's output on the node that made it: `GraphNode.key` is the join. */

import type { GraphNode, NodeOutputs } from "@content-factory/node-graph";
import { api } from "../api/client";
import type { HistoryRunDetail, OutputKind, RunOutput, RunStep } from "../api/types";

/** Intermediate and debug output, the same list the run pane folds away. */
const DEBUG_ROLES = new Set(["control", "anchor-upscaled", "render", "input", "marker"]);

const EMPTY_COUNTS: Record<OutputKind, number> = {
  image: 0,
  video: 0,
  audio: 0,
  text: 0,
  data: 0,
};

const PREVIEWS_PER_NODE = 4;

export interface RunOnCanvas {
  /** What to hand `NodeGraphEditor`: outputs keyed by the canvas's own node ids. */
  readonly byNodeId: Record<string, NodeOutputs>;
  /** The run's step for each canvas node id, for the panel that opens one. */
  readonly stepByNodeId: Record<string, RunStep>;
  /** That step's files, resolved, in the order the panel shows them. */
  readonly filesByNodeId: Record<string, readonly RunOutput[]>;
  /** Run steps with no node on this canvas. */
  readonly unmatchedSteps: readonly string[];
  /** Files the run could not attribute to any step at all. Shown in the run pane, not on a node. */
  readonly unattributed: number;
}

const NOTHING: RunOnCanvas = {
  byNodeId: {},
  stepByNodeId: {},
  filesByNodeId: {},
  unmatchedSteps: [],
  unattributed: 0,
};

/** A node's drawings and counts from the files attributed to its step. */
function outputsFor(
  step: RunStep,
  files: readonly RunOutput[],
  runId: string,
  awaitingReview: number,
): NodeOutputs {
  const counts = { ...EMPTY_COUNTS };
  for (const file of files) counts[file.kind] += 1;
  const previews = files
    .filter((file) => file.kind === "image" && !DEBUG_ROLES.has(file.role))
    .slice(0, PREVIEWS_PER_NODE)
    .map((file) => ({
      url: api.history.fileUrl(runId, file.path),
      label: file.path.split("/").pop() ?? file.path,
      kind: "image" as const,
    }));
  return {
    counts,
    previews,
    // The run's own count where it has one, so a step that wrote 2,904 files says so even though
    // the response carries the first few hundred.
    total: Math.max(step.outputs_total, files.length),
    attribution: step.attribution,
    ...(awaitingReview > 0 ? { awaitingReview } : {}),
    // A node the open run did not execute: its files are whatever an earlier run left there. The
    // strip dims rather than hides them, because on a resumed run that is most of the lane.
    ...(step.ran ? {} : { carried: true }),
  };
}

/** Lay a run over a graph; `awaitingByNode` is what the gate says is still waiting. */
export function runOnCanvas(
  nodes: readonly GraphNode[],
  run: HistoryRunDetail | undefined,
  options: { readonly awaitingByNode?: Readonly<Record<string, number>> } = {},
): RunOnCanvas {
  if (!run) return NOTHING;
  // A duplicate key means two nodes claim the same lane step; showing the drawings on an arbitrary
  // one is worse than on neither, so both are dropped and the step is reported as unmatched.
  const byKey = new Map<string, GraphNode>();
  const ambiguous = new Set<string>();
  for (const node of nodes) {
    if (node.key === "") continue; // hand-built: no lane key, no claim
    if (byKey.has(node.key)) ambiguous.add(node.key);
    else byKey.set(node.key, node);
  }
  for (const key of ambiguous) byKey.delete(key);

  const filesByStep = new Map<string, RunOutput[]>();
  for (const output of run.outputs) {
    if (output.node === null) continue;
    const list = filesByStep.get(output.node);
    if (list) list.push(output);
    else filesByStep.set(output.node, [output]);
  }

  const byNodeId: Record<string, NodeOutputs> = {};
  const stepByNodeId: Record<string, RunStep> = {};
  const filesByNodeId: Record<string, readonly RunOutput[]> = {};
  const unmatchedSteps: string[] = [];
  for (const step of run.nodes) {
    const node = byKey.get(step.node);
    if (!node) {
      // A step with no files and no node is not worth reporting: an `input.brief` node produces
      // nothing and its absence from a graph explains nothing.
      if ((filesByStep.get(step.node)?.length ?? 0) > 0) unmatchedSteps.push(step.node);
      continue;
    }
    const files = filesByStep.get(step.node) ?? [];
    stepByNodeId[node.id] = step;
    filesByNodeId[node.id] = files;
    byNodeId[node.id] = outputsFor(step, files, run.run_id, options.awaitingByNode?.[step.node] ?? 0);
  }
  return {
    byNodeId,
    stepByNodeId,
    filesByNodeId,
    unmatchedSteps,
    unattributed: run.unattributed,
  };
}

/** Which lane node each gate belongs to and how many drawings are undecided. */
export function awaitingByNode(
  run: HistoryRunDetail | undefined,
  unreviewedTotal: number,
): Record<string, number> {
  if (!run || unreviewedTotal <= 0) return {};
  const gates = run.nodes.filter((step) => step.stage === "review_frames");
  // One gate is the usual case and the only one that can be attributed without guessing: with
  // two, the count is a per-deliverable number and the deliverables are not keyed by node.
  return gates.length === 1 ? { [gates[0]!.node]: unreviewedTotal } : {};
}
