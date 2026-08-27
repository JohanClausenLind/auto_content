import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { describe, expect, it, vi } from "vitest";
import { DEFAULT_THEME_STATE, ThemeCustomizer, ThemeProvider, loadThemeState, useTheme, type ThemeSyncAdapter } from "../src";

function Probe() {
  const { resolved, state } = useTheme();
  return <span data-testid="probe">{`${state.mode}:${resolved.id}`}</span>;
}

describe("ThemeCustomizer", () => {
  it("switches presets and persists, syncing through the adapter", async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockResolvedValue(undefined);
    const sync: ThemeSyncAdapter = { load: vi.fn().mockResolvedValue(null), save };
    render(
      <ThemeProvider sync={sync} saveDebounceMs={0} initialState={DEFAULT_THEME_STATE}>
        <ThemeCustomizer />
        <Probe />
      </ThemeProvider>,
    );
    await user.click(screen.getByRole("button", { name: /^Paper theme/ }));
    expect(screen.getByTestId("probe")).toHaveTextContent("preset:paper");
    expect(document.documentElement.style.getPropertyValue("--cf-bg")).toBe("#f6f1e7");
    expect(loadThemeState()?.preset).toBe("paper");
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(save.mock.calls.at(-1)?.[0]).toMatchObject({ preset: "paper", mode: "preset" });
  });

  it("creates a custom theme with live preview and rejects bad pasted JSON", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider initialState={{ ...DEFAULT_THEME_STATE, mode: "preset", preset: "dark" }}>
        <ThemeCustomizer />
        <Probe />
      </ThemeProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Customize colours" }));
    const accent = screen.getByRole("textbox", { name: "Accent hex value" });
    await user.clear(accent);
    await user.type(accent, "#ff8800");
    await waitFor(() => expect(document.documentElement.style.getPropertyValue("--cf-accent")).toBe("#ff8800"));
    expect(screen.getByTestId("probe")).toHaveTextContent("preset:preview");

    await user.type(screen.getByRole("textbox", { name: /Theme name/ }), "Pumpkin");
    await user.click(screen.getByRole("button", { name: "Save theme" }));
    expect(screen.getByTestId("probe")).toHaveTextContent(/custom:custom-/);
    expect(screen.getByRole("status", { name: "Theme status" })).toHaveTextContent("Saved “Pumpkin”.");
    expect(loadThemeState()?.customThemes[0]?.name).toBe("Pumpkin");

    await user.click(screen.getByRole("button", { name: "Paste JSON" }));
    await user.type(screen.getByRole("textbox", { name: "Theme JSON" }), "{{ broken");
    await user.click(screen.getByRole("button", { name: "Import" }));
    expect(screen.getByRole("status", { name: "Theme status" })).toHaveTextContent("not valid JSON");
  });

  it("survives a failing sync adapter", async () => {
    const sync: ThemeSyncAdapter = { load: vi.fn().mockRejectedValue(new Error("offline")), save: vi.fn().mockRejectedValue(new Error("offline")) };
    render(
      <ThemeProvider sync={sync} initialState={DEFAULT_THEME_STATE}>
        <ThemeCustomizer />
      </ThemeProvider>,
    );
    await waitFor(() => expect(screen.getByText(/Server sync is unavailable/)).toBeInTheDocument());
  });

  it("has no axe violations", async () => {
    const { container } = render(
      <ThemeProvider initialState={DEFAULT_THEME_STATE}>
        <main>
          <ThemeCustomizer />
        </main>
      </ThemeProvider>,
    );
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
