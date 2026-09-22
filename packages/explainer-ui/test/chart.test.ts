import type { ChartTemplate, EvidenceDataset } from "@content-factory/content-schema-ts";
import { describe, expect, it } from "vitest";

import { categorySlot, cellMatches, chartCategories, filterMask, formatValue, lineSegments, seriesPolyline, sortOrder, stackCategory, valueDomain, valueLabelSide } from "../src/templates/chart";

const WIND = "ent_wind0001";
const SOLAR = "ent_solar001";

const DATASET: EvidenceDataset = {
  dataset_id: "ds_energy001",
  title: "Installed capacity",
  columns: [
    { name: "year", kind: "temporal", unit: "" },
    { name: "wind", kind: "quantitative", unit: "GW" },
    { name: "solar", kind: "quantitative", unit: "GW" },
  ],
  rows: [
    { key: "2021", values: ["2021", 60, 70], claim_ids: [] },
    { key: "2022", values: ["2022", null, 88], claim_ids: [] },
    { key: "2023", values: ["2023", 75, 104], claim_ids: [] },
    { key: "2024", values: ["2024", 84, 124], claim_ids: [] },
  ],
};

const WIDE: ChartTemplate = {
  template: "chart",
  chart_kind: "stacked_bar",
  title: "",
  dataset_asset_id: "ast_dsenergy01",
  x: { field: "year", kind: "temporal", unit: "", title: "Year" },
  y: { field: "wind", kind: "quantitative", unit: "GW", title: "Capacity" },
  series_field: null,
  series: [
    { value: "wind", entity_id: WIND },
    { value: "solar", entity_id: SOLAR },
  ],
  baseline_zero: true,
};

describe("chart helpers", () => {
  it("keeps a zero baseline for bars and discloses a truncated axis otherwise", () => {
    expect(valueDomain([60, 124], true)).toMatchObject({ min: 0, broken: false });
    expect(valueDomain([60, 124], true).max).toBeGreaterThanOrEqual(124);
    const truncated = valueDomain([100, 124], false);
    expect(truncated.min).toBeGreaterThan(0);
    expect(truncated.broken).toBe(true);
    expect(valueDomain([-3, 5], true)).toMatchObject({ broken: false });
    expect(valueDomain([-3, 5], true).min).toBeLessThanOrEqual(-3);
  });

  it("reads a missing cell as a gap, never as zero", () => {
    const categories = chartCategories(DATASET, WIDE);
    expect(categories.map((c) => c.values[0])).toEqual([60, null, 75, 84]);
    expect(stackCategory([null, 88])).toEqual([{ series: 1, y0: 0, y1: 88, value: 88 }]);
    expect(lineSegments([1, 2, null, 4])).toEqual([[1, 2], [4]]);
  });

  it("stacks positives up and negatives down", () => {
    expect(stackCategory([3, -2, 4])).toEqual([
      { series: 0, y0: 0, y1: 3, value: 3 },
      { series: 1, y0: -2, y1: 0, value: -2 },
      { series: 2, y0: 3, y1: 7, value: 4 },
    ]);
  });

  it("a lone wide series with an empty value draws y.field", () => {
    const single: ChartTemplate = { ...WIDE, y: { ...WIDE.y, field: "solar" }, series: [{ value: "", entity_id: SOLAR }] };
    expect(chartCategories(DATASET, single).map((c) => c.values)).toEqual([[70], [88], [104], [124]]);
  });

  it("groups long data by the series field", () => {
    const long: EvidenceDataset = {
      dataset_id: "ds_long00001",
      title: "",
      columns: [
        { name: "year", kind: "temporal", unit: "" },
        { name: "tech", kind: "nominal", unit: "" },
        { name: "gw", kind: "quantitative", unit: "GW" },
      ],
      rows: [
        { key: "a", values: ["2021", "wind", 60], claim_ids: [] },
        { key: "b", values: ["2021", "solar", 70], claim_ids: [] },
        { key: "c", values: ["2022", "solar", 88], claim_ids: [] },
      ],
    };
    const categories = chartCategories(long, { ...WIDE, series_field: "tech", y: { ...WIDE.y, field: "gw" } });
    expect(categories.map((c) => c.label)).toEqual(["2021", "2022"]);
    expect(categories.map((c) => c.values)).toEqual([
      [60, 70],
      [null, 88],
    ]);
  });

  it("sorts by total with empty categories last, and by label alphabetically", () => {
    // Totals are 130, 88 (the null counts for nothing), 179, 208.
    const categories = chartCategories(DATASET, WIDE);
    expect(sortOrder(categories, "value_desc")).toEqual([2, 3, 1, 0]);
    expect(sortOrder(categories, "value_asc")).toEqual([1, 0, 2, 3]);
    expect(sortOrder(categories, "label")).toEqual([0, 1, 2, 3]);
    const withEmpty = [...categories, { key: "none", label: "1999", x: "1999", values: [null, null], cells: ["1999", null, null] }];
    expect(sortOrder(withEmpty, "value_desc")[4]).toBe(4);
  });

  it("filters on any column and compacts the kept categories as the filter completes", () => {
    const categories = chartCategories(DATASET, WIDE);
    expect(filterMask(categories, DATASET, { field: "year", op: "eq", value: "2022" })).toEqual([false, true, false, false]);
    const mask = filterMask(categories, DATASET, { field: "solar", op: "gt", value: 100 });
    expect(mask).toEqual([false, false, true, true]);
    expect(categorySlot(2, null, mask, 0, 0)).toEqual({ position: 2, alpha: 1 });
    expect(categorySlot(2, null, mask, 0, 1)).toEqual({ position: 0, alpha: 1 });
    expect(categorySlot(3, null, mask, 0, 1)).toEqual({ position: 1, alpha: 1 });
    expect(categorySlot(0, null, mask, 0, 1)).toEqual({ position: 0, alpha: 0 });
    expect(categorySlot(0, null, mask, 0, 0.5).alpha).toBeCloseTo(0.5, 10);
    expect(cellMatches("x", "gt", 1)).toBe(false);
    expect(cellMatches(5, "lte", "5")).toBe(true);
  });

  it("interpolates a sort between the original and the sorted position", () => {
    const order = sortOrder(chartCategories(DATASET, WIDE), "value_desc");
    expect(categorySlot(0, order, null, 0, 0).position).toBe(0);
    expect(categorySlot(0, order, null, 0.5, 0).position).toBe(1);
    expect(categorySlot(0, order, null, 1, 0).position).toBe(2);
  });

  it("formats values deterministically with their unit", () => {
    expect(formatValue(124, "GW")).toBe("124 GW");
    expect(formatValue(24, "%")).toBe("24%");
    expect(formatValue(3.14159)).toBe("3.14");
    expect(formatValue(12.5, "GW")).toBe("12.5 GW");
  });
});

