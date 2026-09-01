import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { FanMessage } from "../src/api/types";
import { authenticated } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

function makeMessage(overrides: Partial<FanMessage> = {}): FanMessage {
  return {
    id: "emsg_00000001",
    platform: "mastodon",
    account: "@nova@example.social",
    fan_id: "fan1",
    text: "How do you plan your videos?",
    received_at: "2026-09-01T08:00:00Z",
    message_class: "question",
    vip: false,
    disposition: "pending",
    skip_reason: null,
    ...overrides,
  };
}

describe("inbox page", () => {
  it("shows classified messages, flags safety, and requires a reason to skip", async () => {
    const user = userEvent.setup();
    const decisions: unknown[] = [];
    server.use(
      authenticated(),
      http.get("*/v1/engagement/inbox", () =>
        HttpResponse.json([
          makeMessage(),
          makeMessage({ id: "emsg_00000002", fan_id: "fan3", text: "i'm 14 and…", message_class: "safety_relevant" }),
        ]),
      ),
      http.post("*/v1/engagement/inbox/emsg_00000001/disposition", async ({ request }) => {
        decisions.push(await request.json());
        return HttpResponse.json(makeMessage({ disposition: "skipped", skip_reason: "answered on stream" }));
      }),
    );
    renderApp("/inbox");

    expect(await screen.findByText("How do you plan your videos?")).toBeInTheDocument();
    // Safety messages are visibly urgent and marked human-only.
    expect(screen.getByText("Safety")).toBeInTheDocument();
    expect(screen.getByText(/needs you personally/)).toBeInTheDocument();

    // Skip demands a reason before anything is sent.
    const [skipButton] = screen.getAllByRole("button", { name: "Skip" });
    if (!skipButton) throw new Error("no skip button");
    await user.click(skipButton);
    const submit = screen.getByRole("button", { name: "Skip with reason" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("Why skip this one?"), "answered on stream");
    await user.click(submit);
    await waitFor(() => expect(decisions).toEqual([{ disposition: "skipped", reason: "answered on stream" }]));
  });

  it("surfaces the disabled-engagement explanation from the server on sync", async () => {
    const user = userEvent.setup();
    server.use(
      authenticated(),
      http.post("*/v1/engagement/sync", () => HttpResponse.json({ detail: "engagement is disabled (settings.engagement.enabled)" }, { status: 503 })),
    );
    renderApp("/inbox");
    await user.click(await screen.findByRole("button", { name: "Check for new messages" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("engagement is disabled");
  });
});
