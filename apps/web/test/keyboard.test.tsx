import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { authenticated } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

describe("keyboard-only navigation", () => {
  it("tabs through the shell and reaches Create, then uses the palette to jump to Inbox", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    const { router } = renderApp("/");
    await screen.findByRole("navigation", { name: "Areas" });

    // First Tab lands on the skip link; keep tabbing until the Create button has focus.
    await user.tab();
    expect(screen.getByText("Skip to content")).toHaveFocus();
    const create = screen.getByRole("button", { name: /Create/ });
    for (let i = 0; i < 8 && document.activeElement !== create; i++) await user.tab();
    expect(create).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/create"));
    expect(await screen.findByRole("heading", { name: "Create" })).toBeInTheDocument();

    // Command palette by shortcut, filter, arrow, enter.
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Command palette" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search commands" })).toHaveFocus();
    await user.keyboard("go inbox");
    await waitFor(() => expect(screen.getByRole("option", { name: /Go to Inbox/ })).toBeInTheDocument());
    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/inbox"));
    expect(await screen.findByRole("heading", { name: "Inbox" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("opens the theme customizer dialog from the palette and traps focus", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/");
    await screen.findByRole("navigation", { name: "Areas" });
    await user.keyboard("{Control>}k{/Control}");
    await screen.findByRole("dialog", { name: "Command palette" });
    await user.keyboard("customize");
    await waitFor(() => expect(screen.getByRole("option", { name: /Customize theme/ })).toBeInTheDocument());
    await user.keyboard("{ArrowDown}{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "Customize theme" });
    expect(dialog).toBeInTheDocument();
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
