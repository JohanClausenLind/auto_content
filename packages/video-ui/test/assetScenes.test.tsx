import type { DatasetTable } from "@content-factory/content-schema-ts";
import { describe, expect, it } from "vitest";

import { isImplementedKind } from "../src/mapping";
import { fillWeight, regionValues } from "../src/scenes/MapScene";
import { containRect, highlightBox } from "../src/scenes/ScreenshotScene";

const REGIONS: DatasetTable = {
  dataset_id: "ds_regions00001",
  classification: "SOURCE_DATA",
  columns: ["region", "share_pct", "note"],
  rows: [
    { region: "SE1", share_pct: 12, note: "north" },
    { region: "SE3", share_pct: 48, note: "mid" },
    { region: "SE4", share_pct: null, note: "south" },
  ],
  unit: "%",
  source_ids: [],
  label: "Share by bidding zone",
};

describe("map + screenshot scenes are wired", () => {
  it("both kinds report as implemented", () => {
    expect(isImplementedKind("map")).toBe(true);
    expect(isImplementedKind("screenshot")).toBe(true);
  });
});

describe("choropleth values", () => {
  it("joins the first column to the named value column", () => {
    const values = regionValues(REGIONS, "share_pct");
    expect(values.get("SE1")).toBe(12);
    expect(values.get("SE3")).toBe(48);
  });

  it("skips rows without a finite value rather than filling them as zero", () => {
    const values = regionValues(REGIONS, "share_pct");
    expect(values.has("SE4")).toBe(false);
    expect(values.size).toBe(2);
  });

  it("defaults to the second column and copes with a missing dataset", () => {
    expect(regionValues(REGIONS, null).get("SE3")).toBe(48);
    expect(regionValues(undefined, null).size).toBe(0);
    expect(regionValues({ ...REGIONS, columns: ["region"], rows: [{ region: "SE1" }] }, null).size).toBe(0);
  });

  it("fillWeight floors visible regions and caps at the maximum", () => {
    expect(fillWeight(undefined, 50)).toBe(0);
    expect(fillWeight(10, 0)).toBe(0);
    expect(fillWeight(50, 50)).toBe(1);
    expect(fillWeight(0, 50)).toBeCloseTo(0.15, 10);
    expect(fillWeight(100, 50)).toBe(1);
  });
});

describe("screenshot geometry", () => {
  const box = { width: 1000, height: 500 };

  it("containRect letterboxes a tall image and pillarboxes a wide one", () => {
    const tall = containRect({ width: 500, height: 1000 }, box);
    expect(tall.height).toBe(500);
    expect(tall.width).toBe(250);
    expect(tall.left).toBe(375); // centred
    expect(tall.top).toBe(0);

    const wide = containRect({ width: 2000, height: 500 }, box);
    expect(wide.width).toBe(1000);
    expect(wide.height).toBe(250);
    expect(wide.top).toBe(125);
  });

  it("containRect falls back to the box for a degenerate natural size", () => {
    expect(containRect({ width: 0, height: 0 }, box)).toEqual({ left: 0, top: 0, ...box });
  });

  it("highlightBox places fractions on the rendered image, not the container", () => {
    const image = containRect({ width: 2000, height: 500 }, box); // top 125, height 250
    const hl = highlightBox([0.5, 0, 0.25, 1], image);
    expect(hl).not.toBeNull();
    expect(hl?.left).toBe(500);
    expect(hl?.top).toBe(125);
    expect(hl?.width).toBe(250);
    expect(hl?.height).toBe(250);
  });

  it("highlightBox clamps to the image and drops malformed regions", () => {
    const image = { left: 0, top: 0, width: 100, height: 100 };
    expect(highlightBox([0.9, 0, 0.5, 0.5], image)?.width).toBeCloseTo(10, 10); // clipped at the edge
    expect(highlightBox(null, image)).toBeNull();
    expect(highlightBox([0.1, 0.1], image)).toBeNull();
    expect(highlightBox(["a", 0, 1, 1], image)).toBeNull();
    expect(highlightBox([Number.NaN, 0, 1, 1], image)).toBeNull();
  });
});
