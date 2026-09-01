import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { authenticated } from "./msw/handlers";
import { makeActionItem, makeRunDetail } from "./msw/runs";
import { server } from "./msw/server";
import { renderApp } from "./render";

const ITEMS = [
  makeActionItem(),
  makeActionItem({
    id: "ai_2",
    kind: "run_failed",
    severity: "critical",
    title: "A render step failed",
    body: "run_4 stopped in the render stage after 3 attempts.",
    run_id: "run_4",
    deep_link: "/projects/run_4",
    created_at: "2026-08-31T09:00:00Z",
  }),
];

describe("action center", () => {
  it("shows a friendly empty state when nothing is open", async () => {
    server.use(authenticated());
    renderApp("/");
    expect(await screen.findByText("Nothing needs you right now")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Action Center, nothing needs attention" })).toBeInTheDocument();
  });

  it("lists open items with severity and updates the top-bar badge", async () => {
    server.use(authenticated(), http.get("*/v1/action-items", ({ request }) => {
      expect(new URL(request.url).searchParams.get("status_filter")).toBe("open");
      return HttpResponse.json(ITEMS);
    }));
    renderApp("/");

    const region = await screen.findByRole("region", { name: /Needs you/ });
    const items = within(region).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("A run is waiting for your approval");
    expect(items[0]).toHaveTextContent("Warning");
    expect(items[1]).toHaveTextContent("A render step failed");
    expect(items[1]).toHaveTextContent("Critical");
    expect(screen.getByRole("button", { name: "Action Center, 2 items need attention" })).toBeInTheDocument();
  });

  it("deep links navigate to the run", async () => {
    const user = userEvent.setup();
    server.use(
      authenticated(),
      http.get("*/v1/action-items", () => HttpResponse.json(ITEMS)),
      http.get("*/v1/runs/run_4", () => HttpResponse.json(makeRunDetail({ run_id: "run_4", state: "FAILED", error: "render step failed 3 times" }))),
    );
    const { router } = renderApp("/");

    await user.click(await screen.findByRole("link", { name: /A render step failed/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/run_4"));
    expect(await screen.findByRole("heading", { name: "Run run_4" })).toBeInTheDocument();
    expect(screen.getByText("The run stopped because a step failed.")).toBeInTheDocument();
  });
});
