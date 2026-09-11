import type { RunNode } from "@content-factory/pipeline-canvas";
import type { ActionItem, HistoryRun, HistoryRunDetail, RunDetail, RunEta, RunReviewPage, RunSummary } from "../../src/api/types";

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
  // The unfinished nodes carry an estimate and the finished ones do not — a finished node has a
  // measured `duration_ms`, and the server sends no estimate to compete with it.
  makeNode({ node_id: "script:d1", stage: "script", deliverable_id: "d1", state: "running", attempts: 2, eta_seconds: 45, eta_samples: 12 }),
  makeNode({ node_id: "render:d1", stage: "render", deliverable_id: "d1", state: "queued", eta_seconds: 610, eta_samples: 4 }),
  makeNode({ node_id: "script:d2", stage: "script", deliverable_id: "d2", state: "failed", error: "voice model unavailable", duration_ms: 12_000 }),
];

export function makeEta(overrides: Partial<RunEta> = {}): RunEta {
  return {
    remaining_seconds: 655,
    finish_at: "2026-08-30T10:11:00Z",
    samples: 4,
    confident: true,
    overdue: false,
    unknown_stages: [],
    ...overrides,
  };
}

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
    eta: makeEta(),
    nodes: RUN_NODES,
    ...overrides,
  };
}

// --- local run history ---
//
// Three runs shaped like the real ones: a finished picture story with a film, one parked at a
// human review gate with its drawings done, and one that actually broke.

export function makeHistoryRun(overrides: Partial<HistoryRun> & Pick<HistoryRun, "run_id">): HistoryRun {
  return {
    workflow: "audio-picture-story",
    outcome: "complete",
    finished_at: 1_757_520_000,
    seconds: 228.1,
    stages: 12,
    stages_ok: 12,
    blocked_at: null,
    project_dir: `/home/vega/git/auto_content/output/${overrides.run_id.replaceAll("~", "/")}`,
    deliverable_id: "dlv_short0000001",
    outputs_total: 4,
    awaiting_review: 0,
    ...overrides,
  };
}

export const HISTORY_RUNS: HistoryRun[] = [
  makeHistoryRun({ run_id: "overnight~ps1c-pinecone" }),
  makeHistoryRun({ run_id: "overnight~ps2c-amber", outcome: "review", stages_ok: 5, stages: 6, seconds: 203.7, awaiting_review: 2 }),
  makeHistoryRun({ run_id: "local-runs~single-image", workflow: "single-image", outcome: "failed", stages_ok: 1, stages: 4, seconds: 56.7, outputs_total: 2 }),
];

export const HISTORY_DETAIL: HistoryRunDetail = {
  ...HISTORY_RUNS[0]!,
  film: "deliverables/dlv_short0000001/exports/final.mp4",
  poster: "deliverables/dlv_short0000001/anchors/frames/0000.png",
  outputs: [
    { path: "deliverables/dlv_short0000001/exports/final.mp4", kind: "video", role: "video", bytes: 1_870_876, content_type: "video/mp4" },
    { path: "deliverables/dlv_short0000001/anchors/frames/0000.png", kind: "image", role: "anchor", bytes: 812_000, content_type: "image/png" },
    { path: "deliverables/dlv_short0000001/audio/narration-mastered.wav", kind: "audio", role: "audio", bytes: 2_879_118, content_type: "audio/wav" },
    { path: "deliverables/dlv_short0000001/captions/captions.srt", kind: "text", role: "caption", bytes: 797, content_type: "text/plain" },
    { path: "deliverables/dlv_short0000001/controls/shot_a/pose_skeleton/frames/0000.png", kind: "image", role: "control", bytes: 40_000, content_type: "image/png" },
  ],
};

/** The frame-review gate of the run parked at it: two drawings, one measurement flagged.
 *
 * Shaped like the real thing — ids as `review_frames` writes them, each frame resolved to a file
 * under the run directory, and a resume command, because a verdict unblocks the gate and makes
 * nothing. */
export const REVIEW_PAGE: RunReviewPage = {
  run_id: "overnight~ps2c-amber",
  workflow: "audio-picture-story",
  project_dir: "/home/vega/git/auto_content/output/overnight/ps2c-amber",
  resume_command:
    "content-factory run-local audio-picture-story --project-dir /home/vega/git/auto_content/output/overnight/ps2c-amber --from review_frames",
  reviews: [
    {
      deliverable: "dlv_short0000001",
      deliverable_id: "dlv_short0000001",
      contact_sheet: "deliverables/dlv_short0000001/reviews/frames/contact-sheet.png",
      reviewer: null,
      reviewed_at: null,
      notes: "",
      passed: false,
      unreviewed: 2,
      rejected: 0,
      accepted: 0,
      flagged: 1,
      frames: [
        {
          frame_id: "shot_9ff49b91f418:0000",
          verdict: "unreviewed",
          reason: "",
          image: "deliverables/dlv_short0000001/anchors/shot_9ff49b91f418/0000.png",
          png_sha256: "a".repeat(64),
          on_disk: true,
          blocked: false,
          findings: [
            { check: "midtone_range", passed: true, severity: "advisory", detail: "82% of pixels are midtones (under 30% is a crushed frame)", measured: 0.82, threshold: 0.3 },
          ],
        },
        {
          frame_id: "shot_d3f50497205c:0000",
          verdict: "unreviewed",
          reason: "",
          image: "deliverables/dlv_short0000001/anchors/shot_d3f50497205c/0000.png",
          png_sha256: "b".repeat(64),
          on_disk: true,
          blocked: false,
          findings: [
            { check: "colour_present", passed: false, severity: "advisory", detail: "a colour brief came back grey (saturation 0.011)", measured: 0.011, threshold: 0.06 },
          ],
        },
      ],
    },
  ],
};

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
