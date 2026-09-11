/**
 * The run history in the workspace dock, and the frame-review gate answered from it.
 *
 * Everything produced on this machine came from local runs that write no database row, so the
 * app's run list could not see any of it — 226 runs and 35 films reachable only by knowing the
 * path on disk — and the gate those runs park at could only be answered from a terminal.
 *
 * These tests pin what that has to get right: the history is a list on the left that stays put
 * while you look at runs, a run opens over the canvas without leaving the workspace, the drawings
 * waiting for a person are counted where they can be seen, and a verdict recorded here is the same
 * verdict `content-factory frames review` would have written.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { authenticated, verdictPosts } from "./msw/handlers";
import { HISTORY_DETAIL, REVIEW_PAGE } from "./msw/runs";
import { server } from "./msw/server";
import { renderApp } from "./render";

async function openHistory() {
  const user = userEvent.setup();
  server.use(authenticated());
  renderApp("/workspace");
  await user.click(await screen.findByRole("button", { name: /^History/ }));
  return user;
}

/** The dock's list. It is a panel now, not a window: the canvas stays behind it. */
function panel() {
  return screen.getByRole("region", { name: "Run history" });
}

async function openRun(user: Awaited<ReturnType<typeof openHistory>>, name: string | RegExp) {
  const runs = within(panel()).getByRole("list", { name: "Runs" });
  await user.click(within(runs).getByRole("button", { name: new RegExp(name) }));
  return screen.findByRole("region", { name: "Run detail" });
}