const PLOT = { x: 216, y: 174, width: 1608, height: 796 };
const SOLAR_BINDING = { value: "solar", entity_id: SOLAR };
const LINE: ChartTemplate = { ...WIDE, chart_kind: "line", y: { ...WIDE.y, field: "solar" }, series: [SOLAR_BINDING] };

describe("line geometry over the compiled plot region", () => {
  it("puts a categorical series at the scalePoint slot centres, plot.x + (i + 0.5) * width / n", () => {
    const xs = seriesPolyline(LINE, DATASET, SOLAR_BINDING, PLOT).map(([x]) => x);
    expect(xs).toEqual([0, 1, 2, 3].map((i) => PLOT.x + ((i + 0.5) * PLOT.width) / 4));
  });

  it("lands the max on plot.y when nice() ends on it and below plot.y otherwise", () => {
    // 70..124 with a zero baseline nices to [0, 130]: d3 and qc.nice_domain both use count 10, not 5.
    expect(valueDomain([70, 88, 104, 124], true)).toMatchObject({ min: 0, max: 130 });
    const top = seriesPolyline(LINE, DATASET, SOLAR_BINDING, PLOT)[3]?.[1];
    expect(top).toBeCloseTo(PLOT.y + PLOT.height * (1 - 124 / 130), 6);
    expect(top).toBeGreaterThan(PLOT.y);
    const ratios: EvidenceDataset = {
      dataset_id: "ds_ratio00001",
      title: "",
      columns: [
        { name: "gear", kind: "ordinal", unit: "" },
        { name: "ratio", kind: "quantitative", unit: "ratio" },
      ],
      rows: [
        { key: "low", values: ["low", 1], claim_ids: [] },
        { key: "mid", values: ["mid", 2], claim_ids: [] },
        { key: "high", values: ["high", 3], claim_ids: [] },
        { key: "top", values: ["top", 4], claim_ids: [] },
      ],
    };
    const binding = { value: "ratio", entity_id: "ent_ratio_ser" };
    const onTick = seriesPolyline({ ...LINE, x: { ...LINE.x, field: "gear" }, y: { ...LINE.y, field: "ratio" }, series: [binding] }, ratios, binding, PLOT);
    expect(onTick[3]?.[1]).toBe(PLOT.y);
    expect(onTick[0]?.[1]).toBeCloseTo(PLOT.y + PLOT.height * 0.75, 6);
  });

  it("skips a gap, so the polyline has one point per present value", () => {
    const wind = { value: "wind", entity_id: WIND };
    expect(seriesPolyline({ ...LINE, series: [wind] }, DATASET, wind, PLOT)).toHaveLength(3);
  });

  it("flips a value label below a mark within one label height of the plot top, inside-left for the last", () => {
    expect(valueLabelSide(PLOT.y + 35, PLOT, 35, false)).toBe("above");
    expect(valueLabelSide(PLOT.y + 34, PLOT, 35, false)).toBe("below");
    expect(valueLabelSide(PLOT.y + 34, PLOT, 35, true)).toBe("left");
    expect(valueLabelSide(PLOT.y, PLOT, 35, true)).toBe("left");
  });
});
