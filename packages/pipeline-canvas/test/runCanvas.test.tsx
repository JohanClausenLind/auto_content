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

  it("shows each unfinished node's estimate on the node itself", async () => {
    render(<RunCanvas nodes={RUN_NODES} aria-label="Run graph" />);
    const region = screen.getByRole("region", { name: "Run graph" });
    await waitFor(() => expect(region.querySelectorAll(".cf-runnode")).toHaveLength(RUN_NODES.length));
    const etas = [...region.querySelectorAll(".cf-runnode__eta")].map((el) => el.textContent);
    expect(etas).toEqual(["~42 s", "~10 min"]);  // the running and the queued node, and only those
    // The estimate says how much evidence is behind it rather than presenting a median of one
    // and a median of three hundred as the same claim.
    expect(region.querySelector(".cf-runnode__eta")).toHaveAttribute("title", "median of 9 past run(s)");
  });
});
