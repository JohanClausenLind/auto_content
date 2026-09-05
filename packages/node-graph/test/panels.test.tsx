import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NodeLibraryPanel } from "../src/NodeLibraryPanel";
import { NodeSearchDialog } from "../src/NodeSearchDialog";
import { catalog } from "./fixtures";

describe("NodeLibraryPanel", () => {
  it("groups by category, filters, and adds on click", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<NodeLibraryPanel catalog={catalog} onAdd={onAdd} />);
    expect(screen.getByRole("button", { name: /input/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /video/ })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Search node library"), "script");
    expect(screen.getByText("Write Script")).toBeInTheDocument();
    expect(screen.queryByText("Save Output")).not.toBeInTheDocument();

    await user.click(screen.getByText("Write Script"));
    expect(onAdd).toHaveBeenCalledWith("test.script");
  });

  it("library rows are draggable with the node type payload", () => {
    render(<NodeLibraryPanel catalog={catalog} onAdd={() => {}} />);
    const row = screen.getByText("Write Script").closest("button");
    expect(row).toHaveAttribute("draggable", "true");
  });
});

describe("NodeSearchDialog", () => {
  it("filters as you type and Enter picks the top hit", async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    render(
      <NodeSearchDialog catalog={catalog} at={{ x: 10, y: 10 }} onPick={onPick} onClose={() => {}} />,
    );
    const input = screen.getByLabelText("Search nodes");
    await user.type(input, "save");
    await user.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ type: "test.save" }));
  });

  it("with a dragged output type only offers nodes that accept it", () => {
    render(
      <NodeSearchDialog
        catalog={catalog}
        at={{ x: 0, y: 0 }}
        forOutputType="VIDEO"
        onPick={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Save Output")).toBeInTheDocument();
    expect(screen.queryByText("Write Script")).not.toBeInTheDocument();
  });

  it("escape closes", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<NodeSearchDialog catalog={catalog} at={{ x: 0, y: 0 }} onPick={() => {}} onClose={onClose} />);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});
