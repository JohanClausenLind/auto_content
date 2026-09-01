import { http, HttpResponse } from "msw";
import type { AuditEvent, CampaignBody, CampaignPreview, DagNode, DeliverableMatrix, OperationsHealth, PrunedStage } from "../../src/api/types";

/** Mirrors GET /v1/campaigns/matrix from the control plane (same rows and reasons). */
export const MATRIX: DeliverableMatrix = {
  rows: [
    { type: "long_video", supported: true, reason: "audio + video branches are implemented (mock narration in this build)", destinations: ["export"] },
    { type: "short_video", supported: true, reason: "audio + video branches are implemented (mock narration in this build)", destinations: ["export"] },
    { type: "single_image_post", supported: true, reason: "static branch (artboard → PNG) is implemented", destinations: ["export"] },
    { type: "carousel", supported: true, reason: "per-card render cache is implemented", destinations: ["export"] },
    { type: "infographic", supported: true, reason: "static branch is implemented", destinations: ["export"] },
    { type: "text_post", supported: true, reason: "text package branch is implemented", destinations: ["export"] },
    { type: "thread", supported: false, reason: "thread splitting arrives with destination packaging (phase 9)", destinations: ["export"] },
    { type: "article", supported: false, reason: "the article branch (draft, link check, SEO export) is not wired into the pipeline yet", destinations: ["export"] },
    { type: "newsletter", supported: false, reason: "the email branch (MJML compile, client previews) is not wired yet", destinations: ["export"] },
    { type: "email_campaign", supported: false, reason: "the email branch is not wired yet", destinations: ["export"] },
    { type: "audio_clip", supported: false, reason: "standalone audio packaging is not wired yet", destinations: ["export"] },
    { type: "audiogram", supported: false, reason: "audiogram compositions are not wired yet", destinations: ["export"] },
    { type: "cover", supported: true, reason: "static branch is implemented", destinations: ["export"] },
    { type: "image_sequence", supported: false, reason: "the sequence engine ships; pipeline wiring arrives with the anchor workflow", destinations: ["export"] },
  ],
  notes: ["Research uses offline fixtures in this build.", "All output is package-only export; publishing arrives with phase 9 destinations."],
};

/** Deterministic preview: shared chain, per-deliverable chains, honest pruned stages. */
export function makePreview(body: CampaignBody): CampaignPreview {
  const deliverables = body.deliverables.map((d, i) => ({ deliverable_id: `dlv_${i + 1}`, type: d.type, title: d.title }));
  const nodes: DagNode[] = [
    { node_id: "research", stage: "research", deliverable_id: null, depends_on: [] },
    { node_id: "plan", stage: "plan", deliverable_id: null, depends_on: ["research"] },
  ];
  const pruned: PrunedStage[] = [{ stage: "ingest", deliverable_id: null, reason: "no uploads or URLs to ingest" }];
  for (const d of deliverables) {
    nodes.push({ node_id: `script:${d.deliverable_id}`, stage: "script", deliverable_id: d.deliverable_id, depends_on: ["plan"] });
    nodes.push({ node_id: `render:${d.deliverable_id}`, stage: "render", deliverable_id: d.deliverable_id, depends_on: [`script:${d.deliverable_id}`] });
    nodes.push({
      node_id: `compile_destination_packages:${d.deliverable_id}`,
      stage: "compile_destination_packages",
      deliverable_id: d.deliverable_id,
      depends_on: [`render:${d.deliverable_id}`],
    });
    if (d.type !== "short_video" && d.type !== "long_video") {
      pruned.push({ stage: "synthesize_narration", deliverable_id: d.deliverable_id, reason: "static deliverables have no narration" });
    }
  }
  return {
    campaign: { campaign_id: "cmp_test0001", deliverables },
    dag: { nodes, pruned },
    estimates: { external_cost_usd: 0, external_calls: 0, local_render: true },
    notes: ["Research uses offline fixtures in this build; narration uses the deterministic mock voice."],
  };
}

export const HEALTH: OperationsHealth = {
  ok: false,
  checks: [
    { name: "database", status: "ok", detail: "postgres reachable, migrations applied", fix: null },
    { name: "temporal", status: "ok", detail: "workflow engine reachable at 127.0.0.1:7233", fix: null },
    { name: "renderer", status: "warn", detail: "remotion bundle is older than the schema package", fix: "run just render-smoke to rebuild" },
    { name: "web push", status: "fail", detail: "VAPID keys are not configured", fix: "run ./setup.sh to generate keys" },
    { name: "gpu", status: "skip", detail: "no GPU in this environment", fix: null },
  ],
};

export const AUDIT: AuditEvent[] = [
  { id: "aud_1", action: "campaign.create", actor: "acct_1", workspace_id: "ws_1", target: "campaign:cmp_1", created_at: "2026-08-31T09:00:00Z" },
  { id: "aud_2", action: "run.approve", actor: "acct_1", workspace_id: "ws_1", target: "run:run_2", created_at: "2026-08-31T08:10:00Z" },
  { id: "aud_3", action: "session.login", actor: "acct_1", workspace_id: null, target: null, created_at: "2026-08-31T08:00:00Z" },
];

/** Defaults for the product endpoints; individual tests override what they need. */
export const productHandlers = [
  http.get("*/v1/campaigns/matrix", () => HttpResponse.json(MATRIX)),
  http.post("*/v1/campaigns/preview", async ({ request }) => HttpResponse.json(makePreview((await request.json()) as CampaignBody))),
  http.post("*/v1/campaigns", () => HttpResponse.json({ run_id: "run_new1", campaign_id: "cmp_new1" }, { status: 202 })),
  http.get("*/v1/operations/health", () => HttpResponse.json(HEALTH)),
  http.get("*/v1/operations/audit", () => HttpResponse.json(AUDIT)),
  // Web push is unconfigured by default, exactly like a fresh local build.
  http.get("*/v1/notifications/vapid-public-key", () => HttpResponse.json({ detail: "web push is not configured (run setup)" }, { status: 503 })),
  http.post("*/v1/notifications/subscriptions", () => HttpResponse.json({ id: "push_1" }, { status: 201 })),
  http.delete("*/v1/notifications/subscriptions", () => new HttpResponse(null, { status: 204 })),
  http.post("*/v1/notifications/test", () => HttpResponse.json({ sent: 1, total: 1 })),
];
