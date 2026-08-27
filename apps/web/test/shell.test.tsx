import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { authenticated, makeSession, META, themePuts } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

describe("shell", () => {
  it("theme toggle persists to localStorage and PUTs /v1/prefs/theme", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/");
    const toggle = await screen.findByRole("button", { name: "Switch to light theme" });
    await user.click(toggle);
    expect(screen.getByRole("button", { name: "Switch to dark theme" })).toBeInTheDocument();
    expect(document.documentElement.getAttribute("data-cf-theme")).toBe("light");
    expect(JSON.parse(window.localStorage.getItem("cf.theme.v1") ?? "{}")).toMatchObject({ mode: "preset", preset: "light" });
    await waitFor(() => expect(themePuts.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(themePuts.at(-1)).toMatchObject({ value: { mode: "preset", preset: "light" } });
  });

  it("hides Operations for non-owners and bounces direct navigation", async () => {
    server.use(authenticated(makeSession({ is_owner: false })));
    const { router } = renderApp("/operations");
    const nav = await screen.findByRole("navigation", { name: "Areas" });
    expect(within(nav).queryByRole("link", { name: "Operations" })).not.toBeInTheDocument();
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Good to see you");
  });

  it("shows Operations for the owner", async () => {
    server.use(authenticated(makeSession({ is_owner: true })));
    renderApp("/operations");
    const nav = await screen.findByRole("navigation", { name: "Areas" });
    expect(within(nav).getByRole("link", { name: "Operations" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Operations" })).toBeInTheDocument();
  });

  it("shows the kill-switch pill and the version in the user menu", async () => {
    const user = userEvent.setup();
    server.use(authenticated(), http.get("*/v1/meta", () => HttpResponse.json({ ...META, kill_switch: true })));
    renderApp("/");
    expect(await screen.findByText(/Kill switch ON/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Account menu/ }));
    expect(await screen.findByText("Content Factory 0.1.0-test")).toBeInTheDocument();
  });

  it("switches workspace from the switcher", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: /Workspace: Acme Studio/ }));
    await user.click(await screen.findByRole("menuitemradio", { name: /Side Project/ }));
    await screen.findByRole("button", { name: /Workspace: Side Project/ });
  });

  it("settings themes tab renders the customizer with presets", async () => {
    server.use(authenticated());
    renderApp("/settings");
    expect(await screen.findByRole("tab", { name: "Themes" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("group", { name: "Presets" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^High contrast theme/ })).toBeInTheDocument();
  });
});
