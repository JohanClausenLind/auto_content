import { describe, expect, it } from "vitest";
import {
  applyOps,
  emptyGraph,
  makeNode,
  validateGraph,
  type WorkspaceGraph,
} from "../src/graphModel";
import {
  estimateNodeSize,
  isWidgetVisible,
  visibleWidgets,
  type WidgetValue,
} from "../src/nodeDefs";
import { catalog } from "./fixtures";

const def = catalog.get("test.enhance")!;
const names = (values: Record<string, WidgetValue>) =>
  visibleWidgets(def, values).map((w) => w.name);

/** One `test.enhance` node, with each `[widget, value]` pair applied in order. */
function enhanceGraph(...sets: ReadonlyArray<readonly [string, WidgetValue]>): WorkspaceGraph {
  let graph = emptyGraph("t", "t");
  graph = applyOps(graph, [
    { op: "add_node", node: makeNode(catalog, "test.enhance", { id: "n1", x: 0, y: 0 }) },
  ]).graph;
  for (const [name, value] of sets) {
    graph = applyOps(graph, [{ op: "set_widget", node_id: "n1", name, value }]).graph;
  }
  return graph;
}

describe("displayOptions", () => {
  it("a widget with no displayOptions always applies", () => {
    const plain = catalog.get("test.script")!;
    expect(visibleWidgets(plain, {})).toHaveLength(plain.widgets.length);
    expect(visibleWidgets(plain, { length: 120 })).toHaveLength(plain.widgets.length);
  });

  it("resolves an unset widget to its own default, so a fresh node reads like a clicked one", () => {
    // `enhancer` defaults to "off"; nothing has been set at all here.
    expect(names({})).toEqual(["enhancer", "legacy"]);
    expect(names({ enhancer: "off" })).toEqual(["enhancer", "legacy"]);
  });

  it("show: one listed value reveals the widget", () => {
    expect(names({ enhancer: "resemble" })).toEqual(["enhancer", "mode", "nfe", "profile"]);
  });

  it("show: any of several listed values counts", () => {
    // `nfe` lists both backends; `mode` lists only resemble.
    expect(names({ enhancer: "clearer" })).toEqual(["enhancer", "nfe", "legacy"]);
  });

  it("hide beats show, and hides on any match", () => {
    const legacy = def.widgets.find((w) => w.name === "legacy")!;
    expect(isWidgetVisible(legacy, def, { enhancer: "off" })).toBe(true);
    expect(isWidgetVisible(legacy, def, { enhancer: "resemble" })).toBe(false);
  });

  it("show with several keys is a conjunction: every one must hold", () => {
    const both = {
      ...def,
      widgets: [
        ...def.widgets,
        {
          name: "pair",
          kind: "text" as const,
          default: "",
          displayOptions: { show: { enhancer: ["resemble"], mode: ["denoise"] } },
        },
      ],
    };
    const pair = both.widgets.find((w) => w.name === "pair")!;
    expect(isWidgetVisible(pair, both, { enhancer: "resemble", mode: "denoise" })).toBe(true);
    expect(isWidgetVisible(pair, both, { enhancer: "resemble", mode: "enhance" })).toBe(false);
    expect(isWidgetVisible(pair, both, { enhancer: "off", mode: "denoise" })).toBe(false);
  });

  it("a hidden widget keeps its value rather than being cleared", () => {
    const graph = enhanceGraph(["enhancer", "resemble"], ["mode", "denoise"], ["enhancer", "off"]);
    const node = graph.nodes.find((n) => n.id === "n1")!;

    expect(names(node.values)).not.toContain("mode");
    // Turning the enhancer back on must bring back what the operator chose, not the default.
    expect(node.values.mode).toBe("denoise");
  });

  it("a hidden required widget does not block the graph, and a shown one does", () => {
    // `profile` is required but hidden while the enhancer is off.
    const off = validateGraph(enhanceGraph(), catalog).filter((p) => p.message.includes("profile"));
    expect(off).toHaveLength(0);

    const on = validateGraph(enhanceGraph(["enhancer", "resemble"]), catalog).filter((p) =>
      p.message.includes("profile"),
    );
    expect(on).toHaveLength(1);
    expect(on[0]?.severity).toBe("error");
    expect(on[0]?.message).toContain("name the restoration profile");
  });

  it("the size estimate measures only the rows it will draw", () => {
    const shut = estimateNodeSize({ width: null, collapsed: false, values: {} }, def);
    const open = estimateNodeSize(
      { width: null, collapsed: false, values: { enhancer: "resemble" } },
      def,
    );
    expect(open.height).toBeGreaterThan(shut.height);

    // No values at all is the old call shape; it must still answer "nothing set" rather than an
    // estimate that counts every hidden row.
    const legacyCall = estimateNodeSize({ width: null, collapsed: false }, def);
    expect(legacyCall.height).toBe(shut.height);
  });
});
