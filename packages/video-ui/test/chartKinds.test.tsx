import type { DatasetTable } from "@content-factory/content-schema-ts";
import { describe, expect, it } from "vitest";

import { donutAngles, numericColumn, stackTotals, waterfallSteps, type SeriesPoint } from "../src/scenes/ChartScene";

const MIX: DatasetTable = {
  dataset_id: "ds_mix000000001",
  classification: "SOURCE_DATA",
  columns: ["load_mw", "price"],
  rows: [
    { load_mw: 120, price: 41.5 },
    { load_mw: 260, price: 38 },
    { load_mw: "n/a", price: null },
  ],
  unit: "MW",
  source_ids: [],
  label: "Load vs price",
};

const points = (...rows: number[][]): SeriesPoint[] => rows.map((values, i) => ({ label: `p${i}`, values }));

describe("chart kinds beyond the bar family", () => {
  it("numericColumn keeps row order and neutralises non-numeric cells", () => {
    expect(numericColumn(MIX, "load_mw")).toEqual([120, 260, 0]);
    expect(numericColumn(MIX, "price")).toEqual([41.5, 38, 0]);
    expect(numericColumn(MIX, "missing")).toEqual([0, 0, 0]);
    expect(numericColumn(undefined, "load_mw")).toEqual([]);
  });

  it("stackTotals sums the series a stacked bar actually reaches", () => {
    expect(stackTotals(points([2, 3], [5, 5], [0, 0]))).toEqual([5, 10, 0]);
    // negatives cannot pull a stack below its baseline
    expect(stackTotals(points([4, -3]))).toEqual([4]);
    expect(stackTotals([])).toEqual([]);
  });

  it("waterfallSteps chains each bar onto the previous close", () => {
    expect(waterfallSteps([10, -4, 2])).toEqual([
      { start: 0, end: 10 },
      { start: 10, end: 6 },
      { start: 6, end: 8 },
    ]);
    expect(waterfallSteps([])).toEqual([]);
  });

  it("donutAngles covers exactly one turn, in row order", () => {
    const angles = donutAngles([1, 1, 2]);
    expect(angles).toHaveLength(3);
    expect(angles[0]?.start).toBe(0);
    expect(angles.at(-1)?.end).toBeCloseTo(Math.PI * 2, 10);
    // a quarter of the total is a quarter turn
    expect(angles[0]?.end).toBeCloseTo(Math.PI / 2, 10);
    // adjacent slices meet with no gap
    expect(angles[1]?.start).toBeCloseTo(angles[0]!.end, 10);
  });

  it("donutAngles draws nothing rather than NaN paths when there is no positive total", () => {
    expect(donutAngles([])).toEqual([]);
    expect(donutAngles([0, 0])).toEqual([]);
    expect(donutAngles([-5])).toEqual([]);
  });

  it("the helpers are pure: the same input gives the same output", () => {
    const values = [3, 1, 4, 1, 5];
    expect(donutAngles(values)).toEqual(donutAngles(values));
    expect(waterfallSteps(values)).toEqual(waterfallSteps(values));
  });
});
