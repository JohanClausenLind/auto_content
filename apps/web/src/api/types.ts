import type { RunNode } from "@content-factory/pipeline-canvas";
import type { ThemeState } from "@content-factory/web-ui";

export interface Account {
  id: string;
  username: string;
  display_name: string;
  is_owner: boolean;
}

export type WorkspaceRole = "owner" | "editor" | "viewer" | (string & {});

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  role: WorkspaceRole;
}

export interface Session {
  account: Account;
  workspaces: Workspace[];
  current_workspace_id: string | null;
  step_up_until: string | null;
}

export interface MfaRequired {
  mfa_required: true;
  methods: ("totp" | "passkey")[];
}

export type LoginResult = { kind: "session"; session: Session } | { kind: "mfa"; methods: MfaRequired["methods"] };

export interface PasskeyOptions {
  options: unknown;
  challenge_id: string;
}

export interface Meta {
  version: string;
  environment: string;
  distribution_enabled: boolean;
  kill_switch: boolean;
}

export interface ThemePrefs {
  value: ThemeState | null;
}

// --- Pipeline runs ---

export type RunState = "CREATED" | "PREFLIGHTING" | "WAITING_FOR_APPROVAL" | "APPROVED" | "PRODUCING" | "COMPLETE" | "BLOCKED" | "FAILED" | "CANCELLED";

export type RunQuality = "smoke" | "demo";

export interface RunSummary {
  run_id: string;
  state: RunState;
  campaign_id: string;
  project_id: string;
  quality: RunQuality | (string & {});
  created_at: string;
}

export interface RunDetail extends RunSummary {
  preflight_revision_hash: string | null;
  approved_by: string | null;
  error: string | null;
  report: unknown;
  nodes: RunNode[];
}

export type ApprovalDecision = "approve" | "reject";

export interface ApprovalRequest {
  revision_hash: string;
  decision: ApprovalDecision;
  reason?: string;
}

// --- Action Center ---

export type ActionItemSeverity = "info" | "warning" | "critical" | (string & {});

export interface ActionItem {
  id: string;
  kind: string;
  severity: ActionItemSeverity;
  title: string;
  body: string;
  run_id: string | null;
  deep_link: string;
  created_at: string;
}

// --- Campaigns (Create flow) ---

export interface MatrixRow {
  type: string;
  supported: boolean;
  /** Plain-language: why it works (supported) or why it doesn't (unsupported). */
  reason: string;
  destinations: string[];
}

export interface DeliverableMatrix {
  rows: MatrixRow[];
  notes: string[];
}

export interface DeliverableChoice {
  type: string;
  title: string;
  card_count?: number;
}

export interface CampaignBody {
  topic: string;
  objective: string;
  deliverables: DeliverableChoice[];
  quality: RunQuality;
}

export interface DagNode {
  node_id: string;
  stage: string;
  deliverable_id: string | null;
  depends_on: string[];
}

export interface PrunedStage {
  stage: string;
  deliverable_id: string | null;
  reason: string;
}

export interface CampaignEstimates {
  external_cost_usd: number;
  external_calls: number;
  local_render: boolean;
}

/** The slice of the compiled campaign the preview screen needs; the server sends more. */
export interface PreviewDeliverable {
  deliverable_id: string;
  type: string;
  title: string;
}

export interface CampaignPreview {
  campaign: { campaign_id: string; deliverables: PreviewDeliverable[] };
  dag: { nodes: DagNode[]; pruned: PrunedStage[] };
  estimates: CampaignEstimates;
  notes: string[];
}

export interface CampaignCreated {
  run_id: string;
  campaign_id: string;
}

// --- Operations ---

export type HealthStatus = "ok" | "warn" | "fail" | "skip" | (string & {});

export interface HealthCheck {
  name: string;
  status: HealthStatus;
  detail: string;
  fix: string | null;
}

export interface OperationsHealth {
  ok: boolean;
  checks: HealthCheck[];
}

export interface AuditEvent {
  id: string;
  action: string;
  actor: string | null;
  workspace_id: string | null;
  target: string | null;
  created_at: string;
}

// --- Notifications (web push) ---

export interface PushSubscriptionBody {
  endpoint: string;
  keys: { p256dh: string; auth: string };
  user_agent?: string;
}

export interface PushTestResult {
  sent: number;
  total: number;
}

// --- Revisions ---

export interface FixPlanOutcome {
  kind: "fix_plan";
  plain_language: string;
  estimated_cost_usd: number;
  estimated_seconds: number;
  operations: unknown[];
  impact: { affected_unit_ids: string[] };
}

export interface ClarifyingQuestionOutcome {
  kind: "clarifying_question";
  question: string;
  candidate_unit_ids: string[];
}

export interface RefusalOutcome {
  kind: "refusal";
  policy: string;
  reason: string;
}

export interface GateRequiredOutcome {
  kind: "gate_required";
  gate: string;
  reason: string;
}

export type RevisionOutcome = FixPlanOutcome | ClarifyingQuestionOutcome | RefusalOutcome | GateRequiredOutcome;

// --- Personas ---

export interface PersonaSummary {
  id: string;
  name: string;
  revision: number;
  archived: boolean;
}

export interface PersonaVoiceDoc {
  tone: string[];
  vocabulary: string;
  sentence_length: string;
  emoji_policy: string;
  punctuation: string;
  humor: string;
  pet_names: string[];
}

export interface PersonaDocument {
  persona_id: string;
  revision: number;
  identity: { display_name: string; pronouns: string; presented_age: number };
  voice: PersonaVoiceDoc;
  backstory: { bio: string };
  disclosure: "disclose_on_ask" | "deflect";
}

export interface PersonaDetail extends PersonaSummary {
  document: PersonaDocument;
}

export interface PersonaFieldChange {
  path: string;
  before: string;
  after: string;
  reason: string;
}

export interface PersonaDiff {
  persona_id: string;
  base_revision: number;
  changes: PersonaFieldChange[];
  plain_language: string;
}

// --- Brand hierarchy ---

export interface BrandNode {
  id: string;
  parent_id: string | null;
  name: string;
  tokens: Record<string, string>;
  locked_tokens: string[];
  policies: Record<string, string>;
  locked_policies: string[];
}

export interface BrandEffective {
  tokens: Record<string, string>;
  policies: Record<string, string>;
}

// --- Portal ---

export interface PortalLinkRow {
  id: string;
  label: string;
  expires_at: string;
  revoked: boolean;
}

export interface PortalLinkCreated extends PortalLinkRow {
  token: string;
  submit_url: string;
}

export interface PortalBriefRow {
  id: string;
  topic: string;
  objective: string;
  deadline: string | null;
  contact: string;
  status: "new" | "accepted" | "declined";
  created_at: string | null;
}

// --- Engagement inbox ---

export interface FanMessage {
  id: string;
  platform: string;
  account: string;
  fan_id: string;
  text: string;
  received_at: string;
  message_class: string;
  vip: boolean;
  disposition: "pending" | "answered" | "skipped";
  skip_reason: string | null;
}

export interface EngagementSyncResult {
  fetched: number;
  stored: number;
  escalated: number;
  accounts: number;
}
