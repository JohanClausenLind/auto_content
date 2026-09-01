import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunCanvas } from "../src/RunCanvas";
import { RUN_NODES } from "./fixtures";

describe("RunCanvas", () => {
  it("mounts, runs the ELK layout and renders a node per API node", async () => {
    render(<RunCanvas nodes={RUN_NODES} onSelect={() => {}} selectedNodeId="plan" aria-label="Run graph" />);
    const region = screen.getByRole("region", { name: "Run graph" });
    expect(region).toBeInTheDocument();
    await waitFor(() => expect(region.querySelectorAll(".cf-runnode")).toHaveLength(RUN_NODES.length));
    const selected = region.querySelector('.cf-runnode[data-selected]');
    expect(selected).not.toBeNull();
    expect(selected).toHaveAttribute("aria-label", expect.stringContaining("plan"));
  });
});
