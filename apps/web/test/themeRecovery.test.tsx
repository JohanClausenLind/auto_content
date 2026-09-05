import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { authenticated, themePuts } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

/**
 * Settings crashed with "can't access property length, state.customThemes is undefined" because
 * /v1/prefs/theme held a row from an earlier build and the adapter passed it through unvalidated.
 */
describe("a theme preference row that predates the current schema", () => {
  const partial = { preset: "midnight", scale: 1.1 };

  it("opens Settings instead of crashing, and keeps the stored preset", async () => {
    server.use(authenticated(), http.get("*/v1/prefs/theme", () => HttpResponse.json({ value: partial })));
    renderApp("/settings");

    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    // The customizer is the crash site: it reads state.customThemes.length on the default tab.
    expect(await screen.findByRole("tab", { name: "Themes" })).toBeInTheDocument();
    await waitFor(() => expect(document.documentElement.dataset["cfTheme"] ?? "").not.toBe("__crashed__"));
    expect(screen.queryByText(/customThemes/i)).not.toBeInTheDocument();
  });

  it("rewrites the row complete once something changes", async () => {
    server.use(authenticated(), http.get("*/v1/prefs/theme", () => HttpResponse.json({ value: partial })));
    renderApp("/settings");
    await screen.findByRole("heading", { name: "Settings" });

    // The repaired state is pushed back, so the next read no longer needs repairing.
    await waitFor(() => expect(themePuts.length).toBeGreaterThan(0), { timeout: 4000 });
    const sent = themePuts.at(-1) as { value: Record<string, unknown> };
    expect(sent.value["customThemes"]).toEqual([]);
    expect(sent.value["preset"]).toBe("midnight");
    expect(sent.value["scale"]).toBe(1.1);
    expect(sent.value["version"]).toBe(1);
  });

  it("still works when the row is unusable junk", async () => {
    server.use(authenticated(), http.get("*/v1/prefs/theme", () => HttpResponse.json({ value: "not-a-theme" })));
    renderApp("/settings");
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
  });
});
