import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { formatEstimates, parseCardCount } from "../src/pages/CreatePage";
import { authenticated } from "./msw/handlers";
import { makeRunDetail } from "./msw/runs";
import { server } from "./msw/server";
import { renderApp } from "./render";

const AXE_OPTS = { rules: { "color-contrast": { enabled: false } } };

async function fillStepOne(user: ReturnType<typeof userEvent.setup>, topic = "Ship the audio engine") {
  await screen.findByRole("heading", { name: "What is this campaign about?" });
  await user.type(screen.getByLabelText("Topic"), topic);
  await user.type(screen.getByLabelText("What should it achieve?"), "Explain what changed to newcomers");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("table", { name: "Content formats this build can make" });
}

describe("create flow", () => {
  it("walks topic → matrix → preview → create and lands on the new run", async () => {
    const user = userEvent.setup();
    const createBodies: unknown[] = [];
    server.use(
      authenticated(),
      http.post("*/v1/campaigns", async ({ request }) => {
        createBodies.push(await request.json());
        return HttpResponse.json({ run_id: "run_new1", campaign_id: "cmp_new1" }, { status: 202 });
      }),
      http.get("*/v1/runs/run_new1", () => HttpResponse.json(makeRunDetail({ run_id: "run_new1", state: "CREATED" }))),
    );
    const { router } = renderApp("/create");

    await fillStepOne(user);

    // Supported rows are selectable; unsupported rows are disabled with the reason in the row.
    expect(screen.getByRole("checkbox", { name: "Single image post" })).toBeEnabled();
    const thread = screen.getByRole("checkbox", { name: "Thread" });
    expect(thread).toBeDisabled();
    expect(screen.getByText("thread splitting arrives with destination packaging (phase 9)")).toBeInTheDocument();
    // Destinations are honest: package-only export.
    expect(screen.getAllByText("export (package only)").length).toBeGreaterThan(0);

    // Select image + carousel; the title prefills from the topic.
    await user.click(screen.getByRole("checkbox", { name: "Single image post" }));
    expect(screen.getByLabelText("Title for single image post")).toHaveValue("Ship the audio engine");
    await user.click(screen.getByRole("checkbox", { name: "Carousel" }));
    const cards = screen.getByLabelText("Cards (2–20)");
    await user.clear(cards);
    await user.type(cards, "7");
    await user.click(screen.getByRole("button", { name: "Continue with 2 formats" }));

    // Preview: estimates, shared stages, per-deliverable chains, pruned with reasons.
    await screen.findByRole("heading", { name: "Review the plan" });
    expect(await screen.findByText("$0.00 external — everything renders locally")).toBeInTheDocument();
    const shared = screen.getByRole("region", { name: "Shared stages" });
    expect(within(shared).getByText("research")).toBeInTheDocument();
    expect(within(shared).getByText("plan")).toBeInTheDocument();
    const image = screen.getByRole("region", { name: "Single image post — Ship the audio engine" });
    expect(within(image).getByText("render")).toBeInTheDocument();
    const pruned = screen.getByRole("region", { name: "Skipped (with reasons)" });
    expect(within(pruned).getByText(/no uploads or URLs to ingest/)).toBeInTheDocument();
    expect(screen.getByText(/offline fixtures/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create & run" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/projects/run_new1"));
    expect(createBodies).toEqual([
      {
        topic: "Ship the audio engine",
        objective: "Explain what changed to newcomers",
        quality: "demo",
        deliverables: [
          { type: "single_image_post", title: "Ship the audio engine" },
          { type: "carousel", title: "Ship the audio engine", card_count: 7 },
        ],
      },
    ]);
    expect(await screen.findByRole("heading", { name: "Run run_new1" })).toBeInTheDocument();
  });

  it("shows a 422 from preview and highlights the offending selection on the way back", async () => {
    const user = userEvent.setup();
    server.use(
      authenticated(),
      http.post("*/v1/campaigns/preview", () => HttpResponse.json({ detail: "carousel: per-card render cache is not available in this build" }, { status: 422 })),
    );
    renderApp("/create");

    await fillStepOne(user);
    await user.click(screen.getByRole("checkbox", { name: "Carousel" }));
    await user.click(screen.getByRole("button", { name: "Continue with 1 format" }));

    // The preview refusal is shown in plain language, and both run buttons stay disabled.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("carousel: per-card render cache is not available in this build");
    expect(screen.getByRole("button", { name: "Create & run" })).toBeDisabled();

    // Going back highlights the carousel row.
    await user.click(screen.getByRole("button", { name: "Back to fix the selection" }));
    await screen.findByRole("table", { name: "Content formats this build can make" });
    expect(screen.getByRole("alert")).toHaveTextContent("The preview refused this selection");
  });

  it("step 2 has no axe violations", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    const { container } = renderApp("/create");
    await fillStepOne(user);
    await user.click(screen.getByRole("checkbox", { name: "Carousel" }));
    const results = await axe.run(container, AXE_OPTS);
    expect(results.violations).toEqual([]);
  });
});

describe("create flow helpers", () => {
  it("parseCardCount clamps to the API's 2–20 and defaults to 5", () => {
    expect(parseCardCount("7")).toBe(7);
    expect(parseCardCount("")).toBe(5);
    expect(parseCardCount("nope")).toBe(5);
    expect(parseCardCount("1")).toBe(2);
    expect(parseCardCount("99")).toBe(20);
  });

  it("formatEstimates is honest about zero-cost local runs and non-zero costs", () => {
    expect(formatEstimates({ external_cost_usd: 0, external_calls: 0, local_render: true })).toBe("$0.00 external — everything renders locally");
    expect(formatEstimates({ external_cost_usd: 1.5, external_calls: 3, local_render: true })).toBe("$1.50 external across 3 external calls — rendering stays local");
  });
});
