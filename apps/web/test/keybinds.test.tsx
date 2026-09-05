import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { appearancePuts, authenticated, keymapPuts } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";
import { DEFAULT_APP_PREFS } from "../src/prefs/schema";

async function signedInShell(path = "/") {
  const user = userEvent.setup();
  server.use(authenticated());
  const app = renderApp(path);
  await screen.findByRole("navigation", { name: "Areas" });
  return { user, ...app };
}

describe("shell keybinds", () => {
  it("navigates with a g sequence", async () => {
    const { user, router } = await signedInShell();
    await user.keyboard("gi");
    await waitFor(() => expect(router.state.location.pathname).toBe("/inbox"));
    expect(await screen.findByRole("heading", { name: "Inbox" })).toBeInTheDocument();
  });

  it("does not navigate on the g prefix alone, and says it is waiting", async () => {
    const { user, router } = await signedInShell();
    await user.keyboard("g");
    expect(router.state.location.pathname).toBe("/");
    // Otherwise a half-typed sequence is indistinguishable from a dropped keypress.
    expect(await screen.findByText("G")).toBeInTheDocument();
  });

  it("drops a stale sequence after the timeout instead of pairing unrelated keys", async () => {
    const { user, router } = await signedInShell();
    await user.keyboard("g");
    await waitFor(() => expect(screen.queryByText("G")).not.toBeInTheDocument(), { timeout: 4000 });
    await user.keyboard("i");
    expect(router.state.location.pathname).toBe("/");
  });

  it("opens Create with c — the shortcut the palette has always advertised", async () => {
    const { user, router } = await signedInShell();
    await user.keyboard("c");
    await waitFor(() => expect(router.state.location.pathname).toBe("/create"));
  });

  it("opens the shortcut sheet with ?", async () => {
    const { user } = await signedInShell();
    await user.keyboard("?");
    const sheet = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    expect(within(sheet).getByText("Go to Inbox")).toBeInTheDocument();
    // The sheet prints the live binding, not a hand-maintained string.
    expect(within(sheet).getAllByText("G").length).toBeGreaterThan(0);
  });

  it("still opens the command palette with Ctrl+K", async () => {
    const { user } = await signedInShell();
    await user.keyboard("{Control>}k{/Control}");
    expect(await screen.findByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
  });

  it("leaves typing alone: a modifier-free shortcut inside a field is just text", async () => {
    const { user, router } = await signedInShell();
    await user.keyboard("{Control>}k{/Control}");
    const search = await screen.findByRole("searchbox", { name: "Search commands" });
    await waitFor(() => expect(search).toHaveFocus());
    // "c" is Create and "gi" is Go to Inbox; inside a field all three are just characters.
    await user.keyboard("cgi");
    expect(search).toHaveValue("cgi");
    expect(router.state.location.pathname).toBe("/");
  });
});

describe("Settings ▸ Keyboard", () => {
  it("rebinds an action, and the new binding drives navigation", async () => {
    const { user, router } = await signedInShell("/settings?tab=keyboard");
    await user.click(await screen.findByRole("button", { name: "Record a shortcut for Go to Inbox" }));
    await user.keyboard("{Control>}j{/Control}");

    await waitFor(() => expect(keymapPuts.length).toBeGreaterThan(0), { timeout: 4000 });
    expect(keymapPuts.at(-1)).toEqual({ value: { version: 1, overrides: { "go:/inbox": ["Mod+j"] } } });

    await user.keyboard("{Control>}j{/Control}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/inbox"));
  });

  it("reports a conflict when two actions land on the same key", async () => {
    const { user } = await signedInShell("/settings?tab=keyboard");
    await user.click(await screen.findByRole("button", { name: "Record a shortcut for Create something new" }));
    await user.keyboard("t"); // already the theme toggle
    expect(await screen.findByText("Same as Toggle light / dark theme")).toBeInTheDocument();
  });

  it("shows the palette as fixed", async () => {
    await signedInShell("/settings?tab=keyboard");
    expect(await screen.findByText("Fixed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record a shortcut for Command palette" })).not.toBeInTheDocument();
  });
});

describe("Settings ▸ Appearance", () => {
  it("paints density onto the document and saves it to the account", async () => {
    const { user } = await signedInShell("/settings?tab=appearance");
    await user.selectOptions(await screen.findByLabelText("Density"), "compact");

    await waitFor(() => expect(document.documentElement.dataset["density"]).toBe("compact"));
    await waitFor(() => expect(appearancePuts.length).toBeGreaterThan(0));
    expect(appearancePuts.at(-1)).toEqual({ value: { ...DEFAULT_APP_PREFS, density: "compact" } });
  });

  it("only sets the motion attribute when overriding the system setting", async () => {
    const { user } = await signedInShell("/settings?tab=appearance");
    expect(document.documentElement.dataset["motion"]).toBeUndefined();

    await user.selectOptions(screen.getByLabelText("Motion"), "always");
    await waitFor(() => expect(document.documentElement.dataset["motion"]).toBe("reduced"));

    await user.selectOptions(screen.getByLabelText("Motion"), "system");
    await waitFor(() => expect(document.documentElement.dataset["motion"]).toBeUndefined());
  });

  it("collapses the rail from the switch and from the [ shortcut", async () => {
    const { user, container } = await signedInShell("/settings?tab=appearance");
    const shell = container.querySelector(".cf-shell");

    await user.click(screen.getByRole("switch", { name: /Collapse the area rail/ }));
    await waitFor(() => expect(shell).toHaveAttribute("data-rail", "icons"));

    await user.keyboard("[[");
    await waitFor(() => expect(shell).not.toHaveAttribute("data-rail"));
  });
});

describe("Settings ▸ Workspace", () => {
  it("offers every visible area as the landing area and persists the choice", async () => {
    const { user } = await signedInShell("/settings?tab=workspace");
    const select = await screen.findByLabelText("Open this area after signing in");
    await user.selectOptions(select, "/projects");

    await waitFor(() => expect(appearancePuts.length).toBeGreaterThan(0));
    expect(appearancePuts.at(-1)).toEqual({ value: { ...DEFAULT_APP_PREFS, landingArea: "/projects" } });
  });
});
