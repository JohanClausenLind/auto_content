import type { DatasetTable, DiagramEdge, DiagramNode } from "@content-factory/content-schema-ts";
import { classificationNotice, refClassification } from "@content-factory/content-ui";
import { describe, expect, it } from "vitest";

import { isImplementedKind } from "../src/mapping";
import { stepPath } from "../src/scenes/ChartScene";
import { comparisonValues } from "../src/scenes/ComparisonScene";
import { FLOW_GAP_RATIO, edgePath, flowBoxes, flowLayers, flowRevealStep } from "../src/scenes/FlowDiagramScene";
import { IMAGE_MOTION_TRAVEL, imageScale } from "../src/scenes/ImageScene";

const TABLE: DatasetTable = {
  dataset_id: "ds_sides000001",
  classification: "SOURCE_DATA",
  columns: ["year", "share_pct"],
  rows: [
    { year: "2019", share_pct: 12 },
    { year: "2024", share_pct: 34 },
  ],
  unit: "%",
  source_ids: [],
  label: "Share by year",
};
const SKETCH: DatasetTable = { ...TABLE, dataset_id: "ds_sketch00001", classification: "ILLUSTRATIVE" };
const DATASETS = { [TABLE.dataset_id]: TABLE, [SKETCH.dataset_id]: SKETCH };

function ref(dataset_id: string, row_key: string | null) {
  return { dataset_id, claim_id: null, column: "share_pct", row_key };
}

function node(node_id: string): DiagramNode {
  return { node_id, label: { text: node_id, claim_ids: [] } };
}
function edge(from_id: string, to_id: string): DiagramEdge {
  return { from_id, to_id, label: null };
}

describe("the three kinds this pass implemented", () => {
  it("all report as implemented", () => {
    expect(isImplementedKind("image")).toBe(true);
    expect(isImplementedKind("comparison")).toBe(true);
    expect(isImplementedKind("flow_diagram")).toBe(true);
  });
});

describe("image motion", () => {
  it("a push grows from the frame and a pull settles back into it", () => {
    expect(imageScale("slow_push", 0)).toBe(1);
    expect(imageScale("slow_push", 1)).toBeCloseTo(1 + IMAGE_MOTION_TRAVEL, 10);
    // The last frame of a pull is the clean one: that is the frame a viewer reads.
    expect(imageScale("slow_pull", 0)).toBeCloseTo(1 + IMAGE_MOTION_TRAVEL, 10);
    expect(imageScale("slow_pull", 1)).toBe(1);
  });

  it("none is static and out-of-range t is clamped, never extrapolated", () => {
    expect(imageScale("none", 0.5)).toBe(1);
    expect(imageScale("slow_push", 2)).toBeCloseTo(1 + IMAGE_MOTION_TRAVEL, 10);
    expect(imageScale("slow_push", -1)).toBe(1);
  });

  it("never crops more than the declared travel, at any t", () => {
    for (let i = 0; i <= 20; i += 1) {
      const t = i / 20;
      for (const motion of ["none", "slow_push", "slow_pull"] as const) {
        const s = imageScale(motion, t);
        expect(s).toBeGreaterThanOrEqual(1);
        expect(s).toBeLessThanOrEqual(1 + IMAGE_MOTION_TRAVEL);
      }
    }
  });
});

describe("comparison figures", () => {
  it("resolves each side to its own row of the shared column", () => {
    const values = comparisonValues({ left_value: ref(TABLE.dataset_id, "2019"), right_value: ref(TABLE.dataset_id, "2024") }, DATASETS);
    expect(values).toEqual({ left: 12, right: 34 });
  });

  it("a labels-only comparison resolves to no figures rather than to zeros", () => {
    expect(comparisonValues({ left_value: null, right_value: null }, DATASETS)).toEqual({ left: null, right: null });
  });

  it("a row that is not in the table is null, not the first row", () => {
    const values = comparisonValues({ left_value: ref(TABLE.dataset_id, "1999"), right_value: ref(TABLE.dataset_id, "2024") }, DATASETS);
    expect(values.left).toBeNull();
    expect(values.right).toBe(34);
  });
});

describe("data classification caveats", () => {
  it("only non-measured tables get a notice", () => {
    expect(classificationNotice(refClassification(ref(TABLE.dataset_id, "2019"), DATASETS))).toBeNull();
    expect(classificationNotice(refClassification(ref(SKETCH.dataset_id, "2019"), DATASETS))).toBe("illustrative — not measured");
    expect(classificationNotice("ESTIMATE")).toBe("estimate");
    expect(classificationNotice("DERIVED_DATA")).toBeNull();
  });

  it("a missing table and a missing ref both classify as unknown, and get no notice", () => {
    expect(refClassification(ref("ds_absent00001", null), DATASETS)).toBeUndefined();
    expect(refClassification(null, DATASETS)).toBeUndefined();
    expect(classificationNotice(undefined)).toBeNull();
  });
});

