/**
 * Blocks: the parts of a lane you add to the graph you are already editing.
 *
 * What has to be true of every block, checked for every block rather than for one:
 *
 * - its nodes are real node types and its wires connect slots that exist and whose types fit —
 *   otherwise it is a fragment that looks fine on a card and cannot be inserted;
 * - inserting it is ONE undoable edit that leaves the graph valid apart from the inputs it is
 *   waiting to be given;
 * - what lands is folded into exactly one group, because the whole point is that a graph gains a
 *   step called "Clean up the voice" rather than three nodes to recognise;
 * - and the block's own description of what it takes and gives matches the graph, so a card
 *   cannot promise a boundary the nodes do not have.
 */

import { describe, expect, it } from "vitest";
import {
  applyOps,
  canConnect,
  emptyGraph,
  groupPorts,
  makeNode,
  newId,
  parseGraph,
  serializeGraph,
  validateGraph,
} from "@content-factory/node-graph";
import { BLOCKS, blockById, insertBlockOps } from "../src/workspace/blocks";
import { workspaceCatalog } from "../src/workspace/catalog";

function insert(blockId: string) {
  const block = blockById(blockId);
  if (!block) throw new Error(`no block ${blockId}`);
  const graph = emptyGraph(newId("graph"), "test");
  return { block, graph: applyOps(graph, insertBlockOps(graph, block, { x: 0, y: 0 })).graph };
}

describe("workflow blocks", () => {
  it("every block names real node types and real widget values", () => {
    for (const block of BLOCKS) {
      for (const node of block.nodes) {
        const def = workspaceCatalog.get(node.type);
        expect(def, `${block.id}: unknown node type ${node.type}`).toBeDefined();
        for (const [name, value] of Object.entries(node.values ?? {})) {
          const widget = def!.widgets.find((w) => w.name === name);
          expect(widget, `${block.id}.${node.key} sets ${name}, which it does not declare`).toBeDefined();
          if (widget!.options) {
            const chosen =
              widget!.kind === "chips" ? String(value).split(",") : [String(value)];
            for (const one of chosen) {
              expect(widget!.options, `${block.id}.${node.key}: ${name}=${one}`).toContain(one);
            }
          }
        }
      }
    }
  });

  it("every block's wires are links the canvas would actually draw", () => {
    for (const block of BLOCKS) {
      // Built the same way the insert builds it, then asked the same question the canvas asks.
      let graph = emptyGraph("g", "t");
      const ids: Record<string, string> = {};
      for (const node of block.nodes) {
        const made = makeNode(workspaceCatalog, node.type, { x: node.x, y: node.y });
        ids[node.key] = made.id;
        graph = applyOps(graph, [{ op: "add_node", node: made }]).graph;
      }
      for (const [from, output, to, input] of block.wires) {
        const verdict = canConnect(
          graph,
          workspaceCatalog,
          { node: ids[from]!, slot: output },
          { node: ids[to]!, slot: input },
        );
        expect(verdict.ok, `${block.id}: ${from}.${output} -> ${to}.${input}: ${JSON.stringify(verdict)}`).toBe(
          true,
        );
      }
    }
  });

  it("inserting a block folds it into exactly one group", () => {
    for (const block of BLOCKS) {
      const { graph } = insert(block.id);
      expect(graph.nodes).toHaveLength(block.nodes.length);
      expect(graph.links).toHaveLength(block.wires.length);
      expect(graph.groups).toHaveLength(1);
      const group = graph.groups[0]!;
      expect(group.name).toBe(block.name);
      expect(group.template).toBe(block.id);
      expect(group.collapsed).toBe(true);
      expect([...group.members].sort()).toEqual(graph.nodes.map((n) => n.id).sort());
      // And the document it produces is one the server would accept.
      expect(parseGraph(JSON.parse(serializeGraph(graph)))).toEqual(graph);
    }
  });

  it("a freshly inserted block asks only for the inputs it says it takes", () => {
    for (const block of BLOCKS) {
      const { graph } = insert(block.id);
      const errors = validateGraph(graph, workspaceCatalog).filter((p) => p.severity === "error");
      const waiting = errors.filter((e) => /is not connected|connect one of/.test(e.message));
      expect(
        errors.length - waiting.length,
        `${block.id}: ${errors.map((e) => e.message).join("; ")}`,
      ).toBe(0);
      // The folded node's ports are what an operator wires to, so the card's "takes" has to be
      // the same set — a card that promised a boundary the nodes do not have would be a lie
      // nobody could act on.
      const ports = groupPorts(graph, workspaceCatalog, graph.groups[0]!);
      const byKey = new Map(graph.nodes.map((n, i) => [n.id, block.nodes[i]?.key ?? n.id]));
      const open = new Set(
        graph.nodes.flatMap((node) => {
          const def = workspaceCatalog.get(node.type)!;
          return def.inputs
            .filter((slot) => !slot.optional && !graph.links.some((l) => l.to_node === node.id && l.to_slot === slot.name))
            .map((slot) => `${byKey.get(node.id)}.${slot.name}`);
        }),
      );
      for (const takes of block.takes) {
        // Either it is a required input still open, or an optional one the card offers.
        const [key, slot] = takes.split(".");
        const node = graph.nodes.find((n) => byKey.get(n.id) === key);
        expect(node, `${block.id}: takes names ${takes}, which is not one of its nodes`).toBeDefined();
        const def = workspaceCatalog.get(node!.type)!;
        expect(
          def.inputs.some((i) => i.name === slot),
          `${block.id}: ${takes} is not an input of ${node!.type}`,
        ).toBe(true);
      }
      for (const open_slot of open) {
        expect(block.takes, `${block.id}: ${open_slot} is required and the card does not mention it`).toContain(
          open_slot,
        );
      }
      expect(ports.inputs.length).toBeGreaterThan(0);
    }
  });

  it("every block gives back what its card says it gives", () => {
    for (const block of BLOCKS) {
      const { graph } = insert(block.id);
      const byKey = new Map(graph.nodes.map((n, i) => [block.nodes[i]?.key ?? n.id, n]));
      for (const gives of block.gives) {
        const [key, slot] = gives.split(".");
        const node = byKey.get(key!);
        expect(node, `${block.id}: gives names ${gives}`).toBeDefined();
        const def = workspaceCatalog.get(node!.type)!;
        expect(def.outputs.some((o) => o.name === slot), `${block.id}: ${gives}`).toBe(true);
      }
    }
  });

  it("covers the steps the lanes actually repeat", () => {
    const ids = BLOCKS.map((b) => b.id);
    for (const wanted of ["clean-voice", "captions", "finish-picture", "check-deliver", "publish"]) {
      expect(ids).toContain(wanted);
    }
    // Ids and names are both unique: the name is what the folded node carries.
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(BLOCKS.map((b) => b.name)).size).toBe(BLOCKS.length);
  });
});
