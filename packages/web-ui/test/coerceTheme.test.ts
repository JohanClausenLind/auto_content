import { describe, expect, it } from "vitest";
import { coerceThemeState, DEFAULT_THEME_STATE, THEME_PRESETS } from "../src/index";

describe("coerceThemeState", () => {
  it("repairs the partial row found in the dev database instead of dropping it", () => {
    // Verbatim from account_preferences: written by an earlier build, missing every field added
    // since. Feeding it straight into state left customThemes undefined and crashed the
    // customizer on `.length` as soon as Settings opened.
    const stored = { preset: "midnight", scale: 1.1 };
    const out = coerceThemeState(stored);

    expect(out).not.toBeNull();
    expect(out!.customThemes).toEqual([]);
    expect(out!.version).toBe(1);
    expect(out!.customId).toBeNull();
    expect(out!.reducedTransparency).toBe(false);
    // The operator's actual choices survive.
    expect(out!.preset).toBe("midnight");
    expect(out!.scale).toBe(1.1);
    // A row that pinned a preset but predates `mode` meant to pin it, not follow the system.
    expect(out!.mode).toBe("preset");
    expect(THEME_PRESETS.some((p) => p.id === "midnight")).toBe(true);
  });

  it("returns a valid state unchanged", () => {
    expect(coerceThemeState(DEFAULT_THEME_STATE)).toEqual(DEFAULT_THEME_STATE);
  });

  it("keeps the fields that validate and defaults only the ones that do not", () => {
    const out = coerceThemeState({ preset: "forest", scale: 99, reducedTransparency: "yes", customThemes: [] });
    expect(out!.preset).toBe("forest");
    expect(out!.scale).toBe(DEFAULT_THEME_STATE.scale);
    expect(out!.reducedTransparency).toBe(false);
  });

  it("keeps an explicit mode rather than inferring one", () => {
    expect(coerceThemeState({ mode: "system", preset: "midnight" })!.mode).toBe("system");
  });

  it("drops custom themes that are malformed, without losing the rest", () => {
    const out = coerceThemeState({ preset: "ocean", customThemes: [{ id: "x" }] });
    expect(out!.preset).toBe("ocean");
    expect(out!.customThemes).toEqual([]);
  });

  it("refuses a row stamped with a schema version it does not know", () => {
    // Repairing it to version 1 would let this build overwrite a newer client's settings.
    expect(coerceThemeState({ version: 99, preset: "midnight" })).toBeNull();
    expect(coerceThemeState({ version: 0 })).toBeNull();
  });

  it("repairs a versioned row that is merely missing newer fields", () => {
    const out = coerceThemeState({ version: 1, mode: "preset", preset: "ocean" });
    expect(out!.customThemes).toEqual([]);
    expect(out!.preset).toBe("ocean");
  });

  it("refuses values that are not objects", () => {
    for (const junk of [null, undefined, 42, "theme", [], true]) {
      expect(coerceThemeState(junk)).toBeNull();
    }
  });

  it("never returns a state missing the fields the UI reads", () => {
    for (const blob of [{}, { preset: "dark" }, { scale: 1 }, { customThemes: undefined }]) {
      const out = coerceThemeState(blob);
      expect(Array.isArray(out!.customThemes)).toBe(true);
      expect(typeof out!.mode).toBe("string");
      expect(typeof out!.preset).toBe("string");
    }
  });
});
