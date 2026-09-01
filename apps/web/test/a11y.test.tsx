import { screen } from "@testing-library/react";
import axe from "axe-core";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { authenticated } from "./msw/handlers";
import { RUN_LIST } from "./msw/runs";
import { server } from "./msw/server";
import { renderApp } from "./render";

const AXE_OPTS = { rules: { "color-contrast": { enabled: false } } };

describe("accessibility", () => {
  it("shell has no axe violations", async () => {
    server.use(authenticated());
    const { container } = renderApp("/");
    await screen.findByRole("navigation", { name: "Areas" });
    const results = await axe.run(container, AXE_OPTS);
    expect(results.violations).toEqual([]);
  });

  it("projects page has no axe violations", async () => {
    server.use(authenticated(), http.get("*/v1/runs", () => HttpResponse.json(RUN_LIST)));
    const { container } = renderApp("/projects");
    await screen.findByRole("table", { name: "Pipeline runs" });
    const results = await axe.run(container, AXE_OPTS);
    expect(results.violations).toEqual([]);
  });

  it("login page has no axe violations", async () => {
    const { container } = renderApp("/login");
    await screen.findByRole("heading", { name: "Sign in to Content Factory" });
    const results = await axe.run(container, AXE_OPTS);
    expect(results.violations).toEqual([]);
  });
});
