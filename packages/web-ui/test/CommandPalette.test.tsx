import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { CommandPalette, useCommandPaletteHotkey, type Command } from "../src";

function Harness({ commands, initiallyOpen = true }: { commands: Command[]; initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open</button>
      <CommandPalette commands={commands} isOpen={open} onOpenChange={setOpen} />
    </>
  );
}

function HotkeyHarness({ commands }: { commands: Command[] }) {
  const [open, setOpen] = useCommandPaletteHotkey();
  return <CommandPalette commands={commands} isOpen={open} onOpenChange={setOpen} />;
}

const makeCommands = () => {
  const goInbox = vi.fn();
  const goCalendar = vi.fn();
  const toggleTheme = vi.fn();
  const commands: Command[] = [
    { id: "nav-inbox", title: "Go to Inbox", group: "Navigate", shortcut: "G I", run: goInbox },
    { id: "nav-calendar", title: "Go to Calendar", group: "Navigate", run: goCalendar },
    { id: "theme-toggle", title: "Toggle dark mode", group: "Appearance", run: toggleTheme },
  ];
  return { commands, goInbox, goCalendar, toggleTheme };
};

describe("CommandPalette", () => {
  it("filters fuzzily and runs a command with the keyboard", async () => {
    const user = userEvent.setup();
    const { commands, goCalendar, goInbox } = makeCommands();
    render(<Harness commands={commands} />);

    const dialog = await screen.findByRole("dialog", { name: "Command palette" });
    expect(dialog).toBeInTheDocument();
    const input = screen.getByRole("searchbox", { name: "Search commands" });
    expect(input).toHaveFocus();

    await user.keyboard("gcal");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
    expect(screen.getByRole("option", { name: /Go to Calendar/ })).toBeInTheDocument();
    expect(screen.getByText("1 command")).toBeInTheDocument();

    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(goCalendar).toHaveBeenCalledTimes(1));
    expect(goInbox).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows an empty state and announces no results", async () => {
    const user = userEvent.setup();
    const { commands } = makeCommands();
    render(<Harness commands={commands} />);
    await screen.findByRole("dialog");
    await user.keyboard("zzzz");
    await waitFor(() => expect(screen.getByText(/Nothing matches/)).toBeInTheDocument());
    expect(screen.getByText("No matching commands")).toBeInTheDocument();
  });

  it("opens with Ctrl+K and closes with Escape", async () => {
    const user = userEvent.setup();
    const { commands } = makeCommands();
    render(<HotkeyHarness commands={commands} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.keyboard("{Control>}k{/Control}");
    await screen.findByRole("dialog", { name: "Command palette" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("has no axe violations", async () => {
    const { commands } = makeCommands();
    render(<Harness commands={commands} />);
    const dialog = await screen.findByRole("dialog");
    const results = await axe.run(dialog, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});
