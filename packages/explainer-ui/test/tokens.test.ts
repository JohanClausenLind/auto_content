import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { apcaLc } from "../src/apca";
import { oklchToHex, type Oklch } from "../src/oklch";
import { TOKENS } from "../src/tokens.gen";

const parity = JSON.parse(readFileSync(new URL("../../../fixtures/explainer/tokens-parity.json", import.meta.url), "utf8")) as Record<string, string>;

function collect(node: unknown, prefix: string, out: Map<string, Oklch>): void {
  if (Array.isArray(node)) {
    node.forEach((v, i) => collect(v, `${prefix}[${i}]`, out));
    return;
  }
  if (node && typeof node === "object") {
    const rec = node as Record<string, unknown>;
    if (Array.isArray(rec.oklch)) out.set(String(rec.id ?? prefix), rec.oklch as unknown as Oklch);
    for (const [k, v] of Object.entries(rec)) collect(v, prefix ? `${prefix}.${k}` : k, out);
  }
}

describe("design tokens", () => {
  it("converts every OKLCH token to the same sRGB hex as the Python generator", () => {
    const colors = new Map<string, Oklch>();
    collect(TOKENS.color, "", colors);
    expect(colors.size).toBe(Object.keys(parity).length);
    for (const [id, oklch] of colors) expect(oklchToHex(oklch), id).toBe(parity[id]);
  });

  it("matches the APCA reference values for black on white and white on black", () => {
    expect(apcaLc([0, 0, 0], [255, 255, 255])).toBeCloseTo(106.0, 0);
    expect(apcaLc([255, 255, 255], [0, 0, 0])).toBeCloseTo(-107.9, 0);
  });

  it("has no pure black and reads primary ink on the canvas above Lc 75", () => {
    expect(parity["ui.surface.0"]).not.toBe("#000000");
    const hex = (id: string): [number, number, number] => {
      const h = parity[id] ?? "#000000";
      return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
    };
    expect(Math.abs(apcaLc(hex("ui.ink.primary"), hex("ui.surface.0")))).toBeGreaterThanOrEqual(75);
  });
});
