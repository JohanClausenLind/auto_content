/**
 * Joining a run to a graph — and refusing to when the answer would be a guess.
 *
 * The canvas and a run name the same step differently: a graph node has a generated id, a run
 * records what each step produced under the lane's own key. `GraphNode.key` is the join, and
 * everything worth testing here is a case where getting it wrong would point at the wrong node —
 * which is worse than pointing at none, because the wrong node is where somebody will look for
 * the cause of a fault that is somewhere else.
 */

import { makeNode } from "@content-factory/node-graph";
import type { GraphNode } from "@content-factory/node-graph";
import { describe, expect, it } from "vitest";
import type { HistoryRunDetail } from "../src/api/types";
import { workspaceCatalog } from "../src/workspace/catalog";
import { awaitingByNode, runOnCanvas } from "../src/workspace/nodeOutputs";
import { HISTORY_DETAIL, makeRunStep } from "./msw/runs";

function node(type: string, key: string): GraphNode {
  return makeNode(workspaceCatalog, type, { key, x: 0, y: 0 });
}

describe("laying a run over a graph", () => {
  it("puts each step's files on the node that carries its lane key", () => {
    const anchor = node("generate_anchor", "anchor");
    const cut = node("compose_video", "cut");
    const mix = node("mix_audio", "mix");

    const laid = runOnCanvas([anchor, cut, mix], HISTORY_DETAIL);

    // The drawing and its control map are on `anchor`, the film and its captions on `cut`, the
    // narration on `mix`.
    expect(laid.byNodeId[anchor.id]?.counts.image).toBe(2);
    expect(laid.byNodeId[anchor.id]?.previews[0]?.url).toContain("anchors/frames/0000.png");
    expect(laid.byNodeId[cut.id]?.counts.video).toBe(1);
    expect(laid.byNodeId[cut.id]?.counts.text).toBe(1);
    expect(laid.byNodeId[mix.id]?.counts.audio).toBe(1);
    // The step's own facts travel with it, for the panel the node's button opens.
    expect(laid.stepByNodeId[anchor.id]?.facts).toMatchObject({ attempts: 3 });
  });

  it("keeps a control map out of the thumbnails without hiding it from the count", () => {
    const anchor = node("generate_anchor", "anchor");
    const laid = runOnCanvas([anchor], HISTORY_DETAIL);

    // Two images are attributed to `anchor`: the drawing and a pose skeleton.
    expect(laid.byNodeId[anchor.id]?.counts.image).toBe(2);
    // Only the drawing is drawn on the node. A node whose thumbnails are pose skeletons is a
    // node showing nothing anybody recognises.
    expect(laid.byNodeId[anchor.id]?.previews).toHaveLength(1);
    expect(laid.filesByNodeId[anchor.id]).toHaveLength(2);
  });

  it("attaches nothing to a hand-built node, whatever its type", () => {
    // No lane key, no claim. Matching on stage type instead would put this run's drawings on any
    // `generate_anchor` node on any canvas — and the workspace is built around having several.
    const handBuilt = makeNode(workspaceCatalog, "generate_anchor", { x: 0, y: 0 });
    const laid = runOnCanvas([handBuilt], HISTORY_DETAIL);

    expect(laid.byNodeId).toEqual({});
    expect(laid.unmatchedSteps).toContain("anchor");
  });

  it("refuses to choose when two nodes claim the same lane step", () => {
    const first = node("generate_anchor", "anchor");
    const second = node("generate_anchor", "anchor");
    const laid = runOnCanvas([first, second], HISTORY_DETAIL);

    // Showing the drawings on an arbitrary one of them is worse than showing them on neither.
    expect(laid.byNodeId[first.id]).toBeUndefined();
    expect(laid.byNodeId[second.id]).toBeUndefined();
    expect(laid.unmatchedSteps).toContain("anchor");
  });

  it("names the run's steps that are not on this canvas, and only the ones with files", () => {
    const laid = runOnCanvas([node("mix_audio", "mix")], HISTORY_DETAIL);

    // `anchor` and `cut` produced files and have no node here — that is the sentence that
    // explains a canvas which otherwise looks like the run made nothing.
    expect(laid.unmatchedSteps).toEqual(expect.arrayContaining(["anchor", "cut"]));
    // `pack` produced nothing, so its absence explains nothing and is not reported.
    expect(laid.unmatchedSteps).not.toContain("pack");
  });

  it("marks a step this run did not execute as carried, not as empty or failed", () => {
    const run: HistoryRunDetail = {
      ...HISTORY_DETAIL,
      nodes: [
        makeRunStep({
          node: "anchor",
          stage: "generate_anchor",
          ran: false,
          ok: false,
          outputs_total: 1,
        }),
      ],
    };
    const anchor = node("generate_anchor", "anchor");
    const laid = runOnCanvas([anchor], run);

    // A resumed run is most of the lane, and those nodes' files are real — left by the run that
    // made them. Dimmed, never badged as a failure.
    expect(laid.byNodeId[anchor.id]?.carried).toBe(true);
    expect(laid.byNodeId[anchor.id]?.counts.image).toBe(2);
  });

  it("reports the run's own file count, not the truncated list's", () => {
    const run: HistoryRunDetail = {
      ...HISTORY_DETAIL,
      nodes: [
        makeRunStep({ node: "anchor", stage: "generate_anchor", outputs_total: 2904 }),
      ],
    };
    const anchor = node("generate_anchor", "anchor");
    // A step that wrote 2,904 files says so, even though the response carries a few hundred.
    expect(runOnCanvas([anchor], run).byNodeId[anchor.id]?.total).toBe(2904);
  });

  it("shows nothing at all with no run open", () => {
    const laid = runOnCanvas([node("generate_anchor", "anchor")], undefined);
    expect(laid.byNodeId).toEqual({});
    expect(laid.unattributed).toBe(0);
  });
});

describe("which node the review badge belongs on", () => {
  it("puts the waiting count on the lane's gate", () => {
    const run: HistoryRunDetail = {
      ...HISTORY_DETAIL,
      nodes: [
        makeRunStep({ node: "anchor", stage: "generate_anchor" }),
        makeRunStep({ node: "frames_gate", stage: "review_frames", ok: false, blocked: true }),
      ],
    };
    expect(awaitingByNode(run, 6)).toEqual({ frames_gate: 6 });
  });

  it("puts it nowhere when a lane has two gates", () => {
    const run: HistoryRunDetail = {
      ...HISTORY_DETAIL,
      nodes: [
        makeRunStep({ node: "gate_a", stage: "review_frames" }),
        makeRunStep({ node: "gate_b", stage: "review_frames" }),
      ],
    };
    // The count is per deliverable and the deliverables are not keyed by node, so attributing it
    // would be a guess — and a badge on the wrong gate sends a reviewer to the wrong pictures.
    expect(awaitingByNode(run, 6)).toEqual({});
  });

  it("puts it nowhere when nothing is waiting", () => {
    expect(awaitingByNode(HISTORY_DETAIL, 0)).toEqual({});
  });
});
