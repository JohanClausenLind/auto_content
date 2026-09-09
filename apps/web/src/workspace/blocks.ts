/**
 * Blocks: the parts of a lane that are the same in every lane.
 *
 * Look at the catalogue and the repetition is the first thing you see. Nine lanes clean up a
 * voice the same way. Six turn word timings into captions the same way. Every single one checks
 * the finished thing and packages it, with the same two nodes wired the same way round. Building
 * a lane by hand therefore meant placing and wiring the same four or five nodes again, correctly,
 * from memory — and reading one meant reading them again.
 *
 * A block is that fragment, once: its nodes, the wires between them, the values that make it
 * mean what it says, and the name it goes by. Inserting one adds all of it in a single undoable
 * commit **and folds it into one node**, so a graph gains a step called "Clean up the voice"
 * rather than three nodes an operator has to recognise. Opening it shows the actual nodes — the
 * same ones, in the same graph — and every widget is there to change. There is no second copy and
 * no separate format: a block is a way of writing nodes and links down, and after insertion the
 * graph is an ordinary flat graph with a folded view over part of it.
 *
 * Adding a block: put it in BLOCKS with `in` naming the slots it takes from outside. Nothing
 * validates a block into existence — the graph model's own connection rules apply to its wires
 * exactly as they do to a hand-drawn one, so a block that wires the wrong slot together simply
 * fails to insert, in the tests, the first time it is tried.
 */

import {
  applyOps,
  connectOps,
  groupNodesOps,
  makeNode,
  newId,
  type GraphOp,
  type WorkspaceGraph,
} from "@content-factory/node-graph";
import { workspaceCatalog } from "./catalog";

interface BlockNode {
  /** Key within the block, used by its wires. */
  readonly key: string;
  readonly type: string;
  /** Position relative to where the block is dropped. */
  readonly x: number;
  readonly y: number;
  readonly values?: Record<string, string | number | boolean>;
  readonly note?: string;
}

export interface WorkflowBlock {
  readonly id: string;
  /** The name the folded node carries. */
  readonly name: string;
  /** One honest line: what it does to the material. */
  readonly summary: string;
  readonly category: "audio" | "picture" | "delivery" | "story";
  readonly nodes: readonly BlockNode[];
  readonly wires: readonly [from: string, output: string, to: string, input: string][];
  /** What it takes from the rest of the graph, as `<key>.<slot>`, for the card. */
  readonly takes: readonly string[];
  /** What it hands back, as `<key>.<slot>`. */
  readonly gives: readonly string[];
}

