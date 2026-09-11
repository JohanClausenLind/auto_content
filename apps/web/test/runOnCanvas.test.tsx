/**
 * Reviewing a run from the canvas it was built on.
 *
 * Before this, a run's output was a panel *over* the graph: five lists grouped by file type, and
 * nothing connecting a bad drawing to the node that drew it. The screenshot this was built from
 * is 22 nodes at 20% zoom with a Run button and no way to see anything any of them made.
 *
 * So: picking a run lays it over the canvas, closing its pane leaves the drawings on the nodes,
 * and a node's own count opens every frame, voice line and film that step produced — with the
 * step's facts, because "3 attempts, backend hidream-o1" is the first thing to know about a
 * picture that came out wrong.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { applyOps, makeNode, serializeGraph } from "@content-factory/node-graph";
import { emptyGraph } from "@content-factory/node-graph";
import { authenticated, graphStore } from "./msw/handlers";
import { server } from "./msw/server";
import { workspaceCatalog } from "../src/workspace/catalog";
import { renderApp } from "./render";

/**
 * A graph carrying the lane keys the fixture run reports, seeded on the server.
 *
 * The keys are the join: without them a canvas shows nothing, which is the correct behaviour for
 * a hand-built graph and useless for a test about a lane's own run.
 */
function seedLaneGraph(): void {
  let graph = emptyGraph("graph_lane", "Picture story");
  for (const [key, type] of [
    ["anchor", "generate_anchor"],
    ["mix", "mix_audio"],
    ["cut", "compose_video"],
  ] as const) {
    graph = applyOps(graph, [
      { op: "add_node", node: makeNode(workspaceCatalog, type, { key, x: 0, y: 0 }) },
    ]).graph;
  }
  graphStore.set("graph_lane", JSON.parse(serializeGraph(graph)));
}

async function openRunOnCanvas(name = "ps1c-pinecone") {
  const user = userEvent.setup();
  server.use(authenticated());
  seedLaneGraph();
  renderApp("/workspace");
  // The workspace adopts the server's graphs and keeps the local tab active, so the lane graph
  // has to be selected the way an operator would select it.
  await user.click(await screen.findByRole("tab", { name: /Picture story/ }));
  await user.click(await screen.findByRole("button", { name: /^History/ }));
  const list = within(screen.getByRole("region", { name: "Run history" })).getByRole("list", {
    name: "Runs",
  });
  await user.click(within(list).getByRole("button", { name: new RegExp(name) }));
  return user;
}

