import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { FixPlanOutcome, RevisionOutcome } from "../src/api/types";
import { authenticated } from "./msw/handlers";
import { makeRunDetail } from "./msw/runs";
import { server } from "./msw/server";
import { renderApp } from "./render";

const FIX_PLAN: FixPlanOutcome = {
  kind: "fix_plan",
  plain_language: "I'll tighten the intro to eight seconds and bump the thumbnail text size.",
  estimated_cost_usd: 1.25,
  estimated_seconds: 90,
  operations: [{ op: "trim_intro" }, { op: "set_thumbnail_text_size" }],
  impact: { affected_unit_ids: ["unit_intro", "unit_thumb"] },
};

function setupRun(outcome: RevisionOutcome, extra: Parameters<typeof server.use>[0][] = []) {
  const proposals: unknown[] = [];
  server.use(
    authenticated(),
    http.get("*/v1/runs/run_3", () => HttpResponse.json(makeRunDetail({ run_id: "run_3", state: "COMPLETE" }))),
    http.post("*/v1/revisions", async ({ request }) => {
      proposals.push(await request.json());
      return HttpResponse.json({ outcome });
    }),
    ...extra,
  );
  return proposals;
}

async function sendFeedback(text: string) {
  const user = userEvent.setup();
  renderApp("/projects/run_3");
  await user.type(await screen.findByLabelText("Tell me what you don't like."), text);
  await user.click(screen.getByRole("button", { name: "Send feedback" }));
  return user;
}

describe("revision box", () => {
  it("renders a fix plan in plain language and applies it on confirm, with a toast to the new run", async () => {
    const applies: unknown[] = [];
    const proposals = setupRun(FIX_PLAN, [
      http.post("*/v1/revisions/apply", async ({ request }) => {
        applies.push(await request.json());
        return HttpResponse.json({ run_id: "run_10" }, { status: 202 });
      }),
      http.get("*/v1/runs/run_10", () => HttpResponse.json(makeRunDetail({ run_id: "run_10", state: "CREATED" }))),
    ]);
    const user = await sendFeedback("The intro is too long.");

    expect(await screen.findByText("Here's the plan")).toBeInTheDocument();
    expect(screen.getByText(FIX_PLAN.kind === "fix_plan" ? FIX_PLAN.plain_language : "")).toBeInTheDocument();
    expect(screen.getByText(/Estimated cost \$1\.25/)).toHaveTextContent("about 2 minutes");
    expect(screen.getByText("Affects 2 units:")).toBeInTheDocument();
    expect(screen.getByText("unit_intro")).toBeInTheDocument();
    expect(screen.getByText("unit_thumb")).toBeInTheDocument();
    expect(proposals).toEqual([{ project_id: "proj_1", feedback: "The intro is too long." }]);

    await user.click(screen.getByRole("button", { name: "Confirm and rebuild" }));
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("Rebuild started.");
    expect(applies).toEqual([{ project_id: "proj_1", feedback: "The intro is too long." }]);

    await user.click(screen.getByRole("link", { name: "View run" }));
    expect(await screen.findByRole("heading", { name: "Run run_10" })).toBeInTheDocument();
  });

  it("renders a clarifying question as a question", async () => {
    setupRun({ kind: "clarifying_question", question: "Which video do you mean — the teaser or the long cut?", candidate_unit_ids: ["vid_teaser", "vid_long"] });
    await sendFeedback("The video is boring.");

    expect(await screen.findByText("One question first")).toBeInTheDocument();
    expect(screen.getByText("Which video do you mean — the teaser or the long cut?")).toBeInTheDocument();
    expect(screen.getByText(/vid_teaser, vid_long/)).toBeInTheDocument();
    // No confirm button for a question.
    expect(screen.queryByRole("button", { name: "Confirm and rebuild" })).not.toBeInTheDocument();
  });

  it("renders a refusal calmly with the policy reason and no dead end", async () => {
    setupRun({ kind: "refusal", policy: "persona_firewall", reason: "That would change the persona's disclosed identity, which is locked." });
    await sendFeedback("Make the persona claim to be a real doctor.");

    expect(await screen.findByText("That change can't be made")).toBeInTheDocument();
    expect(screen.getByText("That would change the persona's disclosed identity, which is locked.")).toBeInTheDocument();
    expect(screen.getByText(/persona_firewall/)).toBeInTheDocument();
    // Calm, not alarmed: rendered as an outcome panel, not an alert.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // No dead end: the box invites a rephrase and stays usable.
    expect(screen.getByText(/You can rephrase/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send feedback" })).toBeEnabled();
  });

  it("renders a required gate with the reason", async () => {
    setupRun({ kind: "gate_required", gate: "owner_approval", reason: "Republishing to a live channel needs an owner to sign off." });
    await sendFeedback("Publish it again with the fix.");

    expect(await screen.findByText("This needs a gate first")).toBeInTheDocument();
    expect(screen.getByText("Republishing to a live channel needs an owner to sign off.")).toBeInTheDocument();
    expect(screen.getByText(/owner_approval/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("revision box persistence", () => {
  it("keeps the box available while a run is producing", async () => {
    server.use(authenticated(), http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail())));
    renderApp("/projects/run_1");
    expect(await screen.findByLabelText("Tell me what you don't like.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send feedback" })).toBeDisabled();
  });

  it("waits for the outcome and disables double submit", async () => {
    setupRun(FIX_PLAN);
    await sendFeedback("Shorter please.");
    await waitFor(() => expect(screen.getByText("Here's the plan")).toBeInTheDocument());
  });
});
