/**
 * What a node produced, drawn on the node.
 *
 * The editor is given the answer, not the question: the host application owns the join from a
 * lane's key to a canvas node id, and hands `outputs` keyed by node id. What is worth pinning
 * here is what the strip says and what it refuses to say — a count that is not a promise, a
 * guessed attribution that admits it, and nothing at all on a node that made nothing.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NodeOutputStrip, summarize, type NodeOutputs } from "../src/NodeOutputs";
import { estimateNodeSize, NODE_OUTPUT_STRIP_HEIGHT } from "../src/nodeDefs";
import { catalog } from "./fixtures";

function outputs(overrides: Partial<NodeOutputs> = {}): NodeOutputs {
  return {
    counts: { image: 2, video: 0, audio: 0, text: 1, data: 0 },
    previews: [
      { url: "/files/0000.png", label: "0000.png", kind: "image" },
      { url: "/files/0001.png", label: "0001.png", kind: "image" },
    ],
    total: 3,
    attribution: "recorded",
    ...overrides,
  };
}

describe("the output strip", () => {
  it("draws the thumbnails and says what else is there", () => {
    render(<NodeOutputStrip outputs={outputs()} nodeTitle="Anchor" />);

    const thumbs = document.querySelectorAll(".ng-outputs__thumb");
    expect(thumbs).toHaveLength(2);
    expect(thumbs[0]).toHaveAttribute("src", "/files/0000.png");
    // The counts are the button's label: at 260px a label and a separate control are two things
    // competing for one row, and the row is "open what this node made".
    expect(screen.getByRole("button", { name: /2 images · 1 text/ })).toBeInTheDocument();
  });

  it("renders nothing when the node produced nothing", () => {
    // An empty strip under every node would cost every graph 40px a node to say nothing.
    const { container } = render(
      <NodeOutputStrip
        outputs={outputs({ counts: { image: 0, video: 0, audio: 0, text: 0, data: 0 }, previews: [], total: 0 })}
        nodeTitle="Lock"
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("counts the files it is not showing", async () => {
    render(<NodeOutputStrip outputs={outputs({ total: 2904 })} nodeTitle="Keyframes" />);
    // 2,904 files, four thumbnails: the count must be the run's, not the strip's.
    expect(screen.getByText("+2902")).toBeInTheDocument();
  });

  it("admits when the attribution is a guess", async () => {
    const user = userEvent.setup();
    render(<NodeOutputStrip outputs={outputs({ attribution: "inferred" })} nodeTitle="Anchor" />);

    const button = screen.getByRole("button");
    expect(button).toHaveTextContent("?");
    // The whole sentence, not just the mark: a reader deciding which step to blame for a fault
    // has to know the answer came from a filename.
    expect(button).toHaveAttribute("title", expect.stringContaining("by their path"));
    await user.click(button); // disabled without an onOpen; must not throw
  });

  it("badges the drawings waiting on somebody", () => {
    render(<NodeOutputStrip outputs={outputs({ awaitingReview: 6 })} nodeTitle="Frames gate" />);
    expect(screen.getByText("6 to review")).toBeInTheDocument();
  });

  it("dims a node the open run did not execute rather than flagging it", () => {
    render(<NodeOutputStrip outputs={outputs({ carried: true })} nodeTitle="Anchor" />);
    // Not a failure: a resumed run is most of the lane, and those files are real.
    expect(document.querySelector(".ng-outputs")).toHaveAttribute("data-carried");
  });

  it("opens the full review when asked", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<NodeOutputStrip outputs={outputs()} nodeTitle="Anchor" onOpen={onOpen} />);

    await user.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledOnce();
  });
});

describe("summarize", () => {
  it("names only the kinds that are there, in a fixed order", () => {
    expect(summarize({ image: 8, video: 1, audio: 0, text: 0, data: 3 })).toBe(
      "8 images · 1 video · 3 data",
    );
    expect(summarize({ image: 1, video: 0, audio: 0, text: 0, data: 0 })).toBe("1 image");
    expect(summarize({ image: 0, video: 0, audio: 0, text: 0, data: 0 })).toBe("");
  });
});

describe("the size estimate", () => {
  it("reserves room for the strip so a node does not paint over the one below it", () => {
    const node = { width: null, collapsed: false, values: {} };
    const def = catalog.get("test.script") ?? null;
    const without = estimateNodeSize(node, def);
    const with_ = estimateNodeSize(node, def, { hasOutputs: true });
    expect(with_.height - without.height).toBe(NODE_OUTPUT_STRIP_HEIGHT);
  });

  it("reserves nothing for a collapsed node, which draws no body at all", () => {
    const node = { width: null, collapsed: true, values: {} };
    const def = catalog.get("test.script") ?? null;
    expect(estimateNodeSize(node, def, { hasOutputs: true })).toEqual(
      estimateNodeSize(node, def),
    );
  });
});