export const BLOCKS: readonly WorkflowBlock[] = [
  {
    id: "clean-voice",
    name: "Clean up the voice",
    summary:
      "Repairs the speech and masters it to a fixed loudness: the chain nine lanes share, in one step.",
    category: "audio",
    nodes: [
      {
        key: "restore",
        type: "restore_speech",
        x: 0,
        y: 0,
        values: { cleanup: "clearervoice", band_extension: "clearervoice_sr", enhancer: "resemble_enhance" },
        note: "Gated on measured artifacts: a clean take passes through the FFmpeg tail alone.",
      },
      { key: "mix", type: "mix_audio", x: 300, y: 0, values: { target_lufs: -14 } },
    ],
    wires: [["restore", "audio", "mix", "audio"]],
    takes: ["restore.audio"],
    gives: ["mix.audio"],
  },
  {
    id: "captions",
    name: "Captions from the voice",
    summary:
      "Validates the word timings and compiles the SRT, WebVTT and burn-in track from them.",
    category: "audio",
    nodes: [
      {
        key: "timings",
        type: "align_words",
        x: 0,
        y: 0,
        note: "A gate, not an aligner: it fails the run before captions are built on a bad take.",
      },
      { key: "captions", type: "compile_captions", x: 300, y: 0 },
    ],
    wires: [["timings", "timings", "captions", "timings"]],
    takes: ["timings.audio", "timings.script"],
    gives: ["captions.captions"],
  },
  {
    id: "transcribe-plan",
    name: "Read the recording",
    summary:
      "Transcribes a recording with word timings and cuts the transcript into beats — the front of the audio lanes.",
    category: "story",
    nodes: [
      {
        key: "transcribe",
        type: "transcribe_audio",
        x: 0,
        y: 0,
        values: { engine: "faster_whisper", model: "base.en" },
      },
      {
        key: "story",
        type: "plan_story",
        x: 320,
        y: 0,
        values: { beats: 6 },
        note: "Beats are spans of what was said, each one measured, so a picture holds for its own words.",
      },
    ],
    wires: [["transcribe", "text", "story", "transcript"]],
    takes: ["transcribe.audio", "story.brief"],
    gives: ["story.story", "transcribe.audio"],
  },
  {
    id: "finish-picture",
    name: "Finish the picture",
    summary:
      "Removes what should not be in frame, restores and enlarges, then interpolates — the post chain, in order.",
    category: "picture",
    nodes: [
      {
        key: "fix",
        type: "fix_video",
        x: 0,
        y: 0,
        note: "A no-op until segmentation ids and a seed mask exist, which is why it is safe to leave in.",
      },
      { key: "upscale", type: "upscale_video", x: 280, y: 0, values: { resolution: "1080" } },
      {
        key: "smooth",
        type: "interpolate",
        x: 560,
        y: 0,
        values: { engine: "rife", factor: "2x" },
        note: "Skips itself on a held cut: interpolating held drawings invents frames nobody drew.",
      },
    ],
    wires: [
      ["fix", "frames", "upscale", "frames"],
      ["upscale", "frames", "smooth", "frames"],
    ],
    takes: ["fix.frames"],
    gives: ["smooth.frames"],
  },
  {
    id: "check-deliver",
    name: "Check and deliver",
    summary:
      "Measures the finished thing, writes one package per destination, and marks where the files land.",
    category: "delivery",
    nodes: [
      { key: "qc", type: "qc_deliverable", x: 0, y: 0 },
      { key: "pack", type: "compile_destination_packages", x: 300, y: 60 },
      { key: "deliver", type: "output.deliverables", x: 600, y: 60 },
    ],
    wires: [
      ["qc", "report", "pack", "qc"],
      ["pack", "packages", "deliver", "packages"],
    ],
    takes: ["qc.deliverable", "pack.deliverable"],
    gives: [],
  },
  {
    id: "publish",
    name: "Package and publish",
    summary:
      "One package per destination, then one approval-gated post to every destination you light up.",
    category: "delivery",
    nodes: [
      { key: "pack", type: "compile_destination_packages", x: 0, y: 0 },
      {
        key: "publish",
        type: "publish.social",
        x: 320,
        y: 0,
        values: { destinations: "export", visibility: "draft" },
        note: "export writes the package into the run's folder and sends nothing. Light up more when a profile exists.",
      },
    ],
    wires: [["pack", "packages", "publish", "packages"]],
    takes: ["pack.deliverable", "pack.qc"],
    gives: [],
  },
];

export function blockById(id: string): WorkflowBlock | undefined {
  return BLOCKS.find((block) => block.id === id);
}

/**
 * The ops that insert a block at ``position``: its nodes, its internal wires, and one collapsed
 * group over all of them, as a single commit — so one undo removes the whole block and the
 * operator never sees a half-inserted one.
 */
export function insertBlockOps(
  graph: WorkspaceGraph,
  block: WorkflowBlock,
  position: { x: number; y: number },
): GraphOp[] {
  const ops: GraphOp[] = [];
  const ids: Record<string, string> = {};
  let next = graph;
  for (const node of block.nodes) {
    const made = makeNode(workspaceCatalog, node.type, {
      x: Math.round(position.x + node.x),
      y: Math.round(position.y + node.y),
      ...(node.values ? { values: node.values } : {}),
      ...(node.note ? { note: node.note } : {}),
    });
    ids[node.key] = made.id;
    ops.push({ op: "add_node", node: made });
    // connectOps and groupNodesOps both ask the graph about nodes that do not exist yet, so the
    // ops are applied to a local copy as they are built rather than after the fact.
    next = applyOps(next, [{ op: "add_node", node: made }]).graph;
  }
  for (const [from, output, to, input] of block.wires) {
    const wire = connectOps(
      next,
      { node: ids[from]!, slot: output },
      { node: ids[to]!, slot: input },
    );
    ops.push(...wire);
    next = applyOps(next, wire).graph;
  }
  ops.push(
    ...groupNodesOps(next, Object.values(ids), {
      name: block.name,
      template: block.id,
      collapsed: true,
      id: newId("grp"),
    }),
  );
  return ops;
}
