import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import stageSchema from "@content-factory/content-schema-ts/schema/DeliverableDAG.schema.json";
import { parseGraph } from "@content-factory/node-graph";
import { WORKSPACE_DEFS, workspaceCatalog } from "../src/workspace/catalog";
import { starterGraph } from "../src/workspace/storage";
import { authenticated } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

beforeEach(() => {
  window.localStorage.removeItem("cf.workspace.graphs.v1");
  window.localStorage.removeItem("cf.workspace.active.v1");
});

describe("workspace catalog", () => {
  it("covers every pipeline stage from the generated contract", () => {
    const stages = (stageSchema as { $defs: { Stage: { enum: string[] } } }).$defs.Stage.enum;
    for (const stage of stages) {
      expect(workspaceCatalog.get(stage), `stage ${stage} has no node definition`).toBeDefined();
    }
    // and nothing in the catalogue claims to be a stage that does not exist
    for (const def of WORKSPACE_DEFS) {
      if (def.stage) expect(stages).toContain(def.stage);
    }
  });

  it("ships a starter graph that is valid and serialisable", () => {
    const graph = starterGraph();
    expect(graph.nodes.length).toBeGreaterThan(5);
    expect(parseGraph(JSON.parse(JSON.stringify(graph)))).toEqual(graph);
  });
});

describe("workspace page", () => {
  it("renders the canvas, node library and workflow overview", async () => {
    server.use(authenticated());
    renderApp("/workspace");
    const canvas = await screen.findByRole("application", { name: /Graph: Video from brief/ });
    expect(screen.getByLabelText("Node library")).toBeInTheDocument();
    expect(screen.getByLabelText("Workflow overview")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Video from brief/ })).toBeInTheDocument();
    // starter nodes are on the canvas
    expect(await within(canvas).findByText("Campaign Brief")).toBeInTheDocument();
    expect(within(canvas).getByText("Generate Anchor")).toBeInTheDocument();
  });

  it("adds a node from the library and persists it", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/workspace");
    await screen.findAllByText("Campaign Brief");
    await user.type(screen.getByLabelText("Search node library"), "narration");
    await user.click(await screen.findByRole("button", { name: /Synthesize Narration/ }));
    await waitFor(() => {
      const stored = JSON.parse(window.localStorage.getItem("cf.workspace.graphs.v1") ?? "[]") as {
        nodes: { type: string }[];
      }[];
      expect(stored[0]?.nodes.some((n) => n.type === "synthesize_narration")).toBe(true);
    });
  });

  it("check graph validates and stays honest about execution", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/workspace");
    await screen.findAllByText("Campaign Brief");
    await user.click(screen.getByRole("button", { name: "Check graph" }));
    const result = await screen.findByRole("region", { name: "Graph check result" });
    expect(within(result).getByText(/Graph is valid|problem/)).toBeInTheDocument();
    expect(within(result).getByText(/Run compiles the graph onto the production pipeline/)).toBeInTheDocument();
  });

  it("opens a second graph tab and switches between graphs", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/workspace");
    await screen.findAllByText("Campaign Brief");
    await user.click(screen.getByRole("button", { name: "New graph" }));
    expect(await screen.findByRole("tab", { name: /Untitled graph/ })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: /Video from brief/ }));
    expect((await screen.findAllByText("Campaign Brief")).length).toBeGreaterThan(0);
  });

  it("job queue lists real runs from the API", async () => {
    const user = userEvent.setup();
    server.use(
      authenticated(),
      http.get("*/v1/runs", () =>
        HttpResponse.json([
          {
            run_id: "run_1",
            state: "PRODUCING",
            campaign_id: "camp_1",
            project_id: "proj_1",
            quality: "demo",
            created_at: "2026-09-01T10:00:00Z",
          },
        ]),
      ),
    );
    renderApp("/workspace");
    await screen.findAllByText("Campaign Brief");
    await user.click(await screen.findByRole("button", { name: /1 active/ }));
    const queue = await screen.findByRole("region", { name: "Job queue" });
    expect(within(queue).getByText(/camp_1/)).toBeInTheDocument();
    expect(within(queue).getByText("producing")).toBeInTheDocument();
  });
});

describe("workspace run", () => {
  it("Run saves the graph to the server and starts a production run", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/workspace");
    await screen.findAllByText("Campaign Brief");
    await user.click(screen.getByRole("button", { name: /Run/ }));
    const result = await screen.findByRole("region", { name: "Graph check result" });
    expect(await within(result).findByText(/started \(4 stages, short_video\)/)).toBeInTheDocument();
    expect(within(result).getByRole("link", { name: "run-msw000001" })).toBeInTheDocument();
    const { graphRunPosts, graphStore } = await import("./msw/handlers");
    expect(graphRunPosts).toHaveLength(1);
    expect(graphStore.size).toBeGreaterThan(0); // the graph was flushed to the server first
  });

  it("graphs persist to the server as they change", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/workspace");
    const canvas = await screen.findByRole("application", { name: /Graph:/ });
    const note = await within(canvas).findByLabelText("Note for Campaign Brief");
    await user.type(note, "hello");
    const { graphStore } = await import("./msw/handlers");
    await waitFor(
      () => {
        const doc = [...graphStore.values()][0] as { nodes?: { note: string }[] } | undefined;
        expect(doc?.nodes?.some((n) => n.note === "hello")).toBe(true);
      },
      { timeout: 4000 },
    );
  });
});

describe("workspace run refusals", () => {
  it("surfaces the server's per-node reasons when a run is refused", async () => {
    const user = userEvent.setup();
    server.use(
      authenticated(),
      http.post("*/v1/graphs/:id/runs", () =>
        HttpResponse.json(
          {
            detail: {
              message: "graph does not compile",
              ok: false,
              problems: ["Generate Anchor: no executor for this stage yet"],
              dispositions: [],
              deliverable_type: null,
              dag_nodes: 0,
            },
          },
          { status: 422 },
        ),
      ),
    );
    renderApp("/workspace");
    await screen.findAllByText("Campaign Brief");
    await user.click(screen.getByRole("button", { name: /Run/ }));
    const result = await screen.findByRole("region", { name: "Graph check result" });
    expect(await within(result).findByText("Run refused")).toBeInTheDocument();
    expect(within(result).getByText(/no executor for this stage yet/)).toBeInTheDocument();
  });
});
