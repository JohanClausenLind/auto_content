import { describe, expect, it } from "vitest";
import { contrastRatio, deriveTokens, THEME_PRESETS } from "../src";

describe("contrastRatio", () => {
  it("matches WCAG reference values", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    expect(contrastRatio("#777777", "#ffffff")).toBeCloseTo(4.48, 1);
  });
});

describe("presets meet WCAG AA for body text", () => {
  for (const preset of THEME_PRESETS) {
    it(`${preset.id}: fg/bg, fg/panel and accent-fg/accent are >= 4.5`, () => {
      const t = deriveTokens(preset.base, preset.advanced);
      expect(contrastRatio(t.fg, t.bg)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(t.fg, t.panel)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(t["accent-fg"], t.accent)).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(t.muted, t.bg)).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("high-contrast preset reaches AAA (>= 7) for text", () => {
    const hc = THEME_PRESETS.find((p) => p.id === "high-contrast")!;
    const t = deriveTokens(hc.base, hc.advanced);
    expect(contrastRatio(t.fg, t.bg)).toBeGreaterThanOrEqual(7);
    expect(contrastRatio(t.fg, t.panel)).toBeGreaterThanOrEqual(7);
    expect(contrastRatio(t.muted, t.bg)).toBeGreaterThanOrEqual(7);
  });
});
