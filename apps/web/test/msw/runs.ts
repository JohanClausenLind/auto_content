import type { RunNode } from "@content-factory/pipeline-canvas";
import type { ActionItem, RunDetail, RunSummary } from "../../src/api/types";

export function makeNode(overrides: Partial<RunNode> & Pick<RunNode, "node_id" | "stage">): RunNode {
  return {
    deliverable_id: null,
    state: "queued",
    attempts: 1,
    cache_hit: false,
    duration_ms: null,
    error: null,
    ...overrides,
  };
}

export const RUN_NODES: RunNode[] = [
  makeNode({ node_id: "research", stage: "research", state: "complete", cache_hit: true, duration_ms: 850 }),
  makeNode({ node_id: "plan", stage: "plan", state: "complete", duration_ms: 1900 }),
  makeNode({ node_id: "script:d1", stage: "script", deliverable_id: "d1", state: "running", attempts: 2 }),
  makeNode({ node_id: "render:d1", stage: "render", deliverable_id: "d1", state: "queued" }),
  makeNode({ node_id: "script:d2", stage: "script", deliverable_id: "d2", state: "failed", error: "voice model unavailable", duration_ms: 12_000 }),
];

export function makeRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: "run_1",
    state: "PRODUCING",
    campaign_id: "camp_fixture",
    project_id: "proj_1",
    quality: "demo",
    created_at: "2026-08-30T10:00:00Z",
    ...overrides,
  };
}

export function makeRunDetail(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    ...makeRun(),
    edges: null,
  preflight_revision_hash: "rev_abc123",
    approved_by: null,
    error: null,
    report: null,
    nodes: RUN_NODES,
    ...overrides,
  };
}

export const RUN_LIST: RunSummary[] = [
  makeRun({ run_id: "run_1", state: "PRODUCING" }),
  makeRun({ run_id: "run_2", state: "WAITING_FOR_APPROVAL", quality: "smoke", created_at: "2026-08-31T08:00:00Z" }),
  makeRun({ run_id: "run_3", state: "COMPLETE", created_at: "2026-08-29T12:00:00Z" }),
  makeRun({ run_id: "run_4", state: "FAILED", created_at: "2026-08-28T09:30:00Z" }),
];

export function makeActionItem(overrides: Partial<ActionItem> = {}): ActionItem {
  return {
    id: "ai_1",
    kind: "approval_needed",
    severity: "warning",
    title: "A run is waiting for your approval",
    body: "run_2 finished preflight and needs a decision before producing.",
    run_id: "run_2",
    deep_link: "/projects/run_2",
    created_at: "2026-08-31T08:05:00Z",
    ...overrides,
  };
}
