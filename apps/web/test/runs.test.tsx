import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { authenticated } from "./msw/handlers";
import { makeRunDetail, RUN_LIST, RUN_NODES } from "./msw/runs";
import { server } from "./msw/server";
import { renderApp } from "./render";

describe("run list", () => {
  it("renders every run with its state and links to the detail view", async () => {
    server.use(authenticated(), http.get("*/v1/runs", () => HttpResponse.json(RUN_LIST)));
    renderApp("/projects");

    const table = await screen.findByRole("table", { name: "Pipeline runs" });
    const rows = within(table).getAllByRole("row").slice(1); // skip header
    expect(rows).toHaveLength(RUN_LIST.length);
    expect(within(table).getByRole("link", { name: "run_1" })).toBeInTheDocument();
    expect(within(table).getByText("Producing")).toBeInTheDocument();
    expect(within(table).getByText("Waiting for approval")).toBeInTheDocument();
    expect(within(table).getByText("Complete")).toBeInTheDocument();
    expect(within(table).getByText("Failed")).toBeInTheDocument();
  });

  it("starts a run with the picked quality and navigates to it", async () => {
    const user = userEvent.setup();
    const startBodies: unknown[] = [];
    server.use(
      authenticated(),
      http.get("*/v1/runs", () => HttpResponse.json([])),
      http.post("*/v1/runs", async ({ request }) => {
        startBodies.push(await request.json());
        return HttpResponse.json({ run_id: "run_9" }, { status: 202 });
      }),
      http.get("*/v1/runs/run_9", () => HttpResponse.json(makeRunDetail({ run_id: "run_9", state: "CREATED", quality: "smoke" }))),
    );
    const { router } = renderApp("/projects");

    // The default quality is demo; the button says so, then follows the picker.
    expect(await screen.findByRole("button", { name: "Start demo run" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Quality"), "smoke");
    await user.click(screen.getByRole("button", { name: "Start smoke run" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/run_9"));
    expect(startBodies).toEqual([{ quality: "smoke", campaign: "fixture" }]);
    expect(await screen.findByRole("heading", { name: "Run run_9" })).toBeInTheDocument();
  });
});

describe("run detail", () => {
  it("renders the plain-language state, a canvas node per API node, and the step list", async () => {
    server.use(authenticated(), http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail())));
    renderApp("/projects/run_1");

    expect(await screen.findByRole("heading", { name: "Run run_1" })).toBeInTheDocument();
    expect(screen.getByText("The factory is producing content right now.")).toBeInTheDocument();

    // The graph canvas renders one node per API node.
    const canvas = screen.getByRole("region", { name: "Pipeline graph" });
    await waitFor(() => expect(canvas.querySelectorAll(".cf-runnode")).toHaveLength(RUN_NODES.length));

    // The parallel plain list mirrors it.
    const list = screen.getByRole("list", { name: "Pipeline steps" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(RUN_NODES.length);
    expect(within(list).getByText("cached")).toBeInTheDocument();
    expect(within(list).getByText("voice model unavailable")).toBeInTheDocument();
  });

  it("opens the node inspector when a step is selected and closes it again", async () => {
    const user = userEvent.setup();
    server.use(authenticated(), http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail())));
    renderApp("/projects/run_1");

    const list = within(await screen.findByRole("list", { name: "Pipeline steps" }));
    expect(screen.queryByRole("complementary", { name: "Node details" })).not.toBeInTheDocument();

    // Select the failed script:d2 node.
    await user.click(list.getAllByRole("button")[4]!);
    const inspector = await screen.findByRole("complementary", { name: "Node details" });
    expect(within(inspector).getByRole("heading", { name: "script" })).toBeInTheDocument();
    const facts = within(inspector);
    expect(facts.getByText("Deliverable").nextElementSibling).toHaveTextContent("d2");
    expect(facts.getByText("Attempts").nextElementSibling).toHaveTextContent("1");
    expect(facts.getByText("Duration").nextElementSibling).toHaveTextContent("12 s");
    expect(facts.getByText("Cache").nextElementSibling).toHaveTextContent("Computed fresh");
    expect(facts.getByText("Error").nextElementSibling).toHaveTextContent("voice model unavailable");

    await user.click(facts.getByRole("button", { name: "Close details" }));
    expect(screen.queryByRole("complementary", { name: "Node details" })).not.toBeInTheDocument();
  });

  it("selects nodes from the canvas with the keyboard", async () => {
    const user = userEvent.setup();
    server.use(authenticated(), http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail())));
    renderApp("/projects/run_1");

    const canvas = await screen.findByRole("region", { name: "Pipeline graph" });
    await waitFor(() => expect(canvas.querySelectorAll('.cf-runnode[role="button"]')).toHaveLength(RUN_NODES.length));
    const researchNode = within(canvas).getByRole("button", { name: /research, shared, complete/ });
    researchNode.focus();
    await user.keyboard("{Enter}");
    const inspector = await screen.findByRole("complementary", { name: "Node details" });
    expect(within(inspector).getByText("Cache").nextElementSibling).toHaveTextContent("Served from cache");
  });
});
