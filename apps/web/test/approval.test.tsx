import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { authenticated } from "./msw/handlers";
import { makeRunDetail } from "./msw/runs";
import { server } from "./msw/server";
import { renderApp } from "./render";

function waitingRun() {
  return makeRunDetail({ run_id: "run_2", state: "WAITING_FOR_APPROVAL", preflight_revision_hash: "rev_abc123" });
}

describe("approval flow", () => {
  it("approves a waiting run and the banner goes away", async () => {
    const user = userEvent.setup();
    const approvals: unknown[] = [];
    let state: "WAITING_FOR_APPROVAL" | "APPROVED" = "WAITING_FOR_APPROVAL";
    server.use(
      authenticated(),
      http.get("*/v1/runs/run_2", () => HttpResponse.json({ ...waitingRun(), state, approved_by: state === "APPROVED" ? "vega" : null })),
      http.post("*/v1/runs/run_2/approval", async ({ request }) => {
        approvals.push(await request.json());
        state = "APPROVED";
        return HttpResponse.json({});
      }),
    );
    renderApp("/projects/run_2");

    const banner = await screen.findByRole("region", { name: "Approval needed" });
    expect(banner).toHaveTextContent("rev_abc123");
    expect(screen.getByText("Nothing is produced until you approve the plan below.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("The plan is approved; production is about to start.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Approval needed" })).not.toBeInTheDocument();
    expect(approvals).toEqual([{ revision_hash: "rev_abc123", decision: "approve" }]);
  });

  it("rejects with a reason", async () => {
    const user = userEvent.setup();
    const approvals: unknown[] = [];
    server.use(
      authenticated(),
      http.get("*/v1/runs/run_2", () => HttpResponse.json(waitingRun())),
      http.post("*/v1/runs/run_2/approval", async ({ request }) => {
        approvals.push(await request.json());
        return HttpResponse.json({});
      }),
    );
    renderApp("/projects/run_2");

    await screen.findByRole("region", { name: "Approval needed" });
    await user.type(screen.getByLabelText(/Note/), "hook is weak");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(approvals).toEqual([{ revision_hash: "rev_abc123", decision: "reject", reason: "hook is weak" }]));
  });

  it("asks for step-up on 403 and retries the approval after the password", async () => {
    const user = userEvent.setup();
    const approvals: unknown[] = [];
    const stepUps: unknown[] = [];
    let steppedUp = false;
    server.use(
      authenticated(),
      http.get("*/v1/runs/run_2", () => HttpResponse.json(waitingRun())),
      http.post("*/v1/runs/run_2/approval", async ({ request }) => {
        approvals.push(await request.json());
        if (!steppedUp) {
          return HttpResponse.json({ detail: "Step-up required." }, { status: 403, headers: { "X-Step-Up": "required" } });
        }
        return HttpResponse.json({});
      }),
      http.post("*/v1/session/step-up", async ({ request }) => {
        const body = (await request.json()) as { password: string };
        stepUps.push(body);
        if (body.password !== "correct horse") return HttpResponse.json({ detail: "Wrong password." }, { status: 401 });
        steppedUp = true;
        return HttpResponse.json({});
      }),
    );
    renderApp("/projects/run_2");

    await screen.findByRole("region", { name: "Approval needed" });
    await user.click(screen.getByRole("button", { name: "Approve" }));

    // The step-up dialog appears instead of an error.
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(dialog).toHaveTextContent("Approvals need a fresh sign-in.");

    // A wrong password shows the error and keeps the dialog open.
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong password.");

    await user.clear(screen.getByLabelText("Password"));
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    // Dialog closes and the original decision is retried and accepted.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(approvals).toHaveLength(2));
    expect(approvals[0]).toEqual(approvals[1]);
    expect(stepUps).toEqual([{ password: "nope" }, { password: "correct horse" }]);
  });
});