describe("run history in the dock", () => {
  it("opens in the left dock and lists the runs made on this machine", async () => {
    await openHistory();
    const runs = within(await screen.findByRole("region", { name: "Run history" })).getByRole("list", { name: "Runs" });
    const rows = within(runs).getAllByRole("button");
    expect(rows).toHaveLength(3);
    // Named by its run directory's last segment — "ps1c-pinecone" is the pinecone story.
    expect(rows[0]).toHaveTextContent("ps1c-pinecone");
    expect(rows[0]).toHaveTextContent("audio-picture-story");
    // What it cost, which is the same evidence every ETA is a median of.
    expect(rows[0]).toHaveTextContent("4m");
    // The canvas is still there behind it — this is a dock, not a page you navigated to.
    expect(screen.getByRole("toolbar", { name: "Run controls" })).toBeInTheDocument();
  });

  it("calls a run parked at a human gate awaiting review, not failed, and counts the drawings", async () => {
    await openHistory();
    expect(within(panel()).getByText("awaiting review")).toBeInTheDocument();
    expect(within(panel()).getByText("failed")).toBeInTheDocument(); // the one that really broke
    expect(within(panel()).getAllByText("complete")).toHaveLength(1);
    // The two drawings nobody has decided about, on the row and on the button that opens it.
    expect(within(panel()).getByText("2 to review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^History/ })).toHaveTextContent("2 to review");
  });

  it("shows a run's outputs over the canvas, and plays them", async () => {
    const user = await openHistory();
    const pane = await openRun(user, "ps1c-pinecone");

    // A video element with the film's URL — the point is to watch it, not download it.
    const video = pane.querySelector("video");
    expect(video?.getAttribute("src")).toContain("/run-history/overnight~ps1c-pinecone/files/");
    expect(video?.getAttribute("src")).toContain("final.mp4");
    expect(video).toHaveAttribute("preload", "metadata"); // must not pull 20 MB to draw a list

    // The narration gets an audio player, the captions a link.
    expect(pane.querySelector("audio")?.getAttribute("src")).toContain("narration-mastered.wav");
    expect(within(pane).getByRole("link", { name: /captions\.srt/ })).toBeInTheDocument();

    // Which run it is, and where it is on disk.
    expect(within(pane).getByRole("heading", { name: "ps1c-pinecone" })).toBeInTheDocument();
    expect(within(pane).getByText(HISTORY_DETAIL.project_dir)).toBeInTheDocument();
    expect(within(pane).getByText(/12\/12 stages/)).toBeInTheDocument();
  });

  it("keeps control maps out of the way instead of burying the drawings in them", async () => {
    const user = await openHistory();
    const pane = await openRun(user, "ps1c-pinecone");

    // One anchor drawing shown; the pose skeleton is not, until asked for.
    expect(pane.querySelectorAll(".cf-hist__grid img")).toHaveLength(1);
    expect(within(pane).getByText(/Images \(1\)/)).toBeInTheDocument();

    await user.click(within(pane).getByRole("button", { name: /Show 1 intermediate file/ }));
    expect(pane.querySelectorAll(".cf-hist__grid img")).toHaveLength(2);
    expect(within(pane).getByRole("button", { name: /Hide 1 intermediate file/ })).toBeInTheDocument();
  });

  it("filters by lane and by what is waiting, and switches runs without closing anything", async () => {
    const user = await openHistory();

    const lanes = within(panel()).getByRole("navigation", { name: "Lanes" });
    await user.click(within(lanes).getByRole("button", { name: /single-image/ }));
    expect(within(within(panel()).getByRole("list", { name: "Runs" })).getAllByRole("button")).toHaveLength(1);

    // "Needs review" counts drawings, not runs: it is the question the history is opened with.
    await user.click(within(lanes).getByRole("button", { name: /Needs review/ }));
    const waiting = within(within(panel()).getByRole("list", { name: "Runs" })).getAllByRole("button");
    expect(waiting).toHaveLength(1);
    expect(waiting[0]).toHaveTextContent("ps2c-amber");

    // Switching runs is one click with the list still on the left.
    await openRun(user, "ps2c-amber");
    await user.click(within(lanes).getByRole("button", { name: /All runs/ }));
    const pane = await openRun(user, "ps1c-pinecone");
    expect(within(pane).getByRole("heading", { name: "ps1c-pinecone" })).toBeInTheDocument();
    expect(within(panel()).getByRole("list", { name: "Runs" })).toBeInTheDocument();
  });
});

describe("the frame-review gate", () => {
  it("asks with the pictures, not with filenames, and shows what was measured", async () => {
    const user = await openHistory();
    await openRun(user, "ps2c-amber");

    const review = await screen.findByRole("region", { name: "Frame review" });
    expect(within(review).getByRole("heading", { name: /2 drawings waiting for you/ })).toBeInTheDocument();
    const shots = review.querySelectorAll(".cf-review__shot img");
    expect(shots).toHaveLength(2);
    expect(shots[0]?.getAttribute("src")).toContain("anchors/shot_9ff49b91f418/0000.png");
    // The measurement that failed is on the frame it is about — "look at this one first" —
    // and again in that frame's folded list of everything measured.
    expect(within(review).getAllByText(/came back grey/)).toHaveLength(2);
    // And the contact sheet, which is every frame in one image.
    expect(within(review).getByRole("link", { name: "contact sheet" })).toBeInTheDocument();
  });

  it("records a blanket accept the way a person may give one", async () => {
    const user = await openHistory();
    await openRun(user, "ps2c-amber");
    const review = await screen.findByRole("region", { name: "Frame review" });

    await user.click(within(review).getByRole("button", { name: /Accept all 2/ }));

    expect(verdictPosts).toHaveLength(1);
    expect(verdictPosts[0]).toMatchObject({
      runId: REVIEW_PAGE.run_id,
      body: { accept_rest: true, deliverable: "dlv_short0000001" },
    });
    // A verdict unblocks the gate and makes nothing; the command that continues the run is here.
    expect(await within(review).findByText(/--from review_frames/)).toBeInTheDocument();
  });

  it("will not send a rejection without a reason, then sends exactly what was marked", async () => {
    const user = await openHistory();
    await openRun(user, "ps2c-amber");
    const review = await screen.findByRole("region", { name: "Frame review" });

    const [first, second] = within(review).getAllByRole("group", { name: /Verdict for/ });
    await user.click(within(first!).getByRole("button", { name: "Accept" }));
    await user.click(within(second!).getByRole("button", { name: "Reject" }));

    // The reason is what the redraw is told and what the prompting proposals are built from.
    const submit = within(review).getByRole("button", { name: /Record verdict/ });
    expect(submit).toBeDisabled();
    expect(within(review).getByText(/A rejection needs a reason/)).toBeInTheDocument();

    await user.type(
      within(review).getByRole("textbox", { name: /Why the rejected ones are wrong/ }),
      "a different object from the other five",
    );
    expect(submit).toBeEnabled();
    await user.click(submit);

    expect(verdictPosts).toHaveLength(1);
    expect(verdictPosts[0]!.body).toMatchObject({
      accept: ["shot_9ff49b91f418:0000"],
      reject: ["shot_d3f50497205c:0000"],
      reason: "a different object from the other five",
    });
  });
});
