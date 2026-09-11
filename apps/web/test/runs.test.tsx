import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { authenticated } from "./msw/handlers";
import { makeEta, makeRunDetail, RUN_LIST, RUN_NODES } from "./msw/runs";
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

  it("says when the run will be done, and what that estimate is made of", async () => {
    server.use(authenticated(), http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail())));
    renderApp("/projects/run_1");

    // 655 s of work left, reported coarsely, plus the clock time it lands at. The query polls
    // every two seconds, so both move as the run progresses.
    const eta = await screen.findByText(/~11 min left/);
    expect(eta.parentElement).toHaveTextContent("done about");
    // How much history is behind it, because a median of four runs and a median of one are not
    // the same claim and the number alone cannot tell them apart.
    expect(eta.parentElement).toHaveTextContent("from 4 past runs");
  });

  it("names the stages it has never timed instead of counting them as free", async () => {
    server.use(
      authenticated(),
      http.get("*/v1/runs/run_1", () =>
        HttpResponse.json(makeRunDetail({ eta: makeEta({ confident: false, samples: 1, unknown_stages: ["master_audio", "burn_captions"] }) })),
      ),
    );
    renderApp("/projects/run_1");
    expect(await screen.findByText(/2 stage\(s\) never timed/)).toHaveTextContent("master_audio, burn_captions");
    expect(screen.getByText(/from 1 past run$/)).toBeInTheDocument();  // singular
  });

  it("says a stage is running long rather than promising a finish that has passed", async () => {
    server.use(
      authenticated(),
      http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail({ eta: makeEta({ remaining_seconds: 0, overdue: true }) }))),
    );
    renderApp("/projects/run_1");
    expect(await screen.findByText("Running longer than usual")).toBeInTheDocument();
    expect(screen.queryByText(/done about/)).not.toBeInTheDocument();
  });

  it("says finishing now rather than \"~any moment left\" when the work is down to seconds", async () => {
    server.use(
      authenticated(),
      http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail({ eta: makeEta({ remaining_seconds: 0.4 }) }))),
    );
    renderApp("/projects/run_1");
    expect(await screen.findByText("Finishing now")).toBeInTheDocument();
    // No clock time either: "done about 14:32" for something finishing this second is noise.
    expect(screen.queryByText(/done about/)).not.toBeInTheDocument();
  });

  it("shows no estimate at all once the run has nothing left to wait for", async () => {
    server.use(authenticated(), http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail({ state: "COMPLETE", eta: null }))));
    renderApp("/projects/run_1");
    expect(await screen.findByRole("heading", { name: "Run run_1" })).toBeInTheDocument();
    expect(screen.queryByText(/left/)).not.toBeInTheDocument();
  });

  it("gives the inspector the node's own remaining time, and a finished node none", async () => {
    const user = userEvent.setup();
    server.use(authenticated(), http.get("*/v1/runs/run_1", () => HttpResponse.json(makeRunDetail())));
    renderApp("/projects/run_1");

    const list = within(await screen.findByRole("list", { name: "Pipeline steps" }));
    await user.click(list.getAllByRole("button")[2]!);  // the running script:d1
    let inspector = within(await screen.findByRole("complementary", { name: "Node details" }));
    expect(inspector.getByText("Still to go").nextElementSibling).toHaveTextContent("~45 s");
    expect(inspector.getByText("Still to go").nextElementSibling).toHaveTextContent("median of 12");

    await user.click(list.getAllByRole("button")[0]!);  // research, complete and measured
    inspector = within(await screen.findByRole("complementary", { name: "Node details" }));
    expect(inspector.getByText("Duration").nextElementSibling).toHaveTextContent("850 ms");
    expect(inspector.queryByText("Still to go")).not.toBeInTheDocument();
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
