import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { RenderBundle } from "@content-factory/content-schema-ts";
import { FIT_SAFETY, fitNumber, fitText, formatNumber, measureText, resolveDataRef, wrapText } from "../src/index";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..", "..", "..");
const artboardBundle = JSON.parse(readFileSync(path.join(root, "fixtures/demo/artboard-bundle.json"), "utf8")) as RenderBundle;
const timelineBundle = JSON.parse(readFileSync(path.join(root, "fixtures/demo/timeline-bundle.json"), "utf8")) as RenderBundle;

function fixtureStrings(): string[] {
  const out: string[] = [];
  for (const layer of artboardBundle.artboard?.layers ?? []) if (layer.kind === "text") out.push(layer.text.text);
  for (const scene of timelineBundle.plan?.scenes ?? []) {
    for (const v of Object.values(scene)) {
      if (v && typeof v === "object" && "text" in v && typeof (v as { text: unknown }).text === "string") out.push((v as { text: string }).text);
      if (Array.isArray(v)) for (const b of v) if (b && typeof b === "object" && "text" in b) out.push((b as { text: string }).text);
    }
  }
  for (const b of timelineBundle.plan?.beats ?? []) out.push(b.display_text);
  return out;
}

describe("deterministic text fitting", () => {
  it("measures with the Inter advance table (wider text is wider; bolder is wider)", () => {
    const a = measureText("iii", { weight: 400, fontSize: 100 });
    const b = measureText("WWW", { weight: 400, fontSize: 100 });
    expect(a).toBeLessThan(b);
    expect(measureText("Sweden", { weight: 700, fontSize: 40 })).toBeGreaterThan(measureText("Sweden", { weight: 400, fontSize: 40 }));
    expect(measureText("", { weight: 400, fontSize: 40 })).toBe(0);
  });

  it("wraps greedily and hard-breaks over-long words without exceeding the width", () => {
    const opts = { weight: 400 as const, fontSize: 30, maxWidth: 200 };
    const lines = wrapText("Supercalifragilisticexpialidocious is long", opts);
    for (const l of lines) expect(measureText(l, opts) * FIT_SAFETY).toBeLessThanOrEqual(200);
    expect(lines.length).toBeGreaterThan(2);
    expect(lines.join("").replace(/ /g, "")).toBe("Supercalifragilisticexpialidociousislong");
  });

  it("never exceeds max_lines, width or height for fixture strings across frames", () => {
    const strings = fixtureStrings();
    expect(strings.length).toBeGreaterThan(8);
    const frames = [
      { maxWidth: 907, maxHeight: 216, maxLines: 2 },
      { maxWidth: 400, maxHeight: 120, maxLines: 1 },
      { maxWidth: 929, maxHeight: 86, maxLines: 4 },
      { maxWidth: 300, maxHeight: 600, maxLines: 6 },
    ];
    for (const text of strings) {
      for (const f of frames) {
        for (const weight of [400, 700] as const) {
          const r = fitText({ text, weight, ...f, preferredSize: 60, minSize: 8, lineHeight: 1.1 });
          expect(r.lines.length, `${text} in ${JSON.stringify(f)}`).toBeLessThanOrEqual(f.maxLines);
          expect(r.widthPx).toBeLessThanOrEqual(f.maxWidth + 1e-6);
          expect(r.heightPx).toBeLessThanOrEqual(f.maxHeight + 1e-6);
          expect(Number.isInteger(r.fontSize)).toBe(true);
          expect(r.truncated).toBe(false);
        }
      }
    }
  });

  it("is deterministic and prefers the largest size that fits", () => {
    const opts = { text: "of electricity came from wind", weight: 700 as const, maxWidth: 907, maxHeight: 216, maxLines: 2, preferredSize: 60, minSize: 32, lineHeight: 1.1, letterSpacing: -0.015 };
    const a = fitText(opts);
    const b = fitText(opts);
    expect(a).toEqual(b);
    expect(a.fontSize).toBe(60);
    expect(a.lines).toEqual(["of electricity came from wind"]);
  });

  it("truncates with an ellipsis only when even the minimum size cannot hold the text", () => {
    const r = fitText({ text: "A very long headline that cannot possibly fit in one narrow line", weight: 700, maxWidth: 120, maxHeight: 40, maxLines: 1, preferredSize: 60, minSize: 30, lineHeight: 1.1 });
    expect(r.truncated).toBe(true);
    expect(r.lines).toHaveLength(1);
    expect(r.lines[0]?.endsWith("…")).toBe(true);
    expect(r.widthPx).toBeLessThanOrEqual(120);
  });

  it("fits a numeral plus unit on one line", () => {
    const r = fitNumber({ numeral: "21", unit: "%", weight: 700, maxWidth: 907, maxHeight: 324, preferredSize: 220, minSize: 8, lineHeight: 1 });
    expect(r.fontSize).toBe(220);
    expect(r.overflow).toBe(false);
    const tight = fitNumber({ numeral: "1,234,567", unit: "GWh", weight: 700, maxWidth: 400, maxHeight: 324, preferredSize: 220, minSize: 8, lineHeight: 1 });
    expect(tight.fontSize).toBeLessThan(220);
    expect(tight.widthPx).toBeLessThanOrEqual(400);
  });
});

describe("number formatting and data refs", () => {
  it("formats deterministically without Intl", () => {
    expect(formatNumber(21, "integer", "%").text).toBe("21%");
    expect(formatNumber(1234567.8, "integer").text).toBe("1,234,568");
    expect(formatNumber(21.456, "percent").text).toBe("21.5%");
    expect(formatNumber(21, "percent").text).toBe("21%");
    expect(formatNumber(3_400_000, "compact").text).toBe("3.4M");
    expect(formatNumber(21_000, "compact", "t").text).toBe("21K t");
    expect(formatNumber(950, "compact").text).toBe("950");
    expect(formatNumber(1234, "currency", "$").text).toBe("$1,234");
    expect(formatNumber(1234.5, "currency", "SEK").text).toBe("1,234.50 SEK");
    expect(formatNumber(0.125, "auto").text).toBe("0.13");
    expect(formatNumber(-42, "auto", "GW").text).toBe("−42 GW");
    expect(formatNumber(null, "auto").text).toBe("—");
    expect(formatNumber("1,200", "integer").text).toBe("1,200");
  });

  it("resolves a DataRef through row_key and column", () => {
    const layer = artboardBundle.artboard?.layers.find((l) => l.kind === "number");
    expect(layer?.kind).toBe("number");
    if (layer?.kind !== "number") return;
    expect(resolveDataRef(layer.value, artboardBundle.datasets)).toBe(21);
    expect(resolveDataRef({ ...layer.value, row_key: "2018" }, artboardBundle.datasets)).toBe(11);
    expect(resolveDataRef({ ...layer.value, row_key: "1999" }, artboardBundle.datasets)).toBeUndefined();
    expect(resolveDataRef({ ...layer.value, dataset_id: "ds_missing000001" }, artboardBundle.datasets)).toBeUndefined();
  });
});
