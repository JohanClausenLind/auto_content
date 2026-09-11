import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RunNodeList } from "../src/RunNodeList";
import { RUN_NODES } from "./fixtures";

describe("RunNodeList", () => {
  it("renders one entry per node with state, scope, cache badge and duration", () => {
    render(<RunNodeList nodes={RUN_NODES} />);
    const list = screen.getByRole("list", { name: "Pipeline steps" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(RUN_NODES.length);

    const research = items[0]!;
    expect(research).toHaveTextContent("research");
    expect(research).toHaveTextContent("shared");
    expect(research).toHaveTextContent("complete");
    expect(research).toHaveTextContent("cached");
    expect(research).toHaveTextContent("850 ms");

    const failed = items[4]!;
    expect(failed).toHaveTextContent("script");
    expect(failed).toHaveTextContent("d2");
    expect(failed).toHaveTextContent("failed");
    expect(failed).toHaveTextContent("voice model unavailable");

    // An unfinished step shows what it will take; a finished one shows what it took, and never
    // both — a measurement and a guess side by side invites reading the guess as a fact.
    const running = items[2]!;
    expect(running).toHaveTextContent("~42 s");
    expect(items[3]!).toHaveTextContent("~10 min");
    expect(research).not.toHaveTextContent("~");

    // Read-only list has no buttons.
    expect(within(list).queryByRole("button")).not.toBeInTheDocument();
  });

  it("selects with mouse and keyboard when onSelect is provided", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<RunNodeList nodes={RUN_NODES} onSelect={onSelect} selectedNodeId="plan" />);

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(RUN_NODES.length);
    expect(buttons[1]).toHaveAttribute("aria-pressed", "true");
    expect(buttons[0]).toHaveAttribute("aria-pressed", "false");

    await user.click(buttons[2]!);
    expect(onSelect).toHaveBeenCalledWith(RUN_NODES[2]);

    buttons[3]!.focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith(RUN_NODES[3]);
  });
});
