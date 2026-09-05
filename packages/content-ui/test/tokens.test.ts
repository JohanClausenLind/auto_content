import { describe, expect, it } from "vitest";

import { applyBrand, contrastMatrix, contrastRatio, cssVarsText, editorialTheme, resolveTheme, textColor, themeToCssVars } from "../src/index";

describe("editorial tokens", () => {
  it("every text role reaches >= 4.5 contrast on paper, ink and surface", () => {
    const matrix = contrastMatrix(editorialTheme);
    expect(matrix).toHaveLength(7 * 3);
    const failing = matrix.filter((c) => !c.pass).map((c) => `${c.role}@${c.background}=${c.ratio.toFixed(2)}`);
    expect(failing).toEqual([]);
    for (const c of matrix) expect(c.ratio).toBeGreaterThanOrEqual(4.5);
  });

  it("contrast math matches the WCAG reference (black on white = 21)", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#ffffff", "#000000")).toBeCloseTo(21, 5);
  });

  it("brand tokens override accent/paper/ink only and re-derive dependents", () => {
    const t = applyBrand(editorialTheme, { accent: "#8A1538", paper: "#FFFFFF", ink: "#000000", font_family: "Inter", logo_asset_id: null });
    expect(t.color.accent).toBe("#8A1538");
    expect(t.color.paper).toBe("#FFFFFF");
    expect(t.color.ink).toBe("#000000");
    expect(t.color.surface).not.toBe(editorialTheme.color.surface);
    expect(t.type).toBe(editorialTheme.type); // only colour changes
    expect(textColor(t, "label", "paper")).toBe("#8A1538");
    // Null overrides are a no-op (same object).
    expect(applyBrand(editorialTheme, { accent: null, paper: null, ink: null, font_family: "Inter", logo_asset_id: null })).toBe(editorialTheme);
  });

  it("unknown theme names fall back to editorial", () => {
    expect(resolveTheme("does-not-exist", null).name).toBe("editorial");
  });

  it("exposes CSS custom properties", () => {
    const vars = themeToCssVars(editorialTheme);
    expect(vars["--cf-color-paper"]).toBe(editorialTheme.color.paper);
    expect(vars["--cf-color-series-6"]).toBe(editorialTheme.color.series[5]);
    expect(cssVarsText(editorialTheme)).toMatch(/^:root\{--cf-font-family:"Inter";/);
  });

  it("series palette is Okabe–Ito, six distinct colours with dash redundancy", () => {
    expect(new Set(editorialTheme.color.series).size).toBe(6);
    expect(new Set(editorialTheme.series.map((s) => s.dash)).size).toBe(6);
  });
});


describe("dark-paper brands", () => {
  it("derive readable on-paper colours instead of vanishing text", () => {
    const dark = applyBrand(editorialTheme, { accent: "#4CC3FF", paper: "#0B0F14", ink: "#F2F5F9", font_family: "Inter", logo_asset_id: null });
    const body = textColor(dark, "body", "paper");
    const label = textColor(dark, "label", "paper");
    const muted = textColor(dark, "caption", "paper");
    expect(contrastRatio(body, dark.color.paper)).toBeGreaterThanOrEqual(7);
    expect(contrastRatio(label, dark.color.paper)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(muted, dark.color.paper)).toBeGreaterThanOrEqual(4.5);
    expect(body.toUpperCase()).toBe("#F2F5F9");
    // the surface used by callouts is a slightly lifted dark, and text on it stays light
    expect(contrastRatio(textColor(dark, "headline", "surface"), dark.color.surface)).toBeGreaterThanOrEqual(7);
  });
});


describe("display family", () => {
  it("a Sora brand sets the display roles in Sora 800/700 and leaves body text in Inter", () => {
    const t = applyBrand(editorialTheme, { accent: null, paper: null, ink: null, font_family: "Sora", logo_asset_id: null });
    expect(t.type.number.family).toBe("Sora");
    expect(t.type.number.weight).toBe(800);
    expect(t.type.headline.family).toBe("Sora");
    expect(t.type.body.family).toBe("Inter");
    expect(t.type.label.family).toBe("Inter");
    expect(editorialTheme.type.number.family).toBe("Inter"); // untouched
  });
});
