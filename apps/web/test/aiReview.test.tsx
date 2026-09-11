/**
 * The vision model's second opinion, and the line it must not cross.
 *
 * `review_frames` stops because a measurement cannot see whether the subject is the same subject.
 * A vision model can, and it is the third reviewer the contract has expected since it was written
 * (`ReviewerKind` is `operator | agent | vlm`). What these tests pin is the boundary, because the
 * failure mode of a fluent model is a confident wrong answer applied automatically:
 *
 * * asking for a review records **no verdict**;
 * * "mark these" fills in the operator's own selection and still needs their reason and their
 *   button, and it never overwrites a decision they already made;
 * * a **stale** opinion — the frames were redrawn after it was written — is labelled;
 * * "cannot ask right now" and "asked and it failed" read differently, because one is fixed by
 *   waiting for a run to finish and the other by fixing the model stack.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { aiReviewMode, aiReviewPosts, authenticated, verdictPosts } from "./msw/handlers";
import { AI_REVIEW, REVIEW_PAGE } from "./msw/runs";
import { server } from "./msw/server";
import { renderApp } from "./render";

/** Open the run that is parked at the gate, with the drawings in front of you. */
async function openGate() {
  const user = userEvent.setup();
  server.use(authenticated());
  renderApp("/workspace");
  await user.click(await screen.findByRole("button", { name: /^History/ }));
  const list = within(screen.getByRole("region", { name: "Run history" })).getByRole("list", {
    name: "Runs",
  });
  await user.click(within(list).getByRole("button", { name: /ps2c-amber/ }));
  await screen.findByRole("region", { name: "Frame review" });
  return user;
}

/** The gate as it arrives with an opinion already stored. */
function withStoredReview(overrides: Partial<typeof AI_REVIEW> = {}) {
  server.use(
    http.get("*/v1/run-history/:runId/review", () =>
      HttpResponse.json({
        ...REVIEW_PAGE,
        ai_reviews: {
          [REVIEW_PAGE.reviews[0]!.deliverable]: { ...AI_REVIEW, ...overrides },
        },
      }),
    ),
  );
}

