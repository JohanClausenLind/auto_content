import { describe, expect, it } from "vitest";
import { DEFAULT_THEME_STATE, parseThemeImport, themeStateSchema } from "../src";

describe("theme import validation", () => {
  it("rejects malformed JSON", () => {
    const r = parseThemeImport("{ nope");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/not valid JSON/);
  });

  it("rejects well-formed JSON with the wrong shape", () => {
    const r = parseThemeImport(JSON.stringify({ version: 1, mode: "party" }));
    expect(r.ok).toBe(false);
  });

  it("rejects non-hex colours and too many custom themes", () => {
    const bad = parseThemeImport(JSON.stringify({ id: "x", name: "X", base: { bg: "red", fg: "#fff", panel: "#000", border: "#000", accent: "#000" } }));
    expect(bad.ok).toBe(false);
    const themes = Array.from({ length: 9 }, (_, i) => ({ id: `t${i}`, name: `T${i}`, base: { bg: "#000000", fg: "#ffffff", panel: "#111111", border: "#222222", accent: "#3399ff" } }));
    expect(themeStateSchema.safeParse({ ...DEFAULT_THEME_STATE, customThemes: themes }).success).toBe(false);
  });

  it("rejects out-of-range scale", () => {
    expect(themeStateSchema.safeParse({ ...DEFAULT_THEME_STATE, scale: 2 }).success).toBe(false);
    expect(themeStateSchema.safeParse({ ...DEFAULT_THEME_STATE, scale: 1.3 }).success).toBe(true);
  });

  it("accepts a full state and a single custom theme", () => {
    const state = parseThemeImport(JSON.stringify(DEFAULT_THEME_STATE));
    expect(state.ok && state.kind).toBe("state");
    const custom = parseThemeImport(JSON.stringify({ id: "c", name: "Custom", base: { bg: "#000", fg: "#fff", panel: "#111", border: "#222", accent: "#39f" } }));
    expect(custom.ok && custom.kind).toBe("custom");
  });
});
