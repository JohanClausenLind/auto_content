import type { CameraKey, HighlightKey } from "@content-factory/content-schema-ts";
import { describe, expect, it } from "vitest";

import { cameraAt, highlightOpacity, isLocalPath, lineProgress, pageScale, screenRect } from "../src/source";

const KEYS: CameraKey[] = [
  { frame: 10, scroll_x: 0, scroll_y: 100, zoom: 1, easing: "hold" },
  { frame: 40, scroll_x: 0, scroll_y: 100, zoom: 1, easing: "hold" },
  { frame: 70, scroll_x: 120, scroll_y: 900, zoom: 2.5, easing: "move" },
];

describe("cameraAt", () => {
  it("holds the first key before it and the last key after it", () => {
    expect(cameraAt(KEYS, 0)).toEqual({ scrollX: 0, scrollY: 100, zoom: 1 });
    expect(cameraAt(KEYS, 40)).toEqual({ scrollX: 0, scrollY: 100, zoom: 1 });
    expect(cameraAt(KEYS, 500)).toEqual({ scrollX: 120, scrollY: 900, zoom: 2.5 });
  });

  it("moves monotonically between two keys with the in-out easing", () => {
    let previous = cameraAt(KEYS, 40).scrollY;
    for (let f = 41; f <= 70; f += 1) {
      const y = cameraAt(KEYS, f).scrollY;
      expect(y).toBeGreaterThanOrEqual(previous);
      previous = y;
    }
    const mid = cameraAt(KEYS, 55);
    expect(mid.scrollY).toBeGreaterThan(100);
    expect(mid.scrollY).toBeLessThan(900);
    const t = (mid.scrollY - 100) / 800;
    expect(mid.zoom).toBeCloseTo(1 + 1.5 * t, 6);
    expect(mid.scrollX).toBeCloseTo(120 * t, 6);
    const early = cameraAt(KEYS, 43).scrollY - 100;
    const late = cameraAt(KEYS, 46).scrollY - cameraAt(KEYS, 43).scrollY;
    expect(early).toBeLessThan(late);
  });
});

describe("page geometry", () => {
  it("maps a page rect through scroll and zoom about the region's top-left", () => {
    const region = { x: 96, y: 54, width: 1728, height: 924 };
    const scale = pageScale(region, 1280, 2);
    expect(scale).toBeCloseTo(2.7, 6);
    const rect = screenRect({ x: 100, y: 1000, width: 200, height: 20 }, { scrollX: 50, scrollY: 900, zoom: 2 }, region, scale);
    expect(rect.x0).toBeCloseTo(96 + 50 * 2.7, 6);
    expect(rect.y0).toBeCloseTo(54 + 100 * 2.7, 6);
    expect(rect.x1 - rect.x0).toBeCloseTo(200 * 2.7, 6);
  });
});

describe("highlight timing", () => {
  const key: HighlightKey = {
    quote_id: "qt_000000000001",
    start_frame: 100,
    end_frame: 112,
    rects: [
      { x: 0, y: 0, width: 10, height: 5 },
      { x: 0, y: 10, width: 10, height: 5 },
      { x: 0, y: 20, width: 10, height: 5 },
    ],
    rgba: "rgba(255, 189, 74, 0.36)",
    clear_start_frame: 200,
    clear_end_frame: 208,
  };

  it("sweeps line by line, left to right", () => {
    expect(lineProgress(key, 0, 99)).toBe(0);
    expect(lineProgress(key, 0, 102)).toBeCloseTo(0.5, 6);
    expect(lineProgress(key, 0, 104)).toBe(1);
    expect(lineProgress(key, 1, 104)).toBe(0);
    expect(lineProgress(key, 2, 112)).toBe(1);
  });

  it("stays until the clear, then fades to nothing", () => {
    expect(highlightOpacity(key, 150)).toBe(1);
    expect(highlightOpacity(key, 204)).toBeCloseTo(0.5, 6);
    expect(highlightOpacity(key, 300)).toBe(0);
    expect(highlightOpacity({ ...key, clear_start_frame: null, clear_end_frame: null }, 10_000)).toBe(1);
  });
});

describe("isLocalPath", () => {
  it("flags absolute paths and passes staged and remote ones", () => {
    expect(isLocalPath("/tmp/tiles/tile-0000.png")).toBe(true);
    expect(isLocalPath("C:\\tiles\\tile.png")).toBe(true);
    expect(isLocalPath("assets/abc.png")).toBe(false);
    expect(isLocalPath("https://example.org/tile.png")).toBe(false);
  });
});
