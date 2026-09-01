import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { HEALTH } from "./msw/campaigns";
import { authenticated, makeSession } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

const AXE_OPTS = { rules: { "color-contrast": { enabled: false } } };

describe("operations", () => {
  it("renders the health table with status chips, details and fixes", async () => {
    server.use(authenticated());
    renderApp("/operations");

    const table = await screen.findByRole("table", { name: "Health checks" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(HEALTH.checks.length);
    expect(within(table).getByText("database")).toBeInTheDocument();
    expect(within(table).getAllByText("OK")).toHaveLength(2);
    expect(within(table).getByText("Warning")).toBeInTheDocument();
    expect(within(table).getByText("Failing")).toBeInTheDocument();
    expect(within(table).getByText("Skipped")).toBeInTheDocument();
    expect(within(table).getByText("VAPID keys are not configured")).toBeInTheDocument();
    expect(within(table).getByText("run ./setup.sh to generate keys")).toBeInTheDocument();
    // Honest verdict when a check fails.
    expect(screen.getByRole("status")).toHaveTextContent("Something needs attention");
  });

  it("refreshes the checks, renders the audit tail, and links to the workflow engine", async () => {
    const user = userEvent.setup();
    let healthCalls = 0;
    server.use(
      authenticated(),
      http.get("*/v1/operations/health", () => {
        healthCalls += 1;
        return HttpResponse.json(HEALTH);
      }),
    );
    renderApp("/operations");

    await screen.findByRole("table", { name: "Health checks" });
    const before = healthCalls;
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(healthCalls).toBe(before + 1));

    const audit = await screen.findByRole("table", { name: "Audit trail" });
    expect(within(audit).getByText("campaign.create")).toBeInTheDocument();
    expect(within(audit).getByText("campaign:cmp_1")).toBeInTheDocument();
    expect(within(audit).getByText("session.login")).toBeInTheDocument();

    const link = screen.getByRole("link", { name: "Workflow engine (low-level)" });
    expect(link).toHaveAttribute("href", "http://127.0.0.1:8233");
  });

  it("still bounces non-owners before anything loads", async () => {
    server.use(authenticated(makeSession({ is_owner: false })));
    const { router } = renderApp("/operations");
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("has no axe violations", async () => {
    server.use(authenticated());
    const { container } = renderApp("/operations");
    await screen.findByRole("table", { name: "Health checks" });
    await screen.findByRole("table", { name: "Audit trail" });
    const results = await axe.run(container, AXE_OPTS);
    expect(results.violations).toEqual([]);
  });
});