describe("asking a vision model to look", () => {
  it("offers the review where the pictures are, and says what it is for", async () => {
    await openGate();
    const panel = await screen.findByRole("region", { name: "AI review" });

    expect(
      within(panel).getByRole("button", { name: /Ask the AI to review these/ }),
    ).toBeInTheDocument();
    // What it adds over the measurements, and what it does not do.
    expect(within(panel).getByText(/same subject in the same world/)).toBeInTheDocument();
    expect(within(panel).getByText(/It decides nothing/)).toBeInTheDocument();
  });

  it("shows what it saw in each picture, and what moves across the set", async () => {
    const user = await openGate();
    const panel = await screen.findByRole("region", { name: "AI review" });

    await user.click(within(panel).getByRole("button", { name: /Ask the AI to review these/ }));

    expect(aiReviewPosts).toHaveLength(1);
    expect(aiReviewPosts[0]).toMatchObject({ runId: "overnight~ps2c-amber" });

    const reviewed = await screen.findByRole("region", { name: "AI review" });
    // The set verdict, which is the question that was asked.
    expect(within(reviewed).getByText("Does not read as one set.")).toBeInTheDocument();
    expect(within(reviewed).getByText(/rough amber to polished glass/)).toBeInTheDocument();
    // `shows` for each frame: what the model says is *in* the picture. Reading it against the
    // thumbnail is the only check there is on the reviewer, so it has to be on screen.
    expect(
      within(reviewed).getByText(/a polished honey-coloured glass bead, no insect/),
    ).toBeInTheDocument();
    expect(within(reviewed).getByText(/an insect visible inside it/)).toBeInTheDocument();
  });

  it("records no verdict, however sure it is", async () => {
    const user = await openGate();
    await user.click(
      within(await screen.findByRole("region", { name: "AI review" })).getByRole("button", {
        name: /Ask the AI to review these/,
      }),
    );
    await screen.findByText("Does not read as one set.");

    // It called one frame unusable and named it as drifting. Nothing was decided.
    expect(verdictPosts).toHaveLength(0);
    const gate = screen.getByRole("region", { name: "Frame review" });
    expect(within(gate).getByRole("heading", { name: /2 drawings waiting for you/ })).toBeInTheDocument();
  });

  it("marks its rejections in the operator's own form, and still asks for a reason", async () => {
    withStoredReview();
    const user = await openGate();
    const panel = await screen.findByRole("region", { name: "AI review" });

    await user.click(within(panel).getByRole("button", { name: /Mark its 1 rejection/ }));

    const gate = screen.getByRole("region", { name: "Frame review" });
    const groups = within(gate).getAllByRole("group", { name: /Verdict for/ });
    // The frame it flagged is marked as a rejection; the one it passed is left alone, because a
    // model's opinion is not a decision about the other frames either.
    const flagged = groups.find((g) => g.getAttribute("aria-label")?.includes("shot_d3f50497205c"));
    expect(within(flagged!).getByRole("button", { name: "Reject" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const passed = groups.find((g) => g.getAttribute("aria-label")?.includes("shot_9ff49b91f418"));
    expect(within(passed!).getByRole("button", { name: "Accept" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    // And the reason is still the reviewer's to write: it is what the redraw is told.
    expect(within(gate).getByRole("button", { name: /Record verdict/ })).toBeDisabled();
    expect(within(gate).getByText(/A rejection needs a reason/)).toBeInTheDocument();
  });

  it("does not overwrite a decision the operator already made", async () => {
    withStoredReview();
    const user = await openGate();
    const gate = screen.getByRole("region", { name: "Frame review" });
    const groups = within(gate).getAllByRole("group", { name: /Verdict for/ });
    const flagged = groups.find((g) => g.getAttribute("aria-label")?.includes("shot_d3f50497205c"))!;

    // The person looked and accepted the frame the model would reject.
    await user.click(within(flagged).getByRole("button", { name: "Accept" }));
    await user.click(
      within(screen.getByRole("region", { name: "AI review" })).getByRole("button", {
        name: /Mark its 1 rejection/,
      }),
    );

    // Their judgement stands. A model must not silently reverse a person on the same picture.
    expect(within(flagged).getByRole("button", { name: "Accept" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("labels an opinion about frames that have since been redrawn", async () => {
    withStoredReview({ current: false });
    await openGate();
    const panel = await screen.findByRole("region", { name: "AI review" });

    expect(
      within(panel).getByText(/redrawn after this review was written/),
    ).toBeInTheDocument();
    // Kept rather than deleted: it says what was wrong last time.
    expect(within(panel).getByText(/the second frame is a different object/)).toBeInTheDocument();
  });

  it("tells a busy GPU apart from a broken model stack", async () => {
    aiReviewMode.next = 409;
    const user = await openGate();
    const panel = await screen.findByRole("region", { name: "AI review" });
    await user.click(within(panel).getByRole("button", { name: /Ask the AI to review these/ }));

    // 409: nothing is wrong with the pictures — wait for the run, or stop it.
    expect(await within(panel).findByRole("alert")).toHaveTextContent(/GPU is busy with image-set/);

    aiReviewMode.next = 502;
    await user.click(within(panel).getByRole("button", { name: /Ask the AI to review these/ }));
    // 502: the thing to fix is the model stack, and the sentence says so.
    expect(await within(panel).findByRole("alert")).toHaveTextContent(/did not answer/);
  });

  it("keeps the provenance of the judgement, including what the model was told", async () => {
    withStoredReview();
    await openGate();
    const panel = await screen.findByRole("region", { name: "AI review" });

    // Which weights answered: "the local vision model" is three different models over a year.
    expect(within(panel).getByText(/local_structured_quality/)).toBeInTheDocument();
    expect(within(panel).getByText("qwen3.6-27b-heretic:Q4_K_M")).toBeInTheDocument();
    // And the brief it was given, which is the only way to tell a wrong picture from a missing
    // brief when it says something did not match intent.
    expect(within(panel).getByText(/one rough irregular lump of unpolished amber/)).toBeInTheDocument();
  });

  it("puts the model's description on the frame card it is about", async () => {
    withStoredReview();
    await openGate();
    const gate = screen.getByRole("region", { name: "Frame review" });

    // Beside the picture, not only in the list below it: "it says this is a different object"
    // belongs next to the object.
    const cards = gate.querySelectorAll(".cf-review__opinion");
    expect(cards).toHaveLength(2);
    expect(cards[1]?.textContent).toContain("polished honey-coloured glass bead");
  });
});
