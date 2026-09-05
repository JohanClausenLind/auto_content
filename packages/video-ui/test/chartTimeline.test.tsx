import type { DatasetTable } from "@content-factory/content-schema-ts";
import { describe, expect, it } from "vitest";

import { chartPoints, niceMax } from "../src/scenes/ChartScene";
import { isImplementedKind } from "../src/mapping";

const DATASET: DatasetTable = {
  dataset_id: "ds_wind00000001",
  classification: "SOURCE_DATA",
  columns: ["year", "share_pct"],
  rows: [
    { year: "2018", share_pct: 11 },
    { year: "2021", share_pct: 17 },
    { year: "2025", share_pct: 21 },
  ],
  unit: "%",
  source_ids: ["src_energimynd01"],
  label: "Wind share",
};

describe("chart + timeline scenes", () => {
  it("chart and timeline kinds are implemented", () => {
    expect(isImplementedKind("chart")).toBe(true);
    expect(isImplementedKind("timeline")).toBe(true);
  });

  it("chartPoints maps rows totally (bad cells become 0, never a crash)", () => {
    const points = chartPoints(DATASET, "year", ["share_pct"]);
    expect(points.map((p) => p.label)).toEqual(["2018", "2021", "2025"]);
    expect(points.map((p) => p.values[0])).toEqual([11, 17, 21]);
    const dirty = chartPoints({ ...DATASET, rows: [{ year: "x", share_pct: null }] }, "year", ["share_pct"]);
    expect(dirty[0]?.values).toEqual([0]);
    expect(chartPoints(undefined, "year", ["share_pct"])).toEqual([]);
  });

  it("niceMax picks a stable rounded ceiling", () => {
    expect(niceMax([11, 17, 21])).toBe(50);
    expect(niceMax([0.4])).toBe(0.5);
    expect(niceMax([])).toBe(1);
    expect(niceMax([100])).toBe(100);
  });
});
