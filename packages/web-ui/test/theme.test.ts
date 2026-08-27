import { describe, expect, it } from "vitest";
import { applyTheme, DEFAULT_THEME_STATE, loadThemeState, resolveTheme, saveThemeState, THEME_PAINT_KEY, themeBootScript, type ThemeState } from "../src";

describe("theme apply", () => {
  it("sets CSS custom properties and root attributes for a preset", () => {
    const state: ThemeState = { ...DEFAULT_THEME_STATE, mode: "preset", preset: "midnight" };
    applyTheme(state);
    const root = document.documentElement;
    expect(root.style.getPropertyValue("--cf-bg")).toBe("#060a18");
    expect(root.style.getPropertyValue("--cf-accent")).toBe("#8b9cff");
    expect(root.style.getPropertyValue("--cf-muted")).not.toBe("");
    expect(root.style.getPropertyValue("--cf-scale")).toBe("1");
    expect(root.getAttribute("data-cf-theme")).toBe("midnight");
    expect(root.getAttribute("data-cf-scheme")).toBe("dark");
    expect(root.style.colorScheme).toBe("dark");
  });

  it("follows the system scheme in system mode", () => {
    expect(resolveTheme(DEFAULT_THEME_STATE, "light").id).toBe("light");
    expect(resolveTheme(DEFAULT_THEME_STATE, "dark").id).toBe("dark");
  });

  it("applies scale and reduced transparency", () => {
    applyTheme({ ...DEFAULT_THEME_STATE, scale: 1.2, reducedTransparency: true });
    const root = document.documentElement;
    expect(root.style.getPropertyValue("--cf-scale")).toBe("1.2");
    expect(root.style.getPropertyValue("--cf-panel-alpha")).toBe("1");
    expect(root.getAttribute("data-cf-reduced-transparency")).toBe("true");
  });

  it("uses advanced overrides on top of derived tokens", () => {
    const r = resolveTheme({ ...DEFAULT_THEME_STATE, mode: "preset", preset: "high-contrast" }, "dark");
    expect(r.tokens.muted).toBe("#d8d8d8");
    expect(r.tokens["accent-fg"]).toBe("#000000");
  });
});

describe("persistence", () => {
  it("round-trips state through localStorage and writes a paint cache", () => {
    const state: ThemeState = {
      ...DEFAULT_THEME_STATE,
      mode: "custom",
      customId: "c1",
      customThemes: [{ id: "c1", name: "Mine", base: { bg: "#101010", fg: "#fafafa", panel: "#181818", border: "#333333", accent: "#ff8800" } }],
      scale: 0.9,
    };
    saveThemeState(state);
    expect(loadThemeState()).toEqual(state);
    const cache = JSON.parse(window.localStorage.getItem(THEME_PAINT_KEY) ?? "{}") as { dark: { vars: Record<string, string> } };
    expect(cache.dark.vars["--cf-accent"]).toBe("#ff8800");
  });

  it("ignores corrupt or outdated stored state", () => {
    window.localStorage.setItem("cf.theme.v1", "{not json");
    expect(loadThemeState()).toBeNull();
    window.localStorage.setItem("cf.theme.v1", JSON.stringify({ version: 99 }));
    expect(loadThemeState()).toBeNull();
  });

  it("boot script paints from the cache before React runs", () => {
    saveThemeState({ ...DEFAULT_THEME_STATE, mode: "preset", preset: "paper" });
    document.documentElement.removeAttribute("style");
    // eslint-disable-next-line no-new-func
    new Function(themeBootScript)();
    expect(document.documentElement.style.getPropertyValue("--cf-bg")).toBe("#f6f1e7");
    expect(document.documentElement.getAttribute("data-cf-theme")).toBe("paper");
  });
});
