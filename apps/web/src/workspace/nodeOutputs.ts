/**
 * Putting a run's output on the node that made it.
 *
 * The canvas and a run speak different names for the same step. A graph node has a generated id
 * (`node_kx91…`) because two graphs built from the same lane must not collide; a run records what
 * each step produced under the lane's own key (`anchor`, `spokes`, `frames_gate`), because that is
 * what `workflows/*.yaml` calls it and what `--from` takes. `GraphNode.key` is the join, carried
 * through the template builder for exactly this.
 *
 * Two honesty rules, and both of them are about not pointing at the wrong node:
 *
 * * **A hand-built node gets nothing.** No lane key, no claim. Falling back to matching on stage
 *   type would attach a run's anchor drawings to any `generate_anchor` node on any canvas, which
 *   is wrong the moment somebody has two graphs open — and the workspace is built around having
 *   several.
 * * **Two nodes claiming one key get nothing either**, and the step is reported as unmatched.
 *   Showing one run's drawings on an arbitrary one of them is worse than showing them on neither.
 * * **A run step with no node on the canvas is named rather than dropped**: a lane edited since
 *   the run is a real thing, and "3 steps of this run are not on this canvas" is the sentence that
 *   explains an otherwise empty-looking graph.
 *
 * The previews are image URLs only. A node draws four thumbnails; the voice lines, the films and
 * the text are in the review panel, which is what the node's button opens.
 */

import type { GraphNode, NodeOutputs } from "@content-factory/node-graph";
import { api } from "../api/client";
import type { HistoryRunDetail, OutputKind, RunOutput, RunStep } from "../api/types";

/** Intermediate and debug output. Same list the run pane folds away, for the same reason: a node
 *  whose four thumbnails are pose skeletons is a node showing nothing anybody recognises. */
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
  /** Run steps with no node on this canvas — the lane was edited, or this is another lane's
   *  graph. Named rather than counted: "which steps" is the actionable half. */
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

/**
 * A node's drawings and counts, from the files the run attributed to its step.
 *
 * `awaitingReview` is attached by the caller rather than derived here: whether a frame is waiting
 * on somebody is the gate's business (`reviews/frames/batch.json`), not the output list's, and a
 * count guessed from filenames would put a badge on the wrong node.
 */
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

/**
 * Lay a run over a graph.
 *
 * `awaitingByNode` is what the gate says is still waiting, keyed by lane node key — the caller
 * gets it from the review page, which is the only thing that knows.
 */
export function runOnCanvas(
  nodes: readonly GraphNode[],
  run: HistoryRunDetail | undefined,
  options: { readonly awaitingByNode?: Readonly<Record<string, number>> } = {},
): RunOnCanvas {
  if (!run) return NOTHING;
  // A duplicate key means two nodes on this canvas claim the same lane step. That cannot be
  // resolved — showing one run's drawings on an arbitrary one of them is worse than showing them
  // on neither — so both are dropped and the step is reported as unmatched.
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

/**
 * Which lane node each gate belongs to, and how many of its drawings are still undecided.
 *
 * The gate is recorded per *deliverable*, and the node that stopped is whichever step of the lane
 * is a `review_frames`. Matched through the run's own step list rather than assumed, so a lane
 * with two gates puts each count on its own node.
 */
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