describe("a run on the nodes", () => {
  it("says which run the canvas is showing, with what it was rendering and when", async () => {
    await openRunOnCanvas();

    const bar = await screen.findByRole("region", { name: "Run shown on the canvas" });
    // What it was rendering, not the directory name: `overnight~ps1c-pinecone` says which lane
    // ran and nothing about what came out of it.
    expect(within(bar).getByText(/a single open pine cone/)).toBeInTheDocument();
    expect(within(bar).getByText(/ps1c-pinecone/)).toBeInTheDocument();
    expect(within(bar).getByText(/audio-picture-story/)).toBeInTheDocument();
    // A real date, machine-readable as well as human-readable.
    expect(bar.querySelector("time")).toHaveAttribute("datetime");
  });

  it("closing the run's pane leaves the drawings on the nodes", async () => {
    const user = await openRunOnCanvas();

    // The pane opens over the canvas, as it always did.
    const pane = await screen.findByRole("region", { name: "Run detail" });
    await user.click(within(pane).getByRole("button", { name: "Close run" }));

    // …and closing it lands on the canvas with the run still on it. That is the whole point: a
    // panel in front of the node page cannot be the answer to "show me the output on the nodes".
    expect(screen.queryByRole("region", { name: "Run detail" })).not.toBeInTheDocument();
    expect(
      await screen.findByRole("region", { name: "Run shown on the canvas" }),
    ).toBeInTheDocument();
    // The node that drew the picture is showing it.
    const thumbs = document.querySelectorAll(".ng-outputs__thumb");
    expect(thumbs.length).toBeGreaterThan(0);
    expect(thumbs[0]?.getAttribute("src")).toContain("anchors/frames/0000.png");
  });

  it("opens one node's output: every frame, the voice line, the film, and the step's facts", async () => {
    const user = await openRunOnCanvas();
    const pane = await screen.findByRole("region", { name: "Run detail" });
    await user.click(within(pane).getByRole("button", { name: "Close run" }));

    // The node's own count is the button. `anchor` drew one picture and one control map.
    await user.click(await screen.findByRole("button", { name: /2 images/ }));

    const nodePane = await screen.findByRole("region", { name: /Output of/ });
    expect(within(nodePane).getByText("generate_anchor")).toBeInTheDocument();
    // The facts, which is what a bad drawing is diagnosed from.
    expect(within(nodePane).getByText(/attempts/)).toBeInTheDocument();
    expect(within(nodePane).getByText("3")).toBeInTheDocument();
    expect(within(nodePane).getByText(/hidream-o1/)).toBeInTheDocument();
    // The drawing is shown; the pose skeleton is folded away until asked for.
    expect(nodePane.querySelectorAll(".cf-nodeout__grid img")).toHaveLength(1);
    await user.click(within(nodePane).getByRole("button", { name: /Show 1 intermediate file/ }));
    expect(nodePane.querySelectorAll(".cf-nodeout__grid img")).toHaveLength(2);
  });

  it("plays the voice line and the film on the nodes that made them", async () => {
    const user = await openRunOnCanvas();
    const pane = await screen.findByRole("region", { name: "Run detail" });
    await user.click(within(pane).getByRole("button", { name: "Close run" }));

    // The narration is on `mix`, and it plays here rather than downloading: the session is a
    // cookie, so the element authenticates itself and the browser does the streaming.
    await user.click(await screen.findByRole("button", { name: /^1 audio/ }));
    const mixPane = await screen.findByRole("region", { name: /Output of/ });
    expect(mixPane.querySelector("audio")?.getAttribute("src")).toContain(
      "narration-mastered.wav",
    );
    await user.click(within(mixPane).getByRole("button", { name: "Close node output" }));

    // The film is on `cut`, with its captions beside it.
    await user.click(await screen.findByRole("button", { name: /1 video/ }));
    const cutPane = await screen.findByRole("region", { name: /Output of/ });
    const video = cutPane.querySelector("video");
    expect(video?.getAttribute("src")).toContain("final.mp4");
    // preload=metadata: opening a node must not pull 20 MB to draw a list.
    expect(video).toHaveAttribute("preload", "metadata");
    expect(within(cutPane).getByRole("link", { name: /captions\.srt/ })).toBeInTheDocument();
  });

  it("switches to the next run without going back to the list", async () => {
    const user = await openRunOnCanvas();
    const bar = await screen.findByRole("region", { name: "Run shown on the canvas" });

    await user.click(within(bar).getByRole("button", { name: "Older run" }));

    // The second fixture run: a different subject, which is how you can tell it moved.
    expect(
      await within(
        screen.getByRole("region", { name: "Run shown on the canvas" }),
      ).findByText(/unpolished amber/),
    ).toBeInTheDocument();
  });

  it("says when the run's steps are not the graph on screen", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    // A graph with only one of the lane's nodes: the other two steps have files and nowhere to
    // put them. Without the sentence, this reads as a run that produced nothing.
    let graph = emptyGraph("graph_lane", "One node");
    graph = applyOps(graph, [
      {
        op: "add_node",
        node: makeNode(workspaceCatalog, "mix_audio", { key: "mix", x: 0, y: 0 }),
      },
    ]).graph;
    graphStore.set("graph_lane", JSON.parse(serializeGraph(graph)));
    renderApp("/workspace");
    await user.click(await screen.findByRole("tab", { name: /One node/ }));
    await user.click(await screen.findByRole("button", { name: /^History/ }));
    const list = within(screen.getByRole("region", { name: "Run history" })).getByRole("list", {
      name: "Runs",
    });
    await user.click(within(list).getByRole("button", { name: /ps1c-pinecone/ }));

    const bar = await screen.findByRole("region", { name: "Run shown on the canvas" });
    expect(within(bar).getByText(/steps of this run are not on this canvas/)).toBeInTheDocument();
  });
});

describe("finding a run in the history", () => {
  it("groups the rows by day and says what each was rendering", async () => {
    await openRunOnCanvas();
    const panel = screen.getByRole("region", { name: "Run history" });

    expect(within(panel).getByText(/a single open pine cone/)).toBeInTheDocument();
    // A day heading, so "what did last night make" is answered by the list's own shape rather
    // than by reading 241 timestamps.
    expect(panel.querySelectorAll(".cf-hist__day").length).toBeGreaterThan(0);
  });

  it("searches what was rendered, not only the directory name", async () => {
    const user = await openRunOnCanvas();
    const panel = screen.getByRole("region", { name: "Run history" });

    await user.type(within(panel).getByRole("searchbox", { name: "Search runs" }), "amber");

    const rows = within(within(panel).getByRole("list", { name: "Runs" })).getAllByRole("button");
    expect(rows).toHaveLength(1);
    // Found by its subject: nothing in `overnight~ps2c-amber`'s row name says "unpolished amber",
    // and "the amber one" is how a run is actually remembered.
    expect(rows[0]).toHaveTextContent("unpolished amber");
  });
});