describe("flow diagram layout", () => {
  it("layers by longest path, not by first mention", () => {
    // a→b→c and a→c: c is two steps deep, because the longest path is what sets the layer.
    const layers = flowLayers([node("a"), node("b"), node("c")], [edge("a", "b"), edge("b", "c"), edge("a", "c")]);
    expect(layers).toEqual([["a"], ["b"], ["c"]]);
  });

  it("puts every root in the first layer and keeps declaration order inside a layer", () => {
    const layers = flowLayers([node("x"), node("y"), node("sink")], [edge("x", "sink"), edge("y", "sink")]);
    expect(layers).toEqual([["x", "y"], ["sink"]]);
  });

  it("nodes with no edges at all are one layer, so an unconnected set still draws", () => {
    expect(flowLayers([node("a"), node("b")], [])).toEqual([["a", "b"]]);
  });

  it("terminates on a cycle and still places every node exactly once", () => {
    const nodes = [node("a"), node("b"), node("c")];
    const layers = flowLayers(nodes, [edge("a", "b"), edge("b", "c"), edge("c", "a")]);
    expect(layers.flat().sort()).toEqual(["a", "b", "c"]);
  });

  it("ignores an edge naming a node that is not in the scene", () => {
    expect(flowLayers([node("a"), node("b")], [edge("a", "ghost"), edge("ghost", "b")])).toEqual([["a", "b"]]);
    // A self-edge is not a step either; it would otherwise push a node past its own layer.
    expect(flowLayers([node("a"), node("b")], [edge("a", "a"), edge("a", "b")])).toEqual([["a"], ["b"]]);
  });

  it("boxes stay inside the plot and advance along the flow axis", () => {
    const plot = { width: 1000, height: 400 };
    const boxes = flowBoxes([["a"], ["b", "c"]], plot, "horizontal", 20);
    for (const b of boxes) {
      expect(b.left).toBeGreaterThanOrEqual(0);
      expect(b.top).toBeGreaterThanOrEqual(0);
      expect(b.left + b.width).toBeLessThanOrEqual(plot.width);
      expect(b.top + b.height).toBeLessThanOrEqual(plot.height);
    }
    const [a, b, c] = boxes;
    expect(a!.left).toBeLessThan(b!.left); // later layer, further along
    expect(b!.top).toBeLessThan(c!.top); // same layer, stacked across
  });

  it("vertical layout swaps the axes rather than reusing the landscape one", () => {
    const boxes = flowBoxes([["a"], ["b"]], { width: 400, height: 1000 }, "vertical", 20);
    expect(boxes[0]!.top).toBeLessThan(boxes[1]!.top);
    expect(boxes[0]!.left).toBe(boxes[1]!.left);
  });

  it("gives the flow axis more room than the seam between siblings, so arrows have length", () => {
    const boxes = flowBoxes([["a"], ["b"]], { width: 1000, height: 400 }, "horizontal", 20);
    const [a, b] = boxes;
    const arrowRoom = b!.left - (a!.left + a!.width);
    expect(arrowRoom).toBeCloseTo(20 * FLOW_GAP_RATIO, 6);
  });

  it("never spends more than half a lane on the gap, however many layers there are", () => {
    // Six layers in a 600px plot: a 3x gap would leave nothing to put a label in.
    const layers = [["a"], ["b"], ["c"], ["d"], ["e"], ["f"]];
    for (const b of flowBoxes(layers, { width: 600, height: 400 }, "horizontal", 60)) {
      expect(b.width).toBeGreaterThanOrEqual(600 / layers.length / 2);
    }
  });

  it("an edge leaves one box and arrives at the next along the flow axis", () => {
    const [from, to] = flowBoxes([["a"], ["b"]], { width: 1000, height: 400 }, "horizontal", 20);
    const d = edgePath(from!, to!, "horizontal");
    expect(d.startsWith(`M ${from!.left + from!.width} `)).toBe(true);
    expect(d).toContain(`${to!.left} ${to!.top + to!.height / 2}`);
    const v = edgePath(from!, to!, "vertical");
    expect(v.startsWith(`M ${from!.left + from!.width / 2} ${from!.top + from!.height}`)).toBe(true);
  });
});

describe("flow reveal cadence", () => {
  it("finishes inside the beat: the last arrow's reveal ends before the scene does", () => {
    // The regression this pins: paced off a motion token, a 48-frame beat left the final arrow
    // drawn at 55 % with no arrowhead — a chain that stopped in mid-air.
    for (const duration of [30, 48, 90, 300]) {
      for (const layers of [1, 2, 3, 6, 12]) {
        const step = flowRevealStep(layers, duration, 8, 8);
        const lastArrowEnds = step * layers + step;
        expect(lastArrowEnds).toBeLessThanOrEqual(duration);
      }
    }
  });

  it("never returns zero, even for a beat shorter than its own intro", () => {
    expect(flowRevealStep(3, 10, 8, 8)).toBe(1);
    expect(flowRevealStep(0, 48, 8, 8)).toBeGreaterThan(0);
  });
});

describe("step chart path", () => {
  it("holds the value then jumps, so no slope claims a value between readings", () => {
    const path = stepPath([
      { x: 0, y: 100 },
      { x: 50, y: 40 },
      { x: 100, y: 60 },
    ]);
    expect(path).toBe("M 0 100 H 50 V 40 H 100 V 60");
    // Every segment is axis-aligned: no command in the path moves in both x and y.
    expect(path).not.toMatch(/[LC]/);
  });

  it("holds the last reading to the plot edge, so the newest value is not a zero-length line", () => {
    expect(stepPath([{ x: 0, y: 100 }, { x: 50, y: 40 }], 150)).toBe("M 0 100 H 50 V 40 H 150");
    // An endX at or behind the last point adds nothing rather than drawing backwards.
    expect(stepPath([{ x: 0, y: 100 }, { x: 50, y: 40 }], 50)).toBe("M 0 100 H 50 V 40");
    expect(stepPath([{ x: 0, y: 100 }, { x: 50, y: 40 }], 10)).toBe("M 0 100 H 50 V 40");
  });

  it("is empty for no points and a bare move for one, never a malformed path", () => {
    expect(stepPath([])).toBe("");
    expect(stepPath([{ x: 7, y: 9 }])).toBe("M 7 9");
  });
});
